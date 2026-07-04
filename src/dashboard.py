"""
Tableau de bord web pour piloter le pipeline moissonneuse-batteuse.

Usage : python3 src/dashboard.py [port]   (défaut : 8765)

Serveur HTTP minimal (stdlib uniquement, pas de framework) qui expose une page
de pilotage : déclenchement des actions de moisson/catalogue/publication RUDI,
suivi en direct du job en cours, purge de données. Réutilise telles quelles les
fonctions de src/cli.py (PURGE_ITEMS, action_*, etat_projet, ETAPES_PIPELINE) —
aucune logique métier dupliquée.

N'écoute QUE sur 127.0.0.1 : ce tableau de bord ne fait aucune authentification
et peut déclencher des actions destructrices (purge) ou réseau (publication sur
le nœud RUDI) ; il ne doit jamais être exposé au-delà de la machine locale.

La découverte interactive n'est pas pilotable depuis ce tableau de bord (elle
repose sur des prompts terminal) — utiliser `python3 src/cli.py` pour celle-ci.
"""
import http.server
import io
import json
import mimetypes
import os
import shutil
import sys
import threading
import time
import webbrowser
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cli
import discover
from catalogue import GABARIT_WMS_MAP
from connectors import rudi_node

HOST = "127.0.0.1"
PORT_DEFAUT = 8765


# ---------------------------------------------------------------------------
# Exécution des jobs en arrière-plan (un seul à la fois)
# ---------------------------------------------------------------------------

_verrou_job = threading.Lock()
_job = {"statut": "idle", "label": None, "debut": None, "fin": None, "buffer": None}


class _Tee:
    """Écrit à la fois dans le buffer du job (pour l'API) et le terminal d'origine."""

    def __init__(self, buffer: io.StringIO, original):
        self._buffer = buffer
        self._original = original

    def write(self, s):
        self._buffer.write(s)
        self._original.write(s)
        return len(s)

    def flush(self):
        self._original.flush()


def _pipeline_complet(params: dict) -> None:
    cli.executer_pipeline_complet()


def _moisson_batch_et_publier(params: dict) -> None:
    cli.action_moisson_batch()
    cli.action_catalogue()
    cli.action_publier_rudi()


def _moisson_geo_et_publier(params: dict) -> None:
    cli.action_moisson_geo()
    cli.action_catalogue()
    cli.action_publier_rudi()


ACTIONS = {
    "moisson_tabulaire": ("Moisson tabulaire (data.gouv.fr)", lambda p: cli.action_moisson_tabulaire()),
    "moisson_batch": ("Moisson batch (candidats découverts)", lambda p: cli.action_moisson_batch()),
    "moisson_insee": ("Moisson INSEE", lambda p: cli.action_moisson_insee(ids=p.get("ids", ""))),
    "moisson_oeb": ("Moisson OEB", lambda p: cli.action_moisson_oeb(ids=p.get("ids", ""))),
    "moisson_bdnb": ("Moisson BDNB (bâtiments)", lambda p: cli.action_moisson_bdnb()),
    "moisson_geo": ("Moisson géo (WFS/WMS/OGC API)", lambda p: cli.action_moisson_geo()),
    "catalogue": ("Génération du catalogue", lambda p: cli.action_catalogue()),
    "publier_rudi": ("Publication sur le nœud RUDI", lambda p: cli.action_publier_rudi()),
    "enrichir_descriptions": ("Enrichir les descriptions vides/quasi vides",
                              lambda p: cli.action_enrichir_descriptions()),
    "verifier_backlog_examen": ("Vérifier le backlog « à examiner » (ressources)",
                                lambda p: cli.action_verifier_backlog_examen()),
    "moisson_batch_et_publier": ("Moisson batch + catalogue + publication RUDI",
                                 _moisson_batch_et_publier),
    "moisson_geo_et_publier": ("Moisson géo + catalogue + publication RUDI",
                               _moisson_geo_et_publier),
    "pipeline_complet": ("Pipeline complet (sans découverte)", _pipeline_complet),
}


def _etat_job() -> dict:
    with _verrou_job:
        buffer = _job["buffer"]
        return {
            "statut": _job["statut"],
            "label": _job["label"],
            "log": buffer.getvalue() if buffer else "",
            "debut": _job["debut"],
            "fin": _job["fin"],
        }


def _demarrer_job(nom: str, params: dict) -> tuple[bool, str]:
    if nom not in ACTIONS:
        return False, f"Action inconnue : {nom}"
    label, fn = ACTIONS[nom]

    with _verrou_job:
        if _job["statut"] == "running":
            return False, f"Une action est déjà en cours : {_job['label']}"
        _job["statut"] = "running"
        _job["label"] = label
        _job["debut"] = time.time()
        _job["fin"] = None
        _job["buffer"] = io.StringIO()
        buffer = _job["buffer"]

    def cible():
        ancien_stdout = sys.stdout
        sys.stdout = _Tee(buffer, ancien_stdout)
        try:
            fn(params)
        finally:
            sys.stdout = ancien_stdout
            with _verrou_job:
                _job["statut"] = "termine"
                _job["fin"] = time.time()

    threading.Thread(target=cible, daemon=True).start()
    return True, f"« {label} » démarré."


def _message_declenchement_job(nom: str) -> str:
    """Déclenche un job en tâche de fond de façon best-effort (jamais bloquant pour l'appelant :
    un job déjà en cours n'est pas une erreur, cet ajout sera simplement inclus au prochain
    lancement — auto ou manuel via le bouton de secours correspondant)."""
    demarre, _ = _demarrer_job(nom, {})
    if demarre:
        return "Moisson + publication démarrées en tâche de fond."
    return "Un job est déjà en cours — cet ajout sera inclus au prochain lancement."


# ---------------------------------------------------------------------------
# Purge (réutilise cli.PURGE_ITEMS)
# ---------------------------------------------------------------------------

def _purge_items_json() -> list[dict]:
    items = []
    for i, item in enumerate(cli.PURGE_ITEMS):
        taille = item["taille"]()
        items.append({
            "id": i,
            "label": item["label"],
            "taille_octets": taille,
            "taille_lisible": cli._formater_taille(taille),
            "impact": item["impact"],
            "destructeur": item["destructeur"],
        })
    return items


def _traiter_purge(idx_str: str, params: dict) -> tuple[int, dict]:
    if _job["statut"] == "running":
        return 409, {"ok": False, "message": "Une action est en cours — attendez sa fin avant de purger."}
    try:
        idx = int(idx_str)
        item = cli.PURGE_ITEMS[idx]
    except (ValueError, IndexError):
        return 404, {"ok": False, "message": "Élément de purge inconnu."}

    confirmation = str(params.get("confirmation") or "").strip()
    valide = (confirmation == "SUPPRIMER") if item["destructeur"] else (confirmation.lower() == "oui")
    if not valide:
        return 400, {"ok": False, "message": "Confirmation invalide."}

    message = item["purger"]()
    return 200, {"ok": True, "message": message}


# ---------------------------------------------------------------------------
# Nœud RUDI (statut/démarrage/arrêt du conteneur Podman + liens rapides)
# ---------------------------------------------------------------------------

def _etat_noeud() -> dict:
    etat = rudi_node.statut_conteneur()

    conf = rudi_node.charger_conf_rudi()
    etat["url_manager"] = (conf["url"].rstrip("/") + "/manager/") if conf else None
    etat["pret"] = bool(conf and etat.get("etat") == "running" and rudi_node.noeud_pret(conf))

    chemin_catalogue = os.path.join(cli.DATA_DIR, "catalogue.html")
    etat["catalogue_disponible"] = os.path.isfile(chemin_catalogue)
    # Servi par le dashboard lui-même (voir /data/<chemin>) plutôt qu'un lien file:// —
    # les navigateurs modernes bloquent la navigation depuis une page http:// vers file://.
    etat["catalogue_url"] = "/data/catalogue.html" if etat["catalogue_disponible"] else None

    return etat


def _traiter_noeud_action(nom: str) -> tuple[int, dict]:
    fn = {"demarrer": rudi_node.demarrer_conteneur, "arreter": rudi_node.arreter_conteneur}.get(nom)
    if fn is None:
        return 404, {"ok": False, "message": "Action de nœud inconnue."}
    ok, message = fn()
    return (200 if ok else 500), {"ok": ok, "message": message}


# ---------------------------------------------------------------------------
# Backlog "à examiner" (candidats ambigus / services géo issus de la découverte
# automatique — src/harvest_auto.py) — voir discover.rechercher_et_filtrer_auto()
# et discover.resoudre_a_examiner().
# ---------------------------------------------------------------------------

def _a_examiner_json() -> list[dict]:
    return discover.charger_decouverte().get("a_examiner", [])


