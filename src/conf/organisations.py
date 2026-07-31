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

    "Bureau de Recherches Géologiques et Minières":
        "Bureau de recherches géologiques et minières",

    "Département d'Ille-et-Vilaine": "Conseil départemental d'Ille-et-Vilaine",

    # --- Ministères (le nom brut du producteur ne correspond à aucune page Wikipédia réelle ;
    #     ces titres sont les pages actuelles vérifiées, avec logo en infobox) ---
    "Ministère chargé des Sports": "Ministère des Sports (France)",
    "Ministères de l'Éducation nationale": "Ministère de l'Éducation nationale (France)",
    "Ministère de l'Agriculture, de l'agro-alimentaire et de la souveraineté alimentaire":
        "Ministère de l'Agriculture (France)",
    "Ministère du Travail du Plein emploi et de l'Insertion": "Ministère du Travail (France)",
    "Ministère de l'Enseignement supérieur, de la Recherche et de l'Espace":
        "Ministère de l'Enseignement supérieur (France)",
    "Ministère de la Justice": "Ministère de la Justice (France)",
    "Ministères économiques et financiers": "Ministère de l'Économie et des Finances (France)",
    "Ministère de la Culture": "Ministère de la Culture (France)",
    "Ministère de l'intérieur": "Ministère de l'Intérieur (France)",

    # --- Directions déconcentrées de l'État (pages génériques, pas de déclinaison par région/dépt) ---
    "Direction Départementale des Territoires et de la Mer d'Ille-et-Vilaine":
        "Direction départementale des Territoires",
    "Direction régionale de l'environnement, de l'aménagement et du logement de Bretagne":
        "Direction régionale de l'Environnement, de l'Aménagement et du Logement",

    # --- Collectivités ---
    "Rennes Métropole": "Rennes Métropole",

    # --- Plateformes / portails ---
    "data.gouv.fr": "Data.gouv.fr",
    "data.gouv.fr / communes-fr": "Data.gouv.fr",
    "adresse.data.gouv.fr": "Data.gouv.fr",

    # --- Autres ---
    "Etalab": "Etalab",
    "Direction interministérielle du numérique": "Direction interministérielle du numérique",
    "Mission Data.gouv": None,

    "Mégalis Bretagne": {
        "summary": "Mégalis Bretagne est le syndicat mixte qui accompagne les collectivités "
                    "bretonnes dans leurs projets numériques et l'aménagement numérique du "
                    "territoire en très haut débit.",
    },

    # Override manuel : "Ecolab" n'a pas de page Wikipédia propre, et une recherche
    # Wikipédia sur ce nom résout vers l'entreprise américaine de traitement de l'eau/
    # hygiène (homonyme, page Wikipédia FR "Ecolab" bien réelle mais sans rapport).
    # Le vrai producteur est le laboratoire d'innovation numérique du ministère chargé
    # de l'Écologie (CGDD), cf. https://www.data.gouv.fr/organizations/ecolab-1/.
    "Ecolab": {
        "caption": "Laboratoire d'innovation numérique au service de la transition écologique",
        "summary": "Ecolab est le laboratoire de l'innovation au service de la transition "
                    "écologique, situé au sein du Commissariat général au développement "
                    "durable (CGDD), direction interministérielle du ministère chargé de "
                    "l'Environnement. Il produit et diffuse des données et analyses "
                    "environnementales, notamment via la plateforme Écosphères.",
    },
}
