"""
Génère un catalogue des jeux de données (JDD) moissonnés.

Parcourt les sous-dossiers de data/, lit les métadonnées RUDI (rudi_metadata.json)
quand elles existent, complète avec l'état de découverte (data/decouverte.json) et
les fichiers filtrés présents sur le disque, puis écrit :

  - data/catalogue.json  : catalogue lisible par une machine
  - data/catalogue.html  : page autonome (JSON embarqué) avec recherche

Usage :
    python3 src/catalogue.py
"""

import csv
import json
import os
import re
from datetime import datetime, timezone

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(RACINE, "data")
DECOUVERTE = os.path.join(DATA, "decouverte.json")
SORTIE_JSON = os.path.join(DATA, "catalogue.json")
SORTIE_HTML = os.path.join(DATA, "catalogue.html")

# Fichiers/dossiers à ignorer lors du parcours
IGNORER = {"cache"}
# Fichiers de service présents dans les dossiers mais qui ne sont pas des ressources de données
NON_RESSOURCES = {"rudi_metadata.json", "wms_service.json"}


def _charger_candidats() -> dict:
    """Indexe les candidats de decouverte.json par dataset_id (titre, champs géo, nb de lignes RM)."""
    if not os.path.exists(DECOUVERTE):
        return {}
    with open(DECOUVERTE, encoding="utf-8") as f:
        d = json.load(f)
    return {c["dataset_id"]: c for c in d.get("candidats", [])}


def _apercu_csv(chemin: str, max_lignes: int = 5000) -> dict | None:
    """Lit les premières lignes d'un CSV pour la visionneuse interactive."""
    try:
        with open(chemin, "rb") as f:
            raw = f.read()
        texte = raw.decode("utf-8-sig", errors="replace")
        if texte.count("�") > 10:
            texte = raw.decode("latin-1")
        try:
            dialect = csv.Sniffer().sniff(texte[:4096], delimiters=";,\t|")
            delim = dialect.delimiter
        except csv.Error:
            delim = ","
        reader = csv.reader(texte.splitlines(), delimiter=delim)
        entetes = [h[:100] for h in (next(reader, None) or [])]
        if not entetes:
            return None
        lignes, nb_total = [], 0
        for row in reader:
            nb_total += 1
            if nb_total <= max_lignes:
                lignes.append([v[:200] for v in row])
        return {"entetes": entetes, "lignes": lignes, "tronque": nb_total > max_lignes, "nb_total": nb_total}
    except OSError:
        return None


