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
  // Une erreur reste affichée plus longtemps qu'un succès — le temps de la lire vraiment.
  setTimeout(() => div.remove(), erreur ? 10000 : 4000);
}

/* --- Modal de confirmation partagée (remplace confirm() natif) ---
   Usage : const ok = await confirmerModal({titre, message, motCle, texteConfirmer, danger}); */
function confirmerModal({titre, message, motCle, texteConfirmer, danger} = {}){
  return new Promise(resolve => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    let resolu = false;
    function finir(v){
      if (resolu) return;
      resolu = true;
      document.removeEventListener("keydown", surEchap);
      overlay.remove();
      resolve(v);
    }
    function surEchap(e){ if (e.key === "Escape") finir(false); }
    overlay.addEventListener("click", e => { if (e.target === overlay) finir(false); });

    overlay.innerHTML = `<div class="modal">
      <h3>${esc(titre || "Confirmation")}</h3>
      <p class="modal-msg">${esc(message || "")}</p>
      ${motCle ? `<label>Tapez <strong>${esc(motCle)}</strong> pour confirmer
        <input type="text" id="modal-confirm-input" autocomplete="off"></label>` : ""}
      <div class="actions">
        <button class="discret" id="modal-confirm-annuler">Annuler</button>
        <button class="${danger ? "danger" : "success"}" id="modal-confirm-ok"
                ${motCle ? "disabled" : ""}>${esc(texteConfirmer || "Confirmer")}</button>
      </div>
    </div>`;
    document.body.appendChild(overlay);

    const btnOk = overlay.querySelector("#modal-confirm-ok");
    overlay.querySelector("#modal-confirm-annuler").addEventListener("click", () => finir(false));
    btnOk.addEventListener("click", () => finir(true));
    document.addEventListener("keydown", surEchap);

    if (motCle){
      const champ = overlay.querySelector("#modal-confirm-input");
      champ.addEventListener("input", () => { btnOk.disabled = champ.value !== motCle; });
      champ.addEventListener("keydown", e => { if (e.key === "Enter" && !btnOk.disabled) finir(true); });
      champ.focus();
    } else {
      btnOk.focus();
    }
  });
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
  const [noeudR, supersetR, portailR, jobR, examenR] = await Promise.allSettled([
    fetch("/api/noeud").then(r => r.json()),
    fetch("/api/superset").then(r => r.json()),
    fetch("/api/portail").then(r => r.json()),
    fetch("/api/job").then(r => r.json()),
    fetch("/api/a_examiner").then(r => r.json()),
  ]);

  // Nœud RUDI (multi-nœuds)
  const pill = document.getElementById("tb-noeud");
  if (pill && noeudR.status === "fulfilled"){
    const n = noeudR.value;
    const noeuds = n.noeuds || [];
    const toutPret = noeuds.length > 0 && noeuds.every(nd => nd.pret);
    const aucunPret = noeuds.every(nd => !nd.pret);
    const aucunInstalle = noeuds.length === 0;
    if (aucunInstalle){ pill.className = "topbar-pill warn"; pill.title = "Aucun nœud configuré"; }
    else if (toutPret){ pill.className = "topbar-pill ok"; pill.title = "Tous les nœuds prêts"; }
    else if (aucunPret){ pill.className = "topbar-pill warn"; pill.title = "Aucun nœud prêt"; }
    else { pill.className = "topbar-pill running"; pill.title = "Certains nœuds en cours"; }
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

  // Portail RUDI
  const pillPortail = document.getElementById("tb-portail");
  if (pillPortail && portailR.status === "fulfilled"){
    const p = portailR.value;
    if (!p.docker_installe){ pillPortail.className = "topbar-pill warn"; pillPortail.title = "Docker introuvable"; }
    else if (!p.existe){ pillPortail.className = "topbar-pill"; pillPortail.title = "Stack absent"; }
    else if (p.etat === "running" && !p.pret){ pillPortail.className = "topbar-pill running"; pillPortail.title = "Démarrage\u2026 (5-15 min)"; }
    else if (p.etat === "running"){ pillPortail.className = "topbar-pill ok"; pillPortail.title = "Opérationnel"; }
    else { pillPortail.className = "topbar-pill warn"; pillPortail.title = p.etat || "Arrêté"; }
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
  if (jobEnCours){
    const ok = await confirmerModal({
      titre: "Redémarrer le tableau de bord",
      message: "Un job est actuellement en cours. Redémarrer le serveur interrompt le suivi affiché "
              + "dans cette page (le job lui-même peut continuer selon son type). Continuer ?",
      texteConfirmer: "Redémarrer quand même",
      danger: true,
    });
    if (!ok) return;
  }
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
