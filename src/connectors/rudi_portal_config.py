"""Lecture/écriture de la configuration visuelle du portail RUDI local.

Fichiers gérés (dans config/konsult/ du déploiement rudi-out-of-the-box) :
  - customization.json      : logos, hero, textes sections, footer
  - konsult-front-office.json : carte, liens docs/contact
  - konsult.properties      : teamName, projectName
  - style-override.css      : CSS override généré (variables --primary-color etc.)
"""

import json
import os
import re

from connectors.rudi_portal import _DOCKER_DIR

_CHEMIN_KONSULT = os.path.join(_DOCKER_DIR, "config", "konsult")

# --- Variables CSS overridables (extraites depuis l'image Angular v3.3.12) ---

VARIABLES_COULEURS = {
    "primary-color": {"label": "Bleu principal", "defaut": "#004680"},
    "rudi-header-primary-color": {"label": "Bleu header", "defaut": "#002748"},
    "accent-color": {"label": "Accent (corail)", "defaut": "#f36b43"},
    "accent-color-svg": {"label": "Accent SVG", "defaut": "#ff8d6d"},
    "primary-text": {"label": "Texte principal", "defaut": "#323643"},
    "primary-text-light": {"label": "Texte secondaire", "defaut": "#71757e"},
    "banner-solid-color": {"label": "Fond sections", "defaut": "#e7e9ed"},
    "error-color": {"label": "Erreur", "defaut": "#d04838"},
    "success-color": {"label": "Succès", "defaut": "#498100"},
    "focus": {"label": "Focus ring", "defaut": "#0270e7"},
    "secondary-color": {"label": "Secondaire (blanc)", "defaut": "#ffffff"},
    "self-data-color": {"label": "Self-data fond", "defaut": "#e1eefc"},
    "accent-color-svg-self-data": {"label": "Self-data accent", "defaut": "#259da5"},
}


def _lire_json(nom_fichier: str) -> dict:
    chemin = os.path.join(_CHEMIN_KONSULT, nom_fichier)
    if not os.path.isfile(chemin):
        return {}
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def _ecrire_json(nom_fichier: str, donnees: dict) -> None:
    chemin = os.path.join(_CHEMIN_KONSULT, nom_fichier)
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent="\t")
        f.write("\n")


def _lire_properties(nom_fichier: str) -> dict[str, str]:
    chemin = os.path.join(_CHEMIN_KONSULT, nom_fichier)
    props = {}
    if not os.path.isfile(chemin):
        return props
    with open(chemin, encoding="utf-8") as f:
        for ligne in f:
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                cle, _, val = ligne.partition("=")
                props[cle.strip()] = val.strip()
    return props


def _ecrire_properties(nom_fichier: str, props: dict[str, str]) -> None:
    chemin = os.path.join(_CHEMIN_KONSULT, nom_fichier)
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        for cle, val in props.items():
            f.write(f"{cle}={val}\n")


# --- Lecture de la config complète ---

