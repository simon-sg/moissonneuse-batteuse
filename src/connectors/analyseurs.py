"""
Analyseurs de formats de données pour la découverte de JDD.

Analyses téléchargent (ou récupèrent du cache) chaque ressource et cherchent
des données relatives à Rennes Métropole. Fonctions partagées entre discover.py
(découverte interactive/automatique) et harvest_insee.py.

Chaque analyseur suit la même signature :
    analyser_<format>(url: str, verbose: bool, dataset_id: str, titre: str) -> dict | None
Retourne None en cas d'échec (téléchargement, parsing…).
Retourne un dict avec nb_rm, champ_*, exemples, premieres_lignes en cas de succès
(même si nb_rm == 0).
"""

import bz2
import csv
import datetime
import gzip
import hashlib
import io
import json
import os
import re
import sys
import warnings
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors.http import session

from conf.communes_rm import CODES_POSTAUX_RM, CODES_INSEE_RM, BBOX_RM, BBOX_RM_STR
from conf.discover import (
    CHAMPS_CP, CHAMPS_VILLE, CHAMPS_IRIS, CHAMPS_DEP, CHAMPS_SIREN,
    CHAMPS_CIRCONSCRIPTION, CHAMPS_GEO_POINT, CHAMPS_LAT, CHAMPS_LON, CHAMPS_ADRESSE,
    CHAMPS_EPCI,
    _FORMATS_EXCLUS_FMT, _FORMATS_EXCLUS_EXT,
    LOG_FILE, CACHE_DIR,
)
from connectors.download import _telecharger, _chemin_cache
from connectors.geo_services import wms_get_capabilities, wms_couches_dans_rm, nettoyer_url_ogc
from filters.harvest import _detecter_encodage_bytes
from connectors.sirene import obtenir_sirens_rm
from filters.geographic import (
    est_dans_rm, est_commune_rm, normaliser, est_circonscription_rm,
    est_iris_rm, est_valeur_commune_rm, est_epci_rm, est_point_rm, est_adresse_rm,
    est_departement_rm,
    EPCI_SIREN_RM,
)

csv.field_size_limit(10_000_000)

_RM_LON_MIN, _RM_LAT_MIN, _RM_LON_MAX, _RM_LAT_MAX = BBOX_RM
_WFS_RM_BBOX = BBOX_RM_STR

# ---------------------------------------------------------------------------
# Détection de format analysable
# ---------------------------------------------------------------------------


def _format_analysable(res: dict) -> str | None:
    fmt = (res.get("format") or "").lower().strip()
    url = (res.get("url") or "").lower().split("?")[0]
    url_full = (res.get("url") or "").lower()
    if any(token in fmt for token in _FORMATS_EXCLUS_FMT):
        return None
    if any(url.endswith(ext) for ext in _FORMATS_EXCLUS_EXT):
        return None
    if fmt == "wms" or "service=wms" in url_full:
        return "wms"
    if fmt == "wfs" or re.search(r"[/.]wfs(/|$)", url):
        return "wfs"
    if url.endswith(".csv.gz") or url.endswith(".tsv.gz") or fmt == "gz":
        return "gz"
    if url.endswith(".csv.bz2") or url.endswith(".tsv.bz2") or fmt == "bz2":
        return "bz2"
    if "csv" in fmt or url.endswith(".csv"):
        return "csv"
    if fmt in ("xlsx", "excel") or url.endswith(".xlsx"):
        return "xlsx"
    if "zip" in fmt or url.endswith(".zip"):
        return "zip"
    if fmt == "geojson" or url.endswith(".geojson"):
        return "geojson"
    if "parquet" in fmt or url.endswith(".parquet"):
        return "parquet"
    return None


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------


def _detecter_delimiteur(sample: str) -> str:
    premiere_ligne = sample.split("\n")[0]
    candidats = {d: premiere_ligne.count(d) for d in (";", "\t", "|", ",")}
    meilleur = max(candidats, key=candidats.get)
    if candidats[meilleur] >= 1:
        return meilleur
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        return dialect.delimiter
    except csv.Error:
        return ","


def _coords_centroide(geometry: dict) -> tuple[float, float] | None:
    gtype = geometry.get("type", "")
    coords = geometry.get("coordinates")
    if not coords:
        return None
    try:
        if gtype == "Point":
            return float(coords[0]), float(coords[1])
        elif gtype in ("LineString", "MultiPoint"):
            return float(coords[0][0]), float(coords[0][1])
        elif gtype in ("Polygon", "MultiLineString"):
            return float(coords[0][0][0]), float(coords[0][0][1])
        elif gtype == "MultiPolygon":
            return float(coords[0][0][0][0]), float(coords[0][0][0][1])
    except (IndexError, TypeError, ValueError):
        return None
    return None