def _compter_lignes(chemin: str, fmt: str) -> int | None:
    """Compte les enregistrements d'un fichier filtré (lignes CSV hors en-tête, ou éléments JSON)."""
    try:
        if fmt == "csv":
            with open(chemin, encoding="utf-8", errors="replace") as f:
                n = sum(1 for _ in f)
            return max(n - 1, 0)  # on retire la ligne d'en-tête
        if fmt in ("json", "geojson"):
            with open(chemin, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return len(data)
            if isinstance(data, dict):
                features = data.get("features")
                if features is not None:
                    return len(features)
                return len(data)
    except (OSError, ValueError):
        return None
    return None


_RE_LAT = re.compile(r"(^|[^a-z])(lat(itude)?)([^a-z]|$)", re.IGNORECASE)
_RE_LON = re.compile(r"(^|[^a-z])(lon(gitude)?|lng|long)([^a-z]|$)", re.IGNORECASE)


def _deviner_lat_lon(entetes: list[str]) -> tuple[str | None, str | None]:
    """Détecte les colonnes latitude et longitude dans les en-têtes d'un CSV.
    Prend la première colonne qui correspond pour chaque axe (évite les mélanges
    départ/arrivée quand les deux sont présentes, ex: covoiturage)."""
    champ_lat, champ_lon = None, None
    for e in entetes:
        if champ_lat is None and _RE_LAT.search(e):
            champ_lat = e
        if champ_lon is None and _RE_LON.search(e):
            champ_lon = e
        if champ_lat and champ_lon:
            break
    return champ_lat, champ_lon


def _apercu_geo_csv(chemin: str, max_points: int = 5000) -> dict | None:
    """Extrait les points géographiques d'un CSV (lat/lon) pour la carte Leaflet."""
    try:
        with open(chemin, "rb") as f:
            raw = f.read()
        texte = raw.decode("utf-8-sig", errors="replace")
        if texte.count("�") > 10:
            texte = raw.decode("latin-1")
        try:
            dialect = csv.Sniffer().sniff(texte[:4096], delimiters=";,\t|")
            delim = dialect.delimiter
        except csv.Error:
            delim = ","
        reader = csv.DictReader(texte.splitlines(), delimiter=delim)
        entetes = list(reader.fieldnames or [])
        champ_lat, champ_lon = _deviner_lat_lon(entetes)
        if not champ_lat or not champ_lon:
            return None
        points, nb_total = [], 0
        for row in reader:
            nb_total += 1
            lat_s = (row.get(champ_lat) or "").replace(",", ".").strip()
            lon_s = (row.get(champ_lon) or "").replace(",", ".").strip()
            try:
                lat, lon = float(lat_s), float(lon_s)
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
            except (ValueError, TypeError):
                continue
            props = {k: v for k, v in row.items()
                     if k not in (champ_lat, champ_lon) and v not in (None, "")}
            if len(points) < max_points:
                points.append([lat, lon, props])
        return {"type": "points", "points": points,
                "nb_total": nb_total, "tronque": nb_total > max_points} if points else None
    except OSError:
        return None


def _apercu_geojson(chemin: str, max_features: int = 5000) -> dict | None:
    """Charge un GeoJSON pour intégration dans la carte Leaflet."""
    try:
        with open(chemin, encoding="utf-8") as f:
            data = json.load(f)
        features = data.get("features", [])
        nb_total = len(features)
        if nb_total > max_features:
            data = {**data, "features": features[:max_features]}
        return {"type": "geojson", "geojson": data,
                "nb_total": nb_total, "tronque": nb_total > max_features}
    except (OSError, ValueError):
        return None


def _ressources_disque(dossier: str) -> list[dict]:
    """Liste les fichiers de données présents dans un dossier de JDD."""
    chemin_dossier = os.path.join(DATA, dossier)
    ressources = []
    for nom in sorted(os.listdir(chemin_dossier)):
        if nom in NON_RESSOURCES or nom.endswith("_viewer.html") or nom.endswith("_map.html"):
            continue
        chemin = os.path.join(chemin_dossier, nom)
        if not os.path.isfile(chemin):
            continue
        ext = nom.rsplit(".", 1)[-1].lower() if "." in nom else ""
        nom_base = nom.rsplit(".", 1)[0] if "." in nom else nom
        entry = {
            "nom": nom,
            "format": ext,
            "taille_octets": os.path.getsize(chemin),
            "nb_lignes": _compter_lignes(chemin, ext),
            "chemin": f"{dossier}/{nom}",
        }
        if ext == "csv":
            entry["viewer"] = f"{dossier}/{nom_base}_viewer.html"
        if ext == "geojson":
            entry["map"] = f"{dossier}/{nom_base}_map.html"
        ressources.append(entry)
    return ressources


def _source_datagouv(meta: dict, dataset_id: str) -> str:
    """URL du JDD source sur data.gouv.fr."""
    src = (meta.get("metadata_info") or {}).get("metadata_source")
    if src:
        return src
    return f"https://www.data.gouv.fr/datasets/{dataset_id}"


def _champs_geo(cand: dict) -> dict:
    """Champs géographiques utilisés pour le filtrage Rennes Métropole."""
    champs = {}
    for cle, libelle in (("champ_ville", "ville"), ("champ_cp", "cp"),
                         ("champ_iris", "iris"), ("champ_adresse", "adresse")):
        valeur = cand.get(cle)
        if valeur:
            champs[libelle] = valeur
    return champs


def construire_catalogue() -> dict:
    candidats = _charger_candidats()
    jeux = []

    for dossier in sorted(os.listdir(DATA)):
        chemin_dossier = os.path.join(DATA, dossier)
        if not os.path.isdir(chemin_dossier) or dossier in IGNORER:
            continue

        ressources = _ressources_disque(dossier)
        # Détecte wms_service.json → ajoute une ressource virtuelle WMS
        chemin_wms = os.path.join(chemin_dossier, "wms_service.json")
        if os.path.exists(chemin_wms):
            try:
                with open(chemin_wms, encoding="utf-8") as f:
                    wms_data = json.load(f)
                ressources.append({
                    "nom": wms_data.get("titre_service", "Service WMS"),
                    "format": "wms",
                    "taille_octets": None,
                    "nb_lignes": None,
                    "chemin": None,
                    "map": f"{dossier}/wms_map.html",
                })
            except (OSError, ValueError):
                pass
        # Détecte les CSVs avec colonnes lat/lon pour la carte
        for res in ressources:
            if res.get("map") or res.get("format") != "csv":
                continue
            chemin_res = os.path.join(DATA, res["chemin"])
            try:
                with open(chemin_res, "rb") as _f:
                    _premiere = _f.readline().decode("utf-8-sig", errors="replace")
                _entetes = next(csv.reader([_premiere]))
                if all(_deviner_lat_lon(_entetes)):
                    nom_base = res["nom"].rsplit(".", 1)[0]
                    res["map"] = f"{dossier}/{nom_base}_map.html"
            except OSError:
                pass
        chemin_meta = os.path.join(chemin_dossier, "rudi_metadata.json")
        meta = {}
        if os.path.exists(chemin_meta):
            with open(chemin_meta, encoding="utf-8") as f:
                meta = json.load(f)

        # On ignore les dossiers vides (échecs de moisson, ni données ni métadonnées)
        if not ressources and not meta:
            continue

        cand = candidats.get(dossier, {})

        titre = meta.get("resource_title") or cand.get("titre") or dossier
        synopsis = ""
        if meta.get("synopsis"):
            synopsis = meta["synopsis"][0].get("text", "")

        producteur = (meta.get("producer") or {}).get("organization_name", "")
        licence = ((meta.get("access_condition") or {}).get("licence") or {}).get("licence_label", "")
        date_maj = (meta.get("dataset_dates") or {}).get("updated", "")

        jeux.append({
            "dataset_id": dossier,
            "titre": titre,
            "producteur": producteur,
            "theme": meta.get("theme", ""),
            "synopsis": synopsis,
            "mots_cles": meta.get("keywords", []),
            "licence": licence,
            "date_maj": date_maj,
            "source_datagouv": _source_datagouv(meta, dossier),
            "nb_lignes_rm": cand.get("nb_rm"),
            "champs_geo": _champs_geo(cand),
            "ressources": ressources,
            "complet": bool(meta),
        })

    jeux.sort(key=lambda j: j["titre"].lower())
    return {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nb_jeux": len(jeux),
        "jeux": jeux,
    }


def ecrire_json(catalogue: dict) -> None:
    with open(SORTIE_JSON, "w", encoding="utf-8") as f:
        json.dump(catalogue, f, ensure_ascii=False, indent=2)


def ecrire_html(catalogue: dict) -> None:
    data_json = json.dumps(catalogue, ensure_ascii=False)
    html = GABARIT_HTML.replace("/*__DONNEES__*/", data_json)
    with open(SORTIE_HTML, "w", encoding="utf-8") as f:
        f.write(html)


GABARIT_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Catalogue des jeux de données — Rennes Métropole</title>
<style>
  :root { --bg:#f5f6f8; --card:#fff; --txt:#1c2733; --muted:#667; --accent:#0b6e99; --bord:#e2e6ea; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background:var(--bg); color:var(--txt); line-height:1.45; }
  header { background:var(--card); border-bottom:1px solid var(--bord); padding:18px 24px;
           position:sticky; top:0; z-index:5; }
  h1 { margin:0 0 4px; font-size:1.3rem; }
  .meta { color:var(--muted); font-size:.85rem; }
  .barre { margin-top:12px; display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  #recherche { flex:1; min-width:240px; padding:10px 12px; font-size:1rem;
               border:1px solid var(--bord); border-radius:8px; }
  #compteur { color:var(--muted); font-size:.85rem; white-space:nowrap; }
  main { max-width:1000px; margin:0 auto; padding:20px 24px 60px; }
  .jeu { background:var(--card); border:1px solid var(--bord); border-radius:10px;
         padding:16px 18px; margin-bottom:14px; }
  .jeu h2 { margin:0 0 6px; font-size:1.05rem; }
  .jeu h2 a { color:var(--accent); text-decoration:none; }
  .jeu h2 a:hover { text-decoration:underline; }
  .infos { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:.82rem; color:var(--muted); margin-bottom:8px; }
  .infos b { color:var(--txt); font-weight:600; }
  .synopsis { font-size:.9rem; margin:8px 0; }
  .tags { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; }
  .tag { background:#eef3f6; color:#345; border-radius:99px; padding:2px 10px; font-size:.75rem; }
  .badge { display:inline-block; background:#fdecea; color:#a3372c; border-radius:99px;
           padding:2px 10px; font-size:.72rem; font-weight:600; }
  details summary { cursor:pointer; font-size:.85rem; color:var(--accent); user-select:none; }
  table.res { width:100%; border-collapse:collapse; margin-top:10px; font-size:.82rem; }
  table.res th, table.res td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--bord); }
  table.res th { color:var(--muted); font-weight:600; }
  table.res code { background:#f0f2f4; padding:1px 5px; border-radius:4px; }
  .vide { text-align:center; color:var(--muted); padding:40px; }
</style>
</head>
<body>
<header>
  <h1>Catalogue des jeux de données — Rennes Métropole</h1>
  <div class="meta" id="entete"></div>
  <div class="barre">
    <input id="recherche" type="search" placeholder="Rechercher (titre, producteur, mot-clé, identifiant…)" autofocus>
    <span id="compteur"></span>
  </div>
</header>
<main id="liste"></main>

<script id="donnees" type="application/json">/*__DONNEES__*/</script>
<script>
const CAT = JSON.parse(document.getElementById("donnees").textContent);
const liste = document.getElementById("liste");
const compteur = document.getElementById("compteur");
document.getElementById("entete").textContent =
  CAT.nb_jeux + " jeux de données moissonnés · généré le " +
  (CAT.genere_le || "").replace("T", " ").replace("+00:00", " UTC");

function octets(n){
  if (n == null) return "—";
  const u = ["o","Ko","Mo","Go"]; let i=0;
  while (n >= 1024 && i < u.length-1){ n/=1024; i++; }
  return (i ? n.toFixed(1) : n) + " " + u[i];
}
function esc(s){ return (s??"").toString().replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c])); }

