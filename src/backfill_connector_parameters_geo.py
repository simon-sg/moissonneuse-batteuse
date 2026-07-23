"""
Rattrapage offline : injecte `connector_parameters` (versions/layer/default_crs/formats)
dans les `available_formats` des JDD géo déjà moissonnés.

Pourquoi : le portail RUDI local exige que `available_formats[0].connector
.connector_parameters` porte ces 4 clés pour afficher l'onglet « Carte » (sans quoi
l'onglet reste bloqué avant même la moindre requête réseau — bug D4, voir
rudi-portal-local/RAPPORT_BUGS_RUDI.md). Pour un service WMS/WFS réellement affiché
en carte (pas de fichier GeoJSON de secours), ces valeurs sont aussi réellement lues
par le portail pour construire la requête GetMap/GetFeature (nom de couche, version,
projection). `traduire_metadonnees_service()` (translation/datagouv_to_rudi.py) les
renseigne désormais pour tout futur harvest ; ce script rattrape les JDD géo
moissonnés avant l'introduction de ce traitement.

Aucune requête réseau : les noms de couches viennent de `data/geo_services.json`
(DATASETS_GEO), déjà sur disque — pipeline incrémental respecté.

Usage :
    python3 src/backfill_connector_parameters_geo.py               # dry-run : rapport seul
    python3 src/backfill_connector_parameters_geo.py --appliquer   # mute rudi_metadata.json + état
    python3 src/backfill_connector_parameters_geo.py --dossier <nom>  # cible un seul dataset

Après --appliquer, enchaîner :
    python3 src/publish_rudi.py   (nœud démarré, republie sur le nœud)
puis, pour propager au portail (le renvoi natif nœud→portail est documenté peu fiable,
bug D1) :
    python3 rudi-portal-local/raccordement/repousser_metadonnees.py <fichier_ids>
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.datasets import DATASETS_GEO
from state import charger_etat, sauvegarder_etat
from translation.datagouv_to_rudi import (
    _placeholder_connector_parameters, _connector_parameters_wms, _connector_parameters_wfs,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_GEO_FILE = os.path.join(DATA_DIR, "state_geo.json")

# Garde-fou (avertissement seulement) : ~99 services dans geo_services.json + quelques
# entrées manuelles au moment de l'écriture de ce script.
DOSSIERS_MIN_ATTENDU = 80
DOSSIERS_MAX_ATTENDU = 130


def _typename_depuis_caption(media: dict) -> str | None:
    """Extrait le typename d'une entrée FILE existante (media_caption =
    "<typename> — GeoJSON filtré Rennes Métropole", voir traduire_metadonnees_service)."""
    caption = media.get("media_caption", "")
    if " — " in caption:
        return caption.split(" — ", 1)[0]
    return None


def _noms_couches(config: dict) -> list[str]:
    """geo_services.json stocke `couches` sous deux formes selon l'origine de l'entrée :
    liste de chaînes (nom de couche/typename), ou liste de dicts {"nom", "titre",
    "bbox_wgs84"} (forme brute issue de _parse_layer_wms lors de la découverte WMS).
    Normalise vers une liste de chaînes."""
    couches = config.get("couches") or []
    return [c.get("nom", "") if isinstance(c, dict) else str(c) for c in couches]


def _connector_parameters_attendus(media: dict, config: dict, fichiers: list[dict]) -> list[dict]:
    contract = media.get("connector", {}).get("interface_contract")
    if media.get("media_type") == "FILE" or contract == "dwnl":
        return _placeholder_connector_parameters()
    if contract == "wms":
        return _connector_parameters_wms(_noms_couches(config))
    if contract == "wfs":
        # Priorité à ce qui a réellement été téléchargé (fichiers déjà présents) ;
        # à défaut, premier typename configuré (config["couches"]).
        typename = None
        for f in fichiers:
            typename = _typename_depuis_caption(f)
            if typename:
                break
        if not typename:
            couches = _noms_couches(config)
            typename = couches[0] if couches else None
        return _connector_parameters_wfs(typename)
    return _placeholder_connector_parameters()


def analyser_dossier(dossier: str, config: dict) -> dict | None:
    """Calcule les connector_parameters attendus pour un dossier, sans rien muter.
    Retourne None si rien à corriger ou si rudi_metadata.json absent."""
    chemin_meta = os.path.join(DATA_DIR, dossier, "rudi_metadata.json")
    if not os.path.isfile(chemin_meta):
        return None
    with open(chemin_meta, encoding="utf-8") as f:
        meta = json.load(f)

    formats = meta.get("available_formats", [])
    fichiers = [m for m in formats if m.get("media_type") == "FILE"]

    modifie = False
    for media in formats:
        attendu = _connector_parameters_attendus(media, config, fichiers)
        actuel = media.get("connector", {}).get("connector_parameters")
        if actuel != attendu:
            media.setdefault("connector", {})["connector_parameters"] = attendu
            modifie = True

    if not modifie:
        return None

    layer_final = None
    for media in formats:
        if media.get("connector", {}).get("interface_contract") in ("wms", "wfs"):
            for p in media["connector"]["connector_parameters"]:
                if p["key"] == "layer":
                    layer_final = p["value"]
            break

    return {"dossier": dossier, "meta": meta, "layer": layer_final,
            "titre": meta.get("resource_title", config.get("titre", ""))}


def appliquer_modification(rapport: dict, state_geo: dict) -> None:
    meta = rapport["meta"]
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta.setdefault("dataset_dates", {})["updated"] = now
    meta.setdefault("metadata_info", {}).setdefault("metadata_dates", {})["updated"] = now

    chemin_meta = os.path.join(DATA_DIR, rapport["dossier"], "rudi_metadata.json")
    with open(chemin_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # La fiche vient de changer : invalider sa publication pour que publish_rudi.py la repousse.
    state_geo.setdefault("_rudi_publie", {})[rapport["dossier"]] = False


def executer(appliquer: bool = False, dossier: str | None = None) -> int:
    configs = {c["dossier"]: c for c in DATASETS_GEO if c.get("dossier")}
    if dossier:
        if dossier not in configs:
            print(f"Dossier {dossier!r} absent de DATASETS_GEO.")
            return 1
        configs = {dossier: configs[dossier]}

    print(f"{len(configs)} service(s) géo à vérifier "
          f"({'APPLICATION' if appliquer else 'dry-run'})")

    rapports = []
    for nom, config in sorted(configs.items()):
        try:
            r = analyser_dossier(nom, config)
        except Exception as e:
            print(f"  {nom} : ERREUR — {e}")
            continue
        if r:
            rapports.append(r)

    a_na = [r for r in rapports if r["layer"] in (None, "n/a")]
    print(f"\n{len(rapports)} dossier(s) à corriger sur {len(configs)} vérifié(s).")
    for r in rapports:
        marque = "  [!] layer=n/a — vérifier config['couches']" if r in a_na else ""
        print(f"  {r['dossier']}  layer={r['layer']!r}  {r['titre'][:60]}{marque}")

    if not dossier and rapports and not (DOSSIERS_MIN_ATTENDU <= len(rapports) <= DOSSIERS_MAX_ATTENDU):
        print(f"[!] Nombre de dossiers à corriger hors de la plage attendue "
              f"[{DOSSIERS_MIN_ATTENDU}, {DOSSIERS_MAX_ATTENDU}] — vérifier le matching DATASETS_GEO "
              f"avant d'appliquer en masse.")

    if not appliquer:
        print("\nDry-run terminé — rien n'a été modifié. Relancer avec --appliquer.")
        return 0

    state_geo = charger_etat(STATE_GEO_FILE)
    for r in rapports:
        print(f"→ correction {r['dossier']}")
        appliquer_modification(r, state_geo)
    sauvegarder_etat(STATE_GEO_FILE, state_geo)

    print(f"\nApplication terminée : {len(rapports)} rudi_metadata.json corrigé(s).")
    if rapports:
        print("Enchaîner : python3 src/publish_rudi.py (nœud démarré), puis republier "
              "les fiches modifiées côté portail (voir docstring de ce script).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rattrapage connector_parameters (versions/layer/default_crs/formats) "
                    "pour les JDD géo déjà moissonnés.")
    parser.add_argument("--appliquer", action="store_true",
                        help="mute rudi_metadata.json et state_geo.json (défaut : dry-run)")
    parser.add_argument("--dossier", help="cible un seul dossier (debug)")
    args = parser.parse_args()
    return executer(appliquer=args.appliquer, dossier=args.dossier)


if __name__ == "__main__":
    sys.exit(main())