def _historique_json() -> dict:
    """Onglets « Exclus »/« Ignorés » de /examen. Fusionne les instantanés riches de
    decouverte["historique"] (décisions prises depuis resoudre_a_examiner(), avec titre/
    organisation/url complets) avec les éventuels ids de decouverte["exclus"] antérieurs à
    l'introduction de l'historique (juste un id, aucun titre disponible sans appel réseau
    supplémentaire — affichés tels quels, sans action « Rouvrir » possible dessus)."""
    decouverte = discover.charger_decouverte()
    historique = decouverte.get("historique", [])
    exclus_avec_historique = {h["dataset_id"] for h in historique if h.get("decision") == "exclure"}
    exclus = [h for h in historique if h.get("decision") == "exclure"]
    for did in decouverte.get("exclus", []):
        if did not in exclus_avec_historique:
            exclus.append({
                "dataset_id": did, "titre": "(titre inconnu — exclu avant le suivi détaillé)",
                "organisation": "", "url": f"https://www.data.gouv.fr/datasets/{did}",
                "type": "?", "raison": "", "date_decision": None, "rouvrable": False,
            })
    for e in exclus:
        e.setdefault("rouvrable", True)
    ignores = [h for h in historique if h.get("decision") == "ignorer"]
    return {"exclus": exclus, "ignores": ignores}


def _traiter_historique(params: dict) -> tuple[int, dict]:
    dataset_id = str(params.get("dataset_id") or "").strip()
    if not dataset_id:
        return 400, {"ok": False, "message": "Paramètres invalides."}
    decouverte = discover.charger_decouverte()
    ok = discover.rouvrir_historique(decouverte, dataset_id)
    if not ok:
        return 404, {"ok": False, "message": "Entrée introuvable dans l'historique, ou déjà "
                                              "revenue dans le backlog."}
    return 200, {"ok": True, "message": "JDD rouvert — de retour dans « À examiner »."}


def _traiter_a_examiner(params: dict) -> tuple[int, dict]:
    dataset_id = str(params.get("dataset_id") or "").strip()
    decision = str(params.get("decision") or "").strip()
    if not dataset_id or decision not in ("exclure", "candidat", "ajouter_geo", "ignorer"):
        return 400, {"ok": False, "message": "Paramètres invalides."}

    champs_manuels = None
    type_variable = str(params.get("type_variable") or "").strip()
    if decision == "candidat" and type_variable:
        col1 = str(params.get("col1") or "").strip()
        col2 = params.get("col2")
        col2 = str(col2).strip() if col2 else None
        nb_rm = int(params.get("nb_rm") or 0)
        champs_manuels = discover._construire_champs_manuels(type_variable, col1, col2, nb_rm)

    decouverte = discover.charger_decouverte()
    ok = discover.resoudre_a_examiner(decouverte, dataset_id, decision, champs_manuels=champs_manuels)
    if not ok:
        return 404, {"ok": False, "message": "Entrée introuvable, ou décision inapplicable "
                                              "(ex : « candidat » sans champ géo détecté)."}

    message = "Décision enregistrée."
    if decision == "candidat":
        message += " " + _message_declenchement_job("moisson_batch_et_publier")
    elif decision == "ajouter_geo":
        message += " " + _message_declenchement_job("moisson_geo_et_publier")

    return 200, {"ok": True, "message": message,
                 "restants": len(decouverte.get("a_examiner", []))}


def _traiter_resoudre_wfs_masse(params: dict) -> tuple[int, dict]:
    decouverte = discover.charger_decouverte()
    n = discover.resoudre_wfs_confirmes_en_masse(decouverte)
    if n == 0:
        return 200, {"ok": True, "message": "Aucun WFS confirmé en attente.", "resolus": 0}
    message = f"{n} service(s) WFS ajouté(s) automatiquement. "
    message += _message_declenchement_job("moisson_geo_et_publier")
    return 200, {"ok": True, "message": message, "resolus": n}


def _resoudre_ressource(dataset_id: str) -> tuple:
    """Récupère les métadonnées data.gouv.fr et la première ressource analysable (tous formats
    reconnus par discover._format_analysable() : csv/gz/zip/xlsx/geojson/parquet) pour un
    dataset_id. Retourne (True, ressource, fmt) ou (False, message, permanent) — `permanent`
    distingue une absence définitive de ressource exploitable d'un échec réseau transitoire."""
    from connectors.datagouv import get_dataset_metadata

    try:
        metadata = get_dataset_metadata(dataset_id)
    except Exception as e:
        return False, f"Impossible de récupérer les métadonnées : {e}", False

    ressource = discover.trouver_ressource_analysable(metadata)
    fmt = discover._format_analysable(ressource) if ressource else None
    if ressource is None or fmt is None:
        return False, "Aucune ressource dans un format pris en charge pour la revue manuelle.", True
    return True, ressource, fmt


def _traiter_a_examiner_preview(params: dict) -> tuple[int, dict]:
    dataset_id = str(params.get("dataset_id") or "").strip()
    if not dataset_id:
        return 400, {"ok": False, "message": "Paramètres invalides."}

    resultat = _resoudre_ressource(dataset_id)
    if not resultat[0]:
        _, message, permanent = resultat
        decouverte = discover.charger_decouverte()
        if permanent:
            discover.marquer_a_examiner_verifie(decouverte, dataset_id, True, message)
        else:
            discover.marquer_a_examiner_echec(decouverte, dataset_id, message)
        return 200, {"ok": False, "message": message, "permanent": permanent}
    _, ressource, _fmt = resultat

    apercu = discover.analyser_apercu_revue(ressource)
    if not apercu[0]:
        _, message, permanent = apercu
        decouverte = discover.charger_decouverte()
        if permanent:
            discover.marquer_a_examiner_verifie(decouverte, dataset_id, True, message)
        else:
            discover.marquer_a_examiner_echec(decouverte, dataset_id, message)
        return 200, {"ok": False, "message": message, "permanent": permanent}

    # Succès confirmé : marque l'entrée comme vérifiée-disponible pour qu'un futur rattrapage en
    # masse (verifier_ressources_a_examiner) ne la re-vérifie pas inutilement.
    decouverte = discover.charger_decouverte()
    discover.marquer_a_examiner_verifie(decouverte, dataset_id, False)

    _, fmt, entetes, lignes_apercu = apercu
    return 200, {"ok": True, "format": fmt, "entetes": entetes, "lignes": lignes_apercu,
                 "types_variables": discover._TYPES_VARIABLES}


def _traiter_a_examiner_test_filter(params: dict) -> tuple[int, dict]:
    dataset_id = str(params.get("dataset_id") or "").strip()
    type_variable = str(params.get("type_variable") or "").strip()
    col1 = str(params.get("col1") or "").strip()
    col2 = params.get("col2")
    col2 = str(col2).strip() if col2 else None
    if not dataset_id or not col1 or type_variable not in dict(discover._TYPES_VARIABLES):
        return 400, {"ok": False, "message": "Paramètres invalides."}

    resultat = _resoudre_ressource(dataset_id)
    if not resultat[0]:
        return 200, {"ok": False, "message": resultat[1]}
    _, ressource, fmt = resultat

    colonnes_utiles = [c for c in (col1, col2) if c]
    lignes = discover.analyser_lignes_revue(ressource, fmt, colonnes_utiles=colonnes_utiles)
    if not lignes[0]:
        return 200, {"ok": False, "message": lignes[1]}
    _, rows = lignes

    nb_total, nb_rm, exemples, _ = discover._compter_lignes_variable(rows, type_variable, col1, col2)
    return 200, {"ok": True, "nb_total": nb_total, "nb_rm": nb_rm, "exemples": exemples}


def _generer_carte_wms_examen(dataset_id: str) -> str | None:
    """Carte Leaflet jetable (pas écrite sur disque, contrairement à catalogue.py) pour aider à
    juger un service WMS encore dans le backlog a_examiner — réutilise GABARIT_WMS_MAP tel quel.
    Retourne None si l'entrée n'existe pas ou n'est pas de type wms."""
    decouverte = discover.charger_decouverte()
    entree = next((e for e in decouverte.get("a_examiner", []) if e["dataset_id"] == dataset_id), None)
    if entree is None or entree.get("type") != "wms":
        return None
    # Normalise : les entrées existantes avant l'extension de _upsert_a_examiner() peuvent encore
    # stocker les couches comme de simples noms (pas de bbox_wgs84) — la couche s'affiche quand
    # même sur la carte, juste sans info de bbox dans l'attribution.
    couches = [c if isinstance(c, dict) else {"nom": c, "titre": c, "bbox_wgs84": {}}
               for c in entree.get("couches", [])]
    data = json.dumps({
        "nom": entree.get("titre", ""),
        "url": entree.get("service_url", ""),
        "couches": couches,
        "producteur": entree.get("organisation", ""),
    }, ensure_ascii=False).replace("</", r"<\/")
    return GABARIT_WMS_MAP.replace("/*__DATA__*/", data)


