"""
Régénère le `synopsis` (résumé court affiché sur les fiches catalogue/portail) des JDD
déjà moissonnés dont l'ancienne version répétait le titre du JDD en préfixe — ce qui
fait doublon visuel avec le titre affiché juste au-dessus sur les fiches (data-set-card
du portail). Voir translation/description_secours.py::resumer_court() pour la nouvelle
règle (vraie description source si disponible, sinon repli thème/producteur/zone —
jamais le titre).

Travaille uniquement à partir des rudi_metadata.json déjà sur disque : ne retélécharge
ni ne refiltre rien, et ne republie pas sur le nœud RUDI (à faire ensuite via
publish_rudi.py). Idempotent : recalcule le même synopsis à chaque passage, donc un JDD
déjà à jour n'est pas retouché.

Cas particulier OEB : la vraie description source (quand elle existe) est imbriquée dans
le `summary` stocké dans un ordre (contenu puis "Source : url") qui ne permet pas de la
ré-extraire de façon fiable a posteriori — ces JDD retombent systématiquement sur la
phrase de repli thème/producteur/zone plutôt que sur leur description OEB d'origine.
Un nouveau passage de harvest_oeb.py produirait un synopsis plus riche pour ceux-là.

BDNB n'est pas concerné : son synopsis n'a jamais répété le titre.

Usage :
  python3 src/enrichir_synopsis.py                    # dry-run : liste ce qui serait changé
  python3 src/enrichir_synopsis.py --appliquer         # écrit réellement
  python3 src/enrichir_synopsis.py --appliquer --dossier <nom>   # un seul dossier
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.datasets import DATASETS_GEO
from state import charger_etat, sauvegarder_etat, construire_index_dossier
from translation.description_secours import LIBELLES_THEMES, MARQUEUR, partie_descriptive, resumer_court

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_TAB_FILE = os.path.join(DATA_DIR, "state.json")
STATE_INSEE_FILE = os.path.join(DATA_DIR, "state_insee.json")
STATE_OEB_FILE = os.path.join(DATA_DIR, "state_oeb.json")
STATE_BDNB_FILE = os.path.join(DATA_DIR, "state_bdnb.json")
STATE_GEO_FILE = os.path.join(DATA_DIR, "state_geo.json")

_ZONE = "Rennes Métropole"


def _repli(source: str, meta: dict, dossier_nom: str) -> str:
    """Phrase de repli utilisée quand aucune vraie description source n'est exploitable
    — jamais dérivée du titre du JDD (voir docstring du module)."""
    theme = meta.get("theme", "society")
    libelle_theme = LIBELLES_THEMES.get(theme, theme)
    producteur = (meta.get("producer") or {}).get("organization_name", "Producteur inconnu")

    if source == "insee":
        return f"Données INSEE « {libelle_theme} » filtrées sur {_ZONE}."
    if source == "oeb":
        return f"Données OEB « {libelle_theme} » filtrées sur {_ZONE}."
    if source == "geo":
        config = next((c for c in DATASETS_GEO if c.get("dossier") == dossier_nom), None)
        type_service = (config or {}).get("type", "wfs").upper()
        return f"Service {type_service} « {libelle_theme} » de {producteur}, filtré sur {_ZONE}."
    return f"Jeu de données « {libelle_theme} » de {producteur}, filtré sur {_ZONE}."


def _description_source(meta: dict) -> str:
    """Récupère la description originale telle qu'incorporée dans `summary` (sans re-
    télécharger), en retirant le préambule de localisation et un éventuel complément déjà
    généré par generer_complement() (repérable via son marqueur)."""
    if not meta.get("summary"):
        return ""
    reste = partie_descriptive(meta["summary"][0].get("text", ""))
    if MARQUEUR in reste:
        reste = reste.split(MARQUEUR)[0]
    return reste.strip()


def enrichir_un(dossier_nom: str, source: str, appliquer: bool) -> str | None:
    """Recalcule le synopsis d'un dossier. Retourne un message si changé, None sinon."""
    dossier = os.path.join(DATA_DIR, dossier_nom)
    chemin_meta = os.path.join(dossier, "rudi_metadata.json")
    if not os.path.isfile(chemin_meta):
        return None
    with open(chemin_meta, encoding="utf-8") as f:
        meta = json.load(f)
    if not meta.get("synopsis"):
        return None

    # OEB : ordre boilerplate/contenu inversé dans le summary stocké — voir docstring.
    description_src = "" if source == "oeb" else _description_source(meta)
    nouveau = resumer_court(description_src, repli=_repli(source, meta, dossier_nom))

    ancien = meta["synopsis"][0].get("text", "")
    if nouveau == ancien:
        return None

    if appliquer:
        meta["synopsis"][0]["text"] = nouveau
        with open(chemin_meta, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    return f"{dossier_nom} ({source})\n    avant : {ancien}\n    après : {nouveau}"


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
    print(f"=== Rattrapage synopsis ({mode}) — {len(dossiers)} dossier(s) à vérifier ===\n")

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
        if source == "bdnb":
            continue  # synopsis BDNB déjà correct (pas de répétition du titre)

        try:
            resultat = enrichir_un(nom, source, args.appliquer)
        except Exception as e:
            print(f"  {nom} : ERREUR — {e}")
            continue
        if resultat:
            print(resultat)
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
    print(f"\n=== Terminé : {n_modifies} synopsis {verbe} sur {len(dossiers)} dossier(s) "
          f"({n_hors_scope} hors scope) ===")
    if n_republier:
        print(f"{n_republier} JDD déjà publié(s) sur le nœud RUDI ont été démarqués "
              f"(rudi_publie remis à false) — le nouveau synopsis ne sera envoyé au nœud "
              f"qu'après un nouveau passage de publish_rudi.py.")
    if n_modifies and not args.appliquer:
        print("Relancer avec --appliquer pour écrire ces changements.")
    elif n_modifies:
        print("Pensez à régénérer le catalogue (catalogue.py) puis à republier (publish_rudi.py).")


if __name__ == "__main__":
    main()
