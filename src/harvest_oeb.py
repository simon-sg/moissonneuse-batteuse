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
import csv
import datetime
import io
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
from connectors.rudi_node import publier_dataset, charger_conf_rudi
from translation.description_secours import generer_complement

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "state_oeb.json")

_PRODUCTEUR = "Observatoire de l'Environnement en Bretagne (OEB)"
_ZONE = "Rennes Métropole"
_BBOX_RM = {
    "bounding_box": {
        "west_longitude": -2.08, "east_longitude": -1.37,
        "south_latitude": 47.89, "north_latitude": 48.27,
    }
}
_LICENCE_ETALAB = {
    "licence_type": "STANDARD",
    "licence_label": "etalab-2.0",
    "licence_uri": "https://www.etalab.gouv.fr/licence-ouverte-open-licence",
}


# ---------------------------------------------------------------------------
# État / cache
# ---------------------------------------------------------------------------

def _charger_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[state_oeb] {STATE_FILE} illisible ({e}), repart d'un état vide.")
            return {}
    return {}


def _sauvegarder_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


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
    titre = f"{titre_source} — {_ZONE}"
    synopsis = f"{titre_source[:100]} — données filtrées sur {_ZONE}."[:150]
    url_source = f"{BASE_URL}/data-fair/dataset/{slug}"

    description_source = info.get("description") or ""
    if len(description_source.strip()) < 40:
        complement = generer_complement(
            theme=theme,
            producteur=_PRODUCTEUR,
            zone=_ZONE,
            colonnes=colonnes,
        )
        description = f"Données OEB filtrées sur {_ZONE}.\n\nSource : {url_source}\n\n{complement}"
    else:
        description = f"{description_source}\n\nDonnées filtrées sur {_ZONE}.\n\nSource : {url_source}"

    local_id = str(uuid.uuid5(uuid.NAMESPACE_URL, url_source))

    media_filtre = {
        "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_source}/filtered")),
        "media_type": "FILE",
        "media_name": nom_csv,
        "media_caption": f"{nom_csv} — données filtrées sur {_ZONE} (CSV)",
        "connector": {
            "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
            "interface_contract": "dwnl",
        },
    }
    media_source = {
        "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_source}/source")),
        "media_type": "SERVICE",
        "media_name": "source-oeb",
        "media_caption": f"Jeu de données complet sur le portail OEB (Bretagne entière)",
        "connector": {
            "url": url_source,
            "interface_contract": "dwnl",
        },
    }

    dates = {}
    updated_at = info.get("updatedAt")
    if updated_at:
        try:
            dates["updated"] = updated_at[:10] + "T00:00:00Z"
        except Exception:
            dates["updated"] = datetime.date.today().isoformat() + "T00:00:00Z"

    mots_cles = ["oeb", "bretagne", _ZONE.lower(), theme]
    for tag in (info.get("keywords") or []):
        if isinstance(tag, str):
            mots_cles.append(tag)

    return {
        "local_id": local_id,
        "resource_title": titre,
        "synopsis": [{"lang": "fr", "text": synopsis}],
        "summary": [{"lang": "fr", "text": description}],
        "theme": theme,
        "keywords": list(dict.fromkeys(mots_cles)),
        "producer": {"organization_name": _PRODUCTEUR},
        "contacts": [],
        "available_formats": [media_filtre, media_source],
        "dataset_dates": dates,
        "storage_status": "online",
        "access_condition": {
            "licence": _LICENCE_ETALAB,
            "confidentiality": {"restricted_access": False, "gdpr_sensitive": False},
        },
        "geography": _BBOX_RM,
        "metadata_info": {"metadata_source": url_source},
    }


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
    champ_code   = config.get("champ_code", "code_territoire")
    champ_echelle = config.get("champ_echelle", "echelle_territoire")
    # champ_echelle=None désactive le filtre par échelle (télécharge tout)
    if "champ_echelle" not in config:
        champ_echelle = "echelle_territoire"

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
    rudi_publie = False
    conf_rudi = charger_conf_rudi()
    if conf_rudi:
        try:
            publier_dataset(conf=conf_rudi, rudi_metadata=rudi_meta,
                            fichiers_filtres=[chemin_csv])
            print(f"  [RUDI] Publié.")
            rudi_publie = True
        except Exception as e:
            print(f"  [RUDI] Erreur publication : {e}")
    else:
        print(f"  [RUDI] rudi_node.json absent — publication ignorée.")

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
        total = data.get("count", total_vu)
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
    state = _charger_state()

    ok, cache, echecs, vides = [], [], [], []
    for config in datasets:
        res = traiter_dataset(config, state)
        _sauvegarder_state(state)

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
