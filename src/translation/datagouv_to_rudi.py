import datetime
import os
import re
import uuid

from connectors.contacts import extraire_contacts_datagouv, resoudre_contacts
from translation.description_secours import (
    LIBELLES_THEMES, description_quasi_vide, entetes_depuis_geojson, generer_complement, resumer_court,
)
from translation.rudi_builder import construire_rudi_metadata

# Thèmes acceptés par le nœud RUDI (conformes aux catégories RUDI)
THEMES_RUDI = {
    "economy", "citizenship", "energyNetworks", "culture", "transportation",
    "children", "environment", "townPlanning", "location", "education",
    "publicSpace", "health", "housing", "society",
}

# Mots-clés pour détecter le thème RUDI depuis titre/description/tags (scoring)
_MOTS_CLES_THEME: list[tuple[str, list[str]]] = [
    ("children",       ["enfance", "enfant", "creche", "jeunesse", "petite enfance", "mineur", "periscolaire"]),
    ("education",      ["education", "scolaire", "ecole", "college", "lycee", "universite", "etudiant",
                        "parcoursup", "enseignement", "bts", "but", "scolarite", "etablissement scolaire",
                        "apprentissage", "formation professionnelle", "ips"]),
    ("health",         ["sante", "medecin", "hopital", "pharmacie", "accident", "mortalite", "handicap",
                        "soins", "maladie", "deces", "natalite", "medecine", "sanitaire"]),
    ("housing",        ["logement", "loyer", "habitat", "immobilier", "hlm", "residence", "hebergement",
                        "foncier", "copropriete", "locatif"]),
    ("transportation", ["transport", "mobilite", "trafic", "velo", "bus", "metro", "gare", "train",
                        "covoiturage", "carburant", "stationnement", "parking", "route", "voie",
                        "circulation", "autoroute", "navette"]),
    ("environment",    ["environnement", "energie", "eau", "pollution", "dechet", "climat", "nature",
                        "biodiversite", "nappe", "phytosanitaire", "pesticide", "sol", "air", "emission",
                        "carbone", "nucleaire", "dechets", "consommation energetique", "gaz a effet",
                        "contaminant", "qualite de l eau", "qualite de l air"]),
    ("energyNetworks", ["reseau electrique", "reseau de gaz", "reseau d eau", "fibre optique",
                        "telecommunication", "infrastructure reseau", "distribution d energie",
                        "raccordement", "electrique", "gazier"]),
    ("townPlanning",   ["urbanisme", "cadastre", "permis de construire", "construction", "batiment",
                        "plan local", "plu", "amenagement", "zone d activite", "zone urbaine", "foncier"]),
    ("economy",        ["economie", "emploi", "entreprise", "commerce", "prix", "fiscal", "impot",
                        "budget", "siren", "siret", "etablissement", "marche", "salaire", "revenu fiscal",
                        "inflation", "chiffre d affaires", "taxe"]),
    ("citizenship",    ["election", "vote", "citoyen", "democratie", "collectivite", "acces public",
                        "information publique", "service public", "droit", "participation"]),
    ("culture",        ["culture", "sport", "loisir", "patrimoine", "musee", "bibliotheque", "festival",
                        "art", "oeuvre", "archives", "spectacle", "cinema", "theatre"]),
    ("publicSpace",    ["espace public", "trottoir", "eclairage", "mobilier urbain", "proprete",
                        "equipement public", "voirie", "amenagement urbain"]),
    ("society",        ["social", "pauvrete", "insertion", "famille", "population", "demographie",
                        "revenu", "precarite", "aide sociale", "minima sociaux", "allocataire"]),
    ("location",       ["referentiel geographique", "adresse postale", "coordonnee", "cadastral",
                        "limite administrative", "decoupage", "zonage"]),
]


def _detecter_theme(metadata_source: dict) -> str:
    """Devine le thème RUDI par scoring sur titre, description et tags."""
    import unicodedata

    def normaliser(s: str) -> str:
        s = unicodedata.normalize("NFD", s.lower())
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        return re.sub(r"[-_]", " ", s)

    tags = [t.get("name", t) if isinstance(t, dict) else t for t in metadata_source.get("tags", [])]
    corpus = normaliser(" ".join([
        metadata_source.get("title", ""),
        metadata_source.get("description", "") or "",
        metadata_source.get("description_short", "") or "",
        " ".join(tags),
    ]))

    scores: dict[str, int] = {}
    for theme, mots in _MOTS_CLES_THEME:
        score = sum(corpus.count(mot) for mot in mots if mot in corpus)
        if score:
            scores[theme] = score

    return max(scores, key=scores.__getitem__) if scores else "society"

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

