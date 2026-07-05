"""
Configuration et constantes pour le module de découverte (discover.py).

Centralise les listes de motifs, mots-clés, patterns de colonnes, URLs API,
et autres constantes de configuration utilisées par la découverte interactive
et automatique de JDD sur data.gouv.fr.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conf.communes_rm import COMMUNES_RM
from filters.geographic import normaliser


# Paramètres de recherche
KEYWORDS = ["commune", "code postal", "code insee", "iris", "adresse"]

NB_PAGES = 50  # pages récupérées par mot-clé (20 résultats/page → 1000 max par keyword)

PAGE_SIZE = 20  # résultats par page

RECHERCHE_STRUCTUREE = True

REQUETES_STRUCTUREES = [
    {"params": {"featured": "true"},                                              "label": "featured"},
    {"params": {"q": "commune", "granularity": "fr:commune", "sort": "-views"}, "label": "commune + granularité commune"},
    {"params": {"q": "iris",    "granularity": "fr:iris",    "sort": "-views"}, "label": "iris + granularité iris"},
    {"params": {"q": "epci",                                 "sort": "-views"}, "label": "epci"},
    {"params": {"q": "code insee",                           "sort": "-views"}, "label": "code insee"},
    {"params": {"q": "code postal",                          "sort": "-views"}, "label": "code postal"},
    {"params": {"q": "adresse",                              "sort": "-views"}, "label": "adresse"},
    {"params": {"q": "siren",                                "sort": "-views"}, "label": "siren"},
    {"params": {"q": "siret",                                "sort": "-views"}, "label": "siret"},
    {"params": {"q": "sirene",                               "sort": "-views"}, "label": "sirene"},
    {"params": {"organization": "534fff81a3a7292c64a77e5c", "sort": "-views"}, "label": "INSEE"},
    {"params": {"organization": "5c812a16634f416583ed1876", "sort": "-views"}, "label": "Cerema"},
    {"params": {"organization": "534fff8da3a7292c64a77eee", "sort": "-views"}, "label": "MTECT (écologie)"},
    {"params": {"q": "transport",         "sort": "-views"}, "label": "transport"},
    {"params": {"format": "wms",          "sort": "-views"}, "label": "WMS"},
    {"params": {"format": "wfs",          "sort": "-views"}, "label": "WFS"},
    {"params": {"format": "geojson",      "sort": "-views"}, "label": "GeoJSON"},
]

TITRES_HORS_RM = [
    "île-de-france", "ile-de-france", "île de france", "ile de france",
    "occitanie", "provence", "paca",
    "auvergne", "rhône-alpes", "rhone-alpes",
    "grand est", "alsace", "lorraine",
    "hauts-de-france", "nord-pas-de-calais", "picardie",
    "nouvelle-aquitaine", "aquitaine",
    "pays de la loire",
    "centre-val de loire",
    "bourgogne", "franche-comté", "franche-comte",
    "corse",
    "normandie",
]

ORGS_EXCLUES = [
    "rennes-metropole",
    "rennes-metropole-en-acces-libre",
    "sig-rennes-metropole",
    "metropole-de-rennes",
    "ville-de-rennes",
    "agglo",
    "ressourcerie-datalocale-1",
]

ZONES_INCLUANT_RM = {
    "fr:region:53",       # Bretagne
    "fr:departement:35",  # Ille-et-Vilaine
    "fr:epci:243500139",  # Rennes Métropole (SIREN)
}

_FORMATS_EXCLUS_FMT = ("pdf", "shapefile", "ogc", "kml", "gpkg")
_FORMATS_EXCLUS_EXT = (".pdf", ".shp", ".kml", ".gpkg", ".html", ".htm", ".doc", ".docx")

_MAGIC_BINAIRE = [
    b"PK\x03\x04",   # ZIP / XLSX / ODS / DOCX
    b"\x1f\x8b",     # gzip
    b"BZh",          # bzip2
    b"%PDF-",        # PDF
    b"\xd0\xcf\x11\xe0",  # OLE2 (XLS, DOC ancien format)
]

_MOTS_DESC_COMMUNE = [
    "par commune", "par code postal", "par code insee",
    "données communales", "niveau communal",
    "chaque commune", "toutes les communes",
    "code_commune", "code_postal",
]

# Marqueurs dans le titre (phrase exacte normalisée)
MARQUEURS_TITRE = ["par commune", "par communes", "par iris", "par code postal",
                   "par code insee", "par adresse", "par epci"]
# Marqueurs dans la description (mots isolés suffisent)
MARQUEURS_DESC = ["commune", "code postal", "code insee", "iris", "adresse", "epci"]
# Sous-chaînes à chercher dans les en-têtes (wildcard *xxx*)
MARQUEURS_ENTETES_SUBSTR = ["epci", "iris", "insee", "commune", "adresse", "postal"]

# Noms de champs courants pour le code postal et la commune dans les données
CHAMPS_CP = ["cp", "code_postal", "codepostal", "code postal", "postal_code",
             "code_post", "cp_ville", "zipcode", "zip"]
CHAMPS_VILLE = ["ville", "commune", "libelle_commune", "nom_commune",
                "city", "municipality", "lib_commune",
                "libgeo", "lib_geo", "libelle_geo", "libcom", "lib_com",
                "nom_com", "nom_geo", "libelle"]

# Noms de champs courants pour le code IRIS (9 chiffres : 5 INSEE + 4 IRIS)
CHAMPS_IRIS = [
    "code iris", "code iris code", "iris code", "codeiris",
    "c iris", "iris", "com iris", "code iris 2024", "code iris 2023",
    "numero commune", "num commune", "code commune", "depcom", "codgeo",
    "insee", "code insee", "codeinsee", "code commune insee", "codecommune",
    "insee com", "insee comm", "cod commune", "com insee",
    "icom",
    "code geographique",
    "geocode commune", "geocode epci", "code officiel geographique",
    "code officiel commune", "cog commune", "cog",
]

CHAMPS_DEP = ["ndept", "dep", "code dep", "code dept", "num dep", "num dept",
              "departement", "dept", "codedep", "dep commune"]

# Noms de champs courants pour les identifiants d'entreprise SIREN/SIRET
CHAMPS_SIREN = [
    "siren", "siret",
    "n siren", "n siret", "no siren", "no siret",
    "num siren", "num siret", "numero siren", "numero siret",
    "code siren", "code siret",
    "siren siege", "siret siege",
    "siren etablissement", "siret etablissement",
    "identifiant siren", "identifiant siret",
]

CHAMPS_CIRCONSCRIPTION = [
    "circonscription", "circo", "code circonscription", "code circo",
    "circonscription legislative", "circonscription_legislative",
    "num circonscription", "num_circonscription",
    "numero circonscription", "numero_circonscription",
    "n circonscription", "n_circonscription",
]

# Colonnes combinant lat et lon en un seul champ
CHAMPS_GEO_POINT = [
    "geo point 2d", "geo_point_2d", "geo point", "geo_point", "geopoint",
    "coordonnees gps", "coord gps", "point gps", "point_gps",
    "centroid", "centroide", "centroid geom", "centroid_geom",
    "geom centroid", "geom_centroid", "the geom centroid", "the_geom_centroid",
    "wkt centroid", "wkt_centroid", "centroid wkt", "centroid_wkt",
    "centroid wgs84", "centroid_wgs84", "point centroide", "point_centroide",
    "geom", "the geom", "the_geom", "geo shape", "geo_shape",
    "geometrie", "geometry", "geojson", "shape", "wkt",
]

CHAMPS_LAT = [
    "latitude", "lat", "y wgs84", "y_wgs84", "lat wgs84", "lat_wgs84",
    "wgs84 lat", "wgs84_lat",
]
CHAMPS_LON = [
    "longitude", "lon", "lng", "long",
    "x wgs84", "x_wgs84", "lon wgs84", "lon_wgs84",
    "wgs84 lon", "wgs84_lon",
]

# Noms de champs courants pour une adresse textuelle complète
CHAMPS_ADRESSE = [
    "adresse", "adresse complete", "adresse_complete", "adresse postale", "adresse_postale",
    "adresse 1", "adresse1", "adresse voie", "adresse_voie",
    "voie", "libelle voie", "libelle_voie", "libelle de voie",
    "localisation", "lieu dit", "lieu_dit",
]

# En-têtes CSV : liste large, faux positifs acceptés
MARQUEURS_ENTETES = set(
    CHAMPS_IRIS + CHAMPS_DEP + CHAMPS_ADRESSE + CHAMPS_CP + CHAMPS_VILLE
) | {
    "code com", "cod com", "code_com", "cod_com",
    "lib com", "lib_com", "libcom", "libelle com", "libelle_com",
    "lib commune", "lib_commune", "libelle commune", "libelle_commune",
    "nom com", "nom_com", "nom commune", "nom_commune",
    "cod commune", "cod_commune",
    "num iris", "num_iris", "numero iris", "numero_iris",
    "l iris", "l_iris", "lib iris", "lib_iris", "libelle iris", "libelle_iris",
    "tri iris", "tri_iris", "p iris", "p_iris",
    "postal", "zip", "zipcode", "zip code", "zip_code", "code_zip",
    "adrs", "adr", "adresse1", "adresse2", "adresse 2",
    "adr1", "adr2", "adr 1", "adr 2",
    "num rue", "num_rue", "numero rue", "numero_rue",
    "num voie", "num_voie", "numero voie", "numero_voie",
    "rue", "lieu", "localite", "quartier", "secteur", "territoire",
    "siret", "siren", "nic",
    "circonscription", "circo",
    "geo point", "geo_point", "geo point 2d", "geo_point_2d",
    "geo shape", "geo_shape", "geometry", "geom", "wkt",
    "coordonnees", "coordinates", "coord",
    "lon", "lat", "longitude", "latitude",
    "x l93", "y l93", "x_l93", "y_l93", "lambert", "lambert93",
    "point gps", "point_gps", "gps",
    "geocode commune", "geocode epci", "code officiel geographique",
    "code officiel commune", "cog commune", "cog",
    "region", "reg", "lib reg", "lib_reg", "libelle region", "libelle_region",
    "arrondissement", "arr", "l ar", "l_ar",
}


# Chemins de données
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")

DECOUVERTE_FILE    = os.path.join(_DATA_DIR, "decouverte.json")
LOG_FILE           = os.path.join(_DATA_DIR, "discover.log")
CACHE_DIR          = os.path.join(_DATA_DIR, "cache")
RESULTATS_API_FILE = os.path.join(_DATA_DIR, "derniere_recherche.json")
PREFILTRES_FILE    = os.path.join(_DATA_DIR, "derniers_prefiltres.json")
GEO_FILE           = os.path.join(_DATA_DIR, "geo_services.json")
