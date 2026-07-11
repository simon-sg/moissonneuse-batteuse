"""
Connecteurs pour services géographiques : WFS, WMS, OGC API Features.
Utilisé par harvest_geo.py et discover.py.
"""
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

from connectors.http import session
from conf.communes_rm import BBOX_RM_STR, BBOX_RM as _BBOX_RM_TUPLE

_RM_BBOX = BBOX_RM_STR
_RM_LON_MIN, _RM_LAT_MIN, _RM_LON_MAX, _RM_LAT_MAX = _BBOX_RM_TUPLE

# Centre de Rennes (point de référence pour les probes GetMap)
_RM_CENTRE_LON = (_RM_LON_MIN + _RM_LON_MAX) / 2   # -1.65
_RM_CENTRE_LAT = (_RM_LAT_MIN + _RM_LAT_MAX) / 2   # 48.075
# Petite bbox autour du centre (~100m) pour le probe
_PROBE_BBOX_110 = f"{_RM_CENTRE_LON - 0.001:.4f},{_RM_CENTRE_LAT - 0.001:.4f}," \
                  f"{_RM_CENTRE_LON + 0.001:.4f},{_RM_CENTRE_LAT + 0.001:.4f}"

# Surface de la bbox RM en degrés² (pour comparer aux bbox de couches)
_RM_BBOX_AREA = abs((_RM_LON_MAX - _RM_LON_MIN) * (_RM_LAT_MAX - _RM_LAT_MIN))

# Cache des probes GetMap : (base_url, nom_couche) → bool
_cache_probes_wms: dict[tuple[str, str], bool] = {}

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
# Contacts extraits des GetCapabilities
# ---------------------------------------------------------------------------

def _texte_si_present(el, tag_suffix: str) -> str:
    """Cherche un enfant par suffixe de tag et retourne son texte nettoyé."""
    if el is None:
        return ""
    for child in el:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local == tag_suffix and child.text:
            return child.text.strip()
    return ""


def _extraire_contact_ows(root) -> dict | None:
    """Extrait le contact depuis ows:ServiceContact (WFS 2.0 / OWS).

    Structure XML :
      <ows:ServiceContact>
        <ows:IndividualName>...</ows:IndividualName>
        <ows:ContactInfo>
          <ows:Address>
            <ows:ElectronicMailAddress>...</ows:ElectronicMailAddress>
          </ows:Address>
        </ows:ContactInfo>
      </ows:ServiceContact>
    """
    ns_ows = "{http://www.opengis.net/ows}"
    sc = root.find(f".//{ns_ows}ServiceContact")
    if sc is None:
        return None
    name = _texte_si_present(sc, "IndividualName")
    # Chercher l'email dans ContactInfo/Address/ElectronicMailAddress
    email = ""
    ci = next((c for c in sc if c.tag == f"{ns_ows}ContactInfo"), None)
    if ci is not None:
        addr = next((c for c in ci.iter() if c.tag == f"{ns_ows}Address"), None)
        if addr is not None:
            email = _texte_si_present(addr, "ElectronicMailAddress")
    if not name and not email:
        return None
    return {"contact_name": name, "email": email}


def _extraire_contact_wms(root) -> dict | None:
    """Extrait le contact depuis ContactInformation (WMS 1.3.0).

    Structure XML (sans namespace ou namespace WMS) :
      <ContactInformation>
        <ContactPersonPrimary>
          <ContactPerson>...</ContactPerson>
          <ContactOrganization>...</ContactOrganization>
        </ContactPersonPrimary>
        <ContactElectronicMailAddress>...</ContactElectronicMailAddress>
      </ContactInformation>
    """
    # WMS peut avoir plusieurs namespaces, on cherche par suffixe de tag
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local == "ContactInformation":
            name = ""
            email = ""
            for child in el:
                clocal = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if clocal == "ContactPersonPrimary":
                    name = _texte_si_present(child, "ContactPerson")
                    if not name:
                        name = _texte_si_present(child, "ContactOrganization")
                elif clocal == "ContactElectronicMailAddress" and child.text:
                    email = child.text.strip()
            if not name and not email:
                return None
            return {"contact_name": name, "email": email}
    return None