def log_analyse(entry: dict) -> None:
    entry["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Détection de champs géographiques dans les en-têtes
# ---------------------------------------------------------------------------


def deviner_champs(entetes: list[str]) -> tuple[str | None, str | None]:
    """Devine les champs code postal et ville dans les en-têtes d'un CSV."""
    entetes_norm = [e.lower().strip() for e in entetes]
    champ_cp = next((e for e in entetes_norm if e in CHAMPS_CP), None)
    champ_ville = next((e for e in entetes_norm if e in CHAMPS_VILLE), None)
    if not champ_cp:
        champ_cp = next((e for e in entetes_norm if "postal" in e or e == "cp"), None)
    if not champ_ville:
        _FAUX_POSITIFS_VILLE = ("declarant", "pollution", "siren", "siret", "marche",
                                "activite", "effectif", "client", "fournisseur", "journey",
                                "epci")
        champ_ville = next(
            (e for e in entetes_norm
             if ("commune" in e or "ville" in e or "libelle" in e or "libgeo" in e)
             and "insee" not in e and "dep" not in e
             and not e.startswith("code") and "partenaire" not in e
             and not any(e.startswith(p) or (p + "_") in e or e.endswith("_" + p)
                         or e.endswith(p) for p in _FAUX_POSITIFS_VILLE)),
            None,
        )
    if champ_cp:
        champ_cp = entetes[entetes_norm.index(champ_cp)]
    if champ_ville:
        champ_ville = entetes[entetes_norm.index(champ_ville)]
    return champ_cp, champ_ville


def deviner_champ_iris(entetes: list[str]) -> str | None:
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_IRIS:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)]
    for i, e in enumerate(entetes_norm):
        if "iris" in e and "libelle" not in e and "lib" not in e:
            return entetes[i]
    _SUFFIXES_GEO_EXCLUS = ("reg", "region", "dep", "departement", "arr", "arrondissement")
    _PREFIXES_INSEE_EXCLUS = ("pollution", "declarant", "journey", "res",
                              "activite", "revenu", "pop", "nb")
    for i, e in enumerate(entetes_norm):
        if "insee" in e and not any(s in e for s in _SUFFIXES_GEO_EXCLUS) \
                and not any(e.startswith(p) or e.startswith(p + " ") for p in _PREFIXES_INSEE_EXCLUS):
            return entetes[i]
    return None


def deviner_champ_epci(entetes: list[str]) -> str | None:
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_EPCI:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)]
    for i, e in enumerate(entetes_norm):
        if "epci" in e:
            return entetes[i]
    return None


def deviner_champ_dep(entetes: list[str]) -> str | None:
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_DEP:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)]
    return None


def deviner_champ_adresse(entetes: list[str]) -> str | None:
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_ADRESSE:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)]
    for i, e in enumerate(entetes_norm):
        if "adresse" in e:
            return entetes[i]
    return None


def deviner_champ_siren(entetes: list[str]) -> str | None:
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_SIREN:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)]
    for i, e in enumerate(entetes_norm):
        if e.startswith(("siren", "siret")):
            return entetes[i]
    return None


def deviner_champ_circonscription(entetes: list[str]) -> str | None:
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_CIRCONSCRIPTION:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)]
    for i, e in enumerate(entetes_norm):
        if "circonscription" in e or e.startswith("circo"):
            return entetes[i]
    return None


def deviner_champs_geo(entetes: list[str]) -> tuple[str | None, str | None]:
    entetes_norm = [normaliser(e) for e in entetes]
    for nom in CHAMPS_GEO_POINT:
        if nom in entetes_norm:
            return entetes[entetes_norm.index(nom)], None
    champ_lat, champ_lon = None, None
    for nom in CHAMPS_LAT:
        if nom in entetes_norm:
            champ_lat = entetes[entetes_norm.index(nom)]
            break
    if champ_lat is None:
        for i, e in enumerate(entetes_norm):
            if e in ("lat", "latitude"):
                champ_lat = entetes[i]
                break
    for nom in CHAMPS_LON:
        if nom in entetes_norm:
            champ_lon = entetes[entetes_norm.index(nom)]
            break
    if champ_lon is None:
        for i, e in enumerate(entetes_norm):
            if e in ("lon", "lng", "longitude"):
                champ_lon = entetes[i]
                break
    if champ_lat and champ_lon:
        return champ_lat, champ_lon
    return None, None


def _detecter_champs(entetes: list[str]) -> tuple:
    """Retourne (champ_cp, champ_ville, champ_iris, champ_dep, champ_epci, champ_adresse,
    champ_siren, champ_lat, champ_lon, champ_circonscription)."""
    champ_cp, champ_ville = deviner_champs(list(entetes))
    champ_iris = deviner_champ_iris(list(entetes))
    champ_dep = deviner_champ_dep(list(entetes)) if champ_iris else None
    champ_epci = (
        None if (champ_cp or champ_ville or champ_iris)
        else deviner_champ_epci(list(entetes))
    )
    champ_adresse = (
        None if (champ_cp or champ_ville or champ_iris or champ_epci)
        else deviner_champ_adresse(list(entetes))
    )
    champ_siren = (
        None if (champ_cp or champ_ville or champ_iris or champ_epci or champ_adresse)
        else deviner_champ_siren(list(entetes))
    )
    champ_lat, champ_lon = (None, None) if (champ_cp or champ_ville or champ_iris
                                              or champ_epci or champ_adresse or champ_siren) \
        else deviner_champs_geo(list(entetes))
    champ_circonscription = (
        None if (champ_cp or champ_ville or champ_iris or champ_epci or champ_adresse
                  or champ_siren or champ_lat)
        else deviner_champ_circonscription(list(entetes))
    )
    if not any([champ_cp, champ_ville, champ_iris, champ_epci, champ_adresse,
                champ_siren, champ_lat, champ_circonscription]):
        champ_dep = deviner_champ_dep(list(entetes))
    return (champ_cp, champ_ville, champ_iris, champ_dep, champ_epci, champ_adresse,
            champ_siren, champ_lat, champ_lon, champ_circonscription)


# ---------------------------------------------------------------------------
# Comptage RM
# ---------------------------------------------------------------------------


