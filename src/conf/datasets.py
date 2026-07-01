# Liste des jeux de données à moissonner.
# Pour ajouter un nouveau JDD data.gouv.fr, ajouter une entrée dans DATASETS.
# Pour les publications INSEE directes (insee.fr), voir DATASETS_INSEE.

#
# Thèmes RUDI valides (champ "theme", obligatoire) :
#   economy | citizenship | energyNetworks | culture | transportation | children
#   environment | townPlanning | location | education | publicSpace | health
#   housing | society

DATASETS = [
]

# ---------------------------------------------------------------------------
# Services géographiques (WFS, WMS, OGC API Features)
# Harvested via : python3 src/harvest_geo.py
# ---------------------------------------------------------------------------
# Champs obligatoires :
#   id         : identifiant local unique (slug sans espaces)
#   type       : "wfs" | "wms" | "ogcapi"
#   url        : URL du service (sans paramètres OGC — seront ajoutés automatiquement)
#   dossier    : sous-dossier de sortie sous data/
#   theme      : thème RUDI (voir ci-dessus)
#
# Champs optionnels :
#   couches    : liste de typename (WFS/OGC) ou layer names (WMS) à traiter
#                Si absent, détection automatique via GetCapabilities / /collections
#   titre      : titre lisible (utilisé si absent du service)
#   producteur : nom du producteur

import json as _json
import os as _os
_GEO_FILE = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "data", "geo_services.json")
_geo_json = _json.load(open(_GEO_FILE, encoding="utf-8")) if _os.path.exists(_GEO_FILE) else []

DATASETS_GEO = _geo_json + [
    # Entrées manuelles (optionnel — la plupart viennent de data/geo_services.json via discover.py)
]

# ---------------------------------------------------------------------------
# Publications INSEE directes (hors data.gouv.fr)
# Harvested via : python3 src/harvest_insee.py
# ---------------------------------------------------------------------------
# Champs communs :
#   id             : identifiant local unique
#   titre          : titre lisible
#   url_page       : page INSEE stable — fallback scraping si url_direct retourne 404
#   url_direct     : URL de téléchargement directe (à mettre à jour à chaque millésime)
#   membre_pattern : regex (sur le nom du fichier seul) pour sélectionner le CSV dans le ZIP
#   dict_pattern   : regex pour le fichier dictionnaire des variables (optionnel)
#   champ_iris     : colonne géographique principale (code IRIS 9c ou commune 5c)
#   champ_iris_ou  : 2e colonne géo (optionnel) — garde une ligne si l'UNE OU L'AUTRE est RM
#                    ex: commune de résidence OU commune de travail pour les mobilités
#   dossier        : sous-dossier de sortie sous data/
#   theme          : thème RUDI

# ---------------------------------------------------------------------------
# Jeux de données OEB (Observatoire de l'Environnement en Bretagne)
# Portail data-fair : https://data.bretagne-environnement.fr
# Harvested via : python3 src/harvest_oeb.py
# Pour découvrir les JDD disponibles : python3 src/harvest_oeb.py --decouvrir
# ---------------------------------------------------------------------------
# Champs obligatoires :
#   id      : slug du JDD sur le portail OEB (identifiant API data-fair)
#   dossier : sous-dossier de sortie sous data/
#   theme   : thème RUDI (voir ci-dessus)
#
# Champs optionnels :
#   titre        : titre lisible (lu depuis l'API si absent)
#   champ_code   : colonne contenant le code territoire (défaut : "code_territoire")
#   champ_echelle: colonne contenant l'échelle (défaut : "echelle_territoire")
#                  Mettre None pour télécharger toutes les lignes sans filtre d'échelle

DATASETS_OEB = [
    # ------------------------------------------------------------------
    # Indicateurs d'hydrologie future TRACC
    # Projections hydrologiques par commune à l'horizon 2100 (débit, étiage).
    # Filtre : echelle_territoire=Communes + code_territoire ∈ codes_INSEE_RM
    #          + echelle_territoire=EPCI + code_territoire=243500139
    # ------------------------------------------------------------------
    {
        "id": "indicateurs-dhydrologie-future-tracc",
        "titre": "Indicateurs d'hydrologie future TRACC",
        "dossier": "oeb_hydrologie_tracc",
        "theme": "environment",
    },
    # ------------------------------------------------------------------
    # Ajouter d'autres JDD OEB ici.
    # Lancez : python3 src/harvest_oeb.py --decouvrir
    # pour voir tous les JDD disponibles sur le portail.
    # ------------------------------------------------------------------
]

