"""
Publie sur le nœud RUDI les JDD déjà moissonnés mais pas encore marqués
publiés (`rudi_publie` absent ou faux dans state.json / state_insee.json),
plus les services géo (DATASETS_GEO, sans suivi d'état — toujours retentés).

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

from connectors.rudi_node import publier_dataset, charger_conf_rudi
from conf.datasets import DATASETS_GEO
from state import charger_state, sauvegarder_state
from harvest_insee import _charger_state as charger_state_insee, _sauvegarder_state as sauvegarder_state_insee
from harvest_oeb import _charger_state as charger_state_oeb, _sauvegarder_state as sauvegarder_state_oeb
from harvest_bdnb import _charger_state as charger_state_bdnb, _sauvegarder_state as sauvegarder_state_bdnb

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


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

    state_tab = charger_state()
    state_insee = charger_state_insee()
    state_oeb = charger_state_oeb()
    state_bdnb = charger_state_bdnb()
    dossiers_geo = {c["dossier"] for c in DATASETS_GEO}

    # dossier -> ("tabulaire"|"insee"|"oeb", clé dans l'état correspondant)
    index = {}
    for cle, entree in state_tab.items():
        index[entree.get("dossier")] = ("tabulaire", cle)
    for cle, entree in state_insee.items():
        index[entree.get("dossier")] = ("insee", cle)
    for cle, entree in state_oeb.items():
        index[entree.get("dossier")] = ("oeb", cle)
    for cle, entree in state_bdnb.items():
        index[entree.get("dossier")] = ("bdnb", cle)

    a_publier = []   # [(dossier, source, cle_etat_ou_None)]
    inconnus = []
    for nom in sorted(os.listdir(DATA_DIR)):
        chemin_meta = os.path.join(DATA_DIR, nom, "rudi_metadata.json")
        if not os.path.isfile(chemin_meta):
            continue
        if nom in dossiers_geo:
            a_publier.append((nom, "geo", None))
        elif nom in index:
            source, cle = index[nom]
            if source == "tabulaire":
                etat = state_tab
            elif source == "insee":
                etat = state_insee
            elif source == "oeb":
                etat = state_oeb
            else:
                etat = state_bdnb
            if not etat[cle].get("rudi_publie"):
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
            if source == "tabulaire":
                state_tab[cle]["rudi_publie"] = True
            elif source == "insee":
                state_insee[cle]["rudi_publie"] = True
            elif source == "oeb":
                state_oeb[cle]["rudi_publie"] = True
            elif source == "bdnb":
                state_bdnb[cle]["rudi_publie"] = True
        except Exception as e:
            print(f"  [RUDI] ERREUR : {e}")
            echecs += 1
        print()

    sauvegarder_state(state_tab)
    sauvegarder_state_insee(state_insee)
    sauvegarder_state_oeb(state_oeb)
    sauvegarder_state_bdnb(state_bdnb)
    print(f"=== Terminé : {ok} publié(s), {echecs} échec(s) sur {len(a_publier)} ===")


if __name__ == "__main__":
    main()
