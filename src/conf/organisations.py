"""
Carte d'alias pour les organisations productrices connues.

Clé : nom normalisé tel qu'il apparaît dans `organization_name` (après
_nettoyer_nom_producteur). Valeur :
  - `str` : titre Wikipédia exact (lookup direct)
  - `None` : forcer le repli factuel (pas de recherche Wikipédia)
  - `dict` avec `caption` et/ou `summary` : override manuel (pas de recherche Wikipédia)
"""
from typing import Union

ALIAS_ORGANISATIONS: dict[str, Union[str, None, dict]] = {
    # --- Organismes publics / État ---
    "Institut national de la statistique et des études économiques":
        "Institut national de la statistique et des études économiques",
    "Institut national de la statistique et des études économiques (Insee)":
        "Institut national de la statistique et des études économiques",

    "Centre Scientifique et Technique du Bâtiment":
        "Centre scientifique et technique du bâtiment",
    "Centre Scientifique et Technique du Bâtiment (CSTB)":
        "Centre scientifique et technique du bâtiment",

    "Observatoire de l'Environnement en Bretagne":
        "Observatoire de l'Environnement en Bretagne",
    "Observatoire de l'Environnement en Bretagne (OEB)":
        "Observatoire de l'Environnement en Bretagne",

    "Cerema": "Cerema",

    "Institut national de l'information géographique et forestière":
        "Institut national de l'information géographique et forestière",
    "Institut national de l'information géographique et forestière (IGN)":
        "Institut national de l'information géographique et forestière",

    # --- Collectivités ---
    "Rennes Métropole": "Rennes Métropole",

    # --- Plateformes / portails ---
    "data.gouv.fr": None,

    # --- Autres ---
    "Etalab": "Etalab",
    "Direction interministérielle du numérique": "Direction interministérielle du numérique",
    "Mission Data.gouv": None,
}