def _compter_lignes_rm(rows, champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse,
                        champ_siren=None, champ_epci=None,
                        champ_lat=None, champ_lon=None,
                        champ_circonscription=None) -> tuple:
    """Itère rows (dicts) et compte ceux appartenant à Rennes Métropole."""
    sirens_rm = obtenir_sirens_rm() if champ_siren else None
    nb_total, nb_rm = 0, 0
    exemples, premieres_lignes = [], []
    for row in rows:
        try:
            nb_total += 1
            if len(premieres_lignes) < 5:
                premieres_lignes.append(dict(row))
            if champ_iris:
                code = str(row.get(champ_iris, "")).strip()
                if len(code) < 5 and champ_dep:
                    dept_raw = str(row.get(champ_dep, "")).strip()
                    dept = (dept_raw.lstrip("0") or "0").zfill(2)
                    code = dept + code.zfill(3)
                in_rm = est_iris_rm(code)
            elif champ_adresse:
                in_rm = est_adresse_rm(str(row.get(champ_adresse, "")))
            elif champ_siren:
                val = str(row.get(champ_siren, "")).strip().replace(" ", "")
                in_rm = val.isdigit() and len(val) in (9, 14) and val[:9] in sirens_rm
            elif champ_epci:
                in_rm = est_epci_rm(str(row.get(champ_epci, "")))
            elif champ_lat:
                lat_val = str(row.get(champ_lat, "")).strip()
                lon_val = str(row.get(champ_lon, "")).strip() if champ_lon else None
                in_rm = est_point_rm(lat_val, lon_val)
            elif champ_circonscription:
                in_rm = est_circonscription_rm(str(row.get(champ_circonscription, "")))
            elif champ_dep:
                in_rm = est_departement_rm(str(row.get(champ_dep, "")))
            else:
                cp = str(row.get(champ_cp, "")).strip() if champ_cp else ""
                ville = str(row.get(champ_ville, "")).strip() if champ_ville else ""
                if champ_cp and champ_ville:
                    in_rm = est_dans_rm(ville, cp)
                elif champ_ville:
                    in_rm = est_commune_rm(ville)
                elif champ_cp:
                    in_rm = cp in CODES_POSTAUX_RM
                else:
                    in_rm = False
            if in_rm:
                nb_rm += 1
                if len(exemples) < 3:
                    exemples.append(dict(row))
        except csv.Error:
            break
    return nb_total, nb_rm, exemples, premieres_lignes


def _construire_resultat(champ_cp, champ_ville, champ_iris, champ_adresse,
                         nb_total, nb_rm, exemples, premieres_lignes,
                         champ_siren=None, champ_epci=None,
                         champ_lat=None, champ_lon=None,
                         champ_circonscription=None, champ_dep=None) -> dict:
    return {
        "nb_total": nb_total, "nb_rm": nb_rm,
        "champ_cp": champ_cp, "champ_ville": champ_ville,
        "champ_iris": champ_iris, "champ_adresse": champ_adresse,
        "champ_siren": champ_siren, "champ_epci": champ_epci,
        "champ_lat": champ_lat, "champ_lon": champ_lon,
        "champ_circonscription": champ_circonscription,
        "champ_dep": champ_dep,
        "exemples": exemples, "premieres_lignes": premieres_lignes,
    }


# ---------------------------------------------------------------------------
# Analyse CSV
# ---------------------------------------------------------------------------


def _analyser_csv_depuis_stream(preambule: bytes, fp_bin,
                                 verbose: bool, dataset_id: str, titre: str,
                                 url: str = "", taille_mo: float = 0,
                                 depuis_cache: bool = False) -> dict | None:
    log = {"url": url, "dataset_id": dataset_id, "titre": titre,
           "taille_mo": round(taille_mo, 2), "cache": depuis_cache}

    if preambule[:5] in (b"%PDF-", b"PK\x03\x04", b"\x1f\x8b\x08"):
        log["erreur"] = "fichier binaire"
        log_analyse(log)
        if verbose:
            print("  (Fichier binaire détecté, non supporté)")
        return None

    debut = preambule[:100].lstrip().lower()
    if debut.startswith((b"<!doctype", b"<html")):
        log["erreur"] = "réponse HTML"
        log_analyse(log)
        if verbose:
            print("  (Réponse HTML reçue — redirection ou authentification)")
        return None

    encoding = _detecter_encodage_bytes(preambule)
    sample_str = preambule.decode(encoding, errors="replace")

    delimiteur = _detecter_delimiteur(sample_str[:4096])

    premiere_ligne = sample_str.split("\n")[0]
    premiere_norm = normaliser(premiere_ligne.split(",")[0].split(";")[0])
    if premiere_norm in ("colonne", "column", "champ", "field", "variable"):
        return None

    log["delimiteur"] = delimiteur

    tf = io.TextIOWrapper(fp_bin, encoding=encoding, errors="replace", newline="")
    reader = csv.DictReader(tf, delimiter=delimiteur)
    entetes = list(reader.fieldnames or [])
    log["entetes"] = entetes[:15]

    (champ_cp, champ_ville, champ_iris, champ_dep, champ_epci, champ_adresse, champ_siren,
     champ_lat, champ_lon, champ_circonscription) = _detecter_champs(entetes)
    log.update({"champ_cp": champ_cp, "champ_ville": champ_ville,
                "champ_iris": champ_iris, "champ_epci": champ_epci,
                "champ_adresse": champ_adresse,
                "champ_siren": champ_siren, "champ_lat": champ_lat,
                "champ_circonscription": champ_circonscription})

    if verbose:
        print(f"  En-têtes détectés : {entetes[:10]}")
        if champ_iris:
            print(f"  Champ IRIS trouvé : {champ_iris}")
        elif champ_epci:
            print(f"  Champ EPCI trouvé : {champ_epci}")
        elif champ_adresse:
            print(f"  Champ adresse trouvé : {champ_adresse}")
        elif champ_siren:
            print(f"  Champ SIREN trouvé : {champ_siren}")
        elif champ_lat:
            desc = champ_lat if champ_lon is None else f"{champ_lat} + {champ_lon}"
            print(f"  Champ géo trouvé : {desc}")
        elif champ_circonscription:
            print(f"  Champ circonscription trouvé : {champ_circonscription}")
        else:
            print(f"  Champ CP trouvé : {champ_cp} | Champ ville trouvé : {champ_ville}")

    try:
        nb_total, nb_rm, exemples, premieres_lignes = _compter_lignes_rm(
            reader, champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse,
            champ_siren=champ_siren, champ_epci=champ_epci,
            champ_lat=champ_lat, champ_lon=champ_lon,
            champ_circonscription=champ_circonscription,
        )
    except csv.Error as e:
        log["erreur"] = f"parsing CSV : {e}"
        log_analyse(log)
        if verbose:
            print(f"  (Erreur de parsing CSV : {e})")
        return None

    log.update({"nb_total": nb_total, "nb_rm": nb_rm})
    log_analyse(log)
    return _construire_resultat(champ_cp, champ_ville, champ_iris, champ_adresse,
                                nb_total, nb_rm, exemples, premieres_lignes,
                                champ_siren=champ_siren, champ_epci=champ_epci,
                                champ_lat=champ_lat, champ_lon=champ_lon,
                                champ_circonscription=champ_circonscription,
                                champ_dep=champ_dep)


