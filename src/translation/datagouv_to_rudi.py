import uuid

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


def traduire_metadonnees(metadata_source: dict, zone: str = "Rennes Métropole") -> dict:
    """
    Traduit les métadonnées data.gouv.fr au format RUDI.
    Ajoute la mention que c'est une version localisée sur la zone spécifiée.

    :param metadata_source: dict retourné par l'API data.gouv.fr
    :param zone: nom de la zone géographique filtrée
    :return: dict au format RUDI
    """
    dataset_id = metadata_source["id"]
    titre_original = metadata_source["title"]
    titre_localise = f"{titre_original} - {zone}"

    description_originale = metadata_source.get("description", "")
    description_localisee = (
        f"Version localisée sur {zone}. "
        f"Seuls les points de vente situés dans les communes de {zone} sont inclus.\n\n"
        f"Jeu de données source (France entière) : https://www.data.gouv.fr/datasets/{dataset_id}\n\n"
        + description_originale
    )

    synopsis = f"Prix des carburants à la pompe pour les stations de {zone}. Mis à jour toutes les 10 minutes."
    if len(synopsis) > 150:
        synopsis = synopsis[:149]

    org = metadata_source.get("organization", {})
    producer = {
        "organization_name": org.get("name", "Producteur inconnu"),
    }

    url_source = f"https://www.data.gouv.fr/datasets/{dataset_id}"
    ressource_source = next(
        (r for r in metadata_source.get("resources", []) if ".json" in r.get("title", "") and ".geojson" not in r.get("title", "")),
        None
    )

    # Les media_id sont déterministes : même dataset + même zone = même ID à chaque run.
    # Indispensable pour que les mises à jour sur le nœud RUDI écrasent le bon média.
    media_id_filtre = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_source}/filtered/{zone}"))
    media_id_source = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_source}/source"))

    available_formats = [
        {
            "media_id": media_id_filtre,
            "media_type": "FILE",
            "media_name": f"carburants-{zone.lower().replace(' ', '-')}.json",
            "media_caption": f"Données filtrées sur {zone} au format JSON",
            "connector": {
                # L'URL réelle sera renseignée lors du dépôt sur le nœud RUDI
                "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
                "interface_contract": "dwnl",
            },
        },
        {
            "media_id": media_id_source,
            "media_type": "SERVICE",
            "media_name": "source-data-gouv",
            "media_caption": f"Jeu de données complet (France entière) sur data.gouv.fr",
            "connector": {
                "url": ressource_source["url"] if ressource_source else url_source,
                "interface_contract": "dwnl",
            },
        },
    ]

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
        "theme": "environment",
        "keywords": ["carburant", "prix", "station-service", zone.lower()],
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