# ---------------------------------------------------------------------------
# Serveur HTTP
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "MoissonneuseDashboard/1.0"

    def log_message(self, format, *args):
        pass  # silence le log d'accès par défaut — la console reste dédiée aux jobs

    def _repondre_json(self, code: int, payload) -> None:
        corps = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _repondre_html(self, code: int, html: str) -> None:
        corps = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _servir_fichier_donnees(self, chemin_relatif: str) -> None:
        """Sert un fichier sous data/ (catalogue, viewers, cartes…) — seul moyen fiable
        d'ouvrir le catalogue depuis le navigateur, un lien file:// direct étant bloqué
        par les navigateurs modernes depuis une page http://."""
        chemin_abs = os.path.normpath(os.path.join(cli.DATA_DIR, unquote(chemin_relatif)))
        if (chemin_abs != cli.DATA_DIR.rstrip(os.sep)
                and not chemin_abs.startswith(cli.DATA_DIR.rstrip(os.sep) + os.sep)):
            self._repondre_json(404, {"erreur": "introuvable"})
            return
        if not os.path.isfile(chemin_abs):
            self._repondre_json(404, {"erreur": "introuvable"})
            return
        type_mime = mimetypes.guess_type(chemin_abs)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", type_mime)
        self.send_header("Content-Length", str(os.path.getsize(chemin_abs)))
        self.end_headers()
        with open(chemin_abs, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        if self.path == "/":
            self._repondre_html(200, PAGE_HTML)
        elif self.path == "/examen":
            self._repondre_html(200, PAGE_EXAMEN_HTML)
        elif self.path == "/api/etat":
            self._repondre_json(200, cli.etat_projet())
        elif self.path == "/api/job":
            self._repondre_json(200, _etat_job())
        elif self.path == "/api/purge":
            self._repondre_json(200, _purge_items_json())
        elif self.path == "/api/noeud":
            self._repondre_json(200, _etat_noeud())
        elif self.path == "/api/a_examiner":
            self._repondre_json(200, _a_examiner_json())
        elif self.path == "/api/historique":
            self._repondre_json(200, _historique_json())
        elif self.path.startswith("/examen/carte/"):
            dataset_id = unquote(self.path[len("/examen/carte/"):])
            html = _generer_carte_wms_examen(dataset_id)
            if html is None:
                self._repondre_json(404, {"erreur": "introuvable"})
            else:
                self._repondre_html(200, html)
        elif self.path.startswith("/data/"):
            self._servir_fichier_donnees(self.path[len("/data/"):])
        else:
            self._repondre_json(404, {"erreur": "introuvable"})

    def do_POST(self):
        longueur = int(self.headers.get("Content-Length", 0) or 0)
        brut = self.rfile.read(longueur) if longueur else b""
        try:
            params = json.loads(brut) if brut else {}
        except ValueError:
            params = {}

        if self.path.startswith("/api/job/"):
            nom = self.path[len("/api/job/"):]
            ok, message = _demarrer_job(nom, params)
            self._repondre_json(200 if ok else 409, {"ok": ok, "message": message})
        elif self.path.startswith("/api/purge/"):
            idx_str = self.path[len("/api/purge/"):]
            code, payload = _traiter_purge(idx_str, params)
            self._repondre_json(code, payload)
        elif self.path.startswith("/api/noeud/"):
            nom = self.path[len("/api/noeud/"):]
            code, payload = _traiter_noeud_action(nom)
            self._repondre_json(code, payload)
        elif self.path == "/api/a_examiner":
            code, payload = _traiter_a_examiner(params)
            self._repondre_json(code, payload)
        elif self.path == "/api/a_examiner/preview":
            code, payload = _traiter_a_examiner_preview(params)
            self._repondre_json(code, payload)
        elif self.path == "/api/a_examiner/test-filter":
            code, payload = _traiter_a_examiner_test_filter(params)
            self._repondre_json(code, payload)
        elif self.path == "/api/a_examiner/resoudre_wfs_masse":
            code, payload = _traiter_resoudre_wfs_masse(params)
            self._repondre_json(code, payload)
        elif self.path == "/api/historique/rouvrir":
            code, payload = _traiter_historique(params)
            self._repondre_json(code, payload)
        else:
            self._repondre_json(404, {"erreur": "introuvable"})


# ---------------------------------------------------------------------------
# Page (HTML + CSS + JS, gabarit auto-contenu — même style que catalogue.py)
# ---------------------------------------------------------------------------

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moissonneuse-batteuse — Tableau de bord</title>
<script>(function(){try{var t=localStorage.getItem("theme");if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}})();</script>
<style>
  :root { --bg:#f5f6f8; --card:#fff; --txt:#1c2733; --muted:#667; --accent:#0b6e99; --bord:#e2e6ea;
          --ok:#1a8a4a; --warn:#a3372c; --disabled-bg:#aab4bb; --pipeline-bg:#eef6f1; --pipeline-bord:#bfe0cc;
          --badge-idle-bg:#eef3f6; --badge-idle-txt:#345; --badge-running-bg:#fff4e0; --badge-running-txt:#9a6a00;
          --badge-termine-bg:#e8f5ec; --badge-warn-bg:#fbe9e7; --notif-bg:#20272e; --notif-err-bg:#a3372c; }
  :root[data-theme="dark"] { --bg:#10151b; --card:#1a222b; --txt:#dbe2e8; --muted:#8996a3; --accent:#4fb3da;
          --bord:#29323c; --ok:#3ddc84; --warn:#ff9686; --disabled-bg:#3a434c; --pipeline-bg:#16241c;
          --pipeline-bord:#2c4a37; --badge-idle-bg:#20303a; --badge-idle-txt:#b7c9d6; --badge-running-bg:#3a2f10;
          --badge-running-txt:#e8c96a; --badge-termine-bg:#16301f; --badge-warn-bg:#3a201d; }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) { --bg:#10151b; --card:#1a222b; --txt:#dbe2e8; --muted:#8996a3; --accent:#4fb3da;
          --bord:#29323c; --ok:#3ddc84; --warn:#ff9686; --disabled-bg:#3a434c; --pipeline-bg:#16241c;
          --pipeline-bord:#2c4a37; --badge-idle-bg:#20303a; --badge-idle-txt:#b7c9d6; --badge-running-bg:#3a2f10;
          --badge-running-txt:#e8c96a; --badge-termine-bg:#16301f; --badge-warn-bg:#3a201d; }
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background:var(--bg); color:var(--txt); line-height:1.45; }
  header { background:var(--card); border-bottom:1px solid var(--bord); padding:18px 24px;
           position:sticky; top:0; z-index:5; display:flex; justify-content:space-between; align-items:baseline; }
  h1 { margin:0; font-size:1.3rem; }
  .meta { color:var(--muted); font-size:.85rem; }
  main { max-width:1100px; margin:0 auto; padding:20px 24px 60px; display:grid; gap:18px; }
  section { background:var(--card); border:1px solid var(--bord); border-radius:10px; padding:16px 18px; }
  section h2 { margin:0 0 12px; font-size:1.02rem; }
  .grille-etat { display:grid; grid-template-columns:repeat(auto-fit, minmax(180px,1fr)); gap:10px; }
  .stat { background:var(--bg); border:1px solid var(--bord); border-radius:8px; padding:10px 12px; }
  .stat .val { font-size:1.25rem; font-weight:700; }
  .stat .lbl { font-size:.78rem; color:var(--muted); }
  .grille-actions { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px,1fr)); gap:10px; }
  .action { border:1px solid var(--bord); border-radius:8px; padding:12px; display:flex;
            flex-direction:column; gap:8px; }
  .action .titre { font-weight:600; font-size:.92rem; }
  .action .desc { font-size:.78rem; color:var(--muted); }
  .action > button { margin-top:auto; }
  .action input[type=text] { padding:7px 9px; border:1px solid var(--bord); border-radius:6px; font-size:.85rem; }
  button { background:var(--accent); color:#fff; border:none; border-radius:6px; padding:8px 14px;
           font-size:.85rem; cursor:pointer; }
  button:hover:not(:disabled) { filter:brightness(1.08); }
  button:disabled { background:var(--disabled-bg); cursor:not-allowed; }
  button.danger { background:var(--notif-err-bg); }
  .discret { background:transparent; color:var(--accent); border:1px solid var(--bord);
             padding:8px 14px; border-radius:6px; font-size:.85rem; display:inline-block;
             text-decoration:none; cursor:pointer; }
  .discret.desactive { opacity:.5; pointer-events:none; }
  .action.pipeline { background:var(--pipeline-bg); border-color:var(--pipeline-bord); }
  .action.disabled { opacity:.55; }
  .badge { display:inline-block; border-radius:99px; padding:2px 10px; font-size:.72rem; font-weight:600; }
  .badge.idle { background:var(--badge-idle-bg); color:var(--badge-idle-txt); }
  .badge.running { background:var(--badge-running-bg); color:var(--badge-running-txt); }
  .badge.termine { background:var(--badge-termine-bg); color:var(--ok); }
  .badge.warn { background:var(--badge-warn-bg); color:var(--warn); }
  #journal { background:#10161c; color:#d7dee4; border-radius:8px; padding:12px 14px; font-size:.8rem;
             font-family: ui-monospace, "SF Mono", Consolas, monospace; white-space:pre-wrap;
             overflow-wrap:anywhere;
             max-height:380px; overflow-y:auto; min-height:60px; }
  #journal:empty::before { content:"Aucun job lancé."; color:#677; }
  .ligne-job { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; flex-wrap:wrap; gap:8px; }
  table.purge { width:100%; border-collapse:collapse; font-size:.85rem; }
  table.purge th, table.purge td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--bord); vertical-align:top; }
  table.purge th { color:var(--muted); font-weight:600; font-size:.78rem; }
  table.purge .impact { color:var(--muted); font-size:.78rem; max-width:380px; }
  table.purge .taille { white-space:nowrap; font-variant-numeric:tabular-nums; }
  .purge-action { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
  .purge-action input[type=text] { width:120px; padding:5px 7px; border:1px solid var(--bord); border-radius:6px; font-size:.8rem; }
  #notif { position:fixed; bottom:18px; right:18px; max-width:360px; }
  #notif div { background:var(--notif-bg); color:#fff; border-radius:8px; padding:10px 14px; margin-top:8px;
               font-size:.85rem; box-shadow:0 4px 14px rgba(0,0,0,.18); }
  #notif div.erreur { background:var(--notif-err-bg); }
  #theme-toggle { background:none; border:1px solid var(--bord); border-radius:6px; padding:6px 10px;
                  cursor:pointer; font-size:.9rem; line-height:1; color:var(--txt); }
  #theme-toggle:hover { background:var(--bg); }
  .entete-droite { display:flex; align-items:center; gap:12px; }
  a { color:var(--accent); }
