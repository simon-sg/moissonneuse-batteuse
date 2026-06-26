"""
Script de découverte interactive de JDD éligibles sur data.gouv.fr.

Usage : python3 src/discover.py
"""

import sys
import os
import json
import csv
import io
import textwrap
import datetime
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from filters.geographic import est_dans_rm, est_commune_rm, normaliser
from conf.communes_rm import CODES_POSTAUX_RM

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KEYWORDS = ["commune", "code postal", "code insee"]

NB_PAGES = 25  # pages récupérées par mot-clé (20 résultats/page → 500 max par keyword)

# Mots dans le titre indiquant un territoire clairement hors RM
# (datasets sans zones spatiales déclarées mais dont le titre trahit la portée)
TITRES_HORS_RM = [
    "île-de-france", "ile-de-france", "île de france", "ile de france",
    "occitanie", "provence", "paca",
    "auvergne", "rhône-alpes", "rhone-alpes",
    "grand est", "alsace", "lorraine",
    "hauts-de-france", "nord-pas-de-calais", "picardie",
    "nouvelle-aquitaine", "aquitaine",
    "pays de la loire",
    "centre-val de loire",
    "bourgogne", "franche-comté", "franche-comte",
    "corse",
    "normandie",
]

# Slugs d'organisations à exclure (déjà publient sur RM ou hors-sujet)
ORGS_EXCLUES = [
    "rennes-metropole",
    "rennes-metropole-en-acces-libre",
    "sig-rennes-metropole",
    "metropole-de-rennes",
    "ville-de-rennes",
]

# Noms de champs courants pour le code postal et la commune dans les données
CHAMPS_CP = ["cp", "code_postal", "codepostal", "code postal", "postal_code",
             "code_post", "cp_ville", "zipcode", "zip"]
CHAMPS_VILLE = ["ville", "commune", "libelle_commune", "nom_commune",
                "city", "municipality", "lib_commune",
                "libgeo", "lib_geo", "libelle_geo", "libcom", "lib_com",
                "nom_com", "nom_geo", "libelle"]

PAGE_SIZE = 20  # résultats par page

DECOUVERTE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "decouverte.json"
)
LOG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "discover.log"
)

# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------

def rechercher_datasets(keyword: str, nb_pages: int = NB_PAGES) -> tuple[list, int]:
    """
    Cherche des JDD sur data.gouv.fr par mot-clé sur plusieurs pages.
    Retourne (liste de datasets, total de résultats sur data.gouv.fr).
    """
    url = f"https://www.data.gouv.fr/api/1/datasets/"
    tous = []
    total = 0
    for page in range(1, nb_pages + 1):
        params = {"q": keyword, "page_size": PAGE_SIZE, "page": page}
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            resultats = data.get("data", [])
            total = data.get("total", 0)
            tous.extend(resultats)
            if len(resultats) < PAGE_SIZE:
                break  # dernière page atteinte
        except Exception as e:
            print(f"  (Erreur page {page} : {e})")
            break
    return tous, total


def est_org_exclue(dataset: dict) -> bool:
    org = dataset.get("organization") or {}
    slug = org.get("slug", "")
    return any(exclu in slug for exclu in ORGS_EXCLUES)


def _mot_present(nom: str, mot: str) -> bool:
    """Vérifie si `mot` est présent dans `nom` comme mot entier
    (au début ou précédé d'un espace — évite 'interdépartemental')."""
    return nom.startswith(mot) or (" " + mot) in nom


