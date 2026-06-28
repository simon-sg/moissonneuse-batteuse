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
from datetime import datetime, timezone

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(RACINE, "data")
DECOUVERTE = os.path.join(DATA, "decouverte.json")
SORTIE_JSON = os.path.join(DATA, "catalogue.json")
SORTIE_HTML = os.path.join(DATA, "catalogue.html")

# Fichiers/dossiers à ignorer lors du parcours
IGNORER = {"cache"}
# Fichiers de service présents dans les dossiers mais qui ne sont pas des ressources de données
NON_RESSOURCES = {"rudi_metadata.json"}


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
        if fmt == "json":
            with open(chemin, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return len(data)
            if isinstance(data, dict):
                return len(data)
    except (OSError, ValueError):
        return None
    return None


def _ressources_disque(dossier: str) -> list[dict]:
    """Liste les fichiers de données présents dans un dossier de JDD."""
    chemin_dossier = os.path.join(DATA, dossier)
    ressources = []
    for nom in sorted(os.listdir(chemin_dossier)):
        if nom in NON_RESSOURCES or nom.endswith("_viewer.html"):
            continue
        chemin = os.path.join(chemin_dossier, nom)
        if not os.path.isfile(chemin):
            continue
        ext = nom.rsplit(".", 1)[-1].lower() if "." in nom else ""
        entry = {
            "nom": nom,
            "format": ext,
            "taille_octets": os.path.getsize(chemin),
            "nb_lignes": _compter_lignes(chemin, ext),
            "chemin": f"{dossier}/{nom}",
        }
        if ext == "csv":
            nom_base = nom.rsplit(".", 1)[0]
            entry["viewer"] = f"{dossier}/{nom_base}_viewer.html"
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
    return `
    <tr><td>${esc(r.nom)}</td><td>${esc(r.format||"")}</td>
        <td>${r.nb_lignes==null?"—":r.nb_lignes.toLocaleString("fr")}</td>
        <td>${octets(r.taille_octets)}</td>
        <td><a href="${esc(r.chemin)}">ouvrir</a>${voir}</td></tr>`;
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
table{border-collapse:collapse;font-size:.82rem;table-layout:fixed;width:100%}
thead{position:sticky;top:0;z-index:2;background:#fff;box-shadow:0 1px 0 #e2e6ea}
th{padding:8px 12px;text-align:left;cursor:pointer;user-select:none;color:#667;font-weight:600;
   white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
th:hover{background:#f5f6f8;color:#1c2733}
th.asc::after{content:" ↑";color:#0b6e99}
th.desc::after{content:" ↓";color:#0b6e99}
td{padding:5px 12px;border-bottom:1px solid #f0f2f4;white-space:nowrap;
   overflow:hidden;text-overflow:ellipsis}
tr:nth-child(even) td{background:#fafbfc}
tr:hover td{background:#eef6fb}
</style>
</head>
<body>
<header>
  <h1 id="titre"></h1>
  <input id="filtre" type="search" placeholder="Filtrer toutes les colonnes…">
  <span id="info"></span>
</header>
<div id="avert" class="avert" style="display:none"></div>
<div class="wrap"><table id="t"></table></div>
<script id="d" type="application/json">/*__DATA__*/</script>
<script>
const D=JSON.parse(document.getElementById("d").textContent);
document.title=D.nom;document.getElementById("titre").textContent=D.nom;
if(D.tronque){const a=document.getElementById("avert");a.style.display="";
  a.textContent=`Prévisualisation : ${D.lignes.length.toLocaleString("fr")} premières lignes sur ${D.nb_total.toLocaleString("fr")} au total.`;}
let sc=-1,asc=true,q="";
function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function rendu(){
  let rows=D.lignes;
  if(q){const f=q.toLowerCase();rows=rows.filter(r=>r.some(v=>String(v??"").toLowerCase().includes(f)));}
  if(sc>=0){const col=sc,up=asc;rows=[...rows].sort((a,b)=>{
    const va=a[col]??"",vb=b[col]??"",na=+va,nb=+vb;
    return(!isNaN(na)&&!isNaN(nb))?(up?na-nb:nb-na):(up?String(va).localeCompare(String(vb),"fr"):String(vb).localeCompare(String(va),"fr"));
  });}
  const th=D.entetes.map((h,i)=>`<th class="${sc===i?(asc?"asc":"desc"):""}" onclick="tri(${i})">${esc(h)}</th>`).join("");
  const td=rows.map(r=>`<tr>${r.map(v=>`<td title="${esc(v)}">${esc(v)}</td>`).join("")}</tr>`).join("");
  document.getElementById("t").innerHTML=`<thead><tr>${th}</tr></thead><tbody>${td}</tbody>`;
  const n=rows.length,tot=D.lignes.length;
  document.getElementById("info").textContent=n<tot
    ?`${n.toLocaleString("fr")} / ${tot.toLocaleString("fr")} lignes`
    :`${n.toLocaleString("fr")} ligne${n>1?"s":""}`;
}
function tri(i){sc===i?asc=!asc:(sc=i,asc=true);rendu();}
document.getElementById("filtre").addEventListener("input",e=>{q=e.target.value;rendu();});
rendu();
</script>
</body>
</html>
"""


def _ecrire_viewer(chemin: str, nom: str, apercu: dict) -> None:
    data = json.dumps({"nom": nom, **apercu}, ensure_ascii=False).replace("</", r"<\/")
    html = GABARIT_VIEWER.replace("/*__DATA__*/", data)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(html)


def ecrire_viewers(catalogue: dict) -> int:
    """Génère un fichier *_viewer.html pour chaque ressource CSV du catalogue."""
    nb = 0
    for jeu in catalogue["jeux"]:
        for res in jeu["ressources"]:
            if res.get("format") != "csv" or not res.get("viewer"):
                continue
            chemin_csv = os.path.join(DATA, res["chemin"])
            chemin_viewer = os.path.join(DATA, res["viewer"])
            apercu = _apercu_csv(chemin_csv)
            if apercu:
                _ecrire_viewer(chemin_viewer, res["nom"], apercu)
                nb += 1
    return nb


def main() -> None:
    catalogue = construire_catalogue()
    ecrire_json(catalogue)
    ecrire_html(catalogue)
    nb_v = ecrire_viewers(catalogue)
    print(f"{catalogue['nb_jeux']} jeux de données catalogués.")
    print(f"  → {os.path.relpath(SORTIE_JSON, RACINE)}")
    print(f"  → {os.path.relpath(SORTIE_HTML, RACINE)}")
    if nb_v:
        print(f"  → {nb_v} visionneuse(s) CSV générée(s)")


if __name__ == "__main__":
    main()
