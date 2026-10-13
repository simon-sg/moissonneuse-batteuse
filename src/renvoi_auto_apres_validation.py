"""
Renvoie automatiquement vers le portail les JDD déjà publiés sur le nœud RUDI dont
l'organisation productrice vient d'être validée par un modérateur (rapport portail
reçu par le nœud : organization_status=VALIDATED).

Travaille uniquement à partir de ce que le nœud sait déjà (organisations, métadonnées
déjà publiées) : ne republie rien de nouveau, ne retélécharge/refiltre rien. Pensé
pour un usage périodique (cron, ou relancé à la main après chaque session de
validation dans l'UI Strukture) — la portée est automatiquement restreinte aux
seules organisations nouvellement validées depuis le dernier appel (état persisté
dans data/etat_organisations_validees.json), jamais tout le catalogue.

Usage (autonome, sans dépendance à un agent) :
    python3 src/renvoi_auto_apres_validation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rudi_harvest_toolkit.rudi_client import (
    charger_conf_rudi, marquer_organisations_traitees,
    organisations_nouvellement_validees, renvoyer_ressources_noeud,
    ressources_pour_organisations, toutes_metadonnees_rudi)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CONF_RUDI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json")
CHEMIN_ETAT = os.path.join(DATA_DIR, "etat_organisations_validees.json")


def main() -> None:
    conf_rudi = charger_conf_rudi(CONF_RUDI_FILE)
    if not conf_rudi:
        print("src/conf/rudi_node.json absent — impossible de vérifier les organisations.")
        return

    nouvelles = organisations_nouvellement_validees(conf_rudi, CHEMIN_ETAT)
    if not nouvelles:
        print("Aucune organisation nouvellement validée depuis le dernier contrôle — rien à renvoyer.")
        return
    print(f"{len(nouvelles)} organisation(s) nouvellement validée(s) côté portail : {nouvelles}")

    metadonnees = toutes_metadonnees_rudi(conf_rudi)
    a_renvoyer = ressources_pour_organisations(metadonnees, nouvelles)
    if not a_renvoyer:
        print("Aucune ressource locale ne référence ces organisations — rien à renvoyer.")
        marquer_organisations_traitees(CHEMIN_ETAT, nouvelles)
        return

    global_ids = [m["global_id"] for m in a_renvoyer if m.get("global_id")]
    print(f"{len(global_ids)} ressource(s) concernée(s), renvoi vers le portail "
          f"(un par un, pause 2s entre chaque — jamais en lot, voir renvoyer_ressources_noeud)...")
    bilan = renvoyer_ressources_noeud(conf_rudi, global_ids)
    print(f"Bilan : {bilan['ok']} renvoyé(s), {len(bilan['echecs'])} échec(s).")
    for gid, err in bilan["echecs"]:
        print(f"  ERREUR {gid} : {err}")

    # Marquées traitées après la tentative (pas avant) : si renvoyer_ressources_noeud()
    # levait une exception avant d'arriver ici, la prochaine exécution les
    # retrouverait encore « nouvelles » et retenterait.
    marquer_organisations_traitees(CHEMIN_ETAT, nouvelles)


if __name__ == "__main__":
    main()