def est_org_hors_rm(dataset: dict) -> bool:
    """
    Retourne True si l'organisation est clairement hors RM :
    - Département autre que le 35 / Ille-et-Vilaine
    - Région autre que Bretagne
    - Intercommunalité (CA, CC, CU, métropole) autre que RM
    - Commune hors RM
    """
    org = dataset.get("organization") or {}
    nom = normaliser(org.get("name") or "")
    slug = (org.get("slug") or "").lower()

    # Département : mot "departement" ou "conseil departemental" n'importe où dans le nom
    # ex: "Département du Finistère", "Seine-Saint-Denis - Le Département"
    if (_mot_present(nom, "departement")
            or _mot_present(nom, "conseil departemental")
            or slug.startswith("departement-")
            or slug.startswith("conseil-departemental-")):
        return "35" not in slug and "ille" not in slug

    # Région : mot "region" ou "conseil regional" n'importe où dans le nom
    if (_mot_present(nom, "region")
            or _mot_present(nom, "conseil regional")
            or slug.startswith("region-")
            or slug.startswith("conseil-regional-")):
        return "bretagne" not in slug and "bretagne" not in nom

    # Intercommunalités hors RM (CA, CC, CU, métropoles, agglo)
    if ("agglomeration" in slug
            or "communaute-de-communes" in slug
            or "communaute-urbaine" in slug
            or "metropole" in slug
            or "agglomeration" in nom
            or "communaute de communes" in nom
            or "metropole" in nom):
        return "rennes" not in slug and "rennes" not in nom

    # Communes hors RM (avec variantes d'élision : "de " et "d'")
    for prefix in ("ville de ", "ville d'",
                   "commune de ", "commune d'",
                   "mairie de ", "mairie d'",
                   "municipalite de ", "municipalite d'"):
        if nom.startswith(prefix):
            nom_commune = nom[len(prefix):]
            return not est_commune_rm(nom_commune)

    return False


def titre_hors_rm(dataset: dict) -> bool:
    """
    Retourne True si le titre indique clairement un territoire hors RM,
    pour filtrer les datasets sans zones spatiales mais géographiquement ciblés ailleurs.
    """
    titre = dataset.get("title", "").lower().strip()
    # "COMMUNE DE X" → dataset sur une commune spécifique, non pertinent
    if titre.startswith("commune de ") or titre.startswith("commune d'"):
        return True
    return any(region in titre for region in TITRES_HORS_RM)


ZONES_INCLUANT_RM = {
    "fr:region:53",       # Bretagne
    "fr:departement:35",  # Ille-et-Vilaine
    "fr:epci:243500139",  # Rennes Métropole (SIREN)
}


def couvre_rennes(dataset: dict) -> bool:
    """
    Retourne True si le périmètre géographique du dataset inclut Rennes Métropole.
    - Pas de zones → on garde (portée nationale non précisée)
    - country:* ou country-subset:* → on garde (France entière)
    - Bretagne, Ille-et-Vilaine, Rennes Métropole, communes RM → on garde
    - Toute autre zone locale explicite → on exclut
    """
    spatial = dataset.get("spatial") or {}
    zones = spatial.get("zones", [])

    if not zones:
        return True

    for zone in zones:
        if zone.startswith(("country:", "country-subset:")):
            return True
        if zone in ZONES_INCLUANT_RM:
            return True
        # Communes dont le code INSEE commence par 35 (Ille-et-Vilaine)
        if zone.startswith("fr:commune:35"):
            return True

    return False


# Formats supportés pour l'analyse automatique (seront étendus progressivement)
FORMATS_SUPPORTES = ("csv", "json")

# Extensions/formats exclus de la détection CSV/JSON (pas encore supportés)
# Ces datasets ne sont PAS stockés dans une liste permanente : quand on ajoutera
# le support XLS, ZIP, geo, etc., ils réapparaîtront automatiquement.
FORMATS_NON_SUPPORTES_FMT = (".gz", ".zip", "pdf", "excel", "shapefile",
                              "xls", "xlsx", "ods", "geojson", "shapefile",
                              "wms", "wfs", "ogc")
FORMATS_NON_SUPPORTES_EXT = (".gz", ".zip", ".pdf", ".doc", ".docx",
                              ".xls", ".xlsx", ".ods",
                              ".shp", ".geojson", ".gpkg", ".kml",
                              ".html", ".htm")


def trouver_ressource_csv_json(dataset: dict) -> dict | None:
    """Retourne la première ressource CSV ou JSON du dataset, ou None."""
    for fmt in FORMATS_SUPPORTES:
        for r in dataset.get("resources", []):
            fmt_r = (r.get("format") or "").lower()
            url_r = (r.get("url") or "").lower().split("?")[0]
            if any(token in fmt_r for token in FORMATS_NON_SUPPORTES_FMT):
                continue
            if any(url_r.endswith(ext) for ext in FORMATS_NON_SUPPORTES_EXT):
                continue
            if fmt in fmt_r or url_r.endswith(f".{fmt}"):
                return r
    return None


