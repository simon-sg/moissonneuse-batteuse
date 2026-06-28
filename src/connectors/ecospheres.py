"""
Connecteur écosphères : portail de données environnementales du MTECT.
API CKAN : https://ecospheres.data.gouv.fr/api/3/action/

Format retourné par _normaliser() est compatible avec le pipeline discover.py
(mêmes clés que les datasets data.gouv.fr : id, title, description, organization,
resources, spatial, last_modified, license).
Les IDs sont préfixés "ecospheres:" pour éviter toute collision avec data.gouv.fr.
"""

import requests

ECOSPHERES_API = "https://ecospheres.data.gouv.fr/api/3/action"
ID_PREFIX = "ecospheres:"
_PAGE_SIZE = 100

# Requêtes lancées sur le catalogue écosphères (déduplication globale ensuite).
# La requête vide ("") scanne tout le catalogue — utile car les datasets
# environnementaux ne mentionnent pas toujours "commune" dans leur titre.
_REQUETES = [
    {"q": "",           "label": "tous"},
    {"q": "commune",    "label": "commune"},
    {"q": "iris",       "label": "iris"},
    {"q": "adresse",    "label": "adresse"},
    {"q": "code insee", "label": "code insee"},
]


def chercher_datasets(nb_pages: int = 30) -> list[dict]:
    """
    Parcourt les requêtes et retourne les datasets normalisés (dédupliqués).
    Retourne une liste vide si le portail est inaccessible.
    """
    vus: set[str] = set()
    datasets: list[dict] = []
    for req in _REQUETES:
        print(f"  écosphères [{req['label']}]...", end=" ", flush=True)
        try:
            resultats = list(_paginer(req["q"], nb_pages))
        except Exception as e:
            print(f"erreur ({e})")
            continue
        nouveaux = 0
        for pkg in resultats:
            pid = ID_PREFIX + pkg["id"]
            if pid not in vus:
                vus.add(pid)
                datasets.append(_normaliser(pkg))
                nouveaux += 1
        print(f"{nouveaux} nouveaux ({len(resultats)} récupérés)")
    return datasets


def recup_dataset(dataset_id: str) -> dict | None:
    """Récupère les métadonnées d'un dataset (pour retry des analyses échouées)."""
    ckan_id = dataset_id.removeprefix(ID_PREFIX)
    try:
        r = requests.get(
            f"{ECOSPHERES_API}/package_show",
            params={"id": ckan_id},
            timeout=10,
        )
        if r.ok:
            return _normaliser(r.json()["result"])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Interne
# ---------------------------------------------------------------------------

def _paginer(q: str, nb_pages: int):
    """Générateur : yield chaque package CKAN page par page (start + rows)."""
    for page in range(nb_pages):
        try:
            r = requests.get(
                f"{ECOSPHERES_API}/package_search",
                params={"q": q, "rows": _PAGE_SIZE, "start": page * _PAGE_SIZE},
                timeout=15,
            )
            if not r.ok:
                break
            results = r.json().get("result", {}).get("results", [])
            yield from results
            if len(results) < _PAGE_SIZE:
                break
        except Exception as e:
            print(f"\n  (écosphères erreur page {page}: {e})")
            break


def _normaliser(pkg: dict) -> dict:
    """Transforme un package CKAN en dict compatible avec le pipeline discover.py."""
    org = pkg.get("organization") or {}
    return {
        "id": ID_PREFIX + pkg["id"],
        "title": pkg.get("title", ""),
        "description": pkg.get("notes", ""),
        "organization": {
            # CKAN : name = slug machine, title = nom affiché
            "slug": org.get("name", ""),
            "name": org.get("title", ""),
        },
        "resources": [_normaliser_ressource(r) for r in pkg.get("resources", [])],
        # Pas de zones spatiales CKAN → couvre_rennes() retourne True (portée non précisée)
        "spatial": {"zones": []},
        "last_modified": pkg.get("metadata_modified", ""),
        "license": pkg.get("license_id", ""),
        # URL canonique écosphères (utilisée dans afficher_fiche)
        "_url": f"https://ecospheres.data.gouv.fr/datasets/{pkg.get('name', pkg['id'])}",
        "_source": "ecospheres",
    }


def _normaliser_ressource(r: dict) -> dict:
    return {
        "url": r.get("url", ""),
        "format": (r.get("format") or "").lower(),
        "title": r.get("name") or r.get("description", ""),
    }
