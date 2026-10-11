"""Cycle de vie du nœud RUDI local (conteneur Podman + process natifs du nœud source).

Hérité de l'ancien ``connectors/rudi_node.py``, amputé de toute la partie publication
(déplacée dans ``rudi_harvest_toolkit.rudi_client``) — il ne reste ici que les fonctions
machine-spécifiques : état/démarrage/arrêt des conteneurs Podman et des 4 process natifs
du nœud source, plus l'état Git des clones de sous-modules.
"""
import os
import socket
import subprocess
import time
from urllib.parse import urlparse

from connectors.http import session
from rudi_harvest_toolkit.rudi_client import noeud_pret

# Nom du conteneur Podman du nœud RUDI local (voir moissonneur-master/run_rudi_node.sh).
CONTENEUR_RUDI = "rudinode"


# ---------------------------------------------------------------------------
# Cycle de vie du conteneur Podman (statut / démarrage / arrêt)
# ---------------------------------------------------------------------------

def statut_conteneur(nom: str = CONTENEUR_RUDI) -> dict:
    """Interroge Podman sur l'état d'un conteneur.

    Retourne {"podman_installe": bool, "existe": bool, "etat": str|None}.
    Ne lève jamais — podman absent, conteneur inexistant ou délai dépassé
    sont tous des résultats normaux à afficher, pas des erreurs à propager.
    """
    try:
        r = subprocess.run(
            ["podman", "inspect", "--format", "{{.State.Status}}", nom],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return {"podman_installe": False, "existe": False, "etat": None}
    except subprocess.TimeoutExpired:
        return {"podman_installe": True, "existe": None, "etat": None}

    if r.returncode != 0:
        return {"podman_installe": True, "existe": False, "etat": None}
    return {"podman_installe": True, "existe": True, "etat": r.stdout.strip()}


def demarrer_conteneur(nom: str = CONTENEUR_RUDI) -> tuple[bool, str]:
    """Démarre un conteneur Podman."""
    try:
        r = subprocess.run(
            ["podman", "start", nom],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return False, "podman n'est pas installé ou introuvable dans le PATH."
    except subprocess.TimeoutExpired:
        return False, "podman start : délai dépassé."

    if r.returncode == 0:
        return True, f"Conteneur « {nom} » démarré."
    return False, (r.stderr or r.stdout).strip() or "Échec du démarrage du conteneur."


def arreter_conteneur(nom: str = CONTENEUR_RUDI) -> tuple[bool, str]:
    """Arrête un conteneur Podman."""
    try:
        r = subprocess.run(
            ["podman", "stop", nom],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return False, "podman n'est pas installé ou introuvable dans le PATH."
    except subprocess.TimeoutExpired:
        return False, "podman stop : délai dépassé."

    if r.returncode == 0:
        return True, f"Conteneur « {nom} » arrêté."
    return False, (r.stderr or r.stdout).strip() or "Échec de l'arrêt du conteneur."


# ---------------------------------------------------------------------------
# État Git (réutilisable par rudi_portal.py)
# ---------------------------------------------------------------------------

def _etat_git(chemin: str) -> dict:
    """État Git d'un dépôt local : branche, nombre d'avance sur origin/HEAD, patché ou non.

    Retourne {"disponible": bool, "branche": str|None, "commits_avance": int|None, "patche": bool|None}.
    ``patche`` vaut ``None`` (jamais ``False``) quand ``commits_avance`` n'a pas pu être déterminé.
    Ne lève jamais.
    """
    result = {"disponible": False, "branche": None, "commits_avance": None, "patche": None}
    try:
        if not os.path.isdir(os.path.join(chemin, ".git")):
            return result
        r = subprocess.run(
            ["git", "-C", chemin, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            result["branche"] = r.stdout.strip()
        r = subprocess.run(
            ["git", "-C", chemin, "rev-list", "--count", "origin/HEAD..HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            n = int(r.stdout.strip())
            result["commits_avance"] = n
            result["patche"] = n > 0
        result["disponible"] = True
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return result


# ---------------------------------------------------------------------------
# État des modules du nœud source (process natifs, pas de conteneur)
# ---------------------------------------------------------------------------

_CHEMIN_NOEUD_SOURCE = "/media/simon/DATA4T/Dev/rudi-node-build/rudi-node-container/src"
MODULES_NOEUD_SOURCE = {
    "catalog": {"dossier": "rudi-catalog", "port": 4030},
    "storage": {"dossier": "rudi-storage", "port": 4031},
    "manager": {"dossier": "rudi-manager", "port": 4032},
    "jwtauth": {"dossier": "rudi-jwtauth", "port": 4033},
}


def etat_modules_noeud_source(conf: dict | None = None) -> list[dict]:
    """État de chaque module du nœud RUDI source (4 process natifs).

    Combine la sonde HTTP (port ouvert) et l'état Git du clone du sous-module.
    Pour le manager, réutilise ``noeud_pret(conf)`` (vérifie /manager/conf, pas juste le port).
    Ne lève jamais.
    """
    if conf:
        hote = urlparse(conf.get("url", "")).hostname or "localhost"
    else:
        hote = "localhost"
    modules = []
    for nom_mod, info in MODULES_NOEUD_SOURCE.items():
        port = info["port"]
        # Sonde HTTP
        up = False
        try:
            if nom_mod == "manager" and conf:
                up = noeud_pret(conf)
            else:
                r = session.get(f"http://{hote}:{port}/", timeout=3)
                up = r.status_code < 500
        except Exception:
            pass
        # État Git
        chemin_git = os.path.join(_CHEMIN_NOEUD_SOURCE, info["dossier"])
        git = _etat_git(chemin_git)
        modules.append({
            "module": nom_mod,
            "port": port,
            "up": up,
            "git": git,
        })
    return modules


def noeud_source_actif(conf: dict) -> bool:
    """Au moins un des 4 modules du nœud source répond déjà."""
    return any(m["up"] for m in etat_modules_noeud_source(conf))


# ---------------------------------------------------------------------------
# Cycle de vie du nœud source (process natifs)
# ---------------------------------------------------------------------------

_SCRIPT_NOEUD_SOURCE = "/media/simon/DATA4T/Dev/rudi-node-build/start-source-node.sh"
_PATTERN_PROCESSUS_NOEUD_SOURCE = r"run-rudinode-(catalog|storage|manager|jwtauth)\.js"
_MONGO_PORT_NOEUD_SOURCE = 4027
_MONGO_TENTATIVES = 15
_MONGO_DELAI_S = 1


def _mongo_source_accessible() -> bool:
    """Le port Mongo du nœud source accepte-t-il une connexion TCP ?"""
    try:
        with socket.create_connection(("127.0.0.1", _MONGO_PORT_NOEUD_SOURCE), timeout=1):
            return True
    except OSError:
        return False


def demarrer_noeud_source(conf: dict) -> tuple[bool, str]:
    """Démarre le nœud RUDI source : Mongo dédié (Podman, rudi-source-mongo) si besoin,
    puis les 4 process natifs via start-source-node.sh (le script se termine seul, les
    process survivent en tâche de fond — subprocess.run() ne bloque pas leur durée de vie)."""
    if noeud_source_actif(conf):
        return True, "Nœud source déjà démarré."
    mongo = statut_conteneur("rudi-source-mongo")
    if mongo.get("etat") != "running":
        ok, msg = demarrer_conteneur("rudi-source-mongo")
        if not ok:
            return False, f"MongoDB du nœud source n'a pas pu démarrer : {msg}"
    if not _mongo_source_accessible():
        # « podman start » rend la main dès que le conteneur est marqué running, mais
        # mongod met encore quelques secondes à finir sa récupération WiredTiger avant
        # d'accepter des connexions — un simple `time.sleep(2)` s'est révélé insuffisant
        # à l'usage (start-source-node.sh échoue alors avec « MongoDB n'est pas
        # accessible » alors que le conteneur tourne). On attend le port réellement.
        for _ in range(_MONGO_TENTATIVES):
            time.sleep(_MONGO_DELAI_S)
            if _mongo_source_accessible():
                break
        else:
            return False, (f"MongoDB (rudi-source-mongo) ne répond toujours pas après "
                            f"{_MONGO_TENTATIVES * _MONGO_DELAI_S}s.")
    try:
        r = subprocess.run(["bash", _SCRIPT_NOEUD_SOURCE], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, f"Script introuvable : {_SCRIPT_NOEUD_SOURCE}"
    except subprocess.TimeoutExpired:
        return False, "start-source-node.sh : délai dépassé (60s)."
    if r.returncode != 0:
        return False, ((r.stderr or r.stdout).strip()[-2000:] or "Échec du démarrage du nœud source.")
    return True, "Nœud source démarré (logs : rudi-node-container/source-logs/)."


def arreter_noeud_source() -> tuple[bool, str]:
    """Arrête les 4 process natifs (pkill par motif — idempotent, pkill retourne 1 si rien ne
    matche). MongoDB n'est volontairement pas arrêté (partagé, coût nul à laisser tourner).

    Le process catalog fait `process.title = app_name` ('catalog') au démarrage — son
    `/proc/<pid>/cmdline` ne contient donc plus jamais `run-rudinode-catalog.js`, et le pkill
    par motif ne le vise jamais (observé le 2026-10-09 : catalog restait seul en vie après
    « arrêt », ce qui faisait croire à `demarrer_noeud_source()` que le nœud tournait déjà —
    no-op silencieux, storage/manager/jwtauth jamais relancés). On tue donc aussi par port
    d'écoute, fiable quel que soit le titre que le process s'est donné.
    """
    try:
        subprocess.run(
            ["pkill", "-f", _PATTERN_PROCESSUS_NOEUD_SOURCE],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return False, "pkill introuvable."
    for info in MODULES_NOEUD_SOURCE.values():
        try:
            subprocess.run(
                ["fuser", "-k", f"{info['port']}/tcp"],
                capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            break
    return True, "Nœud source arrêté (MongoDB laissé tel quel)."