function texteRecherche(j){
  return [j.titre, j.producteur, j.dataset_id, j.theme, (j.mots_cles||[]).join(" "),
          j.synopsis].join(" ").toLowerCase();
}
CAT.jeux.forEach(j => j._t = texteRecherche(j));

function carte(j){
  const tags = (j.mots_cles||[]).slice(0,12).map(m => `<span class="tag">${esc(m)}</span>`).join("");
  const champs = Object.entries(j.champs_geo||{}).map(([k,v]) => `${k}=<code>${esc(v)}</code>`).join(", ");
  const res = (j.ressources||[]).map(r => {
    const voir = r.viewer ? ` <a href="${esc(r.viewer)}" target="_blank" rel="noopener">voir</a>` : "";
    const carte = r.map ? ` <a href="${esc(r.map)}" target="_blank" rel="noopener">carte</a>` : "";
    const ouvrir = r.chemin ? `<a href="${esc(r.chemin)}">${r.format==="wms"?"service":"ouvrir"}</a>` : "";
    return `
    <tr><td>${esc(r.nom)}</td><td>${esc(r.format||"")}</td>
        <td>${r.nb_lignes==null?"—":r.nb_lignes.toLocaleString("fr")}</td>
        <td>${octets(r.taille_octets)}</td>
        <td>${ouvrir}${voir}${carte}</td></tr>`;
  }).join("");
  return `
  <article class="jeu">
    <h2><a href="${esc(j.source_datagouv)}" target="_blank" rel="noopener">${esc(j.titre)}</a></h2>
    <div class="infos">
      ${j.producteur?`<span><b>Producteur :</b> ${esc(j.producteur)}</span>`:""}
      ${j.licence?`<span><b>Licence :</b> ${esc(j.licence)}</span>`:""}
      ${j.date_maj?`<span><b>MàJ :</b> ${esc(j.date_maj.slice(0,10))}</span>`:""}
      ${j.nb_lignes_rm!=null?`<span><b>${j.nb_lignes_rm.toLocaleString("fr")}</b> lignes RM</span>`:""}
      ${champs?`<span><b>Filtre géo :</b> ${champs}</span>`:""}
      <span><code>${esc(j.dataset_id)}</code></span>
      ${j.complet?"":'<span class="badge">métadonnées partielles</span>'}
    </div>
    ${j.synopsis?`<div class="synopsis">${esc(j.synopsis)}</div>`:""}
    ${tags?`<div class="tags">${tags}</div>`:""}
    ${res?`<details><summary>${j.ressources.length} ressource(s)</summary>
      <table class="res"><tr><th>Fichier</th><th>Format</th><th>Lignes</th><th>Taille</th><th></th></tr>
      ${res}</table></details>`:""}
  </article>`;
}