def _analyser_contenu_csv(contenu: bytes, verbose: bool, dataset_id: str, titre: str,
                           url: str = "", taille_mo: float = 0,
                           depuis_cache: bool = False) -> dict | None:
    log = {"url": url, "dataset_id": dataset_id, "titre": titre,
           "taille_mo": round(taille_mo, 2), "cache": depuis_cache}

    if contenu[:5] in (b"%PDF-", b"PK\x03\x04", b"\x1f\x8b\x08"):
        log["erreur"] = "fichier binaire"
        log_analyse(log)
        if verbose:
            print("  (Fichier binaire détecté, non supporté)")
        return None

    debut = contenu[:100].lstrip().lower()
    if debut.startswith((b"<!doctype", b"<html")):
        log["erreur"] = "réponse HTML"
        log_analyse(log)
        if verbose:
            print("  (Réponse HTML reçue — redirection ou authentification)")
        return None

    encoding = _detecter_encodage_bytes(contenu[:8192])
    texte = contenu.decode(encoding, errors="replace")
    sample = texte[:4096]
    delimiteur = _detecter_delimiteur(sample)

    premiere_ligne = texte.split("\n")[0]
    premiere_norm = normaliser(premiere_ligne.split(",")[0].split(";")[0])
    if premiere_norm in ("colonne", "column", "champ", "field", "variable"):
        return None

    log["delimiteur"] = delimiteur

    reader = csv.DictReader(io.StringIO(texte, newline=""), delimiter=delimiteur)
    entetes = list(reader.fieldnames or [])
    log["entetes"] = entetes[:15]

    (champ_cp, champ_ville, champ_iris, champ_dep, champ_epci, champ_adresse, champ_siren,
     champ_lat, champ_lon, champ_circonscription) = _detecter_champs(entetes)
    log.update({"champ_cp": champ_cp, "champ_ville": champ_ville,
                "champ_iris": champ_iris, "champ_epci": champ_epci,
                "champ_adresse": champ_adresse,
                "champ_siren": champ_siren, "champ_lat": champ_lat,
                "champ_circonscription": champ_circonscription})

    if verbose:
        print(f"  En-têtes détectés : {entetes[:10]}")
        if champ_iris:
            print(f"  Champ IRIS trouvé : {champ_iris}")
        elif champ_epci:
            print(f"  Champ EPCI trouvé : {champ_epci}")
        elif champ_adresse:
            print(f"  Champ adresse trouvé : {champ_adresse}")
        elif champ_siren:
            print(f"  Champ SIREN trouvé : {champ_siren}")
        elif champ_lat:
            desc = champ_lat if champ_lon is None else f"{champ_lat} + {champ_lon}"
            print(f"  Champ géo trouvé : {desc}")
        elif champ_circonscription:
            print(f"  Champ circonscription trouvé : {champ_circonscription}")
        else:
            print(f"  Champ CP trouvé : {champ_cp} | Champ ville trouvé : {champ_ville}")

    try:
        nb_total, nb_rm, exemples, premieres_lignes = _compter_lignes_rm(
            reader, champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse,
            champ_siren=champ_siren, champ_epci=champ_epci,
            champ_lat=champ_lat, champ_lon=champ_lon,
            champ_circonscription=champ_circonscription,
        )
    except csv.Error as e:
        log["erreur"] = f"parsing CSV : {e}"
        log_analyse(log)
        if verbose:
            print(f"  (Erreur de parsing CSV : {e})")
        return None

    log.update({"nb_total": nb_total, "nb_rm": nb_rm})
    log_analyse(log)
    return _construire_resultat(champ_cp, champ_ville, champ_iris, champ_adresse,
                                nb_total, nb_rm, exemples, premieres_lignes,
                                champ_siren=champ_siren, champ_epci=champ_epci,
                                champ_lat=champ_lat, champ_lon=champ_lon,
                                champ_circonscription=champ_circonscription,
                                champ_dep=champ_dep)


def analyser_csv(url: str, verbose: bool = True,
                 dataset_id: str = "", titre: str = "") -> dict | None:
    chemin, taille_mo, depuis_cache, erreur = _telecharger(url, verbose)
    if erreur:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre, "erreur": erreur})
        if verbose:
            print(f"  (Échec : {erreur})")
        return None
    with open(chemin, "rb") as fp:
        preambule = fp.read(8192)
    with open(chemin, "rb") as fp:
        return _analyser_csv_depuis_stream(
            preambule, fp, verbose, dataset_id, titre, url, taille_mo, depuis_cache)


