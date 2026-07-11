"""
Harvest de la BDNB (Base de Données Nationale des Bâtiments) pour le département 35.

Télécharge le ZIP du département (~620 Mo), extrait et filtre les tables pour
les 43 communes de Rennes Métropole, génère rudi_metadata.json.

Usage :
  python3 src/harvest_bdnb.py

Résultats dans data/<dossier>/ :
  batiment_groupe-rennesmetropole.csv
  batiment_groupe_dpe_representatif_logement-rennesmetropole.csv
  batiment_groupe_dpe_statistique_logement-rennesmetropole.csv
  batiment_groupe_dpe_tertiaire-rennesmetropole.csv
  batiment_groupe_dle_elec_multimillesime-rennesmetropole.csv
  batiment_groupe_dle_gaz_multimillesime-rennesmetropole.csv
  batiment_groupe_ffo_bat-rennesmetropole.csv
  rudi_metadata.json

Cache : data/state_bdnb.json — évite un re-téléchargement si le ZIP source est inchangé.
"""
import datetime
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.communes_rm import CODES_INSEE_RM
from conf.datasets import DATASETS_BDNB
from connectors.bdnb import (
    get_zip_info, telecharger_zip,
    extraire_batiments_rm, extraire_table_par_bg_id, sauvegarder_csv,
    TABLES_DEFAUT, CHAMP_BG_ID,
)
from connectors.rudi_publish import publier_si_configue
from translation.description_secours import generer_complement
from state import charger_etat, sauvegarder_etat
from translation.rudi_builder import construire_rudi_metadata, media_filtre

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "state_bdnb.json")

_PRODUCTEUR = "Centre Scientifique et Technique du Bâtiment (CSTB)"
_ZONE = "Rennes Métropole"


def _inchange(config_id: str, last_modified: str | None, content_length: str | None,
              state: dict) -> bool:
    entree = state.get(config_id, {})
    if not entree or not last_modified:
        return False
    return (entree.get("last_modified") == last_modified and
            entree.get("content_length") == content_length)


# ---------------------------------------------------------------------------
# Métadonnées RUDI
# ---------------------------------------------------------------------------

def _generer_rudi_metadata(config: dict, fichiers: list[tuple[str, list[str], int]],
                            last_modified: str | None) -> dict:
    """fichiers : [(nom_csv, colonnes, nb_rm), ...]"""
    config_id = config["id"]
    theme = config.get("theme", "housing")
    millesime = config.get("millesime", "")
    url_source = config.get("url_zip", "https://bdnb.io/download/")
    titre_base = f"Base de Données Nationale des Bâtiments (BDNB{(' ' + millesime) if millesime else ''})"

    colonnes_principales = fichiers[0][1] if fichiers else []
    complement = generer_complement(
        theme=theme, producteur=_PRODUCTEUR, zone=_ZONE, colonnes=colonnes_principales
    )
    description = (
        f"Données BDNB filtrées sur {_ZONE}.\n\n"
        f"Le millésime {millesime} inclut {len(fichiers)} table(s) : "
        + ", ".join(n for n, _, _ in fichiers) + ".\n\n"
        f"Source : {url_source}\n\n" + complement
    )

    medias = [media_filtre(f"bdnb/{config_id}", nom, _ZONE,
                           f"{nom} — données BDNB filtrées sur {_ZONE} (CSV, {nb_rm} bâtiments)")
              for nom, _, nb_rm in fichiers]

    return construire_rudi_metadata(
        local_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"bdnb/{config_id}")),
        titre=f"{titre_base} — {_ZONE}",
        synopsis=f"Données BDNB{(' millésime ' + millesime) if millesime else ''} filtrées sur {_ZONE}."[:150],
        description=description,
        theme=theme,
        keywords=["bdnb", "bâtiment", "dpe", "énergie", _ZONE.lower()],
        producteur_nom=_PRODUCTEUR,
        url_source=url_source,
        url_fiche=url_source,
        medias=medias,
        date_source=last_modified,
        metadata_source_label="bdnb.io",
    )


# ---------------------------------------------------------------------------
# Traitement d'une configuration BDNB
# ---------------------------------------------------------------------------