function rendu(q){
  q = (q||"").trim().toLowerCase();
  const termes = q.split(/\s+/).filter(Boolean);
  const filtres = CAT.jeux.filter(j => termes.every(t => j._t.includes(t)));
  compteur.textContent = filtres.length + " / " + CAT.jeux.length;
  liste.innerHTML = filtres.length
    ? filtres.map(carte).join("")
    : '<div class="vide">Aucun résultat.</div>';
}

document.getElementById("recherche").addEventListener("input", e => rendu(e.target.value));
rendu("");
</script>
</body>
</html>
"""


GABARIT_WMS_MAP = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title></title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
     background:#f5f6f8;color:#1c2733;display:flex;flex-direction:column;height:100vh}
header{background:#fff;border-bottom:1px solid #e2e6ea;padding:8px 16px;
       display:flex;flex-wrap:wrap;gap:8px;align-items:center;flex-shrink:0}
h1{font-size:.9rem;font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#info{color:#667;font-size:.82rem;white-space:nowrap}
#carte{flex:1}
</style>
</head>
<body>
<header>
  <h1 id="titre"></h1>
  <span id="info"></span>
</header>
<div id="carte"></div>
<script id="d" type="application/json">/*__DATA__*/</script>
<script>
const D=JSON.parse(document.getElementById("d").textContent);
document.title=D.nom;
document.getElementById("titre").textContent=D.nom;
document.getElementById("info").textContent=D.couches.length+" couche(s) WMS";

const map=L.map("carte");
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",{
  attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  subdomains:"abcd",maxZoom:19
}).addTo(map);

D.couches.forEach(c=>{
  L.tileLayer.wms(D.url,{
    layers:c.nom,
    format:"image/png",
    transparent:true,
    opacity:0.75,
    attribution:D.producteur||c.titre||"Source WMS"
  }).addTo(map);
});

map.setView([48.1,-1.68],11);
</script>
</body>
</html>
"""