def formats_disponibles(dataset: dict) -> list:
    """Retourne les formats uniques des ressources d'un dataset (pour stats)."""
    fmts = set()
    for r in dataset.get("resources", []):
        fmt = (r.get("format") or "").upper()
        if fmt:
            fmts.add(fmt)
    return sorted(fmts)


# ---------------------------------------------------------------------------
# Extrait des données
# ---------------------------------------------------------------------------

def telecharger_extrait_csv(url: str, n_lignes: int = 5) -> str:
    """Télécharge les premières lignes d'un CSV (sans télécharger tout le fichier)."""
    try:
        response = requests.get(url, stream=True, timeout=15)
        response.raise_for_status()
        lignes = []
        for chunk in response.iter_lines():
            if chunk:
                lignes.append(chunk.decode("utf-8", errors="replace"))
            if len(lignes) >= n_lignes + 1:  # +1 pour l'en-tête
                break
        response.close()
        return "\n".join(lignes)
    except Exception as e:
        return f"(Impossible de télécharger l'extrait : {e})"


def telecharger_extrait_json(url: str, n_lignes: int = 5) -> str:
    """Télécharge un petit bout d'un JSON pour en montrer la structure."""
    try:
        response = requests.get(url, stream=True, timeout=15)
        response.raise_for_status()
        # On lit seulement les premiers Ko
        contenu = b""
        for chunk in response.iter_content(chunk_size=4096):
            contenu += chunk
            if len(contenu) > 8192:
                break
        response.close()
        texte = contenu.decode("utf-8", errors="replace")
        # On essaie de trouver les premiers enregistrements
        debut = texte[:2000]
        return debut + "\n[... tronqué ...]"
    except Exception as e:
        return f"(Impossible de télécharger l'extrait : {e})"


def obtenir_extrait(ressource: dict) -> str:
    fmt = ressource.get("format", "").lower()
    url = ressource.get("url", "")
    if fmt == "csv":
        return telecharger_extrait_csv(url)
    elif fmt == "json":
        return telecharger_extrait_json(url)
    return "(format non supporté pour l'extrait)"


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------

SEP = "─" * 72

def afficher_fiche(dataset: dict, extrait: str) -> None:
    org = (dataset.get("organization") or {}).get("name", "?")
    description = dataset.get("description", "") or ""
    description_courte = textwrap.fill(description[:300].replace("\n", " "), width=70)
    formats = list(set(
        r.get("format", "").upper()
        for r in dataset.get("resources", [])
        if r.get("format")
    ))

    print(f"\n{SEP}")
    print(f"TITRE    : {dataset['title']}")
    print(f"ORG      : {org}")
    print(f"LICENCE  : {dataset.get('license', '?')}")
    print(f"FORMATS  : {', '.join(formats)}")
    print(f"MAJ      : {dataset.get('last_modified', '?')[:10]}")
    print(f"URL      : https://www.data.gouv.fr/datasets/{dataset['id']}")
    print(f"\nDESCRIPTION :\n{description_courte}")
    print(f"\nEXTRAIT (5 premières lignes) :\n{extrait[:1000]}")
    print(SEP)


# ---------------------------------------------------------------------------
# Analyse approfondie
# ---------------------------------------------------------------------------