# ---------------------------------------------------------------------------
# Analyse ZIP / GZ / BZ2
# ---------------------------------------------------------------------------


def analyser_zip(url: str, verbose: bool = False,
                 dataset_id: str = "", titre: str = "") -> dict | None:
    chemin, taille_mo, depuis_cache, erreur = _telecharger(url, verbose, plafond_mo=None)
    if erreur:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre, "erreur": erreur})
        return None
    try:
        with zipfile.ZipFile(chemin) as zf:
            noms = [n for n in zf.namelist() if not n.startswith("__MACOSX")]
            membres_csv = [n for n in noms if n.lower().endswith(".csv")]
            membres_geo = [n for n in noms if n.lower().endswith(".geojson")]
            if not membres_csv and not membres_geo:
                if verbose:
                    print("  (ZIP : aucun fichier CSV ou GeoJSON trouvé)")
                return None
            meilleur = None
            for membre in membres_csv:
                if verbose:
                    print(f"  ZIP → {membre}")
                taille_membre = zf.getinfo(membre).file_size / 1024 / 1024
                with zf.open(membre) as fp:
                    preambule = fp.read(8192)
                with zf.open(membre) as fp:
                    result = _analyser_csv_depuis_stream(
                        preambule, fp, verbose, dataset_id, f"{titre} [{membre}]",
                        url=f"{url}#{membre}", taille_mo=taille_membre,
                    )
                if result is None:
                    continue
                if meilleur is None or result["nb_rm"] > meilleur["nb_rm"]:
                    meilleur = result
                if meilleur and meilleur["nb_rm"] > 0:
                    break
            for membre in membres_geo:
                if meilleur and meilleur["nb_rm"] > 0:
                    break
                if verbose:
                    print(f"  ZIP → {membre}")
                with zf.open(membre) as f:
                    contenu_membre = f.read()
                result = _analyser_contenu_geojson(
                    contenu_membre, verbose, dataset_id, f"{titre} [{membre}]",
                    url=f"{url}#{membre}", taille_mo=len(contenu_membre) / 1024 / 1024
                )
                if result is None:
                    continue
                if meilleur is None or result["nb_rm"] > meilleur["nb_rm"]:
                    meilleur = result
            return meilleur
    except zipfile.BadZipFile:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                     "erreur": "archive ZIP invalide"})
        return None


def analyser_gz(url: str, verbose: bool = False,
                dataset_id: str = "", titre: str = "") -> dict | None:
    chemin, taille_mo, depuis_cache, erreur = _telecharger(url, verbose)
    if erreur:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre, "erreur": erreur})
        return None
    try:
        with gzip.open(chemin, "rb") as fp:
            preambule = fp.read(8192)
    except Exception as e:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                     "erreur": f"décompression GZ : {e}"})
        if verbose:
            print(f"  (Erreur décompression GZ : {e})")
        return None
    try:
        with gzip.open(chemin, "rb") as fp:
            return _analyser_csv_depuis_stream(
                preambule, fp, verbose, dataset_id, titre, url=url, taille_mo=taille_mo)
    except Exception as e:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                     "erreur": f"parsing GZ : {e}"})
        if verbose:
            print(f"  (Erreur parsing GZ : {e})")
        return None


def analyser_bz2(url: str, verbose: bool = False,
                 dataset_id: str = "", titre: str = "") -> dict | None:
    chemin, taille_mo, depuis_cache, erreur = _telecharger(url, verbose)
    if erreur:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre, "erreur": erreur})
        return None
    try:
        with bz2.open(chemin, "rb") as fp:
            preambule = fp.read(8192)
    except Exception as e:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                     "erreur": f"décompression BZ2 : {e}"})
        if verbose:
            print(f"  (Erreur décompression BZ2 : {e})")
        return None
    try:
        with bz2.open(chemin, "rb") as fp:
            return _analyser_csv_depuis_stream(
                preambule, fp, verbose, dataset_id, titre, url=url, taille_mo=taille_mo)
    except Exception as e:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                     "erreur": f"parsing BZ2 : {e}"})
        if verbose:
            print(f"  (Erreur parsing BZ2 : {e})")
        return None


# ---------------------------------------------------------------------------
# Analyse XLSX
# ---------------------------------------------------------------------------


