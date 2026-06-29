"""
Script de découverte interactive de JDD éligibles sur data.gouv.fr.

Usage : python3 src/discover.py
"""

import sys
import os
import json
import csv
import io
import re
import textwrap
import datetime
import hashlib
import zipfile
import gzip
import warnings
warnings.filterwarnings("ignore", module="requests")
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, as_completed

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from filters.geographic import est_dans_rm, est_commune_rm, normaliser
from conf.communes_rm import CODES_POSTAUX_RM, CODES_INSEE_RM, COMMUNES_RM
from connectors.sirene import obtenir_sirens_rm

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KEYWORDS = ["commune", "code postal", "code insee", "iris", "adresse"]

NB_PAGES = 50  # pages récupérées par mot-clé (20 résultats/page → 1000 max par keyword)

# Recherche structurée : utilise les filtres API plutôt que les mots-clés texte
# Mettre à False pour revenir à la recherche par mots-clés
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
]

# Mots dans le titre indiquant un territoire clairement hors RM
# (datasets sans zones spatiales déclarées mais dont le titre trahit la portée)
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

# Slugs d'organisations à exclure (déjà publient sur RM ou hors-sujet)
ORGS_EXCLUES = [
    "rennes-metropole",
    "rennes-metropole-en-acces-libre",
    "sig-rennes-metropole",
    "metropole-de-rennes",
    "ville-de-rennes",
    "agglo",                    # agglomérations hors RM (Saint-Nazaire agglo, etc.)
    "ressourcerie-datalocale-1", # datacat.datalocale.fr hors service (DNS mort, 524 JDD inaccessibles)
]

# Noms de champs courants pour le code postal et la commune dans les données
CHAMPS_CP = ["cp", "code_postal", "codepostal", "code postal", "postal_code",
             "code_post", "cp_ville", "zipcode", "zip"]
CHAMPS_VILLE = ["ville", "commune", "libelle_commune", "nom_commune",
                "city", "municipality", "lib_commune",
                "libgeo", "lib_geo", "libelle_geo", "libcom", "lib_com",
                "nom_com", "nom_geo", "libelle"]
# Noms de champs courants pour le code IRIS (9 chiffres : 5 INSEE + 4 IRIS)
# Noms normalisés (sans accents, _ → espace) pour les codes IRIS ou INSEE commune
# normaliser() est appliqué aux en-têtes avant comparaison
CHAMPS_IRIS = [
    # Code IRIS complet (9 chiffres : INSEE 5 + IRIS 4)
    "code iris", "code iris code", "iris code", "codeiris",
    "c iris", "iris", "com iris", "code iris 2024", "code iris 2023",
    # Code commune INSEE (5 chiffres) — fichiers IRIS avec codes séparés
    # ex: 'Numéro commune' + 'Numéro d'IRIS' → on matche la partie INSEE
    "numero commune", "num commune", "code commune", "depcom", "codgeo",
    # Colonne nommée directement 'INSEE' ou 'code_insee' (ex: balances comptables DGFiP)
    "insee", "code insee", "codeinsee", "code commune insee", "codecommune",
    "insee com", "insee comm", "cod commune", "com insee",
    "icom",  # Comptes individuels DGFiP (identifiant commune, 3 chiffres + dep)
    # 'Code géographique' = alias INSEE standard (ex: Revenus des français à la commune INSEE)
    "code geographique",
    # COG (Code Officiel Géographique) — variantes avec préfixe "geocode"
    "geocode commune", "geocode epci", "code officiel geographique",
    "code officiel commune", "cog commune", "cog",
]
# Colonnes contenant le code département (2 chiffres) — utilisées pour reconstituer
# un code INSEE complet quand la colonne INSEE ne contient que 3 chiffres (ex: DGFiP balances)
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
# Colonnes combinant lat et lon en un seul champ (format OpenDataSoft "lat,lon")
CHAMPS_GEO_POINT = [
    "geo point 2d", "geo_point_2d", "geo point", "geo_point", "geopoint",
    "coordonnees gps", "coord gps", "point gps", "point_gps",
]
# Colonnes latitude et longitude séparées
CHAMPS_LAT = [
    "latitude", "lat", "y wgs84", "y_wgs84", "lat wgs84", "lat_wgs84",
    "wgs84 lat", "wgs84_lat",
]
CHAMPS_LON = [
    "longitude", "lon", "lng", "long",
    "x wgs84", "x_wgs84", "lon wgs84", "lon_wgs84",
    "wgs84 lon", "wgs84_lon",
]
# Bounding box de Rennes Métropole en WGS84 (avec marge de ~5 km)
_RM_LAT_MIN, _RM_LAT_MAX = 47.80, 48.35
_RM_LON_MIN, _RM_LON_MAX = -2.00, -1.30

# Noms de champs courants pour une adresse textuelle complète (fallback si pas de CP/ville/IRIS)
CHAMPS_ADRESSE = [
    "adresse", "adresse complete", "adresse_complete", "adresse postale", "adresse_postale",
    "adresse 1", "adresse1", "adresse voie", "adresse_voie",
    "voie", "libelle voie", "libelle_voie", "libelle de voie",
    "localisation", "lieu dit", "lieu_dit",
]

# Ensemble normalisé des noms de communes RM (pour la recherche dans les adresses)
_COMMUNES_NORM_RM = {normaliser(c) for c in COMMUNES_RM}
# Regex pour extraire un code postal 35xxx depuis une adresse textuelle
_RE_CP_35 = re.compile(r'\b(35\d{3})\b')

# Marqueurs dans le titre (phrase exacte normalisée)
MARQUEURS_TITRE = ["par commune", "par communes", "par iris", "par code postal",
                   "par code insee", "par adresse", "par epci"]
# Marqueurs dans la description (mots isolés suffisent)
MARQUEURS_DESC = ["commune", "code postal", "code insee", "iris", "adresse", "epci"]
# Sous-chaînes à chercher dans les en-têtes (wildcard *xxx*) — complètent MARQUEURS_ENTETES
MARQUEURS_ENTETES_SUBSTR = ["epci", "iris", "insee", "commune", "adresse", "postal"]
# En-têtes CSV : liste large, faux positifs acceptés
MARQUEURS_ENTETES = set(
    CHAMPS_IRIS + CHAMPS_DEP + CHAMPS_ADRESSE + CHAMPS_CP + CHAMPS_VILLE
) | {
    # Variantes communes
    "code com", "cod com", "code_com", "cod_com",
    "lib com", "lib_com", "libcom", "libelle com", "libelle_com",
    "lib commune", "lib_commune", "libelle commune", "libelle_commune",
    "nom com", "nom_com", "nom commune", "nom_commune",
    "cod commune", "cod_commune",
    # IRIS variantes
    "num iris", "num_iris", "numero iris", "numero_iris",
    "l iris", "l_iris", "lib iris", "lib_iris", "libelle iris", "libelle_iris",
    "tri iris", "tri_iris", "p iris", "p_iris",
    # Code postal variantes
    "postal", "zip", "zipcode", "zip code", "zip_code", "code_zip",
    # Adresse variantes
    "adrs", "adr", "adresse1", "adresse2", "adresse 2",
    "adr1", "adr2", "adr 1", "adr 2",
    "num rue", "num_rue", "numero rue", "numero_rue",
    "num voie", "num_voie", "numero voie", "numero_voie",
    "rue", "lieu", "localite", "quartier", "secteur", "territoire",
    # Identifiants entreprises (souvent liés à une adresse commune)
    "siret", "siren", "nic",
    # Géométrie / coordonnées
    "geo point", "geo_point", "geo point 2d", "geo_point_2d",
    "geo shape", "geo_shape", "geometry", "geom", "wkt",
    "coordonnees", "coordinates", "coord",
    "lon", "lat", "longitude", "latitude",
    "x l93", "y l93", "x_l93", "y_l93", "lambert", "lambert93",
    "point gps", "point_gps", "gps",
    # COG (Code Officiel Géographique)
    "geocode commune", "geocode epci", "code officiel geographique",
    "code officiel commune", "cog commune", "cog",
    # Région / arrondissement (faux positifs assumés)
    "region", "reg", "lib reg", "lib_reg", "libelle region", "libelle_region",
    "arrondissement", "arr", "l ar", "l_ar",
}

PAGE_SIZE = 20  # résultats par page

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

DECOUVERTE_FILE    = os.path.join(_DATA_DIR, "decouverte.json")
LOG_FILE           = os.path.join(_DATA_DIR, "discover.log")
CACHE_DIR          = os.path.join(_DATA_DIR, "cache")
RESULTATS_API_FILE = os.path.join(_DATA_DIR, "derniere_recherche.json")
PREFILTRES_FILE    = os.path.join(_DATA_DIR, "derniers_prefiltres.json")

# ---------------------------------------------------------------------------
# Pré-filtrage automatique (description + en-têtes CSV)
# ---------------------------------------------------------------------------

def _contient_marqueurs_geo(dataset: dict) -> bool:
    """Vérifie titre et description sans télécharger quoi que ce soit."""
    titre = normaliser(dataset.get("title", "") or "")
    desc  = normaliser(dataset.get("description", "") or "")
    if any(m in titre for m in MARQUEURS_TITRE):
        return True
    return any(m in desc for m in MARQUEURS_DESC)


