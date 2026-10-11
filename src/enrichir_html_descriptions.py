"""
Régénère `summary[].html` des JDD déjà moissonnés, à partir du `summary[].text` déjà sur
disque, via le convertisseur Markdown léger (rudi_harvest_toolkit.markdown).

Contexte : le portail (depuis sa v3.3.2) affiche le résumé long d'un JDD en injectant
`summary[].html ?? summary[].text` tel quel (innerHTML). Les moissons antérieures à
l'introduction de ce traitement (voir rudi_harvest_toolkit.pivot::construire_rudi_metadata) n'ont
jamais rempli `html` : la fiche catalogue affichait donc le `text` brut, Markdown source
compris (gras « **...** », puces « - ... », liens, et retours à la ligne perdus — le
rendu HTML ignore les \n hors balises). Les moissons futures appliquent déjà la
conversion automatiquement — ce script ne sert qu'à rattraper l'historique.

Travaille uniquement à partir des rudi_metadata.json déjà sur disque : ne retélécharge
ni ne refiltre rien, et ne republie pas sur le nœud RUDI (à faire ensuite via
publish_rudi.py). Idempotent : recalcule le même HTML à chaque passage depuis le même
texte, donc un JDD déjà à jour n'est pas retouché.

Usage :
  python3 src/enrichir_html_descriptions.py                     # dry-run : liste ce qui serait changé
  python3 src/enrichir_html_descriptions.py --appliquer          # écrit réellement
  python3 src/enrichir_html_descriptions.py --appliquer --dossier <nom>   # un seul dossier
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.datasets import DATASETS_GEO
from rudi_harvest_toolkit.state import charger_etat, sauvegarder_etat, construire_index_dossier
from rudi_harvest_toolkit.markdown import markdown_vers_html

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_TAB_FILE = os.path.join(DATA_DIR, "state.json")
STATE_INSEE_FILE = os.path.join(DATA_DIR, "state_insee.json")
STATE_OEB_FILE = os.path.join(DATA_DIR, "state_oeb.json")
STATE_BDNB_FILE = os.path.join(DATA_DIR, "state_bdnb.json")
STATE_GEO_FILE = os.path.join(DATA_DIR, "state_geo.json")


def enrichir_un(dossier_nom: str, appliquer: bool) -> str | None:
    """Recalcule summary[0].html d'un dossier. Retourne un message si changé, None sinon."""
    dossier = os.path.join(DATA_DIR, dossier_nom)
    chemin_meta = os.path.join(dossier, "rudi_metadata.json")
    if not os.path.isfile(chemin_meta):
        return None
    with open(chemin_meta, encoding="utf-8") as f:
        meta = json.load(f)
    if not meta.get("summary"):
        return None

    texte = meta["summary"][0].get("text", "")
    nouveau_html = markdown_vers_html(texte)
    ancien_html = meta["summary"][0].get("html", "")
    if nouveau_html == ancien_html:
        return None

    if appliquer:
        if nouveau_html:
            meta["summary"][0]["html"] = nouveau_html
        else:
            meta["summary"][0].pop("html", None)
        with open(chemin_meta, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    etat = "ajouté" if not ancien_html else "mis à jour"
    return f"{dossier_nom} : html {etat} ({len(nouveau_html)} caractères)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--appliquer", action="store_true", help="Écrit réellement (sinon dry-run)")
    parser.add_argument("--dossier", help="Ne traiter qu'un seul dossier (nom sous data/)")
    args = parser.parse_args()

    state_tab = charger_etat(STATE_TAB_FILE)
    state_insee = charger_etat(STATE_INSEE_FILE)
    state_oeb = charger_etat(STATE_OEB_FILE)
    state_bdnb = charger_etat(STATE_BDNB_FILE)
    state_geo = charger_etat(STATE_GEO_FILE)
    geo_deja_publie = state_geo.setdefault("_rudi_publie", {})
    dossiers_geo = {c["dossier"] for c in DATASETS_GEO}

    index = construire_index_dossier(
        ("tabulaire", state_tab), ("insee", state_insee),
        ("oeb", state_oeb), ("bdnb", state_bdnb),
    )
    etats = {"tabulaire": state_tab, "insee": state_insee, "oeb": state_oeb, "bdnb": state_bdnb}

    if args.dossier:
        dossiers = [args.dossier]
    else:
        dossiers = sorted(
            n for n in os.listdir(DATA_DIR)
            if n != "cache" and os.path.isdir(os.path.join(DATA_DIR, n))
        )

    mode = "APPLIQUER" if args.appliquer else "dry-run"
    print(f"=== Rattrapage summary[].html ({mode}) — {len(dossiers)} dossier(s) à vérifier ===\n")

    n_modifies = 0
    n_republier = 0
    n_hors_scope = 0
    for nom in dossiers:
        cle = None
        if nom in dossiers_geo:
            source = "geo"
        elif nom in index:
            source, cle = index[nom]
        else:
            n_hors_scope += 1
            continue

        try:
            resultat = enrichir_un(nom, args.appliquer)
        except Exception as e:
            print(f"  {nom} : ERREUR — {e}")
            continue
        if resultat:
            print(f"  {resultat}")
            n_modifies += 1
            if args.appliquer:
                if source == "geo":
                    if geo_deja_publie.get(nom):
                        geo_deja_publie[nom] = False
                        n_republier += 1
                elif cle is not None and etats[source][cle].get("rudi_publie"):
                    etats[source][cle]["rudi_publie"] = False
                    n_republier += 1

    if args.appliquer:
        sauvegarder_etat(STATE_TAB_FILE, state_tab)
        sauvegarder_etat(STATE_INSEE_FILE, state_insee)
        sauvegarder_etat(STATE_OEB_FILE, state_oeb)
        sauvegarder_etat(STATE_BDNB_FILE, state_bdnb)
        sauvegarder_etat(STATE_GEO_FILE, state_geo)

    verbe = "modifié(s)" if args.appliquer else "à modifier"
    print(f"\n=== Terminé : {n_modifies} summary.html {verbe} sur {len(dossiers)} dossier(s) "
          f"({n_hors_scope} hors scope) ===")
    if n_republier:
        print(f"{n_republier} JDD déjà publié(s) sur le nœud RUDI ont été démarqués "
              f"(rudi_publie remis à false) — le html ne sera envoyé au nœud qu'après "
              f"un nouveau passage de publish_rudi.py.")
    if n_modifies and not args.appliquer:
        print("Relancer avec --appliquer pour écrire ces changements.")
    elif n_modifies:
        print("Pensez à régénérer le catalogue (catalogue.py) puis à republier (publish_rudi.py).")


if __name__ == "__main__":
    main()
