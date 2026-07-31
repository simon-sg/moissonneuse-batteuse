"""Serveur MCP (Model Context Protocol) exposant les métadonnées du nœud RUDI local.

Prototype étape 1 (métadonnées seules) : permet à un client MCP (Claude Desktop,
Claude Code...) d'interroger en langage naturel le catalogue RUDI — recherche de
jeux de données, détail d'une fiche, thèmes, producteurs. Aucune donnée (contenu
des fichiers) n'est téléchargée ni analysée ici : uniquement les métadonnées déjà
publiées sur le nœud (mêmes API que ``connectors/rudi_node.py``).

Lancement en standalone (transport stdio, adapté aux clients MCP type Claude
Desktop/Claude Code) :

    python3 src/mcp_rudi_server.py

Dépendance optionnelle : ``mcp`` (SDK officiel). Absente => échec explicite au
lancement, pas de dégradation silencieuse possible (un serveur MCP sans le SDK
n'a pas de sens).
"""

import unicodedata
from typing import Any

from mcp.server.fastmcp import FastMCP

from connectors.rudi_node import charger_conf_rudi
from rudi_node_write.connectors.rudi_node_auth import RudiNodeAuth
from rudi_node_write.rudi_node_writer import RudiNodeWriter

# Libellés FR des thèmes RUDI (voir CLAUDE.md, THEMES_RUDI dans translation/datagouv_to_rudi.py).
LIBELLES_THEMES = {
    "economy": "Economie",
    "citizenship": "Citoyenneté",
    "energyNetworks": "Réseaux, Energie",
    "culture": "Culture, Sports, Loisirs",
    "transportation": "Mobilité, Transport",
    "children": "Enfance",
    "environment": "Environnement",
    "townPlanning": "Urbanisme",
    "location": "Référentiels géographiques",
    "education": "Education",
    "publicSpace": "Espace public",
    "health": "Santé, Sécurité",
    "housing": "Logement",
    "society": "Social",
}

_LONGUEUR_MAX_SYNOPSIS = 400
_LONGUEUR_MAX_SUMMARY = 4000

mcp = FastMCP("rudi-metadonnees")
_writer: RudiNodeWriter | None = None


def _obtenir_writer() -> RudiNodeWriter:
    """Connecteur RUDI paresseux, réutilisé entre les appels d'outils (cache interne 60 s)."""
    global _writer
    if _writer is None:
        conf = charger_conf_rudi()
        if not conf:
            raise RuntimeError(
                "Config RUDI absente (src/conf/rudi_node.json). "
                "Impossible d'interroger le catalogue sans nœud configuré."
            )
        auth = RudiNodeAuth(usr=conf["usr"], pwd=conf["pwd"])
        _writer = RudiNodeWriter(pm_url=conf["url"] + "/manager", auth=auth)
    return _writer


def _normaliser(texte: str) -> str:
    """Minuscules + accents retirés, pour une recherche insensible à la casse/accents."""
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return sans_accents.lower()


def _texte_localise(champ: Any, langue: str = "fr") -> str:
    """RUDI stocke synopsis/summary comme une liste de ``{lang, text}``. Prend le FR, sinon le premier."""
    if not champ:
        return ""
    if isinstance(champ, str):
        return champ
    if isinstance(champ, list):
        for entree in champ:
            if entree.get("lang") == langue:
                return entree.get("text", "")
        return champ[0].get("text", "") if champ else ""
    return ""


def _texte_recherchable(meta: dict) -> str:
    morceaux = [
        meta.get("resource_title", ""),
        _texte_localise(meta.get("synopsis")),
        meta.get("theme", ""),
        (meta.get("producer") or {}).get("organization_name", ""),
        " ".join(meta.get("keywords") or []),
    ]
    return _normaliser(" ".join(morceaux))


def _resume_jeu_de_donnees(meta: dict) -> dict:
    synopsis = _texte_localise(meta.get("synopsis"))
    return {
        "global_id": meta.get("global_id"),
        "titre": meta.get("resource_title"),
        "theme": meta.get("theme"),
        "theme_libelle": LIBELLES_THEMES.get(meta.get("theme"), meta.get("theme")),
        "producteur": (meta.get("producer") or {}).get("organization_name"),
        "synopsis": synopsis[:_LONGUEUR_MAX_SYNOPSIS],
        "mots_cles": meta.get("keywords") or [],
        "nb_fichiers": len(meta.get("available_formats") or []),
        "derniere_maj": (meta.get("dataset_dates") or {}).get("updated"),
    }


