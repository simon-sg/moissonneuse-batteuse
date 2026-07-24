"""
Harvest des JDD de l'Observatoire de l'Environnement en Bretagne (OEB).
Portail data-fair : https://data.bretagne-environnement.fr

Usage :
  python3 src/harvest_oeb.py                  # tous les JDD configurés dans DATASETS_OEB
  python3 src/harvest_oeb.py mon-slug          # un JDD par son id OEB
  python3 src/harvest_oeb.py --decouvrir      # liste les JDD disponibles sur le portail

Résultats :
  data/<dossier>/<slug>-rennesmetropole.csv   — lignes filtrées Rennes Métropole
  data/<dossier>/rudi_metadata.json           — métadonnées RUDI
  data/state_oeb.json                         — cache (évite re-téléchargements)
"""
import datetime
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.datasets import DATASETS_OEB
from connectors.oeb import (
    lister_datasets, get_dataset_info,
    telecharger_lignes_rm, lignes_vers_csv,
    BASE_URL,
)
from connectors.rudi_publish import publier_si_configue
from translation.description_secours import generer_complement
from state import charger_etat, sauvegarder_etat
from translation.rudi_builder import construire_rudi_metadata, media_filtre

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "state_oeb.json")

_PRODUCTEUR = "Observatoire de l'Environnement en Bretagne (OEB)"
_ZONE = "Rennes Métropole"


def _inchange(dataset_id: str, updated_at: str | None, state: dict) -> bool:
    entree = state.get(dataset_id, {})
    if not entree or not updated_at:
        return False
    return entree.get("updated_at") == updated_at


# ---------------------------------------------------------------------------
# Métadonnées RUDI
# ---------------------------------------------------------------------------

def _generer_rudi_metadata(
    config: dict,
    info: dict,
    nom_csv: str,
    nb_rm: int,
    colonnes: list[str],
) -> dict:
    slug = config["id"]
    theme = config.get("theme", "environment")
    titre_source = info.get("title") or config.get("titre") or slug
    url_source = f"{BASE_URL}/data-fair/dataset/{slug}"

    description_source = info.get("description") or ""
    if len(description_source.strip()) < 40:
        complement = generer_complement(
            theme=theme, producteur=_PRODUCTEUR, zone=_ZONE, colonnes=colonnes,
        )
        description = f"Données OEB filtrées sur {_ZONE}.\n\nSource : {url_source}\n\n{complement}"
    else:
        description = f"{description_source}\n\nDonnées filtrées sur {_ZONE}.\n\nSource : {url_source}"

    mots_cles = ["oeb", "bretagne", _ZONE.lower(), theme]
    for tag in (info.get("keywords") or []):
        if isinstance(tag, str):
            mots_cles.append(tag)

    return construire_rudi_metadata(
        local_id=str(uuid.uuid5(uuid.NAMESPACE_URL, url_source)),
        titre=f"{titre_source} — {_ZONE}",
        synopsis=f"{titre_source[:100]} — données filtrées sur {_ZONE}."[:150],
        description=description,
        theme=theme,
        keywords=mots_cles,
        producteur_nom=_PRODUCTEUR,
        url_source=url_source,
        url_fiche=url_source,
        medias=[media_filtre(slug, nom_csv, _ZONE)],
        date_source=info.get("updatedAt"),
        metadata_source_label="portail OEB",
        source_producteur="OEB",
    )


# ---------------------------------------------------------------------------
# Traitement d'un JDD
# ---------------------------------------------------------------------------

