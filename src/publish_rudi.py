"""
Publie sur le nœud RUDI les JDD déjà moissonnés mais pas encore marqués
publiés (`rudi_publie` absent ou faux dans state.json / state_insee.json /
state_geo.json).

Travaille uniquement à partir des rudi_metadata.json déjà sur disque : ne
retélécharge ni ne refiltre rien. Utile pour rattraper une publication ratée
(nœud injoignable au moment de la moisson) ou une moisson faite avant la
configuration de src/conf/rudi_node.json, sans refaire tout le téléchargement.

Usage : python3 src/publish_rudi.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.rudi_node import (publier_dataset, charger_conf_rudi, toutes_metadonnees_rudi,
                                   supprimer_dataset, supprimer_organisation, _creer_writer)
from conf.datasets import DATASETS_GEO
from state import charger_etat, sauvegarder_etat, construire_index_dossier

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_TAB_FILE = os.path.join(DATA_DIR, "state.json")
STATE_INSEE_FILE = os.path.join(DATA_DIR, "state_insee.json")
STATE_OEB_FILE = os.path.join(DATA_DIR, "state_oeb.json")
STATE_BDNB_FILE = os.path.join(DATA_DIR, "state_bdnb.json")
STATE_GEO_FILE = os.path.join(DATA_DIR, "state_geo.json")

# Mappe source → fichier d'état correspondant (pour la sauvegarde incrémentale)
_FICHIERS_ETAT = {
    "tabulaire": STATE_TAB_FILE, "insee": STATE_INSEE_FILE,
    "oeb": STATE_OEB_FILE, "bdnb": STATE_BDNB_FILE,
}


def _fichiers_a_uploader(dossier_path: str, rudi_metadata: dict) -> list[str]:
    """Reconstruit la liste ordonnée des fichiers FILE en tête de available_formats
    (avant les entrées SERVICE) — c'est l'ordre attendu par publier_dataset()."""
    fichiers = []
    for media in rudi_metadata.get("available_formats", []):
        if media.get("media_type") != "FILE":
            break
        fichiers.append(os.path.join(dossier_path, media.get("media_name", "")))
    return fichiers


def main() -> None:
    conf_rudi = charger_conf_rudi()
    if not conf_rudi:
        print("src/conf/rudi_node.json absent — impossible de publier.")
        return

    state_tab = charger_etat(STATE_TAB_FILE)
    state_insee = charger_etat(STATE_INSEE_FILE)
    state_oeb = charger_etat(STATE_OEB_FILE)
    state_bdnb = charger_etat(STATE_BDNB_FILE)
    state_geo = charger_etat(STATE_GEO_FILE)
    geo_deja_publie = state_geo.get("_rudi_publie", {})
    dossiers_geo = {c["dossier"] for c in DATASETS_GEO}

    # dossier -> ("tabulaire"|"insee"|"oeb"|"bdnb", clé dans l'état correspondant)
    index = construire_index_dossier(
        ("tabulaire", state_tab), ("insee", state_insee),
        ("oeb", state_oeb), ("bdnb", state_bdnb),
    )

    # Mappe source → dict d'état pour la mise à jour rudi_publie
    etats = {"tabulaire": state_tab, "insee": state_insee, "oeb": state_oeb, "bdnb": state_bdnb}

    a_publier = []   # [(dossier, source, cle_etat_ou_None)]
    inconnus = []
    for nom in sorted(os.listdir(DATA_DIR)):
        chemin_meta = os.path.join(DATA_DIR, nom, "rudi_metadata.json")
        if not os.path.isfile(chemin_meta):
            continue
        if nom in dossiers_geo:
            if not geo_deja_publie.get(nom):
                a_publier.append((nom, "geo", None))
        elif nom in index:
            source, cle = index[nom]
            if not etats[source][cle].get("rudi_publie"):
                a_publier.append((nom, source, cle))
        else:
            inconnus.append(nom)

    if inconnus:
        print(f"{len(inconnus)} dossier(s) avec rudi_metadata.json mais sans correspondance dans "
              f"state.json/state_insee.json/DATASETS_GEO (config supprimée ?) — ignorés, à vérifier manuellement :")
        for nom in inconnus:
            print(f"  ? {nom}")
        print()

    if not a_publier:
        print("Rien à publier — tout est déjà marqué publié.")
        return

    print(f"=== Publication RUDI — {len(a_publier)} JDD à (re)publier ===\n")
    ok, echecs = 0, 0
    for dossier, source, cle in a_publier:
        dossier_path = os.path.join(DATA_DIR, dossier)
        print(f"--- {dossier} ({source}) ---")
        try:
            with open(os.path.join(dossier_path, "rudi_metadata.json"), encoding="utf-8") as f:
                rudi_metadata = json.load(f)
            fichiers = _fichiers_a_uploader(dossier_path, rudi_metadata)
            publier_dataset(conf=conf_rudi, rudi_metadata=rudi_metadata, fichiers_filtres=fichiers)
            ok += 1
            # Sauvegarde immédiate — un rattrapage porte sur des dizaines/centaines de JDD
            # et peut être interrompu en cours de route (nœud injoignable, process tué) ;
            # ne pas perdre le travail déjà acquis en ne sauvegardant qu'à la toute fin.
            if source == "geo":
                state_geo.setdefault("_rudi_publie", {})
                state_geo["_rudi_publie"][dossier] = True
                sauvegarder_etat(STATE_GEO_FILE, state_geo)
            else:
                etats[source][cle]["rudi_publie"] = True
                sauvegarder_etat(_FICHIERS_ETAT[source], etats[source])
        except Exception as e:
            print(f"  [RUDI] ERREUR : {e}")
            echecs += 1
        print()

    print(f"=== Terminé : {ok} publié(s), {echecs} échec(s) sur {len(a_publier)} ===")


def menage_rudi_one_shot() -> list[dict]:
    """
    Interroge le nœud RUDI pour lister tous les datasets enregistrés et signale
    ceux qui n'ont plus de correspondance locale (rudi_metadata.json absent).

    Retourne la liste des datasets orphelins (dicts RUDI).
    """
    conf_rudi = charger_conf_rudi()
    if not conf_rudi:
        print("src/conf/rudi_node.json absent — impossible d'interroger le nœud.")
        return []

    print("Récupération de tous les datasets depuis le nœud RUDI…")
    try:
        tous = toutes_metadonnees_rudi(conf_rudi)
    except Exception as e:
        print(f"  ERREUR lors de l'interrogation du nœud : {e}")
        return []

    print(f"  {len(tous)} dataset(s) trouvé(s) sur le nœud.")

    # Collecte les local_id locaux (issus des rudi_metadata.json sur disque)
    locaux_ids: set[str] = set()
    for nom in sorted(os.listdir(DATA_DIR)):
        chemin_meta = os.path.join(DATA_DIR, nom, "rudi_metadata.json")
        if os.path.isfile(chemin_meta):
            with open(chemin_meta, encoding="utf-8") as f:
                meta = json.load(f)
                lid = meta.get("local_id", "")
                if lid:
                    locaux_ids.add(lid)

    # Détection des orphelins : présents sur le nœud, absents en local
    orphelins = [d for d in tous if d.get("local_id", "") not in locaux_ids]

    if not orphelins:
        print("  ✅ Tous les datasets du nœud RUDI ont une contrepartie locale.")
        return []

    print(f"\n⚠️  {len(orphelins)} dataset(s) orphelin(s) détecté(s) sur le nœud RUDI (plus de fichier local) :")
    for d in orphelins:
        titre = d.get("dataset_name", d.get("local_id", "?"))
        lid = d.get("local_id", "?")
        print(f"  • {titre}  (local_id={lid[:40]})")
    return orphelins


def menage_organisations() -> list[dict]:
    """
    Interroge le nœud RUDI pour lister les organisations déclarées mais non
    référencées par aucun dataset (producteur ni éditeur de métadonnées).

    Retourne la liste des organisations non utilisées (dicts RUDI).
    """
    conf_rudi = charger_conf_rudi()
    if not conf_rudi:
        print("src/conf/rudi_node.json absent — impossible d'interroger le nœud.")
        return []

    writer = _creer_writer(conf_rudi)
    try:
        toutes = {o["organization_name"]: o for o in writer.organization_list}
        utilisees = {o.get("organization_name") for o in writer.used_organization_list}
    except Exception as e:
        print(f"  ERREUR lors de l'interrogation du nœud : {e}")
        return []

    non_utilisees = [toutes[n] for n in sorted(set(toutes) - utilisees)]

    if not non_utilisees:
        print("  ✅ Toutes les organisations du nœud RUDI sont utilisées par au moins un dataset.")
        return []

    print(f"\n⚠️  {len(non_utilisees)} organisation(s) non utilisée(s) sur le nœud RUDI :")
    for o in non_utilisees:
        print(f"  • {o.get('organization_name', '?')}  (id={o.get('organization_id', '?')[:12]}…)")
    return non_utilisees


def supprimer_datasets(conf: dict, orphelins: list[dict]) -> tuple[int, int]:
    """Supprime les datasets orphelins du nœud RUDI. Retourne (ok, echecs)."""
    ok, echecs = 0, 0
    for d in orphelins:
        if supprimer_dataset(conf, d["global_id"]):
            ok += 1
        else:
            echecs += 1
    return ok, echecs


def supprimer_organisations(conf: dict, organisations: list[dict]) -> tuple[int, int]:
    """Supprime les organisations non utilisées du nœud RUDI. Retourne (ok, echecs)."""
    ok, echecs = 0, 0
    for o in organisations:
        if supprimer_organisation(conf, o["organization_id"]):
            ok += 1
        else:
            echecs += 1
    return ok, echecs


if __name__ == "__main__":
    main()