def wfs_get_contact(url_base: str, timeout: int = 20) -> dict | None:
    """Interroge le GetCapabilities WFS et retourne le contact du service, ou None."""
    from connectors.contacts import _email_valide
    caps_url = f"{url_base}{_sep(url_base)}SERVICE=WFS&REQUEST=GetCapabilities"
    try:
        resp = session.get(caps_url, timeout=timeout)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception:
        return None
    contact = _extraire_contact_ows(root)
    if contact and _email_valide(contact.get("email", "")):
        return contact
    return None


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
    # srsName force la reprojection des géométries en sortie vers WGS84 : sans ce
    # paramètre, certains serveurs (ex. GeoServer Atmo France) renvoient les coordonnées
    # dans leur CRS de stockage natif (ex. EPSG:3857 en mètres) tout en produisant un
    # GeoJSON syntaxiquement valide — la carte Leaflet du catalogue interprète alors ces
    # mètres comme des degrés lon/lat, ce qui la casse silencieusement (points hors de
    # toute bbox plausible).
    sep = _sep(url_base)
    tentatives = [
        {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
         "TYPENAMES": typename, "BBOX": f"{_RM_BBOX},EPSG:4326",
         "outputFormat": "application/json", "count": str(max_features),
         "srsName": "EPSG:4326"},
        {"SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature",
         "TYPENAME": typename, "BBOX": f"{_RM_BBOX},EPSG:4326",
         "outputFormat": "application/json", "MAXFEATURES": str(max_features),
         "srsName": "EPSG:4326"},
        {"SERVICE": "WFS", "VERSION": "1.0.0", "REQUEST": "GetFeature",
         "TYPENAME": typename, "BBOX": _RM_BBOX,
         "outputFormat": "GeoJSON", "MAXFEATURES": str(max_features),
         "srsName": "EPSG:4326"},
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
              "outputFormat": "application/json", "count": str(max_features),
              "srsName": "EPSG:4326"}
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


def _bbox_area(bbox: dict) -> float:
    """Retourne la surface de la bbox en degrés² (0 si absente)."""
    if not bbox:
        return 0
    return abs((bbox.get("east", 0) - bbox.get("west", 0)) *
               (bbox.get("north", 0) - bbox.get("south", 0)))


def wms_probe_donnees_rm(base_url: str, layer_name: str, timeout: int = 10) -> bool:
    """Envoie un GetMap request (2x2 pixels) au centre de RM pour vérifier si la couche
    WMS a des données à cet endroit. Analyse le contenu du PNG (pas juste la taille)
    pour distinguer une image avec données réelles d'une tuile vide/blanche/transparente."""
    cle = (base_url, layer_name)
    if cle in _cache_probes_wms:
        return _cache_probes_wms[cle]

    # CRS:84 = axis order lon/lat (x/y), universellement compris par les WMS
    params = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
        "LAYERS": layer_name,
        "BBOX": f"{_RM_CENTRE_LON - 0.001:.4f},{_RM_CENTRE_LAT - 0.001:.4f},"
                f"{_RM_CENTRE_LON + 0.001:.4f},{_RM_CENTRE_LAT + 0.001:.4f}",
        "CRS": "CRS:84",
        "WIDTH": "2", "HEIGHT": "2", "FORMAT": "image/png", "STYLES": "",
    }
    try:
        r = session.get(base_url, params=params, timeout=timeout)
        if r.status_code != 200 or "image" not in r.headers.get("Content-Type", ""):
            _cache_probes_wms[cle] = False
            return False
        ok = _png_a_donnees(r.content)
        _cache_probes_wms[cle] = ok
        return ok
    except Exception:
        pass
    _cache_probes_wms[cle] = False
    return False


