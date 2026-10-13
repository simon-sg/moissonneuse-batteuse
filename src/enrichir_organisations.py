"""
Enrichissement des descriptions d'organisations productrices sur le nœud RUDI.

Rattrapage pour les organisations déjà publiées qui n'ont pas de
`organization_summary` (description) et/ou pas de site web (`organization_url`,
issu de la propriété Wikidata P856 découverte via Wikipédia). Ajoute aussi, quand
Wikidata porte un logo (P154), l'image d'organisation côté portail (téléversée puis
rattachée à la demande de modification). Ne re-moissonne ni ne re-publie les JDD.

`enrichir()` est aussi **une étape du pipeline complet** (voir cli.ETAPES_PIPELINE) :
les organisations créées/vues par la publication sont ainsi enrichies — description et
logo — dès le même run, sans action manuelle. L'action CLI 13 reste disponible pour un
rattrapage ponctuel (avec --dry-run/--force).

Usage :
  python3 src/enrichir_organisations.py --dry-run  # affiche sans modifier (défaut)
  python3 src/enrichir_organisations.py            # applique les modifications
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rudi_harvest_toolkit.rudi_client import (
    charger_conf_rudi,
    _creer_writer,
    demander_mise_a_jour_organisation,
    televerser_piece_jointe,
)
from translation.organisation_secours import enrichir_organisation, logo_wikipedia_pour
from connectors.wikipedia import telecharger_logo

CONF_RUDI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
# État des logos déjà soumis au portail (par `organization_id`) : le portail n'expose pas
# « a déjà un logo personnalisé » (la route média renvoie toujours *un* logo, par défaut le
# cas échéant), donc sans cet état chaque passage re-déposerait la même image.
_ETAT_LOGO_FILE = os.path.join(_DATA_DIR, "etat_organisations_logo.json")


def _charger_etat_logo() -> set:
    """Ensemble des `organization_id` dont le logo a déjà été soumis au portail."""
    try:
        with open(_ETAT_LOGO_FILE, encoding="utf-8") as f:
            return set(json.load(f).get("organisations_traitees", []))
    except (OSError, json.JSONDecodeError, AttributeError):
        return set()


def _sauvegarder_etat_logo(ids: set) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_ETAT_LOGO_FILE, "w", encoding="utf-8") as f:
        json.dump({"organisations_traitees": sorted(i for i in ids if i)}, f,
                  ensure_ascii=False, indent=2)


def _nom_fichier_logo(nom: str, type_mime: str) -> str:
    """Nom de fichier de la pièce jointe téléversée (extension cohérente avec le type MIME)."""
    ext = "jpg" if type_mime == "image/jpeg" else "png"
    slug = re.sub(r"[^a-z0-9]+", "-", (nom or "").lower()).strip("-")[:60] or "logo"
    return f"{slug}.{ext}"


def enrichir(dry_run: bool = False, force: bool = False) -> None:
    conf = charger_conf_rudi(CONF_RUDI_FILE)
    if not conf:
        print("Configuration RUDI absente (src/conf/rudi_node.json). Rien à faire.")
        return

    writer = _creer_writer(conf)
    orgs = writer.organization_list
    print(f"{len(orgs)} organisation(s) sur le nœud RUDI\n")

    etat_logo = _charger_etat_logo()
    url_portail = conf.get("url_portail")

    # Une organisation est traitée si sa description/site web est incomplet OU si son logo
    # n'a pas encore été soumis — deux raisons indépendantes (une org peut avoir sa
    # description complète et pas encore de logo). --force re-traite tout le monde.
    a_traiter = []
    for org in orgs:
        org_id = org.get("organization_id")
        manque_desc = force or not (org.get("organization_summary") and org.get("organization_url"))
        manque_logo = bool(url_portail) and bool(org_id) and org_id not in etat_logo
        if manque_desc or manque_logo:
            a_traiter.append(org)

    print(f"{len(a_traiter)} à traiter"
          + (" (mode force)" if force else " (description ou logo manquant)") + "\n")

    if not a_traiter:
        print("Rien à faire.")
        return

    modifies, echecs, logos = 0, 0, 0

    for i, org in enumerate(a_traiter, 1):
        nom = org.get("organization_name", "?")
        org_id = org.get("organization_id")
        print(f"[{i}/{len(a_traiter)}] {nom}")

        # --- Description + site web (fusionnés dans la copie nœud et déposés au portail) ---
        org_mise_a_jour = dict(org)
        desc_modifiee = False
        if force or not (org.get("organization_summary") and org.get("organization_url")):
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

        # Logo (Wikidata P154) : uniquement côté portail (média), et seulement si pas déjà
        # soumis. Inutile de chercher sans portail configuré.
        logo_url = None
        if url_portail and org_id and org_id not in etat_logo:
            try:
                logo_url = logo_wikipedia_pour(nom)
            except Exception as e:
                print(f"  logo : recherche échouée ({e})")
            print(f"  logo : {logo_url or 'aucun trouvé'}")

        if not desc_modifiee and not logo_url:
            # Rien de neuf (description déjà complète, aucun logo disponible) : surtout ne pas
            # redéposer une demande au portail à chaque run.
            print("  ↳ rien de nouveau")
            continue

        if dry_run:
            modifies += 1
            time.sleep(0.3)
            continue

        try:
            writer.connector.put_catalog("organizations", org_mise_a_jour)
            modifies += 1
            print("  ✓ mis à jour sur le nœud")
        except Exception as e:
            print(f"  ✗ erreur nœud : {e}")
            echecs += 1
            time.sleep(0.3)
            continue

        # Téléverser la pièce jointe du logo avant la demande de modification (best-effort) :
        # l'UUID obtenu est transmis comme champ `organizationImage` du formulaire.
        image_id = None
        if logo_url:
            telechargement = telecharger_logo(logo_url)
            if telechargement:
                contenu, type_mime = telechargement
                image_id = televerser_piece_jointe(
                    conf, contenu, _nom_fichier_logo(nom, type_mime), type_mime)
            else:
                print("  ↳ logo ignoré (téléchargement échoué ou format non supporté par le portail)")

        # Propager au portail : le PUT nœud ci-dessus ne touche que la copie locale de
        # l'organisation. Répercuter l'enrichissement (description, site web, logo) dans
        # l'UI Strukture passe par le workflow BPMN officiel de modification (brouillon
        # DRAFT_UPDATE) : cet appel ne fait que *déposer* la demande, un modérateur doit
        # ensuite la valider dans l'UI portail pour qu'elle soit appliquée. Best-effort
        # (no-op si url_portail absent/refusé), ne bloque jamais le rattrapage.
        resultat_portail = demander_mise_a_jour_organisation(conf, org_mise_a_jour, image_id=image_id)
        if resultat_portail is True:
            if image_id:
                etat_logo.add(org_id)
                _sauvegarder_etat_logo(etat_logo)
                logos += 1
                print("  ↳ demande de mise à jour déposée au portail avec logo (validation modérateur requise)")
            else:
                print("  ↳ demande de mise à jour déposée au portail (validation modérateur requise)")
        elif resultat_portail is None:
            print("  ↳ pas de portail configuré : seule la copie locale du nœud est à jour")
        else:
            print("  ↳ mise à jour portail non déposée (voir avertissement ci-dessus)")

        time.sleep(0.3)

    print(f"\n=== Terminé ===")
    print(f"  Enrichis : {modifies}")
    if logos:
        print(f"  Logos soumis au portail : {logos}")
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
