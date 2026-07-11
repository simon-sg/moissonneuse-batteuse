"""
Pipeline de moisson pour les services géographiques (WFS, WMS, OGC API Features).

Usage : python3 src/harvest_geo.py

Configure les services dans src/conf/datasets.py → DATASETS_GEO.

data/state_geo.json — signature (Content-Length/ETag/Last-Modified) par couche WFS/OGC,
pour éviter un re-téléchargement si la couche n'a pas changé. Best-effort : si le serveur
ne répond pas à un HEAD ou ne fournit aucun de ces en-têtes, la couche est retéléchargée
à chaque run (comportement d'avant cette fonctionnalité, pas de régression). WMS n'a pas
d'équivalent : il n'y a rien à télécharger, seulement GetCapabilities (déjà léger).
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.communes_rm import CODES_INSEE_RM, BBOX_RM
from conf.datasets import DATASETS_GEO
from connectors.geo_services import (
    nettoyer_url_ogc,
    wfs_lister_couches, wfs_telecharger_rm, wfs_signature, wfs_get_contact,
    wms_get_capabilities, wms_couches_dans_rm,
    ogcapi_lister_collections, ogcapi_telecharger_rm, ogcapi_signature,
)
from translation.datagouv_to_rudi import traduire_metadonnees_service
from connectors.rudi_publish import publier_si_configue
from state import charger_etat, sauvegarder_etat

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "state_geo.json")


def _slug_typename(typename: str) -> str:
    """Convertit un typename WFS/OGC en nom de fichier safe."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", typename.split(":")[-1])[:50]


def _sauver_geojson(chemin: str, data: dict) -> None:
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    nb = len(data.get("features", []))
    print(f"  Sauvegardé : {os.path.basename(chemin)} ({nb} features)")


def traiter_wfs(config: dict, dossier: str, state: dict) -> tuple[list[tuple[str, str]], bool, dict | None]:
    """
    Télécharge les couches WFS dans la bbox RM (sautées si signature inchangée
    et fichier déjà présent — voir _charger_state en tête de fichier).
    Retourne ([(chemin_fichier, typename), ...], au_moins_une_changee, contact).
    """
    url = nettoyer_url_ogc(config["url"])
    couches = config.get("couches")
    contact = None
    if not couches:
        print("  Détection automatique des couches WFS...")
        couches = wfs_lister_couches(url)
        print(f"  {len(couches)} couche(s) : {couches[:5]}")

    # Extraction du contact depuis le GetCapabilities (une seule requête)
    contact = wfs_get_contact(url)

    resultats = []
    changee = False
    for typename in couches:
        nom_fichier = f"{_slug_typename(typename)}.geojson"
        chemin = os.path.join(dossier, nom_fichier)
        cle = f"{config['id']}::wfs::{typename}"
        signature = wfs_signature(url, typename)
        if signature and os.path.exists(chemin) and signature == state.get(cle, {}).get("signature"):
            print(f"  WFS inchangé (cache) : {typename}")
            resultats.append((chemin, typename))
            continue

        print(f"  Téléchargement WFS : {typename}")
        data = wfs_telecharger_rm(url, typename)
        if data is None:
            print(f"  (échec pour {typename})")
            continue
        _sauver_geojson(chemin, data)
        resultats.append((chemin, typename))
        state[cle] = {"signature": signature} if signature else {}
        changee = True
    return resultats, changee, contact


