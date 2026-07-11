"""
Connecteur API RVA (Référentiel Voies et Adresses) de Rennes Métropole.

https://api-rva.sig.rennesmetropole.fr/
Documentation : https://api-rva.sig.rennesmetropole.fr/documentation.php

Nécessite une clé d'API (inscription sur le site).
Le cache des réponses est interdit par les CGU — usage strictement à la volée.
"""

import json
import os
from urllib.parse import urlencode

from connectors.http import session

_API_BASE = "https://api-rva.sig.rennesmetropole.fr/"
_CONF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "conf")
_CONF_FILE = os.path.join(_CONF_DIR, "rva_key.json")


def _charger_cle() -> str | None:
    if not os.path.isfile(_CONF_FILE):
        return None
    with open(_CONF_FILE, encoding="utf-8") as f:
        return json.load(f).get("key", "").strip() or None


def geocoder_adresse(query: str, insee: str | None = None,
                     epsg: int = 4326) -> list[dict]:
    """Geocode une adresse via la commande getfulladdresses de l'API RVA.

    query : texte d'adresse complète (3 caractères minimum)
    insee : optionnel, filtre sur une commune
    epsg  : 4326 (WGS84), 2154 (Lambert-93), 3948 (CC48)

    Retourne une liste de dicts :
        [{"insee", "idlane", "idaddress", "number", "extension",
          "addr3", "x", "y", "zipcode"}, ...]
    """
    cle = _charger_cle()
    if not cle:
        print("  [RVA] AVERTISSEMENT : clé API non configurée "
              "(créer src/conf/rva_key.json avec {\"key\": \"...\"})")
        return []

    params = {
        "key": cle,
        "version": "1.0",
        "format": "json",
        "epsg": str(epsg),
        "cmd": "getfulladdresses",
        "query": query,
    }
    if insee:
        params["insee"] = insee

    try:
        resp = session.get(_API_BASE, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [RVA] Échec geocodage «{query[:40]}…» : {e}")
        return []

    answer = data.get("rva", {}).get("answer", {})
    if str(answer.get("status", {}).get("code")) != "1":
        return []

    return answer.get("addresses", [])


def geocoder_adresse_unique(query: str, insee: str | None = None,
                            epsg: int = 4326) -> dict | None:
    """Comme geocoder_adresse mais retourne la première adresse ou None."""
    results = geocoder_adresse(query, insee, epsg)
    return results[0] if results else None
