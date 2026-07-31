"""
Passe batch sur les candidats de data/decouverte.json.
Pour chaque candidat : téléchargement complet → filtrage RM → filtered.csv + rudi_metadata.json.
Les 23 candidats sans champ de filtrage connu sont sautés (à traiter manuellement).
"""

import bz2
import csv
csv.field_size_limit(10_000_000)
import glob
import gzip
import io
import json
import os
import re
import shutil
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.datagouv import get_dataset_metadata
from connectors.download import _chemin_cache
from connectors.http import session
from connectors.sirene import obtenir_sirens_rm
from filters.csv import slugifier, sauvegarder_csv
from filters.geographic import (
    est_dans_rm, est_commune_rm, normaliser, est_circonscription_rm,
    est_iris_rm, est_code_rm, est_epci_rm, est_point_rm, est_adresse_rm,
    est_departement_rm,
    EPCI_SIREN_RM,
)
from filters.harvest import (
    _detecter_delimiteur, _detecter_encodage_bytes, _detecter_encodage,
    _ligne_est_rm, _extraire_csvs_zip, nature_champ_iris,
)
from conf.communes_rm import CODES_POSTAUX_RM, CODES_INSEE_RM
from translation.datagouv_to_rudi import traduire_metadonnees
from connectors.rudi_publish import publier_si_configue
from state import charger_state, sauvegarder_state, dataset_a_change

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DECOUVERTE_FILE = os.path.join(DATA_DIR, "decouverte.json")
CACHE_DIR = os.path.join(DATA_DIR, "cache")  # cache partagé avec discover.py
_CACHE_TTL_SECS = 7 * 24 * 3600  # 7 jours — les fichiers plus vieux sont re-téléchargés

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