def _extraire_texte(obj, locale="fr_FR") -> str:
    """Extrait le texte FR depuis un champ i18n [{locale, text}] ou une string."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item.get("locale") == locale:
                return item.get("text", "")
    return ""


def _texte_en_i18n(texte: str, locale="fr_FR") -> list[dict]:
    """Convertit un texte brut en format i18n."""
    return [{"locale": locale, "text": texte}]


def lire_config() -> dict:
    """Lit les 3 fichiers de config et retourne un dict unifié pour l'API."""
    customization = _lire_json("customization.json")
    front_office = _lire_json("konsult-front-office.json")
    props = _lire_properties("konsult.properties")

    hero = customization.get("heroDescription", {})
    projects = customization.get("projectsDescription", {})
    features = customization.get("cmsProjectValuesDescription", {})
    footer = customization.get("footerDescription", {})
    footer_logo = footer.get("footerLogo", {})
    social = footer.get("socialNetworks", [])
    map_info = front_office.get("mapInfo", {})
    front = front_office.get("front", {})

    return {
        "identite": {
            "projectName": props.get("front.projectName", ""),
            "teamName": props.get("front.teamName", ""),
        },
        "textes": {
            "hero_titre1": _extraire_texte(hero.get("titles1")),
            "hero_titre2": _extraire_texte(hero.get("titles2")),
            "projects_titre1": _extraire_texte(projects.get("titles1")),
            "projects_titre2": _extraire_texte(projects.get("titles2")),
            "projects_soustitre": _extraire_texte(projects.get("subtitles")),
            "projects_description": _extraire_texte(projects.get("descriptions")),
            "features_titre1": _extraire_texte(features.get("titles1")),
            "features_titre2": _extraire_texte(features.get("titles2")),
            "features_description": _extraire_texte(features.get("descriptions")),
        },
        "liens": {
            "contact": front.get("contact", ""),
            "docRudi": front.get("docRudi", ""),
            "apiDocumentation": front.get("apiDocumentation", ""),
            "footer_url": footer_logo.get("url", ""),
        },
        "reseaux_sociaux": [
            {"label": s.get("label", ""), "url": s.get("url", "")}
            for s in social
        ],
        "carte": {
            "center_lon": map_info.get("defaultCenter", [0, 0])[0],
            "center_lat": map_info.get("defaultCenter", [0, 0])[1],
            "zoom": map_info.get("defaultZoom", 13),
        },
        "images": {
            "mainLogo": customization.get("mainLogo", ""),
            "mainLogoAltText": customization.get("mainLogoAltText", ""),
            "heroLeftImage": hero.get("leftImage", ""),
            "heroRightImage": hero.get("rightImage", ""),
            "footerLogo": footer_logo.get("logo", ""),
            "footerLogoAltText": footer_logo.get("logoAltText", ""),
            "footerLogoUrl": footer_logo.get("url", ""),
        },
        "couleurs_lues": _lire_couleurs_css(),
    }


# --- Écriture ---

def sauvegarder_config(config: dict) -> None:
    """Écrit les 3 fichiers de config + génère le CSS override."""
    identite = config.get("identite", {})
    textes = config.get("textes", {})
    liens = config.get("liens", {})
    social = config.get("reseaux_sociaux", [])
    carte = config.get("carte", {})
    images = config.get("images", {})
    couleurs = config.get("couleurs", {})

    nom = identite.get("projectName", "RUDI")

    # --- customization.json ---
    customization = {
        "overrideCssFile": "/style-override.css",
        "mainLogo": images.get("mainLogo", ""),
        "mainLogoAltText": images.get("mainLogoAltText", ""),
        "heroDescription": {
            "leftImage": images.get("heroLeftImage", ""),
            "rightImage": images.get("heroRightImage", ""),
            "titles1": _texte_en_i18n(textes.get("hero_titre1", "")),
            "titles2": _texte_en_i18n(textes.get("hero_titre2", nom)),
        },
        "projectsDescription": {
            "titles1": _texte_en_i18n(textes.get("projects_titre1", "")),
            "titles2": _texte_en_i18n(textes.get("projects_titre2", "")),
            "subtitles": _texte_en_i18n(textes.get("projects_soustitre", "")),
            "descriptions": _texte_en_i18n(textes.get("projects_description", "")),
        },
        "keyFiguresDescription": _lire_json("customization.json").get(
            "keyFiguresDescription", {}
        ),
        "cmsNewsDescription": _lire_json("customization.json").get(
            "cmsNewsDescription", {}
        ),
        "cmsTermsDescription": _lire_json("customization.json").get(
            "cmsTermsDescription", {}
        ),
        "cmsProjectValuesDescription": {
            "titles1": _texte_en_i18n(textes.get("features_titre1", "")),
            "titles2": _texte_en_i18n(textes.get("features_titre2", nom)),
            "descriptions": _texte_en_i18n(textes.get("features_description", "")),
            **_lire_json("customization.json").get(
                "cmsProjectValuesDescription", {}
            ),
        },
        "footerDescription": {
            "footerLogo": {
                "logo": images.get("footerLogo", ""),
                "url": liens.get("footer_url", ""),
                "logoAltText": images.get("footerLogoAltText", ""),
            },
            "socialNetworks": [
                {"label": s["label"], "url": s["url"],
                 "icon": _icone_reseau(s["label"])}
                for s in social if s.get("label") and s.get("url")
            ],
        },
        "newsPageDescription": _lire_json("customization.json").get(
            "newsPageDescription", {}
        ),
    }
    _ecrire_json("customization.json", customization)

    # --- konsult-front-office.json ---
    center_lon = float(carte.get("center_lon", -1.662712))
    center_lat = float(carte.get("center_lat", 48.114767))
    zoom = int(carte.get("zoom", 13))
    front_office = {
        "front": {
            "contact": liens.get("contact", ""),
            "apiDocumentation": liens.get("apiDocumentation", ""),
            "apiDocumentationService": liens.get("apiDocumentation", ""),
            "docRudi": liens.get("docRudi", ""),
            "tableDisplayMaxFileSize": "1000000000",
            "datasetHelpLinks": _lire_json("konsult-front-office.json")
                .get("front", {}).get("datasetHelpLinks", []),
        },
        "mapInfo": {
            "defaultCenter": [center_lon, center_lat],
            "defaultTopLeft": [center_lon - 0.05, center_lat + 0.03],
            "defaultBottomRight": [center_lon + 0.05, center_lat - 0.03],
            "paddingExtent": 40,
            "maxZoomExtent": 11,
            "defaultZoom": zoom,
        },
        "scripts": _lire_json("konsult-front-office.json").get("scripts", []),
    }
    _ecrire_json("konsult-front-office.json", front_office)

    # --- konsult.properties ---
    props = _lire_properties("konsult.properties")
    props["front.projectName"] = identite.get("projectName", "Roob")
    props["front.teamName"] = identite.get("teamName", "Roob")
    _ecrire_properties("konsult.properties", props)

    # --- style-override.css ---
    generer_css_override(couleurs)


