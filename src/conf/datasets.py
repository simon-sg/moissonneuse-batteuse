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
# Publications INSEE directes (hors data.gouv.fr)
# Harvested via : python3 src/harvest_insee.py
# ---------------------------------------------------------------------------
# Champs :
#   id             : identifiant local unique (non data.gouv.fr)
#   titre          : titre lisible
#   url_page       : page INSEE stable (utilisée si url_direct retourne 404)
#   url_direct     : URL de téléchargement directe — à mettre à jour à chaque millésime
#   membre_pattern : regex (sur le nom du fichier seul) pour sélectionner le CSV dans le ZIP
#   champ_iris     : nom de la colonne géographique dans le CSV
#                    (code IRIS 9 chiffres, code commune 5 chiffres)
#   dossier        : sous-dossier de sortie sous data/
#   theme          : thème RUDI

DATASETS_INSEE = [
    {
        "id": "bic-iris",
        "titre": "Bases infra-communales IRIS — évolution et structure de la population",
        "url_page": "https://www.insee.fr/fr/statistiques/6543200",
        # Millésime 2019 (dernier disponible au 2025-01) — mettre à jour à chaque nouvelle édition
        "url_direct": "https://www.insee.fr/fr/statistiques/fichier/6543200/base-ic-evol-struct-pop-2019_csv.zip",
        # Exclut le dictionnaire de variables (préfixé "meta_")
        "membre_pattern": r"^(?!meta_).+\.CSV$",
        "champ_iris": "IRIS",    # code IRIS 9 chiffres : 5 premiers = code commune INSEE
        "dossier": "insee_bic_iris",
        "theme": "society",
    },
    {
        "id": "filosofi-commune",
        "titre": "Revenus, pauvreté et niveau de vie par commune (Filosofi)",
        "url_page": "https://www.insee.fr/fr/statistiques/6692392",
        # Millésime 2020 — le ZIP contient plusieurs granularités (COM, DEP, REG, EPCI…)
        "url_direct": "https://www.insee.fr/fr/statistiques/fichier/6692392/base-cc-filosofi-2020_CSV.zip",
        # On ne garde que la granularité commune (_COM), pattern survivant au changement de millésime
        "membre_pattern": r"^cc_filosofi_\d{4}_COM\.csv$",
        "champ_iris": "CODGEO",  # code commune INSEE 5 chiffres
        "dossier": "insee_filosofi_commune",
        "theme": "society",
    },
    {
        "id": "bpe",
        "titre": "Base permanente des équipements (BPE)",
        "url_page": "https://www.insee.fr/fr/statistiques/8217525",
        # Millésime 2024 — le numéro dans le nom suit les 2 derniers chiffres de l'année
        "url_direct": "https://www.insee.fr/fr/statistiques/fichier/8217525/BPE24.zip",
        # Pattern survivant aux mises à jour annuelles : BPE24 → BPE25 → …
        "membre_pattern": r"^BPE\d{2}\.csv$",
        "champ_iris": "DEPCOM",  # code commune INSEE 5 chiffres
        "dossier": "insee_bpe",
        "theme": "publicSpace",
    },
]
