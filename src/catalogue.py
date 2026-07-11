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

from filters.geographic import normaliser
from translation.description_secours import partie_descriptive

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(RACINE, "data")
DECOUVERTE = os.path.join(DATA, "decouverte.json")
SORTIE_JSON = os.path.join(DATA, "catalogue.json")
SORTIE_HTML = os.path.join(DATA, "catalogue.html")

# Fichiers/dossiers à ignorer lors du parcours
IGNORER = {"cache"}
# Fichiers de service présents dans les dossiers mais qui ne sont pas des ressources de données
NON_RESSOURCES = {"rudi_metadata.json", "wms_service.json"}

def _extraire_description(meta: dict) -> str:
    """Extrait la partie descriptive du résumé RUDI (`summary`), après le préambule de
    localisation standard ('Version localisée sur...', 'Source : URL', etc.)."""
    if not meta.get("summary"):
        return ""
    return partie_descriptive(meta["summary"][0].get("text", ""))


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


# Dictionnaires de colonnes : formats reconnus —
#  - "Colonne,Description" (Ecolab et variantes avec Type/Description_FR/Description_EN/Exemple)
#  - "COD_VAR;LIB_VAR[;LIB_VAR_LONG];COD_MOD;LIB_MOD;..." (varmod INSEE)
_COLS_CODE = {"colonne", "column", "champ", "field", "variable", "nom", "libelle"}


def _entetes_rapides(chemin: str, taille: int = 8192) -> tuple[list[str], str]:
    """Lit le début d'un CSV (sans tout charger) pour détecter délimiteur + en-têtes."""
    with open(chemin, "rb") as f:
        echantillon = f.read(taille)
    texte = echantillon.decode("utf-8-sig", errors="replace")
    try:
        delim = csv.Sniffer().sniff(texte, delimiters=";,\t|").delimiter
    except csv.Error:
        delim = ","
    premiere_ligne = texte.splitlines()[0] if texte else ""
    entetes = next(csv.reader([premiere_ligne], delimiter=delim), [])
    return entetes, delim


def _format_dictionnaire(entetes: list[str]) -> str | None:
    """Détecte si des en-têtes correspondent à un fichier dictionnaire connu."""
    if not entetes:
        return None
    if normaliser(entetes[0]).strip() in _COLS_CODE:
        return "colonne"
    if any(normaliser(e).strip() == "cod var" for e in entetes):
        return "cod_var"
    return None


def _charger_dictionnaire(chemin: str) -> dict[str, str]:
    """Parse un fichier dictionnaire en correspondance {nom_colonne: description}.
    Retourne {} si le fichier n'a pas un format de dictionnaire reconnu."""
    try:
        entetes, delim = _entetes_rapides(chemin)
        fmt = _format_dictionnaire(entetes)
        if fmt is None:
            return {}
        with open(chemin, "rb") as f:
            raw = f.read()
        texte = raw.decode("utf-8-sig", errors="replace")
        if texte.count("�") > 10:
            texte = raw.decode("latin-1")
        reader = csv.DictReader(texte.splitlines(), delimiter=delim)
        mapping: dict[str, str] = {}
        if fmt == "colonne":
            col_nom = entetes[0]
            col_desc = (next((e for e in entetes if normaliser(e).strip() == "description fr"), None)
                        or next((e for e in entetes if "description" in normaliser(e)), None)
                        or (entetes[1] if len(entetes) > 1 else None))
            if not col_desc:
                return {}
            for row in reader:
                nom, desc = (row.get(col_nom) or "").strip(), (row.get(col_desc) or "").strip()
                if nom and desc:
                    mapping.setdefault(nom, desc)
        else:
            col_code = next(e for e in entetes if normaliser(e).strip() == "cod var")
            col_mod = next((e for e in entetes if normaliser(e).strip() == "cod mod"), None)
            col_lib = (next((e for e in entetes if normaliser(e).strip() == "lib var long"), None)
                       or next((e for e in entetes if normaliser(e).strip() == "lib var"), None))
            if not col_lib:
                return {}
            for row in reader:
                if col_mod and (row.get(col_mod) or "").strip():
                    continue  # ligne de modalité (valeur possible), pas de définition de variable
                nom, desc = (row.get(col_code) or "").strip(), (row.get(col_lib) or "").strip()
                if nom and desc:
                    mapping.setdefault(nom, desc)
        return mapping
    except OSError:
        return {}


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
            if len(points) < _MAX_EMBED:  # embarque jusqu'à _MAX_EMBED, JS affiche par tranches
                points.append([lat, lon, props])
        return {"type": "points", "points": points,
                "nb_total": nb_total, "tronque": nb_total > max_points} if points else None
    except OSError:
        return None


_MAX_EMBED = 50_000  # limite d'embedding dans le HTML (rendu initial : 5 000)


def _apercu_geojson(chemin: str, max_features: int = 5000) -> dict | None:
    """Charge un GeoJSON pour intégration dans la carte Leaflet."""
    try:
        with open(chemin, encoding="utf-8") as f:
            data = json.load(f)
        features = data.get("features", [])
        nb_total = len(features)
        # On embarque jusqu'à _MAX_EMBED features ; le JS n'en affiche que max_features au départ
        if nb_total > _MAX_EMBED:
            data = {**data, "features": features[:_MAX_EMBED]}
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


def _connecteur(meta: dict, ressources: list[dict]) -> str:
    """Déduit le connecteur de moisson à partir de l'URL source des métadonnées RUDI."""
    src = (meta.get("metadata_info") or {}).get("metadata_source", "")
    if "data.gouv.fr" in src:
        return "data.gouv.fr"
    if "insee.fr" in src:
        return "INSEE"
    if "bretagne-environnement.fr" in src:
        return "OEB"
    if "bdnb.io" in src or "s3.fr-par.scw.cloud" in src:
        return "BDNB"
    if src:
        return "Géographique (WFS/WMS/OGC)"
    formats = {r.get("format") for r in ressources}
    if formats & {"wms", "geojson"}:
        return "Géographique (WFS/WMS/OGC)"
    return "data.gouv.fr"


