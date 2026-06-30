"""
Génère un complément de description factuel quand la source ne fournit aucun texte
exploitable (description data.gouv.fr vide, ou publication INSEE/service géo qui n'a
jamais de description en entrée).

Construit 1 à 3 phrases à partir des seules informations disponibles par ailleurs
(colonnes du fichier filtré, propriétés GeoJSON, thème RUDI, producteur, mots-clés).
N'invente jamais le contenu sémantique du JDD : se limite à des faits vérifiables.
"""
import csv
import io
import json
import re

# En dessous de ce nombre de caractères, une description source est considérée
# vide ou quasi vide (ex: une seule phrase passe-partout).
SEUIL_CARACTERES = 40

# Repère la fin du préambule standard ("Version localisée...", "Source : URL", etc.) commun
# aux 3 traducteurs, pour isoler la partie réellement descriptive d'un résumé RUDI.
_RE_PREAMBULE = re.compile(r"(?:Jeu de données source[^\n]*|Source\s*:\s*\S+)\n*(.*)", re.S)


def partie_descriptive(texte_summary: str) -> str:
    """Retourne la partie d'un `summary` RUDI qui suit le préambule de localisation
    standard (intro + lien source). Si aucun préambule n'est détecté, retourne le texte tel quel."""
    m = _RE_PREAMBULE.search(texte_summary or "")
    return (m.group(1) if m else (texte_summary or "")).strip()

# Marqueur en tête du complément généré : permet aux scripts de rattrapage de
# détecter un complément déjà injecté et d'éviter de le dupliquer.
MARQUEUR = "Jeu de données du thème"

LIBELLES_THEMES = {
    "economy": "économie", "citizenship": "citoyenneté", "energyNetworks": "réseaux, énergie",
    "culture": "culture, sports, loisirs", "transportation": "mobilité, transport",
    "children": "enfance", "environment": "environnement", "townPlanning": "urbanisme",
    "location": "référentiels géographiques", "education": "éducation",
    "publicSpace": "espace public", "health": "santé, sécurité",
    "housing": "logement", "society": "social",
}


def description_quasi_vide(texte: str | None) -> bool:
    """True si le texte source ne contient pas assez de matière pour être une vraie description."""
    return len((texte or "").strip()) < SEUIL_CARACTERES


def entetes_depuis_csv(contenu: bytes | str, max_colonnes: int = 20) -> list[str]:
    """Extrait les noms de colonnes (première ligne) d'un contenu CSV."""
    if isinstance(contenu, bytes):
        texte = contenu.decode("utf-8-sig", errors="replace")
        if texte.count("�") > 10:
            texte = contenu.decode("latin-1", errors="replace")
    else:
        texte = contenu
    premiere = texte.splitlines()[0] if texte else ""
    if not premiere:
        return []
    delim = max((";", ",", "\t", "|"), key=lambda d: premiere.count(d))
    try:
        entetes = next(csv.reader(io.StringIO(premiere), delimiter=delim))
    except (csv.Error, StopIteration):
        return []
    return [e.strip() for e in entetes if e.strip()][:max_colonnes]


def entetes_depuis_geojson(chemin: str, max_colonnes: int = 20) -> list[str]:
    """Extrait les clés de `properties` de la première feature d'un GeoJSON."""
    try:
        with open(chemin, encoding="utf-8") as f:
            data = json.load(f)
        features = data.get("features") or []
        if not features:
            return []
        return list((features[0].get("properties") or {}).keys())[:max_colonnes]
    except (OSError, ValueError, AttributeError):
        return []


def generer_complement(*, theme: str, producteur: str, zone: str = "Rennes Métropole",
                        colonnes: list[str] | None = None,
                        couches: list[str] | None = None,
                        mots_cles: list[str] | None = None) -> str:
    """Construit 1 à 3 phrases factuelles décrivant le JDD à partir des infos disponibles.

    N'invente rien : se limite au thème, au producteur, à la zone, et aux noms de
    colonnes/couches/mots-clés réellement présents dans les métadonnées ou les données.
    """
    libelle_theme = LIBELLES_THEMES.get(theme, theme)
    phrases = [
        f"{MARQUEUR} « {libelle_theme} », produit par {producteur}, "
        f"limité aux communes de {zone}."
    ]

    if colonnes:
        if len(colonnes) <= 8:
            liste = ", ".join(colonnes)
        else:
            liste = ", ".join(colonnes[:8]) + f" (+{len(colonnes) - 8} autres champs)"
        phrases.append(f"Les enregistrements comportent les champs suivants : {liste}.")
    elif couches:
        liste = ", ".join(couches[:8])
        phrases.append(f"Couche(s) géographique(s) disponible(s) : {liste}.")
    elif mots_cles:
        zone_normalisee = zone.lower()
        mc = [m for m in mots_cles if m and m.lower() not in {zone_normalisee, "rennes métropole"}][:6]
        if mc:
            phrases.append(f"Mots-clés associés : {', '.join(mc)}.")

    return " ".join(phrases)
