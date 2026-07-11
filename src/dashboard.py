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
      <span class="topbar-pill" id="tb-noeud" title="Nœud RUDI"><span class="tb-dot" aria-hidden="true"></span>Nœud</span>
      <span class="topbar-pill" id="tb-superset" title="Superset"><span class="tb-dot" aria-hidden="true"></span>Superset</span>
      <span class="topbar-pill" id="tb-job" title="Job"><span class="tb-dot" aria-hidden="true"></span>Job</span>
      <a href="/examen" class="topbar-pill" id="tb-examen-pill" title="JDD à examiner" style="text-decoration:none">
        <span class="tb-dot" aria-hidden="true"></span>Examen&nbsp;<span class="topbar-count" id="tb-examen"></span>
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
    "reanalyser_faux_positifs": ("Ré-analyse des faux positifs INSEE/CP (dry-run)",
                                 lambda p: cli.action_reanalyser_faux_positifs(appliquer_interactif=False)),
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


def _charger_page(nom_fichier: str, actif: str) -> str:
    """Charge une page HTML depuis src/static/ et y injecte le topbar.

    Chargée une fois à l'import, comme les anciens littéraux embarqués."""
    with open(os.path.join(STATIC_DIR, nom_fichier), encoding="utf-8") as f:
        return f.read().replace("{{TOPBAR}}", _html_topbar(actif))


PAGE_HTML = _charger_page("page_dashboard.html", "dashboard")


PAGE_EXAMEN_HTML = _charger_page("page_examen.html", "examen")


PAGE_DECOUVERTE_HTML = _charger_page("page_decouverte.html", "decouverte")


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
