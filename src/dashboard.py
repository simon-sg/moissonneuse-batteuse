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


def _traiter_a_examiner(params: dict) -> tuple[int, dict]:
    dataset_id = str(params.get("dataset_id") or "").strip()
    decision = str(params.get("decision") or "").strip()
    if not dataset_id or decision not in ("exclure", "candidat", "ignorer"):
        return 400, {"ok": False, "message": "Paramètres invalides."}
    decouverte = discover.charger_decouverte()
    ok = discover.resoudre_a_examiner(decouverte, dataset_id, decision)
    if not ok:
        return 404, {"ok": False, "message": "Entrée introuvable, ou décision inapplicable "
                                              "(ex : « candidat » sans champ géo détecté)."}
    return 200, {"ok": True, "message": "Décision enregistrée.",
                 "restants": len(decouverte.get("a_examiner", []))}


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
<style>
  :root { --bg:#f5f6f8; --card:#fff; --txt:#1c2733; --muted:#667; --accent:#0b6e99; --bord:#e2e6ea;
          --ok:#1a8a4a; --warn:#a3372c; }
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
  button:disabled { background:#aab4bb; cursor:not-allowed; }
  button.danger { background:var(--warn); }
  .discret { background:transparent; color:var(--accent); border:1px solid var(--bord);
             padding:8px 14px; border-radius:6px; font-size:.85rem; display:inline-block;
             text-decoration:none; cursor:pointer; }
  .discret.desactive { opacity:.5; pointer-events:none; }
  .action.pipeline { background:#eef6f1; border-color:#bfe0cc; }
  .action.disabled { opacity:.55; }
  .badge { display:inline-block; border-radius:99px; padding:2px 10px; font-size:.72rem; font-weight:600; }
  .badge.idle { background:#eef3f6; color:#345; }
  .badge.running { background:#fff4e0; color:#9a6a00; }
  .badge.termine { background:#e8f5ec; color:var(--ok); }
  .badge.warn { background:#fbe9e7; color:var(--warn); }
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
  #notif div { background:var(--txt); color:#fff; border-radius:8px; padding:10px 14px; margin-top:8px;
               font-size:.85rem; box-shadow:0 4px 14px rgba(0,0,0,.18); }
  #notif div.erreur { background:var(--warn); }
</style>
</head>
<body>
<header>
  <h1>Moissonneuse-batteuse — Tableau de bord</h1>
  <span class="meta">Rennes Métropole · 127.0.0.1 uniquement</span>
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
  <p class="meta" style="margin:0 0 10px">Découverte automatique quotidienne (<code>harvest_auto.py</code>) : candidats
  ambigus (0 ligne RM détectée, analyse en échec) et services WFS/WMS en attente de confirmation manuelle.</p>
  <table class="purge" id="table-examen">
    <thead><tr><th>JDD</th><th>Type</th><th>Raison</th><th></th></tr></thead>
    <tbody id="examen-corps"></tbody>
  </table>
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

let itemsExamen = [];

async function chargerExamen(){
  const resp = await fetch("/api/a_examiner");
  itemsExamen = await resp.json();
  const badge = document.getElementById("badge-examen");
  badge.textContent = itemsExamen.length;
  badge.className = "badge " + (itemsExamen.length ? "warn" : "idle");
  document.getElementById("examen-corps").innerHTML = itemsExamen.length ? itemsExamen.map(it => `
    <tr>
      <td>
        <div style="font-weight:600">${esc(it.titre)}</div>
        <div class="meta">${esc(it.organisation)} · <a href="${esc(it.url)}" target="_blank">data.gouv.fr</a></div>
      </td>
      <td>${esc(it.type)}</td>
      <td class="impact">${esc(it.raison)}${it.nb_rm ? ` (${it.nb_rm} RM)` : ""}</td>
      <td>
        <div class="purge-action">
          ${it.type === "tabulaire"
            ? `<button onclick="resoudreExamen('${it.dataset_id}','candidat')">Ajouter aux candidats</button>`
            : `<button onclick="copierJsonGeo('${it.dataset_id}')">Copier JSON DATASETS_GEO</button>`}
          <button onclick="resoudreExamen('${it.dataset_id}','exclure')">Faux positif</button>
          <button onclick="resoudreExamen('${it.dataset_id}','ignorer')">Ignorer</button>
        </div>
      </td>
    </tr>
  `).join("") : `<tr><td colspan="4" class="meta">Aucun JDD en attente d'examen.</td></tr>`;
}

async function resoudreExamen(datasetId, decision){
  const resp = await fetch("/api/a_examiner", {method:"POST", body: JSON.stringify({dataset_id: datasetId, decision})});
  const data = await resp.json();
  notifier(data.message, !data.ok);
  if (data.ok) chargerExamen();
}

function copierJsonGeo(datasetId){
  const it = itemsExamen.find(e => e.dataset_id === datasetId);
  if (!it) return;
  const slug = datasetId.slice(0, 30).replace(/-/g, "_");
  const entree = {
    id: slug, type: it.type, url: it.service_url || "", couches: it.couches || [],
    titre: it.titre, producteur: it.organisation, dossier: slug, theme: "environment",
  };
  const texte = JSON.stringify(entree, null, 2);
  if (navigator.clipboard) {
    navigator.clipboard.writeText(texte).then(
      () => notifier("JSON copié — collez-le dans DATASETS_GEO (src/conf/datasets.py), puis « Ignorer » cette ligne."),
      () => notifier(texte, true)
    );
  } else {
    notifier(texte, true);
  }
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

rendreActions();
actualiserEtat();
actualiserJob();
chargerPurge();
chargerExamen();
actualiserNoeud();
setInterval(actualiserEtat, 15000);
setInterval(chargerPurge, 15000);
setInterval(chargerExamen, 15000);
setInterval(actualiserNoeud, 15000);
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
