"""
Passe batch sur les candidats de data/decouverte.json.
Pour chaque candidat : téléchargement complet → filtrage RM → filtered.csv + rudi_metadata.json.
Les 23 candidats sans champ de filtrage connu sont sautés (à traiter manuellement).
"""

import csv
import glob
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import unicodedata
import zipfile
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.datagouv import get_dataset_metadata
from connectors.sirene import obtenir_sirens_rm
from filters.geographic import est_dans_rm, est_commune_rm, normaliser
from conf.communes_rm import CODES_POSTAUX_RM, CODES_INSEE_RM, COMMUNES_RM
from translation.datagouv_to_rudi import traduire_metadonnees
from state import charger_state, sauvegarder_state, dataset_a_change

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DECOUVERTE_FILE = os.path.join(DATA_DIR, "decouverte.json")
CACHE_DIR = os.path.join(DATA_DIR, "cache")  # cache partagé avec discover.py


def _chemin_cache(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.md5(url.encode()).hexdigest())

EPCI_SIREN_RM = "243500139"
_RE_CP_35 = re.compile(r"\b(35\d{3})\b")
_COMMUNES_NORM_RM = {normaliser(c) for c in COMMUNES_RM}
_RM_LAT_MIN, _RM_LAT_MAX = 47.80, 48.35
_RM_LON_MIN, _RM_LON_MAX = -2.00, -1.30  # aligné sur discover.py (dérive -2.15 corrigée)


def est_iris_rm(code: str) -> bool:
    code = str(code).strip()
    if code == EPCI_SIREN_RM:
        return True
    return len(code) >= 5 and code[:5] in CODES_INSEE_RM


def est_valeur_commune_rm(valeur: str) -> bool:
    """Teste une colonne 'commune' taguée manuellement en revue (discover.py::
    revue_manuelle_a_examiner()), dont la représentation (code INSEE, IRIS, code postal, ou
    nom en texte) n'est pas connue à l'avance — mirroir de discover.py::est_valeur_commune_rm."""
    v = str(valeur).strip()
    if not v:
        return False
    if v in CODES_POSTAUX_RM:
        return True
    if est_iris_rm(v):
        return True
    return est_commune_rm(v)


def est_epci_rm(code: str) -> bool:
    """Égalité stricte avec le SIREN de l'EPCI RM — mirroir de discover.py::est_epci_rm."""
    return str(code).strip() == EPCI_SIREN_RM


def est_point_rm(lat_val: str, lon_val: str | None) -> bool:
    """Mirroir de discover.py::est_point_rm. Si lon_val is None, lat_val est au format
    combiné "lat,lon"."""
    try:
        if lon_val is None:
            parties = str(lat_val).replace(";", ",").split(",")
            if len(parties) < 2:
                return False
            lat, lon = float(parties[0]), float(parties[1])
        else:
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


def _resoudre_champ(champ: str | None, colonnes_norm: dict) -> str | None:
    """Résout un nom de champ stocké (snake_case ou exact) vers le nom réel dans le CSV.

    La découverte normalise les noms de colonnes (accents, casse, _ → espace) pour la
    détection, mais stocke le nom normalisé, pas l'original. Si la colonne dans le CSV
    s'appelle 'Code Insee commune' et que champ_iris vaut 'code_insee_commune', ils
    sont équivalents après normalisation — on résout ici au nom réel.
    """
    if champ is None:
        return None
    if champ in colonnes_norm.values():
        return champ  # correspondance exacte
    champ_norm = normaliser(champ)
    return colonnes_norm.get(champ_norm, champ)  # fallback : retourne champ tel quel


def _ligne_est_rm(row: dict, champ_cp, champ_ville, champ_iris, champ_adresse,
                   champ_siren=None, sirens_rm=None,
                   champ_epci=None, champ_lat=None, champ_lon=None) -> bool:
    if champ_iris:
        return est_valeur_commune_rm(str(row.get(champ_iris, "")))
    if champ_adresse:
        return est_adresse_rm(str(row.get(champ_adresse, "")))
    if champ_siren:
        val = str(row.get(champ_siren, "")).strip().replace(" ", "")
        return val.isdigit() and len(val) in (9, 14) and val[:9] in sirens_rm
    if champ_epci:
        return est_epci_rm(str(row.get(champ_epci, "")))
    if champ_lat:
        lon_val = str(row.get(champ_lon, "")).strip() if champ_lon else None
        return est_point_rm(str(row.get(champ_lat, "")).strip(), lon_val)
    cp = str(row.get(champ_cp, "")).strip() if champ_cp else ""
    ville = str(row.get(champ_ville, "")).strip() if champ_ville else ""
    if champ_cp and champ_ville:
        return est_dans_rm(ville, cp)
    if champ_ville:
        return est_commune_rm(ville)
    if champ_cp:
        return cp in CODES_POSTAUX_RM
    return False


