"""
Utilitaires de filtrage géographique pour Rennes Métropole.

Contient les fonctions de détection RM partagées entre discover.py (découverte),
harvest_batch.py (moisson batch) et tout autre module qui en a besoin.
"""

import json
import re
import sys
import os
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conf.communes_rm import (
    COMMUNES_RM, CODES_POSTAUX_RENNES, CIRCONSCRIPTIONS_RM,
    CODES_POSTAUX_RM, CODES_INSEE_RM, BBOX_RM, DEPARTEMENTS_RM,
)
from conf.insee_cp_35 import (
    COMMUNES_35, INSEE_35, CP_35, INSEE_SEULEMENT_35, CP_SEULEMENT_35,
    NOMS_35_VERS_INSEE,
)


def normaliser(texte: str) -> str:
    texte = texte.lower().strip()
    texte = texte.replace("_", " ").replace("-", " ")
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return texte


# Table de correspondance normalisée : nom normalisé -> code postal
_COMMUNES_NORMALISEES = {normaliser(nom): cp for nom, cp in COMMUNES_RM.items()}
_CODES_RENNES = set(CODES_POSTAUX_RENNES + ["35000"])


def est_commune_rm(ville: str) -> bool:
    return normaliser(ville) in _COMMUNES_NORMALISEES


def est_dans_rm(ville: str, cp: str) -> bool:
    ville_norm = normaliser(ville)
    if ville_norm == "rennes":
        return cp in _CODES_RENNES
    cp_attendu = _COMMUNES_NORMALISEES.get(ville_norm)
    return cp_attendu is not None and cp_attendu == cp


_RE_NON_DIGIT = re.compile(r"\D+")


def normaliser_circonscription(valeur) -> str | None:
    chiffres = _RE_NON_DIGIT.sub("", str(valeur or ""))
    if len(chiffres) < 3:
        return None
    circo = chiffres[-2:]
    dep = chiffres[:-2].lstrip("0") or "0"
    return f"{dep.zfill(3)}-{circo}"


def est_circonscription_rm(valeur: str) -> bool:
    code = normaliser_circonscription(valeur)
    return code is not None and code in CIRCONSCRIPTIONS_RM


def est_departement_rm(valeur: str) -> bool:
    code = str(valeur).strip().lstrip("0") or "0"
    return code.zfill(3) in DEPARTEMENTS_RM


# ---------------------------------------------------------------------------
# Constantes de détection géographique — partagées entre discover.py et
# harvest_batch.py (et tout autre module qui en a besoin).
# ---------------------------------------------------------------------------

EPCI_SIREN_RM = "243500139"
_COMMUNES_NORM_RM = {normaliser(c) for c in COMMUNES_RM}
_RE_CP_35 = re.compile(r"\b(35\d{3})\b")
_RE_WKT_POINT = re.compile(
    r"^POINT\s*\(\s*(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s*\)$", re.IGNORECASE
)
_RE_WKT_PAIRE = re.compile(r"(-?\d+\.\d+)\s+(-?\d+\.\d+)")

# BBOX unpacking (lon, lat) — ordre cohérent avec BBOX_RM
_RM_LON_MIN, _RM_LAT_MIN, _RM_LON_MAX, _RM_LAT_MAX = BBOX_RM


def _points_depuis_geojson(texte: str) -> list:
    try:
        obj = json.loads(texte)
    except (ValueError, TypeError):
        return []
    if isinstance(obj, dict) and isinstance(obj.get("geometry"), dict):
        obj = obj["geometry"]
    coords = obj.get("coordinates") if isinstance(obj, dict) else None
    if coords is None:
        return []
    points = []

    def _parcourir(node):
        if (isinstance(node, list) and len(node) >= 2
                and all(isinstance(x, (int, float)) for x in node[:2])):
            points.append((node[1], node[0]))
        elif isinstance(node, list):
            for enfant in node:
                _parcourir(enfant)

    _parcourir(coords)
    return points


def est_iris_rm(code: str) -> bool:
    code = str(code).strip()
    if code == EPCI_SIREN_RM:
        return True
    return len(code) >= 5 and code[:5] in CODES_INSEE_RM


# ---------------------------------------------------------------------------
# Classement nature-aware des codes commune (collisions INSEE/CP du dept 35)
# Dans le 35, certains codes INSEE de communes hors RM sont identiques à des
# codes postaux de communes RM (35132 = INSEE Hirel, hors RM, ET CP de
# Vezin-le-Coquet, RM) et inversement (35120 = INSEE Gévezé, RM, ET CP de
# Dol-de-Bretagne, hors RM). L'union aveugle des deux interprétations laissait
# passer des lignes hors RM — d'où le classement ci-dessous.
# ---------------------------------------------------------------------------

_RE_CEDEX = re.compile(r"\s+cedex(\s+\d+)?$")


def _normaliser_ville(ville: str) -> str:
    """normaliser() + suppression du suffixe 'cedex [n]'."""
    return _RE_CEDEX.sub("", normaliser(ville))


