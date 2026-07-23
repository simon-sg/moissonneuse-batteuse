"""
Rattrapage offline des faux positifs INSEE/CP (collisions du dept 35).

L'ancien filtre champ_iris acceptait l'union des interprétations INSEE et CP
d'un code à 5 chiffres (35132 = INSEE Hirel, hors RM, passait parce qu'il est
aussi le CP de Vezin-le-Coquet). Le filtre corrigé (filters/geographic.py) étant
strictement plus restrictif, il n'y a que des lignes à RETIRER des fichiers déjà
moissonnés — aucun re-téléchargement (pipeline incrémental).

Usage :
    python3 src/reanalyser_faux_positifs.py               # dry-run : rapport seul
    python3 src/reanalyser_faux_positifs.py --appliquer   # mute fichiers + états
    python3 src/reanalyser_faux_positifs.py --dossier <nom>  # cible un seul dataset

Après --appliquer, enchaîner : catalogue.py, puis publish_rudi.py (nœud démarré),
puis monitor.py --import-data --refresh si la base monitoring est configurée.
"""

import argparse
import csv
import json
import os
import sys

csv.field_size_limit(10_000_000)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.datasets import DATASETS
from conf.insee_cp_35 import INSEE_SEULEMENT_35, CP_SEULEMENT_35
from filters.geographic import detecter_nature_colonne
from filters.harvest import _detecter_delimiteur, _detecter_encodage, _ligne_est_rm
from harvest_batch import _resoudre_champs
from state import charger_state, sauvegarder_state, construire_index_dossier, lire_rudi_publie, ecrire_rudi_publie
from discover import charger_decouverte, sauvegarder_decouverte

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Garde-fou sur le volume total retiré en application intégrale — ordre de
# grandeur audité le 2026-07-11 : ~78 000 lignes sur ~121 datasets.
RETRAIT_MIN_ATTENDU = 50_000
RETRAIT_MAX_ATTENDU = 120_000

_FICHIERS_EXCLUS = {"rudi_metadata.json", "wms_service.json"}
_ECHANTILLON_NATURE = 5000


def construire_configs() -> dict[str, dict]:
    """Table dossier → config champs (candidats découverts + DATASETS),
    restreinte aux configs ayant champ_iris (seul filtre dont la sémantique change)."""
    configs: dict[str, dict] = {}
    for c in charger_decouverte().get("candidats", []):
        configs[c.get("dossier") or c.get("dataset_id")] = c
    for c in DATASETS:
        configs[c["dossier"]] = c
    return {d: c for d, c in configs.items() if c.get("champ_iris")}


def _fichiers_cibles(chemin_dossier: str) -> list[str]:
    fichiers = []
    for nom in sorted(os.listdir(chemin_dossier)):
        if nom in _FICHIERS_EXCLUS or nom.endswith(("_viewer.html", "_map.html")):
            continue
        if nom.endswith((".csv", ".json")):
            fichiers.append(os.path.join(chemin_dossier, nom))
    return fichiers


def _compter_discriminants(valeurs) -> tuple[int, int]:
    n_insee = n_cp = 0
    for v in valeurs:
        v = str(v or "").strip()
        if len(v) == 5 and v.isdigit() and v.startswith("35"):
            if v in INSEE_SEULEMENT_35:
                n_insee += 1
            elif v in CP_SEULEMENT_35:
                n_cp += 1
    return n_insee, n_cp


