"""
Pipeline de moisson pour les services géographiques (WFS, WMS, OGC API Features).

Usage : python3 src/harvest_geo.py

Configure les services dans src/conf/datasets.py → DATASETS_GEO.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.datasets import DATASETS_GEO
from connectors.geo_services import (
    nettoyer_url_ogc,
    wfs_lister_couches, wfs_telecharger_rm,
    wms_get_capabilities, wms_couches_dans_rm,
    ogcapi_lister_collections, ogcapi_telecharger_rm,
)
from translation.datagouv_to_rudi import traduire_metadonnees_service
from connectors.rudi_node import publier_dataset, charger_conf_rudi

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _slug_typename(typename: str) -> str:
    """Convertit un typename WFS/OGC en nom de fichier safe."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", typename.split(":")[-1])[:50]


def _sauver_geojson(chemin: str, data: dict) -> None:
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    nb = len(data.get("features", []))
    print(f"  Sauvegardé : {os.path.basename(chemin)} ({nb} features)")


def traiter_wfs(config: dict, dossier: str) -> list[tuple[str, str]]:
    """
    Télécharge les couches WFS dans la bbox RM.
    Retourne [(chemin_fichier, typename), ...].
    """
    url = nettoyer_url_ogc(config["url"])
    couches = config.get("couches")
    if not couches:
        print("  Détection automatique des couches WFS...")
        couches = wfs_lister_couches(url)
        print(f"  {len(couches)} couche(s) : {couches[:5]}")

    resultats = []
    for typename in couches:
        print(f"  Téléchargement WFS : {typename}")
        data = wfs_telecharger_rm(url, typename)
        if data is None:
            print(f"  (échec pour {typename})")
            continue
        nom_fichier = f"{_slug_typename(typename)}.geojson"
        chemin = os.path.join(dossier, nom_fichier)
        _sauver_geojson(chemin, data)
        resultats.append((chemin, typename))
    return resultats


def traiter_ogcapi(config: dict, dossier: str) -> list[tuple[str, str]]:
    """
    Télécharge les collections OGC API Features dans la bbox RM.
    Retourne [(chemin_fichier, collection_id), ...].
    """
    url = config["url"].rstrip("/")
    couches = config.get("couches")
    if not couches:
        print("  Détection automatique des collections OGC API...")
        cols = ogcapi_lister_collections(url)
        couches = [c["id"] for c in cols]
        print(f"  {len(couches)} collection(s) : {couches[:5]}")

    resultats = []
    for col_id in couches:
        print(f"  Téléchargement OGC API : {col_id}")
        data = ogcapi_telecharger_rm(url, col_id)
        if data is None:
            print(f"  (échec pour {col_id})")
            continue
        nom_fichier = f"{_slug_typename(col_id)}.geojson"
        chemin = os.path.join(dossier, nom_fichier)
        _sauver_geojson(chemin, data)
        resultats.append((chemin, col_id))
    return resultats


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
    }
    chemin = os.path.join(dossier, "wms_service.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(wms_service, f, ensure_ascii=False, indent=2)
    print(f"  wms_service.json sauvegardé ({len(couches_rm)} couche(s) RM)")
    return wms_service


def traiter_geo_dataset(config: dict) -> None:
    service_type = config.get("type", "wfs")
    dossier = os.path.join(DATA_DIR, config["dossier"])
    os.makedirs(dossier, exist_ok=True)

    print(f"  Type : {service_type.upper()}  |  URL : {config['url'][:70]}")

    fichiers_geojson: list[tuple[str, str]] = []
    wms_service: dict | None = None

    if service_type == "wfs":
        fichiers_geojson = traiter_wfs(config, dossier)
    elif service_type == "ogcapi":
        fichiers_geojson = traiter_ogcapi(config, dossier)
    elif service_type == "wms":
        wms_service = traiter_wms(config, dossier)
    else:
        print(f"  Type inconnu : {service_type!r}. Types supportés : wfs, wms, ogcapi")
        return

    # Métadonnées RUDI
    rudi_metadata_file = os.path.join(dossier, "rudi_metadata.json")
    rudi_metadata = traduire_metadonnees_service(
        config=config,
        fichiers_geojson=fichiers_geojson,
        wms_service=wms_service,
    )
    with open(rudi_metadata_file, "w", encoding="utf-8") as f:
        json.dump(rudi_metadata, f, ensure_ascii=False, indent=2)
    print("  Métadonnées RUDI sauvegardées.")

    # Publication sur le nœud RUDI
    conf_rudi = charger_conf_rudi()
    if conf_rudi:
        try:
            chemins = [ch for ch, _ in fichiers_geojson]
            publier_dataset(conf=conf_rudi, rudi_metadata=rudi_metadata,
                            fichiers_filtres=chemins)
        except Exception as e:
            print(f"  [RUDI] Erreur publication : {e}")
    else:
        print("  [RUDI] src/conf/rudi_node.json absent, publication ignorée.")


def main():
    print("=== Moisson services géographiques ===")
    print(f"{len(DATASETS_GEO)} service(s) configuré(s)\n")
    for config in DATASETS_GEO:
        print(f"--- {config['id']} ---")
        try:
            traiter_geo_dataset(config)
        except Exception as e:
            print(f"  ERREUR : {e}")
        print()
    print("=== Fin ===")


if __name__ == "__main__":
    main()