@mcp.tool()
def rechercher_jeux_de_donnees(
    recherche: str = "",
    theme: str | None = None,
    producteur: str | None = None,
    limite: int = 15,
) -> list[dict]:
    """Recherche des jeux de données dans le catalogue RUDI de Rennes Métropole.

    Recherche en texte libre (titre, résumé, thème, producteur, mots-clés) et/ou
    filtre par thème RUDI exact (voir lister_themes) et/ou par producteur (sous-chaîne,
    insensible à la casse). Retourne une liste résumée (titre, thème, producteur,
    synopsis, nombre de fichiers) triée par pertinence — utiliser
    obtenir_jeu_de_donnees pour le détail complet d'une fiche.
    """
    writer = _obtenir_writer()
    termes = [t for t in _normaliser(recherche).split() if t]
    resultats = []
    for meta in writer.metadata_list:
        if theme and meta.get("theme") != theme:
            continue
        if producteur and _normaliser(producteur) not in _normaliser(
            (meta.get("producer") or {}).get("organization_name", "")
        ):
            continue
        if termes:
            texte = _texte_recherchable(meta)
            score = sum(1 for t in termes if t in texte)
            if score == 0:
                continue
        else:
            score = 0
        resultats.append((score, meta))
    resultats.sort(key=lambda paire: paire[0], reverse=True)
    return [_resume_jeu_de_donnees(meta) for _score, meta in resultats[:limite]]


@mcp.tool()
def obtenir_jeu_de_donnees(global_id: str) -> dict:
    """Détail complet d'un jeu de données RUDI par son identifiant (global_id, UUIDv4).

    Retourne titre, résumé complet, thème, producteur (+ contacts), mots-clés,
    couverture géographique, dates, et la liste des fichiers disponibles (nom,
    type, taille, URL de téléchargement quand elle est utilisable).
    """
    writer = _obtenir_writer()
    meta = writer.find_metadata_with_uuid(global_id)
    if not meta:
        return {"erreur": f"Aucun jeu de données trouvé pour global_id={global_id!r}"}
    fichiers = []
    for media in meta.get("available_formats") or []:
        connector = media.get("connector") or {}
        fichiers.append({
            "nom": media.get("media_name"),
            "type_media": media.get("media_type"),
            "type_fichier": media.get("file_type"),
            "taille_octets": media.get("file_size"),
            "url": connector.get("url"),
        })
    producer = meta.get("producer") or {}
    return {
        "global_id": meta.get("global_id"),
        "titre": meta.get("resource_title"),
        "synopsis": _texte_localise(meta.get("synopsis")),
        "resume": _texte_localise(meta.get("summary"))[:_LONGUEUR_MAX_SUMMARY],
        "theme": meta.get("theme"),
        "theme_libelle": LIBELLES_THEMES.get(meta.get("theme"), meta.get("theme")),
        "mots_cles": meta.get("keywords") or [],
        "producteur": producer.get("organization_name"),
        "contacts": [c.get("contact_name") for c in (meta.get("contacts") or [])],
        "geographie": meta.get("geography"),
        "dates": meta.get("dataset_dates"),
        "fichiers": fichiers,
    }


@mcp.tool()
def lister_themes() -> list[dict]:
    """Liste les thèmes RUDI utilisés dans le catalogue avec le nombre de jeux de données par thème."""
    writer = _obtenir_writer()
    compte: dict[str, int] = {}
    for meta in writer.metadata_list:
        theme = meta.get("theme")
        compte[theme] = compte.get(theme, 0) + 1
    return sorted(
        (
            {"theme": theme, "libelle": LIBELLES_THEMES.get(theme, theme), "nb_jeux_de_donnees": nb}
            for theme, nb in compte.items()
        ),
        key=lambda d: d["nb_jeux_de_donnees"],
        reverse=True,
    )


@mcp.tool()
def lister_producteurs() -> list[dict]:
    """Liste les organisations productrices du catalogue avec le nombre de jeux de données par producteur."""
    writer = _obtenir_writer()
    compte: dict[str, int] = {}
    for meta in writer.metadata_list:
        nom = (meta.get("producer") or {}).get("organization_name") or "(inconnu)"
        compte[nom] = compte.get(nom, 0) + 1
    return sorted(
        ({"producteur": nom, "nb_jeux_de_donnees": nb} for nom, nb in compte.items()),
        key=lambda d: d["nb_jeux_de_donnees"],
        reverse=True,
    )


@mcp.tool()
def statistiques_catalogue() -> dict:
    """Statistiques globales du catalogue RUDI : nombre total de jeux de données, répartition par thème,
    nombre de producteurs distincts."""
    writer = _obtenir_writer()
    themes = lister_themes()
    producteurs = lister_producteurs()
    return {
        "nb_jeux_de_donnees": writer.metadata_count,
        "nb_producteurs": len(producteurs),
        "repartition_themes": themes,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
