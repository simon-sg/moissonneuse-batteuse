import sys
import os
import json
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.datagouv import get_dataset_metadata, find_resource_by_format, download_resource
from filters.geographic import filter_json_by_postal_codes, load_json, save_json
from translation.datagouv_to_rudi import traduire_metadonnees
from state import charger_state, sauvegarder_state, dataset_a_change
from conf.datasets import DATASETS

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def traiter_dataset(config: dict, state: dict) -> dict:
    """
    Exécute le pipeline complet pour un jeu de données :
    récupération → téléchargement → filtrage → traduction → nettoyage.
    Met à jour et retourne l'état.
    """
    dataset_id = config["dataset_id"]
    dossier = os.path.join(DATA_DIR, config["dossier"])
    os.makedirs(dossier, exist_ok=True)

    source_file = os.path.join(dossier, "source.json")
    filtered_file = os.path.join(dossier, "filtered.json")
    rudi_metadata_file = os.path.join(dossier, "rudi_metadata.json")

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

    # Étape 3 : téléchargement du fichier source
    resource = find_resource_by_format(metadata, fmt="json", title_contains=".json")
    if resource is None:
        print(f"  Erreur : aucune ressource JSON trouvée pour {dataset_id}.")
        return state
    download_resource(resource["url"], source_file)

    # Étape 4 : filtrage sur Rennes Métropole
    data = load_json(source_file)
    print(f"  {len(data)} enregistrements au total (France entière)")
    filtered = filter_json_by_postal_codes(
        data,
        ville_field=config.get("champ_ville", "ville"),
        postal_code_field=config.get("champ_cp", "cp"),
    )
    print(f"  {len(filtered)} enregistrements après filtrage Rennes Métropole")
    save_json(filtered, filtered_file)

    # Étape 5 : traduction des métadonnées au format RUDI
    rudi_metadata = traduire_metadonnees(metadata)
    with open(rudi_metadata_file, "w", encoding="utf-8") as f:
        json.dump(rudi_metadata, f, ensure_ascii=False, indent=2)
    print(f"  Métadonnées RUDI sauvegardées.")

    # Étape 6 : suppression du fichier source (trop lourd à conserver)
    os.remove(source_file)
    print(f"  Fichier source supprimé.")

    # Étape 7 : mise à jour de l'état
    state[dataset_id] = {
        "last_modified": last_modified,
        "last_harvested": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nb_enregistrements_rm": len(filtered),
        "dossier": config["dossier"],
    }

    return state


def main():
    print("=== Démarrage du moissonnage ===")
    print(f"{len(DATASETS)} jeu(x) de données configuré(s)\n")

    state = charger_state()

    for config in DATASETS:
        print(f"--- {config['dataset_id']} ---")
        state = traiter_dataset(config, state)
        print()

    sauvegarder_state(state)
    print("=== State sauvegardé ===")
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