</style>
</head>
<body>
<header>
  <h1>Moissonneuse-batteuse — Tableau de bord</h1>
  <div class="entete-droite">
    <span class="meta">Rennes Métropole · 127.0.0.1 uniquement</span>
    <button id="theme-toggle" title="Basculer thème clair/sombre" aria-label="Basculer thème clair/sombre">🌙</button>
  </div>
</header>
<main>

<section>
  <h2>État du projet</h2>
  <div class="grille-etat" id="etat">Chargement…</div>
</section>

<section>
  <h2>Nœud RUDI <span id="badge-noeud" class="badge idle">…</span></h2>
  <div class="ligne-job">
    <button id="btn-noeud" onclick="basculerNoeud()" disabled>…</button>
    <a id="lien-noeud" href="#" target="_blank" class="discret desactive">Ouvrir le nœud</a>
    <a id="lien-catalogue" href="#" target="_blank" class="discret desactive">Ouvrir le catalogue</a>
  </div>
</section>

<section>
  <h2>Actions</h2>
  <div class="grille-actions" id="actions"></div>
  <div class="action disabled" style="margin-top:10px">
    <div class="titre">Découverte interactive</div>
    <div class="desc">Non pilotable depuis ce tableau de bord (prompts terminal). Lancer : <code>python3 src/cli.py</code></div>
  </div>
</section>

<section>
  <h2>JDD à examiner <span id="badge-examen" class="badge idle">…</span></h2>
  <p class="meta" style="margin:0">Candidats ambigus et services WFS/WMS issus de la découverte automatique
  quotidienne (<code>harvest_auto.py</code>), en attente de confirmation manuelle.
  <a href="/examen">Voir la liste →</a></p>
</section>

<section>
  <h2>Job en cours <span id="badge-job" class="badge idle">inactif</span></h2>
  <div class="ligne-job">
    <span id="label-job" class="meta">Aucun job lancé pour l'instant.</span>
  </div>
  <div id="journal"></div>
</section>

<section>
  <h2>Purger des données existantes</h2>
  <table class="purge">
    <thead><tr><th>Élément</th><th>Taille</th><th>Impact</th><th></th></tr></thead>
    <tbody id="purge-corps"></tbody>
  </table>
</section>

</main>
<div id="notif"></div>

<script>
const ACTIONS = [
  {id:"moisson_tabulaire", titre:"Moisson tabulaire", desc:"data.gouv.fr configuré (DATASETS)"},
  {id:"moisson_batch", titre:"Moisson batch", desc:"Candidats découverts (decouverte.json)"},
  {id:"moisson_insee", titre:"Moisson INSEE", desc:"Publications directes insee.fr", champIds:true},
  {id:"moisson_oeb", titre:"Moisson OEB", desc:"Portail environnement Bretagne (data-fair)", champIds:true},
  {id:"moisson_bdnb", titre:"Moisson BDNB", desc:"Bâtiments dep. 35 — DPE, énergie, FFO (~620 Mo)"},
  {id:"moisson_geo", titre:"Moisson géo", desc:"WFS / WMS / OGC API Features"},
  {id:"catalogue", titre:"(Re)générer le catalogue", desc:"data/catalogue.json + .html"},
  {id:"publier_rudi", titre:"Publier sur le nœud RUDI", desc:"Rattrapage — depuis les fichiers déjà sur disque"},
  {id:"enrichir_descriptions", titre:"Enrichir les descriptions", desc:"Rattrapage — JDD avec description vide/quasi vide"},
  {id:"verifier_backlog_examen", titre:"Vérifier le backlog « à examiner »", desc:"Rattrapage — classe les JDD sans ressource exploitable (métadonnées seulement, pas de téléchargement)"},
  {id:"moisson_batch_et_publier", titre:"Moisson batch + publication", desc:"Secours — au cas où l'auto-lancement depuis /examen aurait été sauté (job déjà en cours)"},
  {id:"moisson_geo_et_publier", titre:"Moisson géo + publication", desc:"Secours — au cas où l'auto-lancement depuis /examen aurait été sauté (job déjà en cours)"},
  {id:"pipeline_complet", titre:"Pipeline complet", desc:"Tabulaire → batch → INSEE → OEB → BDNB → géo → catalogue → RUDI", pipeline:true},
];

let jobEnCours = false;

function notifier(message, erreur){
  const conteneur = document.getElementById("notif");
  const div = document.createElement("div");
  if (erreur) div.className = "erreur";
  div.textContent = message;
  conteneur.appendChild(div);
  setTimeout(()=>div.remove(), 5000);
}

