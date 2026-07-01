"""
Régénère la description (résumé RUDI) des JDD déjà moissonnés dont la source ne
fournissait aucun texte exploitable (description data.gouv.fr vide, publication INSEE
ou service géo — qui n'ont par construction jamais de description en entrée).

Travaille uniquement à partir des fichiers déjà sur disque (rudi_metadata.json +
fichier filtré du même dossier) : ne retélécharge ni ne refiltre rien, et ne republie
pas sur le nœud RUDI (à faire ensuite via l'option "Publier sur le nœud RUDI" si
souhaité). Idempotent : un JDD déjà enrichi ou qui a une vraie description n'est pas
retouché.

Les moissons futures (harvest_batch.py / harvest_insee.py / harvest_geo.py) appliquent
déjà ce même traitement automatiquement — ce script ne sert qu'à rattraper les JDD
moissonnés avant l'introduction de ce traitement.

Si un JDD enrichi ici était déjà marqué `rudi_publie: true` (publié avant l'enrichissement),
son flag est remis à `false` dans le fichier d'état correspondant (state.json /
state_insee.json / state_oeb.json / state_bdnb.json) afin que "Publier sur le nœud RUDI"
le reprenne réellement au run suivant — sinon publish_rudi.py le considère comme déjà
publié et ne republie jamais la description mise à jour.

Usage : python3 src/enrichir_descriptions.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from translation.description_secours import (
    MARQUEUR, description_quasi_vide, entetes_depuis_csv, entetes_depuis_geojson,
    generer_complement, partie_descriptive,
)
from state import charger_state, sauvegarder_state
from harvest_insee import _charger_state as charger_state_insee, _sauvegarder_state as sauvegarder_state_insee
from harvest_oeb import _charger_state as charger_state_oeb, _sauvegarder_state as sauvegarder_state_oeb
from harvest_bdnb import _charger_state as charger_state_bdnb, _sauvegarder_state as sauvegarder_state_bdnb

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _colonnes_disponibles(dossier: str, rudi_metadata: dict) -> list[str]:
    """Cherche les colonnes du premier fichier FILE (CSV ou GeoJSON) listé dans available_formats."""
    for media in rudi_metadata.get("available_formats", []):
        if media.get("media_type") != "FILE":
            continue
        chemin = os.path.join(dossier, media.get("media_name", ""))
        if not os.path.isfile(chemin):
            continue
        if chemin.endswith(".csv"):
            with open(chemin, "rb") as f:
                entetes = entetes_depuis_csv(f.read())
        elif chemin.endswith(".geojson") or chemin.endswith(".json"):
            entetes = entetes_depuis_geojson(chemin)
        else:
            continue
        if entetes:
            return entetes
    return []


def enrichir_un(dossier_nom: str) -> str | None:
    """Régénère summary[0].text si quasi vide et pas déjà enrichi. None si rien à faire."""
    dossier = os.path.join(DATA_DIR, dossier_nom)
    chemin_meta = os.path.join(dossier, "rudi_metadata.json")
    if not os.path.isfile(chemin_meta):
        return None
    with open(chemin_meta, encoding="utf-8") as f:
        meta = json.load(f)

    if not meta.get("summary"):
        return None
    texte_complet = meta["summary"][0].get("text", "")
    reste = partie_descriptive(texte_complet)

    if MARQUEUR in reste:
        return None  # déjà enrichi par un run précédent de ce script
    if not description_quasi_vide(reste):
        return None  # a déjà une vraie description source

    theme = meta.get("theme", "society")
    producteur = (meta.get("producer") or {}).get("organization_name", "Producteur inconnu")
    mots_cles = meta.get("keywords", [])
    colonnes = _colonnes_disponibles(dossier, meta)

    complement = generer_complement(theme=theme, producteur=producteur,
                                     colonnes=colonnes, mots_cles=mots_cles)
    meta["summary"][0]["text"] = texte_complet.rstrip() + "\n\n" + complement

    with open(chemin_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    base = "colonnes détectées" if colonnes else "thème/producteur/mots-clés (pas de fichier filtré lisible)"
    return f"{dossier_nom} : description complétée ({base})"


def main() -> None:
    etats = {
        "tabulaire": charger_state(),
        "insee": charger_state_insee(),
        "oeb": charger_state_oeb(),
        "bdnb": charger_state_bdnb(),
    }
    index = {}  # dossier -> (source, clé dans etats[source])
    for source, etat in etats.items():
        for cle, entree in etat.items():
            index[entree.get("dossier")] = (source, cle)

    dossiers = sorted(
        n for n in os.listdir(DATA_DIR)
        if n != "cache" and os.path.isdir(os.path.join(DATA_DIR, n))
    )
    print(f"=== Enrichissement des descriptions — {len(dossiers)} dossier(s) à vérifier ===\n")
    n_traites = 0
    n_republier = 0
    for nom in dossiers:
        try:
            resultat = enrichir_un(nom)
        except Exception as e:
            print(f"  {nom} : ERREUR — {e}")
            continue
        if resultat:
            print(f"  {resultat}")
            n_traites += 1
            if nom in index:
                source, cle = index[nom]
                if etats[source][cle].get("rudi_publie"):
                    etats[source][cle]["rudi_publie"] = False
                    n_republier += 1

    sauvegarder_state(etats["tabulaire"])
    sauvegarder_state_insee(etats["insee"])
    sauvegarder_state_oeb(etats["oeb"])
    sauvegarder_state_bdnb(etats["bdnb"])

    print(f"\n=== Terminé : {n_traites} description(s) complétée(s) sur {len(dossiers)} dossier(s) ===")
    if n_republier:
        print(f'{n_republier} JDD déjà publié(s) sur le nœud RUDI ont été démarqués '
              f'(rudi_publie remis à false) — leur description enrichie ne sera envoyée '
              f'au nœud qu\'après un nouveau passage de "Publier sur le nœud RUDI".')
    if n_traites:
        print('Pensez à régénérer le catalogue (option 6) et, si besoin, à republier '
              'sur le nœud RUDI (option 7).')


if __name__ == "__main__":
    main()
