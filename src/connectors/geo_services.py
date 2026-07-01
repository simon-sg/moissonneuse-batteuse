"""
Connecteurs pour services géographiques : WFS, WMS, OGC API Features.
Utilisé par harvest_geo.py et discover.py.
"""
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

from connectors.http import session

_RM_BBOX = "-2.00,47.80,-1.30,48.35"   # minLon,minLat,maxLon,maxLat (WGS84)
_RM_LON_MIN, _RM_LAT_MIN = -2.00, 47.80
_RM_LON_MAX, _RM_LAT_MAX = -1.30, 48.35

_OGC_KEYS = {
    "service", "request", "version", "typename", "typenames",
    "outputformat", "bbox", "maxfeatures", "count", "srsname",
    "layers", "width", "height", "format", "styles", "crs", "srs",
}


def nettoyer_url_ogc(url: str) -> str:
    """Retire les paramètres OGC standard d'une URL de service."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs_filtre = {k: v for k, v in qs.items() if k.lower() not in _OGC_KEYS}
    query = "&".join(f"{k}={v[0]}" for k, v in qs_filtre.items())
    return urlunparse(parsed._replace(query=query))


def _sep(url: str) -> str:
    return "&" if "?" in url else "?"


def _signature_head(url: str, timeout: int = 15) -> dict | None:
    """
    Sonde une URL en HEAD et retourne un identifiant de contenu (Content-Length/ETag/
    Last-Modified) si le serveur les fournit. Retourne None si le serveur ne supporte
    pas HEAD sur cet endpoint ou ne renvoie aucun de ces en-têtes — dans ce cas
    impossible de détecter un changement sans télécharger, l'appelant doit alors
    retélécharger sans condition (comportement inchangé, pas de régression).
    """
    try:
        resp = session.head(url, timeout=timeout, allow_redirects=True)
        if resp.status_code != 200:
            return None
    except Exception:
        return None
    sig = {k: resp.headers.get(k) for k in ("Content-Length", "ETag", "Last-Modified")}
    return sig if any(sig.values()) else None


# ---------------------------------------------------------------------------
# WFS
# ---------------------------------------------------------------------------

def wfs_lister_couches(url_base: str, timeout: int = 20) -> list[str]:
    """GetCapabilities WFS → liste des typename disponibles."""
    caps_url = f"{url_base}{_sep(url_base)}SERVICE=WFS&REQUEST=GetCapabilities"
    resp = session.get(caps_url, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    layers = []
    for el in root.iter():
        if el.tag.endswith("}FeatureType"):
            name_el = next((c for c in el if c.tag.endswith("}Name")), None)
            if name_el is not None and name_el.text:
                layers.append(name_el.text.strip())
    return layers


def wfs_telecharger_rm(url_base: str, typename: str,
                        max_features: int = 10000, timeout: int = 60) -> dict | None:
    """
    Télécharge les features WFS dans la bbox de Rennes Métropole.
    Essaie WFS 2.0.0 → 1.1.0 → 1.0.0. Retourne un dict GeoJSON ou None.
    """
    sep = _sep(url_base)
    tentatives = [
        {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
         "TYPENAMES": typename, "BBOX": f"{_RM_BBOX},EPSG:4326",
         "outputFormat": "application/json", "count": str(max_features)},
        {"SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature",
         "TYPENAME": typename, "BBOX": f"{_RM_BBOX},EPSG:4326",
         "outputFormat": "application/json", "MAXFEATURES": str(max_features)},
        {"SERVICE": "WFS", "VERSION": "1.0.0", "REQUEST": "GetFeature",
         "TYPENAME": typename, "BBOX": _RM_BBOX,
         "outputFormat": "GeoJSON", "MAXFEATURES": str(max_features)},
    ]
    for params in tentatives:
        try:
            resp = session.get(f"{url_base}{sep}{urlencode(params)}", timeout=timeout)
            if resp.status_code != 200:
                continue
            body = resp.content.lstrip()
            if not body.startswith(b"{"):
                continue
            data = resp.json()
            if "features" in data:
                return data
        except Exception as e:
            print(f"    [WFS] tentative {params['VERSION']} échouée pour {typename} : {e}")
            continue
    return None


def wfs_signature(url_base: str, typename: str, max_features: int = 10000,
                   timeout: int = 15) -> dict | None:
    """Signature best-effort (HEAD) de la requête GetFeature WFS 2.0.0 — voir _signature_head."""
    sep = _sep(url_base)
    params = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
              "TYPENAMES": typename, "BBOX": f"{_RM_BBOX},EPSG:4326",
              "outputFormat": "application/json", "count": str(max_features)}
    return _signature_head(f"{url_base}{sep}{urlencode(params)}", timeout=timeout)


# ---------------------------------------------------------------------------
# WMS
# ---------------------------------------------------------------------------

def _bbox_overlap_rm(bbox: dict) -> bool:
    """True si la bbox (west/east/south/north) chevauche Rennes Métropole."""
    if not bbox:
        return True  # pas de bbox déclarée → on assume couverture nationale
    return (bbox.get("west", -180) <= _RM_LON_MAX and
            bbox.get("east", 180)  >= _RM_LON_MIN and
            bbox.get("south", -90) <= _RM_LAT_MAX and
            bbox.get("north", 90)  >= _RM_LAT_MIN)


def _parse_layer_wms(el) -> dict:
    """Extrait nom, titre et bbox WGS84 d'un élément <Layer> XML."""
    nom, titre = "", ""
    bbox = {}
    for child in el:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "Name" and not nom:
            nom = (child.text or "").strip()
        elif tag == "Title" and not titre:
            titre = (child.text or "").strip()
        elif tag == "EX_GeographicBoundingBox":
            sub = {c.tag.split("}")[-1]: c.text for c in child}
            try:
                bbox = {
                    "west": float(sub.get("westBoundLongitude", -180)),
                    "east": float(sub.get("eastBoundLongitude", 180)),
                    "south": float(sub.get("southBoundLatitude", -90)),
                    "north": float(sub.get("northBoundLatitude", 90)),
                }
            except (TypeError, ValueError):
                pass
        elif tag == "LatLonBoundingBox":
            try:
                bbox = {
                    "west": float(child.get("minx", -180)),
                    "east": float(child.get("maxx", 180)),
                    "south": float(child.get("miny", -90)),
                    "north": float(child.get("maxy", 90)),
                }
            except (TypeError, ValueError):
                pass
    return {"nom": nom, "titre": titre, "bbox_wgs84": bbox}