def _resoudre_champs(fieldnames: list[str], champ_cp, champ_ville, champ_iris,
                     champ_adresse, champ_siren, champ_epci=None, champ_lat=None, champ_lon=None):
    """Résout les noms de champs configurés vers les noms réels des colonnes CSV.

    Retourne un tuple (champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
    champ_epci, champ_lat, champ_lon) avec les noms réels tels qu'ils apparaissent dans le
    fichier, ou les originaux si la résolution échoue.
    """
    norm = {normaliser(k): k for k in fieldnames}
    return (
        _resoudre_champ(champ_cp, norm),
        _resoudre_champ(champ_ville, norm),
        _resoudre_champ(champ_iris, norm),
        _resoudre_champ(champ_adresse, norm),
        _resoudre_champ(champ_siren, norm),
        _resoudre_champ(champ_epci, norm),
        _resoudre_champ(champ_lat, norm),
        _resoudre_champ(champ_lon, norm),
    )


class _Passer(Exception):
    """Levée quand l'utilisateur choisit de passer un dataset pendant le téléchargement."""


def telecharger(url: str) -> str:
    """Télécharge en streaming vers le cache disque. Retourne le chemin du fichier cache."""
    chemin = _chemin_cache(url)
    if os.path.exists(chemin):
        return chemin
    os.makedirs(CACHE_DIR, exist_ok=True)
    r = requests.get(url, timeout=120, stream=True)
    r.raise_for_status()
    total = 0
    chemin_tmp = chemin + ".tmp"
    with open(chemin_tmp, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            total += len(chunk)
    os.rename(chemin_tmp, chemin)
    return chemin


def _detecter_delimiteur(sample: str) -> str:
    """Détecte le délimiteur CSV en comptant les occurrences dans la première ligne."""
    premiere_ligne = sample.split("\n")[0]
    candidats = {d: premiere_ligne.count(d) for d in (";", "\t", "|", ",")}
    # Priorité au délimiteur le plus fréquent (min 2 occurrences = au moins 3 colonnes)
    meilleur = max(candidats, key=candidats.get)
    if candidats[meilleur] >= 2:
        return meilleur
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        return dialect.delimiter
    except csv.Error:
        return ","


def _detecter_encodage_bytes(sample: bytes) -> str:
    """Détecte l'encodage d'un échantillon de bytes (utf-8-sig ou latin-1)."""
    decoded = sample.decode("utf-8-sig", errors="replace")
    return "utf-8-sig" if decoded.count("�") <= 10 else "latin-1"


def _detecter_encodage(chemin: str) -> str:
    """Détecte l'encodage d'un fichier texte (utf-8-sig ou latin-1)."""
    with open(chemin, "rb") as f:
        sample = f.read(8192)
    return _detecter_encodage_bytes(sample)


def filtrer_csv(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse,
                 champ_siren=None, champ_epci=None, champ_lat=None,
                 champ_lon=None) -> tuple[list[dict], list[str]]:
    """Filtre un CSV en streaming ligne par ligne — ne charge pas le fichier entier en mémoire."""
    sirens_rm = obtenir_sirens_rm() if champ_siren else None
    encoding = _detecter_encodage(chemin)
    with open(chemin, encoding=encoding, errors="replace", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiteur = _detecter_delimiteur(sample)
        reader = csv.DictReader(f, delimiter=delimiteur)
        # DictReader lit le header au premier accès à .fieldnames (avant l'itération des lignes).
        entetes = list(reader.fieldnames or [])
        cp, vil, iris, adr, sir, epci, lat, lon = _resoudre_champs(
            entetes, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
            champ_epci, champ_lat, champ_lon)
        lignes = [dict(row) for row in reader
                  if _ligne_est_rm(row, cp, vil, iris, adr, sir, sirens_rm, epci, lat, lon)]
    return lignes, entetes


def filtrer_csv_bytes(contenu: bytes, champ_cp, champ_ville, champ_iris, champ_adresse,
                       champ_siren=None, champ_epci=None, champ_lat=None,
                       champ_lon=None) -> tuple[list[dict], list[str]]:
    """Filtre depuis bytes en mémoire — uniquement pour les membres extraits d'un ZIP."""
    sirens_rm = obtenir_sirens_rm() if champ_siren else None
    encoding = _detecter_encodage_bytes(contenu[:8192])
    texte = contenu.decode(encoding, errors="replace")
    delimiteur = _detecter_delimiteur(texte[:4096])
    reader = csv.DictReader(io.StringIO(texte), delimiter=delimiteur)
    entetes = list(reader.fieldnames or [])
    cp, vil, iris, adr, sir, epci, lat, lon = _resoudre_champs(
        entetes, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
        champ_epci, champ_lat, champ_lon)
    lignes = [dict(row) for row in reader
              if _ligne_est_rm(row, cp, vil, iris, adr, sir, sirens_rm, epci, lat, lon)]
    return lignes, entetes


def filtrer_json(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse,
                  champ_siren=None, champ_epci=None, champ_lat=None,
                  champ_lon=None) -> list[dict]:
    sirens_rm = obtenir_sirens_rm() if champ_siren else None
    with open(chemin, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for cle in ("results", "data", "records", "features"):
            if cle in data and isinstance(data[cle], list):
                rows = data[cle]
                break
        else:
            raise ValueError("Structure JSON non reconnue (pas de liste de lignes)")
    else:
        raise ValueError("Contenu JSON non reconnu (ni liste ni dict)")
    return [row for row in rows
            if _ligne_est_rm(row, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
                             sirens_rm, champ_epci, champ_lat, champ_lon)]


def _extraire_csvs_zip(chemin: str) -> list[tuple[str, bytes]]:
    """Extrait les fichiers CSV d'une archive ZIP. Retourne [(nom_membre, contenu_csv), ...]."""
    with zipfile.ZipFile(chemin) as zf:
        return [
            (nom, zf.read(nom))
            for nom in zf.namelist()
            if nom.lower().endswith(".csv") and not nom.startswith("__MACOSX")
        ]


def filtrer_parquet(url: str, champ_cp, champ_ville, champ_iris, champ_adresse,
                    champ_siren=None, champ_epci=None, champ_lat=None,
                    champ_lon=None) -> tuple[list[dict], list[str]]:
    """Filtre un fichier Parquet distant en HTTP sans téléchargement complet.

    Utilise pyarrow + fsspec pour lire uniquement les row groups dont les statistiques
    min/max indiquent qu'ils peuvent contenir des codes RM (prédicat sur la colonne
    de filtrage principale). Réduit drastiquement les octets lus sur des fichiers
    triés par code commune (ex: Recensement 26M lignes → 1 row group lu sur 25).

    champ_epci/champ_lat/champ_lon ne bénéficient pas du pushdown par statistiques
    min/max (pas de prédicat par plage naturel pour ces types) — tous les row groups
    sont lus dans ce cas, mais le filtrage reste correct.
    """
    try:
        import pyarrow.parquet as pq
        import fsspec
    except ImportError:
        raise RuntimeError("pyarrow et fsspec requis — pip install pyarrow fsspec")

    sirens_rm = obtenir_sirens_rm() if champ_siren else None
    champ_filtre = (champ_iris or champ_cp or champ_siren or champ_ville or champ_adresse
                    or champ_epci or champ_lat)

    with fsspec.open(url, "rb") as f:
        pf = pq.ParquetFile(f)
        schema_names = pf.schema_arrow.names
        md = pf.metadata

        # Résoudre le nom de champ contre les colonnes réelles du schéma
        _, _, iris_r, _, _, _, _, _ = _resoudre_champs(schema_names, champ_cp, champ_ville,
                                               champ_iris, champ_adresse, champ_siren,
                                               champ_epci, champ_lat, champ_lon)
        cp_r, vil_r, _, adr_r, sir_r, _, _, _ = _resoudre_champs(schema_names, champ_cp, champ_ville,
                                                          None, champ_adresse, champ_siren,
                                                          champ_epci, champ_lat, champ_lon)
        champ_filtre_r = iris_r or cp_r or sir_r or vil_r or adr_r or champ_filtre

        # Déterminer les valeurs cibles pour les statistiques de row groups
        if champ_filtre_r == iris_r and iris_r:
            valeurs_cibles = set(CODES_INSEE_RM)
            val_min, val_max = min(valeurs_cibles), max(valeurs_cibles)
        elif champ_filtre_r == cp_r and cp_r:
            valeurs_cibles = set(CODES_POSTAUX_RM)
            val_min, val_max = min(valeurs_cibles), max(valeurs_cibles)
        else:
            valeurs_cibles = None
            val_min = val_max = None

        # Identifier les row groups pertinents via les statistiques min/max
        if val_min is not None:
            rgs_a_lire = []
            for i in range(md.num_row_groups):
                rg = md.row_group(i)
                for j in range(rg.num_columns):
                    col = rg.column(j)
                    if col.path_in_schema == champ_filtre_r and col.statistics:
                        s = col.statistics
                        if str(s.min) <= val_max and str(s.max) >= val_min:
                            rgs_a_lire.append(i)
                        break
                else:
                    rgs_a_lire.append(i)  # pas de stats → lire par précaution
        else:
            rgs_a_lire = list(range(md.num_row_groups))

        print(f"    → parquet : {len(rgs_a_lire)}/{md.num_row_groups} row groups à lire")

        lignes: list[dict] = []
        entetes: list[str] = []
        cp_r = vil_r = iris_r = adr_r = sir_r = epci_r = lat_r = lon_r = None

        for rgi in rgs_a_lire:
            table = pf.read_row_group(rgi)
            if not entetes:
                entetes = table.schema.names
                cp_r, vil_r, iris_r, adr_r, sir_r, epci_r, lat_r, lon_r = _resoudre_champs(
                    entetes, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
                    champ_epci, champ_lat, champ_lon)
            # to_pydict() retourne {col: [valeurs...]} — déjà en RAM pour ce row group
            cols = table.to_pydict()
            n = table.num_rows
            for i in range(n):
                row = {k: str(v[i]) if v[i] is not None else "" for k, v in cols.items()}
                if _ligne_est_rm(row, cp_r, vil_r, iris_r, adr_r, sir_r, sirens_rm,
                                  epci_r, lat_r, lon_r):
                    lignes.append(row)

    return lignes, entetes


def filtrer_gz(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse,
               champ_siren=None, champ_epci=None, champ_lat=None,
               champ_lon=None) -> tuple[list[dict], list[str]]:
    """Décompresse un GZ en streaming et filtre les lignes RM sans tout charger en mémoire."""
    sirens_rm = obtenir_sirens_rm() if champ_siren else None
    with gzip.open(chemin, "rb") as gz:
        sample_bytes = gz.read(8192)
    encoding = _detecter_encodage_bytes(sample_bytes)
    delimiteur = _detecter_delimiteur(sample_bytes.decode(encoding, errors="replace")[:4096])
    with gzip.open(chemin, "rt", encoding=encoding, errors="replace", newline="") as gz:
        reader = csv.DictReader(gz, delimiter=delimiteur)
        entetes = list(reader.fieldnames or [])
        cp, vil, iris, adr, sir, epci, lat, lon = _resoudre_champs(
            entetes, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
            champ_epci, champ_lat, champ_lon)
        lignes = [dict(row) for row in reader
                  if _ligne_est_rm(row, cp, vil, iris, adr, sir, sirens_rm, epci, lat, lon)]
    return lignes, entetes


def filtrer_xlsx(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse,
                  champ_siren=None, champ_epci=None, champ_lat=None,
                  champ_lon=None) -> list[dict]:
    """Extrait et filtre les lignes Rennes Métropole d'un fichier XLSX."""
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl non installé — pip install openpyxl")
    sirens_rm = obtenir_sirens_rm() if champ_siren else None
    # openpyxl vérifie l'extension du chemin, pas les magic bytes — le cache utilise des noms
    # MD5 sans extension, donc on passe les bytes via BytesIO pour contourner cette vérification.
    with open(chemin, "rb") as _f:
        wb = openpyxl.load_workbook(io.BytesIO(_f.read()), read_only=True, data_only=True)
    ws = wb.active
    lignes_brutes = list(ws.iter_rows(values_only=True))
    wb.close()
    if len(lignes_brutes) < 2:
        return []
    entetes = [str(c or "").strip() for c in lignes_brutes[0]]
    cp, vil, iris, adr, sir, epci, lat, lon = _resoudre_champs(
        entetes, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
        champ_epci, champ_lat, champ_lon)
    rows = [
        dict(zip(entetes, [str(v or "").strip() for v in row]))
        for row in lignes_brutes[1:]
    ]
    return [r for r in rows
            if _ligne_est_rm(r, cp, vil, iris, adr, sir, sirens_rm, epci, lat, lon)]


def sauvegarder_csv(lignes: list[dict], chemin: str) -> None:
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=lignes[0].keys())
        writer.writeheader()
        writer.writerows(lignes)


_MOTS_DICT = {
    "dictionnaire", "dict", "codebook", "code book",
    "description des colonnes", "description des champs",
    "nomenclature", "metadonnee", "metadonnees",
}


def _est_dictionnaire_titre(ressource: dict) -> bool:
    titre = normaliser(ressource.get("title", ""))
    desc = normaliser(ressource.get("description", "") or "")
    return any(mot in titre or mot in desc for mot in _MOTS_DICT)


def _est_dictionnaire_contenu(chemin: str) -> bool:
    """Vérifie si la première colonne s'appelle 'Colonne', 'Champ', etc."""
    try:
        with open(chemin, "rb") as f:
            echantillon = f.read(2048)
        texte = echantillon.decode("utf-8-sig", errors="replace")
        premiere_ligne = texte.split("\n")[0]
        delim = _detecter_delimiteur(texte)
        premier_champ = normaliser(premiere_ligne.split(delim)[0])
        return premier_champ in ("colonne", "column", "champ", "field", "variable", "nom", "libelle")
    except Exception:
        return False


def analyser_ressources(metadata: dict) -> dict:
    """Classe les ressources par rôle : données (csv/zip/gz/xlsx/json) et dictionnaires.
    Retourne csvs comme liste de (ressource, format) pour supporter tous les types."""
    resources = metadata.get("resources", [])
    csvs_fmt: list[tuple[dict, str]] = []  # (ressource, format)
    dicts = []
    has_json = False

    for r in resources:
        if r is None:
            continue
        fmt_r = (r.get("format") or "").lower()
        titre_lower = (r.get("title") or "").lower()

        if _est_dictionnaire_titre(r):
            dicts.append(r)
            continue

        if ".zip" in titre_lower or fmt_r == "zip":
            csvs_fmt.append((r, "zip"))
        elif ".gz" in titre_lower or fmt_r == "gz":
            csvs_fmt.append((r, "gz"))
        elif fmt_r == "parquet":
            csvs_fmt.append((r, "parquet"))
        elif fmt_r == "xlsx":
            csvs_fmt.append((r, "xlsx"))
        elif fmt_r == "csv":
            csvs_fmt.append((r, "csv"))
        elif fmt_r == "json" and "geo" not in titre_lower:
            has_json = True

    if csvs_fmt:
        fmt_principal = csvs_fmt[0][1]
        return {"csvs": csvs_fmt, "dicts": dicts, "has_json": has_json, "fmt": fmt_principal}

    # Pas de ressource tabulaire : essayer JSON directement
    jsons = [r for r in resources if r is not None
             and (r.get("format") or "").lower() == "json"
             and "geo" not in (r.get("title") or "").lower()
             and not _est_dictionnaire_titre(r)]
    if jsons:
        return {"csvs": [(jsons[0], "json")], "dicts": dicts, "has_json": False, "fmt": "json"}
    return {"csvs": [], "dicts": dicts, "has_json": False, "fmt": ""}


def _slugifier(titre: str) -> str:
    """Convertit un titre de ressource en slug de fichier (max 50 chars)."""
    titre = re.sub(r"\.[a-zA-Z0-9]{2,5}$", "", titre.strip())
    s = normaliser(titre)
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s[:50] or "fichier"


def filtrer_toutes_ressources(
    ressources_fmt: list[tuple[dict, str]],
    champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren=None,
    champ_epci=None, champ_lat=None, champ_lon=None,
) -> list[tuple[dict, list[dict], list[str]]]:
    """Télécharge et filtre chaque ressource (CSV, ZIP, GZ, XLSX, JSON).
    Retourne [(ressource, lignes_rm, entetes)] — un ZIP peut produire plusieurs entrées."""
    resultats = []
    multi = len(ressources_fmt) > 1
    for r, fmt in ressources_fmt:
        titre = r.get("title", r.get("url", ""))[:55]
        if multi:
            print(f"  ↳ {titre} [{fmt.upper()}]")
        chemin = None
        try:
            # Parquet : filtrage HTTP direct, pas de téléchargement local
            if fmt == "parquet":
                lignes, entetes = filtrer_parquet(r["url"], champ_cp, champ_ville,
                                                  champ_iris, champ_adresse, champ_siren,
                                                  champ_epci, champ_lat, champ_lon)
                resultats.append((r, lignes, entetes))
                if multi:
                    print(f"    → {len(lignes)} lignes RM")
                continue

            chemin = telecharger(r["url"])
            entrees: list[tuple[dict, list[dict], list[str]]] = []

            if fmt == "zip":
                with zipfile.ZipFile(chemin) as zf:
                    noms_csv = [n for n in zf.namelist()
                                if n.lower().endswith(".csv") and not n.startswith("__MACOSX")]
                if not noms_csv:
                    print(f"    → ZIP sans CSV")
                    resultats.append((r, [], []))
                    continue
                sirens_rm = obtenir_sirens_rm() if champ_siren else None
                with zipfile.ZipFile(chemin) as zf:
                    for nom_membre in noms_csv:
                        with zf.open(nom_membre) as fp:
                            sample_bytes = fp.read(8192)
                        encoding = _detecter_encodage_bytes(sample_bytes)
                        delimiteur = _detecter_delimiteur(
                            sample_bytes.decode(encoding, errors="replace")[:4096])
                        with zf.open(nom_membre) as fp:
                            tf = io.TextIOWrapper(fp, encoding=encoding,
                                                  errors="replace", newline="")
                            reader = csv.DictReader(tf, delimiter=delimiteur)
                            entetes = list(reader.fieldnames or [])
                            cp, vil, iris, adr, sir, epci, lat, lon = _resoudre_champs(
                                entetes, champ_cp, champ_ville, champ_iris,
                                champ_adresse, champ_siren, champ_epci, champ_lat, champ_lon)
                            lignes = [dict(row) for row in reader
                                      if _ligne_est_rm(row, cp, vil, iris, adr, sir, sirens_rm,
                                                        epci, lat, lon)]
                        r_m = {**r, "title": os.path.basename(nom_membre)}
                        if len(noms_csv) > 1:
                            print(f"    ↳ {nom_membre}: {len(lignes)} lignes RM")
                        entrees.append((r_m, lignes, entetes))
            elif fmt == "gz":
                lignes, entetes = filtrer_gz(chemin, champ_cp, champ_ville, champ_iris, champ_adresse,
                                              champ_siren, champ_epci, champ_lat, champ_lon)
                entrees = [(r, lignes, entetes)]
            elif fmt == "xlsx":
                lignes = filtrer_xlsx(chemin, champ_cp, champ_ville, champ_iris, champ_adresse,
                                       champ_siren, champ_epci, champ_lat, champ_lon)
                entrees = [(r, lignes, [])]
            elif fmt == "json":
                lignes = filtrer_json(chemin, champ_cp, champ_ville, champ_iris, champ_adresse,
                                       champ_siren, champ_epci, champ_lat, champ_lon)
                entrees = [(r, lignes, [])]
            else:  # csv
                lignes, entetes = filtrer_csv(chemin, champ_cp, champ_ville, champ_iris, champ_adresse,
                                               champ_siren, champ_epci, champ_lat, champ_lon)
                entrees = [(r, lignes, entetes)]

            resultats.extend(entrees)
            if multi and fmt != "zip" and entrees:
                print(f"    → {len(entrees[0][1])} lignes RM")
        except Exception as e:
            print(f"    → ERREUR : {e}")
            resultats.append((r, [], []))
        finally:
            # Le brut n'est plus utile une fois filtré : évite d'accumuler le cache indéfiniment.
            if chemin and os.path.exists(chemin):
                os.remove(chemin)
    return resultats


def traiter_candidat(candidat: dict, state: dict) -> dict:
    dataset_id = candidat["dataset_id"]
    dossier_nom = candidat["dossier"]
    dossier = os.path.join(DATA_DIR, dossier_nom)
    os.makedirs(dossier, exist_ok=True)

    champ_cp = candidat.get("champ_cp")
    champ_ville = candidat.get("champ_ville")
    champ_iris = candidat.get("champ_iris")
    champ_adresse = candidat.get("champ_adresse")
    champ_siren = candidat.get("champ_siren")
    champ_epci = candidat.get("champ_epci")
    champ_lat = candidat.get("champ_lat")
    champ_lon = candidat.get("champ_lon")

    metadata = get_dataset_metadata(dataset_id)
    last_modified = metadata.get("last_modified", "")

    # Vérifier si des fichiers filtrés existent déjà et que la source n'a pas changé
    filtered_existe = (
        bool(glob.glob(os.path.join(dossier, "*-rennesmetropole.csv"))) or
        bool(glob.glob(os.path.join(dossier, "*-rennesmetropole.json"))) or
        bool(glob.glob(os.path.join(dossier, "filtered*.csv"))) or   # ancien nommage
        bool(glob.glob(os.path.join(dossier, "filtered*.json")))
    )
    if filtered_existe and not dataset_a_change(state, dataset_id, last_modified):
        nb_rm = state.get(dataset_id, {}).get("nb_rm", "?")
        return {"statut": "cache", "nb_rm": nb_rm, "last_modified": last_modified}

    analyse = analyser_ressources(metadata)
    fmt = analyse["fmt"]
    if not analyse["csvs"]:
        return {"statut": "echec", "raison": "aucune ressource CSV/JSON trouvée"}

    if len(analyse["csvs"]) > 1:
        fmts = "/".join(sorted({f.upper() for _, f in analyse["csvs"]}))
        print(f"  {len(analyse['csvs'])} ressources [{fmts}] — téléchargées séparément")

    resultats = filtrer_toutes_ressources(analyse["csvs"], champ_cp, champ_ville, champ_iris, champ_adresse,
                                           champ_siren, champ_epci, champ_lat, champ_lon)

    # Sauvegarder un fichier filtré par ressource (+ JSON régénéré si la source en avait)
    fichiers_data = []   # [(nom, nb_rm, ressource)]
    fichiers_dicts = []  # [(nom, ressource)]
    dernieres_entetes = []

    for i, (ressource, lignes, entetes) in enumerate(resultats):
        if entetes:
            dernieres_entetes = entetes
        if not lignes:
            continue
        slug = _slugifier(
            ressource.get("title", "") or metadata.get("title", f"fichier-{i+1}")
        )
        if fmt == "json":
            nom = f"{slug}-rennesmetropole.json"
            chemin = os.path.join(dossier, nom)
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(lignes, f, ensure_ascii=False, indent=2)
            fichiers_data.append((nom, len(lignes), ressource))
        else:
            nom_csv = f"{slug}-rennesmetropole.csv"
            sauvegarder_csv(lignes, os.path.join(dossier, nom_csv))
            fichiers_data.append((nom_csv, len(lignes), ressource))
            # Régénérer JSON si la source en avait un
            if analyse["has_json"]:
                nom_json = nom_csv.replace(".csv", ".json")
                with open(os.path.join(dossier, nom_json), "w", encoding="utf-8") as f:
                    json.dump(lignes, f, ensure_ascii=False, indent=2)
                fichiers_data.append((nom_json, len(lignes), ressource))

    if not fichiers_data:
        champ_cherche = (champ_iris or champ_ville or champ_cp or champ_adresse or champ_siren
                         or champ_epci or champ_lat)
        # Vérifier la présence avec résolution normalisée (le champ peut avoir une casse différente)
        if champ_cherche and dernieres_entetes:
            norm_entetes = {normaliser(k): k for k in dernieres_entetes}
            present = (champ_cherche in dernieres_entetes or
                       normaliser(champ_cherche) in norm_entetes)
        else:
            present = False
        raison = (
            f"colonne '{champ_cherche}' absente du fichier (colonnes : {', '.join(dernieres_entetes[:8])}...)"
            if not present and dernieres_entetes
            else "0 lignes RM après filtrage"
        )
        return {"statut": "vide", "raison": raison}

    # Télécharger les dictionnaires tels quels
    for r in analyse["dicts"]:
        if r is None:
            continue
        titre_r = r.get("title") or "dictionnaire"
        ext = (r.get("format") or "csv").lower()
        nom = f"dict-{_slugifier(titre_r)}.{ext}"
        print(f"  Dictionnaire : {titre_r[:55]}")
        chemin_cache = None
        try:
            chemin_cache = telecharger(r["url"])
            if ext == "csv" and not _est_dictionnaire_contenu(chemin_cache):
                print(f"    → ignoré (contenu détecté comme données, pas dictionnaire)")
                continue
            shutil.copy2(chemin_cache, os.path.join(dossier, nom))
            fichiers_dicts.append((nom, r))
        except Exception as e:
            print(f"    → ERREUR : {e}")
        finally:
            if chemin_cache and os.path.exists(chemin_cache):
                os.remove(chemin_cache)

    # Sommer les CSV uniquement (les JSON régénérés ont le même nb_rm, on évite le double-compte)
    nb_rm_total = sum(nb for nom, nb, _ in fichiers_data if nom.endswith(".csv")) \
               or sum(nb for nom, nb, _ in fichiers_data if nom.endswith(".json"))

    rudi_metadata = traduire_metadonnees(
        metadata, dossier_nom=dossier_nom,
        fichiers_filtres=fichiers_data, fichiers_dicts=fichiers_dicts,
        entetes_colonnes=dernieres_entetes,
    )
    rudi_file = os.path.join(dossier, "rudi_metadata.json")
    with open(rudi_file, "w", encoding="utf-8") as f:
        json.dump(rudi_metadata, f, ensure_ascii=False, indent=2)

    # L'écriture dans `state` (dict partagé entre threads workers) est laissée au
    # thread principal de main() pour qu'elle reste sous le même lock que sauvegarder_state().
    entree = {"last_modified": last_modified, "nb_rm": nb_rm_total, "dossier": dossier_nom}
    return {"statut": "ok", "nb_rm": nb_rm_total, "format": fmt.upper(), "last_modified": last_modified, "entree": entree}


def main():
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with open(DECOUVERTE_FILE, encoding="utf-8") as f:
        decouverte = json.load(f)

    candidats = decouverte["candidats"]
    state = charger_state()
    resultats = {"ok": [], "cache": [], "echecs": [], "vides": [], "sautes": []}
    lock = threading.Lock()  # protège sauvegarder_state (écriture fichier)

    # Pré-tri immédiat des candidats sans champ géo (pas de thread pour eux)
    a_traiter = []
    for candidat in candidats:
        if not any(candidat.get(c) for c in ("champ_cp", "champ_ville", "champ_iris", "champ_adresse", "champ_siren")):
            resultats["sautes"].append({"dataset_id": candidat["dataset_id"], "titre": candidat["titre"]})
        else:
            a_traiter.append(candidat)

    n = len(a_traiter)
    ns = len(resultats["sautes"])
    print(f"=== Harvest batch — {len(candidats)} candidats"
          f"{f', dont {ns} sautés (sans champ géo)' if ns else ''}"
          f" — {n} à traiter (5 threads) ===\n")

    def run(candidat):
        try:
            return traiter_candidat(candidat, state)
        except Exception as e:
            return {"statut": "echec", "raison": str(e)}

    completed = 0
    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(run, c): c for c in a_traiter}
            for future in as_completed(futures):
                candidat = futures[future]
                completed += 1
                dataset_id = candidat["dataset_id"]
                titre = candidat["titre"][:65]
                try:
                    res = future.result()
                except Exception as e:
                    res = {"statut": "echec", "raison": str(e)}

                # Résumé affiché atomiquement depuis le thread principal
                print(f"\n[{completed}/{n}] {titre}")
                if res["statut"] == "cache":
                    print(f"  → CACHE ({res.get('nb_rm', '?')} lignes RM, inchangé)")
                    resultats["cache"].append({"dataset_id": dataset_id, "titre": candidat["titre"]})
                elif res["statut"] == "ok":
                    print(f"  → OK — {res['nb_rm']} lignes RM ({res['format']})")
                    resultats["ok"].append({"dataset_id": dataset_id, "titre": candidat["titre"], "nb_rm": res["nb_rm"]})
                    with lock:
                        state[dataset_id] = res["entree"]
                        sauvegarder_state(state)
                elif res["statut"] == "vide":
                    print(f"  → VIDE — {res['raison']}")
                    resultats["vides"].append({"dataset_id": dataset_id, "titre": candidat["titre"], "raison": res["raison"]})
                else:
                    print(f"  → ÉCHEC — {res.get('raison', '')}")
                    resultats["echecs"].append({"dataset_id": dataset_id, "titre": candidat["titre"], "raison": res.get("raison", "")})

    except KeyboardInterrupt:
        print("\n\nInterruption clavier.")

    sauvegarder_state(state)

    results_file = os.path.join(DATA_DIR, "batch_resultats.json")
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)

    print(f"\n=== Terminé ===")
    print(f"  OK     : {len(resultats['ok'])}")
    print(f"  Cache  : {len(resultats['cache'])} (inchangés)")
    print(f"  Vides  : {len(resultats['vides'])}")
    print(f"  Échecs : {len(resultats['echecs'])}")
    print(f"  Sautés : {len(resultats['sautes'])}")
    print(f"\n  Résultats complets : {results_file}")

    from catalogue import construire_catalogue, ecrire_json, ecrire_html, ecrire_viewers
    catalogue = construire_catalogue()
    ecrire_json(catalogue)
    ecrire_html(catalogue)
    ecrire_viewers(catalogue)
    print(f"\n  Catalogue mis à jour : {catalogue['nb_jeux']} JDD → data/catalogue.json + data/catalogue.html")


if __name__ == "__main__":
    main()
