/* ==========================================================================
   Page portail RUDI — JS (config, preview couleurs, upload images)
   ========================================================================== */

const _PC_VARS = [
  {id:"primary-color",           label:"Bleu principal",  defaut:"#004680"},
  {id:"rudi-header-primary-color",label:"Bleu header",    defaut:"#002748"},
  {id:"accent-color",            label:"Accent (corail)", defaut:"#f36b43"},
  {id:"accent-color-svg",        label:"Accent SVG",      defaut:"#ff8d6d"},
  {id:"primary-text",            label:"Texte principal",  defaut:"#323643"},
  {id:"primary-text-light",      label:"Texte secondaire",defaut:"#71757e"},
  {id:"banner-solid-color",      label:"Fond sections",   defaut:"#e7e9ed"},
  {id:"error-color",             label:"Erreur",          defaut:"#d04838"},
  {id:"success-color",           label:"Succès",          defaut:"#498100"},
  {id:"focus",                   label:"Focus ring",      defaut:"#0270e7"},
  {id:"secondary-color",         label:"Secondaire",      defaut:"#ffffff"},
  {id:"self-data-color",         label:"Self-data fond",  defaut:"#e1eefc"},
  {id:"accent-color-svg-self-data",label:"Self-data accent",defaut:"#259da5"},
];

const _PC_IMAGES = [
  {key:"mainLogo",      label:"Logo header"},
  {key:"heroLeftImage",  label:"Hero gauche"},
  {key:"heroRightImage", label:"Hero droite"},
  {key:"footerLogo",     label:"Logo footer"},
];

let _pcConfig = null;
let _pcImagesServeur = [];

/* --- Chargement initial --- */
async function portailInit() {
  const [configResp, imagesResp] = await Promise.all([
    apiFetch("/api/portail/config"),
    apiFetch("/api/portail/images"),
  ]);
  if (!configResp.ok || !configResp.data) { notifier("Erreur de chargement de la config", true); return; }
  _pcConfig = configResp.data;
  if (imagesResp.ok && imagesResp.data?.images) _pcImagesServeur = imagesResp.data.images;
  _pcRemplirForm(_pcConfig);
  _pcConstruireCouleurs(_pcConfig.couleurs_lues || {});
  _pcMajPreview();
  _pcAfficherImages(_pcConfig.images || {});
  const div = document.getElementById("pc-images-liste");
  if (div) div.textContent = _pcImagesServeur.length ? _pcImagesServeur.join(", ") : "aucun fichier";
}

/* --- Remplir le formulaire --- */
function _pcRemplirForm(cfg) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ""; };
  set("pc-projectName", cfg.identite?.projectName);
  set("pc-teamName", cfg.identite?.teamName);
  set("pc-hero_titre1", cfg.textes?.hero_titre1);
  set("pc-hero_titre2", cfg.textes?.hero_titre2);
  set("pc-projects_titre1", cfg.textes?.projects_titre1);
  set("pc-projects_titre2", cfg.textes?.projects_titre2);
  set("pc-projects_description", cfg.textes?.projects_description);
  set("pc-features_titre1", cfg.textes?.features_titre1);
  set("pc-features_titre2", cfg.textes?.features_titre2);
  set("pc-features_description", cfg.textes?.features_description);
  set("pc-contact", cfg.liens?.contact);
  set("pc-docRudi", cfg.liens?.docRudi);
  set("pc-footer_url", cfg.liens?.footer_url);
  set("pc-center_lon", cfg.carte?.center_lon);
  set("pc-center_lat", cfg.carte?.center_lat);
  set("pc-zoom", cfg.carte?.zoom);

  const socialDiv = document.getElementById("pc-social-liste");
  socialDiv.innerHTML = "";
  (cfg.reseaux_sociaux || []).forEach(s => portailAjouterSocial(s.label, s.url));
}

/* --- Color pickers + preview --- */
function _pcConstruireCouleurs(lues) {
  const div = document.getElementById("pc-couleurs-liste");
  div.innerHTML = "";
  _PC_VARS.forEach(v => {
    const val = lues[v.id] || v.defaut;
    const row = document.createElement("div");
    row.className = "portail-couleur";
    row.innerHTML = `
      <input type="color" id="pc-color-${v.id}" value="${val}"
             oninput="portailCouleurChange(this)">
      <span class="couleur-label">${esc(v.label)}</span>
      <span class="couleur-val" id="pc-color-val-${v.id}">${esc(val)}</span>
    `;
    div.appendChild(row);
  });
}

function portailCouleurChange(el) {
  const id = el.id.replace("pc-color-", "");
  const valEl = document.getElementById("pc-color-val-" + id);
  if (valEl) valEl.textContent = el.value;
  _pcMajPreview();
}

