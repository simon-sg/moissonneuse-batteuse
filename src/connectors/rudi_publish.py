"""
Publication RUDI partagée entre tous les scripts de moisson.

Élimine le bloc try/except de publication copié dans main.py,
harvest_insee.py, harvest_oeb.py, harvest_bdnb.py, harvest_geo.py.

Support multi-nœuds : publie sur tous les nœuds configurés et retourne
un dict ``{nom_noeud: bool}`` au lieu d'un simple booléen.

Usage :
    from connectors.rudi_publish import publier_si_configue
    resultats = publier_si_configue(rudi_metadata, fichiers_filtres)
    # resultats = {"docker": True, "source": False}
"""

import copy
import threading

from connectors.rudi_node import charger_confs_rudi, noeud_pret, publier_dataset

# Les publications arrivent depuis plusieurs threads (workers de harvest_batch,
# moissons INSEE/OEB/BDNB/géo parallélisées dans cli.py). Le get_or_create
# d'organisations/contacts côté nœud n'est pas idempotent sous concurrence
# (risque de doublons), donc on sérialise par nœud — le téléchargement/filtrage,
# lui, reste parallèle.
_verrous: dict[str, threading.Lock] = {}
_verrous_lock = threading.Lock()


def _verrou_noeud(nom: str) -> threading.Lock:
    """Retourne (en créant si nécessaire) le verrou dédié à un nœud."""
    with _verrous_lock:
        if nom not in _verrous:
            _verrous[nom] = threading.Lock()
        return _verrous[nom]


def publier_si_configue(rudi_metadata: dict, fichiers_filtres: list[str]) -> dict[str, bool]:
    """Tente de publier sur tous les nœuds RUDI configurés.

    Retourne un dict ``{nom_noeud: True|False}`` indiquant le résultat
    par nœud. Un échec sur un nœud n'empêche jamais les autres.
    Si aucun nœud n'est configuré, retourne ``{}``.
    """
    noeuds = charger_confs_rudi()
    if not noeuds:
        print("  [RUDI] aucun nœud configuré (rudi_nodes.json / rudi_node.json absent) — publication ignorée.")
        return {}

    resultats: dict[str, bool] = {}
    for noeud in noeuds:
        nom = noeud.get("nom", "inconnu")

        # Sonde préalable : un nœud arrêté échouerait de toute façon, mais après le
        # retry/backoff de la lib de publication et sur une trace peu lisible. Le
        # rattrapage (publish_rudi.py) reprendra ce False au prochain passage.
        if not noeud_pret(noeud):
            print(f"  [RUDI] nœud « {nom} » injoignable — publication différée.")
            resultats[nom] = False
            continue

        try:
            with _verrou_noeud(nom):
                # Copie par nœud : publier_dataset() mute la fiche en place (global_id,
                # producer, contacts et available_formats sont remplacés par les objets
                # renvoyés par le nœud). Sans copie, le 2e nœud recevrait une fiche
                # portant déjà l'empreinte du 1er.
                publier_dataset(conf=noeud, rudi_metadata=copy.deepcopy(rudi_metadata),
                                fichiers_filtres=fichiers_filtres)
            print(f"  [RUDI] Publié sur « {nom} ».")
            resultats[nom] = True
        except Exception as e:
            print(f"  [RUDI] Erreur publication sur « {nom} » : {e}")
            resultats[nom] = False

    return resultats
