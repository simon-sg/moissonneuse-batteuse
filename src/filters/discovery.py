"""
Filtres et utilitaires pour la découverte de JDD sur data.gouv.fr.

Regroupe la logique de pré-filtrage, de recherche, de détection de périmètre
géographique (org/titre hors RM, zones spatiales) et de téléchargement d'extraits.
Fonctions partagées entre discover.py (session interactive/automatique) et
dashboard.py.

Aucune dépendance vers discover.py — peut être importé sans risque de cycle.
"""

import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conf.communes_rm import COMMUNES_RM
from conf.discover import (
    MARQUEURS_TITRE, MARQUEURS_DESC, MARQUEURS_ENTETES_SUBSTR, MARQUEURS_ENTETES,
    TITRES_HORS_RM, ORGS_EXCLUES,
    _MOTS_DESC_COMMUNE, _MAGIC_BINAIRE,
    ZONES_INCLUANT_RM, NB_PAGES, PAGE_SIZE,
)
from connectors.analyseurs import _format_analysable, analyser_dataset
from connectors.http import session
from filters.geographic import normaliser, est_commune_rm

# Ensemble normalisé des noms de communes RM (pour la recherche dans les adresses)
# Nb: aussi défini dans filters/geographic.py pour est_adresse_rm().
_COMMUNES_NORM_RM = {normaliser(c) for c in COMMUNES_RM}


# ---------------------------------------------------------------------------
# Pré-filtrage automatique (description + en-têtes CSV)
# ---------------------------------------------------------------------------


def _contient_marqueurs_geo(dataset: dict) -> bool:
    titre = normaliser(dataset.get("title", "") or "")
    desc = normaliser(dataset.get("description", "") or "")
    if any(m in titre for m in MARQUEURS_TITRE):
        return True
    return any(m in desc for m in MARQUEURS_DESC)