def traiter_ogcapi(config: dict, dossier: str, state: dict) -> tuple[list[tuple[str, str]], bool]:
    """
    Télécharge les collections OGC API Features dans la bbox RM (sautées si signature
    inchangée et fichier déjà présent — voir _charger_state en tête de fichier).
    Retourne ([(chemin_fichier, collection_id), ...], au_moins_une_changee).
    """
    url = config["url"].rstrip("/")
    couches = config.get("couches")
    if not couches:
        print("  Détection automatique des collections OGC API...")
        cols = ogcapi_lister_collections(url)
        couches = [c["id"] for c in cols]
        print(f"  {len(couches)} collection(s) : {couches[:5]}")

    resultats = []
    changee = False
    for col_id in couches:
        nom_fichier = f"{_slug_typename(col_id)}.geojson"
        chemin = os.path.join(dossier, nom_fichier)
        cle = f"{config['id']}::ogcapi::{col_id}"
        signature = ogcapi_signature(url, col_id)
        if signature and os.path.exists(chemin) and signature == state.get(cle, {}).get("signature"):
            print(f"  OGC API inchangé (cache) : {col_id}")
            resultats.append((chemin, col_id))
            continue

        print(f"  Téléchargement OGC API : {col_id}")
        data = ogcapi_telecharger_rm(url, col_id)
        if data is None:
            print(f"  (échec pour {col_id})")
            continue
        _sauver_geojson(chemin, data)
        resultats.append((chemin, col_id))
        state[cle] = {"signature": signature} if signature else {}
        changee = True
    return resultats, changee


def traiter_geojson(config: dict, dossier: str, state: dict) -> tuple[list[tuple[str, str]], bool]:
    """Télécharge un fichier GeoJSON statique et filtre les features RM.

    Filtrage par propriété si `champ_iris` est défini dans la config (valeur = code INSEE
    commune sur 5 chiffres) ; sinon filtrage par bbox RM (lon -2.00–-1.30, lat 47.80–48.35).
    Utilise l'ETag/Last-Modified pour sauter le téléchargement si le fichier n'a pas changé.

    Exemple de config dans DATASETS_GEO :
        {
            "id": "centroides-communes-rm",
            "type": "geojson",
            "url": "https://…/centroides_communes_population.geojson",
            "champ_iris": "code_commune",  # optionnel — propriété = code INSEE 5c
            "titre": "Centroïdes des communes RM",
            "producteur": "…",
            "dossier": "centroides-communes-rm",
            "theme": "location",
        }
    Retourne ([(chemin_fichier, nom_fichier)], au_moins_une_changee).
    """
    from connectors.http import session

    url = config["url"]
    champ_commune = config.get("champ_iris")  # clé de propriété = code INSEE commune
    nom_fichier = config.get("nom_fichier", "features-rennesmetropole.geojson")
    chemin = os.path.join(dossier, nom_fichier)
    cle = f"{config['id']}::geojson"

    # Vérification ETag/Last-Modified pour éviter un re-téléchargement inutile
    try:
        head = session.head(url, timeout=20, allow_redirects=True)
        sig_actuelle = head.headers.get("ETag") or head.headers.get("Last-Modified")
    except Exception:
        sig_actuelle = None

    if sig_actuelle and os.path.exists(chemin) and sig_actuelle == state.get(cle, {}).get("signature"):
        print(f"  GeoJSON inchangé (cache) : {os.path.basename(chemin)}")
        return [(chemin, nom_fichier)], False

    print(f"  Téléchargement GeoJSON : {url[:70]}")
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    features_src = data.get("features", [])
    print(f"  {len(features_src)} features au total")

    if champ_commune:
        codes_rm = set(CODES_INSEE_RM)
        features_rm = [
            f for f in features_src
            if str((f.get("properties") or {}).get(champ_commune, "")) in codes_rm
        ]
    else:
        lon_min, lat_min, lon_max, lat_max = BBOX_RM
        def _dans_bbox(f):
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates")
            if not coords:
                return False
            gtype = geom.get("type", "")
            if gtype == "Point":
                lon, lat = coords[0], coords[1]
                return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max
            return True  # types complexes : inclure par défaut, trop coûteux à vérifier
        features_rm = [f for f in features_src if _dans_bbox(f)]

    print(f"  {len(features_rm)} features RM conservées")
    geojson_rm = {**data, "features": features_rm}
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(geojson_rm, f, ensure_ascii=False)

    state[cle] = {"signature": sig_actuelle} if sig_actuelle else {}
    return [(chemin, nom_fichier)], True


