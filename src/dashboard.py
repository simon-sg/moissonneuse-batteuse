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
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cli
import discover
from catalogue import GABARIT_WMS_MAP
from conf.discover import REQUETES_STRUCTUREES, KEYWORDS, NB_PAGES as CONF_NB_PAGES
from connectors import rudi_node, superset
from filters.discovery import _paginer, _filtrer_communs

HOST = "127.0.0.1"
PORT_DEFAUT = 8765

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


# ---------------------------------------------------------------------------
# Top bar partagée (navigation + statuts + actions rapides)
# ---------------------------------------------------------------------------

def _html_topbar(page_active: str) -> str:
    """Construit le HTML de la top bar partagée entre toutes les pages."""
    liens = [
        ("dashboard", "/", "Dashboard", False),
        ("catalogue", "/catalogue", "Catalogue", False),
        ("examen", "/examen", "Examen", False),
        ("decouverte", "/decouverte", "Découverte", False),
    ]
    nav_html = ""
    for pid, href, label, ext in liens:
        cls = "topbar-link" + (" actif" if pid == page_active else "")
        nav_html += f'<a href="{href}" class="{cls}">{label}</a>\n'

    return f"""<header class="topbar">
  <div class="topbar-left">
    <a href="/" class="topbar-brand">Moissonneuse-batteuse</a>
    <nav class="topbar-nav">
{nav_html}    </nav>
  </div>
  <div class="topbar-right">
    <div class="topbar-pills">
      <span class="topbar-pill" id="tb-noeud" title="Nœud RUDI"><span class="tb-dot"></span>Nœud</span>
      <span class="topbar-pill" id="tb-superset" title="Superset"><span class="tb-dot"></span>Superset</span>
      <span class="topbar-pill" id="tb-job" title="Job"><span class="tb-dot"></span>Job</span>
      <a href="/examen" class="topbar-pill" id="tb-examen-pill" title="JDD à examiner" style="text-decoration:none">
        <span class="tb-dot"></span>Examen&nbsp;<span class="topbar-count" id="tb-examen"></span>
      </a>
    </div>
    <div class="topbar-actions">
      <button class="topbar-btn" onclick="tbRedemarrerDashboard()" title="Redémarrer le dashboard">⟳</button>
      <button class="topbar-btn" onclick="tbRegenererCatalogue()" title="Regénérer le catalogue">↻</button>
      <button id="theme-toggle" title="Basculer thème clair/sombre" aria-label="Basculer thème clair/sombre">🌙</button>
    </div>
  </div>
</header>
"""


# ---------------------------------------------------------------------------
# Exécution des jobs en arrière-plan (un seul à la fois)
# ---------------------------------------------------------------------------

_verrou_job = threading.Lock()
_cancel_event = threading.Event()
_job = {"statut": "idle", "label": None, "debut": None, "fin": None, "buffer": None,
        "process": None}

# Actions exécutables en sous-processus (killable)
_ACTIONS_CMD = {
    "moisson_tabulaire": ["python3", "src/main.py"],
    "moisson_batch": ["python3", "src/harvest_batch.py"],
    "moisson_insee": ["python3", "src/harvest_insee.py"],
    "moisson_oeb": ["python3", "src/harvest_oeb.py"],
    "moisson_bdnb": ["python3", "src/harvest_bdnb.py"],
    "moisson_geo": ["python3", "src/harvest_geo.py"],
    "catalogue": ["python3", "src/catalogue.py"],
    "publier_rudi": ["python3", "src/publish_rudi.py"],
    "enrichir_descriptions": ["python3", "src/enrichir_descriptions.py"],
}

# Actions composites : séquence de sous-actions
_ACTIONS_COMPOSEES = {
    "moisson_batch_et_publier": ["moisson_batch", "catalogue", "publier_rudi"],
    "moisson_geo_et_publier": ["moisson_geo", "catalogue", "publier_rudi"],
}


from tee import Tee as _Tee


def _construire_commandes(nom: str, params: dict) -> list[list[str]] | None:
    """Transforme un nom d'action + params en liste de commandes sous-processus.
    Retourne None pour les actions sans équivalent script (exécutées in-process)."""
    if nom in _ACTIONS_CMD:
        cmd = _ACTIONS_CMD[nom].copy()
        if nom in ("moisson_insee", "moisson_oeb"):
            ids = (params.get("ids") or "").strip()
            if ids:
                cmd.extend(ids.split())
        return [cmd]
    if nom in _ACTIONS_COMPOSEES:
        cmds = []
        for sous_nom in _ACTIONS_COMPOSEES[nom]:
            if sous_nom in _ACTIONS_CMD:
                cmd = _ACTIONS_CMD[sous_nom].copy()
                if sous_nom in ("moisson_insee", "moisson_oeb"):
                    ids = (params.get("ids") or "").strip()
                    if ids:
                        cmd.extend(ids.split())
                cmds.append(cmd)
        return cmds
    return None


def _executer_sous_processus(commandes: list[list[str]], buffer: io.StringIO) -> None:
    """Exécute une séquence de sous-processus, lit stdout ligne à ligne,
    vérifie le drapeau d'annulation entre chaque commande."""
    for cmd in commandes:
        if _cancel_event.is_set():
            return

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=RACINE,
            text=True,
            bufsize=1,
        )

        with _verrou_job:
            _job["process"] = process

        for line in process.stdout:
            if _cancel_event.is_set():
                break
            buffer.write(line)
            sys.__stdout__.write(line)
            sys.__stdout__.flush()

        if _cancel_event.is_set() and process.poll() is None:
            process.send_signal(signal.SIGINT)

        process.wait()


def _annuler_job() -> tuple[bool, str]:
    """Annule le job en cours : drapeau + SIGINT au sous-processus + grace kill."""
    with _verrou_job:
        if _job["statut"] != "running":
            return False, "Aucun job en cours d'exécution."
        _job["statut"] = "cancelling"

    _cancel_event.set()

    process = _job.get("process")
    if process and process.poll() is None:
        process.send_signal(signal.SIGINT)

        def _forcer():
            time.sleep(5)
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                time.sleep(2)
                if process.poll() is None:
                    process.kill()

        threading.Thread(target=_forcer, daemon=True).start()

    return True, "Annulation en cours…"


def _redemarrer_dashboard() -> tuple[int, dict]:
    """Redémarre le serveur HTTP (os.execv) pour prendre en compte les modifications
    du code source. Retourne la réponse avant de remplacer le processus."""
    if _job["statut"] == "running":
        return 409, {"ok": False, "message": "Un job est en cours — impossible de redémarrer."}

    def _restart():
        time.sleep(0.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_restart, daemon=True).start()
    return 200, {"ok": True, "message": "Redémarrage du tableau de bord…"}


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
                                 lambda p: None),  # exécuté via _ACTIONS_COMPOSEES
    "moisson_geo_et_publier": ("Moisson géo + catalogue + publication RUDI",
                               lambda p: None),  # exécuté via _ACTIONS_COMPOSEES
    "reanalyser_wms": ("Re-analyser les WMS du backlog", lambda p: cli.action_reanalyser_wms()),
    "nettoyer_wms_geo": ("Nettoyer les WMS de geo_services.json", lambda p: cli.action_nettoyer_wms_geo()),
    "pipeline_complet": ("Pipeline complet (sans découverte)",
                         lambda p: cli.executer_pipeline_complet()),
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
        _cancel_event.clear()
        _job["statut"] = "running"
        _job["label"] = label
        _job["debut"] = time.time()
        _job["fin"] = None
        _job["buffer"] = io.StringIO()
        _job["process"] = None

    buffer = _job["buffer"]

    def cible():
        commandes = _construire_commandes(nom, params)
        if commandes:
            # Mode sous-processus (killable)
            try:
                _executer_sous_processus(commandes, buffer)
            except Exception as e:
                buffer.write(f"\nERREUR : {e}\n")
                sys.__stdout__.write(f"\nERREUR : {e}\n")
                import traceback as tb
                tb.print_exc()
        else:
            # Mode in-process (fallback pour actions sans script autonome)
            ancien_stdout = sys.stdout
            sys.stdout = _Tee(buffer, ancien_stdout)
            try:
                fn(params)
            except Exception as e:
                sys.__stdout__.write(f"\nERREUR : {e}\n")
                import traceback as tb
                tb.print_exc()
            finally:
                sys.stdout = ancien_stdout

        with _verrou_job:
            if _cancel_event.is_set():
                _job["statut"] = "cancelled"
            else:
                _job["statut"] = "termine"
            _job["fin"] = time.time()
            _job["process"] = None

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

    return etat


def _traiter_noeud_action(nom: str) -> tuple[int, dict]:
    fn = {"demarrer": rudi_node.demarrer_conteneur, "arreter": rudi_node.arreter_conteneur}.get(nom)
    if fn is None:
        return 404, {"ok": False, "message": "Action de nœud inconnue."}
    ok, message = fn()
    return (200 if ok else 500), {"ok": ok, "message": message}


# ---------------------------------------------------------------------------
# Superset (conteneur Docker)
# ---------------------------------------------------------------------------

def _etat_superset() -> dict:
    etat = superset.statut_conteneur()
    en_cours = etat.get("etat") == "running"
    etat["pret"] = bool(en_cours and superset.superset_pret())
    etat["url"] = superset.URL_SUPERSET
    return etat


def _traiter_superset_demarrer() -> tuple[int, dict]:
    ok, message = superset.demarrer_conteneur()
    return (200 if ok else 500), {"ok": ok, "message": message}


def _traiter_superset_arreter() -> tuple[int, dict]:
    ok, message = superset.arreter_conteneur()
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
    n = discover.resoudre_geo_confirme_en_masse(decouverte)
    if n == 0:
        return 200, {"ok": True, "message": "Aucun service géo confirmé en attente.", "resolus": 0}
    message = f"{n} service(s) géo ajouté(s) automatiquement. "
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
# Découverte — configuration et tests de requêtes
# ---------------------------------------------------------------------------