def _telecharger_entetes(url: str) -> list[str] | None:
    """Télécharge les premiers octets d'une URL et retourne la 1ère ligne découpée."""
    try:
        resp = requests.get(url, stream=True, timeout=10)
        if resp.status_code != 200:
            return None
        contenu = b""
        for chunk in resp.iter_content(chunk_size=4096):
            contenu += chunk
            if b"\n" in contenu or len(contenu) > 16384:
                break
        resp.close()
        # Détection binaire rapide
        if contenu[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0") or contenu[:2] == b"\x1f\x8b":
            return None
        texte = contenu.decode("utf-8", errors="replace")
        if texte.count("�") >= 1:
            texte = contenu.decode("latin-1")
        premiere_ligne = texte.split("\n")[0].strip().strip("\r")
        if not premiere_ligne:
            return None
        # Détecter le délimiteur
        nb_cols, delimiteur = 0, ","
        for sep in (";", "\t", "|", ","):
            n = len(premiere_ligne.split(sep))
            if n > nb_cols:
                nb_cols, delimiteur = n, sep
        return [e.strip().strip('"').strip("'") for e in premiere_ligne.split(delimiteur)]
    except Exception:
        return None


def _telecharger_schema_parquet(url: str) -> list[str] | None:
    """Lit uniquement le footer Parquet (~140 Ko) et retourne les noms de colonnes."""
    try:
        import pyarrow.parquet as pq
        import fsspec
        with fsspec.open(url, "rb") as f:
            return [field.name for field in pq.ParquetFile(f).schema_arrow]
    except Exception:
        return None


def pre_filtrer(dataset: dict) -> tuple[str, dict | None]:
    """
    Pipeline automatique en 3 étapes :
      1. Description/titre → marqueurs géo ?
      2. Si non → en-têtes CSV (download léger) → géocode trouvé ?
      3. Si oui (étape 1 ou 2) → analyse complète → RM trouvé ?

    Retourne :
      ("skip",      None)    pas de marqueurs → ignorer silencieusement
      ("candidat",  result)  nb_rm > 0 → ajouter automatiquement
      ("presenter", result)  géo trouvé mais 0 RM (ou échec) → montrer à l'humain
    """
    # Étape 1 : description/titre (instantané)
    geo_en_description = _contient_marqueurs_geo(dataset)

    # Étape 2 : si pas de marqueurs texte, vérifier colonnes CSV/Parquet (téléchargement léger)
    # GeoJSON et WFS sont intrinsèquement géographiques → on passe directement à l'analyse
    if not geo_en_description:
        geo_en_entetes = False
        for res in dataset.get("resources", []):
            fmt = (res.get("format") or "").lower()
            if fmt in ("geojson", "wfs"):
                geo_en_entetes = True
                break
            if "csv" in fmt:
                entetes = _telecharger_entetes(res.get("url", ""))
            elif "parquet" in fmt:
                entetes = _telecharger_schema_parquet(res.get("url", ""))
            else:
                continue
            if entetes:
                noms_norm = {normaliser(e) for e in entetes}
                if (noms_norm & MARQUEURS_ENTETES) or any(
                    s in nom for nom in noms_norm for s in MARQUEURS_ENTETES_SUBSTR
                ):
                    geo_en_entetes = True
                    break
        if not geo_en_entetes:
            return ("skip", None)

    # Étape 3 : analyse complète — essaie toutes les ressources CSV
    result = analyser_dataset(dataset, verbose=False)
    if result is not None:
        return ("candidat", result) if result["nb_rm"] > 0 else ("presenter", result)
    return ("presenter", None)


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------

def rechercher_datasets(keyword: str, nb_pages: int = NB_PAGES) -> tuple[list, int]:
    """Cherche par mot-clé (mode classique). Retourne (datasets, total)."""
    return _paginer({"q": keyword}, nb_pages)


def _paginer(params_base: dict, nb_pages: int = NB_PAGES) -> tuple[list, int]:
    """Récupère toutes les pages d'une requête API. Retourne (datasets, total)."""
    url = "https://www.data.gouv.fr/api/1/datasets/"
    tous = []
    total = 0
    for page in range(1, nb_pages + 1):
        params = {**params_base, "page_size": PAGE_SIZE, "page": page}
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 404:
                break  # page inexistante = fin de pagination
            response.raise_for_status()
            data = response.json()
            resultats = data.get("data", [])
            total = data.get("total", 0)
            tous.extend(resultats)
            if len(resultats) < PAGE_SIZE:
                break
        except Exception as e:
            print(f"  (Erreur page {page} : {e})")
            break
    return tous, total


def est_org_exclue(dataset: dict) -> bool:
    org = dataset.get("organization") or {}
    slug = org.get("slug", "")
    return any(exclu in slug for exclu in ORGS_EXCLUES)


def _mot_present(nom: str, mot: str) -> bool:
    """Vérifie si `mot` est présent comme mot entier dans `nom`.
    Évite les faux positifs : 'departementale' ne matche pas 'departement',
    'interdepartemental' ne matche pas 'departemental'."""
    return bool(re.search(r"(^| )" + re.escape(mot) + r"($| )", nom))


def est_org_hors_rm(dataset: dict) -> bool:
    """
    Retourne True si l'organisation est clairement hors RM :
    - Département autre que le 35 / Ille-et-Vilaine
    - Région autre que Bretagne
    - Intercommunalité (CA, CC, CU, métropole) autre que RM
    - Commune hors RM
    """
    org = dataset.get("organization") or {}
    nom = normaliser(org.get("name") or "")
    slug = (org.get("slug") or "").lower()

    # Département : mot "departement" ou "conseil departemental" n'importe où dans le nom
    # ex: "Département du Finistère", "Seine-Saint-Denis - Le Département"
    if (_mot_present(nom, "departement")
            or _mot_present(nom, "conseil departemental")
            or slug.startswith("departement-")
            or slug.startswith("conseil-departemental-")):
        return "35" not in slug and "ille" not in slug

    # Région : mot "region" ou "conseil regional" n'importe où dans le nom
    if (_mot_present(nom, "region")
            or _mot_present(nom, "conseil regional")
            or slug.startswith("region-")
            or slug.startswith("conseil-regional-")):
        return "bretagne" not in slug and "bretagne" not in nom

    # Intercommunalités hors RM (CA, CC, CU, métropoles, agglo)
    if ("agglomeration" in slug
            or "communaute-de-communes" in slug
            or "communaute-urbaine" in slug
            or "metropole" in slug
            or "agglomeration" in nom
            or "communaute de communes" in nom
            or "metropole" in nom):
        return "rennes" not in slug and "rennes" not in nom

    # Communes hors RM (avec variantes d'élision : "de " et "d'")
    for prefix in ("ville de ", "ville d'",
                   "commune de ", "commune d'",
                   "mairie de ", "mairie d'",
                   "municipalite de ", "municipalite d'"):
        if nom.startswith(prefix):
            nom_commune = nom[len(prefix):]
            return not est_commune_rm(nom_commune)

    # Organisation dont le nom contient un territoire hors RM
    # ex: "Occitanie Pyrénées en Intelligence Géomatique", "SIG Normandie"
    if any(region in nom for region in TITRES_HORS_RM):
        return True

    return False


def titre_hors_rm(dataset: dict) -> bool:
    """
    Retourne True si le titre indique clairement un territoire hors RM,
    pour filtrer les datasets sans zones spatiales mais géographiquement ciblés ailleurs.
    """
    titre = (dataset.get("title", "") or "").lower().strip()
    titre_norm = normaliser(titre)
    # "commune de X" ou "commune d'X" n'importe où dans le titre →
    # filtrer si X n'est pas une des 43 communes de RM
    for pref in ("commune de ", "commune d'",
                 "mairie de ", "mairie d'",
                 "ville de ", "ville d'"):
        idx = titre_norm.find(pref)
        if idx >= 0:
            reste = titre_norm[idx + len(pref):]
            if not any(reste.startswith(c) for c in _COMMUNES_NORM_RM):
                return True
            break  # commune RM trouvée → ne pas filtrer via ce critère
    return any(region in titre for region in TITRES_HORS_RM)


# Termes dans la description signalant des données au niveau commune
_MOTS_DESC_COMMUNE = [
    "par commune", "par code postal", "par code insee",
    "données communales", "niveau communal",
    "chaque commune", "toutes les communes",
    "code_commune", "code_postal",
]


def description_suggerant_commune(dataset: dict) -> bool:
    """Retourne True si la description mentionne des données au niveau commune."""
    desc = (dataset.get("description", "") or "").lower()
    return any(mot in desc for mot in _MOTS_DESC_COMMUNE)


def est_exclu_par_terme(dataset: dict, termes: list[str]) -> bool:
    """Retourne True si un terme d'exclusion personnalisé apparaît dans le titre ou l'org."""
    if not termes:
        return False
    titre = normaliser(dataset.get("title", "") or "")
    org = normaliser((dataset.get("organization") or {}).get("name", ""))
    return any(normaliser(t) in titre or normaliser(t) in org for t in termes)


EPCI_SIREN_RM = "243500139"  # SIREN de Rennes Métropole

ZONES_INCLUANT_RM = {
    "fr:region:53",       # Bretagne
    "fr:departement:35",  # Ille-et-Vilaine
    "fr:epci:243500139",  # Rennes Métropole (SIREN)
}


def couvre_rennes(dataset: dict) -> bool:
    """
    Retourne True si le périmètre géographique du dataset inclut Rennes Métropole.
    - Pas de zones → on garde (portée nationale non précisée)
    - country:* ou country-subset:* → on garde (France entière)
    - Bretagne, Ille-et-Vilaine, Rennes Métropole, communes RM → on garde
    - Toute autre zone locale explicite → on exclut
    """
    spatial = dataset.get("spatial") or {}
    zones = spatial.get("zones", [])

    if not zones:
        return True

    for zone in zones:
        if zone.startswith(("country:", "country-subset:")):
            return True
        if zone in ZONES_INCLUANT_RM:
            return True
        # Communes dont le code INSEE commence par 35 (Ille-et-Vilaine)
        if zone.startswith("fr:commune:35"):
            return True

    return False


_FORMATS_EXCLUS_FMT = ("pdf", "shapefile", "wms", "ogc", "kml", "gpkg")
_FORMATS_EXCLUS_EXT = (".pdf", ".shp", ".kml", ".gpkg", ".html", ".htm", ".doc", ".docx")


def _format_analysable(res: dict) -> str | None:
    """Retourne 'csv', 'xlsx', 'zip', 'gz', 'geojson', 'wfs' ou None selon la ressource."""
    fmt = (res.get("format") or "").lower().strip()
    url = (res.get("url") or "").lower().split("?")[0]
    if any(token in fmt for token in _FORMATS_EXCLUS_FMT):
        return None
    if any(url.endswith(ext) for ext in _FORMATS_EXCLUS_EXT):
        return None
    if fmt == "wfs" or re.search(r"[/.]wfs(/|$)", url):
        return "wfs"
    if url.endswith(".csv.gz") or url.endswith(".tsv.gz") or fmt == "gz":
        return "gz"
    if "csv" in fmt or url.endswith(".csv"):
        return "csv"
    if fmt in ("xlsx", "excel") or url.endswith(".xlsx"):
        return "xlsx"
    if "zip" in fmt or url.endswith(".zip"):
        return "zip"
    if fmt == "geojson" or url.endswith(".geojson"):
        return "geojson"
    if "parquet" in fmt or url.endswith(".parquet"):
        return "parquet"
    return None


def trouver_ressource_analysable(dataset: dict) -> dict | None:
    """Retourne la première ressource analysable (CSV, ZIP, GZ, XLSX) ou JSON."""
    for r in dataset.get("resources", []):
        if _format_analysable(r):
            return r
    # Fallback JSON (extrait uniquement, pas d'analyse RM)
    for r in dataset.get("resources", []):
        if (r.get("format") or "").lower() == "json":
            return r
    return None


def formats_disponibles(dataset: dict) -> list:
    """Retourne les formats uniques des ressources d'un dataset (pour stats)."""
    fmts = set()
    for r in dataset.get("resources", []):
        fmt = (r.get("format") or "").upper()
        if fmt:
            fmts.add(fmt)
    return sorted(fmts)


# ---------------------------------------------------------------------------
# Extrait des données
# ---------------------------------------------------------------------------

_MAGIC_BINAIRE = [
    b"PK\x03\x04",   # ZIP / XLSX / ODS / DOCX
    b"\x1f\x8b",     # gzip
    b"%PDF-",         # PDF
    b"\xd0\xcf\x11\xe0",  # OLE2 (XLS, DOC ancien format)
]


def telecharger_extrait_csv(url: str, n_lignes: int = 5) -> str:
    """Télécharge les premières lignes d'un CSV (sans télécharger tout le fichier)."""
    try:
        response = requests.get(url, stream=True, timeout=15)
        response.raise_for_status()
        # Collecte assez d'octets pour n_lignes+1 lignes (max 64 Ko)
        contenu = b""
        for chunk in response.iter_content(chunk_size=4096):
            contenu += chunk
            if contenu.count(b"\n") >= n_lignes + 1 or len(contenu) > 65536:
                break
        response.close()
        for magic in _MAGIC_BINAIRE:
            if contenu.startswith(magic):
                return "__BINAIRE__"
        # Décodage avec fallback latin-1 (même logique que l'analyse complète)
        texte = contenu.decode("utf-8", errors="replace")
        if texte.count("�") >= 1:
            texte = contenu.decode("latin-1")
        lignes = [l for l in texte.split("\n") if l.strip()][:n_lignes + 1]
        if lignes:
            premiere_norm = normaliser(lignes[0].split(",")[0].split(";")[0])
            if premiere_norm in ("colonne", "column", "champ", "field", "variable"):
                return "__DICTIONNAIRE__"
        return "\n".join(lignes)
    except Exception as e:
        return f"(Impossible de télécharger l'extrait : {e})"


def telecharger_extrait_json(url: str, n_lignes: int = 5) -> str:
    """Télécharge un petit bout d'un JSON pour en montrer la structure."""
    try:
        response = requests.get(url, stream=True, timeout=15)
        response.raise_for_status()
        # On lit seulement les premiers Ko
        contenu = b""
        for chunk in response.iter_content(chunk_size=4096):
            contenu += chunk
            if len(contenu) > 8192:
                break
        response.close()
        texte = contenu.decode("utf-8", errors="replace")
        # On essaie de trouver les premiers enregistrements
        debut = texte[:2000]
        return debut + "\n[... tronqué ...]"
    except Exception as e:
        return f"(Impossible de télécharger l'extrait : {e})"


def _obtenir_extrait_geojson(url: str) -> str:
    """Retourne les premières features d'un GeoJSON pour affichage dans la fiche."""
    try:
        resp = requests.get(url, timeout=10, stream=True)
        resp.raise_for_status()
        contenu = b""
        for chunk in resp.iter_content(chunk_size=65536):
            contenu += chunk
            if len(contenu) > 512 * 1024:
                resp.close()
                break
        data = json.loads(contenu.decode("utf-8", errors="replace"))
        features = data.get("features", [])[:3]
        lignes = []
        for i, f in enumerate(features):
            props = f.get("properties") or {}
            geom_type = (f.get("geometry") or {}).get("type", "?")
            props_court = dict(list(props.items())[:5])
            lignes.append(f"[{i+1}] {geom_type} | {props_court}")
        return "\n".join(lignes) if lignes else "(aucune feature)"
    except Exception as e:
        return f"(erreur extrait GeoJSON : {e})"


def obtenir_extrait(ressource: dict) -> str:
    fmt = _format_analysable(ressource) or (ressource.get("format") or "").lower()
    url = ressource.get("url", "")
    if fmt == "csv":
        return telecharger_extrait_csv(url)
    elif fmt == "json":
        return telecharger_extrait_json(url)
    elif fmt == "geojson":
        return _obtenir_extrait_geojson(url)
    elif fmt == "wfs":
        return "(service WFS — voir résultat d'analyse)"
    elif fmt == "zip":
        return "(archive ZIP — voir résultat d'analyse)"
    elif fmt in ("xlsx", "excel"):
        return "(fichier Excel — voir résultat d'analyse)"
    elif fmt == "gz":
        return "(fichier compressé GZ — voir résultat d'analyse)"
    return "(format non supporté pour l'extrait)"


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------

SEP = "─" * 72

def afficher_fiche(dataset: dict, extrait: str, resultat: dict | None = None) -> None:
    org = (dataset.get("organization") or {}).get("name", "?")
    description = dataset.get("description", "") or ""
    description_courte = textwrap.fill(description[:300].replace("\n", " "), width=70,
                                       break_long_words=True, break_on_hyphens=True)
    formats = list(set(
        r.get("format", "").upper()
        for r in dataset.get("resources", [])
        if r.get("format")
    ))

    print(f"\n{SEP}")
    print(f"TITRE    : {dataset['title']}")
    print(f"ORG      : {org}")
    print(f"LICENCE  : {dataset.get('license', '?')}")
    print(f"FORMATS  : {', '.join(formats)}")
    print(f"MAJ      : {dataset.get('last_modified', '?')[:10]}")
    url = dataset.get("_url") or f"https://www.data.gouv.fr/datasets/{dataset['id']}"
    print(f"URL      : {url}")
    if resultat is not None:
        if resultat.get("champ_iris"):
            methode, champ = "IRIS", resultat["champ_iris"]
        elif resultat.get("champ_adresse"):
            methode, champ = "adresse", resultat["champ_adresse"]
        elif resultat.get("champ_siren"):
            methode, champ = "SIREN", resultat["champ_siren"]
        elif resultat.get("champ_lat"):
            methode = "géo"
            if resultat.get("champ_lon"):
                champ = f"{resultat['champ_lat']} + {resultat['champ_lon']}"
            else:
                champ = resultat["champ_lat"]
        elif resultat.get("champ_cp") or resultat.get("champ_ville"):
            methode = "CP/ville"
            champ = " + ".join(filter(None, [resultat.get("champ_cp"), resultat.get("champ_ville")]))
        else:
            methode, champ = "?", "?"
        print(f"ANALYSE  : {resultat['nb_total']} lignes | {resultat['nb_rm']} RM | {methode}: {champ}")
    print(f"\nDESCRIPTION :\n{description_courte}")
    lignes_extrait = extrait.splitlines()[:6]
    lignes_extrait = [l[:120] + ("…" if len(l) > 120 else "") for l in lignes_extrait]
    print(f"\nEXTRAIT (5 premières lignes) :\n" + "\n".join(lignes_extrait))
    print(SEP)


# ---------------------------------------------------------------------------
# Analyse approfondie
# ---------------------------------------------------------------------------

def deviner_champs(entetes: list[str]) -> tuple[str | None, str | None]:
    """Devine les champs code postal et ville dans les en-têtes d'un CSV."""
    entetes_norm = [e.lower().strip() for e in entetes]
    champ_cp = next((e for e in entetes_norm if e in CHAMPS_CP), None)
    champ_ville = next((e for e in entetes_norm if e in CHAMPS_VILLE), None)
    # Si pas trouvé exactement, cherche si un en-tête contient le mot
    if not champ_cp:
        champ_cp = next((e for e in entetes_norm if "postal" in e or e == "cp"), None)
    if not champ_ville:
        # Exclut les champs de code/insee/dep qui contiennent "commune" sans être des noms
        champ_ville = next(
            (e for e in entetes_norm
             if ("commune" in e or "ville" in e or "libelle" in e or "libgeo" in e)
             and "insee" not in e and "dep" not in e
             and not e.startswith("code") and "partenaire" not in e),
            None,
        )
    # Remappe sur le nom original
    if champ_cp:
        champ_cp = entetes[entetes_norm.index(champ_cp)]
    if champ_ville:
        champ_ville = entetes[entetes_norm.index(champ_ville)]
    return champ_cp, champ_ville


def deviner_champ_iris(entetes: list[str]) -> str | None:
    """Détecte une colonne contenant un code IRIS complet (9 chiffres) ou un code
    INSEE commune (5 chiffres) — les deux permettent de tester l'appartenance à RM
    via est_iris_rm() qui ne regarde que les 5 premiers chiffres.
    Utilise normaliser() pour gérer accents et séparateurs (ex: 'Numéro commune')."""
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_IRIS:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)]
    # Fallback : colonne dont le nom contient "iris" mais pas "libelle"
    for i, e in enumerate(entetes_norm):
        if "iris" in e and "libelle" not in e and "lib" not in e:
            return entetes[i]
    # Fallback : "code insee" + suffixe (ex: "code insee 2024") sans mention région/dept
    _SUFFIXES_GEO_EXCLUS = ("reg", "region", "dep", "departement", "arr", "arrondissement")
    for i, e in enumerate(entetes_norm):
        if "insee" in e and not any(s in e for s in _SUFFIXES_GEO_EXCLUS):
            return entetes[i]
    return None


