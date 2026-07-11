"""
Constructeur de métadonnées RUDI partagé entre tous les scripts de moisson.

Élimine la duplication entre harvest_insee._generer_rudi_metadata(),
harvest_oeb._generer_rudi_metadata() et harvest_bdnb._generer_rudi_metadata().

Usage :
    from translation.rudi_builder import (
        LICENCE_ETALAB, BBOX_RM,
        media_filtre, media_source, media_metadata_page,
        construire_rudi_metadata,
    )
"""

import uuid

from conf.communes_rm import BBOX_RM_RUDI as BBOX_RM
from connectors.contacts import resoudre_contacts

# Licence Etalab 2.0, partagée par tous les scripts de moisson
LICENCE_ETALAB = {
    "licence_type": "STANDARD",
    "licence_label": "etalab-2.0",
    "licence_uri": "https://www.etalab.gouv.fr/licence-ouverte-open-licence",
}


# ---------------------------------------------------------------------------
# Helpers pour les entrées media
# ---------------------------------------------------------------------------

def media_filtre(prefixe_id: str, nom_fichier: str, zone: str,
                 caption: str | None = None) -> dict:
    """Construit une entrée media FILE pour un fichier filtré."""
    return {
        "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{prefixe_id}/filtered/{nom_fichier}")),
        "media_type": "FILE",
        "media_name": nom_fichier,
        "media_caption": caption or f"{nom_fichier} — données filtrées sur {zone} (CSV)",
        "connector": {
            "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
            "interface_contract": "dwnl",
        },
    }


def media_source(prefixe_id: str, url_source: str, caption: str) -> dict:
    """Construit une entrée media SERVICE pour la source complète."""
    return {
        "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{prefixe_id}/source")),
        "media_type": "SERVICE",
        "media_name": "source-data",
        "media_caption": caption,
        "connector": {
            "url": url_source,
            "interface_contract": "dwnl",
        },
    }


def media_metadata_page(prefixe_id: str, url_fiche: str, caption: str) -> dict:
    """Construit une entrée media SERVICE pour la fiche de métadonnées d'origine."""
    return {
        "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{prefixe_id}/metadata-page")),
        "media_type": "SERVICE",
        "media_name": "source-metadata",
        "media_caption": caption,
        "connector": {
            "url": url_fiche,
            "interface_contract": "dwnl",
        },
    }


def media_dict(prefixe_id: str, nom_fichier: str) -> dict:
    """Construit une entrée media FILE pour un dictionnaire de variables."""
    return {
        "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{prefixe_id}/dict/{nom_fichier}")),
        "media_type": "FILE",
        "media_name": nom_fichier,
        "media_caption": f"Dictionnaire des variables — {nom_fichier} (CSV)",
        "connector": {
            "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
            "interface_contract": "dwnl",
        },
    }


def media_pdf(prefixe_id: str, nom_fichier: str, titre: str) -> dict:
    """Construit une entrée media FILE pour un document PDF."""
    return {
        "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{prefixe_id}/pdf/{nom_fichier}")),
        "media_type": "FILE",
        "media_name": nom_fichier,
        "media_caption": f"Documentation — {titre} (PDF)",
        "connector": {
            "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
            "interface_contract": "dwnl",
        },
    }


# ---------------------------------------------------------------------------
# Construction de la date
# ---------------------------------------------------------------------------

def _parser_date_http(date_str: str | None) -> str | None:
    """Convertit une date HTTP (Last-Modified, updatedAt) en ISO 8601."""
    if not date_str:
        return None
    import datetime
    # Format ISO direct (OEB updatedAt : "2025-01-15T10:30:00.000Z")
    if "T" in date_str:
        return date_str[:10] + "T00:00:00Z"
    # Format HTTP (INSEE/BDNB Last-Modified : "Wed, 12 Feb 2025 09:41:37 GMT")
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str).strftime("%Y-%m-%dT00:00:00Z")
    except Exception:
        return datetime.date.today().isoformat() + "T00:00:00Z"


# ---------------------------------------------------------------------------
# Assemblage final
# ---------------------------------------------------------------------------

def construire_rudi_metadata(
    *,
    local_id: str,
    titre: str,
    synopsis: str,
    description: str,
    theme: str,
    keywords: list[str],
    producteur_nom: str,
    url_source: str,
    url_fiche: str,
    medias: list[dict],
    date_source: str | None = None,
    contacts_source: list[dict] | None = None,
    metadata_source_label: str = "source",
) -> dict:
    """Assemble le dict complet rudi_metadata.json.

    Tous les arguments sont nommés (keyword-only) pour forcer la lisibilité
    des sites d'appel.

    Paramètres :
        local_id          : ID déterministe (UUIDv5) — stable d'un run à l'autre
        titre             : titre lisible avec zone (ex: "Titre — Rennes Métropole")
        synopsis          : résumé court (≤150 car.)
        description       : description complète (preamble + complément)
        theme             : thème RUDI (economy, society, environment, …)
        keywords          : liste de mots-clés
        producteur_nom    : nom de l'organisation productrice
        url_source        : URL du jeu de données source (data.gouv.fr, insee.fr, etc.)
        url_fiche         : URL de la fiche de métadonnées d'origine
        medias            : liste d'entrées available_formats (filtres + dicts + source + metadata_page)
        date_source       : Last-Modified HTTP ou updatedAt (converti en ISO 8601)
        contacts_source   : contacts du producteur (optionnel — résolu via resoudre_contacts si absent)
        metadata_source_label : nom du media SERVICE source (défaut "source")
    """
    dates = {}
    iso = _parser_date_http(date_source)
    if iso:
        dates["updated"] = iso

    if contacts_source is None:
        contacts_source = resoudre_contacts([], producteur_nom)

    # S'assurer qu'il y a au moins media_source + media_metadata_page
    noms_media = {m.get("media_name") for m in medias}
    if "source-data" not in noms_media:
        medias.append(media_source(local_id, url_source,
                                   f"Jeu de données complet sur {metadata_source_label}"))
    if "source-metadata" not in noms_media:
        medias.append(media_metadata_page(local_id, url_fiche,
                                          f"Fiche de métadonnées du jeu de données source sur {metadata_source_label}"))

    return {
        "local_id": local_id,
        "resource_title": titre,
        "synopsis": [{"lang": "fr", "text": synopsis}],
        "summary": [{"lang": "fr", "text": description}],
        "theme": theme,
        "keywords": list(dict.fromkeys(keywords)),
        "producer": {"organization_name": producteur_nom},
        "contacts": contacts_source,
        "available_formats": medias,
        "dataset_dates": dates,
        "storage_status": "online",
        "access_condition": {
            "licence": LICENCE_ETALAB,
            "confidentiality": {"restricted_access": False, "gdpr_sensitive": False},
        },
        "geography": BBOX_RM,
        "metadata_info": {"metadata_source": url_fiche},
    }