_DECOUVERTE_CONFIG_FILE = os.path.join(cli.DATA_DIR, "discover_config.json")
_DECOUVERTE_TEST_RESULTS_FILE = os.path.join(cli.DATA_DIR, "discover_test_results.json")
_verrou_test_discover = threading.Lock()
_test_discover = {"en_cours": False, "courant": None, "total": 0, "termine": 0,
                  "resultats": [], "log": "", "annuler": threading.Event()}


def _decouverte_config_par_defaut() -> dict:
    """Construit la config par défaut depuis REQUETES_STRUCTUREES + KEYWORDS."""
    categories = [
        {"id": "geographie", "label": "Géographie", "couleur": "#0b6e99"},
        {"id": "organisation", "label": "Organisations", "couleur": "#6a1b9a"},
        {"id": "format", "label": "Formats", "couleur": "#e65100"},
        {"id": "competence", "label": "Compétences", "couleur": "#2d7d46"},
        {"id": "specialise", "label": "Spécialisé", "couleur": "#8c4a1a"},
        {"id": "autre", "label": "Autre", "couleur": "#667"},
    ]
    # Catégorisation automatique des REQUETES_STRUCTUREES existantes
    _CAT_MAP = {
        0: "geographie",    # featured
        1: "geographie",    # commune + granularité
        2: "geographie",    # iris + granularité
        3: "geographie",    # epci
        4: "geographie",    # code insee
        5: "geographie",    # code postal
        6: "geographie",    # adresse
        7: "geographie",    # siren
        8: "geographie",    # siret
        9: "geographie",    # sirene
        10: "organisation", # INSEE
        11: "organisation", # Cerema
        12: "organisation", # MTECT
        13: "competence",   # transport
        14: "format",       # WMS
        15: "format",       # WFS
        16: "format",       # GeoJSON
    }
    requetes = []
    for i, req in enumerate(REQUETES_STRUCTUREES):
        params = dict(req["params"])
        cat = _CAT_MAP.get(i, "competence")
        if i >= 17:
            cat = "specialise" if i >= 36 else "competence"
        requetes.append({
            "id": f"rs_{i}",
            "categorie": cat,
            "label": req["label"],
            "actif": True,
            "params": params,
        })
    mots_cles = []
    for kw in KEYWORDS:
        mots_cles.append({
            "id": f"kw_{kw.replace(' ', '_')}",
            "categorie": "competence",
            "label": kw,
            "actif": True,
            "params": {"q": kw, "sort": "-views"},
        })
    return {"categories": categories, "requetes": requetes, "mots_cles": mots_cles,
            "nb_pages": CONF_NB_PAGES}


def _charger_config_decouverte() -> dict:
    if os.path.isfile(_DECOUVERTE_CONFIG_FILE):
        try:
            with open(_DECOUVERTE_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg.setdefault("categories", [])
            cfg.setdefault("requetes", [])
            cfg.setdefault("mots_cles", [])
            cfg.setdefault("nb_pages", CONF_NB_PAGES)
            return cfg
        except Exception as e:
            print(f"⚠ config découverte illisible ({_DECOUVERTE_CONFIG_FILE}) : {e} — valeurs par défaut")
    return _decouverte_config_par_defaut()


def _sauvegarder_config_decouverte(cfg: dict) -> None:
    os.makedirs(os.path.dirname(_DECOUVERTE_CONFIG_FILE), exist_ok=True)
    tmp = _DECOUVERTE_CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _DECOUVERTE_CONFIG_FILE)


def _decouverte_config_json() -> dict:
    cfg = _charger_config_decouverte()
    # Ajouter nb_candidats / nb_exclus depuis decouverte.json pour info
    try:
        dec = discover.charger_decouverte()
        cfg["stats"] = {
            "candidats": len(dec.get("candidats", [])),
            "exclus": len(dec.get("exclus", [])),
            "vus": len(dec.get("vus", [])),
            "a_examiner": len(dec.get("a_examiner", [])),
        }
    except Exception:
        cfg["stats"] = {}
    return cfg


def _decouverte_config_save(params: dict) -> tuple[int, dict]:
    if _test_discover["en_cours"]:
        return 409, {"ok": False, "message": "Un test est en cours — attendez sa fin."}
    categories = params.get("categories")
    requetes = params.get("requetes")
    mots_cles = params.get("mots_cles")
    nb_pages = params.get("nb_pages")
    if categories is None or requetes is None:
        return 400, {"ok": False, "message": "Paramètres invalides (categories + requetes requis)."}
    cfg = _charger_config_decouverte()
    cfg["categories"] = categories
    cfg["requetes"] = requetes
    if mots_cles is not None:
        cfg["mots_cles"] = mots_cles
    if nb_pages is not None:
        cfg["nb_pages"] = max(1, min(200, int(nb_pages)))
    _sauvegarder_config_decouverte(cfg)
    return 200, {"ok": True, "message": "Configuration sauvegardée."}


def _lancer_test_decouverte(cfg: dict, indexes: list[int] | None) -> None:
    """Exécute les tests en tâche de fond. indexes=None → tous les actifs."""
    _test_discover["en_cours"] = True
    _test_discover["courant"] = None
    _test_discover["resultats"] = []
    _test_discover["log"] = ""
    _test_discover["annuler"].clear()

    nb_pages = cfg.get("nb_pages", CONF_NB_PAGES)

    # Construire la liste des requêtes à tester
    if indexes is not None:
        requetes_a_tester = [(i, cfg["requetes"][i]) for i in indexes
                             if 0 <= i < len(cfg["requetes"])]
    else:
        requetes_a_tester = [(i, r) for i, r in enumerate(cfg["requetes"]) if r.get("actif", True)]

    total = len(requetes_a_tester)
    _test_discover["total"] = total
    _test_discover["termine"] = 0

    try:
        decouverte = discover.charger_decouverte()
    except Exception:
        decouverte = {"vus": [], "candidats": [], "exclus": [], "exclusions_termes": [],
                      "a_examiner": [], "echecs": [], "echecs_n": [], "sans_ressource": []}

    def _run():
        log_lines = []
        try:
            for idx_in_list, (i, req) in enumerate(requetes_a_tester):
                if _test_discover["annuler"].is_set():
                    log_lines.append("Annulé par l'utilisateur.")
                    break
                _test_discover["courant"] = {"index": i, "label": req.get("label", ""),
                                             "position": idx_in_list + 1}
                params = dict(req.get("params", {}))
                label = req.get("label", str(params))
                log_lines.append(f"[{idx_in_list + 1}/{total}] {label} — params: {params}")
                _test_discover["log"] = "\n".join(log_lines)

                debut = time.time()
                try:
                    tous, total_api = _paginer(params, nb_pages)
                    duree = time.time() - debut
                    filtrés = _filtrer_communs(tous, decouverte, ignorer_deja_vus=True)
                    candidats = [ds for ds in filtrés if ds.get("nb_rm", 0) > 0] if False else []
                    log_lines.append(
                        f"  → {total_api} total, {len(tous)} récupérés, "
                        f"{len(filtrés)} après filtre ({duree:.1f}s)")
                except Exception as e:
                    duree = time.time() - debut
                    tous, total_api, filtrés = [], 0, []
                    log_lines.append(f"  → ERREUR : {e} ({duree:.1f}s)")

                _test_discover["resultats"].append({
                    "index": i,
                    "label": label,
                    "params": params,
                    "total_api": total_api,
                    "recuperes": len(tous),
                    "filtrés": len(filtrés),
                    "duree": round(duree, 1),
                    "erreur": None if total_api > 0 or not tous else None,
                })
                _test_discover["termine"] = idx_in_list + 1
                _test_discover["log"] = "\n".join(log_lines)

        finally:
            _test_discover["en_cours"] = False
            _test_discover["courant"] = None
            _test_discover["log"] = "\n".join(log_lines) + "\nTerminé."
            # Persister les résultats sur disque
            try:
                os.makedirs(os.path.dirname(_DECOUVERTE_TEST_RESULTS_FILE), exist_ok=True)
                with open(_DECOUVERTE_TEST_RESULTS_FILE, "w", encoding="utf-8") as f:
                    json.dump({"resultats": _test_discover["resultats"],
                               "log": _test_discover["log"]}, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"⚠ résultats de test découverte non persistés : {e}")

    threading.Thread(target=_run, daemon=True).start()


def _test_decouverte_etat() -> dict:
    with _verrou_test_discover:
        etat = {
            "en_cours": _test_discover["en_cours"],
            "courant": _test_discover["courant"],
            "total": _test_discover["total"],
            "termine": _test_discover["termine"],
            "resultats": _test_discover["resultats"],
            "log": _test_discover["log"],
        }
    # Si aucun test en cours et aucun résultat en mémoire, charger depuis le disque
    if not etat["en_cours"] and not etat["resultats"] and os.path.isfile(_DECOUVERTE_TEST_RESULTS_FILE):
        try:
            with open(_DECOUVERTE_TEST_RESULTS_FILE, "r", encoding="utf-8") as f:
                persisted = json.load(f)
            etat["resultats"] = persisted.get("resultats", [])
            etat["log"] = persisted.get("log", "")
        except Exception as e:
            print(f"⚠ résultats de test découverte illisibles ({_DECOUVERTE_TEST_RESULTS_FILE}) : {e}")
    return etat