def est_iris_rm(code: str) -> bool:
    """Retourne True si un code IRIS, INSEE commune ou EPCI appartient à RM."""
    code = str(code).strip()
    if code == EPCI_SIREN_RM:
        return True
    return len(code) >= 5 and code[:5] in CODES_INSEE_RM


def deviner_champ_dep(entetes: list[str]) -> str | None:
    """Détecte une colonne contenant un code département (2 chiffres)."""
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_DEP:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)]
    return None


def deviner_champ_adresse(entetes: list[str]) -> str | None:
    """Détecte une colonne contenant une adresse textuelle complète."""
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_ADRESSE:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)]
    # Fallback : colonne dont le nom contient "adresse"
    for i, e in enumerate(entetes_norm):
        if "adresse" in e:
            return entetes[i]
    return None


def deviner_champ_siren(entetes: list[str]) -> str | None:
    """Détecte une colonne contenant des codes SIREN (9 chiffres) ou SIRET (14 chiffres)."""
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_SIREN:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)]
    # Fallback : colonne dont le nom commence par "siren" ou "siret"
    for i, e in enumerate(entetes_norm):
        if e.startswith(("siren", "siret")):
            return entetes[i]
    return None


def deviner_champs_geo(entetes: list[str]) -> tuple[str | None, str | None]:
    """Détecte des colonnes de coordonnées géographiques WGS84.
    Retourne (champ_lat, champ_lon) où :
      - champ_lon is None → champ_lat est une colonne "lat,lon" combinée
      - les deux non-None  → colonnes latitude et longitude séparées
      - les deux None      → aucune colonne géo trouvée
    """
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_GEO_POINT:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)], None
    champ_lat, champ_lon = None, None
    for nom in CHAMPS_LAT:
        if nom in entetes_norm:
            champ_lat = entetes[entetes_norm.index(nom)]
            break
    if champ_lat is None:
        for i, e in enumerate(entetes_norm):
            if e in ("lat", "latitude"):
                champ_lat = entetes[i]
                break
    for nom in CHAMPS_LON:
        if nom in entetes_norm:
            champ_lon = entetes[entetes_norm.index(nom)]
            break
    if champ_lon is None:
        for i, e in enumerate(entetes_norm):
            if e in ("lon", "lng", "longitude"):
                champ_lon = entetes[i]
                break
    if champ_lat and champ_lon:
        return champ_lat, champ_lon
    return None, None


def est_point_rm(lat_val: str, lon_val: str | None) -> bool:
    """Retourne True si le point WGS84 est dans la bounding box de RM.
    Si lon_val is None, lat_val est au format "lat,lon" (OpenDataSoft).
    """
    try:
        if lon_val is None:
            parties = str(lat_val).replace(";", ",").split(",")
            if len(parties) < 2:
                return False
            lat, lon = float(parties[0]), float(parties[1])
        else:
            lat, lon = float(lat_val), float(lon_val)
    except (ValueError, TypeError):
        return False
    return _RM_LAT_MIN <= lat <= _RM_LAT_MAX and _RM_LON_MIN <= lon <= _RM_LON_MAX


def est_adresse_rm(texte: str) -> bool:
    """Retourne True si une adresse textuelle est dans Rennes Métropole.
    Cherche d'abord un code postal 35xxx, puis un nom de commune RM."""
    if not texte:
        return False
    for cp in _RE_CP_35.findall(texte):
        if cp in CODES_POSTAUX_RM:
            return True
    texte_norm = normaliser(texte)
    return any(commune in texte_norm for commune in _COMMUNES_NORM_RM)


