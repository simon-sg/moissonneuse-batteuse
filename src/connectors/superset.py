import subprocess
import os
import socket

from connectors.http import session

CONTENEUR_SUPERSET = "mb-superset"
URL_SUPERSET = "http://127.0.0.1:8088"

_RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def statut_conteneur() -> dict:
    """Interroge Docker sur l'état du conteneur Superset.

    Retourne {"docker_installe": bool, "existe": bool, "etat": str|None}.
    Ne lève jamais.
    """
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", CONTENEUR_SUPERSET],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return {"docker_installe": False, "existe": False, "etat": None}
    except subprocess.TimeoutExpired:
        return {"docker_installe": True, "existe": None, "etat": None}

    if r.returncode != 0:
        return {"docker_installe": True, "existe": False, "etat": None}
    return {"docker_installe": True, "existe": True, "etat": r.stdout.strip()}


def demarrer_conteneur() -> tuple[bool, str]:
    """Démarre le conteneur Superset via docker compose."""
    try:
        r = subprocess.run(
            ["docker", "compose", "up", "-d", "superset"],
            capture_output=True, text=True, timeout=120,
            cwd=_RACINE,
        )
    except FileNotFoundError:
        return False, "docker n'est pas installé ou introuvable dans le PATH."
    except subprocess.TimeoutExpired:
        return False, "docker compose up -d superset : délai dépassé."

    if r.returncode == 0:
        return True, "Conteneur « mb-superset » démarré."
    return False, (r.stderr or r.stdout).strip() or "Échec du démarrage de Superset."


def arreter_conteneur() -> tuple[bool, str]:
    """Arrête le conteneur Superset via docker compose."""
    try:
        r = subprocess.run(
            ["docker", "compose", "stop", "superset"],
            capture_output=True, text=True, timeout=30,
            cwd=_RACINE,
        )
    except FileNotFoundError:
        return False, "docker n'est pas installé ou introuvable dans le PATH."
    except subprocess.TimeoutExpired:
        return False, "docker compose stop superset : délai dépassé."

    if r.returncode == 0:
        return True, "Conteneur « mb-superset » arrêté."
    return False, (r.stderr or r.stdout).strip() or "Échec de l'arrêt de Superset."


def superset_pret() -> bool:
    """Vérifie que Superset répond vraiment (HTTP 200 ou redirect vers login)."""
    try:
        r = session.get(URL_SUPERSET, timeout=5, allow_redirects=True)
        return r.status_code < 500
    except Exception:
        return False