def analyser_xlsx(url: str, verbose: bool = False,
                  dataset_id: str = "", titre: str = "") -> dict | None:
    try:
        import openpyxl
    except ImportError:
        if verbose:
            print("  (openpyxl non installé — pip install openpyxl)")
        return None

    chemin, taille_mo, depuis_cache, erreur = _telecharger(url, verbose)
    if erreur:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre, "erreur": erreur})
        return None

    log = {"url": url, "dataset_id": dataset_id, "titre": titre,
           "taille_mo": round(taille_mo, 2), "cache": depuis_cache}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(chemin, read_only=True, data_only=True)
        ws = wb.active
        lignes = list(ws.iter_rows(values_only=True))
        wb.close()
    except Exception as e:
        log["erreur"] = f"lecture XLSX : {e}"
        log_analyse(log)
        if verbose:
            print(f"  (Erreur lecture XLSX : {e})")
        return None

    if not lignes:
        return None

    entetes = [str(c or "").strip() for c in lignes[0]]
    log["entetes"] = entetes[:15]

    (champ_cp, champ_ville, champ_iris, champ_dep, champ_epci, champ_adresse, champ_siren,
     champ_lat, champ_lon, champ_circonscription) = _detecter_champs(entetes)
    log.update({"champ_cp": champ_cp, "champ_ville": champ_ville,
                "champ_iris": champ_iris, "champ_epci": champ_epci,
                "champ_adresse": champ_adresse,
                "champ_siren": champ_siren, "champ_lat": champ_lat,
                "champ_circonscription": champ_circonscription})

    if verbose:
        print(f"  En-têtes XLSX : {entetes[:10]}")
        if champ_iris:
            print(f"  Champ IRIS trouvé : {champ_iris}")
        elif champ_epci:
            print(f"  Champ EPCI trouvé : {champ_epci}")
        elif champ_adresse:
            print(f"  Champ adresse trouvé : {champ_adresse}")
        elif champ_siren:
            print(f"  Champ SIREN trouvé : {champ_siren}")
        elif champ_lat:
            print(f"  Champ géo : {champ_lat}" + (f" + {champ_lon}" if champ_lon else ""))
        elif champ_circonscription:
            print(f"  Champ circonscription trouvé : {champ_circonscription}")
        else:
            print(f"  Champ CP : {champ_cp} | Champ ville : {champ_ville}")

    def _lignes_en_dicts():
        for row in lignes[1:]:
            yield dict(zip(entetes, (str(v or "").strip() for v in row)))

    nb_total, nb_rm, exemples, premieres_lignes = _compter_lignes_rm(
        _lignes_en_dicts(), champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse,
        champ_siren=champ_siren, champ_epci=champ_epci,
        champ_lat=champ_lat, champ_lon=champ_lon,
        champ_circonscription=champ_circonscription,
    )
    log.update({"nb_total": nb_total, "nb_rm": nb_rm})
    log_analyse(log)
    return _construire_resultat(champ_cp, champ_ville, champ_iris, champ_adresse,
                                nb_total, nb_rm, exemples, premieres_lignes,
                                champ_siren=champ_siren, champ_epci=champ_epci,
                                champ_lat=champ_lat, champ_lon=champ_lon,
                                champ_circonscription=champ_circonscription)


# ---------------------------------------------------------------------------
# Analyse Parquet
# ---------------------------------------------------------------------------


def analyser_parquet(url: str, verbose: bool = False,
                     dataset_id: str = "", titre: str = "") -> dict | None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        import pyarrow.compute as pc
        import fsspec
    except ImportError:
        if verbose:
            print("  (pyarrow/fsspec non installé — pip install pyarrow fsspec)")
        return None

    log: dict = {"url": url, "dataset_id": dataset_id, "titre": titre}

    try:
        fs, fpath = fsspec.url_to_fs(url)

        with fs.open(fpath, "rb") as f:
            pf = pq.ParquetFile(f)
            cols = [field.name for field in pf.schema_arrow]
            nb_total = pf.metadata.num_rows

        log["entetes"] = cols[:15]

        (champ_cp, champ_ville, champ_iris, champ_dep, champ_epci, champ_adresse, champ_siren,
         champ_lat, champ_lon, champ_circonscription) = _detecter_champs(cols)

        if verbose:
            print(f"  Colonnes Parquet ({len(cols)}) : {cols[:10]}")
            if champ_iris:
                print(f"  Champ IRIS : {champ_iris}")
            elif champ_ville:
                print(f"  Champ commune : {champ_ville}")

        if not any([champ_iris, champ_cp, champ_ville, champ_epci, champ_adresse, champ_siren,
                    champ_lat, champ_circonscription]):
            log["erreur"] = "aucun champ géo détecté"
            log_analyse(log)
            return None

        cols_a_lire = [c for c in [champ_iris, champ_cp, champ_ville, champ_epci] if c][:4]
        filtres = None
        if "COMMUNE" in cols and champ_iris:
            filtres = [("COMMUNE", "in", list(CODES_INSEE_RM))]
            if "COMMUNE" not in cols_a_lire:
                cols_a_lire = ["COMMUNE"] + cols_a_lire

        table = pq.read_table(fpath, filesystem=fs, columns=cols_a_lire or None, filters=filtres)

        if champ_iris and champ_iris in table.schema.names:
            iris_col = table.column(champ_iris)
            commune_codes = pc.utf8_slice_codeunits(iris_col, 0, 5)
            rm_mask = pc.is_in(commune_codes, value_set=pa.array(sorted(CODES_INSEE_RM)))
            nb_rm = int(pc.sum(rm_mask.cast(pa.int64())).as_py() or 0)
        elif filtres:
            nb_rm = len(table)
        elif champ_cp and champ_cp in table.schema.names:
            cp_col = table.column(champ_cp).cast(pa.string())
            rm_mask = pc.is_in(cp_col, value_set=pa.array(sorted(CODES_POSTAUX_RM)))
            nb_rm = int(pc.sum(rm_mask.cast(pa.int64())).as_py() or 0)
        else:
            nb_rm = 0

        sample_size = min(5, len(table))
        sample_rows = table.slice(0, sample_size).to_pylist()
        premieres_lignes = sample_rows
        exemples = sample_rows[:3] if nb_rm > 0 else []

        log.update({"nb_total": nb_total, "nb_rm": nb_rm, "champ_iris": champ_iris})
        log_analyse(log)

        return _construire_resultat(
            champ_cp, champ_ville, champ_iris, champ_adresse,
            nb_total, nb_rm, exemples, premieres_lignes,
            champ_siren=champ_siren, champ_epci=champ_epci,
            champ_lat=champ_lat, champ_lon=champ_lon,
            champ_circonscription=champ_circonscription,
        )

    except Exception as e:
        log["erreur"] = str(e)
        log_analyse(log)
        if verbose:
            print(f"  (Erreur Parquet : {e})")
        return None


