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

from connectors.http import session

_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "cache", "wikipedia",
)
_CACHE_TTL_SECS = 30 * 24 * 3600  # 30 jours

_WIKI_API = "https://fr.wikipedia.org/api/rest_v1"
_TIMEOUT = 15


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
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


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


def resumer_wikipedia(nom: str) -> dict | None:
    """Retourne un résumé Wikipédia pour une organisation, ou None.

    Retour : {"caption": <description Wikidata>, "summary": <extract>, "url": <page url>}
    - caption : phrase courte CC0 (description Wikidata via l'API REST)
    - summary : 2-4 phrases CC BY-SA (extract Wikipédia)
    - url : lien vers la page Wikipédia
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

        result = {
            "caption": caption,
            "summary": extract,
            "url": url,
        }
        _ecrire_cache(cle, result)
        return result

    except Exception:
        _ecrire_cache(cle, {})
        return None