def traiter_wms(config: dict, dossier: str) -> dict:
    """
    Sonde le service WMS via GetCapabilities et sauvegarde wms_service.json.
    Retourne le dict wms_service.
    """
    url = nettoyer_url_ogc(config["url"])
    couches_config = config.get("couches")
    print("  Sondage WMS GetCapabilities...")
    try:
        caps = wms_get_capabilities(url)
        couches_rm = wms_couches_dans_rm(caps)
        if couches_config:
            couches_rm = [c for c in couches_rm if c["nom"] in couches_config] or couches_rm
        titre_service = caps.get("titre") or config.get("titre", "Service WMS")
    except Exception as e:
        print(f"  Avertissement GetCapabilities : {e}")
        couches_rm = [{"nom": c, "titre": c, "bbox_wgs84": {}}
                      for c in (couches_config or [])]
        titre_service = config.get("titre", "Service WMS")

    wms_service = {
        "type": "wms",
        "url": url,
        "titre_service": titre_service,
        "producteur": config.get("producteur", ""),
        "couches": couches_rm,
        "metadata_urls": caps.get("metadata_urls", []) if caps else [],
        "contact": caps.get("contact") if caps else None,
    }
    chemin = os.path.join(dossier, "wms_service.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(wms_service, f, ensure_ascii=False, indent=2)
    print(f"  wms_service.json sauvegardé ({len(couches_rm)} couche(s) RM)")
    return wms_service


def traiter_geo_dataset(config: dict, state: dict) -> None:
    service_type = config.get("type", "wfs")
    dossier = os.path.join(DATA_DIR, config["dossier"])
    os.makedirs(dossier, exist_ok=True)

    print(f"  Type : {service_type.upper()}  |  URL : {config['url'][:70]}")

    fichiers_geojson: list[tuple[str, str]] = []
    changee = False
    wms_service: dict | None = None
    contact_source: dict | None = None

    if service_type == "wfs":
        fichiers_geojson, changee, contact_source = traiter_wfs(config, dossier, state)
    elif service_type == "ogcapi":
        fichiers_geojson, changee = traiter_ogcapi(config, dossier, state)
    elif service_type == "wms":
        wms_service = traiter_wms(config, dossier)
        contact_source = wms_service.get("contact") if wms_service else None
        changee = True  # WMS toujours regénéré (GetCapabilities léger)
    elif service_type == "geojson":
        fichiers_geojson, changee = traiter_geojson(config, dossier, state)
    else:
        print(f"  Type inconnu : {service_type!r}. Types supportés : wfs, wms, ogcapi, geojson")
        return

    rudi_metadata_file = os.path.join(dossier, "rudi_metadata.json")
    # Si rien n'a changé et que rudi_metadata.json existe déjà, on garde l'existant
    if not changee and os.path.isfile(rudi_metadata_file):
        print("  Aucune donnée modifiée — métadonnées RUDI et publication inchangées.")
        return

    # Métadonnées RUDI
    metadata_urls = wms_service.get("metadata_urls", []) if wms_service else []
    rudi_metadata = traduire_metadonnees_service(
        config=config,
        fichiers_geojson=fichiers_geojson,
        wms_service=wms_service,
        metadata_urls=metadata_urls,
        contacts_source=[contact_source] if contact_source else None,
    )
    with open(rudi_metadata_file, "w", encoding="utf-8") as f:
        json.dump(rudi_metadata, f, ensure_ascii=False, indent=2)
    print("  Métadonnées RUDI sauvegardées.")

    # Publication sur le nœud RUDI
    publie = publier_si_configue(rudi_metadata, [ch for ch, _ in fichiers_geojson])

    if publie:
        state.setdefault("_rudi_publie", {})
        state["_rudi_publie"][config["dossier"]] = True


def main():
    print("=== Moisson services géographiques ===")
    print(f"{len(DATASETS_GEO)} service(s) configuré(s)\n")
    state = charger_etat(STATE_FILE)
    for config in DATASETS_GEO:
        print(f"--- {config['id']} ---")
        try:
            traiter_geo_dataset(config, state)
        except Exception as e:
            print(f"  ERREUR : {e}")
        sauvegarder_etat(STATE_FILE, state)  # incrémental : un service planté ne perd pas les précédents
        print()
    print("=== Fin ===")


if __name__ == "__main__":
    main()
