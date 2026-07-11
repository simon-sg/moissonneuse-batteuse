"""
Enrichissement des contacts : met à jour les rudi_metadata.json existants
dont le contact est encore le fallback générique (contact@example.org),
en fetchant les contact_points depuis l'API data.gouv.fr.

Usage :
  python3 src/enrichir_contacts.py          # enrichit + republie
  python3 src/enrichir_contacts.py --dry-run # affiche sans modifier

Ne re-télécharge pas les fichiers de données — uniquement les métadonnées API.
"""
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.contacts import extraire_contacts_datagouv, resoudre_contacts
from connectors.datagouv import get_dataset_metadata
from connectors.rudi_node import publier_dataset, charger_conf_rudi
from translation.datagouv_to_rudi import _local_id_depuis_dataset_id

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_EMAIL_GENERIQUE = "contact@example.org"
_EXTRACT_DATAGOUV_ID = re.compile(r"data\.gouv\.fr/datasets/([a-f0-9-]+)")


def _trouver_rudi_metadata():
    """Parcourt data/*/rudi_metadata.json et retourne [(chemin, metadata), ...]."""
    resultats = []
    for chemin in sorted(glob.glob(os.path.join(DATA_DIR, "*", "rudi_metadata.json"))):
        try:
            with open(chemin, encoding="utf-8") as f:
                meta = json.load(f)
            resultats.append((chemin, meta))
        except Exception:
            pass
    return resultats


def _contact_est_generique(metadata: dict) -> bool:
    """Retourne True si le premier contact est le fallback générique."""
    contacts = metadata.get("contacts", [])
    if not contacts:
        return True
    return contacts[0].get("email") == _EMAIL_GENERIQUE


def _dataset_id_depuis_metadata(metadata: dict) -> str | None:
    """Extrait le dataset ID data.gouv.fr depuis metadata_source."""
    url = (metadata.get("metadata_info") or {}).get("metadata_source", "")
    m = _EXTRACT_DATAGOUV_ID.search(url)
    return m.group(1) if m else None


def _mettre_a_jour_contacts(metadata: dict, contacts_source: list[dict]) -> bool:
    """Met à jour les contacts dans le dict metadata. Retourne True si modifié."""
    ancien = metadata.get("contacts", [])
    nouveau = resoudre_contacts(contacts_source, metadata.get("producer", {}).get("organization_name", ""))
    if nouveau == ancien:
        return False
    metadata["contacts"] = nouveau
    return True


def enrichir(dry_run: bool = False) -> None:
    tous = _trouver_rudi_metadata()
    print(f"{len(tous)} rudi_metadata.json trouvés\n")

    a_enrichir = []
    for chemin, meta in tous:
        if _contact_est_generique(meta):
            ds_id = _dataset_id_depuis_metadata(meta)
            if ds_id:
                a_enrichir.append((chemin, meta, ds_id))

    print(f"{len(a_enrichir)} avec contact générique et source data.gouv.fr\n")

    if not a_enrichir:
        print("Rien à faire.")
        return

    modifes, echecs, identiques = 0, 0, 0
    conf_rudi = charger_conf_rudi() if not dry_run else None

    for i, (chemin, meta, ds_id) in enumerate(a_enrichir, 1):
        titre = meta.get("resource_title", "?")[:60]
        dossier = os.path.basename(os.path.dirname(chemin))
        print(f"[{i}/{len(a_enrichir)}] {dossier} — {titre}")

        # Fetch contact_points depuis l'API data.gouv.fr
        try:
            api_meta = get_dataset_metadata(ds_id)
            contacts_source = extraire_contacts_datagouv(api_meta)
        except Exception as e:
            print(f"  ✗ Erreur API : {e}")
            echecs += 1
            time.sleep(0.5)
            continue

        if not contacts_source:
            print(f"  — Pas de contact exploitable dans l'API")
            identiques += 1
            time.sleep(0.3)
            continue

        modifie = _mettre_a_jour_contacts(meta, contacts_source)
        if not modifie:
            print(f"  — Inchangé")
            identiques += 1
            time.sleep(0.3)
            continue

        contact = meta["contacts"][0]
        print(f"  → {contact['contact_name']} <{contact['email']}>")

        if not dry_run:
            # Sauvegarder le rudi_metadata.json mis à jour
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            # Republier sur le noeud RUDI
            if conf_rudi:
                try:
                    # Reconstruire la liste des fichiers filtrés à partir des available_formats
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

        modifes += 1
        time.sleep(0.3)

    print(f"\n=== Terminé ===")
    print(f"  Modifiés  : {modifes}")
    print(f"  Inchangés : {identiques}")
    print(f"  Échecs    : {echecs}")


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("=== Mode dry-run (aucune modification) ===\n")
    else:
        print("=== Enrichissement des contacts ===\n")
    enrichir(dry_run=dry_run)


if __name__ == "__main__":
    main()
