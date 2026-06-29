"""
Passe batch sur les candidats de data/decouverte.json.
Pour chaque candidat : téléchargement complet → filtrage RM → filtered.csv + rudi_metadata.json.
Les 23 candidats sans champ de filtrage connu sont sautés (à traiter manuellement).
"""

import csv
import glob
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import unicodedata
import zipfile
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.datagouv import get_dataset_metadata
from filters.geographic import est_dans_rm, est_commune_rm, normaliser
from conf.communes_rm import CODES_POSTAUX_RM, CODES_INSEE_RM, COMMUNES_RM
from translation.datagouv_to_rudi import traduire_metadonnees
from state import charger_state, sauvegarder_state, dataset_a_change

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DECOUVERTE_FILE = os.path.join(DATA_DIR, "decouverte.json")
CACHE_DIR = os.path.join(DATA_DIR, "cache")  # cache partagé avec discover.py


def _chemin_cache(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.md5(url.encode()).hexdigest())

EPCI_SIREN_RM = "243500139"
_RE_CP_35 = re.compile(r"\b(35\d{3})\b")
_COMMUNES_NORM_RM = {normaliser(c) for c in COMMUNES_RM}
_RM_LAT_MIN, _RM_LAT_MAX = 47.80, 48.35
_RM_LON_MIN, _RM_LON_MAX = -2.15, -1.30


def est_iris_rm(code: str) -> bool:
    code = str(code).strip()
    if code == EPCI_SIREN_RM:
        return True
    return len(code) >= 5 and code[:5] in CODES_INSEE_RM


def est_adresse_rm(texte: str) -> bool:
    if not texte:
        return False
    for cp in _RE_CP_35.findall(texte):
        if cp in CODES_POSTAUX_RM:
            return True
    texte_norm = normaliser(texte)
    return any(commune in texte_norm for commune in _COMMUNES_NORM_RM)


def _ligne_est_rm(row: dict, champ_cp, champ_ville, champ_iris, champ_adresse) -> bool:
    if champ_iris:
        return est_iris_rm(str(row.get(champ_iris, "")))
    if champ_adresse:
        return est_adresse_rm(str(row.get(champ_adresse, "")))
    cp = str(row.get(champ_cp, "")).strip() if champ_cp else ""
    ville = str(row.get(champ_ville, "")).strip() if champ_ville else ""
    if champ_cp and champ_ville:
        return est_dans_rm(ville, cp)
    if champ_ville:
        return est_commune_rm(ville)
    if champ_cp:
        return cp in CODES_POSTAUX_RM
    return False


class _Passer(Exception):
    """Levée quand l'utilisateur choisit de passer un dataset pendant le téléchargement."""