function esc(s){ return String(s??"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function rendreActions(){
  const conteneur = document.getElementById("actions");
  conteneur.innerHTML = ACTIONS.map(a => `
    <div class="action ${a.pipeline ? "pipeline" : ""}" data-id="${a.id}">
      <div class="titre">${esc(a.titre)}</div>
      <div class="desc">${esc(a.desc)}</div>
      ${a.champIds ? `<input type="text" id="champ-${a.id}" placeholder="IDs séparés par des espaces (vide = toutes)">` : ""}
      <button onclick="lancerAction('${a.id}')">Lancer</button>
    </div>
  `).join("");
  appliquerEtatBoutons();
}

function appliquerEtatBoutons(){
  document.querySelectorAll("#actions button, .purge-action button").forEach(b => b.disabled = jobEnCours);
}

async function lancerAction(id){
  let params = {};
  if (id === "moisson_insee" || id === "moisson_oeb"){
    const champ = document.getElementById(`champ-${id}`);
    params.ids = champ ? champ.value.trim() : "";
  }
  const resp = await fetch(`/api/job/${id}`, {method:"POST", body: JSON.stringify(params)});
  const data = await resp.json();
  if (!resp.ok) { notifier(data.message, true); return; }
  notifier(data.message);
  actualiserJob();
}

async function actualiserEtat(){
  const resp = await fetch("/api/etat");
  const d = await resp.json();
  const cfg = d.datasets_configures;
  const stats = [];
  stats.push(["Datasets configurés", `${cfg.tabulaire} tab. / ${cfg.geo} géo / ${cfg.insee} INSEE / ${cfg.oeb} OEB / ${cfg.bdnb} BDNB`]);
  if (d.decouverte){
    stats.push(["Candidats en attente", d.decouverte.candidats]);
    stats.push(["JDD vus / exclus", `${d.decouverte.vus} / ${d.decouverte.exclus}`]);
  } else {
    stats.push(["Découverte", "aucun historique"]);
  }
  const tb = d.etat_moisson.tabulaire_batch, ins = d.etat_moisson.insee, oeb = d.etat_moisson.oeb;
  stats.push(["État tabulaire/batch", tb ? `${tb.total} suivi(s), ${tb.rudi_publie} publié(s)` : "aucun"]);
  stats.push(["État INSEE", ins ? `${ins.total} suivi(s), ${ins.rudi_publie} publié(s)` : "aucun"]);
  stats.push(["État OEB", oeb ? `${oeb.total} suivi(s), ${oeb.rudi_publie} publié(s)` : "aucun"]);
  stats.push(["Nœud RUDI", d.rudi_configure ? "configuré" : "NON configuré"]);
  stats.push(["Données moissonnées", `${d.donnees.n_dossiers} dossier(s)`]);
  document.getElementById("etat").innerHTML = stats.map(([lbl,val]) => `
    <div class="stat"><div class="val">${esc(val)}</div><div class="lbl">${esc(lbl)}</div></div>
  `).join("");
}

let noeudActionEnCours = false;
let intervalleRapideNoeud = null;

const LABELS_ETAT_NOEUD = {
  running: "en cours", exited: "arrêté", paused: "en pause",
};

async function actualiserNoeud(){
  const resp = await fetch("/api/noeud");
  const n = await resp.json();

  const badge = document.getElementById("badge-noeud");
  const bouton = document.getElementById("btn-noeud");
  let enDemarrage = false;

  if (!n.podman_installe){
    badge.className = "badge warn";
    badge.textContent = "podman introuvable";
    bouton.className = "";
    bouton.textContent = "Démarrer";
    bouton.disabled = true;
  } else if (!n.existe){
    badge.className = "badge idle";
    badge.textContent = "absent";
    bouton.className = "";
    bouton.textContent = "Démarrer";
    bouton.disabled = true;
  } else {
    const enCours = n.etat === "running";
    enDemarrage = enCours && !n.pret;
    badge.className = "badge " + (enDemarrage ? "running" : (enCours ? "termine" : "idle"));
    badge.textContent = enDemarrage ? "démarrage…" : (LABELS_ETAT_NOEUD[n.etat] || n.etat || "inconnu");
    bouton.textContent = enCours ? "Arrêter" : "Démarrer";
    bouton.className = enCours ? "danger" : "";
    bouton.disabled = noeudActionEnCours;
  }

  // Le lien vers le nœud n'est activé qu'une fois qu'il répond vraiment (pas seulement
  // que le conteneur tourne — l'appli interne met plusieurs secondes à démarrer).
  const lienNoeud = document.getElementById("lien-noeud");
  lienNoeud.href = n.url_manager || "#";
  lienNoeud.classList.toggle("desactive", !n.url_manager || !n.pret);

  const lienCatalogue = document.getElementById("lien-catalogue");
  lienCatalogue.href = n.catalogue_url || "#";
  lienCatalogue.classList.toggle("desactive", !n.catalogue_disponible);

  // Pendant le démarrage, on vérifie plus souvent pour refléter la disponibilité réelle
  // dès qu'elle survient, plutôt que d'attendre le prochain rafraîchissement (15s).
  if (enDemarrage && !intervalleRapideNoeud){
    intervalleRapideNoeud = setInterval(actualiserNoeud, 2000);
  } else if (!enDemarrage && intervalleRapideNoeud){
    clearInterval(intervalleRapideNoeud);
    intervalleRapideNoeud = null;
  }
}

async function basculerNoeud(){
  const bouton = document.getElementById("btn-noeud");
  const action = bouton.textContent === "Arrêter" ? "arreter" : "demarrer";
  noeudActionEnCours = true;
  bouton.disabled = true;
  try {
    const resp = await fetch(`/api/noeud/${action}`, {method:"POST"});
    const data = await resp.json();
    notifier(data.message, !data.ok);
  } finally {
    noeudActionEnCours = false;
    actualiserNoeud();
  }
}

let dernierStatut = null;

async function actualiserJob(){
  const resp = await fetch("/api/job");
  const j = await resp.json();
  jobEnCours = j.statut === "running";
  appliquerEtatBoutons();

  const badge = document.getElementById("badge-job");
  badge.className = "badge " + j.statut;
  badge.textContent = {idle:"inactif", running:"en cours", termine:"terminé"}[j.statut] || j.statut;
  document.getElementById("label-job").textContent = j.label ? j.label : "Aucun job lancé pour l'instant.";

  const journal = document.getElementById("journal");
  journal.textContent = j.log || "";
  journal.scrollTop = journal.scrollHeight;

  if (dernierStatut === "running" && j.statut !== "running"){
    actualiserEtat();
    chargerPurge();
  }
  dernierStatut = j.statut;

  if (j.statut === "running") setTimeout(actualiserJob, 1000);
}

async function chargerPurge(){
  const resp = await fetch("/api/purge");
  const items = await resp.json();
  document.getElementById("purge-corps").innerHTML = items.map(it => `
    <tr>
      <td>${esc(it.label)}${it.destructeur ? ' <span class="badge running">DESTRUCTEUR</span>' : ""}</td>
      <td class="taille">${esc(it.taille_lisible)}</td>
      <td class="impact">${esc(it.impact)}</td>
      <td>
        <div class="purge-action">
          ${it.destructeur ? `<input type="text" id="conf-${it.id}" placeholder="Tapez SUPPRIMER" oninput="majBoutonPurge(${it.id})">` : ""}
          <button id="btn-purge-${it.id}" class="${it.destructeur ? "danger" : ""}"
                  ${it.destructeur ? "disabled" : ""}
                  onclick="purger(${it.id}, ${it.destructeur ? "true" : "false"})">Supprimer</button>
        </div>
      </td>
    </tr>
  `).join("");
  appliquerEtatBoutons();
}

async function actualiserBadgeExamen(){
  const resp = await fetch("/api/a_examiner");
  const items = await resp.json();
  const badge = document.getElementById("badge-examen");
  badge.textContent = items.length;
  badge.className = "badge " + (items.length ? "warn" : "idle");
}

function majBoutonPurge(id){
  const champ = document.getElementById(`conf-${id}`);
  const bouton = document.getElementById(`btn-purge-${id}`);
  bouton.disabled = jobEnCours || champ.value !== "SUPPRIMER";
}

async function purger(id, destructeur){
  let confirmation;
  if (destructeur){
    confirmation = document.getElementById(`conf-${id}`).value;
  } else {
    if (!confirm("Confirmer la suppression ?")) return;
    confirmation = "oui";
  }
  const resp = await fetch(`/api/purge/${id}`, {method:"POST", body: JSON.stringify({confirmation})});
  const data = await resp.json();
  notifier(data.message, !data.ok);
  if (data.ok) chargerPurge();
}

(function(){
  const btn = document.getElementById("theme-toggle");
  function effectif(){
    return document.documentElement.getAttribute("data-theme") ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  function majIcone(){ btn.textContent = effectif() === "dark" ? "☀️" : "🌙"; }
  btn.addEventListener("click", () => {
    const suivant = effectif() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", suivant);
    try { localStorage.setItem("theme", suivant); } catch(e) {}
    majIcone();
  });
  majIcone();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!document.documentElement.getAttribute("data-theme")) majIcone();
  });
})();

