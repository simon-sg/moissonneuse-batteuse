import os
import json

from connectors.http import session

DATAGOUV_API = "https://www.data.gouv.fr/api/1"


def get_dataset_metadata(dataset_id: str) -> dict:
    """
    Récupère les métadonnées d'un jeu de données depuis l'API data.gouv.fr.
    :param dataset_id: l'identifiant ou le slug du jeu de données
    :return: les métadonnées sous forme de dictionnaire
    """
    url = f"{DATAGOUV_API}/datasets/{dataset_id}/"
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def find_resource_by_format(metadata: dict, fmt: str, title_contains: str = None) -> dict | None:
    """
    Cherche une ressource dans les métadonnées selon son format (csv, json...).
    :param metadata: les métadonnées du JDD (retournées par get_dataset_metadata)
    :param fmt: le format cherché (ex: "csv", "json")
    :param title_contains: filtre optionnel sur le titre de la ressource
    :return: la première ressource correspondante, ou None
    """
    for resource in metadata.get("resources", []):
        if resource.get("format", "").lower() == fmt.lower():
            if title_contains is None or title_contains in resource.get("title", "").lower():
                return resource
    return None


def download_resource(url: str, local_path: str) -> str:
    """
    Télécharge un fichier et le sauvegarde localement.
    Affiche la progression en Mo.
    :param url: l'URL du fichier à télécharger
    :param local_path: le chemin local où sauvegarder le fichier
    :return: le chemin local du fichier téléchargé
    """
    print(f"Téléchargement depuis : {url}")
    response = session.get(url, stream=True, timeout=60)
    response.raise_for_status()

    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    total = 0
    with open(local_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):  # 1 Mo par chunk
            f.write(chunk)
            total += len(chunk)
            print(f"  {total / 1024 / 1024:.1f} Mo téléchargés...", end="\r")

    print(f"\nFichier sauvegardé : {local_path} ({total / 1024 / 1024:.1f} Mo)")
    return local_path
