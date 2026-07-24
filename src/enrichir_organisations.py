"""
Enrichissement des descriptions d'organisations productrices sur le nœud RUDI.

Rattrapage pour les organisations déjà publiées qui n'ont pas de
`organization_summary`. Ne re-moissonne ni ne re-publie les JDD.

Usage :
  python3 src/enrichir_organisations.py --dry-run  # affiche sans modifier (défaut)
  python3 src/enrichir_organisations.py            # applique les modifications
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.rudi_node import charger_conf_rudi, _creer_writer
from translation.organisation_secours import enrichir_organisation


def enrichir(dry_run: bool = False, force: bool = False) -> None:
    conf = charger_conf_rudi()
    if not conf:
        print("Configuration RUDI absente (src/conf/rudi_node.json). Rien à faire.")
        return

    writer = _creer_writer(conf)
    orgs = writer.organization_list
    print(f"{len(orgs)} organisation(s) sur le nœud RUDI\n")

    a_enrichir = []
    for org in orgs:
        nom = org.get("organization_name", "")
        summary = org.get("organization_summary", "")
        if not force and summary:
            continue
        a_enrichir.append(org)

    print(f"{len(a_enrichir)} à enrichir" + (" (mode force)" if force else " (summary vide)") + "\n")

    if not a_enrichir:
        print("Rien à faire.")
        return

    modifies, echecs, identiques = 0, 0, 0

    for i, org in enumerate(a_enrichir, 1):
        nom = org.get("organization_name", "?")
        print(f"[{i}/{len(a_enrichir)}] {nom}")

        result = enrichir_organisation(nom)
        if not result:
            print(f"  — Aucun enrichissement disponible")
            identiques += 1
            time.sleep(0.3)
            continue

        caption = result.get("organization_caption", "")
        summary = result.get("organization_summary", "")
        print(f"  caption : {caption[:80]}")
        print(f"  summary : {summary[:80]}...")

        if dry_run:
            modifies += 1
            time.sleep(0.3)
            continue

        org_mise_a_jour = {
            **org,
            "organization_caption": caption,
            "organization_summary": summary,
        }
        try:
            writer.connector.put_catalog("organizations", org_mise_a_jour)
            modifies += 1
            print(f"  ✓ Mis à jour sur le nœud")
        except Exception as e:
            print(f"  ✗ Erreur : {e}")
            echecs += 1

        time.sleep(0.3)

    print(f"\n=== Terminé ===")
    print(f"  Enrichis : {modifies}")
    if identiques:
        print(f"  Inchangés : {identiques}")
    if echecs:
        print(f"  Échecs    : {echecs}")


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    if dry_run:
        print("=== Mode dry-run (aucune modification) ===\n")
    elif force:
        print("=== Enrichissement des organisations (mode force) ===\n")
    else:
        print("=== Enrichissement des organisations ===\n")
    enrichir(dry_run=dry_run, force=force)


if __name__ == "__main__":
    main()
