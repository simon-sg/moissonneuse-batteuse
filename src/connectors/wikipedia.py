"""
Recherche de résumés Wikipédia/Wikidata pour les organisations productrices.

Utilise l'API REST de Wikipédia FR pour obtenir une description courte (CC0,
issue de Wikidata) et un extract plus long (CC BY-SA).

Usage :
    from connectors.wikipedia import resumer_wikipedia
    res = resumer_wikipedia("Insee")
    # {"caption": "Institut national de la statistique...", "summary": "...", "url": "..."}
"""
import hashlib
import json
import os
import re
import time
import unicodedata
from urllib.parse import quote

from connectors.http import session

_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "cache", "wikipedia",
)
_CACHE_TTL_SECS = 30 * 24 * 3600  # 30 jours

_WIKI_API = "https://fr.wikipedia.org/api/rest_v1"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_WIKIMEDIA_COMMONS_FICHIER = "https://commons.wikimedia.org/wiki/Special:FilePath/"
_TIMEOUT = 15
# Taille max acceptée par le champ image du portail Strukture (10 Mo) — on ne télécharge
# jamais un logo plus gros que ce qu'on pourra téléverser.
_TAILLE_MAX_LOGO = 10 * 1024 * 1024
# Types que le portail accepte pour l'image d'organisation (jpeg/png). Wikimedia peut
# rendre un SVG en PNG via « Special:FilePath?width=… », mais pas un GIF/WebP : filtrés ici.
_TYPES_IMAGE_ACCEPTES = ("image/jpeg", "image/png")


def _normaliser_cle(nom: str) -> str:
    """Clé de cache : minuscule, sans accents, espaces normalisés."""
    nfkd = unicodedata.normalize("NFKD", nom.lower().strip())
    sans_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sans_accents)


def _chemin_cache(cle: str) -> str:
    h = hashlib.md5(cle.encode()).hexdigest()
    return os.path.join(_CACHE_DIR, f"wiki_{h}.json")