def _resoudre_champs(fieldnames: list[str], champ_cp, champ_ville, champ_iris,
                     champ_adresse, champ_siren, champ_epci=None, champ_lat=None, champ_lon=None,
                     champ_circonscription=None, champ_dep=None):
    """Résout les noms de champs configurés vers les noms réels des colonnes CSV.

    Retourne un tuple (champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
    champ_epci, champ_lat, champ_lon, champ_circonscription, champ_dep) avec les noms
    réels tels qu'ils apparaissent dans le fichier, ou les originaux si la résolution échoue.
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
        _resoudre_champ(champ_circonscription, norm),
        _resoudre_champ(champ_dep, norm),
    )


def telecharger(url: str) -> str:
    """Télécharge en streaming vers le cache disque. Retourne le chemin du fichier cache.
    Les fichiers de cache de plus de _CACHE_TTL_SECS sont re-téléchargés."""
    chemin = _chemin_cache(url)
    if os.path.exists(chemin):
        age = time.time() - os.path.getmtime(chemin)
        if age > _CACHE_TTL_SECS:
            print(f"    → cache expiré ({age/3600:.0f}h), re-téléchargement")
            os.remove(chemin)
        else:
            return chemin
    os.makedirs(CACHE_DIR, exist_ok=True)
    r = session.get(url, timeout=120, stream=True)
    r.raise_for_status()
    total = 0
    chemin_tmp = chemin + ".tmp"
    with open(chemin_tmp, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            total += len(chunk)
    os.rename(chemin_tmp, chemin)
    return chemin


def filtrer_csv(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse,
                 champ_siren=None, champ_epci=None, champ_lat=None,
                 champ_lon=None, champ_circonscription=None, champ_dep=None) -> tuple[list[dict], list[str]]:
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
        cp, vil, iris, adr, sir, epci, lat, lon, circo, dep = _resoudre_champs(
            entetes, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
            champ_epci, champ_lat, champ_lon, champ_circonscription, champ_dep)
        nature = "inconnue"
        if iris:
            # Pré-passe : nature INSEE/CP de la colonne, puis seconde passe de filtrage.
            nature = nature_champ_iris(iris, reader)
            f.seek(0)
            reader = csv.DictReader(f, delimiter=delimiteur)
        lignes = [dict(row) for row in reader
                  if _ligne_est_rm(row, cp, vil, iris, adr, sir, sirens_rm, epci, lat, lon, circo, dep,
                                    nature_iris=nature)]
    return lignes, entetes


def filtrer_json(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse,
                  champ_siren=None, champ_epci=None, champ_lat=None,
                  champ_lon=None, champ_circonscription=None, champ_dep=None) -> list[dict]:
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
    nature = nature_champ_iris(champ_iris, iter(rows)) if champ_iris else "inconnue"
    return [row for row in rows
            if _ligne_est_rm(row, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
                             sirens_rm, champ_epci, champ_lat, champ_lon, champ_circonscription,
                             champ_dep, nature_iris=nature)]


def filtrer_parquet(url: str, champ_cp, champ_ville, champ_iris, champ_adresse,
                    champ_siren=None, champ_epci=None, champ_lat=None,
                    champ_lon=None, champ_circonscription=None, champ_dep=None) -> tuple[list[dict], list[str]]:
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
                    or champ_epci or champ_lat or champ_circonscription or champ_dep)

    with fsspec.open(url, "rb") as f:
        pf = pq.ParquetFile(f)
        schema_names = pf.schema_arrow.names
        md = pf.metadata

        # Résoudre le nom de champ contre les colonnes réelles du schéma
        _, _, iris_r, _, _, _, _, _, _, _ = _resoudre_champs(schema_names, champ_cp, champ_ville,
                                               champ_iris, champ_adresse, champ_siren,
                                               champ_epci, champ_lat, champ_lon, champ_circonscription, champ_dep)
        cp_r, vil_r, _, adr_r, sir_r, _, _, _, _, _ = _resoudre_champs(schema_names, champ_cp, champ_ville,
                                                          None, champ_adresse, champ_siren,
                                                          champ_epci, champ_lat, champ_lon, champ_circonscription, champ_dep)
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
        cp_r = vil_r = iris_r = adr_r = sir_r = epci_r = lat_r = lon_r = circo_r = dep_r = None

        for rgi in rgs_a_lire:
            table = pf.read_row_group(rgi)
            if not entetes:
                entetes = table.schema.names
                cp_r, vil_r, iris_r, adr_r, sir_r, epci_r, lat_r, lon_r, circo_r, dep_r = _resoudre_champs(
                    entetes, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
                    champ_epci, champ_lat, champ_lon, champ_circonscription, champ_dep)
            # to_pydict() retourne {col: [valeurs...]} — déjà en RAM pour ce row group
            cols = table.to_pydict()
            n = table.num_rows
            for i in range(n):
                row = {k: str(v[i]) if v[i] is not None else "" for k, v in cols.items()}
                if _ligne_est_rm(row, cp_r, vil_r, iris_r, adr_r, sir_r, sirens_rm,
                                  epci_r, lat_r, lon_r, circo_r, dep_r):
                    lignes.append(row)

    return lignes, entetes


def filtrer_gz(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse,
               champ_siren=None, champ_epci=None, champ_lat=None,
               champ_lon=None, champ_circonscription=None, champ_dep=None) -> tuple[list[dict], list[str]]:
    """Décompresse un GZ en streaming et filtre les lignes RM sans tout charger en mémoire."""
    sirens_rm = obtenir_sirens_rm() if champ_siren else None
    with gzip.open(chemin, "rb") as gz:
        sample_bytes = gz.read(8192)
    encoding = _detecter_encodage_bytes(sample_bytes)
    delimiteur = _detecter_delimiteur(sample_bytes.decode(encoding, errors="replace")[:4096])
    with gzip.open(chemin, "rt", encoding=encoding, errors="replace", newline="") as gz:
        reader = csv.DictReader(gz, delimiter=delimiteur)
        entetes = list(reader.fieldnames or [])
        cp, vil, iris, adr, sir, epci, lat, lon, circo, dep = _resoudre_champs(
            entetes, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
            champ_epci, champ_lat, champ_lon, champ_circonscription, champ_dep)
        nature = "inconnue"
        if iris:
            nature = nature_champ_iris(iris, reader)
            gz.seek(0)
            reader = csv.DictReader(gz, delimiter=delimiteur)
        lignes = [dict(row) for row in reader
                  if _ligne_est_rm(row, cp, vil, iris, adr, sir, sirens_rm, epci, lat, lon, circo, dep,
                                    nature_iris=nature)]
    return lignes, entetes


def filtrer_bz2(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse,
                champ_siren=None, champ_epci=None, champ_lat=None,
                champ_lon=None, champ_circonscription=None, champ_dep=None) -> tuple[list[dict], list[str]]:
    """Décompresse un BZ2 en streaming et filtre les lignes RM (même logique que filtrer_gz())."""
    sirens_rm = obtenir_sirens_rm() if champ_siren else None
    with bz2.open(chemin, "rb") as bz:
        sample_bytes = bz.read(8192)
    encoding = _detecter_encodage_bytes(sample_bytes)
    delimiteur = _detecter_delimiteur(sample_bytes.decode(encoding, errors="replace")[:4096])
    with bz2.open(chemin, "rt", encoding=encoding, errors="replace", newline="") as bz:
        reader = csv.DictReader(bz, delimiter=delimiteur)
        entetes = list(reader.fieldnames or [])
        cp, vil, iris, adr, sir, epci, lat, lon, circo, dep = _resoudre_champs(
            entetes, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
            champ_epci, champ_lat, champ_lon, champ_circonscription, champ_dep)
        nature = "inconnue"
        if iris:
            nature = nature_champ_iris(iris, reader)
            bz.seek(0)
            reader = csv.DictReader(bz, delimiter=delimiteur)
        lignes = [dict(row) for row in reader
                  if _ligne_est_rm(row, cp, vil, iris, adr, sir, sirens_rm, epci, lat, lon, circo, dep,
                                    nature_iris=nature)]
    return lignes, entetes


def filtrer_xlsx(chemin: str, champ_cp, champ_ville, champ_iris, champ_adresse,
                  champ_siren=None, champ_epci=None, champ_lat=None,
                  champ_lon=None, champ_circonscription=None, champ_dep=None) -> list[dict]:
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
    cp, vil, iris, adr, sir, epci, lat, lon, circo, dep = _resoudre_champs(
        entetes, champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren,
        champ_epci, champ_lat, champ_lon, champ_circonscription, champ_dep)
    rows = [
        dict(zip(entetes, [str(v or "").strip() for v in row]))
        for row in lignes_brutes[1:]
    ]
    nature = nature_champ_iris(iris, iter(rows)) if iris else "inconnue"
    return [r for r in rows
            if _ligne_est_rm(r, cp, vil, iris, adr, sir, sirens_rm, epci, lat, lon, circo, dep,
                              nature_iris=nature)]


_RE_PLAGE_DEPT = re.compile(
    r'(?:dept|dpts)\s+(\d{2,3})\s*[–\-à]\s*(\d{2,3})',
    re.IGNORECASE
)


def _hors_plage_35(titre: str) -> bool:
    """Vrai si le titre contient une plage de départements qui exclut le 35."""
    m = _RE_PLAGE_DEPT.search(titre)
    if m:
        debut, fin = int(m.group(1)), int(m.group(2))
        return debut > 35 or fin < 35
    return False


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


def _comparer_ressources(ressources_state: dict, ressources_api: dict) -> tuple[set, set, set, set, set]:
    """Compare les ressources de l'état avec celles de l'API.

    ressources_state: {resource_id: {"last_modified": str, "nb_rm": int}, ...}
    ressources_api:   {resource_id: {"last_modified": str}, ...}

    Retourne (modifiees, nouvelles, inchanged_rm, inchanged_vides, disparues).
    """
    modifiees = set()
    nouvelles = set()
    inchanged_rm = set()
    inchanged_vides = set()

    for rid, rapi in ressources_api.items():
        rlm = rapi.get("last_modified", "")
        if rid not in ressources_state:
            nouvelles.add(rid)
        elif ressources_state[rid].get("last_modified", "") != rlm:
            modifiees.add(rid)
        elif ressources_state[rid].get("nb_rm", 0) > 0:
            inchanged_rm.add(rid)
        else:
            inchanged_vides.add(rid)

    disparues = set(ressources_state.keys()) - set(ressources_api.keys())
    return modifiees, nouvelles, inchanged_rm, inchanged_vides, disparues


def analyser_ressources(metadata: dict) -> dict:
    """Classe les ressources par rôle : données (csv/zip/gz/xlsx/json), dictionnaires et PDF.
    Les ressources dont le titre contient une plage de départements excluant le 35
    (ex: 'dpts 57 à 976') sont ignorées — trop lourdes pour zéro lignes RM."""
    resources = metadata.get("resources", [])
    csvs_fmt: list[tuple[dict, str]] = []  # (ressource, format)
    dicts = []
    pdfs = []
    has_json = False

    for r in resources:
        if r is None:
            continue
        fmt_r = (r.get("format") or "").lower()
        titre_lower = (r.get("title") or "").lower()
        url_lower = (r.get("url") or "").lower().split("?")[0]

        if _est_dictionnaire_titre(r):
            dicts.append(r)
            continue

        if fmt_r == "pdf":
            pdfs.append(r)
            continue

        if _hors_plage_35(titre_lower):
            continue

        if ".zip" in titre_lower or fmt_r == "zip":
            csvs_fmt.append((r, "zip"))
        elif ".gz" in titre_lower or fmt_r == "gz":
            csvs_fmt.append((r, "gz"))
        elif ".bz2" in titre_lower or fmt_r == "bz2" or url_lower.endswith(".bz2"):
            csvs_fmt.append((r, "bz2"))
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
        return {"csvs": csvs_fmt, "dicts": dicts, "pdfs": pdfs, "has_json": has_json, "fmt": fmt_principal}

    # Pas de ressource tabulaire : essayer JSON directement
    jsons = [r for r in resources if r is not None
             and (r.get("format") or "").lower() == "json"
             and "geo" not in (r.get("title") or "").lower()
             and not _est_dictionnaire_titre(r)]
    if jsons:
        return {"csvs": [(jsons[0], "json")], "dicts": dicts, "pdfs": pdfs, "has_json": False, "fmt": "json"}
    return {"csvs": [], "dicts": dicts, "pdfs": pdfs, "has_json": False, "fmt": ""}


_slugifier = slugifier  # alias pour compatibilité ascendante (importé par harvest_insee)


def filtrer_toutes_ressources(
    ressources_fmt: list[tuple[dict, str]],
    champ_cp, champ_ville, champ_iris, champ_adresse, champ_siren=None,
    champ_epci=None, champ_lat=None, champ_lon=None, champ_circonscription=None,
    champ_dep=None,
    ressources_a_ignorer: set[str] | None = None,
) -> list[tuple[dict, list[dict], list[str]]]:
    """Télécharge et filtre chaque ressource (CSV, ZIP, GZ, BZ2, XLSX, JSON).
    Retourne [(ressource, lignes_rm, entetes)] — un ZIP peut produire plusieurs entrées.

    ressources_a_ignorer : ensemble d'ids de ressources à sauter (inchangées,
    déjà confirmées 0 lignes RM au précédent run)."""
    resultats = []
    multi = len(ressources_fmt) > 1
    for r, fmt in ressources_fmt:
        if ressources_a_ignorer and r.get("id") in ressources_a_ignorer:
            titre = r.get("title", r.get("url", ""))[:55]
            print(f"  ↳ {titre} — ignoré (inchangé, 0 lignes RM au précédent run)")
            continue
        titre = r.get("title", r.get("url", ""))[:55]
        if multi:
            print(f"  ↳ {titre} [{fmt.upper()}]")
        chemin = None
        try:
            # Parquet : filtrage HTTP direct, pas de téléchargement local
            if fmt == "parquet":
                lignes, entetes = filtrer_parquet(r["url"], champ_cp, champ_ville,
                                                  champ_iris, champ_adresse, champ_siren,
                                                  champ_epci, champ_lat, champ_lon,
                                                  champ_circonscription, champ_dep)
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
                            cp, vil, iris, adr, sir, epci, lat, lon, circo, dep = _resoudre_champs(
                                entetes, champ_cp, champ_ville, champ_iris,
                                champ_adresse, champ_siren, champ_epci, champ_lat, champ_lon,
                                champ_circonscription, champ_dep)
                            nature = nature_champ_iris(iris, reader) if iris else "inconnue"
                        # Le membre est rouvert : la pré-passe nature a consommé le flux.
                        with zf.open(nom_membre) as fp:
                            tf = io.TextIOWrapper(fp, encoding=encoding,
                                                  errors="replace", newline="")
                            reader = csv.DictReader(tf, delimiter=delimiteur)
                            lignes = [dict(row) for row in reader
                                      if _ligne_est_rm(row, cp, vil, iris, adr, sir, sirens_rm,
                                                        epci, lat, lon, circo, dep,
                                                        nature_iris=nature)]
                        r_m = {**r, "title": os.path.basename(nom_membre)}
                        if len(noms_csv) > 1:
                            print(f"    ↳ {nom_membre}: {len(lignes)} lignes RM")
                        entrees.append((r_m, lignes, entetes))
            elif fmt == "gz":
                lignes, entetes = filtrer_gz(chemin, champ_cp, champ_ville, champ_iris, champ_adresse,
                                              champ_siren, champ_epci, champ_lat, champ_lon,
                                              champ_circonscription, champ_dep)
                entrees = [(r, lignes, entetes)]
            elif fmt == "bz2":
                lignes, entetes = filtrer_bz2(chemin, champ_cp, champ_ville, champ_iris, champ_adresse,
                                               champ_siren, champ_epci, champ_lat, champ_lon,
                                               champ_circonscription, champ_dep)
                entrees = [(r, lignes, entetes)]
            elif fmt == "xlsx":
                lignes = filtrer_xlsx(chemin, champ_cp, champ_ville, champ_iris, champ_adresse,
                                       champ_siren, champ_epci, champ_lat, champ_lon,
                                       champ_circonscription, champ_dep)
                entrees = [(r, lignes, [])]
            elif fmt == "json":
                lignes = filtrer_json(chemin, champ_cp, champ_ville, champ_iris, champ_adresse,
                                       champ_siren, champ_epci, champ_lat, champ_lon,
                                       champ_circonscription, champ_dep)
                entrees = [(r, lignes, [])]
            else:  # csv
                lignes, entetes = filtrer_csv(chemin, champ_cp, champ_ville, champ_iris, champ_adresse,
                                               champ_siren, champ_epci, champ_lat, champ_lon,
                                               champ_circonscription, champ_dep)
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
    champ_circonscription = candidat.get("champ_circonscription")
    champ_dep = candidat.get("champ_dep")

    etat_precedent = state.get(dataset_id, {})
    nb_rm_precedent = etat_precedent.get("nb_rm")
    ressources_state = etat_precedent.get("ressources", {})

    # Un run précédent "vide" (nb_rm == 0) ne produit jamais de fichier filtré sur
    # disque — ne pas en exiger un dans ce cas, sinon ces JDD ne sont jamais mis en cache.
    fichier_attendu = nb_rm_precedent not in (0, None)
    filtered_existe = (
        bool(glob.glob(os.path.join(dossier, "*-rennesmetropole.csv"))) or
        bool(glob.glob(os.path.join(dossier, "*-rennesmetropole.json"))) or
        bool(glob.glob(os.path.join(dossier, "filtered*.csv"))) or   # ancien nommage
        bool(glob.glob(os.path.join(dossier, "filtered*.json")))
    )

    # Rapide : si le last_modified connu (de la découverte) correspond à l'état
    # sauvegardé, le dataset n'a pas bougé — pas besoin d'appeler l'API data.gouv.fr.
    lm_candidat = candidat.get("last_modified", "")
    lm_etat = etat_precedent.get("last_modified", "")
    if lm_candidat and lm_candidat == lm_etat and (filtered_existe or not fichier_attendu):
        nb_rm = nb_rm_precedent if nb_rm_precedent is not None else "?"
        return {"statut": "cache", "nb_rm": nb_rm, "last_modified": lm_candidat}

    metadata = get_dataset_metadata(dataset_id)
    last_modified = metadata.get("last_modified", "")

    if not dataset_a_change(state, dataset_id, last_modified) and (filtered_existe or not fichier_attendu):
        nb_rm = nb_rm_precedent if nb_rm_precedent is not None else "?"
        return {"statut": "cache", "nb_rm": nb_rm, "last_modified": last_modified}

    analyse = analyser_ressources(metadata)
    fmt = analyse["fmt"]
    if not analyse["csvs"]:
        return {"statut": "echec", "raison": "aucune ressource CSV/JSON trouvée"}

    # Cache check avancé : le dataset-level a changé, mais les ressources
    # individuelles n'ont peut-être pas bougé (re-indexation data.gouv.fr).
    # Ne concerne que les JDD déjà traités (avec historique par-ressource).
    ressources_a_ignorer: set[str] = set()
    inchanged_rm: set[str] = set()
    if nb_rm_precedent is not None and ressources_state:
        ressources_data_api = {}
        for r, _ in analyse["csvs"]:
            if r and r.get("id"):
                ressources_data_api[r["id"]] = {
                    "last_modified": r.get("last_modified") or "",
                }
        modifiees, nouvelles, _inchanged_rm, inchanged_vides, _ = _comparer_ressources(
            ressources_state, ressources_data_api
        )
        inchanged_rm = _inchanged_rm

        if not modifiees and not nouvelles:
            nb_rm = nb_rm_precedent
            return {"statut": "cache", "nb_rm": nb_rm, "last_modified": last_modified}

        # Saute les ressources déjà confirmées vides (nb_rm=0 et inchangées)
        # seulement quand le JDD a déjà des lignes RM au catalogue.
        if fichier_attendu:
            ressources_a_ignorer = inchanged_vides

    if len(analyse["csvs"]) > 1:
        fmts = "/".join(sorted({f.upper() for _, f in analyse["csvs"]}))
        print(f"  {len(analyse['csvs'])} ressources [{fmts}] — téléchargées séparément")

    resultats = filtrer_toutes_ressources(analyse["csvs"], champ_cp, champ_ville, champ_iris, champ_adresse,
                                           champ_siren, champ_epci, champ_lat, champ_lon,
                                           champ_circonscription, champ_dep,
                                           ressources_a_ignorer=ressources_a_ignorer)

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

    # Contruire le suivi par-ressource pour le state
    ressources_data = {}
    for r, _ in analyse["csvs"]:
        rsrc_id = r.get("id", "")
        if not rsrc_id:
            continue
        rsrc_lm = r.get("last_modified") or ""
        nb_rm_rsrc = sum(
            len(lignes) for rsrc_dict, lignes, _ in resultats
            if rsrc_dict.get("id") == rsrc_id
        )
        ressources_data[rsrc_id] = {
            "last_modified": rsrc_lm,
            "nb_rm": nb_rm_rsrc,
        }

    # Reporter les ressources RM inchangées (pas re-traitées) depuis l'état précédent
    if ressources_state and inchanged_rm:
        for rsrc_id in inchanged_rm:
            if rsrc_id not in ressources_data:
                ressources_data[rsrc_id] = ressources_state[rsrc_id]

    if not fichiers_data:
        champ_cherche = (champ_iris or champ_ville or champ_cp or champ_adresse or champ_siren
                         or champ_epci or champ_lat or champ_circonscription)
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
        return {"statut": "vide", "raison": raison,
                "entree": {"last_modified": last_modified, "nb_rm": 0, "dossier": dossier_nom,
                           "ressources": ressources_data}}

    # Télécharger les dictionnaires tels quels
    for r in analyse["dicts"]:
        if r is None:
            continue
        titre_r = r.get("title") or "dictionnaire"
        ext = (r.get("format") or "csv").lower().strip()
        # Normaliser les extensions non standard renvoyées par data.gouv.fr
        _EXT_DICT = {"url": "html", "page web": "html", "other": "html",
                     "document": "docx"}
        for anomal, propre in _EXT_DICT.items():
            if anomal in ext:
                ext = propre
                break
        else:
            ext = ext.split()[0] if " " in ext else ext
        if not ext or len(ext) > 10:
            ext = "html"
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

    # Télécharger les PDFs de documentation si des données ont été extraites
    fichiers_pdfs = []
    for r in analyse.get("pdfs", []):
        if r is None:
            continue
        titre_r = r.get("title") or "documentation"
        nom = f"doc-{_slugifier(titre_r)}.pdf"
        print(f"  PDF : {titre_r[:55]}")
        chemin_cache = None
        try:
            chemin_cache = telecharger(r["url"])
            shutil.copy2(chemin_cache, os.path.join(dossier, nom))
            fichiers_pdfs.append((nom, r))
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
        fichiers_pdfs=fichiers_pdfs,
        entetes_colonnes=dernieres_entetes,
    )
    rudi_file = os.path.join(dossier, "rudi_metadata.json")
    with open(rudi_file, "w", encoding="utf-8") as f:
        json.dump(rudi_metadata, f, ensure_ascii=False, indent=2)

    # Publication RUDI inline (comme les autres scripts de moisson)
    fichiers_filtres = [os.path.join(dossier, nom) for nom, _, _ in fichiers_data]
    rudi_publie = publier_si_configue(rudi_metadata, fichiers_filtres)

    # L'écriture dans `state` (dict partagé entre threads workers) est laissée au
    # thread principal de main() pour qu'elle reste sous le même lock que sauvegarder_state().
    entree = {"last_modified": last_modified, "nb_rm": nb_rm_total, "dossier": dossier_nom,
              "ressources": ressources_data, "rudi_publie": rudi_publie}
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
        if not any(candidat.get(c) for c in ("champ_cp", "champ_ville", "champ_iris", "champ_adresse",
                                              "champ_siren", "champ_circonscription")):
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

    start_time = time.time()

    def _fmt_duree(secs):
        h, r = divmod(int(secs), 3600)
        m, s = divmod(r, 60)
        if h:
            return f"{h}h{m:02d}"
        return f"{m:02d}:{s:02d}"

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

                elapsed = time.time() - start_time
                if completed > 1:
                    eta = _fmt_duree(elapsed / completed * (n - completed))
                else:
                    eta = "?"
                chrono = f"[{_fmt_duree(elapsed)} écoulé, ~{eta} restant]"

                # Résumé affiché atomiquement depuis le thread principal
                print(f"\n[{completed}/{n}] {titre}  {chrono}")
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
                    if "entree" in res:
                        with lock:
                            state[dataset_id] = res["entree"]
                            sauvegarder_state(state)
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
