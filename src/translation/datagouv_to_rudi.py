import re
import uuid

# Thèmes acceptés par le nœud RUDI (conformes aux catégories RUDI)
THEMES_RUDI = {
    "economy", "citizenship", "energyNetworks", "culture", "transportation",
    "children", "environment", "townPlanning", "location", "education",
    "publicSpace", "health", "housing", "society",
}

# Correspondance licences data.gouv.fr → RUDI
LICENCES = {
    "lov2": {
        "licence_type": "STANDARD",
        "licence_label": "etalab-2.0",
        "licence_uri": "https://www.etalab.gouv.fr/licence-ouverte-open-licence",
    },
    "odc-odbl": {
        "licence_type": "STANDARD",
        "licence_label": "odbl-1.0",
        "licence_uri": "https://opendatacommons.org/licenses/odbl/1-0",
    },
}

# Emprise géographique de Rennes Métropole (bounding box WGS84)
BBOX_RENNES_METROPOLE = {
    "bounding_box": {
        "west_longitude": -2.08,
        "east_longitude": -1.37,
        "south_latitude": 47.89,
        "north_latitude": 48.27,
    }
}


def _local_id_depuis_dataset_id(dataset_id: str) -> str:
    """Génère un local_id stable à partir de l'identifiant data.gouv.fr."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://www.data.gouv.fr/datasets/{dataset_id}"))


def _trouver_ressource_principale(metadata_source: dict) -> dict | None:
    """Retourne la meilleure ressource téléchargeable : CSV d'abord, JSON ensuite."""
    resources = metadata_source.get("resources", [])
    for r in resources:
        fmt = r.get("format", "").lower()
        titre = r.get("title", "").lower()
        if fmt == "csv" and ".zip" not in titre and ".gz" not in titre:
            return r
    for r in resources:
        fmt = r.get("format", "").lower()
        titre = r.get("title", "").lower()
        if fmt == "json" and "geo" not in titre:
            return r
    return None


def traduire_metadonnees(metadata_source: dict, zone: str = "Rennes Métropole",
                          dossier_nom: str = "",
                          fichiers_filtres: list | None = None,
                          fichiers_dicts: list | None = None,
                          theme: str = "environment") -> dict:
    """
    Traduit les métadonnées data.gouv.fr au format RUDI.

    fichiers_filtres : [(nom_fichier, nb_rm, ressource_originale), ...] ou None
    fichiers_dicts   : [(nom_fichier, ressource_originale), ...] ou None
    dossier_nom      : slug pour nommer le fichier filtré par défaut (ex: "prix-carburants")
    theme            : thème RUDI (voir THEMES_RUDI) ; à préciser dans datasets.py
    """
    if theme not in THEMES_RUDI:
        raise ValueError(f"Thème RUDI invalide : {theme!r}. Valeurs acceptées : {sorted(THEMES_RUDI)}")
    dataset_id = metadata_source["id"]
    titre_original = metadata_source["title"]
    titre_localise = f"{titre_original} - {zone}"

    description_originale = metadata_source.get("description", "")
    description_localisee = (
        f"Version localisée sur {zone}. "
        f"Données filtrées pour ne conserver que les enregistrements des communes de {zone}.\n\n"
        f"Jeu de données source (France entière) : https://www.data.gouv.fr/datasets/{dataset_id}\n\n"
        + description_originale
    )

    synopsis_base = titre_original[:120]
    synopsis = f"{synopsis_base} — données filtrées sur {zone}."
    if len(synopsis) > 150:
        synopsis = synopsis[:149]

    org = metadata_source.get("organization", {})
    producer = {
        "organization_name": org.get("name", "Producteur inconnu"),
    }

    url_source = f"https://www.data.gouv.fr/datasets/{dataset_id}"
    ressource_principale = _trouver_ressource_principale(metadata_source)

    # media_id_source déterministe : même dataset = même ID à chaque run
    media_id_source = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_source}/source"))

    # Entrées pour les fichiers filtrés (une par ressource sauvegardée)
    if not fichiers_filtres:
        slug = dossier_nom or dataset_id[:30]
        fmt_filtre = "CSV" if (ressource_principale and ressource_principale.get("format", "").lower() == "csv") else "JSON"
        zone_slug = re.sub(r"[^a-z0-9]", "", zone.lower())  # "rennesmetropole"
        fichiers_filtres = [(f"{slug}-{zone_slug}.{fmt_filtre.lower()}", 0, None)]

    medias_filtres = []
    for nom_fichier, _, ressource_orig in fichiers_filtres:
        media_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_source}/filtered/{zone}/{nom_fichier}"))
        caption_base = ressource_orig.get("title", nom_fichier) if ressource_orig else nom_fichier
        fmt_label = "CSV" if nom_fichier.endswith(".csv") else "JSON"
        medias_filtres.append({
            "media_id": media_id,
            "media_type": "FILE",
            "media_name": nom_fichier,
            "media_caption": f"{caption_base} — données filtrées sur {zone} ({fmt_label})",
            "connector": {
                "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
                "interface_contract": "dwnl",
            },
        })

    medias_dicts = []
    for nom_fichier, ressource_orig in (fichiers_dicts or []):
        media_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_source}/dict/{nom_fichier}"))
        caption = ressource_orig.get("title", nom_fichier) if ressource_orig else nom_fichier
        medias_dicts.append({
            "media_id": media_id,
            "media_type": "FILE",
            "media_name": nom_fichier,
            "media_caption": f"Dictionnaire des variables — {caption}",
            "connector": {
                "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
                "interface_contract": "dwnl",
            },
        })

    available_formats = medias_filtres + medias_dicts + [
        {
            "media_id": media_id_source,
            "media_type": "SERVICE",
            "media_name": "source-data-gouv",
            "media_caption": "Jeu de données complet (France entière) sur data.gouv.fr",
            "connector": {
                "url": ressource_principale["url"] if ressource_principale else url_source,
                "interface_contract": "dwnl",
            },
        },
    ]

    tags = [t.get("name", t) if isinstance(t, dict) else t for t in metadata_source.get("tags", [])]
    keywords = tags[:8] + [zone.lower()]

    licence_datagouv = metadata_source.get("license", "")
    licence_rudi = LICENCES.get(licence_datagouv, {
        "licence_type": "CUSTOM",
        "custom_licence_label": [{"lang": "fr", "text": licence_datagouv}],
        "custom_licence_uri": url_source,
    })

    dates = {}
    if metadata_source.get("created_at"):
        dates["created"] = metadata_source["created_at"][:10] + "T00:00:00Z"
    if metadata_source.get("last_modified"):
        dates["updated"] = metadata_source["last_modified"][:10] + "T00:00:00Z"

    rudi_metadata = {
        "local_id": _local_id_depuis_dataset_id(dataset_id),
        "resource_title": titre_localise,
        "synopsis": [{"lang": "fr", "text": synopsis}],
        "summary": [{"lang": "fr", "text": description_localisee}],
        "theme": theme,
        "keywords": keywords,
        "producer": producer,
        "contacts": [],
        "available_formats": available_formats,
        "dataset_dates": dates,
        "storage_status": "online",
        "access_condition": {
            "licence": licence_rudi,
            "confidentiality": {
                "restricted_access": False,
                "gdpr_sensitive": False,
            },
        },
        "geography": BBOX_RENNES_METROPOLE,
        "metadata_info": {
            "metadata_dates": dates,
            "metadata_source": url_source,
        },
    }

    return rudi_metadata