# ---------------------------------------------------------------------------
# Analyse GeoJSON
# ---------------------------------------------------------------------------


def _analyser_features_geojson(features: list, verbose: bool,
                                dataset_id: str, titre: str) -> dict | None:
    if not features:
        return None

    entetes = list((features[0].get("properties") or {}).keys())
    (champ_cp, champ_ville, champ_iris, champ_dep, champ_epci, champ_adresse, champ_siren,
     champ_lat, champ_lon, champ_circonscription) = _detecter_champs(entetes)

    if verbose:
        print(f"  Propriétés GeoJSON : {entetes[:10]}")
        if champ_iris:
            print(f"  Champ IRIS : {champ_iris}")
        elif champ_epci:
            print(f"  Champ EPCI : {champ_epci}")
        elif champ_adresse:
            print(f"  Champ adresse : {champ_adresse}")
        elif champ_cp or champ_ville:
            print(f"  Champ CP : {champ_cp} | Champ ville : {champ_ville}")
        else:
            print("  Aucun champ géographique textuel — fallback coordonnées géométrie")

    if (champ_cp or champ_ville or champ_iris or champ_epci or champ_adresse or champ_siren
            or champ_lat or champ_circonscription):
        rows = (f.get("properties") or {} for f in features)
        nb_total, nb_rm, exemples, premieres_lignes = _compter_lignes_rm(
            rows, champ_cp, champ_ville, champ_iris, champ_dep, champ_adresse,
            champ_siren=champ_siren, champ_epci=champ_epci,
            champ_lat=champ_lat, champ_lon=champ_lon,
            champ_circonscription=champ_circonscription,
        )
    else:
        nb_total, nb_rm = 0, 0
        exemples, premieres_lignes = [], []
        for f in features:
            props = f.get("properties") or {}
            nb_total += 1
            if len(premieres_lignes) < 5:
                premieres_lignes.append(props)
            pt = _coords_centroide(f.get("geometry") or {})
            if pt is not None:
                lon, lat = pt
                if _RM_LAT_MIN <= lat <= _RM_LAT_MAX and _RM_LON_MIN <= lon <= _RM_LON_MAX:
                    nb_rm += 1
                    if len(exemples) < 3:
                        exemples.append(props)
        champ_lat = "geometry"

    return _construire_resultat(
        champ_cp, champ_ville, champ_iris, champ_adresse,
        nb_total, nb_rm, exemples, premieres_lignes,
        champ_siren=champ_siren, champ_epci=champ_epci,
        champ_lat=champ_lat, champ_lon=champ_lon,
        champ_circonscription=champ_circonscription,
    )


def _analyser_contenu_geojson(contenu: bytes, verbose: bool,
                               dataset_id: str, titre: str,
                               url: str = "", taille_mo: float = 0) -> dict | None:
    log = {"url": url, "dataset_id": dataset_id, "titre": titre,
           "taille_mo": round(taille_mo, 2)}
    try:
        data = json.loads(contenu.decode("utf-8", errors="replace"))
    except Exception as e:
        log["erreur"] = f"parsing JSON : {e}"
        log_analyse(log)
        if verbose:
            print(f"  (Erreur parsing GeoJSON : {e})")
        return None
    features = data.get("features", [])
    if not features:
        log["erreur"] = "aucune feature"
        log_analyse(log)
        if verbose:
            print("  (GeoJSON : aucune feature)")
        return None
    result = _analyser_features_geojson(features, verbose, dataset_id, titre)
    if result:
        log.update({"nb_total": result["nb_total"], "nb_rm": result["nb_rm"]})
    log_analyse(log)
    return result


def analyser_geojson(url: str, verbose: bool = False,
                     dataset_id: str = "", titre: str = "") -> dict | None:
    chemin, taille_mo, depuis_cache, erreur = _telecharger(url, verbose)
    if erreur:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre, "erreur": erreur})
        if verbose:
            print(f"  (Échec : {erreur})")
        return None
    with open(chemin, "rb") as f:
        contenu = f.read()
    return _analyser_contenu_geojson(contenu, verbose, dataset_id, titre, url, taille_mo)


# ---------------------------------------------------------------------------
# Analyse WFS
# ---------------------------------------------------------------------------


def _wfs_base_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    ogc_keys = {"service", "request", "version", "typename", "typenames",
                "outputformat", "bbox", "maxfeatures", "count", "srsname"}
    qs_filtre = {k: v for k, v in qs.items() if k.lower() not in ogc_keys}
    query = "&".join(f"{k}={v[0]}" for k, v in qs_filtre.items())
    return urlunparse(parsed._replace(query=query))


def _wfs_get_layers(base_url: str, verbose: bool) -> list[str]:
    sep = "&" if "?" in base_url else "?"
    caps_url = f"{base_url}{sep}SERVICE=WFS&REQUEST=GetCapabilities"
    try:
        resp = session.get(caps_url, timeout=20)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        if verbose:
            print(f"  (WFS GetCapabilities échoué : {e})")
        return []
    layers = []
    for el in root.iter():
        if el.tag.endswith("}FeatureType"):
            name_el = next((c for c in el if c.tag.endswith("}Name")), None)
            if name_el is not None and name_el.text:
                layers.append(name_el.text.strip())
    return layers


