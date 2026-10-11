"""
Déclare, sur le catalogue RUDI, les flux WMS/WFS/OGC API Features (pilote
rudi-geo) pour les JDD effectivement (re)publiés pendant ce run — et eux
seuls (voir rudi_harvest_toolkit.publish::local_ids_publies_ce_run()).

Best-effort, comme la publication RUDI elle-même (rudi_harvest_toolkit.publish) :
une erreur ici ne doit jamais faire échouer le pipeline de moisson, qui a sa
propre valeur indépendamment de ce complément géo. Si rudi-geo n'est pas
démarré, tente de le démarrer (même pattern que
harvest_auto.py::_demarrer_noeud_rudi() pour le nœud RUDI lui-même) ;
toujours pas prêt après les tentatives -> l'étape est simplement sautée,
rattrapable en relançant `python3 declare_services.py --apply` directement
dans le dépôt rudi-geo le jour où ce dernier est disponible.

Usage : python3 src/publish_rudi_geo.py (appelé par cli.py en fin de pipeline)
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.http import session
from rudi_harvest_toolkit.rudi_client import charger_conf_rudi
from rudi_harvest_toolkit.publish import local_ids_publies_ce_run

CONF_RUDI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json")

# Chemin par defaut : repos voisins sous le meme parent (convention deja en
# place sur cette machine -- voir rudi_node_lifecycle.py::_SCRIPT_NOEUD_SOURCE).
# Surchargeable via la cle "rudi_geo_dir" de src/conf/rudi_node.json si les
# deux depots ne sont pas voisins.
_RUDI_GEO_DIR_DEFAUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "rudi-node-build", "rudi-mapserver",
)
_METADATA_PORT = 8090
_TENTATIVES = 15
_DELAI_S = 2


def _rudi_geo_dir(conf: dict) -> str:
    return conf.get("rudi_geo_dir") or _RUDI_GEO_DIR_DEFAUT


def _rudi_geo_pret() -> bool:
    """Sonde le service de metadonnees (port 8090) -- signale que la stack
    docker-compose.geo.yml tourne et que le synchroniseur a fait au moins un
    cycle (manifest.json existe)."""
    try:
        r = session.get(f"http://localhost:{_METADATA_PORT}/manifest.json", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _demarrer_rudi_geo(rudi_geo_dir: str) -> bool:
    """Demarre la stack docker-compose de rudi-geo si besoin, attend qu'elle
    reponde. N'echoue jamais (voir docstring module) : retourne juste False
    si rudi-geo reste indisponible."""
    if _rudi_geo_pret():
        return True

    if not os.path.isdir(rudi_geo_dir):
        print(f"  [rudi-geo] repertoire introuvable : {rudi_geo_dir} -- etape sautee.")
        return False

    print(f"  [rudi-geo] service de metadonnees injoignable sur le port {_METADATA_PORT} -- demarrage...")
    try:
        subprocess.run(
            ["docker", "compose", "-f", "docker-compose.geo.yml", "up", "-d"],
            cwd=rudi_geo_dir, capture_output=True, timeout=60, check=True,
        )
    except Exception as e:
        print(f"  [rudi-geo] echec du demarrage ({e}) -- etape sautee.")
        return False

    for tentative in range(1, _TENTATIVES + 1):
        if _rudi_geo_pret():
            print(f"  [rudi-geo] pret (apres {tentative} tentative(s)).")
            return True
        time.sleep(_DELAI_S)
    print(f"  [rudi-geo] toujours indisponible apres {_TENTATIVES * _DELAI_S}s -- etape sautee.")
    return False


def main() -> None:
    local_ids = local_ids_publies_ce_run()
    if not local_ids:
        print("  [rudi-geo] aucun JDD (re)publie ce run -- rien a declarer.")
        return

    conf_rudi = charger_conf_rudi(CONF_RUDI_FILE)
    if not conf_rudi:
        print("  [rudi-geo] src/conf/rudi_node.json absent -- etape sautee.")
        return

    rudi_geo_dir = _rudi_geo_dir(conf_rudi)
    if not _demarrer_rudi_geo(rudi_geo_dir):
        return

    print(f"  [rudi-geo] declaration des services pour {len(local_ids)} JDD...")
    cmd = ["python3", "declare_services.py", "--apply"]
    for lid in sorted(local_ids):
        cmd += ["--local-id", lid]

    env = dict(os.environ)
    env["RUDI_MANAGER_URL"] = conf_rudi["url"].rstrip("/") + "/manager"
    env["RUDI_USER"] = conf_rudi["usr"]
    env["RUDI_PASSWORD"] = conf_rudi["pwd"]

    try:
        r = subprocess.run(cmd, cwd=rudi_geo_dir, env=env, capture_output=True, text=True, timeout=180)
        print(r.stdout)
        if r.returncode != 0:
            print(f"  [rudi-geo] declare_services.py a echoue (code {r.returncode}) :\n{r.stderr}")
    except Exception as e:
        print(f"  [rudi-geo] echec de l'appel a declare_services.py : {e}")


if __name__ == "__main__":
    main()
