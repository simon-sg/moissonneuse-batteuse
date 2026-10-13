"""
Enrichissement des descriptions d'organisations productrices sur le nœud RUDI.

Rattrapage pour les organisations déjà publiées qui n'ont pas de
`organization_summary` (description) et/ou pas de site web (`organization_url`,
issu de la propriété Wikidata P856 découverte via Wikipédia). Ne re-moissonne ni
ne re-publie les JDD.

Usage :
  python3 src/enrichir_organisations.py --dry-run  # affiche sans modifier (défaut)
  python3 src/enrichir_organisations.py            # applique les modifications
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rudi_harvest_toolkit.rudi_client import (
    charger_conf_rudi,
    _creer_writer,
    demander_mise_a_jour_organisation,
)
from translation.organisation_secours import enrichir_organisation

CONF_RUDI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json")


def enrichir(dry_run: bool = False, force: bool = False) -> None:
    conf = charger_conf_rudi(CONF_RUDI_FILE)
    if not conf:
        print("Configuration RUDI absente (src/conf/rudi_node.json). Rien à faire.")
        return

    writer = _creer_writer(conf)
    orgs = writer.organization_list
    print(f"{len(orgs)} organisation(s) sur le nœud RUDI\n")

    a_enrichir = []
    for org in orgs:
        # Enrichir tant qu'il manque la description OU le site web ; --force re-enrichit tout.
        a_jour = org.get("organization_summary") and org.get("organization_url")
        if not force and a_jour:
            continue
        a_enrichir.append(org)

    print(f"{len(a_enrichir)} à enrichir"
          + (" (mode force)" if force else " (summary ou url vide)") + "\n")

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

        # Ne jamais écraser une valeur déjà présente (sauf --force) : un repli factuel
        # générique ne doit pas remplacer une vraie description ou un vrai site web.
        caption = (result.get("organization_caption", "") if force
                   else org.get("organization_caption") or result.get("organization_caption", ""))
        summary = (result.get("organization_summary", "") if force
                   else org.get("organization_summary") or result.get("organization_summary", ""))
        url = (result.get("organization_url", "") if force
               else org.get("organization_url") or result.get("organization_url", ""))

        print(f"  caption : {caption[:80]}")
        print(f"  summary : {summary[:80]}...")
        if url:
            print(f"  site web : {url}")

        if dry_run:
            modifies += 1
            time.sleep(0.3)
            continue

        org_mise_a_jour = {
            **org,
            "organization_caption": caption,
            "organization_summary": summary,
        }
        if url:
            org_mise_a_jour["organization_url"] = url
        try:
            writer.connector.put_catalog("organizations", org_mise_a_jour)
            modifies += 1
            print(f"  ✓ Mis à jour sur le nœud")
        except Exception as e:
            print(f"  ✗ Erreur : {e}")
            echecs += 1
            time.sleep(0.3)
            continue

        # Propager au portail : le PUT nœud ci-dessus ne touche que la copie locale,
        # le champ site web de l'UI Strukture ne se remplit que via l'appel portail.
        # Best-effort (no-op si url_portail absent/refusé).
        demander_mise_a_jour_organisation(conf, org_mise_a_jour)

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