def _local_id_depuis_dataset_id(dataset_id: str) -> str:
    """Génère un local_id stable à partir de l'identifiant data.gouv.fr."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://www.data.gouv.fr/datasets/{dataset_id}"))


def _trouver_ressource_principale(metadata_source: dict) -> dict | None:
    """Retourne la meilleure ressource téléchargeable : CSV d'abord, JSON ensuite.

    `.get(clé, "")` ne suffit pas : une ressource data.gouv.fr peut porter `format`
    ou `title` à `null` (clé présente, valeur absente) plutôt qu'omettre la clé —
    le défaut de .get() ne s'applique alors pas et .lower() plante sur None."""
    resources = metadata_source.get("resources", [])
    for r in resources:
        if r is None:
            continue
        fmt = (r.get("format") or "").lower()
        titre = (r.get("title") or "").lower()
        if fmt == "csv" and ".zip" not in titre and ".gz" not in titre:
            return r
    for r in resources:
        if r is None:
            continue
        fmt = (r.get("format") or "").lower()
        titre = (r.get("title") or "").lower()
        if fmt == "json" and "geo" not in titre:
            return r
    return None


def traduire_metadonnees(metadata_source: dict, zone: str = "Rennes Métropole",
                          dossier_nom: str = "",
                          fichiers_filtres: list | None = None,
                          fichiers_dicts: list | None = None,
                          fichiers_pdfs: list | None = None,
                          theme: str | None = None,
                          entetes_colonnes: list[str] | None = None) -> dict:
    """
    Traduit les métadonnées data.gouv.fr au format RUDI.

    fichiers_filtres : [(nom_fichier, nb_rm, ressource_originale), ...] ou None
    fichiers_dicts   : [(nom_fichier, ressource_originale), ...] ou None
    fichiers_pdfs    : [(nom_fichier, ressource_originale), ...] ou None
    dossier_nom      : slug pour nommer le fichier filtré par défaut (ex: "prix-carburants")
    theme            : thème RUDI (voir THEMES_RUDI) ; auto-détecté si absent
    entetes_colonnes : colonnes du fichier filtré (si connues) — utilisées pour compléter
                       la description quand la source data.gouv.fr n'en fournit pas
    """
    if theme is None:
        theme = _detecter_theme(metadata_source)
    elif theme not in THEMES_RUDI:
        raise ValueError(f"Thème RUDI invalide : {theme!r}. Valeurs acceptées : {sorted(THEMES_RUDI)}")
    dataset_id = metadata_source["id"]
    titre_original = metadata_source["title"]
    titre_localise = f"{titre_original} - {zone}"

    # `.get("organization", {})` ne suffit pas : l'API data.gouv.fr renvoie la clé
    # présente avec la valeur `null` (pas absente) pour un JDD publié par un compte
    # individuel sans organisation — le défaut de .get() ne s'applique alors pas.
    org = metadata_source.get("organization") or {}
    producer = {
        "organization_name": org.get("name", "Producteur inconnu"),
    }
    tags = [t.get("name", t) if isinstance(t, dict) else t for t in metadata_source.get("tags", [])]

    description_originale = metadata_source.get("description", "")
    description_localisee = (
        f"Version localisée sur {zone}. "
        f"Données filtrées pour ne conserver que les enregistrements des communes de {zone}.\n\n"
        f"Jeu de données source (France entière) : https://www.data.gouv.fr/datasets/{dataset_id}\n\n"
        + description_originale
    )
    if description_quasi_vide(description_originale):
        description_localisee += generer_complement(
            theme=theme, producteur=producer["organization_name"], zone=zone,
            colonnes=entetes_colonnes, mots_cles=tags,
        )

    libelle_theme = LIBELLES_THEMES.get(theme, theme)
    synopsis = resumer_court(
        description_originale,
        repli=f"Jeu de données « {libelle_theme} » de {producer['organization_name']}, filtré sur {zone}.",
    )

    url_source = f"https://www.data.gouv.fr/datasets/{dataset_id}"
    ressource_principale = _trouver_ressource_principale(metadata_source)

    # media_id_source déterministe : même dataset = même ID à chaque run
    media_id_source = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_source}/source"))

    # Entrées pour les fichiers filtrés (une par ressource sauvegardée)
    if not fichiers_filtres:
        slug = dossier_nom or dataset_id[:30]
        fmt_filtre = "CSV" if (ressource_principale and (ressource_principale.get("format") or "").lower() == "csv") else "JSON"
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

    medias_pdfs = []
    for nom_fichier, ressource_orig in (fichiers_pdfs or []):
        media_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_source}/doc/{nom_fichier}"))
        caption = ressource_orig.get("title", nom_fichier) if ressource_orig else nom_fichier
        medias_pdfs.append({
            "media_id": media_id,
            "media_type": "FILE",
            "media_name": nom_fichier,
            "media_caption": f"Documentation — {caption}",
            "connector": {
                "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
                "interface_contract": "dwnl",
            },
        })

    media_metadata_page = {
        "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_source}/metadata-page")),
        "media_type": "SERVICE",
        "media_name": "source-metadata",
        "media_caption": "Fiche de métadonnées du jeu de données source sur data.gouv.fr",
        "connector": {
            "url": url_source,
            "interface_contract": "dwnl",
        },
    }
    available_formats = medias_filtres + medias_dicts + medias_pdfs + [
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
        media_metadata_page,
    ]

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

    contacts_source = extraire_contacts_datagouv(metadata_source)
    contacts = resoudre_contacts(contacts_source, producer["organization_name"])

    return construire_rudi_metadata(
        local_id=_local_id_depuis_dataset_id(dataset_id),
        titre=titre_localise,
        synopsis=synopsis,
        description=description_localisee,
        theme=theme,
        keywords=keywords,
        producteur_nom=producer["organization_name"],
        url_source=url_source,
        url_fiche=url_source,
        medias=available_formats,
        contacts_source=contacts,
        licence=licence_rudi,
        dates=dates,
        metadata_dates=dates,
        ajouter_medias_source=False,  # entrées "source-data-gouv"/"source-metadata" déjà construites
        source_producteur="data.gouv.fr",
        page_producteur=org.get("page"),
    )