function _pcMajPreview() {
  const get = id => document.getElementById("pc-color-" + id)?.value || "";
  const nav = document.getElementById("pc-preview-nav");
  const card = document.getElementById("pc-preview-card");
  const btn = document.getElementById("pc-preview-btn");

  const primary = get("primary-color");
  const header = get("rudi-header-primary-color");
  const accent = get("accent-color");
  const text = get("primary-text");
  const textLight = get("primary-text-light");
  const banner = get("banner-solid-color");

  nav.style.backgroundColor = primary;
  nav.style.color = "#fff";
  nav.querySelectorAll("span").forEach(s => s.style.color = "#fff");

  card.style.borderColor = banner;
  card.querySelector("h4").style.color = primary;
  card.querySelector("p").style.color = textLight;

  btn.style.backgroundColor = accent;
  btn.style.color = "#fff";
}

/* --- Réseaux sociaux --- */
function portailAjouterSocial(label, url) {
  const div = document.getElementById("pc-social-liste");
  const row = document.createElement("div");
  row.className = "portail-social";
  row.innerHTML = `
    <input type="text" class="social-label" placeholder="Label (ex: LinkedIn)" value="${esc(label || "")}">
    <input type="text" class="social-url" placeholder="URL" value="${esc(url || "")}">
    <button class="discret" onclick="this.parentElement.remove()" title="Supprimer" style="color:var(--warn)">✕</button>
  `;
  div.appendChild(row);
}

/* --- Images --- */
function _pcAfficherImages(images) {
  const grille = document.getElementById("pc-images-grille");
  grille.innerHTML = "";
  _PC_IMAGES.forEach(cfg => {
    const src = images[cfg.key] || "";
    const card = document.createElement("div");
    card.className = "portail-image-card";

    const imgHtml = src
      ? `<img src="${esc(src)}" alt="${esc(cfg.label)}" onerror="this.outerHTML='<div class=img-placeholder>introuvable</div>'">`
      : `<div class="img-placeholder">aucune</div>`;

    const opts = ['<option value="">— choisir —</option>'];
    if (src && !_pcImagesServeur.some(f => "/" + f === src)) {
      opts.push(`<option value="${esc(src)}" selected>${esc(src)} (actuel)</option>`);
    }
    _pcImagesServeur.forEach(f => {
      const val = "/" + f;
      const sel = val === src ? " selected" : "";
      opts.push(`<option value="${esc(val)}"${sel}>${esc(f)}</option>`);
    });
    opts.push('<option value="__custom__">Autre chemin…</option>');

    const showInput = src && !_pcImagesServeur.some(f => "/" + f === src);

    card.innerHTML = `
      ${imgHtml}
      <span>${esc(cfg.label)}</span>
      <select class="portail-image-select" data-key="${esc(cfg.key)}"
              onchange="portailImageSelectChange(this)" style="width:100%;font-size:.78rem">
        ${opts.join("")}
      </select>
      <input type="text" class="portail-image-path" data-key="${esc(cfg.key)}"
             value="${esc(src)}" style="font-size:.72rem;width:100%;text-align:center;display:${showInput ? 'block' : 'none'}"
             placeholder="/chemin/vers/image.png"
             onchange="portailImagePathChange(this)">
    `;
    grille.appendChild(card);
  });
}

function portailImageSelectChange(sel) {
  if (!_pcConfig) return;
  const key = sel.dataset.key;
  const input = sel.parentElement.querySelector(".portail-image-path");
  if (sel.value === "__custom__") {
    input.style.display = "block";
    input.value = "";
    input.focus();
  } else {
    input.style.display = "none";
    input.value = sel.value;
    if (_pcConfig.images) _pcConfig.images[key] = sel.value;
    _pcMajApercuImage(key, sel.value);
  }
}

function portailImagePathChange(el) {
  if (!_pcConfig) return;
  const key = el.dataset.key;
  if (_pcConfig.images) _pcConfig.images[key] = el.value;
  _pcMajApercuImage(key, el.value);
}

function _pcMajApercuImage(key, src) {
  const card = document.querySelector(`.portail-image-card [data-key="${key}"]`)?.closest(".portail-image-card");
  if (!card) return;
  const oldImg = card.querySelector("img");
  const oldPh = card.querySelector(".img-placeholder");
  const target = oldImg || oldPh;
  if (!target) return;
  if (src) {
    const img = document.createElement("img");
    img.src = src;
    img.alt = key;
    img.style.cssText = "max-width:120px;max-height:60px;object-fit:contain;border-radius:4px";
    img.onerror = function() { this.outerHTML = '<div class="img-placeholder">introuvable</div>'; };
    target.replaceWith(img);
  } else {
    const ph = document.createElement("div");
    ph.className = "img-placeholder";
    ph.textContent = "aucune";
    target.replaceWith(ph);
  }
}