def _champs_geo(cand: dict) -> dict:
    """Champs géographiques utilisés pour le filtrage Rennes Métropole."""
    champs = {}
    for cle, libelle in (("champ_ville", "ville"), ("champ_cp", "cp"),
                         ("champ_iris", "iris"), ("champ_adresse", "adresse"),
                         ("champ_circonscription", "circonscription")):
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
        description = _extraire_description(meta)

        producteur = (meta.get("producer") or {}).get("organization_name", "")
        licence = ((meta.get("access_condition") or {}).get("licence") or {}).get("licence_label", "")
        date_maj = (meta.get("dataset_dates") or {}).get("updated", "")

        formats = sorted({r.get("format") for r in ressources if r.get("format")})

        jeux.append({
            "dataset_id": dossier,
            "titre": titre,
            "producteur": producteur,
            "theme": meta.get("theme", ""),
            "connecteur": _connecteur(meta, ressources),
            "formats": formats,
            "synopsis": synopsis,
            "description": description,
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
    data_json = json.dumps(catalogue, ensure_ascii=False).replace("</", r"<\/")
    html = GABARIT_HTML.replace("/*__DONNEES__*/", data_json)
    # Le fichier écrit doit rester lisible ouvert directement (file://), hors
    # dashboard : le CSS partagé est inliné (le lien /static/dashboard.css ne
    # résout que servi par dashboard.py) et le placeholder topbar devient un
    # commentaire HTML — invisible en direct, substitué par _servir_catalogue().
    chemin_css = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "static", "dashboard.css")
    try:
        with open(chemin_css, encoding="utf-8") as f:
            html = html.replace('<link rel="stylesheet" href="/static/dashboard.css">',
                                "<style>\n" + f.read() + "\n</style>")
    except OSError as e:
        print(f"  ⚠ CSS partagé non inliné ({e}) — catalogue stylé uniquement via le dashboard")
    html = html.replace("{{TOPBAR}}", "<!--TOPBAR-->")
    with open(SORTIE_HTML, "w", encoding="utf-8") as f:
        f.write(html)


GABARIT_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Catalogue des jeux de données — Rennes Métropole</title>
<script>(function(){try{var t=localStorage.getItem("theme");if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}})();</script>
<link rel="stylesheet" href="/static/dashboard.css">
<style>
  :root { --tag-bg:#eef3f6; --tag-txt:#345; --code-bg:#f0f2f4;
          --badge-bg:#fdecea; --badge-txt:#a3372c; }
  :root[data-theme="dark"] { --tag-bg:#20303a; --tag-txt:#b7c9d6;
          --code-bg:#20272e; --badge-bg:#3a201d; --badge-txt:#ff9686; }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) { --tag-bg:#20303a; --tag-txt:#b7c9d6;
          --code-bg:#20272e; --badge-bg:#3a201d; --badge-txt:#ff9686; }
  }
  .catalogue-entete { background:var(--card); border-bottom:1px solid var(--bord); padding:18px 24px;
           position:sticky; top:52px; z-index:5; }
  h1 { margin:0 0 4px; font-size:1.3rem; }
  .meta { color:var(--muted); font-size:.85rem; }
  .barre { margin-top:12px; display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  #recherche { flex:2; min-width:240px; padding:10px 12px; font-size:1rem;
               border:1px solid var(--bord); border-radius:8px; background:var(--card); color:var(--txt); }
  .barre select { padding:9px 10px; font-size:.88rem; border:1px solid var(--bord);
               border-radius:8px; background:var(--card); color:var(--txt); }
  #compteur { color:var(--muted); font-size:.85rem; white-space:nowrap; }
  main { max-width:1000px; margin:0 auto; padding:20px 24px 60px; }
  .jeu { background:var(--card); border:1px solid var(--bord); border-radius:10px;
         padding:16px 18px; margin-bottom:14px; }
  .jeu h2 { margin:0 0 6px; font-size:1.05rem; }
  .jeu h2 a { color:var(--accent); text-decoration:none; }
  .jeu h2 a:hover { text-decoration:underline; }
  .infos { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:.82rem; color:var(--muted); margin-bottom:8px; }
  .infos b { color:var(--txt); font-weight:600; }
  .synopsis { font-size:.9rem; margin:8px 0; font-weight:600; }
  .description { font-size:.88rem; margin:4px 0 8px; color:var(--txt); }
  .description p { margin:0 0 8px; }
  .description p:last-child { margin-bottom:0; }
  .description ul, .description ol { margin:4px 0 8px 20px; }
  .description a { color:var(--accent); }
  .description code { background:var(--code-bg); padding:1px 5px; border-radius:4px; font-size:.85em; }
  .description h4, .description h5, .description h6 { margin:10px 0 4px; color:var(--txt); }
  .description h4 { font-size:1rem; } .description h5 { font-size:.95rem; } .description h6 { font-size:.9rem; }
  .description h4:first-child, .description h5:first-child, .description h6:first-child { margin-top:0; }
  .description.clamp { max-height:4.6em; overflow:hidden; }
  .voir-plus { display:block; margin:2px 0 8px; font-size:.82rem; color:var(--accent);
               background:none; border:none; padding:0; cursor:pointer; font-family:inherit; }
  .voir-plus:hover { text-decoration:underline; }
  .tags { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; }
  .tag { background:var(--tag-bg); color:var(--tag-txt); border-radius:99px; padding:2px 10px; font-size:.75rem; }
  .tag-connecteur { background:var(--accent); color:#fff; border-radius:99px; padding:2px 10px; font-size:.72rem; font-weight:600; white-space:nowrap; }
  .tag-connecteur.insee { background:#1a3a6b; } .tag-connecteur.oeb { background:#2d7d46; }
  .tag-connecteur.bdnb { background:#8c4a1a; } .tag-connecteur.datagouv { background:var(--accent); }
  .tag-connecteur.geo { background:#5a4a8a; }
  .badge { display:inline-block; background:var(--badge-bg); color:var(--badge-txt); border-radius:99px;
           padding:2px 10px; font-size:.72rem; font-weight:600; }
  details summary { cursor:pointer; font-size:.85rem; color:var(--accent); user-select:none; }
  table.res { width:100%; border-collapse:collapse; margin-top:10px; font-size:.82rem; }
  table.res th, table.res td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--bord); }
  table.res th { color:var(--muted); font-weight:600; }
  table.res code { background:var(--code-bg); padding:1px 5px; border-radius:4px; }
  .vide { text-align:center; color:var(--muted); padding:40px; }
</style>
</head>
<body>
{{TOPBAR}}
<section class="catalogue-entete">
  <h1>Catalogue des jeux de données</h1>
  <div class="meta" id="entete"></div>
  <div class="barre">
    <input id="recherche" type="search" placeholder="Rechercher (titre, producteur, mot-clé, identifiant…)" autofocus>
    <select id="filtre-connecteur"><option value="">Tous connecteurs</option></select>
    <select id="filtre-format"><option value="">Tous formats</option></select>
    <select id="filtre-theme"><option value="">Tous thèmes</option></select>
    <select id="tri"><option value="pertinence">Tri : Pertinence</option><option value="titre">Tri : Titre A-Z</option><option value="date">Tri : Plus récent</option></select>
    <span id="compteur"></span>
  </div>
</section>
<main id="liste"></main>
<button id="btn-plus-resultats" style="display:none; margin:0 auto 40px"
        onclick="afficherPlus()">Afficher plus de résultats</button>

<script id="donnees" type="application/json">/*__DONNEES__*/</script>
<script>
const CAT = JSON.parse(document.getElementById("donnees").textContent);
const liste = document.getElementById("liste");
const compteur = document.getElementById("compteur");
document.getElementById("entete").textContent =
  CAT.nb_jeux + " jeux de données moissonnés · généré le " +
  (CAT.genere_le || "").replace("T", " ").replace("+00:00", " UTC");

const THEMES = {economy:"Economie", citizenship:"Citoyenneté", energyNetworks:"Réseaux, Energie",
  culture:"Culture, Sports, Loisirs", transportation:"Mobilité, Transport", children:"Enfance",
  environment:"Environnement", townPlanning:"Urbanisme", location:"Référentiels géographiques",
  education:"Education", publicSpace:"Espace public", health:"Santé, Sécurité",
  housing:"Logement", society:"Social"};

function peupler(id, valeurs, libelle){
  const sel = document.getElementById(id);
  valeurs.forEach(v => {
    const o = document.createElement("option");
    o.value = v; o.textContent = libelle ? (libelle(v)||v) : v;
    sel.appendChild(o);
  });
}
peupler("filtre-connecteur", [...new Set(CAT.jeux.map(j => j.connecteur).filter(Boolean))].sort());
peupler("filtre-format", [...new Set(CAT.jeux.flatMap(j => j.formats||[]))].sort());
peupler("filtre-theme", [...new Set(CAT.jeux.map(j => j.theme).filter(Boolean))].sort(),
  t => THEMES[t]);

function octets(n){
  if (n == null) return "—";
  const u = ["o","Ko","Mo","Go"]; let i=0;
  while (n >= 1024 && i < u.length-1){ n/=1024; i++; }
  return (i ? n.toFixed(1) : n) + " " + u[i];
}
function esc(s){ return (s??"").toString().replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c])); }

// Rendu Markdown minimal (titres/gras/italique/code/liens/listes) pour les descriptions
// sources (data.gouv.fr fournit ses descriptions en Markdown). Échappe d'abord tout le texte,
// puis n'introduit que les balises qu'on construit nous-mêmes : aucune injection possible.
// Séquences \*, \_, \` : protégées via des points de code de contrôle (jamais présents dans
// du texte source réel) le temps du traitement, pour rendre leur échappement Markdown effectif.
const PH_ETOILE = "", PH_SOUL = "", PH_BTICK = "";
function inlineMd(s){
  return esc(s)
    .replace(/\\([*_`])/g, (_, c) => c === "*" ? PH_ETOILE : c === "_" ? PH_SOUL : PH_BTICK)
    .replace(/```([^`]+)```|`([^`]+)`/g, (_, t3, t1) => `<code>${t3 ?? t1}</code>`)
    // Liens en premier, avant gras/italique : une URL (data.gouv.fr en regorge) contient
    // souvent des tirets bas isolés (ex: .../france_6233361_4355770.html) qui, si le motif
    // d'italique passait avant, s'apparient à tort avec un tiret bas sans rapport ailleurs
    // dans le texte — ouvrant un <i> qui ne se referme jamais avant la fin du bloc suivant.
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|&lt;(https?:\/\/[^\s&]+)&gt;|(?<=^|[\s(])(https?:\/\/[^\s<>()]+)/g,
      (m, texteMd, urlMd, urlAuto, urlNu) => {
        if (urlMd) return `<a href="${urlMd}" target="_blank" rel="noopener">${texteMd}</a>`;
        if (urlAuto) return `<a href="${urlAuto}" target="_blank" rel="noopener">${urlAuto}</a>`;
        if (urlNu) {
          const fin = urlNu.match(/[.,;:!?]+$/);
          const propre = fin ? urlNu.slice(0, -fin[0].length) : urlNu;
          const reste = fin ? fin[0] : "";
          return propre ? `<a href="${propre}" target="_blank" rel="noopener">${propre}</a>${reste}` : m;
        }
        return m;
      })
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\s][^*\n]*)\*(?!\*)/g, "$1<i>$2</i>")
    // Tiret bas : jamais d'emphase à l'intérieur d'un mot (garde `\w` de part et d'autre),
    // pour ne pas interpréter un identifiant type `dataset_carte` comme un début d'italique.
    .replace(/(^|[^_\w])_([^_\s][^_\n]*)_(?!_)(?!\w)/g, "$1<i>$2</i>")
    .replace(new RegExp(PH_ETOILE, "g"), "*")
    .replace(new RegExp(PH_SOUL, "g"), "_")
    .replace(new RegExp(PH_BTICK, "g"), "`")
    .replace(/\n/g, "<br>");
}
function markdown(texte){
  const blocs = [];
  let para = [], liste = [], typeListe = null;
  const flushPara = () => { if (para.length) { blocs.push("<p>" + inlineMd(para.join("\n")) + "</p>"); para = []; } };
  const flushListe = () => {
    if (liste.length) {
      const tag = typeListe === "ol" ? "ol" : "ul";
      blocs.push(`<${tag}>` + liste.map(l => "<li>" + inlineMd(l) + "</li>").join("") + `</${tag}>`);
      liste = []; typeListe = null;
    }
  };
  const lignes = (texte || "").split(/\r?\n/);
  for (let i = 0; i < lignes.length; i++) {
    const ligne = lignes[i];
    const mTitre = ligne.match(/^\s{0,3}(#{1,6})\s+(.*)/);
    if (mTitre) {
      flushPara(); flushListe();
      const n = Math.min(mTitre[1].length + 3, 6);
      blocs.push(`<h${n}>${inlineMd(mTitre[2])}</h${n}>`);
      continue;
    }
    const mNum = ligne.match(/^\s*\d+[.)]\s+(.*)/);
    const mPuce = !mNum && ligne.match(/^\s*[-*]\s+(.*)/);
    if (mNum || mPuce) {
      const type = mNum ? "ol" : "ul";
      if (typeListe && typeListe !== type) flushListe();
      flushPara();
      typeListe = type;
      liste.push((mNum || mPuce)[1]);
      continue;
    }
    if (!ligne.trim()) {
      flushPara();
      // Une ligne vide ne referme la liste en cours que si l'item suivant (au-delà
      // d'éventuelles autres lignes vides) n'en est plus un — cas des listes "aérées".
      if (liste.length) {
        let j = i + 1;
        while (j < lignes.length && !lignes[j].trim()) j++;
        const suite = j < lignes.length && (/^\s*\d+[.)]\s+/.test(lignes[j]) || /^\s*[-*]\s+/.test(lignes[j]));
        if (!suite) flushListe();
      }
      continue;
    }
    flushListe(); para.push(ligne);
  }
  flushPara(); flushListe();
  return blocs.join("");
}

function texteRecherche(j){
  return [j.titre, j.producteur, j.dataset_id, j.theme, (j.mots_cles||[]).join(" "),
          j.synopsis, j.description].join(" ").toLowerCase();
}
CAT.jeux.forEach(j => j._t = texteRecherche(j));

function tagConnecteur(j){
  const c = (j.connecteur || "").toLowerCase();
  let cls = "tag-connecteur";
  if (c.includes("insee")) cls += " insee";
  else if (c.includes("oeb")) cls += " oeb";
  else if (c.includes("bdnb")) cls += " bdnb";
  else if (c.includes("géo") || c.includes("wfs") || c.includes("wms")) cls += " geo";
  else cls += " datagouv";
  return `<span class="${cls}">${esc(j.connecteur || "?")}</span>`;
}

function carte(j){
  const iconeConn = tagConnecteur(j);
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
      ${iconeConn}
      ${j.producteur?`<span><b>Producteur :</b> ${esc(j.producteur)}</span>`:""}
      ${j.licence?`<span><b>Licence :</b> ${esc(j.licence)}</span>`:""}
      ${j.date_maj?`<span><b>MàJ :</b> ${esc(j.date_maj.slice(0,10))}</span>`:""}
      ${j.nb_lignes_rm!=null?`<span><b>${j.nb_lignes_rm.toLocaleString("fr")}</b> lignes RM</span>`:""}
      ${champs?`<span><b>Filtre géo :</b> ${champs}</span>`:""}
      <span><code>${esc(j.dataset_id)}</code></span>
      ${j.complet?"":'<span class="badge">métadonnées partielles</span>'}
    </div>
    ${j.synopsis?`<div class="synopsis">${esc(j.synopsis)}</div>`:""}
    ${j.description?`<div class="description clamp">${markdown(j.description)}</div>
    <button type="button" class="voir-plus" hidden>Afficher la description complète</button>`:""}
    ${tags?`<div class="tags">${tags}</div>`:""}
    ${res?`<details><summary>${j.ressources.length} ressource(s)</summary>
      <table class="res"><tr><th>Fichier</th><th>Format</th><th>Lignes</th><th>Taille</th><th></th></tr>
      ${res}</table></details>`:""}
  </article>`;
}

const selConnecteur = document.getElementById("filtre-connecteur");
const selFormat = document.getElementById("filtre-format");
const selTheme = document.getElementById("filtre-theme");
const selTri = document.getElementById("tri");
const btnPlus = document.getElementById("btn-plus-resultats");
const TAILLE_PAGE = 50;
let filtres = [];          // résultats filtrés + triés
let offsetRendu = 0;       // combien déjà affichés

function trier(liste, mode){
  const cpy = [...liste];
  if (mode === "titre") cpy.sort((a, b) => a.titre.localeCompare(b.titre));
  else if (mode === "date") cpy.sort((a, b) => (b.date_maj || "").localeCompare(a.date_maj || ""));
  return cpy;
}

function rendu(){
  const q = document.getElementById("recherche").value.trim().toLowerCase();
  const termes = q.split(/\s+/).filter(Boolean);
  const connecteur = selConnecteur.value, format = selFormat.value, theme = selTheme.value;
  filtres = trier(CAT.jeux.filter(j =>
    termes.every(t => j._t.includes(t)) &&
    (!connecteur || j.connecteur === connecteur) &&
    (!format || (j.formats||[]).includes(format)) &&
    (!theme || j.theme === theme)
  ), selTri.value);
  compteur.textContent = filtres.length + " / " + CAT.jeux.length;
  offsetRendu = 0;
  afficherTranche(true);
}

function afficherTranche(reinit){
  if (reinit) offsetRendu = 0;
  const chunk = filtres.slice(offsetRendu, offsetRendu + TAILLE_PAGE);
  if (reinit) liste.innerHTML = "";
  if (!chunk.length && !offsetRendu){
    liste.innerHTML = '<div class="vide">Aucun résultat.</div>';
    btnPlus.style.display = "none";
    return;
  }
  liste.insertAdjacentHTML("beforeend", chunk.map(carte).join(""));
  offsetRendu += chunk.length;
  btnPlus.style.display = (offsetRendu < filtres.length) ? "block" : "none";

  // Révèle les boutons "voir plus" pour les descriptions tronquées
  requestAnimationFrame(() => {
    liste.querySelectorAll(".description.clamp").forEach(desc => {
      if (desc.scrollHeight > desc.clientHeight + 1) desc.nextElementSibling.hidden = false;
    });
  });
}

function afficherPlus(){ afficherTranche(false); }

// Délégué une fois pour toutes les cartes, y compris celles réaffichées après un filtre.
liste.addEventListener("click", e => {
  const btn = e.target.closest(".voir-plus");
  if (!btn) return;
  const desc = btn.previousElementSibling;
  const replie = desc.classList.toggle("clamp");
  btn.textContent = replie ? "Afficher la description complète" : "Réduire la description";
});

document.getElementById("recherche").addEventListener("input", rendu);
selConnecteur.addEventListener("change", rendu);
selFormat.addEventListener("change", rendu);
selTheme.addEventListener("change", rendu);
selTri.addEventListener("change", rendu);
rendu();
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
<script>(function(){try{var t=localStorage.getItem("theme");if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}})();</script>
<style>
:root{--bg:#f5f6f8;--card:#fff;--txt:#1c2733;--muted:#667;--accent:#0b6e99;--bord:#e2e6ea;
      --sidebar-w:280px;--btn-bg:#eef3f6;--btn-bord:#c8d6e0;--btn-hover:#d4ecf7}
:root[data-theme="dark"]{--bg:#10151b;--card:#1a222b;--txt:#dbe2e8;--muted:#8996a3;--accent:#4fb3da;--bord:#29323c;
      --btn-bg:#1c2932;--btn-bord:#2f4552;--btn-hover:#26404d}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){--bg:#10151b;--card:#1a222b;--txt:#dbe2e8;--muted:#8996a3;--accent:#4fb3da;--bord:#29323c;
      --btn-bg:#1c2932;--btn-bord:#2f4552;--btn-hover:#26404d}
}
*{box-sizing:border-box;margin:0;padding:0}
a{color:var(--accent)}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
     background:var(--bg);color:var(--txt);display:flex;flex-direction:column;height:100vh}
header{background:var(--card);border-bottom:1px solid var(--bord);padding:8px 16px;
       display:flex;flex-wrap:wrap;gap:8px;align-items:center;flex-shrink:0;z-index:1000}
h1{font-size:.9rem;font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#info{color:var(--muted);font-size:.82rem;white-space:nowrap}
#theme-toggle{background:none;border:1px solid var(--bord);border-radius:6px;padding:4px 9px;
              cursor:pointer;font-size:.9rem;line-height:1;color:var(--txt);flex-shrink:0}
#theme-toggle:hover{background:var(--bg)}
#main{flex:1;display:flex;overflow:hidden}
#carte{flex:1}
#sidebar{width:var(--sidebar-w);background:var(--card);border-left:1px solid var(--bord);
         display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
#sidebar.collapsed{width:0;border:none}
#sidebar-toggle{position:absolute;top:50px;right:calc(var(--sidebar-w)+10px);z-index:1000;
                background:var(--card);border:1px solid var(--bord);border-radius:4px;
                padding:4px 8px;cursor:pointer;font-size:.8rem;color:var(--txt)}
#sidebar.collapsed~#sidebar-toggle{right:10px}
#sidebar-header{padding:8px 12px;border-bottom:1px solid var(--bord);font-size:.82rem;
                font-weight:600;color:var(--muted);display:flex;justify-content:space-between;align-items:center}
#search{width:100%;padding:6px 8px;border:1px solid var(--bord);border-radius:4px;
        background:var(--bg);color:var(--txt);font-size:.82rem;margin:8px 12px;width:calc(100% - 24px)}
#layers{flex:1;overflow-y:auto;padding:4px 0}
.layer-item{padding:6px 12px;border-bottom:1px solid var(--bord);cursor:pointer;
            display:flex;align-items:center;gap:8px;font-size:.82rem}
.layer-item:hover{background:var(--bg)}
.layer-item.active{background:var(--accent);color:#fff}
.layer-item input[type="checkbox"]{margin-top:0;flex-shrink:0}
.layer-info{flex:1;min-width:0}
.layer-name{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.layer-title{color:var(--muted);font-size:.78rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#footer{padding:8px 12px;border-top:1px solid var(--bord);font-size:.78rem;color:var(--muted);
        display:flex;gap:8px;flex-wrap:wrap}
#footer button{background:var(--btn-bg);border:1px solid var(--btn-bord);border-radius:4px;
               padding:3px 8px;cursor:pointer;font-size:.78rem;color:var(--txt)}
#footer button:hover{background:var(--btn-hover)}
.leaflet-control-attribution{background:var(--card);color:var(--muted)}
.leaflet-control-attribution a{color:var(--accent)}
.leaflet-bar a{background:var(--card);color:var(--txt);border-bottom-color:var(--bord)}
.leaflet-bar a:hover{background:var(--bg)}
</style>
</head>
<body>
<header>
  <h1 id="titre"></h1>
  <span id="info"></span>
  <button id="theme-toggle" title="Basculer thème clair/sombre" aria-label="Basculer thème clair/sombre">🌙</button>
</header>
<div id="main">
  <div id="carte"></div>
  <div id="sidebar">
    <div id="sidebar-header">
      <span>Couches</span>
      <span id="count"></span>
    </div>
    <input type="text" id="search" placeholder="Filtrer les couches...">
    <div id="layers"></div>
    <div id="footer">
      <button id="btn-all">Tout activer</button>
      <button id="btn-none">Tout désactiver</button>
      <button id="btn-first">Premières</button>
    </div>
  </div>
</div>
<button id="sidebar-toggle" title="Afficher/masquer le panneau">☰</button>
<script id="d" type="application/json">/*__DATA__*/</script>
<script>
function themeEffectif(){
  return document.documentElement.getAttribute("data-theme") ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function urlFond(theme){
  return "https://{s}.basemaps.cartocdn.com/" + (theme==="dark"?"dark_all":"light_all") + "/{z}/{x}/{y}{r}.png";
}

const D=JSON.parse(document.getElementById("d").textContent);
document.title=D.nom;
document.getElementById("titre").textContent=D.nom;
document.getElementById("info").textContent=D.couches.length+" couche(s) WMS";

const map=L.map("carte");
let fond=L.tileLayer(urlFond(themeEffectif()),{
  attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  subdomains:"abcd",maxZoom:19
}).addTo(map);

(function(){
  const btn = document.getElementById("theme-toggle");
  function majIcone(){ btn.textContent = themeEffectif() === "dark" ? "☀️" : "🌙"; }
  btn.addEventListener("click", () => {
    const suivant = themeEffectif() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", suivant);
    try { localStorage.setItem("theme", suivant); } catch(e) {}
    map.removeLayer(fond);
    fond = L.tileLayer(urlFond(suivant),{
      attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains:"abcd",maxZoom:19
    }).addTo(map);
    majIcone();
  });
  majIcone();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!document.documentElement.getAttribute("data-theme")) majIcone();
  });
})();

// Sidebar toggle
const sidebar=document.getElementById("sidebar");
const toggleBtn=document.getElementById("sidebar-toggle");
toggleBtn.addEventListener("click",()=>{
  sidebar.classList.toggle("collapsed");
  toggleBtn.textContent=sidebar.classList.contains("collapsed")?"☰":"✕";
  setTimeout(()=>map.invalidateSize(),300);
});

// Layers
const layerMap={};
const layerEls=[];
const searchInput=document.getElementById("search");
const layersDiv=document.getElementById("layers");
const countSpan=document.getElementById("count");

function updateCount(){
  const n=Object.values(layerMap).filter(l=>map.hasLayer(l.layer)).length;
  countSpan.textContent=n+"/"+D.couches.length;
}

function createLayerItem(c,i){
  const item=document.createElement("div");
  item.className="layer-item";
  item.dataset.nom=c.nom.toLowerCase();
  item.dataset.titre=(c.titre||"").toLowerCase();
  const cb=document.createElement("input");
  cb.type="checkbox";
  cb.id="l"+i;
  const info=document.createElement("div");
  info.className="layer-info";
  const name=document.createElement("div");
  name.className="layer-name";
  name.textContent=c.nom;
  const title=document.createElement("div");
  title.className="layer-title";
  title.textContent=c.titre||"";
  info.appendChild(name);
  if(c.titre&&c.titre!==c.nom)info.appendChild(title);
  item.appendChild(cb);
  item.appendChild(info);

  const layer=L.tileLayer.wms(D.url,{
    layers:c.nom,format:"image/png",transparent:true,opacity:0.75,
    attribution:D.producteur||c.titre||"Source WMS"
  });
  layerMap[c.nom]={layer,cb,item};

  cb.addEventListener("change",()=>{
    if(cb.checked){map.addLayer(layer)}else{map.removeLayer(layer)}
    item.classList.toggle("active",cb.checked);
    updateCount();
  });
  item.addEventListener("click",(e)=>{
    if(e.target===cb)return;
    cb.checked=!cb.checked;
    cb.dispatchEvent(new Event("change"));
  });

  layerEls.push({el:item,nom:c.nom.toLowerCase(),titre:(c.titre||"").toLowerCase()});
  layersDiv.appendChild(item);
  return layer;
}

D.couches.forEach((c,i)=>createLayerItem(c,i));
updateCount();

// Search
searchInput.addEventListener("input",()=>{
  const q=searchInput.value.toLowerCase();
  layerEls.forEach(e=>{
    const match=!q||e.nom.includes(q)||e.titre.includes(q);
    e.el.style.display=match?"":"none";
  });
});

// Footer buttons
document.getElementById("btn-all").addEventListener("click",()=>{
  D.couches.forEach(c=>{const l=layerMap[c.nom];if(l&&!map.hasLayer(l.layer)){l.cb.checked=true;l.cb.dispatchEvent(new Event("change"))}});
});
document.getElementById("btn-none").addEventListener("click",()=>{
  D.couches.forEach(c=>{const l=layerMap[c.nom];if(l&&map.hasLayer(l.layer)){l.cb.checked=false;l.cb.dispatchEvent(new Event("change"))}});
});
document.getElementById("btn-first").addEventListener("click",()=>{
  D.couches.forEach(c=>{const l=layerMap[c.nom];if(l)l.cb.checked=false;l.layer&&map.removeLayer(l.layer)});
  const first=D.couches.slice(0,Math.min(3,D.couches.length));
  first.forEach(c=>{const l=layerMap[c.nom];if(l){l.cb.checked=true;map.addLayer(l.layer)}});
  updateCount();
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
<script>(function(){try{var t=localStorage.getItem("theme");if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}})();</script>
<style>
:root{--bg:#f5f6f8;--card:#fff;--txt:#1c2733;--muted:#667;--accent:#0b6e99;--bord:#e2e6ea;
      --btn-bg:#eef3f6;--btn-bord:#c8d6e0;--btn-hover:#d4ecf7}
:root[data-theme="dark"]{--bg:#10151b;--card:#1a222b;--txt:#dbe2e8;--muted:#8996a3;--accent:#4fb3da;--bord:#29323c;
      --btn-bg:#1c2932;--btn-bord:#2f4552;--btn-hover:#26404d}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){--bg:#10151b;--card:#1a222b;--txt:#dbe2e8;--muted:#8996a3;--accent:#4fb3da;--bord:#29323c;
      --btn-bg:#1c2932;--btn-bord:#2f4552;--btn-hover:#26404d}
}
*{box-sizing:border-box;margin:0;padding:0}
a{color:var(--accent)}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
     background:var(--bg);color:var(--txt);display:flex;flex-direction:column;height:100vh}
header{background:var(--card);border-bottom:1px solid var(--bord);padding:8px 16px;
       display:flex;flex-wrap:wrap;gap:8px;align-items:center;flex-shrink:0}
h1{font-size:.9rem;font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#info{color:var(--muted);font-size:.82rem;white-space:nowrap}
#btn-plus{background:var(--btn-bg);border:1px solid var(--btn-bord);border-radius:6px;padding:4px 12px;
          font-size:.8rem;cursor:pointer;color:var(--accent);white-space:nowrap;flex-shrink:0}
#btn-plus:hover{background:var(--btn-hover)}
#btn-plus:disabled{opacity:.5;cursor:wait}
#theme-toggle{background:none;border:1px solid var(--bord);border-radius:6px;padding:4px 9px;
              cursor:pointer;font-size:.9rem;line-height:1;color:var(--txt);flex-shrink:0}
#theme-toggle:hover{background:var(--bg)}
#carte{flex:1}
.leaflet-popup-content-wrapper,.leaflet-popup-tip{background:var(--card);color:var(--txt)}
.leaflet-popup-content{min-width:160px;max-width:320px;font-size:.8rem}
.leaflet-popup-content table{border-collapse:collapse;width:100%}
.leaflet-popup-content td{padding:3px 6px;vertical-align:top;border-bottom:1px solid var(--bord);word-break:break-word}
.leaflet-popup-content td:first-child{color:var(--muted);font-weight:600;white-space:nowrap;padding-right:10px}
.leaflet-control-attribution{background:var(--card);color:var(--muted)}
.leaflet-control-attribution a{color:var(--accent)}
.leaflet-bar a{background:var(--card);color:var(--txt);border-bottom-color:var(--bord)}
.leaflet-bar a:hover{background:var(--bg)}
</style>
</head>
<body>
<header>
  <h1 id="titre"></h1>
  <span id="info"></span>
  <button id="btn-plus" style="display:none" onclick="chargerPlus()"></button>
  <button id="theme-toggle" title="Basculer thème clair/sombre" aria-label="Basculer thème clair/sombre">🌙</button>
</header>
<div id="carte"></div>
<script id="d" type="application/json">/*__DATA__*/</script>
<script>
function themeEffectif(){
  return document.documentElement.getAttribute("data-theme") ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function urlFond(theme){
  return "https://{s}.basemaps.cartocdn.com/" + (theme==="dark"?"dark_all":"light_all") + "/{z}/{x}/{y}{r}.png";
}

const D=JSON.parse(document.getElementById("d").textContent);
document.title=D.nom;
document.getElementById("titre").textContent=D.nom;

const map=L.map("carte");
let fond=L.tileLayer(urlFond(themeEffectif()),{
  attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  subdomains:"abcd",maxZoom:19
}).addTo(map);

(function(){
  const btn = document.getElementById("theme-toggle");
  function majIcone(){ btn.textContent = themeEffectif() === "dark" ? "☀️" : "🌙"; }
  btn.addEventListener("click", () => {
    const suivant = themeEffectif() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", suivant);
    try { localStorage.setItem("theme", suivant); } catch(e) {}
    map.removeLayer(fond);
    fond = L.tileLayer(urlFond(suivant),{
      attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains:"abcd",maxZoom:19
    }).addTo(map);
    majIcone();
  });
  majIcone();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!document.documentElement.getAttribute("data-theme")) majIcone();
  });
})();

function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function mkPopup(props){
  if(!props||!Object.keys(props).length)return"";
  const rows=Object.entries(props).filter(([,v])=>v!=null&&v!=="").slice(0,30)
    .map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(String(v).slice(0,300))}</td></tr>`).join("");
  return`<table>${rows}</table>`;
}

const PT={radius:7,color:"#0b6e99",weight:1.5,fillColor:"#1a8bbf",fillOpacity:.75};

function ajouterPoints(pts){
  pts.forEach(([lat,lon,props])=>L.circleMarker([lat,lon],PT).bindPopup(mkPopup(props)).addTo(map));
}
function ajouterGeoJSON(features){
  L.geoJSON({type:"FeatureCollection",features},{
    style:{color:"#0b6e99",weight:2,fillColor:"#1a8bbf",fillOpacity:.35},
    pointToLayer:(_,ll)=>L.circleMarker(ll,PT),
    onEachFeature:(f,lyr)=>{if(f.properties)lyr.bindPopup(mkPopup(f.properties));}
  }).addTo(map);
}

// Chargement initial : premiers 5 000 seulement
const BATCH=5000;
let layer;
if(D.type==="points"){
  layer=L.featureGroup(D.points.slice(0,BATCH).map(([lat,lon,props])=>
    L.circleMarker([lat,lon],PT).bindPopup(mkPopup(props))
  )).addTo(map);
}else{
  layer=L.geoJSON({type:"FeatureCollection",features:D.geojson.features.slice(0,BATCH)},{
    style:{color:"#0b6e99",weight:2,fillColor:"#1a8bbf",fillOpacity:.35},
    pointToLayer:(_,ll)=>L.circleMarker(ll,PT),
    onEachFeature:(f,lyr)=>{if(f.properties)lyr.bindPopup(mkPopup(f.properties));}
  }).addTo(map);
}
try{const b=layer.getBounds();if(b.isValid())map.fitBounds(b.pad(0.1));else map.setView([48.1,-1.68],11);}
catch{map.setView([48.1,-1.68],11);}

// Toutes les données sont embarquées dans D — pas de fetch nécessaire
const allItems = D.type==="geojson" ? D.geojson.features : D.points;
let offset = Math.min(BATCH, allItems.length);  // rendu initial

function majInfo(){
  const n=offset, tot=D.nb_total;
  document.getElementById("info").textContent=
    n<tot ? `${n.toLocaleString("fr")} / ${tot.toLocaleString("fr")} éléments`
          : `${n.toLocaleString("fr")} élément${n>1?"s":""}`;
}
function majBouton(){
  const btn=document.getElementById("btn-plus");
  const restant=allItems.length-offset;
  if(restant<=0){btn.style.display="none";return;}
  btn.style.display="";
  btn.textContent=`Afficher 5 000 de plus (${restant.toLocaleString("fr")} restants)`;
}

function chargerPlus(){
  const batch=allItems.slice(offset, offset+BATCH);
  if(D.type==="geojson") ajouterGeoJSON(batch); else ajouterPoints(batch);
  offset+=batch.length;
  majInfo(); majBouton();
}

majInfo();
if(D.tronque) majBouton();
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
<script>(function(){try{var t=localStorage.getItem("theme");if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}})();</script>
<style>
:root{--bg:#f5f6f8;--card:#fff;--txt:#1c2733;--muted:#667;--accent:#0b6e99;--bord:#e2e6ea;
      --avert-bg:#fff8e1;--avert-bord:#ffe082;--avert-txt:#6d4c00;
      --geo-bg:#eaf4fb;--geo-hover:#d4ecf7;--stripe:#fafbfc;--row-hover:#eef6fb;
      --resizer:rgba(11,110,153,.35);--td-bord:#f0f2f4}
:root[data-theme="dark"]{--bg:#10151b;--card:#1a222b;--txt:#dbe2e8;--muted:#8996a3;--accent:#4fb3da;--bord:#29323c;
      --avert-bg:#3a2f10;--avert-bord:#6b5418;--avert-txt:#e8c96a;
      --geo-bg:#16303c;--geo-hover:#1d3f4d;--stripe:#161d24;--row-hover:#17242c;
      --resizer:rgba(79,179,218,.35);--td-bord:#232c35}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){--bg:#10151b;--card:#1a222b;--txt:#dbe2e8;--muted:#8996a3;--accent:#4fb3da;--bord:#29323c;
      --avert-bg:#3a2f10;--avert-bord:#6b5418;--avert-txt:#e8c96a;
      --geo-bg:#16303c;--geo-hover:#1d3f4d;--stripe:#161d24;--row-hover:#17242c;
      --resizer:rgba(79,179,218,.35);--td-bord:#232c35}
}
*{box-sizing:border-box;margin:0;padding:0}
a{color:var(--accent)}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
     background:var(--bg);color:var(--txt);display:flex;flex-direction:column;height:100vh}
header{background:var(--card);border-bottom:1px solid var(--bord);padding:10px 16px;
       display:flex;flex-wrap:wrap;gap:10px;align-items:center;flex-shrink:0}
h1{font-size:.9rem;font-weight:600;flex:1;min-width:150px;
   white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#filtre{padding:7px 11px;font-size:.9rem;border:1px solid var(--bord);
        border-radius:6px;width:220px;background:var(--card);color:var(--txt)}
#info{color:var(--muted);font-size:.82rem;white-space:nowrap}
#theme-toggle{background:none;border:1px solid var(--bord);border-radius:6px;padding:5px 10px;
              cursor:pointer;font-size:.9rem;line-height:1;color:var(--txt);flex-shrink:0}
#theme-toggle:hover{background:var(--bg)}
.avert{background:var(--avert-bg);border-bottom:1px solid var(--avert-bord);padding:5px 16px;
       font-size:.8rem;color:var(--avert-txt);flex-shrink:0}
.wrap{flex:1;overflow:auto}
table{border-collapse:collapse;font-size:.82rem;table-layout:fixed}
thead{position:sticky;top:0;z-index:2;background:var(--card);box-shadow:0 1px 0 var(--bord)}
th{padding:8px 12px;text-align:left;cursor:pointer;user-select:none;color:var(--muted);font-weight:600;
   white-space:nowrap;overflow:hidden;text-overflow:ellipsis;position:relative}
th:hover{background:var(--bg);color:var(--txt)}
th.asc::after{content:" ↑";color:var(--accent)}
th.desc::after{content:" ↓";color:var(--accent)}
th.geo{color:var(--accent);background:var(--geo-bg)}
th.geo:hover{background:var(--geo-hover)}
th.trad{text-decoration:underline dotted;text-decoration-color:var(--accent);text-underline-offset:3px}
td{padding:0;border-bottom:1px solid var(--td-bord)}
td div{padding:5px 12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
td div a{color:var(--accent);text-decoration:none}
td div a:hover{text-decoration:underline}
tr:nth-child(even) td{background:var(--stripe)}
tr:hover td{background:var(--row-hover)}
.resizer{position:absolute;right:0;top:0;width:5px;height:100%;cursor:col-resize;z-index:1}
.resizer:hover,.resizer.active{background:var(--resizer)}
</style>
</head>
<body>
<header>
  <h1 id="titre"></h1>
  <input id="filtre" type="search" placeholder="Filtrer toutes les colonnes…">
  <span id="info"></span>
  <button id="theme-toggle" title="Basculer thème clair/sombre" aria-label="Basculer thème clair/sombre">🌙</button>
</header>
<div id="avert" class="avert" style="display:none"></div>
<div class="wrap"><table id="t"><colgroup id="cols"></colgroup><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>
<script id="d" type="application/json">/*__DATA__*/</script>
<script>
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
const DICO=D.dictionnaire||{};
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
  const th=D.entetes.map((h,i)=>{
    const trad=DICO[h];
    const cls=[sc===i?(asc?"asc":"desc"):"",geo.has(h)?"geo":"",trad?"trad":""].filter(Boolean).join(" ");
    const tip=trad?` title="${esc(h)}"`:"";
    return`<th class="${cls}"${tip}>${esc(trad||h)}<div class="resizer"></div></th>`;
  }).join("");
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


def _ecrire_viewer(chemin: str, nom: str, apercu: dict, champs_geo: list | None = None,
                    dictionnaire: dict | None = None) -> None:
    data = json.dumps({"nom": nom, "champs_geo": champs_geo or [], "dictionnaire": dictionnaire or {},
                        **apercu}, ensure_ascii=False).replace("</", r"<\/")
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
        # Dictionnaire de colonnes fusionné à partir de toutes les ressources CSV du JDD
        # (un JDD peut avoir plusieurs fichiers dictionnaire, ex. variantes par découpage)
        dictionnaire: dict[str, str] = {}
        for res in jeu["ressources"]:
            if res.get("format") == "csv":
                for nom, desc in _charger_dictionnaire(os.path.join(DATA, res["chemin"])).items():
                    dictionnaire.setdefault(nom, desc)
        for res in jeu["ressources"]:
            fmt = res.get("format")
            if res.get("viewer") and fmt == "csv":
                chemin_csv = os.path.join(DATA, res["chemin"])
                chemin_viewer = os.path.join(DATA, res["viewer"])
                apercu = _apercu_csv(chemin_csv)
                if apercu:
                    _ecrire_viewer(chemin_viewer, res["nom"], apercu, champs_geo, dictionnaire)
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