GABARIT_MAP = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title></title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
     background:#f5f6f8;color:#1c2733;display:flex;flex-direction:column;height:100vh}
header{background:#fff;border-bottom:1px solid #e2e6ea;padding:8px 16px;
       display:flex;flex-wrap:wrap;gap:8px;align-items:center;flex-shrink:0}
h1{font-size:.9rem;font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#info{color:#667;font-size:.82rem;white-space:nowrap}
.avert{background:#fff8e1;border-bottom:1px solid #ffe082;padding:5px 16px;
       font-size:.8rem;color:#6d4c00;flex-shrink:0}
#carte{flex:1}
.leaflet-popup-content{min-width:160px;max-width:320px;font-size:.8rem}
.leaflet-popup-content table{border-collapse:collapse;width:100%}
.leaflet-popup-content td{padding:3px 6px;vertical-align:top;border-bottom:1px solid #eee;word-break:break-word}
.leaflet-popup-content td:first-child{color:#667;font-weight:600;white-space:nowrap;padding-right:10px}
</style>
</head>
<body>
<header>
  <h1 id="titre"></h1>
  <span id="info"></span>
</header>
<div id="avert" class="avert" style="display:none"></div>
<div id="carte"></div>
<script id="d" type="application/json">/*__DATA__*/</script>
<script>
const D=JSON.parse(document.getElementById("d").textContent);
document.title=D.nom;
document.getElementById("titre").textContent=D.nom;
if(D.tronque){
  const a=document.getElementById("avert"),n=D.type==="points"?D.points.length:D.geojson.features.length;
  a.style.display="";
  a.textContent=`Carte : ${n.toLocaleString("fr")} premiers éléments affichés sur ${D.nb_total.toLocaleString("fr")} au total.`;
}

const map=L.map("carte");
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",{
  attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  subdomains:"abcd",maxZoom:19
}).addTo(map);

function mkPopup(props){
  if(!props||!Object.keys(props).length)return"";
  const rows=Object.entries(props).filter(([,v])=>v!=null&&v!=="").slice(0,30)
    .map(([k,v])=>`<tr><td>${k}</td><td>${String(v).slice(0,300)}</td></tr>`).join("");
  return`<table>${rows}</table>`;
}

const PT={radius:7,color:"#0b6e99",weight:1.5,fillColor:"#1a8bbf",fillOpacity:.75};
let layer;

if(D.type==="points"){
  layer=L.featureGroup(D.points.map(([lat,lon,props])=>
    L.circleMarker([lat,lon],PT).bindPopup(mkPopup(props))
  )).addTo(map);
  document.getElementById("info").textContent=D.points.length.toLocaleString("fr")+" point"+(D.points.length>1?"s":"");
}else{
  layer=L.geoJSON(D.geojson,{
    style:{color:"#0b6e99",weight:2,fillColor:"#1a8bbf",fillOpacity:.35},
    pointToLayer:(_,ll)=>L.circleMarker(ll,PT),
    onEachFeature:(f,lyr)=>{if(f.properties)lyr.bindPopup(mkPopup(f.properties));}
  }).addTo(map);
  const n=D.geojson.features.length;
  document.getElementById("info").textContent=n.toLocaleString("fr")+" feature"+(n>1?"s":"");
}

try{const b=layer.getBounds();if(b.isValid())map.fitBounds(b.pad(0.1));else map.setView([48.1,-1.68],11);}
catch{map.setView([48.1,-1.68],11);}
</script>
</body>
</html>
"""


GABARIT_VIEWER = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title></title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
     background:#f5f6f8;color:#1c2733;display:flex;flex-direction:column;height:100vh}
header{background:#fff;border-bottom:1px solid #e2e6ea;padding:10px 16px;
       display:flex;flex-wrap:wrap;gap:10px;align-items:center;flex-shrink:0}
h1{font-size:.9rem;font-weight:600;flex:1;min-width:150px;
   white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#filtre{padding:7px 11px;font-size:.9rem;border:1px solid #e2e6ea;
        border-radius:6px;width:220px}
#info{color:#667;font-size:.82rem;white-space:nowrap}
.avert{background:#fff8e1;border-bottom:1px solid #ffe082;padding:5px 16px;
       font-size:.8rem;color:#6d4c00;flex-shrink:0}
.wrap{flex:1;overflow:auto}
table{border-collapse:collapse;font-size:.82rem;table-layout:fixed}
thead{position:sticky;top:0;z-index:2;background:#fff;box-shadow:0 1px 0 #e2e6ea}
th{padding:8px 12px;text-align:left;cursor:pointer;user-select:none;color:#667;font-weight:600;
   white-space:nowrap;overflow:hidden;text-overflow:ellipsis;position:relative}
th:hover{background:#f5f6f8;color:#1c2733}
th.asc::after{content:" ↑";color:#0b6e99}
th.desc::after{content:" ↓";color:#0b6e99}
th.geo{color:#0b6e99;background:#eaf4fb}
th.geo:hover{background:#d4ecf7}
td{padding:0;border-bottom:1px solid #f0f2f4}
td div{padding:5px 12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
td div a{color:#0b6e99;text-decoration:none}
td div a:hover{text-decoration:underline}
tr:nth-child(even) td{background:#fafbfc}
tr:hover td{background:#eef6fb}
.resizer{position:absolute;right:0;top:0;width:5px;height:100%;cursor:col-resize;z-index:1}
.resizer:hover,.resizer.active{background:rgba(11,110,153,.35)}
</style>
</head>
<body>
<header>
  <h1 id="titre"></h1>
  <input id="filtre" type="search" placeholder="Filtrer toutes les colonnes…">
  <span id="info"></span>
</header>
<div id="avert" class="avert" style="display:none"></div>
<div class="wrap"><table id="t"><colgroup id="cols"></colgroup><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>
<script id="d" type="application/json">/*__DATA__*/</script>
<script>
const D=JSON.parse(document.getElementById("d").textContent);
const tbl=document.getElementById("t"),wrap=document.querySelector(".wrap");
const nCols=D.entetes.length;
const wrapW=wrap.clientWidth||800;
const colW=Math.max(80,Math.floor(Math.max(wrapW,nCols*160)/nCols));
tbl.style.width=Math.max(wrapW,nCols*160)+"px";
document.getElementById("cols").innerHTML=D.entetes.map(()=>`<col style="width:${colW}px">`).join("");
document.title=D.nom;document.getElementById("titre").textContent=D.nom;
if(D.tronque){const a=document.getElementById("avert");a.style.display="";
  a.textContent=`Prévisualisation : ${D.lignes.length.toLocaleString("fr")} premières lignes sur ${D.nb_total.toLocaleString("fr")} au total.`;}
const geo=new Set(D.champs_geo||[]);
let sc=-1,asc=true,q="";
function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function cell(v){const s=String(v??"").trim();return /^https?:\/\/\S+$/.test(s)?`<a href="${esc(s)}" target="_blank" rel="noopener">${esc(s)}</a>`:esc(v);}
function rendu(){
  let rows=D.lignes;
  if(q){const f=q.toLowerCase();rows=rows.filter(r=>r.some(v=>String(v??"").toLowerCase().includes(f)));}
  if(sc>=0){const col=sc,up=asc;rows=[...rows].sort((a,b)=>{
    const va=a[col]??"",vb=b[col]??"",na=+va,nb=+vb;
    return(!isNaN(na)&&!isNaN(nb))?(up?na-nb:nb-na):(up?String(va).localeCompare(String(vb),"fr"):String(vb).localeCompare(String(va),"fr"));
  });}
  const th=D.entetes.map((h,i)=>{const cls=[sc===i?(asc?"asc":"desc"):"",geo.has(h)?"geo":""].filter(Boolean).join(" ");return`<th class="${cls}">${esc(h)}<div class="resizer"></div></th>`;}).join("");
  document.getElementById("thead").innerHTML=`<tr>${th}</tr>`;
  document.getElementById("tbody").innerHTML=rows.map(r=>`<tr>${r.map(v=>`<td><div title="${esc(v)}">${cell(v)}</div></td>`).join("")}</tr>`).join("");
  const n=rows.length,tot=D.lignes.length;
  document.getElementById("info").textContent=n<tot
    ?`${n.toLocaleString("fr")} / ${tot.toLocaleString("fr")} lignes`
    :`${n.toLocaleString("fr")} ligne${n>1?"s":""}`;
}
// Sort via delegation — ignore clicks that start on the resizer handle
tbl.addEventListener("click",e=>{
  if(e.target.closest(".resizer"))return;
  const th=e.target.closest("th");
  if(!th)return;
  const i=th.cellIndex;sc===i?asc=!asc:(sc=i,asc=true);rendu();
});
// Column resize
tbl.addEventListener("mousedown",e=>{
  const rz=e.target.closest(".resizer");
  if(!rz)return;
  e.preventDefault();
  const th=rz.parentElement,idx=th.cellIndex;
  const cols=document.querySelectorAll("#cols col");
  const startX=e.clientX,startW=th.offsetWidth;
  rz.classList.add("active");
  document.body.style.cursor="col-resize";
  function onMove(e){
    const w=Math.max(40,startW+(e.clientX-startX));
    cols[idx].style.width=w+"px";
    tbl.style.width=Array.from(cols).reduce((s,c)=>s+(parseInt(c.style.width)||colW),0)+"px";
  }
  function onUp(){
    rz.classList.remove("active");
    document.body.style.cursor="";
    document.removeEventListener("mousemove",onMove);
    document.removeEventListener("mouseup",onUp);
  }
  document.addEventListener("mousemove",onMove);
  document.addEventListener("mouseup",onUp);
});
document.getElementById("filtre").addEventListener("input",e=>{q=e.target.value;rendu();});
rendu();
</script>
</body>
</html>
"""


def _ecrire_viewer(chemin: str, nom: str, apercu: dict, champs_geo: list | None = None) -> None:
    data = json.dumps({"nom": nom, "champs_geo": champs_geo or [], **apercu}, ensure_ascii=False).replace("</", r"<\/")
    html = GABARIT_VIEWER.replace("/*__DATA__*/", data)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(html)


def _ecrire_wms_map(chemin: str, nom: str, wms_data: dict, producteur: str = "") -> None:
    data = json.dumps({
        "nom": nom,
        "url": wms_data.get("url", ""),
        "couches": wms_data.get("couches", []),
        "producteur": producteur or wms_data.get("producteur", ""),
    }, ensure_ascii=False).replace("</", r"<\/")
    html = GABARIT_WMS_MAP.replace("/*__DATA__*/", data)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(html)


def _ecrire_map(chemin: str, nom: str, apercu: dict) -> None:
    data = json.dumps({"nom": nom, **apercu}, ensure_ascii=False).replace("</", r"<\/")
    html = GABARIT_MAP.replace("/*__DATA__*/", data)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(html)


def ecrire_viewers(catalogue: dict) -> tuple[int, int]:
    """Génère les fichiers *_viewer.html (CSV) et *_map.html (CSV+GeoJSON) du catalogue."""
    nb_v, nb_m = 0, 0
    for jeu in catalogue["jeux"]:
        champs_geo = list(jeu.get("champs_geo", {}).values())
        for res in jeu["ressources"]:
            fmt = res.get("format")
            if res.get("viewer") and fmt == "csv":
                chemin_csv = os.path.join(DATA, res["chemin"])
                chemin_viewer = os.path.join(DATA, res["viewer"])
                apercu = _apercu_csv(chemin_csv)
                if apercu:
                    _ecrire_viewer(chemin_viewer, res["nom"], apercu, champs_geo)
                    nb_v += 1
            if res.get("map"):
                chemin_map = os.path.join(DATA, res["map"])
                if fmt == "wms":
                    chemin_wms_json = os.path.join(DATA, jeu["dataset_id"], "wms_service.json")
                    try:
                        with open(chemin_wms_json, encoding="utf-8") as f:
                            wms_data = json.load(f)
                        _ecrire_wms_map(chemin_map, res["nom"], wms_data,
                                        jeu.get("producteur", ""))
                        nb_m += 1
                    except (OSError, ValueError):
                        pass
                elif res.get("chemin"):
                    chemin_src = os.path.join(DATA, res["chemin"])
                    if fmt == "geojson":
                        apercu_geo = _apercu_geojson(chemin_src)
                    else:
                        apercu_geo = _apercu_geo_csv(chemin_src)
                    if apercu_geo:
                        _ecrire_map(chemin_map, res["nom"], apercu_geo)
                        nb_m += 1
    return nb_v, nb_m


def main() -> None:
    catalogue = construire_catalogue()
    ecrire_json(catalogue)
    ecrire_html(catalogue)
    nb_v, nb_m = ecrire_viewers(catalogue)
    print(f"{catalogue['nb_jeux']} jeux de données catalogués.")
    print(f"  → {os.path.relpath(SORTIE_JSON, RACINE)}")
    print(f"  → {os.path.relpath(SORTIE_HTML, RACINE)}")
    if nb_v:
        print(f"  → {nb_v} visionneuse(s) CSV générée(s)")
    if nb_m:
        print(f"  → {nb_m} carte(s) géographique(s) générée(s)")


if __name__ == "__main__":
    main()