def _test_decouverte_stop() -> tuple[int, dict]:
    if not _test_discover["en_cours"]:
        return 200, {"ok": True, "message": "Aucun test en cours."}
    _test_discover["annuler"].set()
    return 200, {"ok": True, "message": "Annulation demandée…"}


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

    def _servir_catalogue(self) -> None:
        """Sert data/catalogue.html avec le topbar injecté (marqueur <!--TOPBAR-->,
        ou {{TOPBAR}} pour un catalogue généré avant l'inlining CSS)."""
        chemin = os.path.join(cli.DATA_DIR, "catalogue.html")
        if not os.path.isfile(chemin):
            self._repondre_json(404, {"erreur": "catalogue non généré"})
            return
        with open(chemin, encoding="utf-8") as f:
            html = f.read()
        topbar = _html_topbar("catalogue")
        html = html.replace("<!--TOPBAR-->", topbar).replace("{{TOPBAR}}", topbar)
        self._repondre_html(200, html)

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

    def _servir_fichier_statique(self, chemin_relatif: str) -> None:
        """Sert un fichier sous src/static/ — CSS/JS partagés du dashboard."""
        chemin_abs = os.path.normpath(os.path.join(STATIC_DIR, unquote(chemin_relatif)))
        if not chemin_abs.startswith(STATIC_DIR):
            self._repondre_json(404, {"erreur": "introuvable"})
            return
        if not os.path.isfile(chemin_abs):
            self._repondre_json(404, {"erreur": "introuvable"})
            return
        type_mime = mimetypes.guess_type(chemin_abs)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", type_mime)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(os.path.getsize(chemin_abs)))
        self.end_headers()
        with open(chemin_abs, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        if self.path == "/":
            self._repondre_html(200, PAGE_HTML)
        elif self.path == "/examen":
            self._repondre_html(200, PAGE_EXAMEN_HTML)
        elif self.path == "/decouverte":
            self._repondre_html(200, PAGE_DECOUVERTE_HTML)
        elif self.path == "/catalogue":
            self._servir_catalogue()
        elif self.path.startswith("/static/"):
            self._servir_fichier_statique(self.path[len("/static/"):])
        elif self.path == "/api/etat":
            self._repondre_json(200, cli.etat_projet())
        elif self.path == "/api/job":
            self._repondre_json(200, _etat_job())
        elif self.path == "/api/purge":
            self._repondre_json(200, _purge_items_json())
        elif self.path == "/api/noeud":
            self._repondre_json(200, _etat_noeud())
        elif self.path == "/api/superset":
            self._repondre_json(200, _etat_superset())
        elif self.path == "/api/a_examiner":
            self._repondre_json(200, _a_examiner_json())
        elif self.path == "/api/historique":
            self._repondre_json(200, _historique_json())
        elif self.path == "/api/decouverte/config":
            self._repondre_json(200, _decouverte_config_json())
        elif self.path == "/api/decouverte/test":
            self._repondre_json(200, _test_decouverte_etat())
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

        if self.path == "/api/job/cancel":
            ok, message = _annuler_job()
            self._repondre_json(200 if ok else 409, {"ok": ok, "message": message})
        elif self.path == "/api/dashboard/restart":
            code, payload = _redemarrer_dashboard()
            self._repondre_json(code, payload)
        elif self.path.startswith("/api/job/"):
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
        elif self.path == "/api/superset/demarrer":
            code, payload = _traiter_superset_demarrer()
            self._repondre_json(code, payload)
        elif self.path == "/api/superset/arreter":
            code, payload = _traiter_superset_arreter()
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
        elif self.path == "/api/decouverte/config":
            code, payload = _decouverte_config_save(params)
            self._repondre_json(code, payload)
        elif self.path == "/api/decouverte/test":
            if _test_discover["en_cours"]:
                self._repondre_json(409, {"ok": False, "message": "Un test est déjà en cours."})
            else:
                cfg = params.get("config") or _charger_config_decouverte()
                indexes = params.get("indexes")  # None = tous les actifs
                _lancer_test_decouverte(cfg, indexes)
                self._repondre_json(200, {"ok": True, "message": "Test lancé."})
        elif self.path == "/api/decouverte/test/stop":
            code, payload = _test_decouverte_stop()
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
<link rel="stylesheet" href="/static/dashboard.css">
<style>
  .grille-actions { display:flex; flex-direction:column; gap:4px; }
  .action { display:flex; align-items:center; gap:10px; padding:6px 10px; border-radius:6px;
            border:1px solid var(--bord); transition:background .1s; }
  .action:hover { background:rgba(128,128,128,.04); }
  .action .titre { font-weight:600; font-size:.82rem; white-space:nowrap; }
  .action .desc { font-size:.76rem; color:var(--muted); flex:1; min-width:0;
                  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .action button { white-space:nowrap; padding:4px 11px; font-size:.78rem; flex-shrink:0; }
  .action input[type=text] { width:120px; padding:3px 6px; font-size:.78rem;
                             border:1px solid var(--bord); border-radius:5px; }
  .action.pipeline { background:var(--pipeline-bg); border-color:var(--pipeline-bord); }
  .action.disabled { opacity:.55; }
  .puce { width:7px; height:7px; border-radius:50%; flex-shrink:0; }
  .groupe-sep { display:flex; align-items:center; gap:10px; margin:14px 0 4px; }
  .groupe-sep:first-child { margin-top:0; }
  .groupe-sep label { font-size:.7rem; font-weight:600; color:var(--muted); text-transform:uppercase;
                      letter-spacing:.06em; white-space:nowrap; }
  .groupe-sep::after { content:""; flex:1; height:1px; background:var(--bord); }
  #journal { background:#10161c; color:#d7dee4; border-radius:8px; padding:12px 14px; font-size:.8rem;
             font-family: ui-monospace, "SF Mono", Consolas, monospace; white-space:pre-wrap;
             overflow-wrap:anywhere; max-height:380px; overflow-y:auto; min-height:60px; }
  #journal:empty::before { content:"Aucun job lancé."; color:#677; }
  .outil-sticky { position:sticky; top:62px; z-index:4; background:var(--card); padding:0 0 6px; }
</style>
</head>
<body>
{{TOPBAR}}
<main>

<section>
  <h2>État du projet</h2>
  <div class="grille-etat" id="etat">Chargement…</div>
</section>

<section>
  <h2>Nœud RUDI <span id="badge-noeud" class="badge idle" title="État du nœud RUDI — conteneur Podman géré par rudi_node.py">…</span></h2>
  <div class="ligne-job">
    <button id="btn-noeud" onclick="basculerNoeud()" disabled
            title="Démarrer ou arrêter le conteneur Podman du nœud RUDI">…</button>
    <a id="lien-noeud" href="#" target="_blank" class="discret desactive"
       title="Ouvrir l'interface web du nœud RUDI (manager)">Ouvrir le nœud</a>
  </div>
</section>

<section>
  <h2>Superset <span id="badge-superset" class="badge idle" title="État du conteneur Docker Superset — tableau de bord data.gouv.fr">…</span></h2>
  <div class="ligne-job">
    <button id="btn-superset" onclick="basculerSuperset()"
            title="Démarrer ou arrêter le conteneur Docker Superset">Démarrer</button>
    <a id="lien-superset" href="#" target="_blank" class="discret desactive"
       title="Ouvrir l'interface web Superset">Ouvrir Superset</a>
  </div>
</section>

<section>
  <h2>Actions</h2>
  <div class="grille-actions" id="actions"></div>
</section>

<section>
  <h2>JDD à examiner <span id="badge-examen" class="badge idle" title="Nombre de JDD en attente d'examen dans le backlog">…</span></h2>
  <p class="meta" style="margin:0" title="Candidats ambigus (aucune colonne RM détectée) et services WFS/WMS issus de la découverte automatique, en attente de validation manuelle avant moisson">
  Candidats ambigus et services WFS/WMS issus de la découverte automatique
  quotidienne (<code>harvest_auto.py</code>), en attente de confirmation manuelle.
  <a href="/examen">Voir la liste →</a></p>
</section>

<section>
  <h2>Job en cours <span id="badge-job" class="badge idle" title="État du job : aucun job en cours">inactif</span></h2>
  <div class="ligne-job">
    <span id="label-job" class="meta">Aucun job lancé pour l'instant.</span>
    <span id="duree-job" class="meta" style="display:none"></span>
    <button id="btn-annuler-job" class="danger" style="display:none"
            onclick="annulerJob()" title="Arrêter le job en cours (SIGINT → SIGTERM → SIGKILL)">Annuler</button>
  </div>
  <div id="journal" role="log" aria-live="polite" title="Sortie console du job en cours ou du dernier job exécuté"></div>
</section>

<section>
  <h2>Purger des données existantes</h2>
  <table class="purge">
    <thead><tr><th title="Élément de données à purger">Élément</th><th title="Espace disque actuellement occupé">Taille</th><th title="Conséquence de la suppression — régénération possible ou non">Impact</th><th></th></tr></thead>
    <tbody id="purge-corps"></tbody>
  </table>
</section>

</main>
<div id="notif"></div>

<script src="/static/dashboard.js"></script>
<script>
const GROUPES = [
  {id:"moisson", titre:"Moisson", couleur:"#0b6e99"},
  {id:"catalogue", titre:"Catalogue & publication", couleur:"#2d7d46"},
  {id:"rattrapage", titre:"Rattrapage & secours", couleur:"#8c4a1a"},
  {id:"pipeline", titre:"Pipeline", couleur:"#a3372c"},
];
const ACTIONS = [
  {id:"moisson_tabulaire", titre:"Tabulaire", desc:"data.gouv.fr configuré (DATASETS)",
   tooltip:"Télécharge, filtre par commune RM et traduit les datasets tabulaires de data.gouv.fr listés dans DATASETS",
   groupe:"moisson"},
  {id:"moisson_batch", titre:"Batch", desc:"Candidats de la découverte (decouverte.json)",
   tooltip:"Moissonne les datasets repérés par la découverte automatique comme contenant des données RM",
   groupe:"moisson"},
  {id:"moisson_insee", titre:"INSEE", desc:"Publications insee.fr",
   tooltip:"Télécharge les publications INSEE — millésime automatique avec repli scraping si l'URL directe échoue",
   champIds:true, groupe:"moisson"},
  {id:"moisson_oeb", titre:"OEB", desc:"Observatoire env. Bretagne (data-fair)",
   tooltip:"Moissonne les données environnementales bretonnes depuis l'Observatoire de l'Environnement en Bretagne",
   champIds:true, groupe:"moisson"},
  {id:"moisson_bdnb", titre:"BDNB", desc:"Bâtiments dép. 35 — DPE, énergie, FFO",
   tooltip:"Moissonne les données BDNB (Bâtiments) du département 35 — DPE, énergie, FFO (~620 Mo téléchargés)",
   groupe:"moisson"},
  {id:"moisson_geo", titre:"Géo", desc:"WFS → GeoJSON · WMS · OGC API Features",
   tooltip:"Télécharge les couches WFS en GeoJSON, enregistre les références WMS — configuré dans DATASETS_GEO",
   groupe:"moisson"},
  {id:"catalogue", titre:"(Re)générer", desc:"catalogue.json + .html + cartes Leaflet",
   tooltip:"Reconstruit le catalogue, la page HTML et les cartes Leaflet — sans re-télécharger les données",
   groupe:"catalogue"},
  {id:"publier_rudi", titre:"Publier RUDI", desc:"Rattrapage — fichiers déjà sur disque",
   tooltip:"Publie (ou republie) les datasets moissonnés sur le nœud RUDI à partir des rudi_metadata.json",
   groupe:"catalogue"},
  {id:"enrichir_descriptions", titre:"Descriptions", desc:"Rattrapage — JDD vides/quasi vides",
   tooltip:"Génère une description de secours (thème, colonnes, producteur) pour les JDD sans description exploitable",
   groupe:"catalogue"},
  {id:"decouverte_interactive", titre:"Découverte interactive", desc:"Prompts input() — lancer python3 src/cli.py",
   tooltip:"La découverte utilise des prompts input() dans le terminal — impossible depuis ce tableau de bord web",
   groupe:"rattrapage", desactive:true},
  {id:"verifier_backlog_examen", titre:"Backlog examen", desc:"Classe les JDD sans ressource",
   tooltip:"Analyse le backlog decouverte.json : déplace les JDD sans ressource exploitable vers la bonne liste",
   groupe:"rattrapage"},
  {id:"moisson_batch_et_publier", titre:"Batch + publier", desc:"Batch → catalogue → RUDI",
   tooltip:"Enchaîne moisson batch, catalogue et publication RUDI — utile si l'auto-lancement depuis /examen a été sauté",
   groupe:"rattrapage"},
  {id:"moisson_geo_et_publier", titre:"Géo + publier", desc:"Géo → catalogue → RUDI",
   tooltip:"Enchaîne moisson géo, catalogue et publication RUDI — utile si l'auto-lancement depuis /examen a été sauté",
   groupe:"rattrapage"},
  {id:"pipeline_complet", titre:"Pipeline complet", desc:"Tabulaire → batch → INSEE → OEB → BDNB → géo → catalogue → RUDI",
   tooltip:"Exécute toutes les moissons, le catalogue et la publication RUDI à la suite",
   pipeline:true, groupe:"pipeline"},
];

function rendreActions(){
  const conteneur = document.getElementById("actions");
  conteneur.innerHTML = GROUPES.map(g => {
    const items = ACTIONS.filter(a => a.groupe === g.id);
    if (!items.length) return "";
    return `
    <div class="groupe-sep"><label>${esc(g.titre)}</label></div>
    ${items.map(a => `
      <div class="action ${a.pipeline?"pipeline":""} ${a.desactive?"disabled":""}" data-id="${a.id}" title="${esc(a.tooltip || a.desc)}">
        <span class="puce" style="background:${g.couleur}"></span>
        <span class="titre">${esc(a.titre)}</span>
        <span class="desc">${esc(a.desc)}</span>
        ${a.champIds ? `<input type="text" id="champ-${a.id}" placeholder="IDs (vide = toutes)">` : ""}
        ${a.desactive ? "" : `<button onclick="lancerAction('${a.id}')" title="Lancer ${esc(a.titre)}">Lancer</button>`}
      </div>
    `).join("")}`;
  }).join("");
  appliquerEtatBoutons();
}

function appliquerEtatBoutons(){
  document.querySelectorAll("#actions button:not([disabled]), .purge-action button:not([disabled])")
    .forEach(b => b.disabled = jobEnCours);
}

async function lancerAction(id){
  let params = {};
  if (id === "moisson_insee" || id === "moisson_oeb"){
    const champ = document.getElementById(`champ-${id}`);
    params.ids = champ ? champ.value.trim() : "";
  }
  const {ok, data} = await apiFetch(`/api/job/${id}`, {method:"POST", body: JSON.stringify(params)});
  if (!ok) { if (data) notifier(data.message, true); return; }
  notifier(data.message);
  actualiserJob();
}

const AIDE_METRIQUES = {
  "Datasets configurés": "Jeux de données configurés dans datasets.py, par connecteur (tabulaire / géo / INSEE / OEB / BDNB)",
  "Candidats en attente": "Datasets identifiés comme pertinents pour RM par la découverte, prêts pour la moisson batch",
  "JDD vus / exclus": "Nombre total de datasets examinés lors des sessions de découverte / exclus volontairement",
  "Découverte": "Historique de découverte (decouverte.json) — s'il n'existe pas, lancer src/discover.py ou src/harvest_auto.py",
  "État tabulaire/batch": "Datasets tabulaires suivis par state.json : total configurés / déjà publiés sur le nœud RUDI",
  "État INSEE": "Publications INSEE suivies par state_insee.json : total configurés / déjà publiés",
  "État OEB": "Publications OEB suivies : total configurés / déjà publiés",
  "Nœud RUDI": "Le nœud RUDI est-il configuré (rudi_node.json) ? Sans fichier de configuration, la publication est impossible",
  "Données moissonnées": "Nombre de dossiers dans data/ — un dossier contient les fichiers filtrés + métadonnées d'un jeu de données",
};

async function actualiserEtat(){
  const {ok, data: d} = await apiFetch("/api/etat");
  if (!ok || !d) { return; }
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
    <div class="stat" title="${esc(AIDE_METRIQUES[lbl] || "")}">
      <div class="val">${esc(val)}</div>
      <div class="lbl">${esc(lbl)}</div>
    </div>
  `).join("");
}

let noeudActionEnCours = false;
let intervalleRapideNoeud = null;

const LABELS_ETAT_NOEUD = {
  running: "en cours", exited: "arrêté", paused: "en pause",
};

async function actualiserNoeud(){
  const {ok, data: n} = await apiFetch("/api/noeud");
  if (!ok || !n) { return; }

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
    badge.title = enDemarrage ? "Le conteneur tourne mais l'application n'est pas encore prête"
                 : (enCours ? "Le nœud RUDI est opérationnel et répond aux requêtes"
                    : "Le conteneur est arrêté — cliquer sur Démarrer pour le lancer");
    bouton.textContent = enCours ? "Arrêter" : "Démarrer";
    bouton.className = enCours ? "danger" : "";
    bouton.disabled = noeudActionEnCours;
  }

  // Le lien vers le nœud n'est activé qu'une fois qu'il répond vraiment (pas seulement
  // que le conteneur tourne — l'appli interne met plusieurs secondes à démarrer).
  const lienNoeud = document.getElementById("lien-noeud");
  lienNoeud.href = n.url_manager || "#";
  lienNoeud.classList.toggle("desactive", !n.url_manager || !n.pret);

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
  const {ok, data} = await apiFetch(`/api/noeud/${action}`, {method:"POST"});
  if (data) notifier(data.message, !ok);
  noeudActionEnCours = false;
  actualiserNoeud();
  return;
}

let supersetActionEnCours = false;

async function actualiserSuperset(){
  const {ok, data: s} = await apiFetch("/api/superset");
  if (!ok || !s) { return; }

  const badge = document.getElementById("badge-superset");
  const bouton = document.getElementById("btn-superset");
  const lien = document.getElementById("lien-superset");

  if (!s.docker_installe){
    badge.className = "badge warn";
    badge.textContent = "docker introuvable";
    bouton.textContent = "Démarrer";
    bouton.className = "";
    bouton.disabled = true;
  } else if (!s.existe){
    badge.className = "badge idle";
    badge.textContent = "absent";
    bouton.textContent = "Démarrer";
    bouton.className = "";
    bouton.disabled = false;
  } else {
    const enCours = s.etat === "running";
    const enDemarrage = enCours && !s.pret;
    badge.className = "badge " + (enDemarrage ? "running" : (enCours ? "termine" : "idle"));
    badge.textContent = enDemarrage ? "démarrage…" : (enCours ? "en cours" : (s.etat || "inconnu"));
    badge.title = enDemarrage ? "Le conteneur tourne mais Superset n'est pas encore prêt"
                 : (enCours ? "Superset est opérationnel"
                    : "Le conteneur est arrêté");
    bouton.textContent = enCours ? "Arrêter" : "Démarrer";
    bouton.className = enCours ? "danger" : "";
    bouton.disabled = supersetActionEnCours;
  }

  lien.href = s.url || "#";
  lien.classList.toggle("desactive", !s.pret);
}

async function basculerSuperset(){
  const bouton = document.getElementById("btn-superset");
  const action = bouton.textContent === "Arrêter" ? "arreter" : "demarrer";
  supersetActionEnCours = true;
  bouton.disabled = true;
  const {ok, data} = await apiFetch(`/api/superset/${action}`, {method:"POST"});
  if (data) notifier(data.message, !ok);
  supersetActionEnCours = false;
  actualiserSuperset();
  return;
}

let dernierStatut = null;
let timerInterval = null;

async function actualiserJob(){
  const {ok, data: j} = await apiFetch("/api/job");
  if (!ok || !j) { setTimeout(actualiserJob, 1000); return; }
  jobEnCours = j.statut === "running" || j.statut === "cancelling";
  appliquerEtatBoutons();

  const badge = document.getElementById("badge-job");
  badge.className = "badge " + j.statut;
  const LABEL_STATUT = {idle:"inactif", running:"en cours", cancelling:"annulation…", cancelled:"annulé", termine:"terminé"};
  badge.textContent = LABEL_STATUT[j.statut] || j.statut;
  badge.title = "État : " + (LABEL_STATUT[j.statut] || j.statut);
  document.getElementById("label-job").textContent = j.label || "Aucun job lancé pour l'instant.";

  // Bouton annuler
  const btnAnnuler = document.getElementById("btn-annuler-job");
  btnAnnuler.style.display = (j.statut === "running") ? "" : "none";
  btnAnnuler.disabled = j.statut !== "running";

  // Timer durée
  const dureeSpan = document.getElementById("duree-job");
  if (j.statut === "running" && j.debut){
    dureeSpan.style.display = "";
    if (!timerInterval){
      const debut = j.debut * 1000;
      timerInterval = setInterval(() => {
        const ecoule = Date.now() - debut;
        const m = String(Math.floor(ecoule / 60000)).padStart(2, "0");
        const s = String(Math.floor((ecoule % 60000) / 1000)).padStart(2, "0");
        dureeSpan.textContent = `${m}:${s}`;
      }, 1000);
    }
  } else {
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
    dureeSpan.style.display = "none";
  }

  const journal = document.getElementById("journal");
  journal.textContent = j.log || "";
  journal.scrollTop = journal.scrollHeight;

  if (j.statut === "termine" || j.statut === "cancelled"){
    if (dernierStatut === "running" || dernierStatut === "cancelling"){
      actualiserEtat();
      chargerPurge();
    }
  }
  dernierStatut = j.statut;

  if (j.statut === "running" || j.statut === "cancelling") setTimeout(actualiserJob, 1000);
}

async function annulerJob(){
  const {ok, data} = await apiFetch("/api/job/cancel", {method:"POST"});
  if (data) notifier(data.message, !ok);
  if (ok) actualiserJob();
}

async function chargerPurge(){
  const {ok, data: items} = await apiFetch("/api/purge");
  if (!ok || !items) { return; }
  document.getElementById("purge-corps").innerHTML = items.map(it => {
    const destructeurTitre = "Action destructive : tapez SUPPRIMER pour confirmer, puis cliquez sur Supprimer";
    const normalTitre = "Supprime ces données. Attention : action irréversible pour cette catégorie.";
    return `
    <tr>
      <td>${esc(it.label)}${it.destructeur ? ' <span class="badge running" title="Action destructive — confirmation par code requise">DESTRUCTEUR</span>' : ""}</td>
      <td class="taille" title="Taille actuelle sur disque">${esc(it.taille_lisible)}</td>
      <td class="impact" title="${esc(it.impact)}">${esc(it.impact)}</td>
      <td>
        <div class="purge-action">
          ${it.destructeur ? `<input type="text" id="conf-${it.id}" placeholder="Tapez SUPPRIMER" oninput="majBoutonPurge(${it.id})">` : ""}
          <button id="btn-purge-${it.id}" class="${it.destructeur ? "danger" : ""}"
                  ${it.destructeur ? "disabled" : ""}
                  title="${it.destructeur ? destructeurTitre : normalTitre}"
                  onclick="purger(${it.id}, ${it.destructeur ? "true" : "false"})">Supprimer</button>
        </div>
      </td>
    </tr>`;
  }).join("");
  appliquerEtatBoutons();
}

async function actualiserBadgeExamen(){
  const {ok, data: items} = await apiFetch("/api/a_examiner");
  if (!ok || !items) return;
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
  const {ok, data} = await apiFetch(`/api/purge/${id}`, {method:"POST", body: JSON.stringify({confirmation})});
  if (data) notifier(data.message, !ok);
  if (ok) chargerPurge();
}

rendreActions();
actualiserEtat();
actualiserJob();
chargerPurge();
actualiserBadgeExamen();
actualiserNoeud();
actualiserSuperset();
setInterval(actualiserEtat, 15000);
setInterval(chargerPurge, 15000);
setInterval(actualiserBadgeExamen, 15000);
setInterval(actualiserNoeud, 15000);
setInterval(actualiserSuperset, 15000);
</script>
</body>
</html>
"""

PAGE_HTML = PAGE_HTML.replace("{{TOPBAR}}", _html_topbar("dashboard"))


PAGE_EXAMEN_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moissonneuse-batteuse — JDD à examiner</title>
<script>(function(){try{var t=localStorage.getItem("theme");if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}})();</script>
<link rel="stylesheet" href="/static/dashboard.css">
<style>
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
  .analyse-ligne td { background:var(--card); border-bottom:2px solid var(--accent); }
  .barre-examen { display:flex; gap:10px; align-items:center; margin:10px 0; flex-wrap:wrap; }
  .filtre-onglet { flex:1; min-width:180px; padding:8px 10px; font-size:.85rem;
                   border:1px solid var(--bord); border-radius:6px; background:var(--card); color:var(--txt); }
  .action-batch { display:flex; gap:6px; }
</style>
</head>
<body>
{{TOPBAR}}
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
    <div class="barre-examen">
      <input type="search" id="filtre-examen" class="filtre-onglet" placeholder="Filtrer…"
             oninput="filtrerOnglet()" aria-label="Filtrer les JDD">
      <span id="action-batch-examen" class="action-batch" style="display:none">
        <button onclick="batchResoudre('examen','exclure')">Exclure la sélection</button>
        <button onclick="batchResoudre('examen','ignorer')">Ignorer la sélection</button>
      </span>
    </div>
    <table class="purge" id="table-examen">
      <thead><tr><th style="width:30px"><input type="checkbox" id="sel-tout-examen" onchange="selTout('examen')" aria-label="Tout sélectionner"></th><th>JDD</th><th>Raison</th><th></th></tr></thead>
      <tbody id="examen-corps"></tbody>
    </table>
  </div>

  <div id="onglet-echec" style="display:none">
    <p class="meta" style="margin:10px 0">JDD tabulaires dont l'analyse automatique a levé une exception (format
    inattendu, erreur de parsing...) — ni confirmés ni infirmés pour Rennes Métropole, à revoir manuellement
    (bouton « Analyser »).</p>
    <div class="barre-examen">
      <input type="search" id="filtre-echec" class="filtre-onglet" placeholder="Filtrer…"
             oninput="filtrerOnglet()" aria-label="Filtrer les échecs">
    </div>
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
    <div class="barre-examen">
      <input type="search" id="filtre-sans-ressource" class="filtre-onglet" placeholder="Filtrer…"
             oninput="filtrerOnglet()" aria-label="Filtrer les sans-ressource">
    </div>
    <table class="purge" id="table-sans-ressource">
      <thead><tr><th>JDD</th><th>Raison</th><th></th></tr></thead>
      <tbody id="sans-ressource-corps"></tbody>
    </table>
  </div>

  <div id="onglet-geo" style="display:none">
    <p class="meta" style="margin:10px 0">Services WFS/WMS en attente de confirmation manuelle. Un service géo avec au moins
    une donnée RM confirmée (WFS: features dans la bbox RM, WMS: couches dont la bbox chevauche RM)
    est ajouté automatiquement dès la découverte quotidienne — seul le reliquat détecté avant
    l'introduction de cet auto-ajout reste ici.</p>
    <button id="btn-resoudre-wfs-masse" style="display:none; margin-bottom:10px"
            onclick="resoudreWfsEnMasse()">Ajouter automatiquement les services géo confirmés</button>
    <button id="btn-reanalyser-wms" style="display:none; margin-bottom:10px; margin-left:6px"
            onclick="reanalyserWms()">Re-analyser les WMS du backlog</button>
    <div class="barre-examen">
      <input type="search" id="filtre-geo" class="filtre-onglet" placeholder="Filtrer…"
             oninput="filtrerOnglet()" aria-label="Filtrer les services géo">
      <span id="action-batch-geo" class="action-batch" style="display:none">
        <button onclick="batchResoudre('geo','exclure')">Exclure la sélection</button>
        <button onclick="batchResoudre('geo','ignorer')">Ignorer la sélection</button>
      </span>
    </div>
    <table class="purge" id="table-geo">
      <thead><tr><th style="width:30px"><input type="checkbox" id="sel-tout-geo" onchange="selTout('geo')" aria-label="Tout sélectionner"></th><th>JDD</th><th>Type</th><th>Raison</th><th></th></tr></thead>
      <tbody id="geo-corps"></tbody>
    </table>
  </div>

  <div id="onglet-exclus" style="display:none">
    <p class="meta" style="margin:10px 0">Faux positifs écartés définitivement (bouton « Exclure »). « Rouvrir »
    remet le JDD dans « À examiner » — indisponible pour les JDD exclus avant l'introduction de ce suivi détaillé
    (titre/contexte non enregistrés à l'époque).</p>
    <div class="barre-examen">
      <input type="search" id="filtre-exclus" class="filtre-onglet" placeholder="Filtrer…"
             oninput="filtrerOnglet()" aria-label="Filtrer les exclus">
    </div>
    <table class="purge" id="table-exclus">
      <thead><tr><th>JDD</th><th>Raison</th><th>Exclu le</th><th></th></tr></thead>
      <tbody id="exclus-corps"></tbody>
    </table>
  </div>

  <div id="onglet-ignores" style="display:none">
    <p class="meta" style="margin:10px 0">JDD écartés du backlog sans décision définitive (bouton « Ignorer » — par
    exemple un service géo déjà ajouté à la main dans <code>DATASETS_GEO</code>). « Rouvrir » les remet dans
    « À examiner ».</p>
    <div class="barre-examen">
      <input type="search" id="filtre-ignores" class="filtre-onglet" placeholder="Filtrer…"
             oninput="filtrerOnglet()" aria-label="Filtrer les ignorés">
    </div>
    <table class="purge" id="table-ignores">
      <thead><tr><th>JDD</th><th>Raison</th><th>Ignoré le</th><th></th></tr></thead>
      <tbody id="ignores-corps"></tbody>
    </table>
  </div>
</section>

</main>
<div id="notif"></div>

<script src="/static/dashboard.js"></script>
<script>
let itemsExamen = [];
let panneauOuvert = null;
let etatAnalyse = {};
let ongletActif = "examen";

const ONGLETS = ["examen", "echec", "sans-ressource", "geo", "exclus", "ignores"];

function basculerOnglet(nom){
  ongletActif = nom;
  for (const o of ONGLETS){
    document.getElementById(`onglet-${o}`).style.display = (o === nom) ? "" : "none";
    document.getElementById(`onglet-btn-${o}`).classList.toggle("actif", o === nom);
  }
}

// Filtre textuel par onglet
function filtrerOnglet(){
  const q = (document.getElementById("filtre-" + ongletActif)?.value || "").toLowerCase();
  document.querySelectorAll(`#onglet-${ongletActif} .purge tbody tr`).forEach(tr => {
    tr.style.display = (!q || tr.textContent.toLowerCase().includes(q)) ? "" : "none";
  });
}

