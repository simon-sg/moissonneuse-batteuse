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

from rudi_harvest_toolkit.publish import local_ids_publies_ce_run
from rudi_harvest_toolkit.rudi_client import (
    charger_conf_rudi, marquer_organisations_traitees,
    organisations_nouvellement_validees, renvoyer_ressources_connues,
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
    concernees = ressources_pour_organisations(metadonnees, nouvelles)

    # Ne renvoyer QUE les ressources encore bloquées (`refused`) — jamais `published`/`sent`.
    # Repousser une ressource déjà intégrée redéclenche `sendToPortal()` côté nœud
    # (fire-and-forget, donc concurrent) pendant que Kalim traite encore l'envoi précédent :
    # le `GET` préalable du nœud ne trouve pas encore la ressource → `POST` en double →
    # `ERR-304` (« valeur déjà utilisée »), qui fait repasser le JDD en `refused` alors
    # qu'il est bel et bien dans le portail. C'est la cause des JDD « visibles dans le
    # portail mais refusés sur le nœud ».
    # Et jamais une ressource déjà (re)publiée par un autre maillon de CE run (moisson
    # inline, rattrapage `publish_rudi`) : son envoi est déjà en vol, un second envoi
    # concurrent recréerait exactement le doublon ci-dessus.
    deja_ce_run = local_ids_publies_ce_run()
    a_renvoyer = [m for m in concernees
                  if m.get("metadata_status") == "refused"
                  and m.get("local_id") not in deja_ce_run]
    # Ressources bloquées mais dont l'envoi de ce run est encore en vol : on ne les renvoie
    # pas maintenant, mais on ne marque pas non plus leur organisation comme traitée — une
    # organisation validée en cours de run pourrait avoir été refusée (ERR-112) avant sa
    # validation, on retentera donc au prochain passage.
    en_vol = [m for m in concernees
              if m.get("metadata_status") == "refused"
              and m.get("local_id") in deja_ce_run]

    if not a_renvoyer:
        if concernees:
            print(f"{len(concernees)} ressource(s) pour ces organisations, aucune à renvoyer "
                  f"({len(concernees) - len(en_vol)} déjà publiée(s)/en cours, "
                  f"{len(en_vol)} déjà envoyée(s) par ce run).")
        else:
            print("Aucune ressource locale ne référence ces organisations — rien à renvoyer.")
        if not en_vol:
            marquer_organisations_traitees(CHEMIN_ETAT, nouvelles)
        return

    print(f"{len(a_renvoyer)} ressource(s) bloquée(s) à renvoyer"
          f"{f' ({len(en_vol)} déjà envoyée(s) par ce run, ignorée(s))' if en_vol else ''}, "
          f"renvoi vers le portail — PUT producteur normal (un par un, pause 2s entre chaque : "
          f"sendToPortal est fire-and-forget côté nœud, jamais en lot — voir "
          f"renvoyer_ressources_connues)...")
    bilan = renvoyer_ressources_connues(conf_rudi, a_renvoyer)
    print(f"Bilan : {bilan['ok']} renvoyé(s), {len(bilan['echecs'])} échec(s).")
    for gid, err in bilan["echecs"]:
        print(f"  ERREUR {gid} : {err}")

    # Marquées traitées après la tentative (pas avant) : si renvoyer_ressources_connues()
    # levait une exception avant d'arriver ici, la prochaine exécution les
    # retrouverait encore « nouvelles » et retenterait. Idem si des ressources bloquées
    # ont été laissées de côté parce qu'envoyées par ce run (voir `en_vol`).
    if not en_vol:
        marquer_organisations_traitees(CHEMIN_ETAT, nouvelles)


if __name__ == "__main__":
    main()