async function portailUploadImage() {
  const fileInput = document.getElementById("pc-upload-fichier");
  const statut = document.getElementById("pc-upload-statut");
  if (!fileInput.files.length) { statut.textContent = "Choisissez un fichier"; return; }

  const form = new FormData();
  form.append("file", fileInput.files[0]);

  statut.textContent = "Upload en cours…";
  try {
    const resp = await fetch("/api/portail/upload", {method: "POST", body: form});
    const data = await resp.json();
    if (data.ok) {
      statut.textContent = "✓ " + data.chemin;
      fileInput.value = "";
      await _pcChargerImagesServeur();
    } else {
      statut.textContent = "Erreur: " + (data.erreur || "inconnue");
    }
  } catch(e) {
    statut.textContent = "Erreur réseau: " + e.message;
  }
}

async function _pcChargerImagesServeur() {
  const {ok, data} = await apiFetch("/api/portail/images");
  if (ok && data?.images) _pcImagesServeur = data.images;
  const div = document.getElementById("pc-images-liste");
  if (!div) return;
  if (!_pcImagesServeur.length) { div.textContent = "aucun fichier"; return; }
  div.textContent = _pcImagesServeur.join(", ");
}

/* --- Lecture du formulaire --- */
function _pcLireForm() {
  const g = id => document.getElementById(id)?.value || "";
  const socials = [];
  document.querySelectorAll("#pc-social-liste .portail-social").forEach(row => {
    const label = row.querySelector(".social-label")?.value?.trim();
    const url = row.querySelector(".social-url")?.value?.trim();
    if (label && url) socials.push({label, url});
  });

  const couleurs = {};
  _PC_VARS.forEach(v => {
    const el = document.getElementById("pc-color-" + v.id);
    if (el) couleurs[v.id] = el.value;
  });

  const images = {};
  _PC_IMAGES.forEach(cfg => {
    const input = document.querySelector(`.portail-image-path[data-key="${cfg.key}"]`);
    if (input) images[cfg.key] = input.value;
  });

  return {
    identite: { projectName: g("pc-projectName"), teamName: g("pc-teamName") },
    textes: {
      hero_titre1: g("pc-hero_titre1"), hero_titre2: g("pc-hero_titre2"),
      projects_titre1: g("pc-projects_titre1"), projects_titre2: g("pc-projects_titre2"),
      projects_description: g("pc-projects_description"),
      features_titre1: g("pc-features_titre1"), features_titre2: g("pc-features_titre2"),
      features_description: g("pc-features_description"),
    },
    liens: { contact: g("pc-contact"), docRudi: g("pc-docRudi"), footer_url: g("pc-footer_url") },
    reseaux_sociaux: socials,
    carte: { center_lon: g("pc-center_lon"), center_lat: g("pc-center_lat"), zoom: g("pc-zoom") },
    images,
    couleurs,
  };
}

/* --- Sauvegarde --- */
async function portailSauvegarder() {
  const statut = document.getElementById("pc-statut");
  const config = _pcLireForm();

  statut.innerHTML = '<span class="badge running">Sauvegarde…</span>';
  try {
    const resp = await fetch("/api/portail/config/save", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(config),
    });
    const data = await resp.json();
    if (data.ok) {
      statut.innerHTML = '<span class="badge-ok">✓ Fichiers écrits</span> '
        + '<button class="success" onclick="portailAppliquer()" style="font-size:.82rem">Redémarrer Konsult pour appliquer</button>';
      _pcConfig = config;
      _pcMajPreview();
    } else {
      statut.innerHTML = '<span class="badge-err">Erreur: ' + esc(data.erreur || "inconnue") + '</span>';
    }
  } catch(e) {
    statut.innerHTML = '<span class="badge-err">Erreur réseau</span>';
  }
}

async function portailAppliquer() {
  const statut = document.getElementById("pc-statut");
  const ok = await confirmerModal({
    titre: "Redémarrer Konsult",
    message: "Redémarrer le microservice Konsult pour charger la nouvelle config ? Le portail sera indisponible quelques secondes.",
  });
  if (!ok) return;
  statut.innerHTML = '<span class="badge running">Redémarrage Konsult…</span>';
  const {ok: success, data} = await apiFetch("/api/portail/konsult/restart", {method: "POST"});
  if (success) {
    statut.innerHTML = '<span class="badge-ok">✓ Appliqué — le portail utilise la nouvelle config</span>';
  } else {
    statut.innerHTML = '<span class="badge-err">Erreur: ' + esc(data?.message || "inconnue") + '</span>';
  }
}

/* --- Recharger (relit les fichiers disque) --- */
function portailRecharger() { portailInit(); }

document.addEventListener("DOMContentLoaded", portailInit);