# default_crs ne sert pas qu'à la couche WMS/WFS elle-même : le composant carte du portail
# (app-map-tab) l'utilise aussi comme PROJECTION DE LA CARTE ENTIÈRE (viewProjectionString),
# y compris le fond de plan (basemap), dès que la valeur n'est pas null (sinon repli sur
# EPSG:3857 par défaut). EPSG:3857 (Web Mercator) est la projection native du fond de plan et de
# la quasi-totalité des serveurs WMS/WFS modernes — l'utiliser évite toute déformation visuelle
# et toute reprojection.
_DEFAULT_CRS_CARTE = "EPSG:3857"


def _connector_parameters_wms(couches_rm: list[str]) -> list[dict]:
    """connector_parameters pour un connecteur SERVICE WMS : ces valeurs sont réellement lues
    par le portail (createWmsDataLayer) pour construire la requête GetMap (LAYERS/VERSION)."""
    layer = ",".join(couches_rm) if couches_rm else "n/a"
    return [
        {"key": "versions", "value": "1.3.0"},
        {"key": "layer", "value": layer},
        {"key": "default_crs", "value": _DEFAULT_CRS_CARTE},
        {"key": "formats", "value": "image/png"},
    ]


def _connector_parameters_wfs(typename: str | None) -> list[dict]:
    """connector_parameters pour un connecteur SERVICE WFS/OGC API : "layer"/"formats"/"versions"
    sont réellement lus par le portail (createWfsDataLayer) pour construire la requête GetFeature."""
    return [
        {"key": "versions", "value": "2.0.0"},
        {"key": "layer", "value": typename or "n/a"},
        {"key": "default_crs", "value": _DEFAULT_CRS_CARTE},
        {"key": "formats", "value": "application/json"},
    ]


