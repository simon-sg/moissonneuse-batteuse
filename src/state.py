"""Gestion de l'état propre à ce repo : ``data/state.json`` (moisson tabulaire).

Les fonctions génériques de gestion d'état (``charger_etat``, ``sauvegarder_etat``,
``dataset_a_change``, ``construire_index_dossier``) ont migré vers
``rudi_harvest_toolkit.state`` — ne restent ici que les raccourcis liés au fichier
``data/state.json`` de ce dépôt.
"""
import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "state.json")


def charger_state() -> dict:
    """Charge l'état depuis data/state.json. Retourne {} si le fichier n'existe pas ou est corrompu."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[state] {STATE_FILE} illisible ou corrompu ({e}), repart d'un état vide.")
        return {}


def sauvegarder_state(state: dict) -> None:
    """Sauvegarde l'état dans data/state.json."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
