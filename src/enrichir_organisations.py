"""
Enrichissement des descriptions d'organisations productrices sur le nœud RUDI.

Rattrapage pour les organisations déjà publiées qui n'ont pas de
`organization_summary` (description) et/ou pas de site web (`organization_url`).
Ne re-moissonne ni ne re-publie les JDD.

**Local au nœud uniquement (2026-10-15)** : la propagation au portail
(`demander_mise_a_jour_organisation()`, workflow BPMN `DRAFT_UPDATE`, et le dépôt de
logo qui en dépendait) est désactivée — signalée cassée par l'équipe RUDI elle-même. Ce
script met donc à jour la copie **locale** du nœud uniquement (utile pour toute lecture
directe du nœud, ex. le MCP ou le catalogue local) ; l'UI Strukture/le portail gardent
l'ancienne description tant que cette fonction reste hors service. La bonne pratique
désormais : une organisation doit être complète **dès sa création**
(`demander_creation_organisation()`, nom + description), pas enrichie après coup.

`enrichir()` est aussi **une étape du pipeline complet** (voir cli.ETAPES_PIPELINE) : les
organisations créées/vues par la publication sont ainsi enrichies localement dès le même
run. L'action CLI 13 reste disponible pour un rattrapage ponctuel (avec --dry-run/--force).

**`--organisations`** (nœud externe potentiellement partagé, ex. démo RM — voir
`src/demo_rm_precheck.py`) : sur le nœud de dev dédié, « toutes les organisations du
nœud » veut toujours dire « toutes nos organisations », donc le comportement par défaut
ci-dessus balaie `writer.organization_list` sans restriction. Sur un nœud partagé, ce même
balayage enrichirait aussi des organisations tierces (ex. `IRISA - RM_migration`, qui
appartient au nœud lui-même) dès qu'il leur manque un résumé ou un site web — une écriture
sur une donnée qui n'est pas la nôtre. ``--organisations "Nom1,Nom2"`` restreint
`a_traiter` aux seuls `organization_name` listés (séparés par des virgules).

Usage :
  python3 src/enrichir_organisations.py --dry-run  # affiche sans modifier (défaut)
  python3 src/enrichir_organisations.py            # applique les modifications
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rudi_harvest_toolkit.rudi_client import charger_conf_rudi, _creer_writer
from translation.organisation_secours import enrichir_organisation

CONF_RUDI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json")


def enrichir(dry_run: bool = False, force: bool = False, restreindre_noms: set[str] | None = None) -> None:
    conf = charger_conf_rudi(CONF_RUDI_FILE)
    if not conf:
        print("Configuration RUDI absente (src/conf/rudi_node.json). Rien à faire.")
        return

    writer = _creer_writer(conf)
    orgs = writer.organization_list
    print(f"{len(orgs)} organisation(s) sur le nœud RUDI\n")

    a_traiter = [
        org for org in orgs
        if (force or not (org.get("organization_summary") and org.get("organization_url")))
        and (restreindre_noms is None or org.get("organization_name") in restreindre_noms)
    ]

    print(f"{len(a_traiter)} à traiter"
          + (" (mode force)" if force else " (description ou site web manquant)") + "\n")

    if not a_traiter:
        print("Rien à faire.")
        return

    modifies, echecs = 0, 0

    for i, org in enumerate(a_traiter, 1):
        nom = org.get("organization_name", "?")
        print(f"[{i}/{len(a_traiter)}] {nom}")

        org_mise_a_jour = dict(org)
        desc_modifiee = False
        try:
            result = enrichir_organisation(nom) or {}
        except Exception as e:
            print(f"  description : recherche échouée ({e})")
            result = {}
        # Ne jamais écraser une valeur déjà présente (sauf --force) : un repli factuel
        # générique ne doit pas remplacer une vraie description ou un vrai site web.
        for cle in ("organization_caption", "organization_summary", "organization_url"):
            val = result.get(cle, "")
            if val and (force or not org_mise_a_jour.get(cle)):
                org_mise_a_jour[cle] = val
                desc_modifiee = True
        print(f"  description : {'mise à jour' if desc_modifiee else 'déjà à jour ou aucune source'}")

        if not desc_modifiee:
            print("  ↳ rien de nouveau")
            continue

        if dry_run:
            modifies += 1
            time.sleep(0.3)
            continue

        try:
            writer.connector.put_catalog("organizations", org_mise_a_jour)
            modifies += 1
            print("  ✓ mis à jour sur le nœud (local uniquement, voir docstring)")
        except Exception as e:
            print(f"  ✗ erreur nœud : {e}")
            echecs += 1

        time.sleep(0.3)

    print(f"\n=== Terminé ===")
    print(f"  Enrichis : {modifies}")
    if echecs:
        print(f"  Échecs    : {echecs}")


def _arg_organisations(argv: list[str]) -> set[str] | None:
    if "--organisations" not in argv:
        return None
    i = argv.index("--organisations")
    if i + 1 >= len(argv):
        return None
    return {nom.strip() for nom in argv[i + 1].split(",") if nom.strip()}


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    restreindre_noms = _arg_organisations(sys.argv[1:])
    if dry_run:
        print("=== Mode dry-run (aucune modification) ===\n")
    elif force:
        print("=== Enrichissement des organisations (mode force) ===\n")
    else:
        print("=== Enrichissement des organisations ===\n")
    enrichir(dry_run=dry_run, force=force, restreindre_noms=restreindre_noms)


if __name__ == "__main__":
    main()
