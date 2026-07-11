import csv
csv.field_size_limit(10_000_000)
import sys
import os
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.datagouv import get_dataset_metadata, find_resource_by_format, download_resource
from connectors.rudi_publish import publier_si_configue
from connectors.sirene import obtenir_sirens_rm
from filters.geographic import filter_json_by_postal_codes, load_json, save_json
from filters.harvest import (
    _detecter_delimiteur, _detecter_encodage, _ligne_est_rm,
)
from harvest_batch import _resoudre_champs
from translation.datagouv_to_rudi import traduire_metadonnees
from state import charger_state, sauvegarder_state, dataset_a_change
from conf.datasets import DATASETS

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _filtrer_csv(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse,
                 champ_siren=None, champ_epci=None, champ_lat=None,
                 champ_lon=None, champ_circonscription=None) -> tuple[list[dict], list[str]]:
    """Filtre un CSV en streaming ligne par ligne — ne charge pas le fichier entier en mémoire."""
    sirens_rm = obtenir_sirens_rm() if champ_siren else None
    encoding = _detecter_encodage(chemin)
    with open(chemin, encoding=encoding, errors="replace", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiteur = _detecter_delimiteur(sample)
        reader = csv.DictReader(f, delimiter=delimiteur)
        entetes = list(reader.fieldnames or [])
        cp, vil, iris, adr, sir, epci, lat, lon, circo, dep = _resoudre_champs(
            entetes, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
            champ_epci, champ_lat, champ_lon, champ_circonscription, None)
        lignes = [{k: v for k, v in row.items() if k is not None}
                  for row in reader
                  if _ligne_est_rm(row, cp, vil, iris, adr, sir, sirens_rm, epci, lat, lon, circo, dep)]
    return lignes, entetes


def traiter_dataset(config: dict, state: dict) -> dict:
    """
    Exécute le pipeline complet pour un jeu de données :
    récupération → téléchargement → filtrage → traduction → nettoyage.
    Supporte les ressources JSON (ancien chemin) et CSV (nouveau).
    Met à jour et retourne l'état.
    """
    dataset_id = config["dataset_id"]
    dossier = os.path.join(DATA_DIR, config["dossier"])
    os.makedirs(dossier, exist_ok=True)

    rudi_metadata_file = os.path.join(dossier, "rudi_metadata.json")

    champ_cp = config.get("champ_cp")
    champ_ville = config.get("champ_ville")
    champ_iris = config.get("champ_iris")
    champ_adresse = config.get("champ_adresse")
    champ_siren = config.get("champ_siren")
    champ_epci = config.get("champ_epci")
    champ_lat = config.get("champ_lat")
    champ_lon = config.get("champ_lon")
    champ_circonscription = config.get("champ_circonscription")
    theme = config.get("theme")

    # Étape 1 : métadonnées (appel léger, ~1 Ko)
    print(f"  Récupération des métadonnées...")
    metadata = get_dataset_metadata(dataset_id)
    last_modified = metadata.get("last_modified", "")
    print(f"  Dernière modification source : {last_modified}")

    # Étape 2 : vérification si changement
    if not dataset_a_change(state, dataset_id, last_modified):
        print(f"  Aucun changement détecté, on passe.")
        return state

    print(f"  Changement détecté, lancement du pipeline...")

    # Étape 3 : recherche de la ressource (JSON en priorité, sinon CSV)
    resource_json = find_resource_by_format(metadata, fmt="json", title_contains=".json")
    resource_csv = find_resource_by_format(metadata, fmt="csv")

    if resource_json:
        source_file = os.path.join(dossier, "source.json")
        filtered_file = os.path.join(dossier, "filtered.json")
        download_resource(resource_json["url"], source_file)
        data = load_json(source_file)
        print(f"  {len(data)} enregistrements au total (France entière)")
        filtered = filter_json_by_postal_codes(
            data,
            ville_field=champ_ville or "ville",
            postal_code_field=champ_cp or "cp",
        )
        print(f"  {len(filtered)} enregistrements après filtrage Rennes Métropole")
        save_json(filtered, filtered_file)
        entetes_colonnes = list(filtered[0].keys()) if filtered else None
        fichiers_filtres = [filtered_file]
        nb_rm = len(filtered)
        os.remove(source_file)
    elif resource_csv:
        source_file = os.path.join(dossier, "source.csv")
        filtered_file = os.path.join(dossier, "filtered.csv")
        download_resource(resource_csv["url"], source_file)
        lignes, entetes = _filtrer_csv(
            source_file, champ_cp, champ_ville, champ_iris, champ_adresse,
            champ_siren, champ_epci, champ_lat, champ_lon, champ_circonscription,
        )
        print(f"  {len(lignes)} lignes RM après filtrage Rennes Métropole")
        if lignes:
            with open(filtered_file, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=entetes)
                writer.writeheader()
                writer.writerows(lignes)
        entetes_colonnes = entetes if lignes else None
        fichiers_filtres = [filtered_file] if lignes else []
        nb_rm = len(lignes)
        os.remove(source_file)
    else:
        print(f"  Erreur : aucune ressource JSON ou CSV trouvée pour {dataset_id}.")
        return state

    # Étape 5 : traduction des métadonnées au format RUDI
    rudi_metadata = traduire_metadonnees(metadata, theme=theme,
                                          entetes_colonnes=entetes_colonnes)
    with open(rudi_metadata_file, "w", encoding="utf-8") as f:
        json.dump(rudi_metadata, f, ensure_ascii=False, indent=2)
    print(f"  Métadonnées RUDI sauvegardées.")

    # Étape 6 : publication sur le nœud RUDI
    rudi_publie = publier_si_configue(rudi_metadata, fichiers_filtres)

    # Étape 7 : mise à jour de l'état
    state[dataset_id] = {
        "last_modified": last_modified,
        "last_harvested": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nb_enregistrements_rm": nb_rm,
        "dossier": config["dossier"],
        "rudi_publie": rudi_publie,
    }

    return state


def main():
    print("=== Démarrage du moissonnage ===")
    print(f"{len(DATASETS)} jeu(x) de données configuré(s)\n")

    state = charger_state()

    for config in DATASETS:
        print(f"--- {config['dataset_id']} ---")
        state = traiter_dataset(config, state)
        sauvegarder_state(state)
        print()

    print("=== State sauvegardé ===")
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
