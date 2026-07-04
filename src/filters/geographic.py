import json
import sys
import os
import re
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conf.communes_rm import COMMUNES_RM, CODES_POSTAUX_RENNES, CIRCONSCRIPTIONS_RM


def normaliser(texte: str) -> str:
    """
    Normalise un nom de commune pour la comparaison :
    minuscules, underscores/tirets → espaces, suppression des accents.
    Gère les formats LIBGEO style (SAINT_GILLES, CESSON_SEVIGNE…).
    """
    texte = texte.lower().strip()
    texte = texte.replace("_", " ").replace("-", " ")
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return texte


# Table de correspondance normalisée : nom normalisé -> code postal
_COMMUNES_NORMALISEES = {normaliser(nom): cp for nom, cp in COMMUNES_RM.items()}
# Rennes avec ses 3 codes postaux
_CODES_RENNES = set(CODES_POSTAUX_RENNES + ["35000"])


def est_commune_rm(ville: str) -> bool:
    """
    Vérifie si une commune appartient à Rennes Métropole par le nom seul,
    sans code postal. Utilisé quand le dataset n'a pas de champ CP.
    """
    return normaliser(ville) in _COMMUNES_NORMALISEES


def est_dans_rm(ville: str, cp: str) -> bool:
    """
    Vérifie si une commune appartient à Rennes Métropole
    en croisant le nom de la ville et son code postal.
    """
    ville_norm = normaliser(ville)

    # Cas Rennes : plusieurs codes postaux
    if ville_norm == "rennes":
        return cp in _CODES_RENNES

    # Cas général : le nom normalisé doit être dans les communes RM
    # ET le code postal doit correspondre
    cp_attendu = _COMMUNES_NORMALISEES.get(ville_norm)
    return cp_attendu is not None and cp_attendu == cp


_RE_NON_DIGIT = re.compile(r"\D+")


def normaliser_circonscription(valeur) -> str | None:
    """
    Normalise un code de circonscription législative vers la forme canonique "DDD-NN"
    (département sur 3 chiffres, numéro de circonscription sur 2 chiffres), quel que
    soit le format rencontré dans un JDD réel : "035-01", "35-01", "3501", "35_01"...
    On ne garde que les chiffres ; les 2 derniers forment le numéro de circonscription,
    tout ce qui précède est le département (zéros de tête retirés puis remis à 3
    chiffres, pour matcher le format de CIRCONSCRIPTIONS_RM). Retourne None si moins de
    3 chiffres exploitables.
    """
    chiffres = _RE_NON_DIGIT.sub("", str(valeur or ""))
    if len(chiffres) < 3:
        return None
    circo = chiffres[-2:]
    dep = chiffres[:-2].lstrip("0") or "0"
    return f"{dep.zfill(3)}-{circo}"


def est_circonscription_rm(valeur: str) -> bool:
    """
    Teste si un code de circonscription législative recoupe Rennes Métropole.
    ATTENTION (voir CLAUDE.md "Known limitations") : une circonscription couvre un
    territoire plus large que RM — ce test confirme seulement que la ligne est
    *quelque part* dans une circonscription qui recoupe RM, pas qu'elle est dans une
    des 43 communes. C'est pourquoi ce champ est toujours essayé en dernier recours.
    """
    code = normaliser_circonscription(valeur)
    return code is not None and code in CIRCONSCRIPTIONS_RM


def filter_json_by_postal_codes(data: list, ville_field: str = "ville", postal_code_field: str = "cp") -> list:
    """
    Filtre une liste d'enregistrements pour ne garder que ceux
    appartenant à Rennes Métropole (vérifie nom de commune ET code postal).
    """
    return [row for row in data if est_dans_rm(str(row.get(ville_field, "")), str(row.get(postal_code_field, "")))]


def load_json(path: str) -> list:
    """Charge un fichier JSON et retourne son contenu."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: list, path: str) -> None:
    """Sauvegarde une liste de données en JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Données filtrées sauvegardées : {path} ({len(data)} enregistrements)")
