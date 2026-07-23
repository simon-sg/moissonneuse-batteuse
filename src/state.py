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


def lire_rudi_publie(entree: dict) -> dict[str, bool]:
    """Lit rudi_publie depuis une entrée d'état, en gérant la migration bool→dict.

    Le « hors périmètre » (un nœud ajouté après coup, à qui on ne repousse pas
    rétroactivement l'historique) s'exprime par l'**absence de la clé de ce nœud
    dans un dict déjà peuplé** — et seulement par là :

        {"docker": True}            → publié sur docker, source hors périmètre
        {"docker": False}           → échec sur docker, à rattraper
        True / False (hérité)       → {"docker": <valeur>}
        absent / None / {} / illisible → {"docker": False}

    Le dernier cas couvre les entrées écrites avant l'existence du flag (27 dans
    state.json au moment de la migration) et les moissons faites avant qu'un nœud
    ne soit configuré : sous l'ancienne logique elles étaient rattrapées, elles
    doivent continuer de l'être. Une valeur illisible retombe sur « à rattraper »
    plutôt que sur « à ignorer » — republier est idempotent, oublier est définitif.
    """
    val = entree.get("rudi_publie")
    if isinstance(val, bool):
        return {"docker": val}
    if isinstance(val, dict) and val:
        return dict(val)
    return {"docker": False}


def ecrire_rudi_publie(entree: dict, resultats: dict[str, bool]) -> None:
    """Écrit le dict rudi_publie multi-nœuds dans une entrée d'état.

    resultats : ``{"docker": True, "source": False}`` — dict complet tel que
    retourné par ``publier_si_configue()``.
    """
    entree["rudi_publie"] = resultats


def compter_publies(etat: dict) -> int:
    """Compte les JDD publiés sur au moins un nœud dans un state dict."""
    n = 0
    for entree in etat.values():
        if isinstance(entree, dict):
            rp = lire_rudi_publie(entree)
            if any(rp.values()):
                n += 1
    return n


def compter_publies_noeud(etat: dict, nom_noeud: str) -> int:
    """Compte les JDD publiés sur un nœud donné."""
    n = 0
    for entree in etat.values():
        if isinstance(entree, dict) and lire_rudi_publie(entree).get(nom_noeud):
            n += 1
    return n


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
