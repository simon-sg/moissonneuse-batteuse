import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "state.json")


def charger_etat(chemin: str) -> dict:
    """Charge un état depuis un fichier JSON. Retourne {} si le fichier n'existe pas ou est corrompu."""
    if not os.path.exists(chemin):
        return {}
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[state] {chemin} illisible ou corrompu ({e}), repart d'un état vide.")
        return {}


def sauvegarder_etat(chemin: str, etat: dict) -> None:
    """Sauvegarde un état dans un fichier JSON."""
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=2)


def charger_state() -> dict:
    """Charge l'état depuis data/state.json."""
    return charger_etat(STATE_FILE)


def sauvegarder_state(state: dict) -> None:
    """Sauvegarde l'état dans data/state.json."""
    sauvegarder_etat(STATE_FILE, state)


def dataset_a_change(state: dict, dataset_id: str, last_modified: str) -> bool:
    """
    Retourne True si le JDD a été modifié depuis le dernier run,
    ou s'il n'a jamais été traité.
    """
    etat_precedent = state.get(dataset_id, {})
    return etat_precedent.get("last_modified") != last_modified


def construire_index_dossier(*etat_pairs: tuple[str, dict]) -> dict[str, tuple[str, str]]:
    """Construit un index dossier → (source, clé) à partir de paires (nom_source, etat_dict).

    Utilisé par publish_rudi.py et enrichir_descriptions.py pour retrouver
    l'état associé à un dossier donné, sans duploguer cette logique.

    Exemple :
        index = construire_index_dossier(
            ("tabulaire", state_tab),
            ("insee", state_insee),
            ("oeb", state_oeb),
            ("bdnb", state_bdnb),
        )
        # index["mon-dossier"] = ("insee", "bic-iris")
    """
    index = {}
    for source, etat in etat_pairs:
        for cle, entree in etat.items():
            dossier = entree.get("dossier")
            if dossier:
                index[dossier] = (source, cle)
    return index