def _lire_cache(cle: str) -> dict | None:
    chemin = _chemin_cache(cle)
    if not os.path.exists(chemin):
        return None
    age = time.time() - os.path.getmtime(chemin)
    if age > _CACHE_TTL_SECS:
        return None
    try:
        with open(chemin, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    # Entrée positive écrite avant l'ajout du site officiel (P856) puis du logo (P154) :
    # format obsolète, on la traite comme un cache *miss* pour forcer un nouvel appel. Le
    # cache négatif (dict vide, « aucune page Wikipédia ») reste valide, lui.
    if data and ("site_officiel" not in data or "logo" not in data):
        return None
    return data


def _ecrire_cache(cle: str, data: dict) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    chemin = _chemin_cache(cle)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _rechercher_titre(nom: str) -> str | None:
    """Recherche le meilleur titre Wikipédia FR pour un nom d'organisation.

    Stratégie : opensearch → premier résultat standard (pas de désambiguïsation).
    """
    try:
        r = session.get(
            f"{_WIKI_API}/page/summary/{nom}",
            timeout=_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("type") == "standard" and data.get("extract"):
                return data.get("title")
    except Exception:
        pass

    try:
        r = session.get(
            "https://fr.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": nom,
                "limit": 5,
                "namespace": 0,
                "format": "json",
            },
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            _, titres, _, _ = r.json()
            for titre in titres:
                if titre:
                    return titre
    except Exception:
        pass

    return None


def _site_officiel(qid: str) -> str:
    """Récupère le site officiel (propriété Wikidata P856) d'une entité, ou "".

    P856 peut porter plusieurs valeurs (versions linguistiques). On prend la
    première URL http(s) exploitable, sans préférence de langue : le champ
    `url` du portail Strukture est libre.
    """
    if not qid:
        return ""
    try:
        r = session.get(
            _WIKIDATA_API,
            params={
                "action": "wbgetclaims",
                "entity": qid,
                "property": "P856",
                "format": "json",
            },
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return ""
        claims = r.json().get("claims", {}).get("P856", [])
    except Exception:
        return ""
    for claim in claims:
        valeur = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(valeur, str) and valeur.startswith(("http://", "https://")):
            return valeur
    return ""


def _logo_officiel(qid: str) -> str:
    """Récupère le nom de fichier Wikimedia Commons du logo (propriété Wikidata P154), ou "".

    P154 porte un nom de fichier Commons (ex. « Ministère de la Culture.svg »), sans
    préfixe « File: ». Le portail Strukture n'accepte que jpeg/png : un SVG sera rendu en
    PNG par Wikimedia (voir ``url_logo_wikimedia``), mais un éventuel autre format est
    filtré plus tard par ``telecharger_logo``.
    """
    if not qid:
        return ""
    try:
        r = session.get(
            _WIKIDATA_API,
            params={
                "action": "wbgetclaims",
                "entity": qid,
                "property": "P154",
                "format": "json",
            },
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return ""
        claims = r.json().get("claims", {}).get("P154", [])
    except Exception:
        return ""
    for claim in claims:
        valeur = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(valeur, str) and valeur.strip():
            return valeur.strip()
    return ""


def url_logo_wikimedia(nom_fichier: str, largeur: int = 512) -> str:
    """URL de téléchargement d'un logo Commons, rendu à ``largeur`` px.

    Passe par « Special:FilePath » avec un paramètre ``width`` : Wikimedia rend alors un
    SVG en PNG (raster) et produit une vignette des images bitmap — indispensable car le
    portail n'accepte ni SVG ni image surdimensionnée.
    """
    return f"{_WIKIMEDIA_COMMONS_FICHIER}{quote(nom_fichier)}?width={largeur}"


def telecharger_logo(url: str) -> tuple[bytes, str] | None:
    """Télécharge un logo et retourne ``(contenu, type_mime)``, ou ``None``.

    Filtre ce que le portail n'accepterait pas : seuls ``image/jpeg`` et ``image/png``,
    taille ≤ 10 Mo. Toute erreur réseau, statut non-200 ou format non supporté → ``None``.
    """
    try:
        r = session.get(url, timeout=30, allow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    type_mime = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if type_mime not in _TYPES_IMAGE_ACCEPTES:
        return None
    contenu = r.content
    if not contenu or len(contenu) > _TAILLE_MAX_LOGO:
        return None
    return contenu, type_mime


def resumer_wikipedia(nom: str) -> dict | None:
    """Retourne un résumé Wikipédia pour une organisation, ou None.

    Retour : {"caption": <description Wikidata>, "summary": <extract>,
              "url": <page Wikipédia>, "site_officiel": <site officiel Wikidata P856>,
              "logo": <nom de fichier Commons du logo Wikidata P154>}
    - caption : phrase courte CC0 (description Wikidata via l'API REST)
    - summary : 2-4 phrases CC BY-SA (extract Wikipédia)
    - url : lien vers la page Wikipédia
    - site_officiel : site internet officiel de l'entité ("" si absent)
    - logo : nom de fichier Commons du logo ("" si absent)
    """
    if not nom or not nom.strip():
        return None

    cle = _normaliser_cle(nom)
    cached = _lire_cache(cle)
    if cached is not None:
        return cached if cached else None

    titre = _rechercher_titre(nom)
    if not titre:
        _ecrire_cache(cle, {})
        return None

    try:
        r = session.get(
            f"{_WIKI_API}/page/summary/{titre}",
            timeout=_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if r.status_code != 200:
            _ecrire_cache(cle, {})
            return None

        data = r.json()

        if data.get("type") != "standard":
            _ecrire_cache(cle, {})
            return None

        caption = (data.get("description") or "").strip()
        extract = (data.get("extract") or "").strip()
        url = data.get("content_urls", {}).get("desktop", {}).get("page", "")

        if not extract:
            _ecrire_cache(cle, {})
            return None

        qid = data.get("wikibase_item", "")
        result = {
            "caption": caption,
            "summary": extract,
            "url": url,
            "site_officiel": _site_officiel(qid),
            "logo": _logo_officiel(qid),
        }
        _ecrire_cache(cle, result)
        return result

    except Exception:
        _ecrire_cache(cle, {})
        return None