def traiter_dataset(config: dict, state: dict) -> dict:
    slug = config["id"]
    dossier = os.path.join(DATA_DIR, config["dossier"])
    os.makedirs(dossier, exist_ok=True)

    print(f"\n--- {config.get('titre') or slug} ---")

    # 1. Métadonnées API
    try:
        info = get_dataset_info(slug)
    except Exception as e:
        return {"statut": "echec", "raison": f"métadonnées API : {e}"}

    updated_at = info.get("updatedAt")

    # 2. Cache : dataset inchangé ?
    if _inchange(slug, updated_at, state):
        nb_rm = state.get(slug, {}).get("nb_rm", "?")
        print(f"  → Cache (inchangé, {nb_rm} ligne(s) RM)")
        return {"statut": "cache", "nb_rm": nb_rm}

    # 3. Téléchargement filtré
    champ_code    = config.get("champ_code", "code_territoire")
    champ_echelle = config.get("champ_echelle", "echelle_territoire")

    print(f"  Téléchargement lignes RM ({champ_echelle}/{champ_code})...")
    try:
        lignes = telecharger_lignes_rm(slug, champ_code=champ_code, champ_echelle=champ_echelle)
    except Exception as e:
        return {"statut": "echec", "raison": f"téléchargement : {e}"}

    if not lignes:
        return {"statut": "vide", "raison": "0 lignes RM après filtrage"}

    # 4. Sauvegarde CSV
    titre_source = info.get("title") or config.get("titre") or slug
    nom_csv = slug + "-rennesmetropole.csv"
    chemin_csv = os.path.join(dossier, nom_csv)
    colonnes = lignes_vers_csv(lignes, chemin_csv)
    print(f"  → {len(lignes)} lignes RM → {chemin_csv}")

    # 5. Métadonnées RUDI
    rudi_meta = _generer_rudi_metadata(config, info, nom_csv, len(lignes), colonnes)
    chemin_rudi = os.path.join(dossier, "rudi_metadata.json")
    with open(chemin_rudi, "w", encoding="utf-8") as f:
        json.dump(rudi_meta, f, ensure_ascii=False, indent=2)
    print(f"  → rudi_metadata.json généré")

    # 6. Publication RUDI
    rudi_publie = publier_si_configue(rudi_meta, [chemin_csv])

    # 7. État
    state[slug] = {
        "updated_at": updated_at,
        "date_harvest": datetime.date.today().isoformat(),
        "nb_rm": len(lignes),
        "dossier": config["dossier"],
        "rudi_publie": rudi_publie,
    }

    return {"statut": "ok", "nb_rm": len(lignes)}


# ---------------------------------------------------------------------------
# Découverte
# ---------------------------------------------------------------------------

def afficher_datasets_disponibles() -> None:
    """Liste tous les JDD publiés sur le portail OEB."""
    print("=== JDD disponibles sur le portail OEB ===\n")
    page, total_vu = 0, 0
    while True:
        data = lister_datasets(taille=100, page=page)
        resultats = data.get("results", [])
        if not resultats:
            break
        for jdd in resultats:
            slug = jdd.get("id", "")
            titre = jdd.get("title", "")
            desc = (jdd.get("description") or "")[:80]
            updated = (jdd.get("updatedAt") or "")[:10]
            print(f"  {slug}")
            print(f"    {titre}")
            if desc:
                print(f"    {desc}...")
            print(f"    Mis à jour : {updated}\n")
        total_vu += len(resultats)
        total = data.get("total", total_vu)
        if total_vu >= total:
            break
        page += 1
    print(f"Total : {total_vu} JDD")


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]

    if "--decouvrir" in args:
        afficher_datasets_disponibles()
        return

    ids_demandes = set(a for a in args if not a.startswith("--"))
    datasets = [
        c for c in DATASETS_OEB
        if not ids_demandes or c["id"] in ids_demandes
    ]
    if ids_demandes and not datasets:
        ids_valides = [c["id"] for c in DATASETS_OEB]
        print(f"ID(s) inconnu(s). IDs configurés : {', '.join(ids_valides)}")
        sys.exit(1)

    if not datasets:
        print("Aucun JDD OEB configuré dans DATASETS_OEB (src/conf/datasets.py).")
        print("Lancez avec --decouvrir pour voir les JDD disponibles.")
        return

    print(f"=== Harvest OEB — {len(datasets)} JDD configuré(s) ===\n")
    state = charger_etat(STATE_FILE)

    ok, cache, echecs, vides = [], [], [], []
    for config in datasets:
        res = traiter_dataset(config, state)
        sauvegarder_etat(STATE_FILE, state)

        statut = res["statut"]
        if statut == "ok":
            print(f"  ✓ {config['id']} — {res['nb_rm']} lignes RM")
            ok.append(config["id"])
        elif statut == "cache":
            cache.append(config["id"])
        elif statut == "vide":
            print(f"  ! {config['id']} — vide : {res['raison']}")
            vides.append(config["id"])
        else:
            print(f"  ✗ {config['id']} — ÉCHEC : {res['raison']}")
            echecs.append(config["id"])

    print(f"\n=== Terminé ===")
    print(f"  OK     : {len(ok)}")
    print(f"  Cache  : {len(cache)}")
    print(f"  Vides  : {len(vides)}")
    print(f"  Échecs : {len(echecs)}")


if __name__ == "__main__":
    main()