def _telecharger_entetes(url: str) -> list[str] | None:
    try:
        resp = requests.get(url, stream=True, timeout=10)
        if resp.status_code != 200:
            return None
        contenu = b""
        for chunk in resp.iter_content(chunk_size=4096):
            contenu += chunk
            if b"\n" in contenu or len(contenu) > 16384:
                break
        resp.close()
        if contenu[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0") or contenu[:2] == b"\x1f\x8b":
            return None
        texte = contenu.decode("utf-8", errors="replace")
        if texte.count("�") >= 1:
            texte = contenu.decode("latin-1")
        premiere_ligne = texte.split("\n")[0].strip().strip("\r")
        if not premiere_ligne:
            return None
        nb_cols, delimiteur = 0, ","
        for sep in (";", "\t", "|", ","):
            n = len(premiere_ligne.split(sep))
            if n > nb_cols:
                nb_cols, delimiteur = n, sep
        return [e.strip().strip('"').strip("'") for e in premiere_ligne.split(delimiteur)]
    except Exception:
        return None


def _telecharger_schema_parquet(url: str) -> list[str] | None:
    try:
        import pyarrow.parquet as pq
        import fsspec
        with fsspec.open(url, "rb") as f:
            return [field.name for field in pq.ParquetFile(f).schema_arrow]
    except Exception:
        return None


def pre_filtrer(dataset: dict) -> tuple[str, dict | None]:
    geo_en_description = _contient_marqueurs_geo(dataset)

    if not geo_en_description:
        geo_en_entetes = False
        for res in dataset.get("resources", []):
            fmt = (res.get("format") or "").lower()
            if fmt in ("geojson", "wfs", "wms"):
                geo_en_entetes = True
                break
            if "csv" in fmt:
                entetes = _telecharger_entetes(res.get("url", ""))
            elif "parquet" in fmt:
                entetes = _telecharger_schema_parquet(res.get("url", ""))
            else:
                continue
            if entetes:
                noms_norm = {normaliser(e) for e in entetes}
                if (noms_norm & MARQUEURS_ENTETES) or any(
                    s in nom for nom in noms_norm for s in MARQUEURS_ENTETES_SUBSTR
                ):
                    geo_en_entetes = True
                    break
        if not geo_en_entetes:
            return ("skip", None)

    result = analyser_dataset(dataset, verbose=False)
    if result is not None:
        return ("candidat", result) if result["nb_rm"] > 0 else ("presenter", result)
    return ("presenter", None)


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------


def rechercher_datasets(keyword: str, nb_pages: int = NB_PAGES) -> tuple[list, int]:
    return _paginer({"q": keyword}, nb_pages)


def _paginer(params_base: dict, nb_pages: int = NB_PAGES) -> tuple[list, int]:
    url = "https://www.data.gouv.fr/api/1/datasets/"
    tous = []
    total = 0
    for page in range(1, nb_pages + 1):
        params = {**params_base, "page_size": PAGE_SIZE, "page": page}
        try:
            response = session.get(url, params=params, timeout=30)
            if response.status_code == 404:
                break
            response.raise_for_status()
            data = response.json()
            resultats = data.get("data", [])
            total = data.get("total", 0)
            tous.extend(resultats)
            if len(resultats) < PAGE_SIZE:
                break
        except Exception as e:
            print(f"  (Erreur page {page} : {e} — page ignorée, poursuite)")
            continue
    return tous, total


def est_org_exclue(dataset: dict) -> bool:
    org = dataset.get("organization") or {}
    slug = org.get("slug", "")
    return any(exclu in slug for exclu in ORGS_EXCLUES)


def _mot_present(nom: str, mot: str) -> bool:
    return bool(re.search(r"(^| )" + re.escape(mot) + r"($| )", nom))


def est_org_hors_rm(dataset: dict) -> bool:
    org = dataset.get("organization") or {}
    nom = normaliser(org.get("name") or "")
    slug = (org.get("slug") or "").lower()

    if (_mot_present(nom, "departement")
            or _mot_present(nom, "conseil departemental")
            or slug.startswith("departement-")
            or slug.startswith("conseil-departemental-")):
        return "35" not in slug and "ille" not in slug

    if (_mot_present(nom, "region")
            or _mot_present(nom, "conseil regional")
            or slug.startswith("region-")
            or slug.startswith("conseil-regional-")):
        return "bretagne" not in slug and "bretagne" not in nom

    if ("agglomeration" in slug
            or "communaute-de-communes" in slug
            or "communaute-urbaine" in slug
            or "metropole" in slug
            or "agglomeration" in nom
            or "communaute de communes" in nom
            or "metropole" in nom):
        return "rennes" not in slug and "rennes" not in nom

    for prefix in ("ville de ", "ville d'",
                   "commune de ", "commune d'",
                   "mairie de ", "mairie d'",
                   "municipalite de ", "municipalite d'"):
        if nom.startswith(prefix):
            nom_commune = nom[len(prefix):]
            return not est_commune_rm(nom_commune)

    if any(region in nom for region in TITRES_HORS_RM):
        return True

    return False


def titre_hors_rm(dataset: dict) -> bool:
    titre = (dataset.get("title", "") or "").lower().strip()
    titre_norm = normaliser(titre)
    for pref in ("commune de ", "commune d'",
                 "mairie de ", "mairie d'",
                 "ville de ", "ville d'"):
        idx = titre_norm.find(pref)
        if idx >= 0:
            reste = titre_norm[idx + len(pref):]
            if not any(reste.startswith(c) for c in _COMMUNES_NORM_RM):
                return True
            break
    return any(region in titre for region in TITRES_HORS_RM)


def description_suggerant_commune(dataset: dict) -> bool:
    desc = (dataset.get("description", "") or "").lower()
    return any(mot in desc for mot in _MOTS_DESC_COMMUNE)


def est_exclu_par_terme(dataset: dict, termes: list[str]) -> bool:
    if not termes:
        return False
    titre = normaliser(dataset.get("title", "") or "")
    org = normaliser((dataset.get("organization") or {}).get("name", ""))
    return any(normaliser(t) in titre or normaliser(t) in org for t in termes)


def _filtrer_communs(datasets: list, decouverte: dict, ignorer_deja_vus: bool = False,
                      ids_ignores_supp: set | None = None, deja_vus: set | None = None) -> list:
    exclusions_termes = decouverte.get("exclusions_termes", [])
    exclus_ids = set(decouverte["exclus"])
    ids_ignores_supp = ids_ignores_supp or set()
    if deja_vus is None:
        deja_vus = set(decouverte["vus"])
    return [
        ds for ds in datasets
        if not est_org_exclue(ds)
        and not est_org_hors_rm(ds)
        and couvre_rennes(ds)
        and (not titre_hors_rm(ds) or description_suggerant_commune(ds))
        and not est_exclu_par_terme(ds, exclusions_termes)
        and (ignorer_deja_vus or ds["id"] not in deja_vus)
        and ds["id"] not in exclus_ids
        and ds["id"] not in ids_ignores_supp
    ]


def couvre_rennes(dataset: dict) -> bool:
    spatial = dataset.get("spatial") or {}
    zones = spatial.get("zones", [])

    if not zones:
        return True

    for zone in zones:
        if zone.startswith(("country:", "country-subset:")):
            return True
        if zone in ZONES_INCLUANT_RM:
            return True
        if zone.startswith("fr:commune:35"):
            return True

    return False


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------


def trouver_ressource_analysable(dataset: dict) -> dict | None:
    for r in dataset.get("resources", []):
        if _format_analysable(r):
            return r
    for r in dataset.get("resources", []):
        if (r.get("format") or "").lower() == "json":
            return r
    return None


def formats_disponibles(dataset: dict) -> list:
    fmts = set()
    for r in dataset.get("resources", []):
        fmt = (r.get("format") or "").upper()
        if fmt:
            fmts.add(fmt)
    return sorted(fmts)


# ---------------------------------------------------------------------------
# Extrait des données
# ---------------------------------------------------------------------------


def telecharger_extrait_csv(url: str, n_lignes: int = 5) -> str:
    try:
        response = requests.get(url, stream=True, timeout=15)
        response.raise_for_status()
        contenu = b""
        for chunk in response.iter_content(chunk_size=4096):
            contenu += chunk
            if contenu.count(b"\n") >= n_lignes + 1 or len(contenu) > 65536:
                break
        response.close()
        for magic in _MAGIC_BINAIRE:
            if contenu.startswith(magic):
                return "__BINAIRE__"
        texte = contenu.decode("utf-8", errors="replace")
        if texte.count("�") >= 1:
            texte = contenu.decode("latin-1")
        lignes = [l for l in texte.split("\n") if l.strip()][:n_lignes + 1]
        if lignes:
            premiere_norm = normaliser(lignes[0].split(",")[0].split(";")[0])
            if premiere_norm in ("colonne", "column", "champ", "field", "variable"):
                return "__DICTIONNAIRE__"
        return "\n".join(lignes)
    except Exception as e:
        return f"(Impossible de télécharger l'extrait : {e})"


def telecharger_extrait_json(url: str, n_lignes: int = 5) -> str:
    try:
        response = requests.get(url, stream=True, timeout=15)
        response.raise_for_status()
        contenu = b""
        for chunk in response.iter_content(chunk_size=4096):
            contenu += chunk
            if len(contenu) > 8192:
                break
        response.close()
        texte = contenu.decode("utf-8", errors="replace")
        debut = texte[:2000]
        return debut + "\n[... tronqué ...]"
    except Exception as e:
        return f"(Impossible de télécharger l'extrait : {e})"


def _obtenir_extrait_geojson(url: str) -> str:
    try:
        resp = requests.get(url, timeout=10, stream=True)
        resp.raise_for_status()
        contenu = b""
        for chunk in resp.iter_content(chunk_size=65536):
            contenu += chunk
            if len(contenu) > 512 * 1024:
                resp.close()
                break
        data = json.loads(contenu.decode("utf-8", errors="replace"))
        features = data.get("features", [])[:3]
        lignes = []
        for i, f in enumerate(features):
            props = f.get("properties") or {}
            geom_type = (f.get("geometry") or {}).get("type", "?")
            props_court = dict(list(props.items())[:5])
            lignes.append(f"[{i+1}] {geom_type} | {props_court}")
        return "\n".join(lignes) if lignes else "(aucune feature)"
    except Exception as e:
        return f"(erreur extrait GeoJSON : {e})"


def obtenir_extrait(ressource: dict) -> str:
    fmt = _format_analysable(ressource) or (ressource.get("format") or "").lower()
    url = ressource.get("url", "")
    if fmt == "csv":
        return telecharger_extrait_csv(url)
    elif fmt == "json":
        return telecharger_extrait_json(url)
    elif fmt == "geojson":
        return _obtenir_extrait_geojson(url)
    elif fmt == "wfs":
        return "(service WFS — voir résultat d'analyse)"
    elif fmt == "zip":
        return "(archive ZIP — voir résultat d'analyse)"
    elif fmt in ("xlsx", "excel"):
        return "(fichier Excel — voir résultat d'analyse)"
    elif fmt == "gz":
        return "(fichier compressé GZ — voir résultat d'analyse)"
    elif fmt == "bz2":
        return "(fichier compressé BZ2 — voir résultat d'analyse)"
    return "(format non supporté pour l'extrait)"