def deviner_champs(entetes: list[str]) -> tuple[str | None, str | None]:
    """Devine les champs code postal et ville dans les en-têtes d'un CSV."""
    entetes_norm = [e.lower().strip() for e in entetes]
    champ_cp = next((e for e in entetes_norm if e in CHAMPS_CP), None)
    champ_ville = next((e for e in entetes_norm if e in CHAMPS_VILLE), None)
    # Si pas trouvé exactement, cherche si un en-tête contient le mot
    if not champ_cp:
        champ_cp = next((e for e in entetes_norm if "postal" in e or e == "cp"), None)
    if not champ_ville:
        # Exclut les champs de code/insee/dep qui contiennent "commune" sans être des noms
        champ_ville = next(
            (e for e in entetes_norm
             if ("commune" in e or "ville" in e or "libelle" in e or "libgeo" in e)
             and "insee" not in e and "dep" not in e
             and not e.startswith("code") and "partenaire" not in e),
            None,
        )
    # Remappe sur le nom original
    if champ_cp:
        champ_cp = entetes[entetes_norm.index(champ_cp)]
    if champ_ville:
        champ_ville = entetes[entetes_norm.index(champ_ville)]
    return champ_cp, champ_ville


def log_analyse(entry: dict) -> None:
    """Ajoute une entrée JSON à discover.log (une ligne par analyse)."""
    entry["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def analyser_csv(url: str, verbose: bool = True,
                 dataset_id: str = "", titre: str = "") -> dict | None:
    """
    Télécharge un CSV complet et cherche des données Rennes Métropole.
    verbose=False supprime les prints (pour l'exécution en arrière-plan).
    Retourne None si le téléchargement ou le parsing échoue (le JDD sera reproposé).
    """
    log = {"url": url, "dataset_id": dataset_id, "titre": titre}
    if verbose:
        print("  Téléchargement en cours...")
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
    except Exception as e:
        log["erreur"] = f"téléchargement : {e}"
        log_analyse(log)
        if verbose:
            print(f"  (Échec du téléchargement : {e})")
        return None

    contenu = b""
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            contenu += chunk
            total += len(chunk)
            if verbose:
                print(f"  {total / 1024 / 1024:.1f} Mo...", end="\r")
    except Exception as e:
        log["erreur"] = f"téléchargement interrompu : {e}"
        log_analyse(log)
        if verbose:
            print(f"\n  (Interruption du téléchargement : {e})")
        return None
    if verbose:
        print()

    log["taille_mo"] = round(total / 1024 / 1024, 2)

    # Détection précoce de fichiers binaires (PDF, ZIP…)
    if contenu[:5] in (b"%PDF-", b"PK\x03\x04", b"\x1f\x8b\x08"):
        log["erreur"] = "fichier binaire détecté (PDF/ZIP/GZ), non supporté"
        log_analyse(log)
        if verbose:
            print("  (Fichier binaire détecté, non supporté)")
        return None

    texte = contenu.decode("utf-8-sig", errors="replace")
    sample = texte[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiteur = dialect.delimiter
    except csv.Error:
        delimiteur = ","
    log["delimiteur"] = delimiteur

    reader = csv.DictReader(io.StringIO(texte), delimiter=delimiteur)
    entetes = reader.fieldnames or []
    log["entetes"] = list(entetes)[:15]

    champ_cp, champ_ville = deviner_champs(list(entetes))
    log["champ_cp"] = champ_cp
    log["champ_ville"] = champ_ville

    if verbose:
        print(f"  En-têtes détectés : {list(entetes)[:10]}")
        print(f"  Champ CP trouvé : {champ_cp} | Champ ville trouvé : {champ_ville}")

    nb_total = 0
    nb_rm = 0
    exemples = []

    try:
        for row in reader:
            nb_total += 1
            cp = str(row.get(champ_cp, "")).strip() if champ_cp else ""
            ville = str(row.get(champ_ville, "")).strip() if champ_ville else ""

            if champ_cp and champ_ville:
                in_rm = est_dans_rm(ville, cp)
            elif champ_ville:
                in_rm = est_commune_rm(ville)
            elif champ_cp:
                in_rm = cp in CODES_POSTAUX_RM
            else:
                in_rm = False
            if in_rm:
                nb_rm += 1
                if len(exemples) < 3:
                    exemples.append({k: v for k, v in row.items()})
    except csv.Error as e:
        log["erreur"] = f"parsing CSV : {e}"
        log_analyse(log)
        if verbose:
            print(f"  (Erreur de parsing CSV : {e})")
        return None

    log["nb_total"] = nb_total
    log["nb_rm"] = nb_rm
    log_analyse(log)

    return {
        "nb_total": nb_total,
        "nb_rm": nb_rm,
        "champ_cp": champ_cp,
        "champ_ville": champ_ville,
        "exemples": exemples,
    }


# ---------------------------------------------------------------------------
# Traitement des résultats d'analyse
# ---------------------------------------------------------------------------

def traiter_resultat(ds: dict, resultat: dict | None, decouverte: dict) -> None:
    """
    Affiche le résultat d'une analyse (sync ou arrière-plan) et demande
    si on ajoute le JDD aux candidats. Ajoute à vus et sauvegarde.
    """
    did = ds["id"]
    print(f"\n{SEP}")
    print(f"Résultat analyse : {ds['title'][:60]}")

    if resultat is None:
        # Incrémente le compteur d'échecs consécutifs
        n = decouverte["echecs_n"].get(did, 0) + 1
        decouverte["echecs_n"][did] = n
        # Retire de vus (rétrocompat)
        decouverte["vus"] = [i for i in decouverte["vus"] if i != did]
        if did not in decouverte["echecs"]:
            decouverte["echecs"].append(did)
        if n >= 3:
            print(f"  Analyse échouée ({n} fois) — skip définitif recommandé.")
            choix = input("  (s)kip définitif  (r)éessayer plus tard  ? ").strip().lower()
            if choix == "s":
                decouverte["echecs"] = [i for i in decouverte["echecs"] if i != did]
                decouverte["echecs_n"].pop(did, None)
                decouverte["exclus"].append(did)
                decouverte["vus"].append(did)
                print("  Skip définitif enregistré.")
            else:
                print("  Sera reproposé à la prochaine session.")
        else:
            print(f"  Analyse échouée ({n}/3) — sera reproposé à la prochaine session.")
        sauvegarder_decouverte(decouverte)
        return

    # Succès : retire de echecs et remet le compteur à zéro
    decouverte["echecs"] = [i for i in decouverte["echecs"] if i != did]
    decouverte["echecs_n"].pop(did, None)

    print(f"  Total enregistrements : {resultat['nb_total']}")
    print(f"  Dont Rennes Métropole  : {resultat['nb_rm']}")
    if resultat["exemples"]:
        print("  Exemples RM :")
        for ex in resultat["exemples"]:
            print(f"    {ex}")

    if resultat["nb_rm"] > 0:
        ajout = input("\n  Ajouter à datasets.py ? (o/n) ").strip().lower()
        if ajout == "o":
            candidat = {
                "dataset_id": ds["id"],
                "titre": ds["title"],
                "dossier": ds["id"][:30].replace("-", "_"),
                "champ_cp": resultat["champ_cp"],
                "champ_ville": resultat["champ_ville"],
                "nb_rm": resultat["nb_rm"],
            }
            decouverte["candidats"].append(candidat)
            print(f"  Ajouté à la liste des candidats.")
            print(f"  Ajoute manuellement dans src/conf/datasets.py :")
            print(f"    {json.dumps(candidat, ensure_ascii=False)}")

    decouverte["vus"].append(did)
    sauvegarder_decouverte(decouverte)


# ---------------------------------------------------------------------------
# Persistance de la découverte
# ---------------------------------------------------------------------------

def charger_decouverte() -> dict:
    """Charge l'historique des JDD déjà vus (pour ne pas les reproposer)."""
    if os.path.exists(DECOUVERTE_FILE):
        with open(DECOUVERTE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("echecs", [])
        d.setdefault("echecs_n", {})     # {dataset_id: nb_echecs consécutifs}
        d.setdefault("sans_ressource", [])
        return d
    return {"vus": [], "candidats": [], "exclus": [],
            "echecs": [], "echecs_n": {}, "sans_ressource": []}


def fetcher_datasets_par_ids(ids: list) -> list:
    """Récupère les métadonnées de datasets depuis l'API data.gouv.fr."""
    datasets = []
    for did in ids:
        try:
            r = requests.get(
                f"https://www.data.gouv.fr/api/1/datasets/{did}/",
                timeout=10
            )
            if r.ok:
                ds = r.json()
                ds["_echec"] = True
                datasets.append(ds)
            else:
                print(f"  (impossible de récupérer {did} : HTTP {r.status_code})")
        except Exception as e:
            print(f"  (impossible de récupérer {did} : {e})")
    return datasets


def sauvegarder_decouverte(decouverte: dict) -> None:
    os.makedirs(os.path.dirname(DECOUVERTE_FILE), exist_ok=True)
    with open(DECOUVERTE_FILE, "w", encoding="utf-8") as f:
        json.dump(decouverte, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Boucle interactive principale
# ---------------------------------------------------------------------------

def main():
    print("=== Découverte interactive de JDD éligibles ===")
    print(f"Mots-clés recherchés : {', '.join(KEYWORDS)}\n")

    decouverte = charger_decouverte()
    echecs_ids = set(decouverte["echecs"])
    sans_ressource_ids = set(decouverte["sans_ressource"])
    # deja_vus exclut les échecs pour qu'ils ne soient pas filtrés dans les candidats
    deja_vus = set(decouverte["vus"]) - echecs_ids

    # Repropose les analyses échouées en priorité
    echecs_datasets = []
    if decouverte["echecs"]:
        print(f"Récupération de {len(decouverte['echecs'])} JDD à ré-analyser...")
        echecs_datasets = fetcher_datasets_par_ids(decouverte["echecs"])
        print(f"{len(echecs_datasets)} JDD récupérés pour ré-analyse.\n")

    datasets_trouves = []
    ids_trouves = set()

    for keyword in KEYWORDS:
        print(f"Recherche : « {keyword} »...")
        resultats, total = rechercher_datasets(keyword)
        print(f"  {total} résultats au total, {len(resultats)} récupérés ({NB_PAGES} pages)")
        for ds in resultats:
            if ds["id"] not in ids_trouves:
                datasets_trouves.append(ds)
                ids_trouves.add(ds["id"])

    # Les échecs sont traités séparément : on les exclut des candidats normaux
    echecs_ids_fetched = {ds["id"] for ds in echecs_datasets}

    candidats_nouveaux = [
        ds for ds in datasets_trouves
        if not est_org_exclue(ds)
        and not est_org_hors_rm(ds)
        and couvre_rennes(ds)
        and not titre_hors_rm(ds)
        and ds["id"] not in deja_vus
        and ds["id"] not in echecs_ids_fetched
        # sans_ressource : on garde pour re-vérifier inline (les datasets peuvent évoluer)
    ]

    # Les échecs passent en premier
    candidats = echecs_datasets + candidats_nouveaux
    print(f"\n{len(candidats)} JDD à examiner", end="")
    if echecs_datasets:
        print(f" (dont {len(echecs_datasets)} ré-analyse(s) échouée(s))", end="")
    print("\n")

    executor = ThreadPoolExecutor(max_workers=5)
    en_cours = []  # liste de (ds, future)

    def traiter_finis():
        """Affiche les analyses terminées, laisse les autres en attente."""
        restants = []
        for ds_a, fut in en_cours:
            if fut.done():
                try:
                    resultat = fut.result()
                except Exception as e:
                    print(f"\n  Erreur analyse ({ds_a['title'][:40]}) : {e}")
                    resultat = None
                traiter_resultat(ds_a, resultat, decouverte)
            else:
                restants.append((ds_a, fut))
        en_cours[:] = restants

    nb_format_non_supporte = 0  # compteur pour stats fin de session

    try:
        for i, ds in enumerate(candidats, 1):
            # Affiche les analyses en arrière-plan terminées avant chaque nouveau JDD
            if en_cours:
                traiter_finis()
                if en_cours:
                    print(f"  ({len(en_cours)} analyse(s) en arrière-plan en cours...)")

            is_echec = ds.get("_echec", False)
            did = ds["id"]
            ressource = trouver_ressource_csv_json(ds)

            if ressource is None:
                ressources = ds.get("resources", [])
                if not ressources:
                    # Vraiment vide : on mémorise pour ne plus jamais vérifier
                    if did not in sans_ressource_ids:
                        decouverte["sans_ressource"].append(did)
                        sans_ressource_ids.add(did)
                        sauvegarder_decouverte(decouverte)
                else:
                    # A des ressources mais dans un format non encore supporté
                    nb_format_non_supporte += 1
                    if is_echec:
                        # Echec dû à un format non supporté, pas à une erreur d'analyse
                        # → retirer de echecs, il reviendra quand le support sera ajouté
                        fmts = formats_disponibles(ds)
                        print(f"\n  (!) Echec {ds['title'][:50]!r} : "
                              f"format non supporté ({', '.join(fmts)}) — retiré des échecs.")
                        decouverte["echecs"] = [j for j in decouverte["echecs"] if j != did]
                        decouverte["echecs_n"].pop(did, None)
                        sauvegarder_decouverte(decouverte)
                continue

            print(f"\n[{i}/{len(candidats)}]")
            if is_echec:
                print("  (!) Analyse précédemment échouée")

            # Si le dataset était dans sans_ressource mais a maintenant des ressources → on le retire
            if did in sans_ressource_ids:
                decouverte["sans_ressource"] = [
                    j for j in decouverte["sans_ressource"] if j != did
                ]
                sans_ressource_ids.discard(did)
                sauvegarder_decouverte(decouverte)
                print("  (ressources détectées — analyse disponible)")

            extrait = obtenir_extrait(ressource)
            afficher_fiche(ds, extrait)

            if is_echec:
                n_echecs = decouverte["echecs_n"].get(did, 0)
                suffixe = f" — {n_echecs} échec(s) précédent(s)" if n_echecs else ""
                choix = input(
                    f"\n(s)kip définitif  (a)nalyse en arrière-plan  (q)uitter ?{suffixe} "
                ).strip().lower()
            else:
                choix = input(
                    "\n(s)kip  (a)nalyse en arrière-plan  (q)uitter ? "
                ).strip().lower()

            # Affiche immédiatement les analyses terminées pendant la lecture
            if en_cours:
                traiter_finis()

            if choix == "q":
                break
            elif choix == "a":
                future = executor.submit(
                    analyser_csv, ressource["url"], False, ds["id"], ds["title"]
                )
                en_cours.append((ds, future))
                print(f"  Analyse lancée en arrière-plan ({len(en_cours)} en cours).")
                continue  # pas ajouté à vus pour l'instant
            else:
                # skip (s ou autre)
                if is_echec:
                    # Skip définitif : retire des échecs
                    decouverte["echecs"] = [
                        i for i in decouverte["echecs"] if i != ds["id"]
                    ]
                decouverte["exclus"].append(ds["id"])

            decouverte["vus"].append(ds["id"])
            sauvegarder_decouverte(decouverte)

    except KeyboardInterrupt:
        print("\n\nInterruption clavier.")

    # Attend et affiche toutes les analyses restantes
    if en_cours:
        print(f"\n{len(en_cours)} analyse(s) en cours, attente des résultats...")
        print("(Ctrl+C pour abandonner les analyses restantes)\n")
        for ds_a, fut in en_cours:
            try:
                resultat = fut.result(timeout=180)
            except KeyboardInterrupt:
                print("\nAbandon des analyses restantes — elles seront reproposées.")
                fut.cancel()
                break
            except Exception as e:
                print(f"  Erreur : {e}")
                resultat = None
            traiter_resultat(ds_a, resultat, decouverte)

    executor.shutdown(wait=False)

    print("\n=== Session terminée ===")
    print(f"Résultats sauvegardés dans {DECOUVERTE_FILE}")
    if nb_format_non_supporte:
        print(f"\n{nb_format_non_supporte} JDD ignorés cette session (format non encore supporté : "
              f"XLS, ZIP, géo, WMS…) — ils réapparaîtront quand le support sera ajouté.")
    if decouverte["echecs"]:
        print(f"\n{len(decouverte['echecs'])} JDD en échec — reproposés à la prochaine session.")
    if decouverte["candidats"]:
        print(f"\nJDD candidats retenus : {len(decouverte['candidats'])}")
        for c in decouverte["candidats"]:
            print(f"  - {c['titre'][:60]}")


if __name__ == "__main__":
    main()
