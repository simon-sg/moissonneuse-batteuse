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

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from filters.geographic import est_dans_rm, est_commune_rm, normaliser
from conf.communes_rm import CODES_POSTAUX_RM

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KEYWORDS = ["commune", "code postal", "code insee"]

NB_PAGES = 5  # pages récupérées par mot-clé (20 résultats/page → 100 max par keyword)

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

    # Département : slug "departement-*" ou nom commence par "departement"
    # Garde le 35 (ille-et-vilaine) et exclut tous les autres
    if nom.startswith("departement") or slug.startswith("departement-"):
        return "35" not in slug and "ille" not in slug

    # Région : garde Bretagne, exclut toutes les autres
    if nom.startswith("region") or slug.startswith("region-"):
        return "bretagne" not in slug and "bretagne" not in nom

    # Intercommunalités hors RM (CA, CC, CU, métropoles, agglo)
    # Repère via le slug (plus fiable que le nom) ou le nom normalisé
    if ("agglomeration" in slug
            or "communaute-de-communes" in slug
            or "communaute-urbaine" in slug
            or "metropole" in slug
            or "agglomeration" in nom
            or "communaute de communes" in nom
            or "metropole" in nom):
        return "rennes" not in slug and "rennes" not in nom

    # Communes hors RM
    for prefix in ("ville de ", "commune de ", "mairie de ", "municipalite de "):
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


def trouver_ressource_csv_json(dataset: dict) -> dict | None:
    """Retourne la première ressource CSV ou JSON du dataset (exclut les .gz)."""
    for fmt in ["csv", "json"]:
        for r in dataset.get("resources", []):
            fmt_r = (r.get("format") or "").lower()
            url_r = (r.get("url") or "").lower().split("?")[0]
            # Exclut les formats compressés (.gz, .zip) — non supportés pour l'instant
            if "gz" in fmt_r or "zip" in fmt_r:
                continue
            if url_r.endswith(".gz") or url_r.endswith(".zip"):
                continue
            if fmt in fmt_r or url_r.endswith(f".{fmt}"):
                return r
    return None


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


def analyser_csv(url: str) -> dict | None:
    """
    Télécharge un CSV complet et cherche des données Rennes Métropole.
    Retourne None si le téléchargement ou le parsing échoue (le JDD sera reproposé).
    """
    print("  Téléchargement en cours...")
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
    except Exception as e:
        print(f"  (Échec du téléchargement : {e})")
        return None

    contenu = b""
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            contenu += chunk
            total += len(chunk)
            print(f"  {total / 1024 / 1024:.1f} Mo...", end="\r")
    except Exception as e:
        print(f"\n  (Interruption du téléchargement : {e})")
        return None
    print()

    # utf-8-sig supprime automatiquement le BOM (﻿) s'il est présent
    texte = contenu.decode("utf-8-sig", errors="replace")
    # Détection automatique du séparateur (virgule, point-virgule, tabulation…)
    sample = texte[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiteur = dialect.delimiter
    except csv.Error:
        delimiteur = ","
    reader = csv.DictReader(io.StringIO(texte), delimiter=delimiteur)
    entetes = reader.fieldnames or []

    champ_cp, champ_ville = deviner_champs(list(entetes))
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
                in_rm = est_commune_rm(ville)  # pas de CP → nom seul (ex: eau potable)
            elif champ_cp:
                in_rm = cp in CODES_POSTAUX_RM
            else:
                in_rm = False
            if in_rm:
                nb_rm += 1
                if len(exemples) < 3:
                    exemples.append({k: v for k, v in row.items()})
    except csv.Error as e:
        print(f"  (Erreur de parsing CSV : {e})")
        return None

    return {
        "nb_total": nb_total,
        "nb_rm": nb_rm,
        "champ_cp": champ_cp,
        "champ_ville": champ_ville,
        "exemples": exemples,
    }


# ---------------------------------------------------------------------------
# Persistance de la découverte
# ---------------------------------------------------------------------------

def charger_decouverte() -> dict:
    """Charge l'historique des JDD déjà vus (pour ne pas les reproposer)."""
    if os.path.exists(DECOUVERTE_FILE):
        with open(DECOUVERTE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"vus": [], "candidats": [], "exclus": []}


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
    deja_vus = set(decouverte["vus"])

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

    # Filtrer les orgs exclues, les territoires hors RM, et les déjà vus
    candidats = [
        ds for ds in datasets_trouves
        if not est_org_exclue(ds)
        and not est_org_hors_rm(ds)
        and couvre_rennes(ds)
        and not titre_hors_rm(ds)
        and ds["id"] not in deja_vus
    ]
    print(f"\n{len(candidats)} JDD à examiner (hors orgs RM et déjà vus)\n")

    for i, ds in enumerate(candidats, 1):
        print(f"\n[{i}/{len(candidats)}]")

        ressource = trouver_ressource_csv_json(ds)
        if ressource is None:
            print(f"  (pas de ressource CSV/JSON, on passe)")
            decouverte["vus"].append(ds["id"])
            continue

        extrait = obtenir_extrait(ressource)
        afficher_fiche(ds, extrait)

        choix = input("\n(s)kip  (a)nalyse approfondie  (q)uitter ? ").strip().lower()

        if choix == "q":
            break
        elif choix == "a":
            print("\nAnalyse approfondie...")
            resultat = analyser_csv(ressource["url"])
            if resultat is None:
                print("  → Ce JDD sera reproposé à la prochaine session.")
                continue  # pas ajouté à vus

            print(f"\n  Total enregistrements : {resultat['nb_total']}")
            print(f"  Dont Rennes Métropole  : {resultat['nb_rm']}")
            if resultat['exemples']:
                print("  Exemples RM :")
                for ex in resultat['exemples']:
                    print(f"    {ex}")

            if resultat['nb_rm'] > 0:
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
                    print(f"  ✓ Ajouté à la liste des candidats.")
                    print(f"  → Ajoute manuellement dans src/conf/datasets.py :")
                    print(f"    {json.dumps(candidat, ensure_ascii=False)}")
        else:
            decouverte["exclus"].append(ds["id"])

        decouverte["vus"].append(ds["id"])
        sauvegarder_decouverte(decouverte)

    print("\n=== Session terminée ===")
    print(f"Résultats sauvegardés dans {DECOUVERTE_FILE}")
    if decouverte["candidats"]:
        print(f"\nJDD candidats retenus : {len(decouverte['candidats'])}")
        for c in decouverte["candidats"]:
            print(f"  - {c['titre'][:60]}")


if __name__ == "__main__":
    main()