def log_analyse(entry: dict) -> None:
    """Ajoute une entrée JSON à discover.log (une ligne par analyse)."""
    entry["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _chemin_cache(url: str) -> str:
    cle = hashlib.md5(url.encode()).hexdigest()
    return os.path.join(CACHE_DIR, cle)


def _purger_cache(jours: int = 30) -> None:
    """Supprime les entrées de cache plus vieilles que `jours` jours."""
    if not os.path.isdir(CACHE_DIR):
        return
    limite = datetime.datetime.now().timestamp() - jours * 86400
    supprimes = 0
    for nom in os.listdir(CACHE_DIR):
        chemin = os.path.join(CACHE_DIR, nom)
        if os.path.isfile(chemin) and os.path.getmtime(chemin) < limite:
            os.remove(chemin)
            supprimes += 1
    if supprimes:
        print(f"  (Cache : {supprimes} fichier(s) supprimé(s), plus vieux que {jours} jours)\n")


def _telecharger(url: str, verbose: bool) -> tuple:
    """
    Retourne (contenu_bytes, taille_mo, depuis_cache, erreur).
    Utilise le cache si le fichier a déjà été téléchargé.
    """
    chemin = _chemin_cache(url)
    if os.path.exists(chemin):
        with open(chemin, "rb") as f:
            contenu = f.read()
        taille = len(contenu) / 1024 / 1024
        if verbose:
            print(f"  Cache ({taille:.1f} Mo).")
        return contenu, taille, True, None

    if verbose:
        print("  Téléchargement en cours...")
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
    except Exception as e:
        return None, 0, False, f"téléchargement : {e}"

    MAX_MO = 50  # plafond : inutile de télécharger plus pour détecter des données RM
    contenu = b""
    total = 0
    interrompu = None
    try:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            contenu += chunk
            total += len(chunk)
            if verbose:
                print(f"  {total / 1024 / 1024:.1f} Mo...", end="\r")
            if total >= MAX_MO * 1024 * 1024:
                response.close()
                if verbose:
                    print(f"\n  (Plafond {MAX_MO} Mo atteint — données partielles utilisées)")
                break
    except Exception as e:
        interrompu = str(e)
    if verbose:
        print()

    if interrompu:
        # Si on a quand même récupéré des données, on les utilise (transfert partiel)
        if total >= 1024 * 1024:  # au moins 1 Mo
            if verbose:
                print(f"  (Transfert interrompu après {total/1024/1024:.1f} Mo — données partielles utilisées)")
        else:
            return None, 0, False, f"téléchargement interrompu : {interrompu}"

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(chemin, "wb") as f:
        f.write(contenu)

    return contenu, total / 1024 / 1024, False, None


def _detecter_champs(entetes: list[str]) -> tuple:
    """Retourne (champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse, champ_siren)."""
    champ_cp, champ_ville = deviner_champs(list(entetes))
    champ_iris = deviner_champ_iris(list(entetes))
    champ_dep = deviner_champ_dep(list(entetes)) if champ_iris else None
    champ_adresse = (
        None if (champ_cp or champ_ville or champ_iris)
        else deviner_champ_adresse(list(entetes))
    )
    # SIREN en dernier recours : seulement si aucun champ géographique local n'est trouvé
    champ_siren = (
        None if (champ_cp or champ_ville or champ_iris or champ_adresse)
        else deviner_champ_siren(list(entetes))
    )
    # Géolocalisation : fallback après tout le reste
    champ_lat, champ_lon = (None, None) if (champ_cp or champ_ville or champ_iris
                                             or champ_adresse or champ_siren) \
        else deviner_champs_geo(list(entetes))
    return champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse, champ_siren, champ_lat, champ_lon


def _compter_lignes_rm(rows, champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse,
                        champ_siren=None, champ_lat=None, champ_lon=None) -> tuple:
    """Itère rows (dicts) et compte ceux appartenant à Rennes Métropole."""
    sirens_rm = obtenir_sirens_rm() if champ_siren else None
    nb_total, nb_rm = 0, 0
    exemples, premieres_lignes = [], []
    for row in rows:
        try:
            nb_total += 1
            if len(premieres_lignes) < 5:
                premieres_lignes.append(dict(row))
            if champ_iris:
                code = str(row.get(champ_iris, "")).strip()
                if len(code) < 5 and champ_dep:
                    dept_raw = str(row.get(champ_dep, "")).strip()
                    dept = (dept_raw.lstrip("0") or "0").zfill(2)
                    code = dept + code.zfill(3)
                in_rm = est_iris_rm(code)
            elif champ_adresse:
                in_rm = est_adresse_rm(str(row.get(champ_adresse, "")))
            elif champ_siren:
                val = str(row.get(champ_siren, "")).strip().replace(" ", "")
                in_rm = val.isdigit() and len(val) in (9, 14) and val[:9] in sirens_rm
            elif champ_lat:
                lat_val = str(row.get(champ_lat, "")).strip()
                lon_val = str(row.get(champ_lon, "")).strip() if champ_lon else None
                in_rm = est_point_rm(lat_val, lon_val)
            else:
                cp = str(row.get(champ_cp, "")).strip() if champ_cp else ""
                ville = str(row.get(champ_ville, "")).strip() if champ_ville else ""
                if champ_cp and champ_ville:
                    in_rm = est_dans_rm(ville, cp)
                elif champ_ville:
                    in_rm = est_commune_rm(ville)
                elif champ_cp:
                    in_rm = cp in CODES_POSTAUX_RM
                else:
                    in_rm = False
            if in_rm:
                nb_rm += 1
                if len(exemples) < 3:
                    exemples.append(dict(row))
        except csv.Error:
            break  # ligne tronquée (téléchargement partiel) — on garde ce qu'on a
    return nb_total, nb_rm, exemples, premieres_lignes


def _construire_resultat(champ_cp, champ_ville, champ_iris, champ_adresse,
                         nb_total, nb_rm, exemples, premieres_lignes,
                         champ_siren=None, champ_lat=None, champ_lon=None) -> dict:
    return {
        "nb_total": nb_total, "nb_rm": nb_rm,
        "champ_cp": champ_cp, "champ_ville": champ_ville,
        "champ_iris": champ_iris, "champ_adresse": champ_adresse,
        "champ_siren": champ_siren,
        "champ_lat": champ_lat, "champ_lon": champ_lon,
        "exemples": exemples, "premieres_lignes": premieres_lignes,
    }


def _analyser_contenu_csv(contenu: bytes, verbose: bool, dataset_id: str, titre: str,
                           url: str = "", taille_mo: float = 0,
                           depuis_cache: bool = False) -> dict | None:
    """Parse des bytes CSV et cherche des données Rennes Métropole."""
    log = {"url": url, "dataset_id": dataset_id, "titre": titre,
           "taille_mo": round(taille_mo, 2), "cache": depuis_cache}

    if contenu[:5] in (b"%PDF-", b"PK\x03\x04", b"\x1f\x8b\x08"):
        log["erreur"] = "fichier binaire"
        log_analyse(log)
        if verbose:
            print("  (Fichier binaire détecté, non supporté)")
        return None

    debut = contenu[:100].lstrip().lower()
    if debut.startswith((b"<!doctype", b"<html")):
        log["erreur"] = "réponse HTML"
        log_analyse(log)
        if verbose:
            print("  (Réponse HTML reçue — redirection ou authentification)")
        return None

    texte = contenu.decode("utf-8-sig", errors="replace")
    if texte.count("�") > 10:
        texte = contenu.decode("latin-1")
    sample = texte[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiteur = dialect.delimiter
    except csv.Error:
        delimiteur = ","

    premiere_ligne = texte.split("\n")[0]
    premiere_norm = normaliser(premiere_ligne.split(",")[0].split(";")[0])
    if premiere_norm in ("colonne", "column", "champ", "field", "variable"):
        return None

    nb_cols = len(premiere_ligne.split(delimiteur))
    if nb_cols <= 2:
        for sep in (";", "\t", "|", ","):
            n = len(premiere_ligne.split(sep))
            if n > nb_cols:
                nb_cols, delimiteur = n, sep
    log["delimiteur"] = delimiteur

    reader = csv.DictReader(io.StringIO(texte), delimiter=delimiteur)
    entetes = list(reader.fieldnames or [])
    log["entetes"] = entetes[:15]

    champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse, champ_siren, champ_lat, champ_lon = _detecter_champs(entetes)
    log.update({"champ_cp": champ_cp, "champ_ville": champ_ville,
                "champ_iris": champ_iris, "champ_adresse": champ_adresse,
                "champ_siren": champ_siren, "champ_lat": champ_lat})

    if verbose:
        print(f"  En-têtes détectés : {entetes[:10]}")
        if champ_iris:
            print(f"  Champ IRIS trouvé : {champ_iris}")
        elif champ_adresse:
            print(f"  Champ adresse trouvé : {champ_adresse}")
        elif champ_siren:
            print(f"  Champ SIREN trouvé : {champ_siren}")
        elif champ_lat:
            desc = champ_lat if champ_lon is None else f"{champ_lat} + {champ_lon}"
            print(f"  Champ géo trouvé : {desc}")
        else:
            print(f"  Champ CP trouvé : {champ_cp} | Champ ville trouvé : {champ_ville}")

    try:
        nb_total, nb_rm, exemples, premieres_lignes = _compter_lignes_rm(
            reader, champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse,
            champ_siren, champ_lat, champ_lon
        )
    except csv.Error as e:
        log["erreur"] = f"parsing CSV : {e}"
        log_analyse(log)
        if verbose:
            print(f"  (Erreur de parsing CSV : {e})")
        return None

    log.update({"nb_total": nb_total, "nb_rm": nb_rm})
    log_analyse(log)
    return _construire_resultat(champ_cp, champ_ville, champ_iris, champ_adresse,
                                nb_total, nb_rm, exemples, premieres_lignes,
                                champ_siren=champ_siren, champ_lat=champ_lat, champ_lon=champ_lon)


def analyser_csv(url: str, verbose: bool = True,
                 dataset_id: str = "", titre: str = "") -> dict | None:
    """
    Télécharge (ou récupère du cache) un CSV et cherche des données Rennes Métropole.
    verbose=False supprime les prints (pour l'exécution en arrière-plan).
    Retourne None si le téléchargement ou le parsing échoue (le JDD sera reproposé).
    """
    contenu, taille_mo, depuis_cache, erreur = _telecharger(url, verbose)
    if erreur:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre, "erreur": erreur})
        if verbose:
            print(f"  (Échec : {erreur})")
        return None
    return _analyser_contenu_csv(contenu, verbose, dataset_id, titre, url, taille_mo, depuis_cache)


def analyser_zip(url: str, verbose: bool = False,
                 dataset_id: str = "", titre: str = "") -> dict | None:
    """Télécharge une archive ZIP et analyse les fichiers CSV ou GeoJSON qu'elle contient."""
    contenu, taille_mo, depuis_cache, erreur = _telecharger(url, verbose)
    if erreur:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre, "erreur": erreur})
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(contenu)) as zf:
            noms = [n for n in zf.namelist() if not n.startswith("__MACOSX")]
            membres_csv = [n for n in noms if n.lower().endswith(".csv")]
            membres_geo = [n for n in noms if n.lower().endswith(".geojson")]
            if not membres_csv and not membres_geo:
                if verbose:
                    print("  (ZIP : aucun fichier CSV ou GeoJSON trouvé)")
                return None
            meilleur = None
            for membre in membres_csv:
                if verbose:
                    print(f"  ZIP → {membre}")
                with zf.open(membre) as f:
                    contenu_membre = f.read()
                result = _analyser_contenu_csv(
                    contenu_membre, verbose, dataset_id, f"{titre} [{membre}]",
                    url=f"{url}#{membre}", taille_mo=len(contenu_membre) / 1024 / 1024
                )
                if result is None:
                    continue
                if meilleur is None or result["nb_rm"] > meilleur["nb_rm"]:
                    meilleur = result
                if meilleur and meilleur["nb_rm"] > 0:
                    break
            for membre in membres_geo:
                if meilleur and meilleur["nb_rm"] > 0:
                    break
                if verbose:
                    print(f"  ZIP → {membre}")
                with zf.open(membre) as f:
                    contenu_membre = f.read()
                result = _analyser_contenu_geojson(
                    contenu_membre, verbose, dataset_id, f"{titre} [{membre}]",
                    url=f"{url}#{membre}", taille_mo=len(contenu_membre) / 1024 / 1024
                )
                if result is None:
                    continue
                if meilleur is None or result["nb_rm"] > meilleur["nb_rm"]:
                    meilleur = result
            return meilleur
    except zipfile.BadZipFile:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                     "erreur": "archive ZIP invalide"})
        return None


