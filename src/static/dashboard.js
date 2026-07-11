/* ==========================================================================
   Dashboard — JS partagé (utils, thème, top bar, notifications)
   ========================================================================== */

/* --- Utilitaires --- */
function esc(s){ return String(s??"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function notifier(message, erreur){
  const conteneur = document.getElementById("notif");
  if (!conteneur) return;
  const div = document.createElement("div");
  if (erreur) div.className = "erreur";
  div.textContent = message;
  div.addEventListener("click", () => div.remove());
  conteneur.appendChild(div);
  setTimeout(() => div.remove(), 5000);
}

async function apiFetch(url, opts){
  try {
    const resp = await fetch(url, opts);
    return {ok: resp.ok, data: await resp.json()};
  } catch(e) {
    notifier("Erreur réseau : " + e.message, true);
    return {ok: false, data: null};
  }
}

/* --- État global --- */
let jobEnCours = false;

/* --- Thème clair/sombre --- */
(function(){
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  function effectif(){
    return document.documentElement.getAttribute("data-theme") ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  function majIcone(){ btn.textContent = effectif() === "dark" ? "\u2600\uFE0F" : "\uD83C\uDF19"; }
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

/* --- Top bar : polling des statuts --- */
async function actualiserTopBar(){
  const [noeudR, supersetR, jobR, examenR] = await Promise.allSettled([
    fetch("/api/noeud").then(r => r.json()),
    fetch("/api/superset").then(r => r.json()),
    fetch("/api/job").then(r => r.json()),
    fetch("/api/a_examiner").then(r => r.json()),
  ]);

  // Nœud RUDI
  const pill = document.getElementById("tb-noeud");
  if (pill && noeudR.status === "fulfilled"){
    const n = noeudR.value;
    if (!n.podman_installe){ pill.className = "topbar-pill warn"; pill.title = "Podman introuvable"; }
    else if (!n.existe){ pill.className = "topbar-pill"; pill.title = "Conteneur absent"; }
    else if (n.etat === "running" && !n.pret){ pill.className = "topbar-pill running"; pill.title = "Démarrage\u2026"; }
    else if (n.etat === "running"){ pill.className = "topbar-pill ok"; pill.title = "Opérationnel"; }
    else { pill.className = "topbar-pill warn"; pill.title = n.etat || "Arrêté"; }
  }

  // Superset
  const pillSup = document.getElementById("tb-superset");
  if (pillSup && supersetR.status === "fulfilled"){
    const s = supersetR.value;
    if (!s.docker_installe){ pillSup.className = "topbar-pill warn"; pillSup.title = "Docker introuvable"; }
    else if (!s.existe){ pillSup.className = "topbar-pill"; pillSup.title = "Conteneur absent"; }
    else if (s.etat === "running" && !s.pret){ pillSup.className = "topbar-pill running"; pillSup.title = "Démarrage\u2026"; }
    else if (s.etat === "running"){ pillSup.className = "topbar-pill ok"; pillSup.title = "Opérationnel"; }
    else { pillSup.className = "topbar-pill warn"; pillSup.title = s.etat || "Arrêté"; }
  }

  // Job
  const pillJob = document.getElementById("tb-job");
  if (pillJob && jobR.status === "fulfilled"){
    const j = jobR.value;
    const running = j.statut === "running" || j.statut === "cancelling";
    jobEnCours = running;
    pillJob.className = "topbar-pill" + (running ? " running" : "");
    pillJob.title = running ? "Job : " + (j.label || "en cours") : "Aucun job en cours";
  }

  // Badge examen — nb de JDD tabulaires à examiner (1er onglet de /examen)
  const countEl = document.getElementById("tb-examen");
  if (countEl && examenR.status === "fulfilled"){
    const items = examenR.value;
    const n = Array.isArray(items)
      ? items.filter(it => it.type === "tabulaire" && !it.sans_ressource
          && !(it.raison || "").startsWith("analyse échouée")).length
      : 0;
    countEl.textContent = n || "";
    countEl.className = "topbar-count" + (n ? "" : " zero");
  }
}

/* --- Top bar : actions --- */
async function tbRedemarrerDashboard(){
  notifier("Redémarrage du tableau de bord\u2026");
  const {ok} = await apiFetch("/api/dashboard/restart", {method:"POST"});
  if (ok) setTimeout(() => location.reload(), 2000);
}

async function tbRegenererCatalogue(){
  const {ok, data} = await apiFetch("/api/job/catalogue", {method:"POST"});
  if (data) notifier(data.message, !ok);
}

/* --- Fermeture navigateur si job en cours --- */
window.addEventListener("beforeunload", e => {
  if (jobEnCours){ e.preventDefault(); e.returnValue = ""; }
});

/* --- Démarrage polling --- */
actualiserTopBar();
setInterval(actualiserTopBar, 15000);