// Sélection par checkbox
function selTout(onglet){
  const coche = document.getElementById("sel-tout-" + onglet).checked;
  document.querySelectorAll(`#${onglet}-corps input[type=checkbox].sel-ligne`).forEach(cb => cb.checked = coche);
  majBatchBar(onglet);
}

function majBatchBar(onglet){
  const nb = document.querySelectorAll(`#${onglet}-corps input[type=checkbox].sel-ligne:checked`).length;
  const barre = document.getElementById("action-batch-" + onglet);
  if (barre) barre.style.display = nb ? "" : "none";
}

async function batchResoudre(onglet, decision){
  const ids = [];
  document.querySelectorAll(`#${onglet}-corps input[type=checkbox].sel-ligne:checked`).forEach(cb => ids.push(cb.value));
  if (!ids.length) return;
  for (const id of ids){
    const {data} = await apiFetch("/api/a_examiner", {method:"POST", body: JSON.stringify({dataset_id: id, decision})});
    if (data && !data.ok) notifier(data.message, true);
  }
  if (decision === "exclure" || decision === "ignorer") chargerHistorique();
  chargerExamen();
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
          <button title="Exclusion définitive" onclick="resoudreExamen('${it.dataset_id}','exclure')">Exclure</button>
          <button title="Retire sans blacklister" onclick="resoudreExamen('${it.dataset_id}','ignorer')">Ignorer</button>
        </div>
      </td>
    </tr>
  `;
}

async function chargerExamen(){
  if (panneauOuvert) return;
  const {ok, data} = await apiFetch("/api/a_examiner");
  if (!ok || !data) return;
  itemsExamen = data;
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
      <td><input type="checkbox" class="sel-ligne" value="${it.dataset_id}" onchange="majBatchBar('examen')" aria-label="Sélectionner"></td>
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
          <button title="Exclusion définitive" onclick="resoudreExamen('${it.dataset_id}','exclure')">Exclure</button>
          <button title="Retire sans blacklister" onclick="resoudreExamen('${it.dataset_id}','ignorer')">Ignorer</button>
        </div>
      </td>
    </tr>
  `).join("") : `<tr><td colspan="4" class="meta">Aucun JDD en attente d'examen.</td></tr>`;

  document.getElementById("geo-corps").innerHTML = servicesGeo.length ? servicesGeo.map(it => `
    <tr id="ligne-${it.dataset_id}">
      <td><input type="checkbox" class="sel-ligne" value="${it.dataset_id}" onchange="majBatchBar('geo')" aria-label="Sélectionner"></td>
      <td>
        <div style="font-weight:600">${esc(it.titre)}</div>
        <div class="meta">${esc(it.organisation)} · <a href="${esc(it.url)}" target="_blank">data.gouv.fr</a></div>
        ${it.description ? `<div class="meta" style="margin-top:4px">${esc(it.description.slice(0, 200))}${it.description.length > 200 ? "…" : ""}</div>` : ""}
      </td>
      <td>${esc(it.type)}</td>
      <td class="impact">${esc(it.raison)}${it.nb_rm ? ` (${it.nb_rm} RM)` : ""}</td>
      <td>
        <div class="purge-action">
          <button onclick="resoudreExamen('${it.dataset_id}','ajouter_geo')">Ajouter</button>
          ${it.type === "wms" ? `<a class="discret" href="/examen/carte/${esc(it.dataset_id)}" target="_blank">Carte</a>` : ""}
          <button title="Exclusion définitive" onclick="resoudreExamen('${it.dataset_id}','exclure')">Exclure</button>
          <button title="Retire sans blacklister" onclick="resoudreExamen('${it.dataset_id}','ignorer')">Ignorer</button>
        </div>
      </td>
    </tr>
  `).join("") : `<tr><td colspan="5" class="meta">Aucun service géo en attente.</td></tr>`;

  const nbGeoConfirmes = servicesGeo.filter(it => ["wfs","wms"].includes(it.type) && (it.nb_rm || 0) > 0).length;
  const btnMasse = document.getElementById("btn-resoudre-wfs-masse");
  btnMasse.style.display = nbGeoConfirmes ? "" : "none";
  btnMasse.textContent = `Ajouter les services géo confirmés (${nbGeoConfirmes})`;

  const nbWmsBacklog = servicesGeo.filter(it => it.type === "wms").length;
  const btnReanalyser = document.getElementById("btn-reanalyser-wms");
  btnReanalyser.style.display = nbWmsBacklog ? "" : "none";
  btnReanalyser.textContent = `Re-analyser les WMS du backlog (${nbWmsBacklog})`;

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
          <button title="Exclusion définitive" onclick="resoudreExamen('${it.dataset_id}','exclure')">Exclure</button>
          <button title="Retire sans blacklister" onclick="resoudreExamen('${it.dataset_id}','ignorer')">Ignorer</button>
        </div>
      </td>
    </tr>
  `).join("") : `<tr><td colspan="3" class="meta">Aucun JDD sans ressource.</td></tr>`;

  filtrerOnglet();
}

async function chargerHistorique(){
  const {ok, data} = await apiFetch("/api/historique");
  if (!ok || !data) return;

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
  const {ok, data} = await apiFetch("/api/historique/rouvrir", {method:"POST", body: JSON.stringify({dataset_id: datasetId})});
  if (!ok || !data) return;
  notifier(data.message, !data.ok);
  if (data.ok){ chargerExamen(); chargerHistorique(); }
}

async function resoudreWfsEnMasse(){
  const {ok, data} = await apiFetch("/api/a_examiner/resoudre_wfs_masse", {method:"POST", body: "{}"});
  if (!ok || !data) return;
  notifier(data.message, !data.ok);
  if (data.ok) chargerExamen();
}

async function reanalyserWms(){
  const {ok, data} = await apiFetch("/api/job/reanalyser_wms", {method:"POST", body: "{}"});
  if (!ok || !data) return;
  notifier(data.message, !data.ok);
}

async function resoudreExamen(datasetId, decision, extra){
  const body = Object.assign({dataset_id: datasetId, decision}, extra || {});
  const {ok, data} = await apiFetch("/api/a_examiner", {method:"POST", body: JSON.stringify(body)});
  if (!ok || !data) return;
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
  const {ok, data} = await apiFetch("/api/a_examiner/preview", {method:"POST", body: JSON.stringify({dataset_id: datasetId})});
  if (!ok || !data) return;
  if (!data.ok){
    notifier(data.message, true);
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
    <tr id="analyse-${datasetId}" class="analyse-ligne"><td colspan="4">
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
          <button title="Exclusion définitive" onclick="resoudreExamen('${datasetId}','exclure')">Exclure</button>
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
  const {ok, data} = await apiFetch("/api/a_examiner/test-filter", {method:"POST",
    body: JSON.stringify({dataset_id: datasetId, type_variable: type, col1, col2})});
  if (!ok || !data) return;
  const zone = document.getElementById(`resultat-${datasetId}`);
  const btnAjouter = document.getElementById(`btn-ajouter-${datasetId}`);
  if (!zone || !btnAjouter) return;
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
  if (test.nb_rm === 0 && !confirm("0 ligne RM détectée — ajouter quand même ?")) return;
  await resoudreExamen(datasetId, "candidat", test);
}

function fermerAnalyse(datasetId){
  document.getElementById(`analyse-${datasetId}`)?.remove();
  delete etatAnalyse[datasetId];
  if (panneauOuvert === datasetId) panneauOuvert = null;
}

// Raccourcis clavier
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.target.tagName === "TEXTAREA") return;
  if (ongletActif === "examen" || ongletActif === "geo" || ongletActif === "echec"){
    if (e.key === "a") {
      const sel = document.querySelector(`#onglet-${ongletActif} .purge tbody tr:first-child button`);
      if (sel && (sel.textContent.includes("Ajouter") || sel.textContent.includes("Candidat"))) sel.click();
    }
    if (e.key === "x") {
      const exclure = document.querySelector(`#onglet-${ongletActif} .purge tbody tr:first-child button[title*="définitive"]`);
      if (exclure) exclure.click();
    }
    if (e.key === "i") {
      const ignorer = document.querySelector(`#onglet-${ongletActif} .purge tbody tr:first-child button[title*="blacklister"]`);
      if (ignorer) ignorer.click();
    }
  }
});