def traiter_config(config: dict, state: dict) -> dict:
    config_id = config["id"]
    url_zip    = config["url_zip"]
    dossier    = os.path.join(DATA_DIR, config["dossier"])
    os.makedirs(dossier, exist_ok=True)
    millesime  = config.get("millesime", "")
    tables     = config.get("tables", TABLES_DEFAUT)
    exclure_geom = config.get("exclure_geom", True)

    print(f"\n--- BDNB {millesime} ---")

    # 1. Vérifier si le ZIP a changé (HEAD)
    last_modified, content_length = get_zip_info(url_zip)
    if _inchange(config_id, last_modified, content_length, state):
        print(f"  → Cache (inchangé)")
        return {"statut": "cache"}

    # 2. Télécharger le ZIP
    chemin_zip = os.path.join(dossier, f"_tmp_bdnb_{config_id}.zip")
    print(f"  Téléchargement : {url_zip}")
    print(f"  (déjà en cache si le fichier existe et est complet)")
    if os.path.exists(chemin_zip):
        print(f"  → Fichier temporaire déjà présent, on le réutilise.")
    else:
        try:
            telecharger_zip(url_zip, chemin_zip)
        except Exception as e:
            return {"statut": "echec", "raison": f"téléchargement : {e}"}
    print(f"  → {os.path.getsize(chemin_zip) / 1024 / 1024:.0f} Mo téléchargés")

    # 3. Extraire et filtrer les tables
    codes_rm = set(CODES_INSEE_RM)
    fichiers_produits: list[tuple[str, list[str], int]] = []   # (nom_csv, colonnes, nb_rm)
    chemins_csv: list[str] = []
    ids_rm: set[str] = set()

    for nom_table, methode in tables:
        slug = nom_table.removeprefix("csv/").removesuffix(".csv")
        nom_csv = f"{slug}-rennesmetropole.csv"
        chemin_csv = os.path.join(dossier, nom_csv)

        print(f"  Extraction : {nom_table}...", end=" ")
        try:
            if methode == "commune":
                colonnes, lignes, ids_rm = extraire_batiments_rm(
                    chemin_zip, codes_rm, exclure_geom=exclure_geom
                )
            elif methode == "bg_id":
                if not ids_rm:
                    print(f"\n    → Ignoré (aucun batiment_groupe_id RM disponible)")
                    continue
                colonnes, lignes = extraire_table_par_bg_id(
                    chemin_zip, nom_table, ids_rm, exclure_geom=exclure_geom
                )
            else:
                print(f"méthode inconnue : {methode}")
                continue
        except Exception as e:
            print(f"\n    → Erreur : {e}")
            continue

        print(f"{len(lignes)} lignes RM")
        if not lignes:
            continue

        sauvegarder_csv(colonnes, lignes, chemin_csv)
        fichiers_produits.append((nom_csv, colonnes, len(lignes)))
        chemins_csv.append(chemin_csv)

    # 4. Supprimer le ZIP temporaire
    try:
        os.remove(chemin_zip)
        print(f"  → ZIP temporaire supprimé")
    except OSError:
        pass

    if not fichiers_produits:
        return {"statut": "vide", "raison": "aucune table filtrée avec des données RM"}

    # 5. Génération rudi_metadata.json
    rudi_meta = _generer_rudi_metadata(config, fichiers_produits, last_modified)
    chemin_rudi = os.path.join(dossier, "rudi_metadata.json")
    with open(chemin_rudi, "w", encoding="utf-8") as f:
        json.dump(rudi_meta, f, ensure_ascii=False, indent=2)
    print(f"  → rudi_metadata.json ({len(fichiers_produits)} fichier(s))")

    # 6. Publication RUDI
    rudi_publie = publier_si_configue(rudi_meta, chemins_csv)

    # 7. État
    nb_total = sum(nb for _, _, nb in fichiers_produits)
    state[config_id] = {
        "last_modified": last_modified,
        "content_length": content_length,
        "date_harvest": datetime.date.today().isoformat(),
        "nb_rm": nb_total,
        "tables": [nom for nom, _, _ in fichiers_produits],
        "dossier": config["dossier"],
        "rudi_publie": rudi_publie,
    }

    return {"statut": "ok", "nb_rm": nb_total, "fichiers": len(fichiers_produits)}


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def main() -> None:
    if not DATASETS_BDNB:
        print("Aucun JDD BDNB configuré dans DATASETS_BDNB (src/conf/datasets.py).")
        return

    print(f"=== Harvest BDNB — {len(DATASETS_BDNB)} configuration(s) ===\n")
    state = charger_etat(STATE_FILE)

    ok, cache, echecs = [], [], []
    for config in DATASETS_BDNB:
        res = traiter_config(config, state)
        sauvegarder_etat(STATE_FILE, state)

        statut = res["statut"]
        if statut == "ok":
            print(f"  ✓ {config['id']} — {res['nb_rm']} lignes RM ({res['fichiers']} tables)")
            ok.append(config["id"])
        elif statut == "cache":
            cache.append(config["id"])
        else:
            print(f"  ✗ {config['id']} — {statut} : {res.get('raison', '')}")
            echecs.append(config["id"])

    print(f"\n=== Terminé ===")
    print(f"  OK     : {len(ok)}")
    print(f"  Cache  : {len(cache)}")
    print(f"  Échecs : {len(echecs)}")


if __name__ == "__main__":
    main()