def telecharger(url: str) -> str:
    """Télécharge en streaming vers le cache disque. Retourne le chemin du fichier cache."""
    chemin = _chemin_cache(url)
    if os.path.exists(chemin):
        return chemin
    os.makedirs(CACHE_DIR, exist_ok=True)
    r = requests.get(url, timeout=120, stream=True)
    r.raise_for_status()
    total = 0
    chemin_tmp = chemin + ".tmp"
    with open(chemin_tmp, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            total += len(chunk)
    os.rename(chemin_tmp, chemin)
    return chemin


def _detecter_delimiteur(sample: str) -> str:
    """Détecte le délimiteur CSV en comptant les occurrences dans la première ligne."""
    premiere_ligne = sample.split("\n")[0]
    candidats = {d: premiere_ligne.count(d) for d in (";", "\t", "|", ",")}
    # Priorité au délimiteur le plus fréquent (min 2 occurrences = au moins 3 colonnes)
    meilleur = max(candidats, key=candidats.get)
    if candidats[meilleur] >= 2:
        return meilleur
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        return dialect.delimiter
    except csv.Error:
        return ","


def _detecter_encodage(chemin: str) -> str:
    """Détecte l'encodage d'un fichier texte (utf-8-sig ou latin-1)."""
    with open(chemin, "rb") as f:
        sample = f.read(8192)
    decoded = sample.decode("utf-8-sig", errors="replace")
    return "utf-8-sig" if decoded.count("�") <= 10 else "latin-1"


def filtrer_csv(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse) -> tuple[list[dict], list[str]]:
    """Filtre un CSV en streaming ligne par ligne — ne charge pas le fichier entier en mémoire."""
    encoding = _detecter_encodage(chemin)
    with open(chemin, encoding=encoding, errors="replace", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiteur = _detecter_delimiteur(sample)
        reader = csv.DictReader(f, delimiter=delimiteur)
        lignes = [dict(row) for row in reader if _ligne_est_rm(row, champ_cp, champ_ville, champ_iris, champ_adresse)]
        entetes = list(reader.fieldnames or [])
    return lignes, entetes


def _filtrer_csv_bytes(contenu: bytes, champ_cp, champ_ville, champ_iris, champ_adresse) -> tuple[list[dict], list[str]]:
    """Filtre depuis bytes en mémoire — uniquement pour les membres extraits d'un ZIP."""
    texte = contenu.decode("utf-8-sig", errors="replace")
    if texte.count("�") > 10:
        texte = contenu.decode("latin-1")
    delimiteur = _detecter_delimiteur(texte[:4096])
    reader = csv.DictReader(io.StringIO(texte), delimiter=delimiteur)
    lignes = [dict(row) for row in reader if _ligne_est_rm(row, champ_cp, champ_ville, champ_iris, champ_adresse)]
    entetes = list(reader.fieldnames or [])
    return lignes, entetes


def filtrer_json(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse) -> list[dict]:
    with open(chemin, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for cle in ("results", "data", "records", "features"):
            if cle in data and isinstance(data[cle], list):
                rows = data[cle]
                break
        else:
            raise ValueError("Structure JSON non reconnue (pas de liste de lignes)")
    else:
        raise ValueError("Contenu JSON non reconnu (ni liste ni dict)")
    return [row for row in rows if _ligne_est_rm(row, champ_cp, champ_ville, champ_iris, champ_adresse)]


def _extraire_csvs_zip(chemin: str) -> list[tuple[str, bytes]]:
    """Extrait les fichiers CSV d'une archive ZIP. Retourne [(nom_membre, contenu_csv), ...]."""
    with zipfile.ZipFile(chemin) as zf:
        return [
            (nom, zf.read(nom))
            for nom in zf.namelist()
            if nom.lower().endswith(".csv") and not nom.startswith("__MACOSX")
        ]


def filtrer_gz(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse) -> tuple[list[dict], list[str]]:
    """Décompresse un GZ en streaming et filtre les lignes RM sans tout charger en mémoire."""
    with gzip.open(chemin, "rb") as gz:
        sample_bytes = gz.read(8192)
    decoded = sample_bytes.decode("utf-8-sig", errors="replace")
    encoding = "utf-8-sig" if decoded.count("�") <= 10 else "latin-1"
    delimiteur = _detecter_delimiteur(decoded[:4096])
    with gzip.open(chemin, "rt", encoding=encoding, errors="replace", newline="") as gz:
        reader = csv.DictReader(gz, delimiter=delimiteur)
        lignes = [dict(row) for row in reader if _ligne_est_rm(row, champ_cp, champ_ville, champ_iris, champ_adresse)]
        entetes = list(reader.fieldnames or [])
    return lignes, entetes


def filtrer_xlsx(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse) -> list[dict]:
    """Extrait et filtre les lignes Rennes Métropole d'un fichier XLSX."""
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl non installé — pip install openpyxl")
    wb = openpyxl.load_workbook(chemin, read_only=True, data_only=True)
    ws = wb.active
    lignes_brutes = list(ws.iter_rows(values_only=True))
    wb.close()
    if len(lignes_brutes) < 2:
        return []
    entetes = [str(c or "").strip() for c in lignes_brutes[0]]
    rows = [
        dict(zip(entetes, [str(v or "").strip() for v in row]))
        for row in lignes_brutes[1:]
    ]
    return [r for r in rows if _ligne_est_rm(r, champ_cp, champ_ville, champ_iris, champ_adresse)]


def sauvegarder_csv(lignes: list[dict], chemin: str) -> None:
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=lignes[0].keys())
        writer.writeheader()
        writer.writerows(lignes)


_MOTS_DICT = {
    "dictionnaire", "dict", "codebook", "code book",
    "description des colonnes", "description des champs",
    "nomenclature", "metadonnee", "metadonnees",
}


def _est_dictionnaire_titre(ressource: dict) -> bool:
    titre = normaliser(ressource.get("title", ""))
    desc = normaliser(ressource.get("description", "") or "")
    return any(mot in titre or mot in desc for mot in _MOTS_DICT)


def _est_dictionnaire_contenu(chemin: str) -> bool:
    """Vérifie si la première colonne s'appelle 'Colonne', 'Champ', etc."""
    try:
        with open(chemin, "rb") as f:
            echantillon = f.read(2048)
        texte = echantillon.decode("utf-8-sig", errors="replace")
        premiere_ligne = texte.split("\n")[0]
        delim = _detecter_delimiteur(texte)
        premier_champ = normaliser(premiere_ligne.split(delim)[0])
        return premier_champ in ("colonne", "column", "champ", "field", "variable", "nom", "libelle")
    except Exception:
        return False


def analyser_ressources(metadata: dict) -> dict:
    """Classe les ressources par rôle : données (csv/zip/gz/xlsx/json) et dictionnaires.
    Retourne csvs comme liste de (ressource, format) pour supporter tous les types."""
    resources = metadata.get("resources", [])
    csvs_fmt: list[tuple[dict, str]] = []  # (ressource, format)
    dicts = []
    has_json = False

    for r in resources:
        fmt_r = r.get("format", "").lower()
        titre_lower = r.get("title", "").lower()

        if _est_dictionnaire_titre(r):
            dicts.append(r)
            continue

        if ".zip" in titre_lower or fmt_r == "zip":
            csvs_fmt.append((r, "zip"))
        elif ".gz" in titre_lower or fmt_r == "gz":
            csvs_fmt.append((r, "gz"))
        elif fmt_r == "xlsx":
            csvs_fmt.append((r, "xlsx"))
        elif fmt_r == "csv":
            csvs_fmt.append((r, "csv"))
        elif fmt_r == "json" and "geo" not in titre_lower:
            has_json = True

    if csvs_fmt:
        fmt_principal = csvs_fmt[0][1]
        return {"csvs": csvs_fmt, "dicts": dicts, "has_json": has_json, "fmt": fmt_principal}

    # Pas de ressource tabulaire : essayer JSON directement
    jsons = [r for r in resources if r.get("format", "").lower() == "json"
             and "geo" not in r.get("title", "").lower()
             and not _est_dictionnaire_titre(r)]
    if jsons:
        return {"csvs": [(jsons[0], "json")], "dicts": dicts, "has_json": False, "fmt": "json"}
    return {"csvs": [], "dicts": dicts, "has_json": False, "fmt": ""}


def _slugifier(titre: str) -> str:
    """Convertit un titre de ressource en slug de fichier (max 50 chars)."""
    titre = re.sub(r"\.[a-zA-Z0-9]{2,5}$", "", titre.strip())
    s = normaliser(titre)
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s[:50] or "fichier"


def filtrer_toutes_ressources(
    ressources_fmt: list[tuple[dict, str]],
    champ_cp, champ_ville, champ_iris, champ_adresse,
) -> list[tuple[dict, list[dict], list[str]]]:
    """Télécharge et filtre chaque ressource (CSV, ZIP, GZ, XLSX, JSON).
    Retourne [(ressource, lignes_rm, entetes)] — un ZIP peut produire plusieurs entrées."""
    resultats = []
    multi = len(ressources_fmt) > 1
    for r, fmt in ressources_fmt:
        titre = r.get("title", r.get("url", ""))[:55]
        if multi:
            print(f"  ↳ {titre} [{fmt.upper()}]")
        try:
            chemin = telecharger(r["url"])
            entrees: list[tuple[dict, list[dict], list[str]]] = []

            if fmt == "zip":
                membres = _extraire_csvs_zip(chemin)
                if not membres:
                    print(f"    → ZIP sans CSV")
                for nom_membre, contenu_csv in membres:
                    r_m = {**r, "title": os.path.basename(nom_membre)}
                    lignes, entetes = _filtrer_csv_bytes(contenu_csv, champ_cp, champ_ville, champ_iris, champ_adresse)
                    if len(membres) > 1:
                        print(f"    ↳ {nom_membre}: {len(lignes)} lignes RM")
                    entrees.append((r_m, lignes, entetes))
            elif fmt == "gz":
                lignes, entetes = filtrer_gz(chemin, champ_cp, champ_ville, champ_iris, champ_adresse)
                entrees = [(r, lignes, entetes)]
            elif fmt == "xlsx":
                lignes = filtrer_xlsx(chemin, champ_cp, champ_ville, champ_iris, champ_adresse)
                entrees = [(r, lignes, [])]
            elif fmt == "json":
                lignes = filtrer_json(chemin, champ_cp, champ_ville, champ_iris, champ_adresse)
                entrees = [(r, lignes, [])]
            else:  # csv
                lignes, entetes = filtrer_csv(chemin, champ_cp, champ_ville, champ_iris, champ_adresse)
                entrees = [(r, lignes, entetes)]

            resultats.extend(entrees)
            if multi and fmt != "zip" and entrees:
                print(f"    → {len(entrees[0][1])} lignes RM")
        except Exception as e:
            print(f"    → ERREUR : {e}")
            resultats.append((r, [], []))
    return resultats


def traiter_candidat(candidat: dict, state: dict) -> dict:
    dataset_id = candidat["dataset_id"]
    dossier_nom = candidat["dossier"]
    dossier = os.path.join(DATA_DIR, dossier_nom)
    os.makedirs(dossier, exist_ok=True)

    champ_cp = candidat.get("champ_cp")
    champ_ville = candidat.get("champ_ville")
    champ_iris = candidat.get("champ_iris")
    champ_adresse = candidat.get("champ_adresse")

    metadata = get_dataset_metadata(dataset_id)
    last_modified = metadata.get("last_modified", "")

    # Vérifier si des fichiers filtrés existent déjà et que la source n'a pas changé
    filtered_existe = (
        bool(glob.glob(os.path.join(dossier, "*-rennesmetropole.csv"))) or
        bool(glob.glob(os.path.join(dossier, "*-rennesmetropole.json"))) or
        bool(glob.glob(os.path.join(dossier, "filtered*.csv"))) or   # ancien nommage
        bool(glob.glob(os.path.join(dossier, "filtered*.json")))
    )
    if filtered_existe and not dataset_a_change(state, dataset_id, last_modified):
        nb_rm = state.get(dataset_id, {}).get("nb_rm", "?")
        return {"statut": "cache", "nb_rm": nb_rm, "last_modified": last_modified}

    analyse = analyser_ressources(metadata)
    fmt = analyse["fmt"]
    if not analyse["csvs"]:
        return {"statut": "echec", "raison": "aucune ressource CSV/JSON trouvée"}

    if len(analyse["csvs"]) > 1:
        fmts = "/".join(sorted({f.upper() for _, f in analyse["csvs"]}))
        print(f"  {len(analyse['csvs'])} ressources [{fmts}] — téléchargées séparément")

    resultats = filtrer_toutes_ressources(analyse["csvs"], champ_cp, champ_ville, champ_iris, champ_adresse)

    # Sauvegarder un fichier filtré par ressource (+ JSON régénéré si la source en avait)
    fichiers_data = []   # [(nom, nb_rm, ressource)]
    fichiers_dicts = []  # [(nom, ressource)]
    dernieres_entetes = []

    for i, (ressource, lignes, entetes) in enumerate(resultats):
        if entetes:
            dernieres_entetes = entetes
        if not lignes:
            continue
        slug = _slugifier(
            ressource.get("title", "") or metadata.get("title", f"fichier-{i+1}")
        )
        if fmt == "json":
            nom = f"{slug}-rennesmetropole.json"
            chemin = os.path.join(dossier, nom)
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(lignes, f, ensure_ascii=False, indent=2)
            fichiers_data.append((nom, len(lignes), ressource))
        else:
            nom_csv = f"{slug}-rennesmetropole.csv"
            sauvegarder_csv(lignes, os.path.join(dossier, nom_csv))
            fichiers_data.append((nom_csv, len(lignes), ressource))
            # Régénérer JSON si la source en avait un
            if analyse["has_json"]:
                nom_json = nom_csv.replace(".csv", ".json")
                with open(os.path.join(dossier, nom_json), "w", encoding="utf-8") as f:
                    json.dump(lignes, f, ensure_ascii=False, indent=2)
                fichiers_data.append((nom_json, len(lignes), ressource))

    if not fichiers_data:
        champ_cherche = champ_iris or champ_ville or champ_cp or champ_adresse
        present = champ_cherche in dernieres_entetes if (champ_cherche and dernieres_entetes) else False
        raison = (
            f"colonne '{champ_cherche}' absente du fichier (colonnes : {', '.join(dernieres_entetes[:8])}...)"
            if not present and dernieres_entetes
            else "0 lignes RM après filtrage"
        )
        return {"statut": "vide", "raison": raison}

    # Télécharger les dictionnaires tels quels
    for r in analyse["dicts"]:
        titre_r = r.get("title", "dictionnaire")
        ext = r.get("format", "csv").lower()
        nom = f"dict-{_slugifier(titre_r)}.{ext}"
        print(f"  Dictionnaire : {titre_r[:55]}")
        try:
            chemin_cache = telecharger(r["url"])
            if ext == "csv" and not _est_dictionnaire_contenu(chemin_cache):
                print(f"    → ignoré (contenu détecté comme données, pas dictionnaire)")
                continue
            shutil.copy2(chemin_cache, os.path.join(dossier, nom))
            fichiers_dicts.append((nom, r))
        except Exception as e:
            print(f"    → ERREUR : {e}")

    # Sommer les CSV uniquement (les JSON régénérés ont le même nb_rm, on évite le double-compte)
    nb_rm_total = sum(nb for nom, nb, _ in fichiers_data if nom.endswith(".csv")) \
               or sum(nb for nom, nb, _ in fichiers_data if nom.endswith(".json"))

    rudi_metadata = traduire_metadonnees(
        metadata, dossier_nom=dossier_nom,
        fichiers_filtres=fichiers_data, fichiers_dicts=fichiers_dicts,
    )
    rudi_file = os.path.join(dossier, "rudi_metadata.json")
    with open(rudi_file, "w", encoding="utf-8") as f:
        json.dump(rudi_metadata, f, ensure_ascii=False, indent=2)

    state[dataset_id] = {"last_modified": last_modified, "nb_rm": nb_rm_total, "dossier": dossier_nom}
    return {"statut": "ok", "nb_rm": nb_rm_total, "format": fmt.upper(), "last_modified": last_modified}


def main():
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with open(DECOUVERTE_FILE, encoding="utf-8") as f:
        decouverte = json.load(f)

    candidats = decouverte["candidats"]
    state = charger_state()
    resultats = {"ok": [], "cache": [], "echecs": [], "vides": [], "sautes": []}
    lock = threading.Lock()  # protège sauvegarder_state (écriture fichier)

    # Pré-tri immédiat des candidats sans champ géo (pas de thread pour eux)
    a_traiter = []
    for candidat in candidats:
        if not any(candidat.get(c) for c in ("champ_cp", "champ_ville", "champ_iris", "champ_adresse")):
            resultats["sautes"].append({"dataset_id": candidat["dataset_id"], "titre": candidat["titre"]})
        else:
            a_traiter.append(candidat)

    n = len(a_traiter)
    ns = len(resultats["sautes"])
    print(f"=== Harvest batch — {len(candidats)} candidats"
          f"{f', dont {ns} sautés (sans champ géo)' if ns else ''}"
          f" — {n} à traiter (5 threads) ===\n")

    def run(candidat):
        try:
            return traiter_candidat(candidat, state)
        except Exception as e:
            return {"statut": "echec", "raison": str(e)}

    completed = 0
    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(run, c): c for c in a_traiter}
            for future in as_completed(futures):
                candidat = futures[future]
                completed += 1
                dataset_id = candidat["dataset_id"]
                titre = candidat["titre"][:65]
                try:
                    res = future.result()
                except Exception as e:
                    res = {"statut": "echec", "raison": str(e)}

                # Résumé affiché atomiquement depuis le thread principal
                print(f"\n[{completed}/{n}] {titre}")
                if res["statut"] == "cache":
                    print(f"  → CACHE ({res.get('nb_rm', '?')} lignes RM, inchangé)")
                    resultats["cache"].append({"dataset_id": dataset_id, "titre": candidat["titre"]})
                elif res["statut"] == "ok":
                    print(f"  → OK — {res['nb_rm']} lignes RM ({res['format']})")
                    resultats["ok"].append({"dataset_id": dataset_id, "titre": candidat["titre"], "nb_rm": res["nb_rm"]})
                    with lock:
                        sauvegarder_state(state)
                elif res["statut"] == "vide":
                    print(f"  → VIDE — {res['raison']}")
                    resultats["vides"].append({"dataset_id": dataset_id, "titre": candidat["titre"], "raison": res["raison"]})
                else:
                    print(f"  → ÉCHEC — {res.get('raison', '')}")
                    resultats["echecs"].append({"dataset_id": dataset_id, "titre": candidat["titre"], "raison": res.get("raison", "")})

    except KeyboardInterrupt:
        print("\n\nInterruption clavier.")

    sauvegarder_state(state)

    results_file = os.path.join(DATA_DIR, "batch_resultats.json")
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)

    print(f"\n=== Terminé ===")
    print(f"  OK     : {len(resultats['ok'])}")
    print(f"  Cache  : {len(resultats['cache'])} (inchangés)")
    print(f"  Vides  : {len(resultats['vides'])}")
    print(f"  Échecs : {len(resultats['echecs'])}")
    print(f"  Sautés : {len(resultats['sautes'])}")
    print(f"\n  Résultats complets : {results_file}")

    from catalogue import construire_catalogue, ecrire_json, ecrire_html, ecrire_viewers
    catalogue = construire_catalogue()
    ecrire_json(catalogue)
    ecrire_html(catalogue)
    ecrire_viewers(catalogue)
    print(f"\n  Catalogue mis à jour : {catalogue['nb_jeux']} JDD → data/catalogue.json + data/catalogue.html")


if __name__ == "__main__":
    main()
