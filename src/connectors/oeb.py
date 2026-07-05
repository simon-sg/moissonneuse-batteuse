"""
Connecteur OEB — Observatoire de l'Environnement en Bretagne
Portail data-fair/Koumoul : https://data.bretagne-environnement.fr

API data-fair v1 :
  GET /data-fair/api/v1/datasets                    → liste des JDD publiés
  GET /data-fair/api/v1/datasets/{slug}             → métadonnées d'un JDD
  GET /data-fair/api/v1/datasets/{slug}/lines       → lignes (paginé, filtrable)

Filtrage géographique via qs (Elasticsearch) :
  Le filtrage direct field=value ne fonctionne pas sur les champs numériques — on utilise
  le paramètre qs (Elasticsearch query string) :
    - Communes : qs=code_territoire:(35001 OR 35022 OR ...)  → 1 requête pour les 43 codes
    - EPCI RM  : qs=echelle_territoire:"EPCI" AND code_territoire:243500139
  Les JDD OEB exposent généralement :
    - echelle_territoire : "Communes" | "EPCI" | "Département" | "Région" | "SAGE"
    - code_territoire    : code INSEE (entier, communes) ou code EPCI (entier)
"""
import csv
import urllib.parse

from connectors.http import session
from conf.communes_rm import CODES_INSEE_RM

BASE_URL = "https://data.bretagne-environnement.fr"
_API = f"{BASE_URL}/data-fair/api/v1/datasets"
EPCI_CODE_RM = "243500139"
_ECHELLE_EPCI = "EPCI"

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

def _construire_url(slug: str, params: dict) -> str:
    """Construit l'URL de pagination en encodant qs avec %20 (pas +).

    requests utilise quote_plus (espaces → +) pour les params, mais l'Elasticsearch
    data-fair exige RFC 3986 (%20 pour les espaces). On construit l'URL à la main
    pour le paramètre qs seulement ; les autres params restent en quote_plus standard.
    """
    qs_value = params.get("qs")
    other = {k: v for k, v in params.items() if k != "qs"}
    base = f"{_API}/{slug}/lines?" + urllib.parse.urlencode(other)
    if qs_value:
        # safe=':()\"' — garde la syntaxe ES lisible, encode les espaces en %20 (pas +)
        base += "&qs=" + urllib.parse.quote(qs_value, safe=':()\"')
    return base


def _telecharger_pages(slug: str, params: dict) -> list[dict]:
    """Télécharge toutes les pages de résultats pour des paramètres de filtre donnés.

    Ce portail data-fair utilise une pagination 1-indexée : page=1 est la première page,
    page=0 déclenche une erreur Elasticsearch ("numHits must be > 0").
    """
    p = {**params, "size": _PAGE_SIZE, "page": 1}
    r = session.get(_construire_url(slug, p), headers=_HEADERS, timeout=_TIMEOUT_DATA)
    r.raise_for_status()
    data = r.json()
    total = data.get("total", 0)
    resultats: list[dict] = list(data.get("results", []))

    page = 2
    while len(resultats) < total:
        p["page"] = page
        r = session.get(_construire_url(slug, p), headers=_HEADERS, timeout=_TIMEOUT_DATA)
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
    """Télécharge les lignes pour Rennes Métropole via Elasticsearch (qs), code par code.

    La requête OR groupée (35001 OR 35022 OR ...) avec size>1 déclenche un bug
    Elasticsearch dans data-fair ("numHits must be > 0 on all shards") — on contourne
    en faisant 43 requêtes individuelles (une par code INSEE RM) + 1 pour l'EPCI.
    Chaque commune ayant ≤ 1000 lignes, chaque requête tient en une seule page.
    """
    lignes_rm: list[dict] = []
    codes = sorted(CODES_INSEE_RM)
    n = len(codes)

    for i, code in enumerate(codes, 1):
        qs = f"{champ_code}:{int(code)}"
        print(f"    Communes RM {i}/{n}...", end="\r")
        lignes_rm.extend(_telecharger_pages(slug, {"qs": qs}))

    print(f"    {len(lignes_rm)} lignes communes RM ({n} communes)")

    # Données EPCI Rennes Métropole
    if champ_echelle:
        qs_epci = f'{champ_echelle}:"{_ECHELLE_EPCI}" AND {champ_code}:{EPCI_CODE_RM}'
        print(f"    Lignes EPCI RM...", end="\r")
        epci = _telecharger_pages(slug, {"qs": qs_epci})
        if epci:
            lignes_rm.extend(epci)
            print(f"    + {len(epci)} ligne(s) EPCI")
        else:
            print(f"    0 ligne EPCI         ")

    return lignes_rm


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------

def lignes_vers_csv(lignes: list[dict], chemin: str) -> list[str]:
    """Écrit les lignes (liste de dicts) en CSV UTF-8-sig. Retourne les noms de colonnes.

    Prend l'union de toutes les colonnes (communes et EPCI peuvent avoir des schémas
    légèrement différents). Les champs internes data-fair (_i, _rand, _score, _id)
    sont exclus du CSV.
    """
    if not lignes:
        return []
    _CHAMPS_INTERNES = {"_i", "_rand", "_score", "_id"}
    colonnes = list(dict.fromkeys(
        k for ligne in lignes for k in ligne.keys() if k not in _CHAMPS_INTERNES
    ))
    with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";",
                           extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(lignes)
    return colonnes