chargerExamen();
chargerHistorique();
setInterval(chargerExamen, 15000);
setInterval(chargerHistorique, 15000);
</script>
</body>
</html>
"""

PAGE_EXAMEN_HTML = PAGE_EXAMEN_HTML.replace("{{TOPBAR}}", _html_topbar("examen"))


PAGE_DECOUVERTE_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moissonneuse-batteuse — Découverte data.gouv.fr</title>
<script>(function(){try{var t=localStorage.getItem("theme");if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}})();</script>
<link rel="stylesheet" href="/static/dashboard.css">
<style>
  main.wide { max-width:1200px; }
  .toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
  .toolbar .spacer { flex:1; }
  .filtre-categorie { display:flex; gap:4px; flex-wrap:wrap; }
  .cat-btn { padding:4px 10px; font-size:.78rem; border-radius:99px; border:1px solid var(--bord);
             background:transparent; color:var(--muted); cursor:pointer; transition:all .15s; }
  .cat-btn:hover { border-color:var(--accent); color:var(--accent); }
  .cat-btn.actif { background:var(--accent); color:#fff; border-color:var(--accent); }
  table.requetes { width:100%; font-size:.85rem; }
  table.requetes th, table.requetes td { text-align:left; padding:6px 8px;
    border-bottom:1px solid var(--bord); vertical-align:middle; }
  table.requetes th { color:var(--muted); font-weight:600; font-size:.78rem; background:var(--card); }
  table.requetes td { background:var(--card); }
  .requetes-wrapper { border:1px solid var(--bord); border-radius:8px; overflow:hidden; }
  .requetes-scroll { max-height:55vh; overflow-y:auto; }
  table.requetes colgroup col.c-actif { width:40px; }
  table.requetes colgroup col.c-cat { width:36px; }
  table.requetes colgroup col.c-label { }
  table.requetes colgroup col.c-params { width:260px; }
  table.requetes colgroup col.c-total { width:70px; }
  table.requetes colgroup col.c-filtre { width:70px; }
  table.requetes colgroup col.c-duree { width:55px; }
  table.requetes colgroup col.c-actions { width:140px; }
  table.requetes tr:hover td { background:var(--row-hover); }
  table.requetes tr.test-en-cours td { background:var(--row-test-bg); }
  .cat-dot { width:10px; height:10px; border-radius:50%; display:inline-block; vertical-align:middle; }
  .cb-actif { width:16px; height:16px; cursor:pointer; accent-color:var(--ok); }
  .barre-progression { height:6px; background:var(--progress-bg); border-radius:3px; overflow:hidden; margin:8px 0; }
  .barre-progression .remplissage { height:100%; background:var(--progress-fill); border-radius:3px;
    transition:width .3s ease; }
  #journal-test { background:#10161c; color:#d7dee4; border-radius:8px; padding:12px 14px; font-size:.8rem;
    font-family:ui-monospace,"SF Mono",Consolas,monospace; white-space:pre-wrap; overflow-wrap:anywhere;
    max-height:300px; overflow-y:auto; min-height:60px; }
  #journal-test:empty::before { content:"Aucun test lancé."; color:#677; }
  /* Modal */
  .modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,.4); z-index:10; display:flex;
    align-items:center; justify-content:center; }
  .modal { background:var(--card); border:1px solid var(--bord); border-radius:12px; padding:20px 24px;
    width:90%; max-width:520px; max-height:85vh; overflow-y:auto; box-shadow:0 8px 30px rgba(0,0,0,.2); }
  .modal h3 { margin:0 0 14px; font-size:1rem; }
  .modal label { display:block; margin-bottom:10px; font-size:.82rem; color:var(--muted); }
  .modal input[type=text], .modal input[type=number], .modal select { width:100%; padding:6px 8px;
    border:1px solid var(--bord); border-radius:6px; font-size:.85rem; background:var(--bg); color:var(--txt); }
  .modal .ligne-form { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .modal .actions { display:flex; gap:8px; justify-content:flex-end; margin-top:16px; }
</style>
</head>
<body>
{{TOPBAR}}
<main class="wide">

<section>
  <h2>État de la découverte</h2>
  <div class="grille-stats" id="stats-decouverte">Chargement…</div>
</section>

<section>
  <h2>Requêtes API <span id="badge-requetes" class="badge idle">…</span></h2>
  <div class="toolbar">
    <div class="filtre-categorie" id="filtres-categories"></div>
    <span class="spacer"></span>
    <button class="sm" onclick="ouvrirModalAjouter()" title="Ajouter une nouvelle requête">+ Ajouter</button>
  </div>
  <div class="requetes-wrapper">
    <div class="requetes-scroll">
      <table class="requetes">
        <colgroup>
          <col class="c-actif">
          <col class="c-cat">
          <col class="c-label">
          <col class="c-params">
          <col class="c-total">
          <col class="c-filtre">
          <col class="c-duree">
          <col class="c-actions">
        </colgroup>
        <thead><tr>
          <th title="Requête active / inactive">Actif</th>
          <th></th>
          <th>Libellé</th>
          <th>Paramètres API</th>
          <th>Total</th>
          <th>Filtrés</th>
          <th>Durée</th>
          <th>Actions</th>
        </tr></thead>
        <tbody id="requetes-corps"></tbody>
      </table>
    </div>
  </div>
</section>

<section>
  <h2>Exécution des tests
    <span id="badge-test" class="badge idle" title="État du test">inactif</span>
  </h2>
  <div class="ligne-job">
    <span id="label-test" class="meta">Aucun test lancé.</span>
    <span id="duree-test" class="meta" style="display:none"></span>
    <div>
      <button id="btn-test-all" onclick="lancerTestTous()" title="Tester toutes les requêtes actives">Tester tout</button>
      <button id="btn-test-stop" class="danger" style="display:none" onclick="arreterTest()" title="Annuler le test en cours">Arrêter</button>
    </div>
  </div>
  <div class="barre-progression" id="barre-progression" style="display:none">
    <div class="remplissage" id="remplissage" style="width:0%"></div>
  </div>
  <div id="journal-test" role="log" aria-live="polite" title="Journal du test en cours ou du dernier test exécuté"></div>
</section>

<section>
  <h2>Paramètres</h2>
  <div style="display:flex; gap:16px; align-items:center; flex-wrap:wrap">
    <label style="display:flex; align-items:center; gap:8px; font-size:.85rem; color:var(--muted)">
      Pages par requête :
      <input type="number" id="nb-pages" min="1" max="200" style="width:70px; padding:4px 6px;
        border:1px solid var(--bord); border-radius:6px; font-size:.85rem; background:var(--bg); color:var(--txt);">
    </label>
    <button class="sm success" onclick="sauvegarderConfig()" title="Sauvegarder la configuration sur disque">Sauvegarder</button>
    <button class="sm" onclick="reinitialiserConfig()" title="Réinitialiser à la configuration par défaut"
            style="background:transparent; color:var(--warn); border:1px solid var(--bord)">Réinitialiser</button>
  </div>
</section>

</main>
<div id="notif"></div>

<script src="/static/dashboard.js"></script>
<script>
let config = null;
let categorieActive = null;
let timerTest = null;
let timerDuree = null;

// --- Catégories ---
function couleurCat(catId){
  if (!config || !config.categories) return "#667";
  const c = config.categories.find(x => x.id === catId);
  return c ? c.couleur : "#667";
}
function labelCat(catId){
  if (!config || !config.categories) return catId;
  const c = config.categories.find(x => x.id === catId);
  return c ? c.label : catId;
}

function renduFiltresCategories(){
  if (!config) return;
  const el = document.getElementById("filtres-categories");
  el.innerHTML = `<button class="cat-btn ${categorieActive===null?"actif":""}"
    onclick="filtrerCategorie(null)">Toutes</button>` +
    config.categories.map(c =>
      `<button class="cat-btn ${categorieActive===c.id?"actif":""}"
        style="border-color:${esc(c.couleur)}; ${categorieActive===c.id ? "background:"+esc(c.couleur)+";color:#fff" : "color:"+esc(c.couleur)}"
        onclick="filtrerCategorie('${esc(c.id)}')">${esc(c.label)}</button>`
    ).join("");
}

function filtrerCategorie(catId){
  categorieActive = catId;
  renduFiltresCategories();
  renduRequetes();
}

// --- Requêtes ---
function renduRequetes(){
  if (!config) return;
  const tbody = document.getElementById("requetes-corps");
  const filtres = config.requetes.filter(r => !categorieActive || r.categorie === categorieActive);
  document.getElementById("badge-requetes").textContent = config.requetes.length;
  document.getElementById("badge-requetes").className = "badge " + (config.requetes.length ? "idle" : "warn");

  if (!filtres.length){
    tbody.innerHTML = `<tr><td colspan="8" class="meta" style="text-align:center;padding:20px">Aucune requête dans cette catégorie.</td></tr>`;
    return;
  }
  tbody.innerHTML = filtres.map((r, idx) => {
    const i = config.requetes.indexOf(r);
    const paramsStr = Object.entries(r.params).map(([k,v]) => `${k}=${v}`).join(", ");
    return `<tr id="rq-${i}" ${!r.actif ? 'style="opacity:.4"' : ""}>
      <td style="text-align:center">
        <input type="checkbox" class="cb-actif" ${r.actif ? "checked" : ""}
               title="${r.actif ? "Désactiver" : "Activer"}" onchange="basculerActif(${i})">
      </td>
      <td style="text-align:center" title="${esc(labelCat(r.categorie))}">
        <span class="cat-dot" style="background:${esc(couleurCat(r.categorie))}"></span>
      </td>
      <td style="font-weight:600">${esc(r.label)}</td>
      <td style="font-family:ui-monospace,'SF Mono',Consolas,monospace;font-size:.76rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(paramsStr)}">${esc(paramsStr)}</td>
      <td id="res-total-${i}" class="meta" style="text-align:right">—</td>
      <td id="res-filtre-${i}" class="meta" style="text-align:right">—</td>
      <td id="res-duree-${i}" class="meta" style="text-align:right">—</td>
      <td>
        <button class="sm" onclick="testerRequete(${i})" title="Tester cette requête">▶</button>
        <button class="sm" onclick="ouvrirModalEditer(${i})" title="Éditer">✎</button>
        <button class="sm danger" onclick="supprimerRequete(${i})" title="Supprimer">×</button>
      </td>
    </tr>`;
  }).join("");
}

function basculerActif(i){
  config.requetes[i].actif = !config.requetes[i].actif;
  renduRequetes();
}

function supprimerRequete(i){
  if (!confirm(`Supprimer la requête « ${config.requetes[i].label} » ?`)) return;
  config.requetes.splice(i, 1);
  renduRequetes();
  notifier("Requête supprimée (sauvegarder pour persist).");
}

// --- Modal édition/ajout ---
function ouvrirModalEditer(i){
  const r = config.requetes[i];
  const isNew = false;
  ouvrirModal({
    titre: "Éditer la requête",
    label: r.label,
    categorie: r.categorie,
    params: r.params,
    onSave: (data) => {
      config.requetes[i].label = data.label;
      config.requetes[i].categorie = data.categorie;
      config.requetes[i].params = data.params;
      renduRequetes();
      notifier("Requête mise à jour (sauvegarder pour persist).");
    }
  });
}

function ouvrirModalAjouter(){
  ouvrirModal({
    titre: "Ajouter une requête",
    label: "",
    categorie: categorieActive || (config.categories && config.categories[0] ? config.categories[0].id : "autre"),
    params: {q: "", "sort": "-views"},
    onSave: (data) => {
      config.requetes.push({
        id: "custom_" + Date.now(),
        categorie: data.categorie,
        label: data.label,
        actif: true,
        params: data.params,
      });
      renduRequetes();
      notifier("Requête ajoutée (sauvegarder pour persist).");
    }
  });
}

function ouvrirModal({titre, label, categorie, params, onSave}){
  const typesRequete = [
    {value:"q", label:"Mot-clé (q=...)", paramKey:"q"},
    {value:"organization", label:"Organisation (organization=...)", paramKey:"organization"},
    {value:"format", label:"Format (format=...)", paramKey:"format"},
    {value:"featured", label:"Featured (featured=true)", paramKey:"featured"},
    {value:"granularity", label:"Granularité + mot-clé", paramKey:"q+granularity"},
  ];
  // Détecter le type actuel
  let typeActuel = "q";
  if (params.featured) typeActuel = "featured";
  else if (params.organization) typeActuel = "organization";
  else if (params.format) typeActuel = "format";
  else if (params.granularity) typeActuel = "granularity";

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const catOpts = (config.categories||[]).map(c =>
    `<option value="${esc(c.id)}" ${c.id===categorie?"selected":""}>${esc(c.label)}</option>`
  ).join("");

  function paramsChamp(typeVal){
    switch(typeVal){
      case "featured": return `<div class="ligne-form">
        <label>Valeur <input type="text" id="modal-val" value="true" disabled></label>
        <label>Tri <select id="modal-sort"><option value="">aucun</option><option value="-views">-views</option></select></label></div>`;
      case "organization": return `<div class="ligne-form">
        <label>ID organisation <input type="text" id="modal-val" value="${esc(params.organization||"")}" placeholder="UUID"></label>
        <label>Tri <select id="modal-sort"><option value="-views" ${params.sort==="-views"?"selected":""}>-views</option><option value="" ${!params.sort?"selected":""}>aucun</option></select></label></div>`;
      case "format": return `<div class="ligne-form">
        <label>Format <input type="text" id="modal-val" value="${esc(params.format||"")}" placeholder="wms, wfs, csv..."></label>
        <label>Tri <select id="modal-sort"><option value="-views" ${params.sort==="-views"?"selected":""}>-views</option><option value="" ${!params.sort?"selected":""}>aucun</option></select></label></div>`;
      case "granularity": return `<div class="ligne-form">
        <label>Mot-clé <input type="text" id="modal-val" value="${esc(params.q||"")}" placeholder="commune, iris..."></label>
        <label>Granularité <input type="text" id="modal-gran" value="${esc(params.granularity||"")}" placeholder="fr:commune, fr:iris"></label></div>
        <label>Tri <select id="modal-sort"><option value="-views" ${params.sort==="-views"?"selected":""}>-views</option><option value="" ${!params.sort?"selected":""}>aucun</option></select></label>`;
      default: return `<div class="ligne-form">
        <label>Mot-clé (q) <input type="text" id="modal-val" value="${esc(params.q||"")}" placeholder="urbanisme, transport..."></label>
        <label>Tri <select id="modal-sort"><option value="-views" ${params.sort==="-views"?"selected":""}>-views</option><option value="" ${!params.sort?"selected":""}>aucun</option></select></label></div>`;
    }
  }

  function typeOpts(){
    return typesRequete.map(t =>
      `<option value="${t.value}" ${t.value===typeActuel?"selected":""}>${t.label}</option>`
    ).join("");
  }

  overlay.innerHTML = `<div class="modal">
    <h3>${esc(titre)}</h3>
    <label>Libellé <input type="text" id="modal-label" value="${esc(label)}" placeholder="Mon urbanisme"></label>
    <label>Catégorie <select id="modal-cat">${catOpts}</select></label>
    <label>Type de requête <select id="modal-type" onchange="reRenderModalChamps()">${typeOpts()}</select></label>
    <div id="modal-champs">${paramsChamp(typeActuel)}</div>
    <div class="actions">
      <button class="discret" onclick="this.closest('.modal-overlay').remove()">Annuler</button>
      <button class="success" onclick="modalSave()">Enregistrer</button>
    </div>
  </div>`;

  overlay._onSave = onSave;
  overlay._typesRequete = typesRequete;
  overlay._paramsChamp = paramsChamp;
  document.body.appendChild(overlay);
  document.getElementById("modal-label").focus();

  window.reRenderModalChamps = function(){
    const t = document.getElementById("modal-type").value;
    document.getElementById("modal-champs").innerHTML = overlay._paramsChamp(t);
  };

  window.modalSave = function(){
    const lbl = document.getElementById("modal-label").value.trim();
    if (!lbl){ notifier("Le libellé est obligatoire.", true); return; }
    const cat = document.getElementById("modal-cat").value;
    const type = document.getElementById("modal-type").value;
    const sort = document.getElementById("modal-sort")?.value || "";
    const val = document.getElementById("modal-val")?.value?.trim() || "";
    const gran = document.getElementById("modal-gran")?.value?.trim() || "";
    let p = {};
    if (sort) p.sort = sort;
    switch(type){
      case "featured": p.featured = "true"; break;
      case "organization": p.organization = val; break;
      case "format": p.format = val; break;
      case "granularity": p.q = val; p.granularity = gran; break;
      default: if (val) p.q = val; break;
    }
    overlay._onSave({label: lbl, categorie: cat, params: p});
    overlay.remove();
  };
}

// --- Tests ---
async function testerRequete(i){
  const {ok, data} = await apiFetch("/api/decouverte/test", {
    method:"POST", body: JSON.stringify({indexes: [i], config})
  });
  if (!ok){ if(data) notifier(data.message, true); return; }
  demarrerPollingTest();
}

async function lancerTestTous(){
  const {ok, data} = await apiFetch("/api/decouverte/test", {
    method:"POST", body: JSON.stringify({config})
  });
  if (!ok){ if(data) notifier(data.message, true); return; }
  demarrerPollingTest();
}

async function arreterTest(){
  await apiFetch("/api/decouverte/test/stop", {method:"POST"});
}

function demarrerPollingTest(){
  if (timerTest) return;
  pollTest();
  timerTest = setInterval(pollTest, 1000);
}

async function pollTest(){
  const {ok, data: st} = await apiFetch("/api/decouverte/test");
  if (!ok || !st) return;

  const badge = document.getElementById("badge-test");
  const btnAll = document.getElementById("btn-test-all");
  const btnStop = document.getElementById("btn-test-stop");
  const barre = document.getElementById("barre-progression");
  const remplissage = document.getElementById("remplissage");
  const label = document.getElementById("label-test");

  badge.className = "badge " + (st.en_cours ? "running" : (st.resultats.length ? "termine" : "idle"));
  badge.textContent = st.en_cours ? `${st.termine}/${st.total}` : (st.resultats.length ? "terminé" : "inactif");
  btnAll.style.display = st.en_cours ? "none" : "";
  btnStop.style.display = st.en_cours ? "" : "none";
  btnAll.disabled = false;

  if (st.en_cours && st.courant){
    label.textContent = `[${st.courant.position}/${st.total}] ${st.courant.label}`;
    barre.style.display = "";
    remplissage.style.width = (st.total ? (st.termine / st.total * 100) : 0) + "%";
    // Timer
    if (!timerDuree){
      const debut = Date.now();
      timerDuree = setInterval(() => {
        const e = Date.now() - debut;
        const m = String(Math.floor(e/60000)).padStart(2,"0");
        const s = String(Math.floor((e%60000)/1000)).padStart(2,"0");
        document.getElementById("duree-test").style.display = "";
        document.getElementById("duree-test").textContent = m+":"+s;
      }, 1000);
    }
  } else {
    label.textContent = st.resultats.length ? "Terminé." : "Aucun test lancé.";
    barre.style.display = "none";
    if (timerDuree){ clearInterval(timerDuree); timerDuree = null; }
    document.getElementById("duree-test").style.display = "none";
  }

  // Mettre à jour les résultats par requête
  for (const r of st.resultats){
    const totalEl = document.getElementById(`res-total-${r.index}`);
    const filtreEl = document.getElementById(`res-filtre-${r.index}`);
    const dureeEl = document.getElementById(`res-duree-${r.index}`);
    if (totalEl) totalEl.textContent = r.total_api;
    if (filtreEl) filtreEl.textContent = r["filtrés"];
    if (dureeEl) dureeEl.textContent = r.duree + "s";
    const row = document.getElementById(`rq-${r.index}`);
    if (row) row.classList.add("test-en-cours");
  }

  document.getElementById("journal-test").textContent = st.log || "";
  const journal = document.getElementById("journal-test");
  journal.scrollTop = journal.scrollHeight;

  if (!st.en_cours){
    if (timerTest){ clearInterval(timerTest); timerTest = null; }
    // Retirer les highlights
    document.querySelectorAll("tr.test-en-cours").forEach(tr => tr.classList.remove("test-en-cours"));
  }
}

// --- Config ---
async function chargerConfig(){
  const {ok, data} = await apiFetch("/api/decouverte/config");
  if (!ok || !data) return;
  config = data;
  document.getElementById("nb-pages").value = config.nb_pages || 50;
  // Stats
  const s = config.stats || {};
  document.getElementById("stats-decouverte").innerHTML = [
    ["Requêtes", (config.requetes||[]).length],
    ["Actives", (config.requetes||[]).filter(r=>r.actif).length],
    ["Mots-clés", (config.mots_cles||[]).length],
    ["Candidats", s.candidats ?? "?"],
    ["Vus", s.vus ?? "?"],
    ["Exclus", s.exclus ?? "?"],
    ["À examiner", s["a_examiner"] ?? "?"],
  ].map(([l,v]) => `<div class="stat"><div class="val">${esc(v)}</div><div class="lbl">${esc(l)}</div></div>`).join("");
  renduFiltresCategories();
  renduRequetes();
}

async function sauvegarderConfig(){
  if (!config) return;
  config.nb_pages = parseInt(document.getElementById("nb-pages").value) || 50;
  const {ok, data} = await apiFetch("/api/decouverte/config", {
    method:"POST", body: JSON.stringify(config)
  });
  if (data) notifier(data.message, !ok);
}

async function reinitialiserConfig(){
  if (!confirm("Réinitialiser la configuration aux valeurs par défaut ? Les modifications non sauvegardées seront perdues.")) return;
  // Supprimer le fichier config pour revenir aux défauts
  config = null;
  await chargerConfig();
  notifier("Configuration réinitialisée aux valeurs par défaut.");
}

// --- Init ---
chargerConfig().then(() => pollTest());
</script>
</body>
</html>
"""

PAGE_DECOUVERTE_HTML = PAGE_DECOUVERTE_HTML.replace("{{TOPBAR}}", _html_topbar("decouverte"))


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