def analyser_gz(url: str, verbose: bool = False,
                dataset_id: str = "", titre: str = "") -> dict | None:
    """Télécharge un fichier GZ et analyse le CSV décompressé."""
    contenu, taille_mo, depuis_cache, erreur = _telecharger(url, verbose)
    if erreur:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre, "erreur": erreur})
        return None
    try:
        contenu_csv = gzip.decompress(contenu)
    except Exception as e:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                     "erreur": f"décompression GZ : {e}"})
        if verbose:
            print(f"  (Erreur décompression GZ : {e})")
        return None
    return _analyser_contenu_csv(
        contenu_csv, verbose, dataset_id, titre,
        url=url, taille_mo=len(contenu_csv) / 1024 / 1024
    )


def analyser_xlsx(url: str, verbose: bool = False,
                  dataset_id: str = "", titre: str = "") -> dict | None:
    """Télécharge un fichier Excel XLSX et cherche des données Rennes Métropole."""
    try:
        import openpyxl
    except ImportError:
        if verbose:
            print("  (openpyxl non installé — pip install openpyxl)")
        return None

    contenu, taille_mo, depuis_cache, erreur = _telecharger(url, verbose)
    if erreur:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre, "erreur": erreur})
        return None

    log = {"url": url, "dataset_id": dataset_id, "titre": titre,
           "taille_mo": round(taille_mo, 2), "cache": depuis_cache}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
        ws = wb.active
        lignes = list(ws.iter_rows(values_only=True))
        wb.close()
    except Exception as e:
        log["erreur"] = f"lecture XLSX : {e}"
        log_analyse(log)
        if verbose:
            print(f"  (Erreur lecture XLSX : {e})")
        return None

    if not lignes:
        return None

    entetes = [str(c or "").strip() for c in lignes[0]]
    log["entetes"] = entetes[:15]

    champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse, champ_siren, champ_lat, champ_lon = _detecter_champs(entetes)
    log.update({"champ_cp": champ_cp, "champ_ville": champ_ville,
                "champ_iris": champ_iris, "champ_adresse": champ_adresse,
                "champ_siren": champ_siren, "champ_lat": champ_lat})

    if verbose:
        print(f"  En-têtes XLSX : {entetes[:10]}")
        if champ_iris:
            print(f"  Champ IRIS trouvé : {champ_iris}")
        elif champ_adresse:
            print(f"  Champ adresse trouvé : {champ_adresse}")
        elif champ_siren:
            print(f"  Champ SIREN trouvé : {champ_siren}")
        elif champ_lat:
            print(f"  Champ géo : {champ_lat}" + (f" + {champ_lon}" if champ_lon else ""))
        else:
            print(f"  Champ CP : {champ_cp} | Champ ville : {champ_ville}")

    def _lignes_en_dicts():
        for row in lignes[1:]:
            yield dict(zip(entetes, (str(v or "").strip() for v in row)))

    nb_total, nb_rm, exemples, premieres_lignes = _compter_lignes_rm(
        _lignes_en_dicts(), champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse,
        champ_siren, champ_lat, champ_lon
    )
    log.update({"nb_total": nb_total, "nb_rm": nb_rm})
    log_analyse(log)
    return _construire_resultat(champ_cp, champ_ville, champ_iris, champ_adresse,
                                nb_total, nb_rm, exemples, premieres_lignes,
                                champ_siren=champ_siren, champ_lat=champ_lat, champ_lon=champ_lon)


def analyser_parquet(url: str, verbose: bool = False,
                     dataset_id: str = "", titre: str = "") -> dict | None:
    """
    Analyse un fichier Parquet distant sans le télécharger intégralement.
    Utilise pyarrow + fsspec : lit le footer (schéma) puis les colonnes utiles
    avec filter pushdown sur COMMUNE ∈ CODES_INSEE_RM (range requests HTTP).
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        import pyarrow.compute as pc
        import fsspec
    except ImportError:
        if verbose:
            print("  (pyarrow/fsspec non installé — pip install pyarrow fsspec)")
        return None

    log: dict = {"url": url, "dataset_id": dataset_id, "titre": titre}

    try:
        fs, fpath = fsspec.url_to_fs(url)

        # 1. Schéma et métadonnées (range request footer, ~140 Ko)
        with fs.open(fpath, "rb") as f:
            pf = pq.ParquetFile(f)
            cols = [field.name for field in pf.schema_arrow]
            nb_total = pf.metadata.num_rows

        log["entetes"] = cols[:15]

        # 2. Détection des champs géographiques
        champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse, champ_siren, champ_lat, champ_lon = _detecter_champs(cols)

        if verbose:
            print(f"  Colonnes Parquet ({len(cols)}) : {cols[:10]}")
            if champ_iris:
                print(f"  Champ IRIS : {champ_iris}")
            elif champ_ville:
                print(f"  Champ commune : {champ_ville}")

        if not any([champ_iris, champ_cp, champ_ville, champ_adresse, champ_siren, champ_lat]):
            log["erreur"] = "aucun champ géo détecté"
            log_analyse(log)
            return None

        # 3. Lecture filtrée : filter pushdown sur COMMUNE si disponible
        # pyarrow exploite les statistiques de row groups pour ne lire que les blocs pertinents
        cols_a_lire = [c for c in [champ_iris, champ_cp, champ_ville] if c][:3]
        filtres = None
        if "COMMUNE" in cols and champ_iris:
            # COMMUNE ∈ RM → ne télécharge que les row groups concernés (~1-2 sur 25)
            filtres = [("COMMUNE", "in", list(CODES_INSEE_RM))]
            if "COMMUNE" not in cols_a_lire:
                cols_a_lire = ["COMMUNE"] + cols_a_lire

        table = pq.read_table(fpath, filesystem=fs, columns=cols_a_lire or None, filters=filtres)

        # 4. Comptage RM
        if champ_iris and champ_iris in table.schema.names:
            iris_col = table.column(champ_iris)
            commune_codes = pc.utf8_slice_codeunits(iris_col, 0, 5)
            rm_mask = pc.is_in(commune_codes, value_set=pa.array(sorted(CODES_INSEE_RM)))
            nb_rm = int(pc.sum(rm_mask.cast(pa.int64())).as_py() or 0)
        elif filtres:
            # Le filtre COMMUNE a déjà sélectionné les lignes RM
            nb_rm = len(table)
        elif champ_cp and champ_cp in table.schema.names:
            cp_col = table.column(champ_cp).cast(pa.string())
            rm_mask = pc.is_in(cp_col, value_set=pa.array(sorted(CODES_POSTAUX_RM)))
            nb_rm = int(pc.sum(rm_mask.cast(pa.int64())).as_py() or 0)
        else:
            nb_rm = 0

        # 5. Exemples et premières lignes (depuis la table déjà filtrée)
        sample_size = min(5, len(table))
        sample_rows = table.slice(0, sample_size).to_pylist()
        premieres_lignes = sample_rows
        exemples = sample_rows[:3] if nb_rm > 0 else []

        log.update({"nb_total": nb_total, "nb_rm": nb_rm, "champ_iris": champ_iris})
        log_analyse(log)

        return _construire_resultat(
            champ_cp, champ_ville, champ_iris, champ_adresse,
            nb_total, nb_rm, exemples, premieres_lignes,
            champ_siren=champ_siren, champ_lat=champ_lat, champ_lon=champ_lon,
        )

    except Exception as e:
        log["erreur"] = str(e)
        log_analyse(log)
        if verbose:
            print(f"  (Erreur Parquet : {e})")
        return None


# ---------------------------------------------------------------------------
# Analyse GeoJSON
# ---------------------------------------------------------------------------

def _coords_centroide(geometry: dict) -> tuple[float, float] | None:
    """Retourne un point représentatif de la géométrie GeoJSON (lon, lat)."""
    gtype = geometry.get("type", "")
    coords = geometry.get("coordinates")
    if not coords:
        return None
    try:
        if gtype == "Point":
            return float(coords[0]), float(coords[1])
        elif gtype in ("LineString", "MultiPoint"):
            return float(coords[0][0]), float(coords[0][1])
        elif gtype in ("Polygon", "MultiLineString"):
            return float(coords[0][0][0]), float(coords[0][0][1])
        elif gtype == "MultiPolygon":
            return float(coords[0][0][0][0]), float(coords[0][0][0][1])
    except (IndexError, TypeError, ValueError):
        return None
    return None


def _analyser_features_geojson(features: list, verbose: bool,
                                dataset_id: str, titre: str) -> dict | None:
    """Analyse une liste de features GeoJSON et cherche des données Rennes Métropole."""
    if not features:
        return None

    entetes = list((features[0].get("properties") or {}).keys())
    champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse, champ_siren, champ_lat, champ_lon = \
        _detecter_champs(entetes)

    if verbose:
        print(f"  Propriétés GeoJSON : {entetes[:10]}")
        if champ_iris:
            print(f"  Champ IRIS : {champ_iris}")
        elif champ_adresse:
            print(f"  Champ adresse : {champ_adresse}")
        elif champ_cp or champ_ville:
            print(f"  Champ CP : {champ_cp} | Champ ville : {champ_ville}")
        else:
            print("  Aucun champ géographique textuel — fallback coordonnées géométrie")

    if champ_cp or champ_ville or champ_iris or champ_adresse or champ_siren or champ_lat:
        rows = (f.get("properties") or {} for f in features)
        nb_total, nb_rm, exemples, premieres_lignes = _compter_lignes_rm(
            rows, champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse,
            champ_siren, champ_lat, champ_lon
        )
    else:
        # Fallback : vérifier les coordonnées des géométries dans la bbox RM
        nb_total, nb_rm = 0, 0
        exemples, premieres_lignes = [], []
        for f in features:
            props = f.get("properties") or {}
            nb_total += 1
            if len(premieres_lignes) < 5:
                premieres_lignes.append(props)
            pt = _coords_centroide(f.get("geometry") or {})
            if pt is not None:
                lon, lat = pt  # GeoJSON : [lon, lat]
                if _RM_LAT_MIN <= lat <= _RM_LAT_MAX and _RM_LON_MIN <= lon <= _RM_LON_MAX:
                    nb_rm += 1
                    if len(exemples) < 3:
                        exemples.append(props)
        champ_lat = "geometry"  # marqueur : détection par coordonnées

    return _construire_resultat(
        champ_cp, champ_ville, champ_iris, champ_adresse,
        nb_total, nb_rm, exemples, premieres_lignes,
        champ_siren=champ_siren, champ_lat=champ_lat, champ_lon=champ_lon,
    )


def _analyser_contenu_geojson(contenu: bytes, verbose: bool,
                               dataset_id: str, titre: str,
                               url: str = "", taille_mo: float = 0) -> dict | None:
    """Parse des bytes GeoJSON et cherche des données Rennes Métropole."""
    log = {"url": url, "dataset_id": dataset_id, "titre": titre,
           "taille_mo": round(taille_mo, 2)}
    try:
        data = json.loads(contenu.decode("utf-8", errors="replace"))
    except Exception as e:
        log["erreur"] = f"parsing JSON : {e}"
        log_analyse(log)
        if verbose:
            print(f"  (Erreur parsing GeoJSON : {e})")
        return None
    features = data.get("features", [])
    if not features:
        log["erreur"] = "aucune feature"
        log_analyse(log)
        if verbose:
            print("  (GeoJSON : aucune feature)")
        return None
    result = _analyser_features_geojson(features, verbose, dataset_id, titre)
    if result:
        log.update({"nb_total": result["nb_total"], "nb_rm": result["nb_rm"]})
    log_analyse(log)
    return result


def analyser_geojson(url: str, verbose: bool = False,
                     dataset_id: str = "", titre: str = "") -> dict | None:
    """Télécharge (ou récupère du cache) un GeoJSON et cherche des données Rennes Métropole."""
    contenu, taille_mo, depuis_cache, erreur = _telecharger(url, verbose)
    if erreur:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre, "erreur": erreur})
        if verbose:
            print(f"  (Échec : {erreur})")
        return None
    return _analyser_contenu_geojson(contenu, verbose, dataset_id, titre, url, taille_mo)


# ---------------------------------------------------------------------------
# Analyse WFS
# ---------------------------------------------------------------------------

_WFS_RM_BBOX = "-2.00,47.80,-1.30,48.35"  # minLon,minLat,maxLon,maxLat (EPSG:4326)


def _wfs_base_url(url: str) -> str:
    """Extrait l'URL de base d'un service WFS (retire les paramètres OGC)."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    ogc_keys = {"service", "request", "version", "typename", "typenames",
                "outputformat", "bbox", "maxfeatures", "count", "srsname"}
    qs_filtre = {k: v for k, v in qs.items() if k.lower() not in ogc_keys}
    query = "&".join(f"{k}={v[0]}" for k, v in qs_filtre.items())
    return urlunparse(parsed._replace(query=query))