def _lire_fichier(chemin: str, config: dict):
    """Lit un fichier cible. Retourne (rows, entetes, delimiteur, iris, ville)
    ou None si le fichier n'est pas re-filtrable (JSON non-liste…).
    Pour les CSV les noms de champs sont résolus contre les en-têtes réels
    (même logique que la moisson) ; pour les JSON les noms config sont utilisés
    tels quels (même logique que harvest_batch.filtrer_json)."""
    if chemin.endswith(".csv"):
        encoding = _detecter_encodage(chemin)
        with open(chemin, encoding=encoding, errors="replace", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            delimiteur = _detecter_delimiteur(sample)
            reader = csv.DictReader(f, delimiter=delimiteur)
            entetes = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
        _, ville, iris, _, _, _, _, _, _, _ = _resoudre_champs(
            entetes, None, config.get("champ_ville"), config.get("champ_iris"),
            None, None, None, None, None, None, None)
        return rows, entetes, delimiteur, iris, ville
    # JSON — liste de dicts (format save_json)
    with open(chemin, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError:
            return None
    if not isinstance(data, list) or (data and not isinstance(data[0], dict)):
        return None
    return data, None, None, config.get("champ_iris"), config.get("champ_ville")


def analyser_dossier(dossier: str, config: dict) -> dict | None:
    """Analyse tous les fichiers cibles d'un dossier sans rien muter.

    Retourne un dict rapport :
    { "dossier", "action" ("retypage"|"douteux"|"zero_rm"|"modifie"|"inchange"),
      "avant", "apres", "natures", "fichiers": [(chemin, rows_gardees, entetes,
      delimiteur, avant, apres), ...] }
    """
    chemin_dossier = os.path.join(DATA_DIR, dossier)
    if not os.path.isdir(chemin_dossier):
        return None
    cibles = _fichiers_cibles(chemin_dossier)
    if not cibles:
        return None

    avant_total = apres_total = 0
    n_insee_total = n_cp_total = 0
    natures = []
    fichiers = []
    for chemin in cibles:
        lu = _lire_fichier(chemin, config)
        if lu is None:
            print(f"  [!] {os.path.relpath(chemin, DATA_DIR)} : non re-filtrable, ignoré")
            continue
        rows, entetes, delimiteur, iris, ville = lu
        if not iris:
            continue
        valeurs = [str(r.get(iris, "")) for r in rows[:_ECHANTILLON_NATURE]]
        n_insee, n_cp = _compter_discriminants(valeurs)
        n_insee_total += n_insee
        n_cp_total += n_cp
        nature = detecter_nature_colonne(valeurs)
        natures.append(nature)
        gardees = [r for r in rows
                   if _ligne_est_rm(r, None, ville, iris, None, nature_iris=nature)]
        avant_total += len(rows)
        apres_total += len(gardees)
        fichiers.append((chemin, gardees, entetes, delimiteur, len(rows), len(gardees)))

    if not fichiers:
        return None
    retirees = avant_total - apres_total
    if "cp" in natures and "insee" not in natures:
        # Colonne de nature CP dans tout le dossier : tag champ_iris erroné.
        # Un dossier MIXTE (des fichiers INSEE et un fichier CP, cas RNIC) n'est
        # PAS re-typé : la pré-passe nature par fichier le filtre correctement.
        action = "retypage"
    elif retirees == 0:
        action = "inchange"
    elif n_insee_total + n_cp_total == 0:
        action = "douteux"       # tag champ_iris probablement aberrant — revue manuelle
    elif apres_total == 0:
        action = "zero_rm"       # candidat probablement faux positif — exclusion manuelle
    else:
        action = "modifie"
    return {"dossier": dossier, "action": action, "avant": avant_total,
            "apres": apres_total, "natures": sorted(set(natures)),
            "fichiers": fichiers, "titre": config.get("titre", ""),
            "champ_iris": config.get("champ_iris"),
            "dataset_id": config.get("dataset_id", dossier)}


def _reecrire_fichier(chemin: str, rows: list[dict], entetes: list[str] | None,
                      delimiteur: str | None) -> None:
    """Réécrit un fichier cible avec les seules lignes conservées.
    CSV : utf-8, même délimiteur, même ordre de colonnes. JSON : liste indentée."""
    if chemin.endswith(".csv"):
        with open(chemin, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=entetes, delimiter=delimiteur,
                                    extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    else:
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)


def appliquer_modifications(rapport: dict, state: dict, index: dict) -> None:
    """Réécrit les fichiers modifiés d'un dossier et met à jour l'état
    (sauvegarde incrémentale — voir CLAUDE.md « Discipline d'état »)."""
    for chemin, gardees, entetes, delimiteur, avant, apres in rapport["fichiers"]:
        if apres < avant:
            _reecrire_fichier(chemin, gardees, entetes, delimiteur)
    correspondance = index.get(rapport["dossier"])
    if correspondance:
        _, cle = correspondance
        state[cle]["nb_rm"] = rapport["apres"]
        rp = lire_rudi_publie(state[cle])
        for nom_noeud in rp:
            rp[nom_noeud] = False
        ecrire_rudi_publie(state[cle], rp)
        # last_modified conservé : le skip-si-inchangé doit continuer à fonctionner.
        sauvegarder_state(state)
    else:
        print(f"  [!] {rapport['dossier']} : aucune entrée state.json — nb_rm non mis à jour")


def appliquer_retypage(rapport: dict, state: dict) -> None:
    """Corrige le candidat (champ_cp ← champ_iris) dans decouverte.json et supprime
    l'entrée state pour forcer la re-moisson ciblée au prochain harvest_batch."""
    decouverte = charger_decouverte()
    for c in decouverte.get("candidats", []):
        if (c.get("dossier") or c.get("dataset_id")) == rapport["dossier"]:
            if not c.get("champ_cp"):
                c["champ_cp"] = c.get("champ_iris")
            c["champ_iris"] = None
            sauvegarder_decouverte(decouverte)
            break
    else:
        print(f"  [!] {rapport['dossier']} : candidat introuvable dans decouverte.json")
    if rapport["dataset_id"] in state:
        del state[rapport["dataset_id"]]
        sauvegarder_state(state)


def _imprimer_section(titre: str, rapports: list[dict]) -> None:
    if not rapports:
        return
    print(f"\n=== {titre} ({len(rapports)}) ===")
    for r in rapports:
        natures = "/".join(r["natures"]) or "-"
        print(f"  {r['dossier']}  avant={r['avant']}  après={r['apres']}  "
              f"retirées={r['avant'] - r['apres']}  nature={natures}  "
              f"champ_iris={r['champ_iris']!r}  {r['titre'][:60]}")


def executer(appliquer: bool = False, dossier: str | None = None) -> int:
    """Analyse (et applique si demandé) le re-filtrage. Retourne un code de sortie.
    Appelable depuis le CLI (action Maintenance) comme depuis le __main__."""
    configs = construire_configs()
    if dossier:
        if dossier not in configs:
            print(f"Dossier {dossier!r} sans config champ_iris connue.")
            return 1
        configs = {dossier: configs[dossier]}
    print(f"{len(configs)} configs avec champ_iris à examiner "
          f"({'APPLICATION' if appliquer else 'dry-run'})")

    rapports = []
    for nom_dossier in sorted(configs):
        r = analyser_dossier(nom_dossier, configs[nom_dossier])
        if r:
            rapports.append(r)

    par_action: dict[str, list[dict]] = {}
    for r in rapports:
        par_action.setdefault(r["action"], []).append(r)

    modifies = par_action.get("modifie", [])
    retirees_total = sum(r["avant"] - r["apres"] for r in modifies)

    _imprimer_section("Datasets à re-filtrer", modifies)
    _imprimer_section("À re-typer champ_cp et re-moissonner (colonne de nature CP)",
                      par_action.get("retypage", []))
    _imprimer_section("Candidats probablement faux positifs — à exclure manuellement "
                      "via /examen ou menu 2 (0 ligne RM restante)",
                      par_action.get("zero_rm", []))
    _imprimer_section("Tag champ_iris douteux — revue manuelle (aucun code discriminant)",
                      par_action.get("douteux", []))

    n_inchanges = len(par_action.get("inchange", []))
    print(f"\nTotaux : {len(modifies)} datasets à re-filtrer, "
          f"{retirees_total} lignes à retirer, {n_inchanges} inchangés, "
          f"{len(par_action.get('retypage', []))} à re-typer, "
          f"{len(par_action.get('zero_rm', []))} à 0 ligne RM, "
          f"{len(par_action.get('douteux', []))} douteux.")

    hors_plage = not (RETRAIT_MIN_ATTENDU <= retirees_total <= RETRAIT_MAX_ATTENDU)
    if hors_plage and not dossier:
        print(f"[!] Volume retiré hors de la plage attendue "
              f"[{RETRAIT_MIN_ATTENDU}, {RETRAIT_MAX_ATTENDU}] — investiguer avant d'appliquer.")

    if not appliquer:
        print("\nDry-run terminé — rien n'a été modifié. Relancer avec --appliquer.")
        return 0
    if hors_plage and not dossier:
        print("Application REFUSÉE (garde-fou volume). Utiliser --dossier pour cibler.")
        return 1

    state = charger_state()
    index = construire_index_dossier(("tabulaire", state))
    for r in modifies:
        print(f"→ ré-écriture {r['dossier']} ({r['avant']} → {r['apres']})")
        appliquer_modifications(r, state, index)
    for r in par_action.get("retypage", []):
        print(f"→ re-typage {r['dossier']} (champ_iris {r['champ_iris']!r} → champ_cp)")
        appliquer_retypage(r, state)
    print("\nApplication terminée. Enchaîner : catalogue.py → publish_rudi.py "
          "(nœud démarré) → monitor.py --import-data --refresh si configuré.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-filtrage offline des faux positifs INSEE/CP (dept 35).")
    parser.add_argument("--appliquer", action="store_true",
                        help="mute les fichiers et les états (défaut : dry-run)")
    parser.add_argument("--dossier", help="cible un seul dossier (debug)")
    args = parser.parse_args()
    return executer(appliquer=args.appliquer, dossier=args.dossier)


if __name__ == "__main__":
    sys.exit(main())
