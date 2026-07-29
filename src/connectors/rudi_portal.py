import re
import subprocess

from connectors.http import session
from connectors.rudi_node import _etat_git

CONTENEUR_PORTAIL = "rudiplatform-portail-1"
# Le port 8088 (host) mappe le port 8080 du conteneur reverse-proxy — le dashboard
# Traefik, pas l'appli. Le vrai point d'entrée (routage par hôte) est rudi.localhost,
# résolu vers 127.0.0.1 sans entrée /etc/hosts (TLD .localhost).
URL_PORTAIL = "http://rudi.localhost"

_DOCKER_DIR = "/media/simon/DATA4T/Dev/rudi-portal-local/rudi-out-of-the-box"
_COMPOSE_FILES = [
    "docker-compose-magnolia.yml",
    "docker-compose-rudi.yml",
    "docker-compose-dataverse.yml",
    "docker-compose-network.yml",
]

_CHEMIN_PORTAIL_SOURCE = "/media/simon/DATA4T/Dev/rudi-portal-source"

MODULES_PORTAIL = {
    "registry":    "Registry",
    "gateway":     "Gateway",
    "acl":         "ACL",
    "apigateway":  "API Gateway",
    "strukture":   "Strukture",
    "kalim":       "Kalim",
    "konsult":     "Konsult",
    "kos":         "KOS",
    "projekt":     "Projekt",
    "selfdata":    "Selfdata",
    "konsent":     "Konsent",
    "portail":     "Portail (front)",
}

INFRA_PORTAIL = {
    "reverse-proxy": "Reverse proxy (Traefik)",
    "database":      "Base de données",
    "mailhog":       "MailHog",
    "dataverse":     "Dataverse",
    "solr":          "Solr",
    "magnolia":      "Magnolia CMS",
}


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


def redemarrer_konsult() -> tuple[bool, str]:
    """Redémarre uniquement le microservice Konsult (recharge customization.json)."""
    try:
        cmd = _compose_cmd() + ["restart", "konsult"]
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            cwd=_DOCKER_DIR,
        )
    except FileNotFoundError:
        return False, "docker n'est pas installé ou introuvable dans le PATH."
    except subprocess.TimeoutExpired:
        return False, "docker compose restart konsult : délai dépassé (60s)."

    if r.returncode == 0:
        return True, "Konsult redémarré."
    return False, (r.stderr or r.stdout).strip() or "Échec du redémarrage de Konsult."


# ---------------------------------------------------------------------------
# État détaillé des modules (conteneurs + process natifs hybrides)
# ---------------------------------------------------------------------------

def _conteneurs_portail() -> dict[str, dict]:
    """Interroge Docker sur tous les conteneurs rudiplatform-* en un seul appel.

    Retourne dict nom_service → {"etat": str, "image": str}.
    Ne lève jamais.
    """
    try:
        r = subprocess.run(
            ["docker", "ps", "-a",
             "--filter", "name=rudiplatform-",
             "--format", "{{.Names}}\t{{.Image}}\t{{.State}}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if r.returncode != 0:
        return {}
    result = {}
    for ligne in r.stdout.strip().splitlines():
        m = re.match(r"rudiplatform-(.+)-\d+$", ligne.split("\t")[0])
        if m:
            parties = ligne.split("\t")
            result[m.group(1)] = {
                "etat": parties[2] if len(parties) > 2 else "inconnu",
                "image": parties[1] if len(parties) > 1 else "",
            }
    return result


def _classifier_image(image: str | None) -> dict:
    """Classe une image Docker : patché (:source), natif (:vX.Y.Z), ou inconnu."""
    if not image:
        return {"patche": None, "version": None, "label": "inconnu"}
    if image.endswith(":source"):
        return {"patche": True, "version": None, "label": "patché"}
    m = re.search(r":v?(\d+\.\d+\.\d+)$", image)
    if m:
        return {"patche": False, "version": m.group(1), "label": f"v{m.group(1)}"}
    return {"patche": None, "version": None, "label": image.split(":")[-1]}


def _processus_natifs() -> set[str]:
    """Détecte les microservices RUDI tournant en process Java natif (hors conteneur)."""
    try:
        r = subprocess.run(
            ["pgrep", "-af", "rudi-microservice-"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    if r.returncode != 0:
        return set()
    noms = set()
    for ligne in r.stdout.strip().splitlines():
        m = re.search(r"rudi-microservice-([a-z]+)", ligne)
        if m and m.group(1) in MODULES_PORTAIL:
            noms.add(m.group(1))
    return noms


def etat_modules_portail() -> list[dict]:
    """État détaillé de chaque module du portail RUDI (12 microservices/front).

    Combine : mode natif hybride (process Java hors conteneur), conteneur Docker, tag d'image.
    Ne lève jamais.
    """
    conteneurs = _conteneurs_portail()
    natifs = _processus_natifs()
    modules = []
    for nom_mod, label in MODULES_PORTAIL.items():
        cont = conteneurs.get(nom_mod)
        est_natif = nom_mod in natifs
        if est_natif:
            mode = "natif hybride"
            image_info = {"patche": None, "version": None, "label": "process natif"}
        elif cont:
            mode = "conteneur"
            image_info = _classifier_image(cont.get("image"))
        else:
            mode = "absent"
            image_info = {"patche": None, "version": None, "label": ""}
        modules.append({
            "module": nom_mod,
            "label": label,
            "mode": mode,
            "etat": cont["etat"] if cont else ("actif" if est_natif else "absent"),
            "image": image_info["label"],
            "patche": image_info["patche"],
            "version": image_info["version"],
        })
    return modules


def etat_infra_portail() -> list[dict]:
    """État des services d'infrastructure du portail (6 services, images tierces)."""
    conteneurs = _conteneurs_portail()
    infra = []
    for nom_mod, label in INFRA_PORTAIL.items():
        cont = conteneurs.get(nom_mod)
        infra.append({
            "module": nom_mod,
            "label": label,
            "etat": cont["etat"] if cont else "absent",
            "image": cont.get("image", "") if cont else "",
        })
    return infra


def etat_git_portail_source() -> dict:
    """État Git du monorepo rudi-portal-source (informatif, pas par microservice)."""
    return _etat_git(_CHEMIN_PORTAIL_SOURCE)
