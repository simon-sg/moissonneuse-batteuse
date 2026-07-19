import subprocess

from connectors.http import session

CONTENEUR_PORTAIL = "rudiplatform-portail-1"
URL_PORTAIL = "http://127.0.0.1:8088"

_DOCKER_DIR = "/media/simon/DATA4T/Dev/rudi-portal-local/rudi-out-of-the-box"
_COMPOSE_FILES = [
    "docker-compose-magnolia.yml",
    "docker-compose-rudi.yml",
    "docker-compose-dataverse.yml",
    "docker-compose-network.yml",
]


def _compose_cmd() -> list[str]:
    cmd = ["docker", "compose"]
    for f in _COMPOSE_FILES:
        cmd.extend(["-f", f])
    cmd.extend(["--profile", "*"])
    return cmd


def statut_conteneur() -> dict:
    """Interroge Docker sur l'état du conteneur portail RUDI.

    Retourne {"docker_installe": bool, "existe": bool, "etat": str|None}.
    Ne lève jamais.
    """
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", CONTENEUR_PORTAIL],
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
    """Démarre le stack portail RUDI via docker compose."""
    try:
        cmd = _compose_cmd() + ["up", "-d"]
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            cwd=_DOCKER_DIR,
        )
    except FileNotFoundError:
        return False, "docker n'est pas installé ou introuvable dans le PATH."
    except subprocess.TimeoutExpired:
        return False, "docker compose up -d : délai dépassé (120s)."

    if r.returncode == 0:
        return True, "Portail RUDI démarré."
    return False, (r.stderr or r.stdout).strip() or "Échec du démarrage du portail RUDI."


def arreter_conteneur() -> tuple[bool, str]:
    """Arrête le stack portail RUDI via docker compose (stop, pas down — préserve les BDD)."""
    try:
        cmd = _compose_cmd() + ["stop"]
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            cwd=_DOCKER_DIR,
        )
    except FileNotFoundError:
        return False, "docker n'est pas installé ou introuvable dans le PATH."
    except subprocess.TimeoutExpired:
        return False, "docker compose stop : délai dépassé (60s)."

    if r.returncode == 0:
        return True, "Portail RUDI arrêté."
    return False, (r.stderr or r.stdout).strip() or "Échec de l'arrêt du portail RUDI."


def portail_pret() -> bool:
    """Vérifie que le portail répond (HTTP < 500)."""
    try:
        r = session.get(URL_PORTAIL, timeout=5, allow_redirects=True)
        return r.status_code < 500
    except Exception:
        return False