def _icone_reseau(label: str) -> str:
    """Retourne le chemin d'icône pour un réseau social connu."""
    icones = {
        "linkedin": "/assets/footer/linkedin.svg",
        "github": "/assets/footer/github.svg",
        "twitter": "/assets/footer/twitter.svg",
        "mastodon": "/assets/footer/mastodon.svg",
    }
    return icones.get(label.lower(), "/assets/footer/github.svg")


# --- CSS override ---

def _lire_couleurs_css() -> dict[str, str]:
    """Lit les couleurs actuelles depuis le style-override.css existant."""
    chemin = os.path.join(_CHEMIN_KONSULT, "style-override.css")
    couleurs = {}
    if not os.path.isfile(chemin):
        return couleurs
    with open(chemin, encoding="utf-8") as f:
        for ligne in f:
            match = re.match(r"\s*--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;", ligne)
            if match:
                couleurs[match.group(1)] = match.group(2)
    return couleurs


def generer_css_override(couleurs: dict[str, str]) -> None:
    """Génère le style-override.css avec les couleurs spécifiées."""
    chemin = os.path.join(_CHEMIN_KONSULT, "style-override.css")
    os.makedirs(os.path.dirname(chemin), exist_ok=True)

    lignes = ["/* Généré par moissonneuse-batteuse — ne pas éditer à la main */"]
    lignes.append(":root {")
    for cle, defaut in VARIABLES_COULEURS.items():
        valeur = couleurs.get(cle, defaut["defaut"])
        if valeur:
            lignes.append(f"  --{cle}: {valeur};")
    lignes.append("}")
    lignes.append("")

    with open(chemin, "w", encoding="utf-8") as f:
        f.write("\n".join(lignes))


# --- Upload d'images ---

def sauvegarder_image(nom_fichier: str, donnees: bytes) -> str:
    """Sauvegarde une image uploadée dans config/konsult/.

    Retourne le chemin relatif à servir dans le JSON (ex: /mon-logo.png).
    """
    # Sécuriser le nom de fichier
    nom = os.path.basename(nom_fichier)
    nom = re.sub(r"[^a-zA-Z0-9._-]", "_", nom)
    chemin = os.path.join(_CHEMIN_KONSULT, nom)
    with open(chemin, "wb") as f:
        f.write(donnees)
    return f"/{nom}"


def lister_images() -> list[str]:
    """Liste les fichiers image disponibles dans config/konsult/."""
    if not os.path.isdir(_CHEMIN_KONSULT):
        return []
    exts = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
    return sorted(
        f for f in os.listdir(_CHEMIN_KONSULT)
        if os.path.splitext(f)[1].lower() in exts
    )