rendreActions();
actualiserEtat();
actualiserJob();
chargerPurge();
actualiserBadgeExamen();
actualiserNoeud();
setInterval(actualiserEtat, 15000);
setInterval(chargerPurge, 15000);
setInterval(actualiserBadgeExamen, 15000);
setInterval(actualiserNoeud, 15000);
</script>
</body>
</html>
"""


PAGE_EXAMEN_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moissonneuse-batteuse — JDD à examiner</title>
<script>(function(){try{var t=localStorage.getItem("theme");if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}})();</script>
<style>
  :root { --bg:#f5f6f8; --card:#fff; --txt:#1c2733; --muted:#667; --accent:#0b6e99; --bord:#e2e6ea;
          --ok:#1a8a4a; --warn:#a3372c; --disabled-bg:#aab4bb; --badge-idle-bg:#eef3f6; --badge-idle-txt:#345;
          --badge-warn-bg:#fbe9e7; --notif-bg:#20272e; --notif-err-bg:#a3372c; }
  :root[data-theme="dark"] { --bg:#10151b; --card:#1a222b; --txt:#dbe2e8; --muted:#8996a3; --accent:#4fb3da;
          --bord:#29323c; --ok:#3ddc84; --warn:#ff9686; --disabled-bg:#3a434c; --badge-idle-bg:#20303a;
          --badge-idle-txt:#b7c9d6; --badge-warn-bg:#3a201d; }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) { --bg:#10151b; --card:#1a222b; --txt:#dbe2e8; --muted:#8996a3; --accent:#4fb3da;
          --bord:#29323c; --ok:#3ddc84; --warn:#ff9686; --disabled-bg:#3a434c; --badge-idle-bg:#20303a;
          --badge-idle-txt:#b7c9d6; --badge-warn-bg:#3a201d; }
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background:var(--bg); color:var(--txt); line-height:1.45; }
  header { background:var(--card); border-bottom:1px solid var(--bord); padding:18px 24px;
           position:sticky; top:0; z-index:5; display:flex; justify-content:space-between; align-items:baseline; }
  h1 { margin:0; font-size:1.3rem; }
  h1 a { color:var(--muted); font-size:.85rem; font-weight:400; text-decoration:none; margin-left:14px; }
  h1 a:hover { text-decoration:underline; }
  .meta { color:var(--muted); font-size:.85rem; }
  main { max-width:1100px; margin:0 auto; padding:20px 24px 60px; display:grid; gap:18px; }
  section { background:var(--card); border:1px solid var(--bord); border-radius:10px; padding:16px 18px; }
  section h2 { margin:0 0 12px; font-size:1.02rem; }
  button { background:var(--accent); color:#fff; border:none; border-radius:6px; padding:8px 14px;
           font-size:.85rem; cursor:pointer; }
  button:hover:not(:disabled) { filter:brightness(1.08); }
  button:disabled { background:var(--disabled-bg); cursor:not-allowed; }
  .discret { background:transparent; color:var(--accent); border:1px solid var(--bord);
             padding:8px 14px; border-radius:6px; font-size:.85rem; display:inline-block;
             text-decoration:none; cursor:pointer; }
  .badge { display:inline-block; border-radius:99px; padding:2px 10px; font-size:.72rem; font-weight:600; }
  .badge.idle { background:var(--badge-idle-bg); color:var(--badge-idle-txt); }
  .badge.warn { background:var(--badge-warn-bg); color:var(--warn); }
  table.purge { width:100%; border-collapse:collapse; font-size:.85rem; }
  table.purge th, table.purge td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--bord); vertical-align:top; }
  table.purge th { color:var(--muted); font-weight:600; font-size:.78rem; }
  table.purge .impact { color:var(--muted); font-size:.78rem; max-width:380px; }
  .purge-action { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
  #notif { position:fixed; bottom:18px; right:18px; max-width:360px; }
  #notif div { background:var(--notif-bg); color:#fff; border-radius:8px; padding:10px 14px; margin-top:8px;
               font-size:.85rem; box-shadow:0 4px 14px rgba(0,0,0,.18); }
  #notif div.erreur { background:var(--notif-err-bg); }
  .onglets { display:flex; gap:6px; border-bottom:1px solid var(--bord); }
  .onglet { background:transparent; color:var(--muted); border:none; border-bottom:2px solid transparent;
            border-radius:0; padding:10px 4px; margin-bottom:-1px; font-size:.9rem; cursor:pointer;
            display:flex; align-items:center; gap:8px; }
  .onglet.actif { color:var(--txt); font-weight:600; border-bottom-color:var(--accent); }
  .analyse-panneau { background:var(--bg); border:1px solid var(--bord); border-radius:8px;
                      padding:12px 14px; display:grid; gap:10px; }
  .analyse-apercu { display:grid; gap:2px; font-size:.78rem; max-height:140px; overflow-y:auto; }
  .analyse-controles { display:flex; flex-wrap:wrap; gap:14px; align-items:center; }
  .analyse-controles label { display:flex; flex-direction:column; gap:3px; font-size:.78rem; color:var(--muted); }
  .analyse-controles select { padding:6px 8px; border:1px solid var(--bord); border-radius:6px; font-size:.85rem; }
  .analyse-resultat { font-size:.82rem; }
  .analyse-decision { display:flex; gap:8px; flex-wrap:wrap; }
  #theme-toggle { background:none; border:1px solid var(--bord); border-radius:6px; padding:6px 10px;
                  cursor:pointer; font-size:.9rem; line-height:1; color:var(--txt); }
  #theme-toggle:hover { background:var(--bg); }
  .entete-droite { display:flex; align-items:center; gap:12px; }
  a { color:var(--accent); }
</style>
</head>
<body>
<header>
  <h1>JDD à examiner <a href="/">← Tableau de bord</a></h1>
  <div class="entete-droite">
    <span class="meta">Rennes Métropole · 127.0.0.1 uniquement</span>
    <button id="theme-toggle" title="Basculer thème clair/sombre" aria-label="Basculer thème clair/sombre">🌙</button>
  </div>
</header>
<main>

<section>
  <div class="onglets">
    <button id="onglet-btn-examen" class="onglet actif" onclick="basculerOnglet('examen')">
      À examiner <span id="badge-examen" class="badge idle">…</span>
    </button>
    <button id="onglet-btn-echec" class="onglet" onclick="basculerOnglet('echec')">
      Analyse échouée <span id="badge-echec" class="badge idle">…</span>
    </button>
    <button id="onglet-btn-sans-ressource" class="onglet" onclick="basculerOnglet('sans-ressource')">
      Sans ressource <span id="badge-sans-ressource" class="badge idle">…</span>
    </button>
    <button id="onglet-btn-geo" class="onglet" onclick="basculerOnglet('geo')">
      Services géo <span id="badge-geo" class="badge idle">…</span>
    </button>
    <button id="onglet-btn-exclus" class="onglet" onclick="basculerOnglet('exclus')">
      Exclus <span id="badge-exclus" class="badge idle">…</span>
    </button>
    <button id="onglet-btn-ignores" class="onglet" onclick="basculerOnglet('ignores')">
      Ignorés <span id="badge-ignores" class="badge idle">…</span>
    </button>
  </div>

  <div id="onglet-examen">
    <p class="meta" style="margin:10px 0">Découverte automatique quotidienne (<code>harvest_auto.py</code>) : candidats
    tabulaires ambigus (0 ligne RM détectée), en attente de confirmation manuelle.</p>
    <table class="purge" id="table-examen">
      <thead><tr><th>JDD</th><th>Raison</th><th></th></tr></thead>
      <tbody id="examen-corps"></tbody>
    </table>
  </div>

  <div id="onglet-echec" style="display:none">
    <p class="meta" style="margin:10px 0">JDD tabulaires dont l'analyse automatique a levé une exception (format
    inattendu, erreur de parsing...) — ni confirmés ni infirmés pour Rennes Métropole, à revoir manuellement
    (bouton « Analyser »).</p>
    <table class="purge" id="table-echec">
      <thead><tr><th>JDD</th><th>Raison</th><th></th></tr></thead>
      <tbody id="echec-corps"></tbody>
    </table>
  </div>

  <div id="onglet-sans-ressource" style="display:none">
    <p class="meta" style="margin:10px 0">JDD tabulaires sans ressource exploitable (aucune ressource, ou uniquement des
    formats non pris en charge — ODS, SHP, PDF...) — classés automatiquement à la découverte quotidienne, ou au premier
    clic "Analyser" confirmant une absence définitive (un simple accident réseau ne fait pas basculer un JDD ici). Pour
    reclasser le backlog existant (JDD ajoutés avant cette classification automatique) : action « Vérifier le backlog «
    à examiner » » sur le <a href="/">tableau de bord principal</a>.</p>
    <table class="purge" id="table-sans-ressource">
      <thead><tr><th>JDD</th><th>Raison</th><th></th></tr></thead>
      <tbody id="sans-ressource-corps"></tbody>
    </table>
  </div>

  <div id="onglet-geo" style="display:none">
    <p class="meta" style="margin:10px 0">Services WFS/WMS en attente de confirmation manuelle. Un WFS avec au moins
    une feature RM confirmée (<code>nb_rm &gt; 0</code>) est ajouté automatiquement dès la découverte quotidienne —
    seul le reliquat détecté avant l'introduction de cet auto-ajout reste ici ; un WMS reste toujours soumis à
    confirmation (bbox de couche seule = signal trop faible).</p>
    <button id="btn-resoudre-wfs-masse" style="display:none; margin-bottom:10px"
            onclick="resoudreWfsEnMasse()">Ajouter automatiquement tous les WFS confirmés</button>
    <table class="purge" id="table-geo">
      <thead><tr><th>JDD</th><th>Type</th><th>Raison</th><th></th></tr></thead>
      <tbody id="geo-corps"></tbody>
    </table>
  </div>

  <div id="onglet-exclus" style="display:none">
    <p class="meta" style="margin:10px 0">Faux positifs écartés définitivement (bouton « Exclure »). « Rouvrir »
    remet le JDD dans « À examiner » — indisponible pour les JDD exclus avant l'introduction de ce suivi détaillé
    (titre/contexte non enregistrés à l'époque).</p>
    <table class="purge" id="table-exclus">
      <thead><tr><th>JDD</th><th>Raison</th><th>Exclu le</th><th></th></tr></thead>
      <tbody id="exclus-corps"></tbody>
    </table>
  </div>

  <div id="onglet-ignores" style="display:none">
    <p class="meta" style="margin:10px 0">JDD écartés du backlog sans décision définitive (bouton « Ignorer » — par
    exemple un service géo déjà ajouté à la main dans <code>DATASETS_GEO</code>). « Rouvrir » les remet dans
    « À examiner ».</p>
    <table class="purge" id="table-ignores">
      <thead><tr><th>JDD</th><th>Raison</th><th>Ignoré le</th><th></th></tr></thead>
      <tbody id="ignores-corps"></tbody>
    </table>
  </div>
</section>

</main>
<div id="notif"></div>

<script>
function notifier(message, erreur){
  const conteneur = document.getElementById("notif");
  const div = document.createElement("div");
  if (erreur) div.className = "erreur";
  div.textContent = message;
  conteneur.appendChild(div);
  setTimeout(()=>div.remove(), 5000);
}

function esc(s){ return String(s??"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

let itemsExamen = [];
let panneauOuvert = null;   // dataset_id de l'analyse manuelle actuellement ouverte, ou null
let etatAnalyse = {};       // dataset_id -> {entetes, typesVariables, dernierTest}
let ongletActif = "examen"; // "examen" | "echec" | "sans-ressource" | "geo" | "exclus" | "ignores"

const ONGLETS = ["examen", "echec", "sans-ressource", "geo", "exclus", "ignores"];

function basculerOnglet(nom){
  ongletActif = nom;
  for (const o of ONGLETS){
    document.getElementById(`onglet-${o}`).style.display = (o === nom) ? "" : "none";
    document.getElementById(`onglet-btn-${o}`).classList.toggle("actif", o === nom);
  }
}

function majBadge(nom, n){
  const badge = document.getElementById(`badge-${nom}`);
  badge.textContent = n;
  badge.className = "badge " + (n ? "warn" : "idle");
}

function ligneAnalyseEchouee(it){
  return `
    <tr id="ligne-${it.dataset_id}">
      <td>
        <div style="font-weight:600">${esc(it.titre)}</div>
        <div class="meta">${esc(it.organisation)} · <a href="${esc(it.url)}" target="_blank">data.gouv.fr</a></div>
        ${it.description ? `<div class="meta" style="margin-top:4px">${esc(it.description.slice(0, 200))}${it.description.length > 200 ? "…" : ""}</div>` : ""}
      </td>
      <td class="impact">${esc(it.raison)}</td>
      <td>
        <div class="purge-action">
          <button onclick="resoudreExamen('${it.dataset_id}','candidat')">Ajouter aux candidats</button>
          <button id="btn-analyser-${it.dataset_id}" onclick="analyserJdd('${it.dataset_id}')">Analyser</button>
          <button title="Exclusion définitive : ce JDD ne sera plus jamais reproposé, même après un reset de l'historique de découverte" onclick="resoudreExamen('${it.dataset_id}','exclure')">Exclure</button>
          <button title="Retire du backlog sans blacklist : peut réapparaître si la découverte retombe dessus plus tard" onclick="resoudreExamen('${it.dataset_id}','ignorer')">Ignorer</button>
        </div>
      </td>
    </tr>
  `;
}

async function chargerExamen(){
  if (panneauOuvert) return;  // ne pas écraser une analyse en cours de saisie
  const resp = await fetch("/api/a_examiner");
  itemsExamen = await resp.json();
  const echecAnalyse = itemsExamen.filter(it => !it.sans_ressource && (it.raison || "").startsWith("analyse échouée"));
  const sansRessource = itemsExamen.filter(it => it.sans_ressource);
  const reste = itemsExamen.filter(it => !it.sans_ressource && !(it.raison || "").startsWith("analyse échouée"));
  const aExaminer = reste.filter(it => it.type === "tabulaire");
  const servicesGeo = reste.filter(it => it.type !== "tabulaire");

  majBadge("examen", aExaminer.length);
  majBadge("echec", echecAnalyse.length);
  majBadge("sans-ressource", sansRessource.length);
  majBadge("geo", servicesGeo.length);

  document.getElementById("examen-corps").innerHTML = aExaminer.length ? aExaminer.map(it => `
    <tr id="ligne-${it.dataset_id}">
      <td>
        <div style="font-weight:600">${esc(it.titre)}</div>
        <div class="meta">${esc(it.organisation)} · <a href="${esc(it.url)}" target="_blank">data.gouv.fr</a></div>
        ${it.description ? `<div class="meta" style="margin-top:4px">${esc(it.description.slice(0, 200))}${it.description.length > 200 ? "…" : ""}</div>` : ""}
      </td>
      <td class="impact">${esc(it.raison)}${it.nb_rm ? ` (${it.nb_rm} RM)` : ""}</td>
      <td>
        <div class="purge-action">
          <button onclick="resoudreExamen('${it.dataset_id}','candidat')">Ajouter aux candidats</button>
          <button id="btn-analyser-${it.dataset_id}" onclick="analyserJdd('${it.dataset_id}')">Analyser</button>
          <button title="Exclusion définitive : ce JDD ne sera plus jamais reproposé, même après un reset de l'historique de découverte" onclick="resoudreExamen('${it.dataset_id}','exclure')">Exclure</button>
          <button title="Retire du backlog sans blacklist : peut réapparaître si la découverte retombe dessus plus tard" onclick="resoudreExamen('${it.dataset_id}','ignorer')">Ignorer</button>
        </div>
      </td>
    </tr>
  `).join("") : `<tr><td colspan="3" class="meta">Aucun JDD en attente d'examen.</td></tr>`;

  document.getElementById("geo-corps").innerHTML = servicesGeo.length ? servicesGeo.map(it => `
    <tr id="ligne-${it.dataset_id}">
      <td>
        <div style="font-weight:600">${esc(it.titre)}</div>
        <div class="meta">${esc(it.organisation)} · <a href="${esc(it.url)}" target="_blank">data.gouv.fr</a></div>
        ${it.description ? `<div class="meta" style="margin-top:4px">${esc(it.description.slice(0, 200))}${it.description.length > 200 ? "…" : ""}</div>` : ""}
      </td>
      <td>${esc(it.type)}</td>
      <td class="impact">${esc(it.raison)}${it.nb_rm ? ` (${it.nb_rm} RM)` : ""}</td>
      <td>
        <div class="purge-action">
          <button onclick="resoudreExamen('${it.dataset_id}','ajouter_geo')">Ajouter automatiquement</button>
          ${it.type === "wms" ? `<a class="discret" href="/examen/carte/${esc(it.dataset_id)}" target="_blank">Voir la carte</a>` : ""}
          <button title="Exclusion définitive : ce JDD ne sera plus jamais reproposé, même après un reset de l'historique de découverte" onclick="resoudreExamen('${it.dataset_id}','exclure')">Exclure</button>
          <button title="Retire du backlog sans blacklist : peut réapparaître si la découverte retombe dessus plus tard" onclick="resoudreExamen('${it.dataset_id}','ignorer')">Ignorer</button>
        </div>
      </td>
    </tr>
  `).join("") : `<tr><td colspan="4" class="meta">Aucun service géo en attente.</td></tr>`;

  const nbWfsConfirmes = servicesGeo.filter(it => it.type === "wfs" && (it.nb_rm || 0) > 0).length;
  const btnMasse = document.getElementById("btn-resoudre-wfs-masse");
  btnMasse.style.display = nbWfsConfirmes ? "" : "none";
  btnMasse.textContent = `Ajouter automatiquement tous les WFS confirmés (${nbWfsConfirmes})`;

  document.getElementById("echec-corps").innerHTML = echecAnalyse.length
    ? echecAnalyse.map(ligneAnalyseEchouee).join("")
    : `<tr><td colspan="3" class="meta">Aucun JDD ici.</td></tr>`;

  document.getElementById("sans-ressource-corps").innerHTML = sansRessource.length ? sansRessource.map(it => `
    <tr>
      <td>
        <div style="font-weight:600">${esc(it.titre)}</div>
        <div class="meta">${esc(it.organisation)} · <a href="${esc(it.url)}" target="_blank">data.gouv.fr</a></div>
      </td>
      <td class="impact">${esc(it.raison_indisponible || it.raison)}</td>
      <td>
        <div class="purge-action">
          <button title="Exclusion définitive : ce JDD ne sera plus jamais reproposé, même après un reset de l'historique de découverte" onclick="resoudreExamen('${it.dataset_id}','exclure')">Exclure</button>
          <button title="Retire du backlog sans blacklist : peut réapparaître si la découverte retombe dessus plus tard" onclick="resoudreExamen('${it.dataset_id}','ignorer')">Ignorer</button>
        </div>
      </td>
    </tr>
  `).join("") : `<tr><td colspan="3" class="meta">Aucun JDD sans ressource.</td></tr>`;
}

async function chargerHistorique(){
  const resp = await fetch("/api/historique");
  const data = await resp.json();

  majBadge("exclus", data.exclus.length);
  majBadge("ignores", data.ignores.length);

  const ligneHistorique = (it, coldate) => `
    <tr>
      <td>
        <div style="font-weight:600">${esc(it.titre)}</div>
        <div class="meta">${esc(it.organisation || "")}${it.url ? ` · <a href="${esc(it.url)}" target="_blank">data.gouv.fr</a>` : ""}</div>
      </td>
      <td class="impact">${esc(it.raison || "")}</td>
      <td class="impact">${esc(it[coldate] || "?")}</td>
      <td>
        ${it.rouvrable !== false
          ? `<button onclick="rouvrirHistorique('${it.dataset_id}')">Rouvrir</button>`
          : `<span class="meta">non rouvrable</span>`}
      </td>
    </tr>
  `;

  document.getElementById("exclus-corps").innerHTML = data.exclus.length
    ? data.exclus.map(it => ligneHistorique(it, "date_decision")).join("")
    : `<tr><td colspan="4" class="meta">Aucun JDD exclu.</td></tr>`;

  document.getElementById("ignores-corps").innerHTML = data.ignores.length
    ? data.ignores.map(it => ligneHistorique(it, "date_decision")).join("")
    : `<tr><td colspan="4" class="meta">Aucun JDD ignoré.</td></tr>`;
}

async function rouvrirHistorique(datasetId){
  const resp = await fetch("/api/historique/rouvrir", {method:"POST", body: JSON.stringify({dataset_id: datasetId})});
  const data = await resp.json();
  notifier(data.message, !data.ok);
  if (data.ok){
    chargerExamen();
    chargerHistorique();
  }
}

async function resoudreWfsEnMasse(){
  const resp = await fetch("/api/a_examiner/resoudre_wfs_masse", {method:"POST", body: "{}"});
  const data = await resp.json();
  notifier(data.message, !data.ok);
  if (data.ok) chargerExamen();
}

async function resoudreExamen(datasetId, decision, extra){
  const body = Object.assign({dataset_id: datasetId, decision}, extra || {});
  const resp = await fetch("/api/a_examiner", {method:"POST", body: JSON.stringify(body)});
  const data = await resp.json();
  notifier(data.message, !data.ok);
  if (data.ok){
    fermerAnalyse(datasetId);
    chargerExamen();
    if (decision === "exclure" || decision === "ignorer") chargerHistorique();
  }
}

function resumerLigne(ligne){
  return Object.entries(ligne).slice(0, 5)
    .map(([k, v]) => `${k}=${String(v ?? "").slice(0, 18)}`).join(" | ");
}

async function analyserJdd(datasetId){
  const resp = await fetch("/api/a_examiner/preview", {method:"POST", body: JSON.stringify({dataset_id: datasetId})});
  const data = await resp.json();
  if (!data.ok){
    notifier(data.message, true);
    // Échec définitif (aucune ressource exploitable) : bascule vers "Sans ressource".
    // Échec transitoire (réseau, dépendance manquante...) : bascule vers "Analyse échouée".
    // Dans les deux cas le serveur a déjà persisté l'état — recharger fait apparaître la ligne
    // dans le bon onglet immédiatement, sans attendre le prochain rafraîchissement périodique.
    chargerExamen();
    return;
  }
  etatAnalyse[datasetId] = {entetes: data.entetes, typesVariables: data.types_variables, dernierTest: null};
  panneauOuvert = datasetId;
  document.getElementById(`analyse-${datasetId}`)?.remove();
  const ligne = document.getElementById(`ligne-${datasetId}`);
  if (ligne) ligne.insertAdjacentHTML("afterend", rendreAnalyse(datasetId, data.lignes));
}

function rendreAnalyse(datasetId, lignes){
  const st = etatAnalyse[datasetId];
  const optsColonnes = st.entetes.map(e => `<option value="${esc(e)}">${esc(e)}</option>`).join("");
  const optsTypes = st.typesVariables.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
  const apercu = st.entetes.map(e => {
    const valeurs = lignes.map(l => esc(String(l[e] ?? "").slice(0, 20))).join(", ");
    return `<div>${esc(e)} : <span class="meta">${valeurs}</span></div>`;
  }).join("");
  return `
    <tr id="analyse-${datasetId}"><td colspan="4">
      <div class="analyse-panneau">
        <div class="analyse-apercu">${apercu}</div>
        <div class="analyse-controles">
          <label>Colonne de filtrage
            <select id="col1-${datasetId}" onchange="majTypeLatlon('${datasetId}')">${optsColonnes}</select>
          </label>
          <label>Type de variable
            <select id="type-${datasetId}" onchange="majTypeLatlon('${datasetId}')">${optsTypes}</select>
          </label>
          <span id="latlon-${datasetId}"></span>
          <button onclick="testerFiltre('${datasetId}')">Tester</button>
        </div>
        <div id="resultat-${datasetId}" class="analyse-resultat"></div>
        <div class="analyse-decision">
          <button id="btn-ajouter-${datasetId}" disabled onclick="confirmerAjout('${datasetId}')">Ajouter au catalogue</button>
          <button title="Exclusion définitive : ce JDD ne sera plus jamais reproposé, même après un reset de l'historique de découverte" onclick="resoudreExamen('${datasetId}','exclure')">Exclure</button>
          <button onclick="fermerAnalyse('${datasetId}')">Fermer</button>
        </div>
      </div>
    </td></tr>`;
}

function majTypeLatlon(datasetId){
  const type = document.getElementById(`type-${datasetId}`).value;
  const conteneur = document.getElementById(`latlon-${datasetId}`);
  if (type !== "latlon"){ conteneur.innerHTML = ""; delete conteneur.dataset.rendu; return; }
  if (conteneur.dataset.rendu) return;
  const st = etatAnalyse[datasetId];
  const optsColonnes = st.entetes.map(e => `<option value="${esc(e)}">${esc(e)}</option>`).join("");
  conteneur.innerHTML = `
    <label style="flex-direction:row; align-items:center; gap:6px">
      <input type="checkbox" id="combine-${datasetId}" checked onchange="basculerCombine('${datasetId}')">
      Coordonnées combinées dans une seule colonne (ex. '48.11,-1.68')
    </label>
    <span id="col2-conteneur-${datasetId}"></span>
  `;
  conteneur.dataset.rendu = "1";
}

function basculerCombine(datasetId){
  const combine = document.getElementById(`combine-${datasetId}`).checked;
  const conteneur = document.getElementById(`col2-conteneur-${datasetId}`);
  if (combine){ conteneur.innerHTML = ""; return; }
  const st = etatAnalyse[datasetId];
  const optsColonnes = st.entetes.map(e => `<option value="${esc(e)}">${esc(e)}</option>`).join("");
  conteneur.innerHTML = `<label>Colonne de longitude <select id="col2-${datasetId}">${optsColonnes}</select></label>`;
}

async function testerFiltre(datasetId){
  const col1 = document.getElementById(`col1-${datasetId}`).value;
  const type = document.getElementById(`type-${datasetId}`).value;
  let col2 = null;
  if (type === "latlon"){
    const combineEl = document.getElementById(`combine-${datasetId}`);
    if (combineEl && !combineEl.checked){
      const col2El = document.getElementById(`col2-${datasetId}`);
      col2 = col2El ? col2El.value : null;
    }
  }
  const resp = await fetch("/api/a_examiner/test-filter", {method:"POST",
    body: JSON.stringify({dataset_id: datasetId, type_variable: type, col1, col2})});
  const data = await resp.json();
  const zone = document.getElementById(`resultat-${datasetId}`);
  const btnAjouter = document.getElementById(`btn-ajouter-${datasetId}`);
  if (!zone || !btnAjouter) return;  // panneau fermé entre-temps
  if (!data.ok){
    notifier(data.message, true);
    zone.innerHTML = "";
    btnAjouter.disabled = true;
    return;
  }
  etatAnalyse[datasetId].dernierTest = {type_variable: type, col1, col2, nb_rm: data.nb_rm};
  const exemples = (data.exemples || []).map(ex => `<div class="meta">${esc(resumerLigne(ex))}</div>`).join("");
  zone.innerHTML = `<div><strong>${data.nb_rm}</strong> ligne(s) RM sur ${data.nb_total}.</div>${exemples}`;
  btnAjouter.disabled = false;
}

async function confirmerAjout(datasetId){
  const test = etatAnalyse[datasetId] && etatAnalyse[datasetId].dernierTest;
  if (!test) return;
  if (test.nb_rm === 0 && !confirm("0 ligne RM détectée — ajouter quand même au catalogue ?")) return;
  await resoudreExamen(datasetId, "candidat", test);
}

function fermerAnalyse(datasetId){
  document.getElementById(`analyse-${datasetId}`)?.remove();
  delete etatAnalyse[datasetId];
  if (panneauOuvert === datasetId) panneauOuvert = null;
}

(function(){
  const btn = document.getElementById("theme-toggle");
  function effectif(){
    return document.documentElement.getAttribute("data-theme") ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  function majIcone(){ btn.textContent = effectif() === "dark" ? "☀️" : "🌙"; }
  btn.addEventListener("click", () => {
    const suivant = effectif() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", suivant);
    try { localStorage.setItem("theme", suivant); } catch(e) {}
    majIcone();
  });
  majIcone();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!document.documentElement.getAttribute("data-theme")) majIcone();
  });
})();

chargerExamen();
chargerHistorique();
setInterval(chargerExamen, 15000);
setInterval(chargerHistorique, 15000);
</script>
</body>
</html>
"""


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT_DEFAUT
    try:
        serveur = http.server.ThreadingHTTPServer((HOST, port), Handler)
    except OSError as e:
        print(f"Impossible de démarrer le serveur sur {HOST}:{port} ({e}).")
        print(f"Essayez un autre port : python3 src/dashboard.py {port + 1}")
        return

    url = f"http://{HOST}:{port}/"
    print(f"Tableau de bord disponible sur {url}")
    print("(local uniquement — Ctrl+C pour arrêter)")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")
    finally:
        serveur.server_close()


if __name__ == "__main__":
    main()
