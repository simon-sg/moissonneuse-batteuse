"""
Connecteur OEB — Observatoire de l'Environnement en Bretagne
Portail data-fair/Koumoul : https://data.bretagne-environnement.fr

API data-fair v1 :
  GET /data-fair/api/v1/datasets                    → liste des JDD publiés
  GET /data-fair/api/v1/datasets/{slug}             → métadonnées d'un JDD
  GET /data-fair/api/v1/datasets/{slug}/lines       → lignes (paginé, filtrable)

Filtrage géographique :
  Les JDD OEB exposent généralement deux colonnes de territoire :
    - echelle_territoire : "Communes" | "EPCI" | "Département" | "Région" | "SAGE"
    - code_territoire    : code INSEE 5c (communes) ou EPCI 9c (243500139 pour RM)
  On télécharge les lignes Communes (filtrées localement) + EPCI Rennes Métropole.
"""
import csv
import io

from connectors.http import session
from conf.communes_rm import CODES_INSEE_RM

BASE_URL = "https://data.bretagne-environnement.fr"
_API = f"{BASE_URL}/data-fair/api/v1/datasets"
EPCI_CODE_RM = "243500139"

_HEADERS = {"User-Agent": "moissonneuse-batteuse/1.0 (projet open-data Rennes Métropole)"}
_TIMEOUT_META = 20
_TIMEOUT_DATA = 60
_PAGE_SIZE = 1000


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def lister_datasets(taille: int = 100, page: int = 0) -> dict:
    """Retourne la page `page` du catalogue OEB (dict API brut avec `results` et `total`)."""
    r = session.get(
        _API,
        params={
            "size": taille,
            "page": page,
            "select": "id,title,description,updatedAt,license",
            "status": "finalized",
        },
        headers=_HEADERS,
        timeout=_TIMEOUT_META,
    )
    r.raise_for_status()
    return r.json()


def get_dataset_info(slug: str) -> dict:
    """Retourne les métadonnées complètes d'un JDD OEB."""
    r = session.get(f"{_API}/{slug}", headers=_HEADERS, timeout=_TIMEOUT_META)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Téléchargement paginé
# ---------------------------------------------------------------------------

def _telecharger_pages(slug: str, params: dict) -> list[dict]:
    """Télécharge toutes les pages de résultats pour des paramètres de filtre donnés."""
    p = {**params, "size": _PAGE_SIZE, "page": 0}
    r = session.get(f"{_API}/{slug}/lines", params=p, headers=_HEADERS, timeout=_TIMEOUT_DATA)
    r.raise_for_status()
    data = r.json()
    total = data.get("total", 0)
    resultats: list[dict] = list(data.get("results", []))

    page = 1
    while len(resultats) < total:
        p["page"] = page
        r = session.get(f"{_API}/{slug}/lines", params=p, headers=_HEADERS, timeout=_TIMEOUT_DATA)
        r.raise_for_status()
        batch = r.json().get("results", [])
        if not batch:
            break
        resultats.extend(batch)
        page += 1
        print(f"    {len(resultats)}/{total}...", end="\r")

    if total > _PAGE_SIZE:
        print()
    return resultats


def telecharger_lignes_rm(
    slug: str,
    champ_code: str = "code_territoire",
    champ_echelle: str | None = "echelle_territoire",
) -> list[dict]:
    """Télécharge les lignes pour Rennes Métropole.

    Stratégie :
    1. Télécharge toutes les lignes à l'échelle Communes (ou toutes si pas d'echelle),
       puis filtre localement les 43 codes INSEE RM.
    2. Télécharge les lignes EPCI pour le code EPCI RM (243500139), si champ_echelle défini.
    Retourne la liste combinée (communes + EPCI).
    """
    codes_rm = set(CODES_INSEE_RM)
    lignes_rm: list[dict] = []

    # --- Données à l'échelle Communes ---
    filtres: dict = {}
    if champ_echelle:
        filtres[champ_echelle] = "Communes"

    print(f"    Communes ({champ_echelle or 'sans filtre échelle'})...", end="\r")
    toutes = _telecharger_pages(slug, filtres)
    avant = len(lignes_rm)
    for ligne in toutes:
        raw = ligne.get(champ_code, "")
        # Le portail OEB stocke les codes INSEE comme entiers ou chaînes
        code = str(raw).strip().zfill(5) if raw != "" else ""
        if code in codes_rm:
            lignes_rm.append(ligne)
    print(f"    {len(lignes_rm) - avant}/{len(toutes)} lignes Communes RM retenues")

    # --- Données à l'échelle EPCI ---
    if champ_echelle:
        filtres_epci = {champ_echelle: "EPCI", champ_code: EPCI_CODE_RM}
        print(f"    EPCI RM ({EPCI_CODE_RM})...", end="\r")
        epci = _telecharger_pages(slug, filtres_epci)
        if epci:
            lignes_rm.extend(epci)
            print(f"    + {len(epci)} ligne(s) EPCI")
        else:
            print(f"    0 ligne EPCI                    ")

    return lignes_rm


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------

def lignes_vers_csv(lignes: list[dict], chemin: str) -> list[str]:
    """Écrit les lignes (liste de dicts) en CSV UTF-8-sig. Retourne les noms de colonnes."""
    if not lignes:
        return []
    colonnes = list(lignes[0].keys())
    with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";")
        w.writeheader()
        w.writerows(lignes)
    return colonnes
