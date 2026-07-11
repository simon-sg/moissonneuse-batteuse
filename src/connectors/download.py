"""
Utilitaires de téléchargement avec cache disque.

Fonctions partagées entre discover.py (découverte interactive/automatique) et
connectors/analyseurs.py (analyseurs de formats). Chaque fichier téléchargé est
stocké dans le répertoire de cache (data/cache/) indexé par MD5 de l'URL.
"""

import datetime
import hashlib
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conf.discover import CACHE_DIR


def _chemin_cache(url: str) -> str:
    cle = hashlib.md5(url.encode()).hexdigest()
    return os.path.join(CACHE_DIR, cle)


def _purger_cache(jours: int = 30) -> None:
    if not os.path.isdir(CACHE_DIR):
        return
    limite = datetime.datetime.now().timestamp() - jours * 86400
    supprimes = 0
    for nom in os.listdir(CACHE_DIR):
        chemin = os.path.join(CACHE_DIR, nom)
        if os.path.isfile(chemin) and os.path.getmtime(chemin) < limite:
            os.remove(chemin)
            supprimes += 1
    if supprimes:
        print(f"  (Cache : {supprimes} fichier(s) supprimé(s), plus vieux que {jours} jours)\n")


def _telecharger(url: str, verbose: bool, plafond_mo: float | None = 200) -> tuple:
    """
    Télécharge en streaming vers le cache disque (un chunk à la fois, jamais en mémoire).
    Retourne (chemin, taille_mo, depuis_cache, erreur).

    plafond_mo : tronque le téléchargement au-delà de ce plafond (suffisant pour
    échantillonner un CSV/GZ). None = pas de plafond — nécessaire pour un ZIP, dont
    le sommaire est à la fin du fichier : un ZIP tronqué est structurellement invalide,
    pas juste partiel.
    """
    chemin = _chemin_cache(url)
    if os.path.exists(chemin):
        taille = os.path.getsize(chemin) / 1024 / 1024
        if verbose:
            print(f"  Cache ({taille:.1f} Mo).")
        return chemin, taille, True, None

    if verbose:
        print("  Téléchargement en cours...")
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
    except Exception as e:
        return None, 0, False, f"téléchargement : {e}"

    total = 0
    interrompu = None
    os.makedirs(CACHE_DIR, exist_ok=True)
    chemin_tmp = chemin + ".tmp"
    try:
        with open(chemin_tmp, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                total += len(chunk)
                if verbose:
                    print(f"  {total / 1024 / 1024:.1f} Mo...", end="\r")
                if plafond_mo is not None and total >= plafond_mo * 1024 * 1024:
                    response.close()
                    if verbose:
                        print(f"\n  (Plafond {plafond_mo} Mo atteint — données partielles utilisées)")
                    break
    except Exception as e:
        interrompu = str(e)
    if verbose:
        print()

    if interrompu:
        if total >= 1024 * 1024:
            if verbose:
                print(f"  (Transfert interrompu après {total/1024/1024:.1f} Mo — données partielles utilisées)")
        else:
            if os.path.exists(chemin_tmp):
                os.remove(chemin_tmp)
            return None, 0, False, f"téléchargement interrompu : {interrompu}"

    os.rename(chemin_tmp, chemin)
    return chemin, total / 1024 / 1024, False, None
