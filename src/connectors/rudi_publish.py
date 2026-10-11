"""
Publication RUDI partagée entre tous les scripts de moisson.

Élimine le bloc try/except de publication copié dans main.py,
harvest_insee.py, harvest_oeb.py, harvest_bdnb.py, harvest_geo.py.

Usage :
    from connectors.rudi_publish import publier_si_configue
    rudi_publie = publier_si_configue(rudi_metadata, fichiers_filtres)
"""

import threading

from connectors.rudi_node import charger_conf_rudi, noeud_pret, publier_dataset

# Les publications arrivent depuis plusieurs threads (workers de harvest_batch,
# moissons INSEE/OEB/BDNB/géo parallélisées dans cli.py). Le get_or_create
# d'organisations/contacts côté nœud n'est pas idempotent sous concurrence
# (risque de doublons), donc on sérialise la publication — le téléchargement/
# filtrage, lui, reste parallèle.
_verrou_publication = threading.Lock()

# local_id des JDD effectivement (re)publiés avec succès depuis le dernier
# demarrer_nouveau_run() — sert de signal "nouveau ou modifié" pour l'étape
# rudi-geo (publish_rudi_geo.py) : publier_si_configue() n'est appelé par
# chaque script de moisson que lorsqu'il y a reellement quelque chose de
# change a publier (chacun a son propre fast-path "rien de change, on
# saute" en amont) — ce set est donc deja exactement le bon perimetre, sans
# nouvelle detection de changement a inventer. Proteger par le meme verrou
# que la publication elle-meme (deja pris a chaque appel, aucun cout
# supplementaire).
_local_ids_publies_ce_run: set[str] = set()


def demarrer_nouveau_run() -> None:
    """A appeler au debut d'un run de pipeline (executer_pipeline_complet) --
    vide l'accumulateur du run precedent. Necessaire car le process peut
    vivre plus longtemps qu'un run (dashboard.py), contrairement a un script
    autonome toujours lance dans un process frais."""
    with _verrou_publication:
        _local_ids_publies_ce_run.clear()


def local_ids_publies_ce_run() -> set[str]:
    """local_id des JDD (re)publies avec succes depuis le dernier
    demarrer_nouveau_run()."""
    with _verrou_publication:
        return set(_local_ids_publies_ce_run)


def enregistrer_local_id_publie(local_id: str | None) -> None:
    """Enregistre un local_id comme publie ce run. Appele par
    publier_si_configue() ET par la boucle de rattrapage de publish_rudi.py
    (qui appelle publier_dataset() directement, sans passer par
    publier_si_configue() -- les deux chemins doivent alimenter le meme
    accumulateur)."""
    if not local_id:
        return
    with _verrou_publication:
        _local_ids_publies_ce_run.add(local_id)


def publier_si_configue(rudi_metadata: dict, fichiers_filtres: list[str]) -> bool:
    """Tente de publier sur le nœud RUDI si celui-ci est configuré.

    Retourne True si la publication a réussi, False si elle a échoué
    ou si le nœud n'est pas configuré.
    """
    conf_rudi = charger_conf_rudi()
    if not conf_rudi:
        print("  [RUDI] rudi_node.json absent — publication ignorée.")
        return False

    # Sonde préalable : un nœud injoignable échouerait de toute façon, mais après le
    # retry/backoff de la lib de publication et sur une trace peu lisible. Le
    # rattrapage (publish_rudi.py) reprendra ce False au prochain passage.
    if not noeud_pret(conf_rudi):
        print("  [RUDI] nœud injoignable — publication différée.")
        return False

    try:
        with _verrou_publication:
            publier_dataset(conf=conf_rudi, rudi_metadata=rudi_metadata,
                            fichiers_filtres=fichiers_filtres)
        enregistrer_local_id_publie(rudi_metadata.get("local_id"))
        print("  [RUDI] Publié.")
        return True
    except Exception as e:
        print(f"  [RUDI] Erreur publication : {e}")
        return False