def wms_get_capabilities(url_base: str, timeout: int = 20) -> dict:
    """
    GetCapabilities WMS → {"titre": str, "couches": [{"nom", "titre", "bbox_wgs84"}]}.
    Lève une exception si la requête échoue.
    """
    sep = _sep(url_base)
    caps_url = f"{url_base}{sep}SERVICE=WMS&REQUEST=GetCapabilities"
    resp = session.get(caps_url, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    titre_service = ""
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "Title" and (el.text or "").strip():
            titre_service = el.text.strip()
            break

    couches = []
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "Layer":
            layer = _parse_layer_wms(el)
            if layer["nom"]:
                couches.append(layer)

    return {"titre": titre_service, "couches": couches}


def wms_couches_dans_rm(capabilities: dict) -> list[dict]:
    """Filtre les couches WMS dont la bbox couvre Rennes Métropole."""
    return [c for c in capabilities.get("couches", [])
            if _bbox_overlap_rm(c.get("bbox_wgs84", {}))]


# ---------------------------------------------------------------------------
# OGC API Features (WFS 3.0)
# ---------------------------------------------------------------------------

def ogcapi_lister_collections(url_base: str, timeout: int = 20) -> list[dict]:
    """
    Liste les collections d'un service OGC API Features.
    Retourne [{"id", "titre", "bbox_wgs84"}].
    """
    url = url_base.rstrip("/") + "/collections"
    resp = session.get(url, timeout=timeout, headers={"Accept": "application/json"})
    resp.raise_for_status()
    data = resp.json()
    collections = []
    for col in data.get("collections", []):
        bbox_raw = (col.get("extent") or {}).get("spatial", {}).get("bbox", [[]])
        bbox_list = bbox_raw[0] if bbox_raw else []
        bbox = {}
        if len(bbox_list) >= 4:
            bbox = {"west": bbox_list[0], "south": bbox_list[1],
                    "east": bbox_list[2], "north": bbox_list[3]}
        titre = col.get("title") or col.get("id", "")
        collections.append({"id": col["id"], "titre": titre, "bbox_wgs84": bbox})
    return collections


def ogcapi_telecharger_rm(url_base: str, collection_id: str,
                           limit: int = 10000, timeout: int = 60) -> dict | None:
    """
    Télécharge les items d'une collection OGC API Features dans la bbox RM.
    Retourne un dict GeoJSON ou None.
    """
    url = f"{url_base.rstrip('/')}/collections/{collection_id}/items"
    params = {"bbox": _RM_BBOX, "limit": limit, "f": "application/geo+json"}
    try:
        resp = session.get(url, params=params, timeout=timeout,
                            headers={"Accept": "application/geo+json,application/json"})
        resp.raise_for_status()
        data = resp.json()
        if "features" in data:
            return data
    except Exception as e:
        print(f"    [OGC API] téléchargement de {collection_id} échoué : {e}")
    return None


def ogcapi_signature(url_base: str, collection_id: str, limit: int = 10000,
                      timeout: int = 15) -> dict | None:
    """Signature best-effort (HEAD) de la requête items OGC API — voir _signature_head."""
    url = f"{url_base.rstrip('/')}/collections/{collection_id}/items"
    params = {"bbox": _RM_BBOX, "limit": limit, "f": "application/geo+json"}
    return _signature_head(f"{url}{_sep(url)}{urlencode(params)}", timeout=timeout)
