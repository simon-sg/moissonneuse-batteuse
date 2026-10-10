"""
Rattrapage des dates : met à jour les rudi_metadata.json existants (source
data.gouv.fr) dont dataset_dates.created/updated a été tronqué à minuit UTC
par l'ancien bug de traduction (voir translation/rudi_builder.py::_parser_date_http
et translation/datagouv_to_rudi.py), en refetchant created_at/last_modified
depuis l'API data.gouv.fr et en réappliquant la traduction corrigée.

Usage :
  python3 src/enrichir_dates.py          # corrige + republie
  python3 src/enrichir_dates.py --dry-run # affiche sans modifier

Ne re-télécharge pas les fichiers de données — uniquement les métadonnées API.
Scope volontairement limité aux JDD sourcés depuis data.gouv.fr (comme
enrichir_contacts.py) — INSEE/OEB/BDNB/géo partagent le même bug via
_parser_date_http mais n'ont pas de source API uniforme pour un rattrapage
générique ; à traiter séparément si besoin.
"""
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.datagouv import get_dataset_metadata
from connectors.rudi_node import publier_dataset, charger_conf_rudi
from translation.rudi_builder import _parser_date_http

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_EXTRACT_DATAGOUV_ID = re.compile(r"data\.gouv\.fr/datasets/([a-f0-9-]+)")


def _trouver_rudi_metadata():
    """Parcourt data/*/rudi_metadata.json et retourne [(chemin, metadata), ...]."""
    resultats = []
    for chemin in sorted(glob.glob(os.path.join(DATA_DIR, "*", "rudi_metadata.json"))):
        try:
            with open(chemin, encoding="utf-8") as f:
                meta = json.load(f)
            resultats.append((chemin, meta))
        except Exception as e:
            print(f"⚠ rudi_metadata.json illisible, ignoré : {chemin} ({e})")
    return resultats


def _dataset_id_depuis_metadata(metadata: dict) -> str | None:
    """Extrait le dataset ID data.gouv.fr depuis metadata_info.metadata_source."""
    url = (metadata.get("metadata_info") or {}).get("metadata_source", "")
    m = _EXTRACT_DATAGOUV_ID.search(url)
    return m.group(1) if m else None


def _mettre_a_jour_dates(metadata: dict, api_meta: dict) -> bool:
    """Recalcule dataset_dates depuis l'API avec le parseur corrigé. Retourne True si modifié."""
    ancien = metadata.get("dataset_dates", {})
    nouveau = dict(ancien)

    created = _parser_date_http(api_meta.get("created_at"))
    if created:
        nouveau["created"] = created
    updated = _parser_date_http(api_meta.get("last_modified"))
    if updated:
        nouveau["updated"] = updated

    if nouveau == ancien:
        return False
    metadata["dataset_dates"] = nouveau
    return True


def enrichir(dry_run: bool = False) -> None:
    tous = _trouver_rudi_metadata()
    print(f"{len(tous)} rudi_metadata.json trouvés\n")

    a_traiter = []
    for chemin, meta in tous:
        ds_id = _dataset_id_depuis_metadata(meta)
        if ds_id:
            a_traiter.append((chemin, meta, ds_id))

    print(f"{len(a_traiter)} avec source data.gouv.fr identifiable\n")

    if not a_traiter:
        print("Rien à faire.")
        return

    modifies, echecs, identiques = 0, 0, 0
    conf_rudi = charger_conf_rudi() if not dry_run else None

    for i, (chemin, meta, ds_id) in enumerate(a_traiter, 1):
        titre = meta.get("resource_title", "?")[:60]
        dossier = os.path.basename(os.path.dirname(chemin))
        print(f"[{i}/{len(a_traiter)}] {dossier} — {titre}")

        try:
            api_meta = get_dataset_metadata(ds_id)
        except Exception as e:
            print(f"  ✗ Erreur API : {e}")
            echecs += 1
            time.sleep(0.5)
            continue

        avant = dict(meta.get("dataset_dates", {}))
        modifie = _mettre_a_jour_dates(meta, api_meta)
        if not modifie:
            print(f"  — Inchangé")
            identiques += 1
            time.sleep(0.3)
            continue

        print(f"  → {avant} -> {meta['dataset_dates']}")

        if not dry_run:
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            if conf_rudi:
                try:
                    fichiers = [
                        os.path.join(os.path.dirname(chemin), m["media_name"])
                        for m in meta.get("available_formats", [])
                        if m.get("media_type") == "FILE"
                        and os.path.isfile(os.path.join(os.path.dirname(chemin), m["media_name"]))
                    ]
                    publier_dataset(conf=conf_rudi, rudi_metadata=meta,
                                    fichiers_filtres=fichiers)
                    print(f"  ✓ Republié")
                except Exception as e:
                    print(f"  ✗ Erreur publication : {e}")
                    echecs += 1

        modifies += 1
        time.sleep(0.3)

    print(f"\n=== Terminé ===")
    print(f"  Modifiés  : {modifies}")
    print(f"  Inchangés : {identiques}")
    print(f"  Échecs    : {echecs}")


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("=== Mode dry-run (aucune modification) ===\n")
    else:
        print("=== Rattrapage des dates ===\n")
    enrichir(dry_run=dry_run)


if __name__ == "__main__":
    main()