def detecter_nature_colonne(valeurs) -> str:
    """Détermine la nature d'une colonne de codes à partir d'un échantillon de valeurs
    (itérable de str). Retourne "insee", "cp" ou "inconnue".
    Ne compte que les valeurs à 5 chiffres commençant par "35" (seul le 35 est
    discriminant pour RM). Règle : majorité 3:1 requise (au moins 3 discriminants
    purs sans opposant), sinon "inconnue"."""
    n_insee = n_cp = 0
    for v in valeurs:
        v = str(v or "").strip()
        if len(v) == 5 and v.isdigit() and v.startswith("35"):
            if v in INSEE_SEULEMENT_35:
                n_insee += 1
            elif v in CP_SEULEMENT_35:
                n_cp += 1
    if n_insee >= 3 * max(n_cp, 1):
        return "insee"
    if n_cp >= 3 * max(n_insee, 1):
        return "cp"
    return "inconnue"


def classer_code_rm(valeur: str, ville: str | None = None) -> str:
    """Classe une valeur de colonne code-commune vis-à-vis de RM.
    Retourne : "rm" (RM quelle que soit l'interprétation), "hors_rm",
    "amb_insee" (RM seulement si la colonne est de nature INSEE — 35120/35210/35240),
    "amb_cp" (RM seulement si la colonne est de nature CP — 35132 et consorts).
    Si `ville` est fournie, elle arbitre les ambigus quand elle désigne une commune
    du 35 corroborée par le code (protection contre les homonymes hors 35)."""
    v = str(valeur).strip()
    if v == EPCI_SIREN_RM:
        return "rm"
    if len(v) >= 5 and v[:5].isdigit() and len(v) != 5:
        # IRIS 9 chiffres, codes suffixés — jamais un CP : pas d'ambiguïté.
        return "rm" if est_iris_rm(v) else "hors_rm"
    if not (len(v) == 5 and v.isdigit()):
        if v and not v.isdigit():
            # Colonnes taguées iris qui contiennent en fait des noms de communes.
            return "rm" if est_commune_rm(v) else "hors_rm"
        return "hors_rm"
    insee_rm = v in CODES_INSEE_RM
    cp_rm = v in CODES_POSTAUX_RM
    if not insee_rm and not cp_rm:
        return "hors_rm"
    if insee_rm and cp_rm:
        return "rm"
    if ville:
        code_ville = NOMS_35_VERS_INSEE.get(_normaliser_ville(str(ville)))
        if code_ville is not None:
            if code_ville not in CODES_INSEE_RM:
                # La ligne dit elle-même être une commune du 35 hors RM (cas Hirel).
                return "hors_rm"
            if v == code_ville or v in COMMUNES_35[code_ville][1]:
                return "rm"
            # Ville RM non corroborée par le code (homonyme possible hors 35,
            # ex. Saint-Armel dans le 56) : ne pas trancher par la ville.
    if insee_rm:
        return "rm" if v not in CP_35 else "amb_insee"
    return "rm" if v not in INSEE_35 else "amb_cp"


def est_code_rm(valeur, ville=None, nature="inconnue") -> bool:
    """Résolution complète : classer_code_rm + tranchage des ambigus par la nature.
    nature "inconnue" → défaut INSEE (sémantique du champ champ_iris : strict,
    on préfère perdre une ligne RM que publier une ligne hors RM)."""
    c = classer_code_rm(valeur, ville)
    if c == "rm":
        return True
    if c == "hors_rm":
        return False
    if c == "amb_insee":
        return nature != "cp"
    return nature == "cp"          # amb_cp


def est_valeur_commune_rm(valeur: str) -> bool:
    """Alias de compatibilité — préférer est_code_rm(valeur, ville, nature),
    qui arbitre les collisions INSEE/CP au lieu d'accepter l'union des deux."""
    return est_code_rm(valeur)


def est_epci_rm(code: str) -> bool:
    return str(code).strip() == EPCI_SIREN_RM


def est_point_rm(lat_val: str, lon_val: str | None) -> bool:
    if lon_val is None:
        texte = str(lat_val).strip()
        if not texte:
            return False
        if texte[0] in "{[":
            points = _points_depuis_geojson(texte)
            return any(_RM_LAT_MIN <= la <= _RM_LAT_MAX and _RM_LON_MIN <= lo <= _RM_LON_MAX
                       for la, lo in points)
        m = _RE_WKT_POINT.match(texte)
        if m:
            lon, lat = float(m.group(1)), float(m.group(2))
        elif texte[:3].upper() in ("POL", "MUL", "LIN", "GEO"):
            paires = _RE_WKT_PAIRE.findall(texte)
            return any(_RM_LAT_MIN <= float(la) <= _RM_LAT_MAX and _RM_LON_MIN <= float(lo) <= _RM_LON_MAX
                       for lo, la in paires)
        else:
            parties = texte.replace(";", ",").split(",")
            if len(parties) < 2:
                return False
            lat, lon = float(parties[0]), float(parties[1])
    else:
        try:
            lat, lon = float(lat_val), float(lon_val)
        except (ValueError, TypeError):
            return False
    return _RM_LAT_MIN <= lat <= _RM_LAT_MAX and _RM_LON_MIN <= lon <= _RM_LON_MAX


def est_adresse_rm(texte: str) -> bool:
    if not texte:
        return False
    for cp in _RE_CP_35.findall(texte):
        if cp in CODES_POSTAUX_RM:
            return True
    texte_norm = normaliser(texte)
    return any(commune in texte_norm for commune in _COMMUNES_NORM_RM)


def filter_json_by_postal_codes(data: list, ville_field: str = "ville", postal_code_field: str = "cp") -> list:
    return [row for row in data if est_dans_rm(str(row.get(ville_field, "")), str(row.get(postal_code_field, "")))]


def load_json(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: list, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Données filtrées sauvegardées : {path} ({len(data)} enregistrements)")
