"""
Connecteur SIRENE RM : récupère les SIREN des établissements de Rennes Métropole.

Source  : Datahub SIG Rennes Métropole
          https://data.rennesmetropole.fr/explore/dataset/insee-sirene
          ~117 000 établissements géolocalisés et filtrés par RM, MAJ mensuelle.
Cache   : data/cache/sirens_rm.json  (TTL 30 jours)

Avantage sur la recherche-entreprises.api.gouv.fr : couvre TOUS les établissements
actifs sur le territoire (pas seulement les sièges), sans limite de pagination.
"""

import os
import json
import datetime
import threading
import requests

_EXPORT_URL = (
    "https://data.rennesmetropole.fr/api/explore/v2.1/catalog/datasets"
    "/insee-sirene/exports/csv?select=siren&limit=-1&delimiter=%3B"
)
_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "cache", "sirens_rm.json",
)
_TTL_JOURS = 30

_mem: set[str] | None = None
_lock = threading.Lock()


def obtenir_sirens_rm() -> set[str]:
    """Retourne l'ensemble des SIREN RM (charge ou construit le cache au premier appel)."""
    global _mem
    if _mem is not None:
        return _mem
    with _lock:
        if _mem is not None:  # un autre thread a pu le charger pendant l'attente
            return _mem
        _mem = _lire_cache()
        if _mem is None:
            _mem = _telecharger()
            _ecrire_cache(_mem)
    return _mem


def invalider_cache() -> None:
    """Force le re-téléchargement au prochain appel."""
    global _mem
    _mem = None
    if os.path.exists(_CACHE_FILE):
        os.remove(_CACHE_FILE)


# ---------------------------------------------------------------------------
# Cache disque
# ---------------------------------------------------------------------------

def _lire_cache() -> set[str] | None:
    if not os.path.exists(_CACHE_FILE):
        return None
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        age = (datetime.date.today() - datetime.date.fromisoformat(data["date"])).days
        if age > _TTL_JOURS:
            return None
        sirens = set(data["sirens"])
        print(f"  [SIREN] Cache chargé : {len(sirens)} SIREN ({age} jour(s))")
        return sirens
    except Exception:
        return None


def _ecrire_cache(sirens: set[str]) -> None:
    os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"date": datetime.date.today().isoformat(), "sirens": sorted(sirens)},
            f,
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------

def _telecharger() -> set[str]:
    """Télécharge l'export CSV (colonne siren uniquement) depuis data.rennesmetropole.fr."""
    print("  [SIREN] Téléchargement depuis data.rennesmetropole.fr…")
    resp = requests.get(_EXPORT_URL, timeout=60, stream=True)
    resp.raise_for_status()

    contenu = b""
    total = 0
    for chunk in resp.iter_content(chunk_size=65536):
        contenu += chunk
        total += len(chunk)
        print(f"  [SIREN] {total // 1024} Ko reçus…", end="\r")
    print()

    texte = contenu.decode("utf-8-sig", errors="replace")
    sirens: set[str] = set()
    for ligne in texte.splitlines()[1:]:  # ignore la ligne d'en-tête
        s = ligne.strip().strip('"').split(";")[0]
        if s and len(s) == 9 and s.isdigit():
            sirens.add(s)

    print(f"  [SIREN] {len(sirens)} SIREN uniques ({total // 1024} Ko téléchargés).")
    return sirens