def _png_a_donnees(data: bytes) -> bool:
    """Vérifie si un PNG contient des données visuelles (pas une tuile vide/blanche/transparente).
    Analyse minimaliste : parse IHDR + IDAT, décompresse, vérifie la variété des pixels."""
    import struct as _struct
    import zlib as _zlib
    if len(data) < 30 or data[:8] != b'\x89PNG\r\n\x1a\n':
        return False
    pos = 8
    width = height = bit_depth = color_type = 0
    idat_data = b''
    has_plte = False
    while pos + 8 < len(data):
        length = _struct.unpack('>I', data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8]
        chunk_data = data[pos + 8:pos + 8 + length]
        if chunk_type == b'IHDR' and length >= 13:
            width, height, bit_depth, color_type = _struct.unpack('>IIBB', chunk_data[:10])
        elif chunk_type == b'PLTE':
            has_plte = True
        elif chunk_type == b'IDAT':
            idat_data += chunk_data
        pos += 12 + length
        if chunk_type == b'IEND':
            break
    if not idat_data or width == 0 or height == 0:
        return False
    try:
        pixels = _zlib.decompress(idat_data)
    except Exception:
        return False
    # Palette (ct=3) : au moins 2 indices différents = données réelles
    if color_type == 3 and has_plte:
        indices = set(pixels)
        return len(indices) > 1
    # RGB (ct=2) : pixels codés sur 3 octets, skip filter bytes en début de ligne
    if color_type == 2:
        row_stride = 1 + width * 3
        rgb_vals = set()
        for row in range(height):
            for col in range(width):
                offset = row * row_stride + 1 + col * 3
                if offset + 3 <= len(pixels):
                    rgb_vals.add(tuple(pixels[offset:offset + 3]))
        return len(rgb_vals) > 1
    # RGBA (ct=8) : vérifier qu'au moins un pixel a un alpha > 0 et une couleur non nulle
    if color_type == 6:
        row_stride = 1 + width * 4
        non_vide = 0
        for row in range(height):
            for col in range(width):
                offset = row * row_stride + 1 + col * 4
                if offset + 4 <= len(pixels):
                    r_val, g_val, b_val, a = pixels[offset:offset + 4]
                    if a > 0 and (r_val > 0 or g_val > 0 or b_val > 0):
                        non_vide += 1
        return non_vide > 0
    return False


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
    GetCapabilities WMS → {"titre": str, "couches": [...], "metadata_urls": [...]}.
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
    metadata_urls = []
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "Layer":
            layer = _parse_layer_wms(el)
            if layer["nom"]:
                couches.append(layer)
            # Extraire MetadataURL depuis chaque couche
            for child in el:
                ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if ctag == "MetadataURL":
                    for online in child.iter():
                        otag = online.tag.split("}")[-1] if "}" in online.tag else online.tag
                        if otag == "OnlineResource":
                            href = online.get(
                                "{http://www.w3.org/1999/xlink}href",
                                online.get("href", ""),
                            )
                            if href and href.startswith("http"):
                                metadata_urls.append(href)

    # URLs uniques, en préservant l'ordre
    seen = set()
    urls_uniques = []
    for u in metadata_urls:
        if u not in seen:
            seen.add(u)
            urls_uniques.append(u)

    contact = _extraire_contact_wms(root)
    return {"titre": titre_service, "couches": couches, "metadata_urls": urls_uniques,
            "contact": contact}


def wms_couches_dans_rm(capabilities: dict, base_url: str = "") -> list[dict]:
    """Filtre les couches WMS qui contiennent des données Rennes Métropole.

    Deux niveaux de vérification :
    1. Si la bbox de la couche chevauche RM → la couche est retenue (vérification rapide,
       fiable pour les couches à portée locale/départementale).
    2. Si la bbox est absente ou très grande (> 10× la surface de RM) → probe GetMap
       au centre de RM pour vérifier que le service renvoie effectivement des données à
       cet endroit (évite les faux positifs des services nationaux/départementaux dont
       la bbox déclarée couvre toute la France mais les données sont limitées).
    """
    if not base_url:
        # Pas d'URL → fallback sur le seul check bbox (mode rétro-compatible)
        return [c for c in capabilities.get("couches", [])
                if _bbox_overlap_rm(c.get("bbox_wgs84", {}))]

    resultats = []
    for c in capabilities.get("couches", []):
        bb = c.get("bbox_wgs84", {})
        nom = c.get("nom", "")
        if not nom:
            continue

        if _bbox_overlap_rm(bb):
            area = _bbox_area(bb)
            if area > 0 and area <= _RM_BBOX_AREA * 10:
                # bbox déclarée et de taille raisonnable → fiable
                resultats.append(c)
            else:
                # bbox absente ou très large (nationale) → probe GetMap
                if wms_probe_donnees_rm(base_url, nom):
                    resultats.append(c)
    return resultats


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
