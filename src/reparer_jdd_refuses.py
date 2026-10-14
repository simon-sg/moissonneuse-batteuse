"""
Réparation ponctuelle : re-soumet au portail les JDD marqués `refused` sur le nœud RUDI.

Certains JDD sont bel et bien intégrés au portail mais restent `metadata_status: refused`
sur le nœud : un second envoi concurrent a produit un `ERR-304` (« valeur déjà utilisée »)
côté Kalim, qui a écrasé le statut (voir `renvoi_auto_apres_validation`).

Depuis que `renvoi_auto_apres_validation` retente aussi les `refused` hors vol dont
l'organisation est `VALIDATED` (étape du pipeline), ce script est un **levier manuel** :
il force une reprise immédiate, sans attendre un passage du pipeline, et couvre un JDD
`refused` dont l'organisation n'est pas (plus) marquée `VALIDATED`.

Ce script les re-soumet **une seule fois**, un par un (pause de sécurité entre chaque :
`sendToPortal()` est fire-and-forget côté nœud). Cette fois aucun autre envoi n'est en vol :
le `GET` préalable du nœud trouve la ressource côté portail, fait un `PUT` de mise à jour →
`OK`, et le JDD repasse en `published`. Idempotent : relancé avec plus aucun JDD refusé, il
ne fait rien.

Usage :
    python3 src/reparer_jdd_refuses.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rudi_harvest_toolkit.rudi_client import (
    charger_conf_rudi, renvoyer_ressources_connues, toutes_metadonnees_rudi)

CONF_RUDI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json")


def main() -> None:
    conf_rudi = charger_conf_rudi(CONF_RUDI_FILE)
    if not conf_rudi:
        print("src/conf/rudi_node.json absent — impossible de vérifier les JDD refusés.")
        return

    refuses = [m for m in toutes_metadonnees_rudi(conf_rudi)
               if m.get("metadata_status") == "refused"]
    if not refuses:
        print("Aucun JDD refusé sur le nœud — rien à réparer.")
        return

    print(f"{len(refuses)} JDD refusé(s) — re-soumission unique vers le portail, un par un "
          f"(PUT producteur normal, pause 2s entre chaque)...")
    bilan = renvoyer_ressources_connues(conf_rudi, refuses)
    print(f"Bilan : {bilan['ok']} re-soumis, {len(bilan['echecs'])} échec(s).")
    for gid, err in bilan["echecs"]:
        print(f"  ERREUR {gid} : {err}")
    print("Les JDD déjà intégrés au portail repasseront en `published` une fois le rapport "
          "portail revenu (revérifier `metadata_status` côté nœud).")


if __name__ == "__main__":
    main()