def _wfs_get_layers(base_url: str, verbose: bool) -> list[str]:
    """Interroge GetCapabilities et retourne les noms de couches disponibles."""
    sep = "&" if "?" in base_url else "?"
    caps_url = f"{base_url}{sep}SERVICE=WFS&REQUEST=GetCapabilities"
    try:
        resp = requests.get(caps_url, timeout=20)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        if verbose:
            print(f"  (WFS GetCapabilities échoué : {e})")
        return []
    layers = []
    for el in root.iter():
        if el.tag.endswith("}FeatureType"):
            name_el = next((c for c in el if c.tag.endswith("}Name")), None)
            if name_el is not None and name_el.text:
                layers.append(name_el.text.strip())
    return layers


def _wfs_query_layer(base_url: str, layer: str, verbose: bool,
                     dataset_id: str, titre: str) -> dict | None:
    """Interroge un layer WFS avec filtre bbox RM et retourne le résultat d'analyse."""
    tentatives = [
        {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
         "TYPENAMES": layer, "BBOX": f"{_WFS_RM_BBOX},EPSG:4326",
         "outputFormat": "application/json", "count": "500"},
        {"SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature",
         "TYPENAME": layer, "BBOX": f"{_WFS_RM_BBOX},EPSG:4326",
         "outputFormat": "application/json", "MAXFEATURES": "500"},
        {"SERVICE": "WFS", "VERSION": "1.0.0", "REQUEST": "GetFeature",
         "TYPENAME": layer, "BBOX": _WFS_RM_BBOX,
         "outputFormat": "GeoJSON", "MAXFEATURES": "500"},
    ]
    sep = "&" if "?" in base_url else "?"
    for params in tentatives:
        try:
            resp = requests.get(f"{base_url}{sep}{urlencode(params)}", timeout=30)
            if resp.status_code != 200:
                continue
            ct = resp.headers.get("content-type", "")
            body = resp.content.lstrip()
            if "json" not in ct and not body.startswith(b"{"):
                continue
            data = resp.json()
            features = data.get("features")
            if features is None:
                continue
            if verbose:
                print(f"  WFS {layer} (v{params['VERSION']}) : {len(features)} features dans bbox RM")
            if not features:
                return _construire_resultat(None, None, None, None, 0, 0, [], [])
            return _analyser_features_geojson(features, verbose, dataset_id, titre)
        except Exception:
            continue
    return None


def analyser_wfs(url: str, verbose: bool = False,
                 dataset_id: str = "", titre: str = "") -> dict | None:
    """Interroge un service WFS et cherche des données dans la bbox de Rennes Métropole."""
    base_url = _wfs_base_url(url)
    if verbose:
        print(f"  WFS base URL : {base_url}")
    layers = _wfs_get_layers(base_url, verbose)
    if not layers:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                     "erreur": "WFS : aucune couche trouvée (GetCapabilities échoué ou vide)"})
        return None
    if verbose:
        print(f"  WFS couches ({len(layers)}) : {layers[:10]}")
    meilleur = None
    for layer in layers[:10]:
        result = _wfs_query_layer(base_url, layer, verbose, dataset_id, titre)
        if result is None:
            continue
        if meilleur is None or result["nb_rm"] > meilleur["nb_rm"]:
            meilleur = result
        if meilleur["nb_rm"] > 0:
            break
    log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                 "nb_rm": meilleur["nb_rm"] if meilleur else 0})
    return meilleur


_ANALYSEURS = {
    "csv":     analyser_csv,
    "zip":     analyser_zip,
    "gz":      analyser_gz,
    "xlsx":    analyser_xlsx,
    "geojson": analyser_geojson,
    "wfs":     analyser_wfs,
    "parquet": analyser_parquet,
}


_MOTS_DICT_TITRE = {
    "dictionnaire", "dict", "codebook", "code book",
    "description des colonnes", "description des champs",
    "nomenclature", "metadonnee", "metadonnees",
}


def _est_dict_titre(titre: str) -> bool:
    t = normaliser(titre)
    return any(m in t for m in _MOTS_DICT_TITRE)


def analyser_dataset(dataset: dict, verbose: bool = False) -> dict | None:
    """
    Analyse toutes les ressources CSV/ZIP/GZ/XLSX/GeoJSON/WFS du dataset (sauf dictionnaires).
    Accumule nb_rm sur toutes les ressources et retourne un résultat combiné
    avec les champs de la ressource la plus riche en données RM.
    """
    nb_rm_total = 0
    meilleur = None  # résultat avec le plus de nb_rm (pour les champs)

    for res in dataset.get("resources", []):
        fmt = _format_analysable(res)
        if not fmt:
            continue
        url = res.get("url", "")
        if not url:
            continue
        if _est_dict_titre(res.get("title", "")):
            continue
        result = _ANALYSEURS[fmt](url, verbose, dataset["id"], dataset["title"])
        if result is None:
            continue
        nb_rm_total += result["nb_rm"]
        if meilleur is None or result["nb_rm"] > meilleur["nb_rm"]:
            meilleur = result

    if meilleur is None:
        return None
    return {**meilleur, "nb_rm": nb_rm_total}


# ---------------------------------------------------------------------------
# Traitement des résultats d'analyse
# ---------------------------------------------------------------------------

def _resumer_ligne(ligne: dict, max_cols: int = 5, max_val: int = 18) -> str:
    """Affiche les premières colonnes d'une ligne CSV sur une seule ligne de terminal."""
    items = list(ligne.items())
    parts = []
    total = 2  # pour les accolades
    for k, v in items[:max_cols]:
        vs = str(v)[:max_val] + ("…" if len(str(v)) > max_val else "")
        ks = str(k)[:18]
        part = f"{ks}: {vs}"
        total += len(part) + 2
        if total > 76:
            parts.append(f"+{len(items) - len(parts)} autres")
            break
        parts.append(part)
    if len(parts) == max_cols and len(items) > max_cols:
        parts.append(f"+{len(items) - max_cols} autres")
    return "{" + ", ".join(parts) + "}"


def traiter_resultat(ds: dict, resultat: dict | None, decouverte: dict) -> None:
    """
    Affiche le résultat d'une analyse (sync ou arrière-plan) et demande
    si on ajoute le JDD aux candidats. Ajoute à vus et sauvegarde.
    """
    did = ds["id"]
    print(f"\n{SEP}")
    print(f"Résultat analyse : {ds['title'][:60]}")

    if resultat is None:
        # Ne pas toucher un dataset explicitement exclu (skip définitif)
        if did in decouverte["exclus"]:
            return
        # Incrémente le compteur d'échecs consécutifs
        n = decouverte["echecs_n"].get(did, 0) + 1
        decouverte["echecs_n"][did] = n
        # Retire de vus (rétrocompat)
        decouverte["vus"] = [v for v in decouverte["vus"] if v != did]
        if did not in decouverte["echecs"]:
            decouverte["echecs"].append(did)
        if n >= 3:
            print(f"  Analyse échouée ({n} fois) — skip définitif recommandé.")
            choix = input("  (s)kip définitif  (r)éessayer plus tard  ? ").strip().lower()
            if choix == "s":
                decouverte["echecs"] = [v for v in decouverte["echecs"] if v != did]
                decouverte["echecs_n"].pop(did, None)
                decouverte["exclus"].append(did)
                decouverte["vus"].append(did)
                print("  Skip définitif enregistré.")
            else:
                print("  Sera reproposé à la prochaine session.")
        else:
            print(f"  Analyse échouée ({n}/3) — sera reproposé à la prochaine session.")
        sauvegarder_decouverte(decouverte)
        return

    # Succès : retire de echecs et remet le compteur à zéro
    decouverte["echecs"] = [v for v in decouverte["echecs"] if v != did]
    decouverte["echecs_n"].pop(did, None)

    print(f"  Total enregistrements : {resultat['nb_total']}")
    print(f"  Dont Rennes Métropole  : {resultat['nb_rm']}")

    if resultat["nb_rm"] > 0:
        # Données RM trouvées → ajout automatique
        if resultat["exemples"]:
            print("  Exemples RM :")
            for ex in resultat["exemples"]:
                print("    " + _resumer_ligne(ex))
        candidat = {
            "dataset_id": ds["id"],
            "titre": ds["title"],
            "dossier": ds["id"][:30].replace("-", "_"),
            "champ_cp": resultat["champ_cp"],
            "champ_ville": resultat["champ_ville"],
            "champ_iris": resultat.get("champ_iris"),
            "champ_adresse": resultat.get("champ_adresse"),
            "nb_rm": resultat["nb_rm"],
        }
        decouverte["candidats"].append(candidat)
        print(f"  Ajouté automatiquement aux candidats.")
        print(f"  >>> src/conf/datasets.py : {json.dumps(candidat, ensure_ascii=False)}")
    else:
        # Aucune donnée RM : montrer les premières lignes pour vérification manuelle
        print("  Premières lignes du fichier :")
        for ligne in resultat.get("premieres_lignes", []):
            print("    " + _resumer_ligne(ligne))
        ajout = input("\n  Ajouter quand même aux candidats ? (o/n) ").strip().lower()
        if ajout == "o":
            candidat = {
                "dataset_id": ds["id"],
                "titre": ds["title"],
                "dossier": ds["id"][:30].replace("-", "_"),
                "champ_cp": resultat["champ_cp"],
                "champ_ville": resultat["champ_ville"],
                "champ_iris": resultat.get("champ_iris"),
                "champ_adresse": resultat.get("champ_adresse"),
                "nb_rm": 0,
            }
            decouverte["candidats"].append(candidat)
            print(f"  Ajouté aux candidats.")
            print(f"  >>> src/conf/datasets.py : {json.dumps(candidat, ensure_ascii=False)}")

    decouverte["vus"].append(did)
    sauvegarder_decouverte(decouverte)


# ---------------------------------------------------------------------------
# Persistance de la découverte
# ---------------------------------------------------------------------------