def _wfs_query_layer(base_url: str, layer: str, verbose: bool,
                     dataset_id: str, titre: str) -> dict | None:
    tentatives = [
        {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
         "TYPENAMES": layer, "BBOX": f"{_WFS_RM_BBOX},EPSG:4326",
         "outputFormat": "application/json", "count": "500"},
        {"SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature",
         "TYPENAME": layer, "BBOX": f"{_WFS_RM_BBOX},EPSG:4326",
         "outputFormat": "application/json", "MAXFEATURES": "500"},
        {"SERVICE": "WFS", "VERSION": "1.0.0", "REQUEST": "GetFeature",
         "TYPENAME": layer, "BBOX": _WFS_RM_BBOX,
         "outputFormat": "GeoJSON", "MAXFEATURES": "500"},
    ]
    sep = "&" if "?" in base_url else "?"
    for params in tentatives:
        try:
            resp = session.get(f"{base_url}{sep}{urlencode(params)}", timeout=30)
            if resp.status_code != 200:
                continue
            ct = resp.headers.get("content-type", "")
            body = resp.content.lstrip()
            if "json" not in ct and not body.startswith(b"{"):
                continue
            data = resp.json()
            features = data.get("features")
            if features is None:
                continue
            if verbose:
                print(f"  WFS {layer} (v{params['VERSION']}) : {len(features)} features dans bbox RM")
            if not features:
                return _construire_resultat(None, None, None, None, 0, 0, [], [])
            return _analyser_features_geojson(features, verbose, dataset_id, titre)
        except Exception:
            continue
    return None


def analyser_wfs(url: str, verbose: bool = False,
                 dataset_id: str = "", titre: str = "") -> dict | None:
    base_url = _wfs_base_url(url)
    if verbose:
        print(f"  WFS base URL : {base_url}")
    layers = _wfs_get_layers(base_url, verbose)
    if not layers:
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                     "erreur": "WFS : aucune couche trouvée (GetCapabilities échoué ou vide)"})
        return None
    if verbose:
        print(f"  WFS couches ({len(layers)}) : {layers[:10]}")
    meilleur = None
    for layer in layers[:10]:
        result = _wfs_query_layer(base_url, layer, verbose, dataset_id, titre)
        if result is None:
            continue
        if meilleur is None or result["nb_rm"] > meilleur["nb_rm"]:
            meilleur = result
        if meilleur["nb_rm"] > 0:
            break
    log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                 "nb_rm": meilleur["nb_rm"] if meilleur else 0})
    if meilleur is not None:
        meilleur["type"] = "wfs"
        meilleur["url"] = base_url
        meilleur["wfs_layers"] = layers[:10]
    return meilleur


# ---------------------------------------------------------------------------
# Analyse WMS
# ---------------------------------------------------------------------------


def analyser_wms(url: str, verbose: bool = False,
                 dataset_id: str = "", titre: str = "") -> dict | None:
    base_url = nettoyer_url_ogc(url)
    if verbose:
        print(f"  WMS base URL : {base_url}")
    try:
        caps = wms_get_capabilities(base_url, timeout=20)
    except Exception as e:
        if verbose:
            print(f"  WMS GetCapabilities échoué : {e}")
        log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                     "erreur": f"WMS GetCapabilities échoué : {e}"})
        return None
    couches_rm = wms_couches_dans_rm(caps, base_url=base_url)
    nb_couches = len(couches_rm)
    if verbose:
        print(f"  WMS : {nb_couches} couche(s) dans bbox RM")
    log_analyse({"url": url, "dataset_id": dataset_id, "titre": titre,
                 "wms_couches_rm": nb_couches})
    return {
        "type": "wms",
        "url": base_url,
        "titre_service": caps.get("titre", titre),
        "couches": couches_rm,
        "nb_couches_rm": nb_couches,
        "metadata_urls": caps.get("metadata_urls", []),
        "champ_cp": None,
        "champ_ville": None,
        "champ_iris": None,
        "champ_adresse": None,
        "nb_total": nb_couches,
        "nb_rm": nb_couches,  # couches bbox-chevauchant RM ≡ "lignes RM" pour un WMS
        "exemples": [],
        "premieres_lignes": [],
    }


# ---------------------------------------------------------------------------
# Registre des analyseurs
# ---------------------------------------------------------------------------

_ANALYSEURS = {
    "csv":     analyser_csv,
    "zip":     analyser_zip,
    "gz":      analyser_gz,
    "bz2":     analyser_bz2,
    "xlsx":    analyser_xlsx,
    "geojson": analyser_geojson,
    "wfs":     analyser_wfs,
    "wms":     analyser_wms,
    "parquet": analyser_parquet,
}


# ---------------------------------------------------------------------------
# Analyse complète d'un dataset
# ---------------------------------------------------------------------------

_MOTS_DICT_TITRE = {
    "dictionnaire", "dict", "codebook", "code book",
    "description des colonnes", "description des champs",
    "nomenclature", "metadonnee", "metadonnees",
}


def _est_dict_titre(titre: str) -> bool:
    t = normaliser(titre)
    return any(m in t for m in _MOTS_DICT_TITRE)


def analyser_dataset(dataset: dict, verbose: bool = False) -> dict | None:
    nb_rm_total = 0
    meilleur = None

    for res in dataset.get("resources", []):
        fmt = _format_analysable(res)
        if not fmt:
            continue
        url = res.get("url", "")
        if not url:
            continue
        if _est_dict_titre(res.get("title", "")):
            continue
        result = _ANALYSEURS[fmt](url, verbose, dataset["id"], dataset["title"])
        if result is None:
            continue
        nb_rm_total += result["nb_rm"]
        if meilleur is None or result["nb_rm"] > meilleur["nb_rm"]:
            meilleur = result

    if meilleur is None:
        return None
    return {**meilleur, "nb_rm": nb_rm_total,
            "last_modified": dataset.get("last_modified", "")}
