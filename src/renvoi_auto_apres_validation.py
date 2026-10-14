"""
Renvoie automatiquement vers le portail les JDD déjà publiés sur le nœud RUDI restés
`refused`, dans deux cas :

1. **Organisation nouvellement validée** — le rapport portail est arrivé
   (`organization_status=VALIDATED`), les JDD qui étaient bloqués en `ERR-112` peuvent
   être repoussés. Portée restreinte aux organisations nouvellement validées depuis le
   dernier appel (état persisté dans `data/etat_organisations_validees.json`).
2. **JDD `refused` hors vol** dont l'organisation est déjà `VALIDATED` — rattrapage
   automatique des refus transitoires / anciens (`ERR-304`, `ERR-500` corrigés depuis)
   qui ne sont plus rattachés à une validation fraîche. Jamais une ressource déjà
   (re)publiée par un autre maillon de ce run (`local_ids_publies_ce_run()`), et par
   défaut jamais une ressource dont l'organisation n'est pas encore validée (ce serait
   un `ERR-112` futile, repris dès la validation grâce au cas 1) — sauf avec
   ``force=True``/``--force`` (voir ci-dessous), qui désactive ce filtre.

**`force`** (ex-menu 24, `reparer_jdd_refuses.py`, fusionné ici le 2026-10-14) : renvoie
TOUS les JDD `refused` hors vol, y compris ceux dont l'organisation n'est pas (encore)
marquée `VALIDATED` — le levier manuel pour forcer une reprise immédiate sans attendre un
passage du pipeline, ou pour un refus dont la cause n'a rien à voir avec une organisation
(vieux `ERR-304`/`ERR-500` déjà corrigés). Sans ``force``, ce script reste sans risque à
rejouer ou automatiser (jamais un `ERR-112` futile) ; avec ``force``, c'est un geste
délibéré — un JDD dont l'organisation n'est réellement pas validée sera retenté pour rien
(mais sans effet de bord, le PUT producteur est idempotent).

Travaille uniquement à partir de ce que le nœud sait déjà (organisations, métadonnées
déjà publiées) : ne republie rien de nouveau, ne retélécharge/refiltre rien. Pensé pour
un usage périodique (cron, ou relancé à la main après chaque session de validation dans
l'UI Strukture) — jamais tout le catalogue.

Usage (autonome, sans dépendance à un agent) :
    python3 src/renvoi_auto_apres_validation.py [--force]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rudi_harvest_toolkit.publish import local_ids_publies_ce_run
from rudi_harvest_toolkit.rudi_client import (
    charger_conf_rudi, lister_organisations, marquer_organisations_traitees,
    organisations_nouvellement_validees, renvoyer_ressources_connues,
    toutes_metadonnees_rudi)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CONF_RUDI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json")
CHEMIN_ETAT = os.path.join(DATA_DIR, "etat_organisations_validees.json")


def main(force: bool = False) -> None:
    conf_rudi = charger_conf_rudi(CONF_RUDI_FILE)
    if not conf_rudi:
        print("src/conf/rudi_node.json absent — impossible de vérifier les organisations.")
        return

    nouvelles = organisations_nouvellement_validees(conf_rudi, CHEMIN_ETAT)
    if nouvelles:
        print(f"{len(nouvelles)} organisation(s) nouvellement validée(s) côté portail : {nouvelles}")
    else:
        print("Aucune organisation nouvellement validée depuis le dernier contrôle.")

    metadonnees = toutes_metadonnees_rudi(conf_rudi)
    deja_ce_run = local_ids_publies_ce_run()
    validees = {o["organization_id"]
                for o in lister_organisations(conf_rudi, statut="VALIDATED")
                if o.get("organization_id")}

    def _org_validee(m: dict) -> bool:
        org_id = (m.get("producer") or {}).get("organization_id")
        # Pas d'organisation rattachée : on ne peut pas conclure à un ERR-112, on retente.
        return not org_id or org_id in validees

    # 1. Résultats de validation fraîche : ressources `refused` des organisations
    #    nouvellement validées (cas historique).
    # 2. Rattrapage automatique : toutes les ressources `refused` hors vol dont
    #    l'organisation est VALIDATED — refus transitoires ou anciens qu'aucune validation
    #    fraîche ne reprendrait jamais. Union des deux pour ne pas envoyer deux fois la même.
    #    `force=True` désactive le filtre `_org_validee` (ex-menu 24) : renvoie tout JDD
    #    `refused` hors vol, même si son organisation n'est pas (encore) VALIDATED.
    #
    # Dans tous les cas, on ne renvoie QUE des ressources `refused` (jamais `published`/
    # `sent`) : repousser une ressource déjà intégrée redéclenche `sendToPortal()` côté nœud
    # (fire-and-forget, donc concurrent) pendant que Kalim traite encore l'envoi précédent —
    # le `GET` préalable du nœud ne trouve pas encore la ressource → `POST` en double →
    # `ERR-304` (« valeur déjà utilisée »), qui fait repasser le JDD en `refused` alors qu'il
    # est bel et bien dans le portail. Et jamais une ressource déjà (re)publiée par un autre
    # maillon de CE run (moisson inline, rattrapage `publish_rudi`) : son envoi est déjà en
    # vol, un second envoi concurrent recréerait exactement le doublon ci-dessus.
    a_renvoyer = [m for m in metadonnees
                  if m.get("metadata_status") == "refused"
                  and m.get("local_id") not in deja_ce_run
                  and (force or _org_validee(m))]
    if a_renvoyer:
        print(f"{len(a_renvoyer)} ressource(s) `refused` à renvoyer "
              f"({'forcé, organisation ignorée' if force else 'organisation validée'}, pas "
              f"en vol), renvoi vers le portail — PUT producteur normal (un par un, pause 2s "
              f"entre chaque : sendToPortal est fire-and-forget côté nœud, jamais en lot — "
              f"voir renvoyer_ressources_connues)...")
        bilan = renvoyer_ressources_connues(conf_rudi, a_renvoyer)
        print(f"Bilan : {bilan['ok']} renvoyé(s), {len(bilan['echecs'])} échec(s).")
        for gid, err in bilan["echecs"]:
            print(f"  ERREUR {gid} : {err}")
    else:
        print("Aucune ressource `refused` à renvoyer.")

    # Les organisations nouvellement validées sont marquées traitées après la tentative (pas
    # avant) : si renvoyer_ressources_connues() levait une exception, la prochaine exécution
    # les retrouverait encore « nouvelles » et retenterait. On attend aussi qu'aucune de
    # LEURS ressources refusées ne soit encore en vol (sinon on retente via `nouvelles`) —
    # les ressources refusées des autres organisations restent, elles, reprises par le
    # rattrapage automatique du point 2 tant que l'organisation est VALIDATED.
    if nouvelles:
        en_vol_nouvelles = [m for m in metadonnees
                            if m.get("metadata_status") == "refused"
                            and m.get("local_id") in deja_ce_run
                            and (m.get("producer") or {}).get("organization_id") in set(nouvelles)]
        if not en_vol_nouvelles:
            marquer_organisations_traitees(CHEMIN_ETAT, nouvelles)


if __name__ == "__main__":
    main(force="--force" in sys.argv[1:])
