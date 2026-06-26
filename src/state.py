import json
import os

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "state.json")


def charger_state() -> dict:
    """Charge l'état sauvegardé des runs précédents."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def sauvegarder_state(state: dict) -> None:
    """Sauvegarde l'état après un run."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def dataset_a_change(state: dict, dataset_id: str, last_modified: str) -> bool:
    """
    Retourne True si le JDD a été modifié depuis le dernier run,
    ou s'il n'a jamais été traité.
    """
    etat_precedent = state.get(dataset_id, {})
    return etat_precedent.get("last_modified") != last_modified
