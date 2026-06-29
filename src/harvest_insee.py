"""
Harvest des publications INSEE directes (fichiers non disponibles sur data.gouv.fr).

Usage :
  python3 src/harvest_insee.py            # toutes les publications
  python3 src/harvest_insee.py bic-iris   # une publication par ID

Résultats :
  data/<dossier>/<slug>-rennesmetropole.csv   — lignes filtrées Rennes Métropole
  data/state_insee.json                       — cache (évite re-téléchargements)
"""
import datetime
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.datasets import DATASETS_INSEE
from connectors.insee import resoudre_url, extraire_membres
from harvest_batch import filtrer_csv, sauvegarder_csv, _slugifier
from discover import _detecter_champs

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "state_insee.json")

_HEADERS = {"User-Agent": "moissonneuse-batteuse/1.0 (projet open-data Rennes Métropole)"}


# ---------------------------------------------------------------------------
# State / cache
# ---------------------------------------------------------------------------

def _charger_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _sauvegarder_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _inchange(pub_id: str, url: str, state: dict) -> bool:
    """Retourne True si url+taille+date-modif correspondent à la dernière entrée en cache."""
    entree = state.get(pub_id, {})
    if entree.get("url") != url:
        return False
    try:
        r = requests.head(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        size = r.headers.get("Content-Length")
        lm   = r.headers.get("Last-Modified")
        # Inchangé si taille identique ET (pas de Last-Modified OU date identique)
        if size and str(size) == str(entree.get("content_length")):
            if not lm or lm == entree.get("last_modified"):
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------

def telecharger_zip(pub_id: str, url: str) -> bytes:
    """Télécharge un ZIP sans plafond de taille, avec barre de progression."""
    r = requests.get(url, headers=_HEADERS, timeout=120, stream=True)
    r.raise_for_status()

    total_attendu = int(r.headers.get("Content-Length", 0))
    chunks = []
    total = 0
    try:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            chunks.append(chunk)
            total += len(chunk)
            if total_attendu:
                pct = total * 100 // total_attendu
                print(f"  [{pub_id}] {total / 1024 / 1024:.0f}/{total_attendu / 1024 / 1024:.0f} Mo ({pct}%)",
                      end="\r")
            else:
                print(f"  [{pub_id}] {total / 1024 / 1024:.1f} Mo...", end="\r")
    except KeyboardInterrupt:
        mo = total / 1024 / 1024
        print(f"\n  [{pub_id}] Interrompu à {mo:.1f} Mo.")
        raise
    print()
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# Traitement d'une publication
# ---------------------------------------------------------------------------

def traiter_publication(pub: dict, state: dict) -> dict:
    pub_id = pub["id"]
    dossier = os.path.join(DATA_DIR, pub["dossier"])
    os.makedirs(dossier, exist_ok=True)

    print(f"\n--- {pub['titre']} ---")

    # 1. Résoudre l'URL (URL directe → fallback scraping)
    url = resoudre_url(pub)
    if not url:
        return {"statut": "echec", "raison": "URL introuvable (direct + scraping)"}

    # 2. Cache : vérifier si le fichier a changé
    if _inchange(pub_id, url, state):
        nb_rm = state.get(pub_id, {}).get("nb_rm", "?")
        print(f"  → Cache (inchangé, {nb_rm} lignes RM)")
        return {"statut": "cache", "nb_rm": nb_rm}

    # 3. Métadonnées HTTP (pour la détection de changement future)
    try:
        r_head = requests.head(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        content_length = r_head.headers.get("Content-Length")
        last_modified  = r_head.headers.get("Last-Modified")
    except Exception:
        content_length, last_modified = None, None

    # 4. Téléchargement
    print(f"  Téléchargement : {url}")
    try:
        contenu_zip = telecharger_zip(pub_id, url)
    except Exception as e:
        return {"statut": "echec", "raison": f"téléchargement : {e}"}

    # 5. Extraction des membres CSV
    membres = extraire_membres(pub, contenu_zip)
    if not membres:
        return {"statut": "echec", "raison": "aucun membre CSV correspondant dans le ZIP"}

    champ_iris    = pub.get("champ_iris")
    champ_cp      = pub.get("champ_cp")
    champ_ville   = pub.get("champ_ville")
    champ_adresse = pub.get("champ_adresse")

    # 6. Filtrage et sauvegarde
    nb_rm_total = 0
    for nom_membre, contenu_csv in membres:
        print(f"  Filtrage : {nom_membre}")
        try:
            lignes, entetes = filtrer_csv(
                contenu_csv, champ_cp, champ_ville, champ_iris, champ_adresse
            )
        except Exception as e:
            print(f"    → Erreur de filtrage : {e}")
            continue

        # Failsafe : si 0 lignes et champ_iris absent des colonnes réelles, auto-détection
        if not lignes and champ_iris and champ_iris not in (entetes or []):
            auto = _detecter_champs(entetes)[2]  # indice 2 = champ_iris
            if auto and auto != champ_iris:
                print(f"    Champ '{champ_iris}' absent — essai auto-détecté : '{auto}'")
                try:
                    lignes, entetes = filtrer_csv(contenu_csv, champ_cp, champ_ville, auto, champ_adresse)
                except Exception as e:
                    print(f"    → Erreur (auto) : {e}")

        if not lignes:
            print(f"    → 0 lignes RM")
            if entetes:
                print(f"    Colonnes disponibles : {entetes[:12]}")
            continue

        print(f"    → {len(lignes)} lignes RM")
        slug = _slugifier(os.path.splitext(os.path.basename(nom_membre))[0])
        chemin_csv = os.path.join(dossier, f"{slug}-rennesmetropole.csv")
        sauvegarder_csv(lignes, chemin_csv)
        print(f"    → {chemin_csv}")
        nb_rm_total += len(lignes)

    # 7. Mettre à jour le cache
    state[pub_id] = {
        "url": url,
        "content_length": content_length,
        "last_modified":  last_modified,
        "date_harvest":   datetime.date.today().isoformat(),
        "nb_rm": nb_rm_total,
        "dossier": pub["dossier"],
    }

    if nb_rm_total == 0:
        return {"statut": "vide", "raison": "0 lignes RM après filtrage de tous les membres"}
    return {"statut": "ok", "nb_rm": nb_rm_total}


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def main() -> None:
    # Filtre optionnel : python3 harvest_insee.py bic-iris filosofi-commune
    ids_demandes = set(sys.argv[1:])
    publications = [
        p for p in DATASETS_INSEE
        if not ids_demandes or p["id"] in ids_demandes
    ]
    if ids_demandes and not publications:
        ids_valides = [p["id"] for p in DATASETS_INSEE]
        print(f"ID(s) inconnu(s). IDs valides : {', '.join(ids_valides)}")
        sys.exit(1)

    print(f"=== Harvest INSEE direct — {len(publications)} publication(s) ===\n")
    state = _charger_state()

    ok, cache, echecs, vides = [], [], [], []

    for pub in publications:
        res = traiter_publication(pub, state)
        _sauvegarder_state(state)  # sauvegarde immédiate après chaque publication

        statut = res["statut"]
        if statut == "ok":
            print(f"  ✓ {pub['id']} — {res['nb_rm']} lignes RM")
            ok.append(pub["id"])
        elif statut == "cache":
            cache.append(pub["id"])
        elif statut == "vide":
            print(f"  ! {pub['id']} — vide : {res['raison']}")
            vides.append(pub["id"])
        else:
            print(f"  ✗ {pub['id']} — ÉCHEC : {res['raison']}")
            echecs.append(pub["id"])

    print(f"\n=== Terminé ===")
    print(f"  OK     : {len(ok)}")
    print(f"  Cache  : {len(cache)}")
    print(f"  Vides  : {len(vides)}")
    print(f"  Échecs : {len(echecs)}")

    if echecs:
        print(f"\n  Consultez data/state_insee.json pour l'état détaillé.")


if __name__ == "__main__":
    main()