def traduire_metadonnees_service(config: dict,
                                  fichiers_geojson: list | None = None,
                                  wms_service: dict | None = None,
                                  metadata_urls: list | None = None,
                                  contacts_source: list[dict] | None = None) -> dict:
    """
    Traduit un service géographique (WFS, WMS, OGC API) au format RUDI.

    config          : entrée de DATASETS_GEO (id, type, url, titre, producteur, theme, ...)
    fichiers_geojson: [(chemin_fichier, typename), ...] pour WFS/OGC
    wms_service     : dict lu depuis wms_service.json, pour WMS
    metadata_urls   : URLs de fiche metadata du producteur (extraites des MetadataURL WMS)
    contacts_source : contacts extraits du service ([{"contact_name": ..., "email": ...}, ...])
    """
    service_id = config["id"]
    service_type = config.get("type", "wfs")
    theme = config.get("theme", "environment")
    if theme not in THEMES_RUDI:
        raise ValueError(f"Thème RUDI invalide : {theme!r}. Valeurs acceptées : {sorted(THEMES_RUDI)}")

    local_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"geo:{service_id}"))
    titre = config.get("titre") or service_id
    producteur_nom = config.get("producteur", "Producteur inconnu")
    url_service = config["url"]

    couches_rm = []
    if wms_service:
        couches_rm = [c.get("nom", "") for c in wms_service.get("couches", [])]

    colonnes = []
    if service_type in ("wfs", "ogcapi", "geojson") and fichiers_geojson:
        colonnes = entetes_depuis_geojson(fichiers_geojson[0][0])

    libelle_theme = LIBELLES_THEMES.get(theme, theme)
    synopsis = (
        f"Service {service_type.upper()} « {libelle_theme} » de {producteur_nom}, "
        f"filtré sur Rennes Métropole."
    )[:150]
    description = (
        f"Service {service_type.upper()} filtré sur Rennes Métropole (43 communes, EPCI 243500139).\n\n"
        f"Source : {url_service}\n\n"
        + generer_complement(theme=theme, producteur=producteur_nom,
                              colonnes=colonnes, couches=couches_rm)
    )

    available_formats = []

    if service_type in ("wfs", "ogcapi", "geojson") and fichiers_geojson:
        for chemin, typename in fichiers_geojson:
            nom_fichier = os.path.basename(chemin)
            media_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"geo:{service_id}:file:{nom_fichier}"))
            available_formats.append({
                "media_id": media_id,
                "media_type": "FILE",
                "media_name": nom_fichier,
                "media_caption": f"{typename} — GeoJSON filtré Rennes Métropole",
                "connector": {
                    "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
                    "interface_contract": "dwnl",
                },
            })

    # Entrée SERVICE vers l'endpoint/fichier source (France entière / non filtré)
    sep = "&" if "?" in url_service else "?"
    if service_type == "wms":
        caps_url = f"{url_service}{sep}SERVICE=WMS&REQUEST=GetCapabilities"
        contract = "wms"
    elif service_type == "ogcapi":
        caps_url = url_service.rstrip("/") + "/collections"
        contract = "wfs"
    elif service_type == "geojson":
        # Fichier GeoJSON statique : pas de service OGC, l'URL source EST le lien de téléchargement complet
        caps_url = url_service
        contract = "dwnl"
    else:
        caps_url = f"{url_service}{sep}SERVICE=WFS&REQUEST=GetCapabilities"
        contract = "wfs"

    media_id_service = str(uuid.uuid5(uuid.NAMESPACE_URL, f"geo:{service_id}:service"))
    caption_service = (
        "Jeu de données complet (non filtré)" if service_type == "geojson"
        else f"Service {service_type.upper()} source"
    )
    # URL de la fiche metadata du producteur (ex: GeoNetwork, cartes.gouv.fr)
    # Priorité : metadata_urls extraites des capabilities WMS > URL du service
    url_metadata = config["url"]
    if metadata_urls:
        url_metadata = metadata_urls[0]

    media_metadata_page = {
        "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"geo:{service_id}:metadata-page")),
        "media_type": "SERVICE",
        "media_name": "source-metadata",
        "media_caption": "Fiche de métadonnées du service source",
        "connector": {
            "url": url_metadata,
            "interface_contract": "dwnl",
        },
    }
    if contract == "wms":
        connector_parameters = _connector_parameters_wms(couches_rm)
    elif contract == "wfs":
        premier_typename = fichiers_geojson[0][1] if fichiers_geojson else None
        connector_parameters = _connector_parameters_wfs(premier_typename)
    # contrat "dwnl" (geojson statique) : pas de connector_parameters (contrat non validable)

    connecteur_service = {
        "url": caps_url,
        "interface_contract": contract,
    }
    if contract in ("wms", "wfs"):
        connecteur_service["connector_parameters"] = connector_parameters

    available_formats.append({
        "media_id": media_id_service,
        "media_type": "SERVICE",
        "media_name": f"service-{service_type}",
        "media_caption": caption_service,
        "connector": connecteur_service,
    })
    available_formats.append(media_metadata_page)

    keywords = ["rennes métropole", "géographique", service_type]
    if couches_rm:
        keywords += couches_rm[:5]

    contacts = resoudre_contacts(contacts_source or [], producteur_nom)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dates = {"created": now, "updated": now}

    return construire_rudi_metadata(
        local_id=local_id,
        titre=f"{titre} — Rennes Métropole",
        synopsis=synopsis,
        description=description,
        theme=theme,
        keywords=keywords,
        producteur_nom=producteur_nom,
        url_source=url_service,
        url_fiche=url_service,
        medias=available_formats,
        contacts_source=contacts,
        dates=dates,
        metadata_dates=dates,
        ajouter_medias_source=False,  # entrées "service-<type>"/"source-metadata" déjà construites
        source_producteur=url_service.split("/")[2] if "/" in url_service else None,
    )