# ---------------------------------------------------------------------------
# Publications INSEE directes (hors data.gouv.fr)
# Harvested via : python3 src/harvest_insee.py
# ---------------------------------------------------------------------------
DATASETS_INSEE = [
    # ------------------------------------------------------------------
    # BIC IRIS — Évolution et structure de la population (millésime 2020)
    # Géographie au 01/01/2022. Format : ZIP → 1 CSV + 1 meta_CSV.
    # Failsafe : si url_direct → 404, scraping de url_page trouve le nouveau ZIP.
    # ------------------------------------------------------------------
    {
        "id": "bic-iris",
        "titre": "Bases infra-communales IRIS — évolution et structure de la population",
        "url_page": "https://www.insee.fr/fr/statistiques/7704076",
        "url_direct": "https://www.insee.fr/fr/statistiques/fichier/7704076/base-ic-evol-struct-pop-2020_csv.zip",
        "membre_pattern": r"^(?!meta_).+\.CSV$",
        "dict_pattern": r"^meta_.+\.CSV$",
        "champ_iris": "IRIS",
        "dossier": "insee_bic_iris",
        "theme": "society",
    },
    # ------------------------------------------------------------------
    # BIC IRIS — Diplômes et formation (millésime 2019, RP 2019)
    # Niveaux de diplôme et scolarisation par IRIS. Même format que BIC pop.
    # ------------------------------------------------------------------
    {
        "id": "bic-iris-diplomes",
        "titre": "Bases infra-communales IRIS — diplômes et formation",
        "url_page": "https://www.insee.fr/fr/statistiques/6543298",
        "url_direct": "https://www.insee.fr/fr/statistiques/fichier/6543298/base-ic-diplomes-formation-2019_csv.zip",
        "membre_pattern": r"^(?!meta_).+\.CSV$",
        "dict_pattern": r"^meta_.+\.CSV$",
        "champ_iris": "IRIS",
        "dossier": "insee_bic_iris_diplomes",
        "theme": "education",
    },
    # ------------------------------------------------------------------
    # Filosofi — Revenus, pauvreté et niveau de vie par commune (millésime 2021)
    # Millésime 2022 annulé par INSEE (pb géolocalisation post-réforme taxe foncière).
    # ATTENTION format changé depuis 2021 : passage en long/SDMX.
    #   ZIP → DS_FILOSOFI_CC_data.csv (GEO × FILOSOFI_MEASURE × OBS_VALUE)
    #   Filtre : GEO ∈ codes_communes_RM (5c) ou GEO == EPCI_RM (9c)
    # url_direct inclut le millésime de géographie (geo2025) : si 404, scraping trouve
    # le nouveau ZIP sur url_page (le pattern _csv.zip est stable).
    # ------------------------------------------------------------------
    {
        "id": "filosofi-commune",
        "titre": "Revenus, pauvreté et niveau de vie — communes et EPCI (Filosofi)",
        "url_page": "https://www.insee.fr/fr/statistiques/7756729",
        "url_direct": "https://www.insee.fr/fr/statistiques/fichier/7756729/base-cc-filosofi-2021-geo2025_csv.zip",
        "membre_pattern": r"^DS_FILOSOFI_CC_data\.csv$",
        "dict_pattern": r"^DS_FILOSOFI_CC_metadata\.csv$",
        "champ_iris": "GEO",
        "dossier": "insee_filosofi_commune",
        "theme": "society",
    },
    # ------------------------------------------------------------------
    # BPE — Base permanente des équipements (millésime 2024)
    # 1 seul CSV dans le ZIP, pas de dictionnaire inclus (PDF séparé sur la page).
    # Le numéro dans le nom suit les 2 derniers chiffres de l'année : BPE24 → BPE25…
    # La BPE 2024 contient LATITUDE/LONGITUDE/DCIRIS (géolocalisation).
    # ------------------------------------------------------------------
    {
        "id": "bpe",
        "titre": "Base permanente des équipements (BPE)",
        "url_page": "https://www.insee.fr/fr/statistiques/8217525",
        "url_direct": "https://www.insee.fr/fr/statistiques/fichier/8217525/BPE24.zip",
        "membre_pattern": r"^BPE\d{2}\.csv$",
        "champ_iris": "DEPCOM",
        "dossier": "insee_bpe",
        "theme": "publicSpace",
    },
    # ------------------------------------------------------------------
    # Mobilités professionnelles 2019 — flux domicile/travail par commune
    # Fichier individuel pondéré (7,93M individus, 32 variables).
    # champ_iris     = COMMUNE (commune de résidence, code INSEE 5c)
    # champ_iris_ou  = DCLT   (commune de travail, code INSEE 5c)
    # → on garde toute ligne où la résidence OU le lieu de travail est en RM.
    # Le ZIP contient aussi Varmod_MOBPRO_2019.csv (dictionnaire des variables).
    # Pattern survivant au changement d'année : FD_MOBPRO_YYYY.csv.
    # ------------------------------------------------------------------
    {
        "id": "mobpro",
        "titre": "Mobilités professionnelles — déplacements résidence/travail (RP 2019)",
        "url_page": "https://www.insee.fr/fr/statistiques/6456056",
        "url_direct": "https://www.insee.fr/fr/statistiques/fichier/6456056/RP2019_mobpro_csv.zip",
        "membre_pattern": r"^FD_MOBPRO_\d{4}\.csv$",
        "dict_pattern": r"^Varmod_MOBPRO_\d{4}\.csv$",
        "champ_iris": "COMMUNE",
        "champ_iris_ou": "DCLT",
        "dossier": "insee_mobpro",
        "theme": "transportation",
    },
]
