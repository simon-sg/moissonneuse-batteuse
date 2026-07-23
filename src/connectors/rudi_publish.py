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
        print("  [RUDI] Publié.")
        return True
    except Exception as e:
        print(f"  [RUDI] Erreur publication : {e}")
        return False