def charger_decouverte() -> dict:
    """Charge l'historique des JDD déjà vus (pour ne pas les reproposer)."""
    if os.path.exists(DECOUVERTE_FILE):
        with open(DECOUVERTE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("echecs", [])
        d.setdefault("echecs_n", {})     # {dataset_id: nb_echecs consécutifs}
        d.setdefault("sans_ressource", [])
        d.setdefault("exclusions_termes", [])
        return d
    return {"vus": [], "candidats": [], "exclus": [],
            "echecs": [], "echecs_n": {}, "sans_ressource": [],
            "exclusions_termes": []}


def fetcher_datasets_par_ids(ids: list) -> list:
    """Récupère les métadonnées de datasets depuis l'API data.gouv.fr."""
    datasets = []
    for did in ids:
        try:
            r = requests.get(
                f"https://www.data.gouv.fr/api/1/datasets/{did}/",
                timeout=10
            )
            if r.ok:
                ds = r.json()
                ds["_echec"] = True
                datasets.append(ds)
            else:
                print(f"  (impossible de récupérer {did} : HTTP {r.status_code})")
        except Exception as e:
            print(f"  (impossible de récupérer {did} : {e})")
    return datasets


def sauvegarder_decouverte(decouverte: dict) -> None:
    os.makedirs(os.path.dirname(DECOUVERTE_FILE), exist_ok=True)
    with open(DECOUVERTE_FILE, "w", encoding="utf-8") as f:
        json.dump(decouverte, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Évolution 4 : re-analyse des candidats sans champ géo
# ---------------------------------------------------------------------------

def _reanalyser_candidats_sans_champ(decouverte: dict) -> None:
    """Re-analyse les candidats sans champ géo pour détecter SIREN, lat/lon ajoutés depuis."""
    from connectors.datagouv import get_dataset_metadata

    sans_champ = [
        c for c in decouverte.get("candidats", [])
        if not any(c.get(ch) for ch in ("champ_cp", "champ_ville", "champ_iris", "champ_adresse"))
    ]
    if not sans_champ:
        return

    print(f"\n{len(sans_champ)} candidat(s) sans champ géo — re-analyse (SIREN, lat/lon…)")

    def _analyser_un(candidat):
        try:
            meta = get_dataset_metadata(candidat["dataset_id"])
            return candidat, analyser_dataset(meta, verbose=False)
        except Exception:
            return candidat, None

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [(c, ex.submit(_analyser_un, c)) for c in sans_champ]

    modifies = 0
    for candidat, fut in futs:
        try:
            _, result = fut.result()
        except Exception:
            result = None
        if result and any(result.get(ch) for ch in ("champ_cp", "champ_ville", "champ_iris", "champ_adresse")):
            candidat["champ_cp"] = result["champ_cp"]
            candidat["champ_ville"] = result["champ_ville"]
            candidat["champ_iris"] = result.get("champ_iris")
            candidat["champ_adresse"] = result.get("champ_adresse")
            candidat["nb_rm"] = result["nb_rm"]
            print(f"  ✓ {candidat['titre'][:60]} — {result['nb_rm']} lignes RM")
            modifies += 1
        else:
            print(f"  - {candidat['titre'][:60]} — champ toujours inconnu")

    if modifies:
        sauvegarder_decouverte(decouverte)
        print(f"  {modifies} candidat(s) mis à jour — lancez harvest_batch.py pour les moissonner.\n")


# ---------------------------------------------------------------------------
# Évolution 3 : harvest automatique en fin de session
# ---------------------------------------------------------------------------

def _harvest_nouveaux_candidats(decouverte: dict, ids_avant_session: set) -> None:
    """Moissonne les candidats confirmés pendant cette session (évite le re-téléchargement grâce au cache)."""
    nouveaux = [
        c for c in decouverte.get("candidats", [])
        if c["dataset_id"] not in ids_avant_session
        and any(c.get(ch) for ch in ("champ_cp", "champ_ville", "champ_iris", "champ_adresse"))
    ]
    if not nouveaux:
        return

    from harvest_batch import traiter_candidat as _harvest
    from state import charger_state, sauvegarder_state

    print(f"\n{len(nouveaux)} nouveau(x) candidat(s) avec données RM.")
    choix = input("  Lancer le harvest maintenant ? (o/n) ").strip().lower()
    if choix != "o":
        print("  → Harvest ignoré. Lancez harvest_batch.py quand vous voulez.\n")
        return

    state = charger_state()
    ok, vides, echecs = 0, 0, 0
    for candidat in nouveaux:
        print(f"  {candidat['titre'][:65]}")
        try:
            res = _harvest(candidat, state)
            if res["statut"] in ("ok", "cache"):
                nb = res.get("nb_rm", "?")
                print(f"    → {nb} lignes RM" + (" (cache)" if res["statut"] == "cache" else f" ({res.get('format', '')})"))
                sauvegarder_state(state)
                ok += 1
            elif res["statut"] == "vide":
                print(f"    → 0 lignes RM ({res['raison']})")
                vides += 1
            else:
                print(f"    → échec : {res.get('raison', '')}")
                echecs += 1
        except Exception as e:
            print(f"    → erreur : {e}")
            echecs += 1
    print(f"  Harvest : {ok} OK, {vides} vides, {echecs} échecs.")


# ---------------------------------------------------------------------------
# Boucle interactive principale
# ---------------------------------------------------------------------------

def main():
    _purger_cache(jours=30)
    print("=== Découverte interactive de JDD éligibles ===")
    if RECHERCHE_STRUCTUREE:
        labels = [r["label"] for r in REQUETES_STRUCTUREES]
        print(f"Requêtes : {', '.join(labels)}\n")
    else:
        print(f"Mots-clés recherchés : {', '.join(KEYWORDS)}\n")

    decouverte = charger_decouverte()
    # Snapshot avant session pour identifier les nouveaux candidats (évolution 3)
    ids_candidats_avant_session = {c["dataset_id"] for c in decouverte.get("candidats", [])}
    # Évolution 4 : re-analyser les candidats sans champ géo identifié
    _n_sans_champ = sum(
        1 for c in decouverte.get("candidats", [])
        if not any(c.get(ch) for ch in ("champ_cp", "champ_ville", "champ_iris", "champ_adresse"))
    )
    if _n_sans_champ:
        _rep = input(f"\n{_n_sans_champ} candidat(s) sans champ géo — re-analyser maintenant ? (o/N) ").strip().lower()
        if _rep == "o":
            _reanalyser_candidats_sans_champ(decouverte)

    echecs_ids = set(decouverte["echecs"])
    sans_ressource_ids = set(decouverte["sans_ressource"])
    # deja_vus exclut les échecs pour qu'ils ne soient pas filtrés dans les candidats
    deja_vus = set(decouverte["vus"]) - echecs_ids

    # Repropose les analyses échouées en priorité
    echecs_datasets = []
    passer_echecs = False
    if decouverte["echecs"]:
        n = len(decouverte["echecs"])
        print(f"{n} JDD dont l'analyse avait échoué (erreur téléchargement ou parsing).")
        choix_echecs = input(f"  (r)eproposer interactivement  (p)asser jusqu'au prochain run ? ").strip().lower()
        if choix_echecs == "p":
            passer_echecs = True
            print(f"  → {n} JDD passés — ils reviendront au prochain run.\n")
        else:
            echecs_datasets = fetcher_datasets_par_ids(decouverte["echecs"])
            print(f"  → {len(echecs_datasets)} JDD récupérés.\n")

    # --- Choix du point de départ ---
    def _ts(path):
        t = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        age = datetime.datetime.now() - t
        age_str = f"{int(age.total_seconds()//3600)}h" if age.total_seconds() > 3600 else f"{int(age.total_seconds()//60)}min"
        return t.strftime("%d/%m %H:%M"), age_str

    has_api = os.path.exists(RESULTATS_API_FILE)
    has_pf  = os.path.exists(PREFILTRES_FILE)

    print("Point de départ :")
    print("  (n) Nouvelle recherche API + préfiltrage")
    if has_api:
        ts_a, age_a = _ts(RESULTATS_API_FILE)
        print(f"  (a) Résultats API du {ts_a} (il y a {age_a}) + refaire le préfiltrage")
    if has_pf:
        ts_p, age_p = _ts(PREFILTRES_FILE)
        print(f"  (p) Résultats pré-filtrés du {ts_p} (il y a {age_p}) → trier directement")
    options_valides = "n" + ("a" if has_api else "") + ("p" if has_pf else "")
    choix_depart = input(f"Choix [{'/'.join(options_valides)}] : ").strip().lower()
    if choix_depart not in options_valides:
        choix_depart = "n"

    exclusions_termes = decouverte.get("exclusions_termes", [])
    exclus_ids = set(decouverte["exclus"])
    echecs_ids_fetched = {ds["id"] for ds in echecs_datasets}

    def _filtrer_communs(datasets, ignorer_deja_vus=False):
        """Applique les filtres invariants (orgs, géo, termes, déjà vus)."""
        return [
            ds for ds in datasets
            if not est_org_exclue(ds)
            and not est_org_hors_rm(ds)
            and couvre_rennes(ds)
            and (not titre_hors_rm(ds) or description_suggerant_commune(ds))
            and not est_exclu_par_terme(ds, exclusions_termes)
            and (ignorer_deja_vus or ds["id"] not in deja_vus)
            and ds["id"] not in exclus_ids
            and ds["id"] not in echecs_ids_fetched
        ]

    candidats_nouveaux = []

    if choix_depart == "p":
        # Charger directement les résultats pré-filtrés (analyse déjà faite)
        # ignorer_deja_vus=True car ces datasets ont été marqués vus au pré-filtrage
        with open(PREFILTRES_FILE, encoding="utf-8") as f:
            prefiltres_bruts = json.load(f)
        candidats_nouveaux = _filtrer_communs(prefiltres_bruts, ignorer_deja_vus=True)
        _resultats_auto = {}
        print(f"  → {len(candidats_nouveaux)} JDD chargés (après filtres mis à jour).\n")

    else:
        # Recherche API (nouvelle ou depuis cache)
        datasets_trouves = []
        ids_trouves = set()

        if choix_depart == "a":
            with open(RESULTATS_API_FILE, encoding="utf-8") as f:
                datasets_trouves = json.load(f)
            ids_trouves = {ds["id"] for ds in datasets_trouves}
            print(f"  → {len(datasets_trouves)} JDD chargés depuis le cache API.\n")
        else:
            if RECHERCHE_STRUCTUREE:
                for requete in REQUETES_STRUCTUREES:
                    print(f"Recherche : {requete['label']}...")
                    resultats, total = _paginer(requete["params"])
                    print(f"  {total} résultats au total, {len(resultats)} récupérés ({NB_PAGES} pages)")
                    for ds in resultats:
                        if ds["id"] not in ids_trouves:
                            datasets_trouves.append(ds)
                            ids_trouves.add(ds["id"])
            else:
                for keyword in KEYWORDS:
                    print(f"Recherche : « {keyword} »...")
                    resultats, total = rechercher_datasets(keyword)
                    print(f"  {total} résultats au total, {len(resultats)} récupérés ({NB_PAGES} pages)")
                    for ds in resultats:
                        if ds["id"] not in ids_trouves:
                            datasets_trouves.append(ds)
                            ids_trouves.add(ds["id"])
            with open(RESULTATS_API_FILE, "w", encoding="utf-8") as f:
                json.dump(datasets_trouves, f, ensure_ascii=False)

        candidats_nouveaux = _filtrer_communs(datasets_trouves)

        # Analyse automatique : description → en-têtes → analyse RM en parallèle
        if candidats_nouveaux:
            total_pf = len(candidats_nouveaux)
            print(f"\nAnalyse automatique de {total_pf} candidats...", flush=True)
            auto_ajoutes, a_presenter, ignores = [], [], 0
            done_pf = 0
            with ThreadPoolExecutor(max_workers=10) as pf_exec:
                future_to_ds = {pf_exec.submit(pre_filtrer, ds): ds for ds in candidats_nouveaux}
                for fut in as_completed(future_to_ds):
                    ds = future_to_ds[fut]
                    done_pf += 1
                    print(f"\r  {done_pf}/{total_pf} analysés...", end="", flush=True)
                    try:
                        verdict, result = fut.result()
                    except Exception:
                        verdict, result = "presenter", None
                    if verdict == "skip":
                        decouverte["vus"].append(ds["id"])
                        ignores += 1
                    elif verdict == "candidat":
                        candidat = {
                            "dataset_id": ds["id"],
                            "titre": ds["title"],
                            "dossier": ds["id"][:30].replace("-", "_"),
                            "champ_cp":      result["champ_cp"],
                            "champ_ville":   result["champ_ville"],
                            "champ_iris":    result.get("champ_iris"),
                            "champ_adresse": result.get("champ_adresse"),
                            "nb_rm":         result["nb_rm"],
                        }
                        decouverte["candidats"].append(candidat)
                        decouverte["vus"].append(ds["id"])
                        auto_ajoutes.append((ds, result))
                    else:  # "presenter"
                        a_presenter.append((ds, result))
            print()  # saut de ligne après le \r
            sauvegarder_decouverte(decouverte)
            print(f"  {ignores} sans marqueurs géo → ignorés")
            print(f"  {len(auto_ajoutes)} avec données RM → ajoutés automatiquement")
            for ds, result in auto_ajoutes:
                print(f"    ✓ {ds['title'][:60]}  ({result['nb_rm']} lignes RM)")
            print(f"  {len(a_presenter)} à examiner manuellement (0 RM ou échec)")
            candidats_nouveaux = [ds for ds, _ in a_presenter]
            # Garder le résultat d'analyse pour éviter de retélécharger lors de l'affichage
            _resultats_auto = {ds["id"]: result for ds, result in a_presenter}
            # Marquer comme vus pour ne pas les re-analyser lors d'une future recherche API
            for ds in candidats_nouveaux:
                if ds["id"] not in decouverte["vus"]:
                    decouverte["vus"].append(ds["id"])
            sauvegarder_decouverte(decouverte)
        else:
            _resultats_auto = {}

        # Sauvegarder les candidats à présenter (pour reprise via option p)
        with open(PREFILTRES_FILE, "w", encoding="utf-8") as f:
            json.dump(candidats_nouveaux, f, ensure_ascii=False)

    # Les échecs passent en premier
    candidats = echecs_datasets + candidats_nouveaux
    print(f"\n{len(candidats)} JDD à examiner", end="")
    if echecs_datasets:
        print(f" (dont {len(echecs_datasets)} ré-analyse(s) échouée(s))", end="")
    print("\n")


    executor = ThreadPoolExecutor(max_workers=10)
    en_cours = []  # liste de (ds, future)

    def traiter_finis():
        """Affiche les analyses terminées, laisse les autres en attente."""
        restants = []
        for ds_a, fut in en_cours:
            if fut.done():
                try:
                    resultat = fut.result()
                except Exception as e:
                    print(f"\n  Erreur analyse ({ds_a['title'][:40]}) : {e}")
                    resultat = None
                traiter_resultat(ds_a, resultat, decouverte)
            else:
                restants.append((ds_a, fut))
        en_cours[:] = restants

    nb_format_non_supporte = 0  # compteur pour stats fin de session

    try:
        for i, ds in enumerate(candidats, 1):
            # Affiche les analyses en arrière-plan terminées avant chaque nouveau JDD
            if en_cours:
                traiter_finis()
                if en_cours:
                    print(f"  ({len(en_cours)} analyse(s) en arrière-plan en cours...)")

            is_echec = ds.get("_echec", False)
            did = ds["id"]
            ressource = trouver_ressource_analysable(ds)

            if ressource is None:
                ressources = ds.get("resources", [])
                if not ressources:
                    # Vraiment vide : on mémorise pour ne plus jamais vérifier
                    if did not in sans_ressource_ids:
                        decouverte["sans_ressource"].append(did)
                        sans_ressource_ids.add(did)
                        sauvegarder_decouverte(decouverte)
                else:
                    # A des ressources mais dans un format non encore supporté
                    nb_format_non_supporte += 1
                    if is_echec:
                        # Echec dû à un format non supporté, pas à une erreur d'analyse
                        # → retirer de echecs, il reviendra quand le support sera ajouté
                        fmts = formats_disponibles(ds)
                        print(f"\n  (!) Echec {ds['title'][:50]!r} : "
                              f"format non supporté ({', '.join(fmts)}) — retiré des échecs.")
                        decouverte["echecs"] = [j for j in decouverte["echecs"] if j != did]
                        decouverte["echecs_n"].pop(did, None)
                        sauvegarder_decouverte(decouverte)
                continue

            print(f"\n[{i}/{len(candidats)}]")
            if is_echec:
                print("  (!) Analyse précédemment échouée")

            # Si le dataset était dans sans_ressource mais a maintenant des ressources → on le retire
            if did in sans_ressource_ids:
                decouverte["sans_ressource"] = [
                    j for j in decouverte["sans_ressource"] if j != did
                ]
                sans_ressource_ids.discard(did)
                sauvegarder_decouverte(decouverte)
                print("  (ressources détectées — analyse disponible)")

            extrait = obtenir_extrait(ressource)
            # Dictionnaire de colonnes → essayer les autres ressources CSV du dataset
            if extrait == "__DICTIONNAIRE__":
                extrait = "(dictionnaire de colonnes)"
                for r in ds.get("resources", []):
                    if r is ressource or (r.get("format") or "").lower() != "csv":
                        continue
                    candidat_extrait = telecharger_extrait_csv(r.get("url", ""))
                    if candidat_extrait and candidat_extrait not in ("__BINAIRE__", "__DICTIONNAIRE__") \
                            and not candidat_extrait.startswith("("):
                        extrait = candidat_extrait
                        ressource = r
                        break
            if extrait == "__BINAIRE__":
                nb_format_non_supporte += 1
                fmt = ressource.get("format", "?").upper()
                if is_echec:
                    print(f"\n  (!) Echec {ds['title'][:50]!r} : "
                          f"fichier binaire déclaré {fmt} — retiré des échecs.")
                    decouverte["echecs"] = [j for j in decouverte["echecs"] if j != did]
                    decouverte["echecs_n"].pop(did, None)
                    sauvegarder_decouverte(decouverte)
                continue
            if extrait.startswith("(Impossible de télécharger"):
                # Si déjà exclu définitivement, on ignore silencieusement
                if did in decouverte["exclus"]:
                    continue
                # Erreur réseau : l'analyse en arrière-plan échouera aussi → enregistrer echec
                afficher_fiche(ds, extrait)
                n = decouverte["echecs_n"].get(did, 0) + 1
                decouverte["echecs_n"][did] = n
                if did not in decouverte["echecs"]:
                    decouverte["echecs"].append(did)
                decouverte["vus"] = [v for v in decouverte["vus"] if v != did]
                if n >= 3:
                    print(f"  URL inaccessible ({n} fois) — skip définitif recommandé.")
                    choix = input("  (s)kip définitif  (p)asser ? ").strip().lower()
                    if choix != "p":
                        decouverte["echecs"] = [v for v in decouverte["echecs"] if v != did]
                        decouverte["echecs_n"].pop(did, None)
                        decouverte["exclus"].append(did)
                        decouverte["vus"].append(did)
                        print("  Skip définitif enregistré.")
                    else:
                        print("  Sera reproposé à la prochaine session.")
                else:
                    print(f"  URL inaccessible ({n}/3) — sera reproposé à la prochaine session.")
                sauvegarder_decouverte(decouverte)
                continue
            afficher_fiche(ds, extrait, resultat=_resultats_auto.get(did))

            # Auto-analyse si le dataset est clairement IRIS
            titre_desc = (ds.get("title", "") + " " + (ds.get("description", "") or "")).lower()
            est_iris = bool(re.search(r'\biris\b', titre_desc))
            if est_iris and not is_echec:
                print("  [IRIS] Analyse automatique lancée en arrière-plan.")
                future = executor.submit(analyser_dataset, ds, False)
                en_cours.append((ds, future))
                print(f"  ({len(en_cours)} analyse(s) en cours.)")
                continue

            if is_echec:
                n_echecs = decouverte["echecs_n"].get(did, 0)
                suffixe = f" — {n_echecs} échec(s) précédent(s)" if n_echecs else ""
                choix = input(
                    f"\n(s)kip définitif  (p)asser  (a)nalyse  (x)exception  (q)uitter ?{suffixe} "
                ).strip().lower()
            else:
                choix = input(
                    "\n(s)kip  (p)asser  (a)nalyse  (x)exception  (q)uitter ? "
                ).strip().lower()

            # Affiche immédiatement les analyses terminées pendant la lecture
            if en_cours:
                traiter_finis()

            if choix == "q":
                break
            elif choix == "p":
                continue  # passe sans enregistrer — reviendra la prochaine session
            elif choix == "x":
                org_name = (ds.get("organization") or {}).get("name", "")
                suggestion = f" [suggestion : {org_name}]" if org_name else ""
                terme = input(f"  Terme à exclure{suggestion} : ").strip()
                if terme:
                    decouverte["exclusions_termes"].append(terme)
                    sauvegarder_decouverte(decouverte)
                    print(f"  Terme {terme!r} ajouté — les JDD contenant ce terme dans le titre ou l'org seront filtrés.")
                continue  # passe le JDD courant, le terme filtrera les suivants
            elif choix == "a":
                future = executor.submit(analyser_dataset, ds, False)
                en_cours.append((ds, future))
                print(f"  Analyse lancée en arrière-plan ({len(en_cours)} en cours).")
                continue  # pas ajouté à vus pour l'instant
            else:
                # skip définitif (s ou autre)
                if is_echec:
                    # Retire des échecs
                    decouverte["echecs"] = [
                        i for i in decouverte["echecs"] if i != ds["id"]
                    ]
                decouverte["exclus"].append(ds["id"])

            decouverte["vus"].append(ds["id"])
            sauvegarder_decouverte(decouverte)

    except KeyboardInterrupt:
        print("\n\nInterruption clavier.")

    # Attend et affiche toutes les analyses restantes (dans l'ordre de complétion)
    if en_cours:
        total_restants = len(en_cours)
        print(f"\n{total_restants} analyse(s) en cours, attente des résultats...")
        print("(Ctrl+C pour abandonner les analyses restantes)\n")
        futures_map = {fut: ds_a for ds_a, fut in en_cours}
        en_attente = set(futures_map.keys())
        terminees = 0
        try:
            while en_attente:
                done, en_attente = wait(en_attente, timeout=15, return_when=FIRST_COMPLETED)
                for fut in done:
                    ds_a = futures_map[fut]
                    terminees += 1
                    print(f"\n  [{terminees}/{total_restants}] Résultat reçu : {ds_a['title'][:50]}")
                    try:
                        resultat = fut.result()
                    except Exception as e:
                        msg = str(e) or type(e).__name__
                        print(f"  Erreur inattendue : {msg}")
                        resultat = None
                    traiter_resultat(ds_a, resultat, decouverte)
                if en_attente:
                    titres = [futures_map[f]["title"][:40] for f in list(en_attente)[:3]]
                    suite = "…" if len(en_attente) > 3 else ""
                    print(f"  [{terminees}/{total_restants}] {len(en_attente)} en cours : {', '.join(titres)}{suite}")
        except KeyboardInterrupt:
            print("\nAbandon des analyses restantes — elles seront reproposées.")

    executor.shutdown(wait=False)

    print("\n=== Session terminée ===")
    print(f"Résultats sauvegardés dans {DECOUVERTE_FILE}")
    if nb_format_non_supporte:
        print(f"\n{nb_format_non_supporte} JDD ignorés cette session (format non encore supporté : "
              f"XLS, ZIP, géo, WMS…) — ils réapparaîtront quand le support sera ajouté.")
    if decouverte["echecs"]:
        print(f"\n{len(decouverte['echecs'])} JDD en échec — reproposés à la prochaine session.")
    if decouverte["candidats"]:
        print(f"\nJDD candidats retenus : {len(decouverte['candidats'])}")
        for c in decouverte["candidats"]:
            print(f"  - {c['titre'][:60]}")

    # Évolution 3 : harvest automatique des nouveaux candidats
    _harvest_nouveaux_candidats(decouverte, ids_candidats_avant_session)


if __name__ == "__main__":
    main()
