"""
Script de découverte interactive de JDD éligibles sur data.gouv.fr.

Usage : python3 src/discover.py
"""

import datetime
import os
import json
import re
import textwrap
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.discover import (
    REQUETES_STRUCTUREES, KEYWORDS, NB_PAGES, RECHERCHE_STRUCTUREE,
    DECOUVERTE_FILE, RESULTATS_API_FILE, PREFILTRES_FILE, GEO_FILE,
)
from connectors.analyseurs import (
    analyser_dataset, _format_analysable,
)
from connectors.download import _purger_cache
from filters.discovery import (
    pre_filtrer, rechercher_datasets, _paginer,
    _filtrer_communs,
    trouver_ressource_analysable, formats_disponibles,
    telecharger_extrait_csv,
    obtenir_extrait,
)

# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------

SEP = "─" * 72

def afficher_fiche(dataset: dict, extrait: str, resultat: dict | None = None) -> None:
    org = (dataset.get("organization") or {}).get("name", "?")
    description = dataset.get("description", "") or ""
    description_courte = textwrap.fill(description[:300].replace("\n", " "), width=70,
                                       break_long_words=True, break_on_hyphens=True)
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
    url = dataset.get("_url") or f"https://www.data.gouv.fr/datasets/{dataset['id']}"
    print(f"URL      : {url}")
    if resultat is not None:
        if resultat.get("champ_iris"):
            methode, champ = "IRIS", resultat["champ_iris"]
        elif resultat.get("champ_adresse"):
            methode, champ = "adresse", resultat["champ_adresse"]
        elif resultat.get("champ_siren"):
            methode, champ = "SIREN", resultat["champ_siren"]
        elif resultat.get("champ_lat"):
            methode = "géo"
            if resultat.get("champ_lon"):
                champ = f"{resultat['champ_lat']} + {resultat['champ_lon']}"
            else:
                champ = resultat["champ_lat"]
        elif resultat.get("champ_cp") or resultat.get("champ_ville"):
            methode = "CP/ville"
            champ = " + ".join(filter(None, [resultat.get("champ_cp"), resultat.get("champ_ville")]))
        elif resultat.get("champ_circonscription"):
            methode, champ = "circonscription", resultat["champ_circonscription"]
        elif resultat.get("champ_dep"):
            methode, champ = "département", resultat["champ_dep"]
        else:
            methode, champ = "?", "?"
        print(f"ANALYSE  : {resultat['nb_total']} lignes | {resultat['nb_rm']} RM | {methode}: {champ}")
    print(f"\nDESCRIPTION :\n{description_courte}")
    lignes_extrait = extrait.splitlines()[:6]
    lignes_extrait = [l[:120] + ("…" if len(l) > 120 else "") for l in lignes_extrait]
    print(f"\nEXTRAIT (5 premières lignes) :\n" + "\n".join(lignes_extrait))
    print(SEP)


# ---------------------------------------------------------------------------
# Traitement des résultats d'analyse
# ---------------------------------------------------------------------------

def _resumer_ligne(ligne: dict, max_cols: int = 5, max_val: int = 18) -> str:
    """Affiche les premières colonnes d'une ligne CSV sur une seule ligne de terminal."""
    items = list(ligne.items())
    parts = []
    total = 2  # pour les accolades
    for k, v in items[:max_cols]:
        vs = str(v)[:max_val] + ("…" if len(str(v)) > max_val else "")
        ks = str(k)[:18]
        part = f"{ks}: {vs}"
        total += len(part) + 2
        if total > 76:
            parts.append(f"+{len(items) - len(parts)} autres")
            break
        parts.append(part)
    if len(parts) == max_cols and len(items) > max_cols:
        parts.append(f"+{len(items) - max_cols} autres")
    return "{" + ", ".join(parts) + "}"


def traiter_resultat(ds: dict, resultat: dict | None, decouverte: dict) -> None:
    """
    Affiche le résultat d'une analyse (sync ou arrière-plan) et demande
    si on ajoute le JDD aux candidats. Ajoute à vus et sauvegarde.
    """
    did = ds["id"]
    print(f"\n{SEP}")
    print(f"Résultat analyse : {ds['title'][:60]}")

    if resultat is None:
        # Ne pas toucher un dataset explicitement exclu (skip définitif)
        if did in decouverte["exclus"]:
            return
        # Incrémente le compteur d'échecs consécutifs
        n = decouverte["echecs_n"].get(did, 0) + 1
        decouverte["echecs_n"][did] = n
        # Retire de vus (rétrocompat)
        decouverte["vus"] = [v for v in decouverte["vus"] if v != did]
        if did not in decouverte["echecs"]:
            decouverte["echecs"].append(did)
        if n >= 3:
            print(f"  Analyse échouée ({n} fois) — skip définitif recommandé.")
            choix = input("  (s)kip définitif  (r)éessayer plus tard  ? ").strip().lower()
            if choix == "s":
                decouverte["echecs"] = [v for v in decouverte["echecs"] if v != did]
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
    decouverte["echecs"] = [v for v in decouverte["echecs"] if v != did]
    decouverte["echecs_n"].pop(did, None)

    # Cas spécial WFS : données géographiques → DATASETS_GEO (pas harvest_batch)
    if resultat.get("type") == "wfs":
        nb_rm = resultat.get("nb_rm", 0)
        layers = resultat.get("wfs_layers", [])
        print(f"  Service WFS — {nb_rm} feature(s) RM trouvé(s)")
        print(f"  Couches : {layers[:5]}" + (f" +{len(layers)-5} autres" if len(layers) > 5 else ""))
        if resultat.get("exemples"):
            print("  Exemples RM :")
            for ex in resultat["exemples"][:2]:
                print("    " + _resumer_ligne(ex))
        ajout = input("\n  Ajouter aux services géo (harvest_geo.py) ? (o/n) ").strip().lower()
        if ajout == "o":
            geo_entry = {
                "id": did[:30].replace("-", "_"),
                "type": "wfs",
                "url": resultat.get("url", ""),
                "couches": layers[:10],
                "titre": ds["title"],
                "producteur": (ds.get("organization") or {}).get("name", ""),
                "dossier": did[:30].replace("-", "_"),
                "theme": "environment",
            }
            _sauver_service_geo(geo_entry)
        decouverte["vus"].append(did)
        sauvegarder_decouverte(decouverte)
        return

    # Cas spécial WMS : pas de données filtrables, présentation interactive
    if resultat.get("type") == "wms":
        nb_couches = resultat.get("nb_couches_rm", 0)
        print(f"  Service WMS — {nb_couches} couche(s) dans la bbox Rennes Métropole")
        print(f"  Titre service : {resultat.get('titre_service', '')}")
        couches = resultat.get("couches", [])
        if couches:
            print("  Couches RM :")
            for c in couches[:6]:
                print(f"    - {c['nom']} : {c.get('titre', '')}")
            if len(couches) > 6:
                print(f"    … +{len(couches) - 6} autres")
        ajout = input("\n  Ajouter aux services géo (harvest_geo.py) ? (o/n) ").strip().lower()
        if ajout == "o":
            couches_noms = [c["nom"] for c in couches]
            geo_entry = {
                "id": did[:30].replace("-", "_"),
                "type": "wms",
                "url": resultat["url"],
                "couches": couches_noms[:10],
                "titre": ds["title"],
                "producteur": (ds.get("organization") or {}).get("name", ""),
                "dossier": did[:30].replace("-", "_"),
                "theme": "environment",
            }
            _sauver_service_geo(geo_entry)
        decouverte["vus"].append(did)
        sauvegarder_decouverte(decouverte)
        return

    print(f"  Total enregistrements : {resultat['nb_total']}")
    print(f"  Dont Rennes Métropole  : {resultat['nb_rm']}")

    if resultat["nb_rm"] > 0:
        # Données RM trouvées → ajout automatique
        if resultat["exemples"]:
            print("  Exemples RM :")
            for ex in resultat["exemples"]:
                print("    " + _resumer_ligne(ex))
        candidat = {
            "dataset_id": ds["id"],
            "titre": ds["title"],
            "dossier": ds["id"][:30].replace("-", "_"),
            "champ_cp": resultat["champ_cp"],
            "champ_ville": resultat["champ_ville"],
            "champ_iris": resultat.get("champ_iris"),
            "champ_adresse": resultat.get("champ_adresse"),
            "champ_dep": resultat.get("champ_dep"),
            "champ_circonscription": resultat.get("champ_circonscription"),
            "nb_rm": resultat["nb_rm"],
            "last_modified": resultat.get("last_modified", ""),
        }
        decouverte["candidats"].append(candidat)
        print(f"  Ajouté automatiquement aux candidats.")
        print(f"  >>> src/conf/datasets.py : {json.dumps(candidat, ensure_ascii=False)}")
    else:
        # Aucune donnée RM : montrer les premières lignes pour vérification manuelle
        print("  Premières lignes du fichier :")
        for ligne in resultat.get("premieres_lignes", []):
            print("    " + _resumer_ligne(ligne))
        ajout = input("\n  Ajouter quand même aux candidats ? (o/n) ").strip().lower()
        if ajout == "o":
            candidat = {
                "dataset_id": ds["id"],
                "titre": ds["title"],
                "dossier": ds["id"][:30].replace("-", "_"),
                "champ_cp": resultat["champ_cp"],
                "champ_ville": resultat["champ_ville"],
                "champ_iris": resultat.get("champ_iris"),
                "champ_adresse": resultat.get("champ_adresse"),
                "champ_dep": resultat.get("champ_dep"),
                "champ_circonscription": resultat.get("champ_circonscription"),
                "nb_rm": 0,
                "last_modified": resultat.get("last_modified", ""),
            }
            decouverte["candidats"].append(candidat)
            print(f"  Ajouté aux candidats.")
            print(f"  >>> src/conf/datasets.py : {json.dumps(candidat, ensure_ascii=False)}")

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
        d.setdefault("exclusions_termes", [])
        d.setdefault("a_examiner", [])   # backlog de revue différée (découverte automatique)
        d.setdefault("historique", [])   # décisions "exclure"/"ignorer" (onglets /examen)
        return d
    return {"vus": [], "candidats": [], "exclus": [],
            "echecs": [], "echecs_n": {}, "sans_ressource": [],
            "exclusions_termes": [], "a_examiner": [], "historique": []}


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
    """Écriture atomique (fichier temporaire + os.replace) : decouverte.json est réécrit très
    fréquemment (une fois par entrée lors d'un rattrapage en masse, voir
    verifier_ressources_a_examiner()) pendant que le dashboard web le relit en parallèle
    (polling /api/etat, /api/a_examiner) — une écriture en place exposerait un JSON tronqué à un
    lecteur concurrent. os.replace() est atomique sur un même système de fichiers : un lecteur ne
    voit jamais que l'ancienne version complète ou la nouvelle, jamais un état intermédiaire."""
    os.makedirs(os.path.dirname(DECOUVERTE_FILE), exist_ok=True)
    chemin_tmp = DECOUVERTE_FILE + ".tmp"
    with open(chemin_tmp, "w", encoding="utf-8") as f:
        json.dump(decouverte, f, ensure_ascii=False, indent=2)
    os.replace(chemin_tmp, DECOUVERTE_FILE)


def _sauver_service_geo(entry: dict) -> None:
    """Ajoute ou met à jour un service géo dans data/geo_services.json."""
    existing = []
    if os.path.exists(GEO_FILE):
        with open(GEO_FILE, encoding="utf-8") as f:
            existing = json.load(f)
    existing = [e for e in existing if e.get("id") != entry["id"]]
    existing.append(entry)
    with open(GEO_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"  Sauvegardé dans data/geo_services.json ({len(existing)} service(s))")
    print("  Lancez : python3 src/harvest_geo.py")


# ---------------------------------------------------------------------------
# Découverte automatique (non-interactive, pour cron/Jenkins — src/harvest_auto.py)
# ---------------------------------------------------------------------------

def _upsert_a_examiner(a_examiner_par_id: dict, ds: dict, result: dict | None, raison: str) -> None:
    """Ajoute/rafraîchit une entrée du backlog de revue différée (decouverte['a_examiner'])."""
    did = ds["id"]
    result = result or {}
    type_ = result.get("type") or "tabulaire"
    if type_ == "wfs":
        couches = result.get("wfs_layers", [])
    elif type_ == "wms":
        # Dicts complets (nom/titre/bbox_wgs84), pas juste les noms : nécessaire pour générer une
        # carte de prévisualisation (dashboard.py, /examen/carte/<id>) sans refaire un
        # GetCapabilities — wms_couches_dans_rm() retourne déjà exactement cette forme.
        couches = result.get("couches", [])
    else:
        couches = []
    entree = {
        "dataset_id": did,
        "titre": ds.get("title", ""),
        "organisation": (ds.get("organization") or {}).get("name", ""),
        "url": ds.get("_url") or f"https://www.data.gouv.fr/datasets/{did}",
        "type": type_,
        "raison": raison,
        "nb_rm": result.get("nb_rm", 0),
        "description": (ds.get("description") or "")[:500],
        "champs_detectes": {
            "champ_cp": result.get("champ_cp"),
            "champ_ville": result.get("champ_ville"),
            "champ_iris": result.get("champ_iris"),
            "champ_epci": result.get("champ_epci"),
            "champ_adresse": result.get("champ_adresse"),
            "champ_dep": result.get("champ_dep"),
            "champ_circonscription": result.get("champ_circonscription"),
        },
        "couches": couches,
        "service_url": result.get("url") if type_ in ("wfs", "wms") else None,
        "last_modified": ds.get("last_modified", ""),
        "date_ajout": datetime.date.today().isoformat(),
    }

    if type_ == "tabulaire":
        # Classification "sans ressource" gratuite : ds["resources"] est déjà en mémoire (même
        # dict que pre_filtrer() a reçu), donc trouver_ressource_analysable()/_format_analysable()
        # ne coûtent aucun appel réseau supplémentaire ici — contrairement à un vrai téléchargement,
        # ça ne vérifie que le format déclaré, pas que le contenu s'ouvre réellement (un ZIP/XLSX
        # annoncé peut encore s'avérer illisible une fois ouvert ; ce cas est rattrapé par le clic
        # "Analyser" du dashboard, qui reclasse l'entrée si besoin).
        ressource = trouver_ressource_analysable(ds)
        fmt = _format_analysable(ressource) if ressource else None
        entree["ressource_verifiee"] = True
        if ressource is None or fmt is None:
            entree["sans_ressource"] = True
            entree["raison_indisponible"] = "Aucune ressource dans un format pris en charge pour la revue manuelle."
        else:
            entree["sans_ressource"] = False
            entree["raison_indisponible"] = None

    a_examiner_par_id[did] = entree


def rechercher_et_filtrer_auto(decouverte: dict) -> dict:
    """
    Version non-interactive du cycle recherche + pré-filtrage de main() : jamais d'input().
    Réutilise les mêmes fonctions (_paginer, _filtrer_communs, pre_filtrer) pour ne pas
    dupliquer la logique de matching.

    - Datasets tabulaires avec RM détecté (nb_rm > 0) : ajoutés automatiquement à
      decouverte["candidats"], exactement comme en session interactive.
    - Services WFS/WMS avec données RM confirmées (nb_rm > 0 — WFS: features réelles
      téléchargées dans la bbox RM, WMS: couches dont la bbox chevauche RM) : ajoutés
      automatiquement à data/geo_services.json (_sauver_service_geo()), qui alimente
      DATASETS_GEO — les couches non-RM ne sont pas incluses.
    - Services WFS/WMS sans donnée RM (nb_rm == 0) : exclus automatiquement (ajoutés à
      decouverte["exclus"]).
    - Datasets tabulaires ambigus (0 RM détecté) ou en échec d'analyse : accumulés (upsert
      par dataset_id) dans decouverte["a_examiner"] pour revue différée (voir
      resoudre_a_examiner() et la vue dédiée du dashboard).

    Retourne des statistiques de run (pas d'affichage — appelant : src/harvest_auto.py).
    """
    _purger_cache(jours=30)
    decouverte.setdefault("a_examiner", [])

    datasets_trouves: list = []
    ids_trouves: set = set()
    requetes = REQUETES_STRUCTUREES if RECHERCHE_STRUCTUREE else [{"params": {"q": k}} for k in KEYWORDS]
    print(f"[découverte] recherche API : {len(requetes)} requête(s)...", flush=True)
    for requete in requetes:
        resultats, total = _paginer(requete["params"])
        nouveaux = 0
        for ds in resultats:
            if ds["id"] not in ids_trouves:
                datasets_trouves.append(ds)
                ids_trouves.add(ds["id"])
                nouveaux += 1
        print(f"[découverte]   {requete.get('label', requete['params'])} : "
              f"{total} résultat(s), {nouveaux} nouveau(x) (cumul {len(datasets_trouves)})", flush=True)

    candidats_nouveaux = _filtrer_communs(datasets_trouves, decouverte)
    print(f"[découverte] {len(datasets_trouves)} JDD trouvés, {len(candidats_nouveaux)} restants "
          f"après filtres (org/géo/déjà vus/exclus)...", flush=True)

    stats = {"analyses": len(candidats_nouveaux), "ignores": 0,
             "candidats_auto": 0, "geo_auto": 0, "a_examiner": 0, "echecs_analyse": 0}
    if not candidats_nouveaux:
        sauvegarder_decouverte(decouverte)
        return stats

    a_examiner_par_id = {e["dataset_id"]: e for e in decouverte["a_examiner"]}

    total_pf = len(candidats_nouveaux)
    done_pf = 0
    with ThreadPoolExecutor(max_workers=10) as pf_exec:
        future_to_ds = {pf_exec.submit(pre_filtrer, ds): ds for ds in candidats_nouveaux}
        for fut in as_completed(future_to_ds):
            ds = future_to_ds[fut]
            done_pf += 1
            if done_pf % 50 == 0 or done_pf == total_pf:
                print(f"[découverte]   analyse : {done_pf}/{total_pf}...", flush=True)
            try:
                verdict, result = fut.result()
            except Exception as e:
                decouverte["vus"].append(ds["id"])
                stats["echecs_analyse"] += 1
                _upsert_a_examiner(a_examiner_par_id, ds, None, raison=f"analyse échouée : {e}")
                continue

            if verdict == "skip":
                decouverte["vus"].append(ds["id"])
                stats["ignores"] += 1
            elif verdict == "candidat" and result.get("type") == "wfs":
                layers = result.get("wfs_layers", [])
                geo_entry = {
                    "id": ds["id"][:30].replace("-", "_"),
                    "type": "wfs",
                    "url": result.get("url", ""),
                    "couches": layers[:10],
                    "titre": ds["title"],
                    "producteur": (ds.get("organization") or {}).get("name", ""),
                    "dossier": ds["id"][:30].replace("-", "_"),
                    "theme": "environment",
                }
                _sauver_service_geo(geo_entry)
                decouverte["vus"].append(ds["id"])
                stats["geo_auto"] += 1
            elif verdict == "candidat" and result.get("type") == "wms":
                couches_rm = result.get("couches", [])
                geo_entry = {
                    "id": ds["id"][:30].replace("-", "_"),
                    "type": "wms",
                    "url": result.get("url", ""),
                    "couches": couches_rm[:10],
                    "titre": ds["title"],
                    "producteur": (ds.get("organization") or {}).get("name", ""),
                    "dossier": ds["id"][:30].replace("-", "_"),
                    "theme": "environment",
                }
                _sauver_service_geo(geo_entry)
                decouverte["vus"].append(ds["id"])
                stats["geo_auto"] += 1
            elif verdict == "candidat" and result.get("type") not in ("wfs", "wms"):
                decouverte["candidats"].append({
                    "dataset_id": ds["id"],
                    "titre": ds["title"],
                    "dossier": ds["id"][:30].replace("-", "_"),
                    "champ_cp":      result["champ_cp"],
                    "champ_ville":   result["champ_ville"],
                    "champ_iris":    result.get("champ_iris"),
                    "champ_adresse": result.get("champ_adresse"),
                    "champ_siren":   result.get("champ_siren"),
                    "champ_dep":     result.get("champ_dep"),
                    "champ_lat":     result.get("champ_lat"),
                    "champ_lon":     result.get("champ_lon"),
                    "champ_circonscription": result.get("champ_circonscription"),
                    "nb_rm":         result["nb_rm"],
                    "last_modified":  result.get("last_modified", ""),
                })
                decouverte["vus"].append(ds["id"])
                stats["candidats_auto"] += 1
            else:
                if result and result.get("type") in ("wfs", "wms"):
                    # Services géo sans donnée RM confirmée : exclusion automatique.
                    # WMS : nb_rm == 0 → aucune couche ne chevauche la bbox RM.
                    # WFS : nb_rm == 0 → aucun feature RM trouvé dans la bbox.
                    did = ds["id"]
                    if did not in decouverte["exclus"]:
                        decouverte["exclus"].append(did)
                    decouverte["vus"].append(did)
                elif result is not None:
                    _upsert_a_examiner(a_examiner_par_id, ds, result,
                                       raison="0 ligne RM détectée")
                    decouverte["vus"].append(ds["id"])
                else:
                    _upsert_a_examiner(a_examiner_par_id, ds, None,
                                       raison="analyse échouée")
                    decouverte["vus"].append(ds["id"])

    decouverte["a_examiner"] = list(a_examiner_par_id.values())
    stats["a_examiner"] = len(decouverte["a_examiner"])
    sauvegarder_decouverte(decouverte)
    return stats


def _upsert_historique(decouverte: dict, entree: dict, decision: str) -> None:
    """Enregistre un instantané complet de l'entrée a_examiner au moment d'une décision
    terminale (exclure/ignorer) — ces deux décisions retirent l'entrée de a_examiner sans
    laisser de trace ailleurs (contrairement à "candidat"/"ajouter_geo", déjà visibles via
    decouverte["candidats"]/data/geo_services.json). Alimente les onglets « Exclus »/« Ignorés »
    de /examen. Instantané complet (pas juste titre/url) pour que rouvrir_historique() puisse
    reconstruire l'entrée a_examiner à l'identique (carte WMS, colonnes détectées, etc.)."""
    historique = decouverte.setdefault("historique", [])
    historique[:] = [h for h in historique if h["dataset_id"] != entree["dataset_id"]]
    historique.append({
        **entree,
        "decision": decision,
        "date_decision": datetime.date.today().isoformat(),
    })


def rouvrir_historique(decouverte: dict, dataset_id: str) -> bool:
    """Annule une décision "exclure"/"ignorer" : remet l'entrée dans decouverte["a_examiner"]
    telle qu'elle était au moment de la décision, et la retire de decouverte["historique"].
    Pour une réouverture d'exclusion, retire aussi l'id de decouverte["exclus"] — sinon le
    JDD resterait bloqué par le filtre "déjà exclus" (_filtrer_communs()) alors même qu'il est
    de nouveau visible dans le backlog de revue. Retourne False si l'id n'est pas dans
    l'historique, ou si l'entrée est déjà revenue dans a_examiner entre-temps."""
    historique = decouverte.get("historique", [])
    snapshot = next((h for h in historique if h["dataset_id"] == dataset_id), None)
    if snapshot is None:
        return False
    if any(e["dataset_id"] == dataset_id for e in decouverte.get("a_examiner", [])):
        return False
    entree = {k: v for k, v in snapshot.items() if k not in ("decision", "date_decision")}
    decouverte["a_examiner"].append(entree)
    decouverte["historique"] = [h for h in historique if h["dataset_id"] != dataset_id]
    if dataset_id in decouverte.get("exclus", []):
        decouverte["exclus"] = [i for i in decouverte["exclus"] if i != dataset_id]
    sauvegarder_decouverte(decouverte)
    return True


def resoudre_a_examiner(decouverte: dict, dataset_id: str, decision: str,
                         champs_manuels: dict | None = None) -> bool:
    """
    Applique une décision de revue humaine sur une entrée de decouverte["a_examiner"] :
      "exclure"     → faux positif définitif, ajouté à decouverte["exclus"] + instantané dans
                      decouverte["historique"] (onglet « Exclus » de /examen)
      "candidat"    → ajouté quand même à decouverte["candidats"] (nécessite un champ détecté —
                      n'a de sens que pour une entrée tabulaire)
      "ajouter_geo" → service WFS/WMS confirmé par l'utilisateur : ajouté à
                      data/geo_services.json (_sauver_service_geo(), alimente DATASETS_GEO) —
                      n'a de sens que pour une entrée type wfs/wms
      "ignorer"     → retiré du backlog + instantané dans decouverte["historique"] (onglet
                      « Ignorés » de /examen)

    champs_manuels : pour decision="candidat", remplace les champs auto-détectés par un tag
    manuel (voir revue_manuelle_a_examiner()) — dict champ_cp/champ_ville/champ_iris/
    champ_adresse/champ_siren/champ_epci/champ_lat/champ_lon/champ_circonscription/nb_rm.
    None (défaut) = comportement inchangé, utilise entree["champs_detectes"]/entree["nb_rm"].

    Retourne True si l'entrée existait et a été traitée, False sinon (dataset_id inconnu ou
    décision invalide/inapplicable).
    """
    a_examiner = decouverte.get("a_examiner", [])
    entree = next((e for e in a_examiner if e["dataset_id"] == dataset_id), None)
    if entree is None:
        return False

    if decision == "exclure":
        if dataset_id not in decouverte["exclus"]:
            decouverte["exclus"].append(dataset_id)
        _upsert_historique(decouverte, entree, "exclure")
    elif decision == "candidat":
        champs = champs_manuels if champs_manuels is not None else entree.get("champs_detectes", {})
        if not any(champs.get(c) for c in
                   ("champ_cp", "champ_ville", "champ_iris", "champ_adresse", "champ_siren",
                    "champ_epci", "champ_lat", "champ_circonscription")):
            return False
        decouverte["candidats"].append({
            "dataset_id": dataset_id,
            "titre": entree["titre"],
            "dossier": dataset_id[:30].replace("-", "_"),
            "champ_cp": champs.get("champ_cp"),
            "champ_ville": champs.get("champ_ville"),
            "champ_iris": champs.get("champ_iris"),
            "champ_adresse": champs.get("champ_adresse"),
            "champ_siren": champs.get("champ_siren"),
            "champ_epci": champs.get("champ_epci"),
            "champ_dep": champs.get("champ_dep"),
            "champ_lat": champs.get("champ_lat"),
            "champ_lon": champs.get("champ_lon"),
            "champ_circonscription": champs.get("champ_circonscription"),
            "nb_rm": champs.get("nb_rm", entree.get("nb_rm", 0)),
            "last_modified": entree.get("last_modified", ""),
        })
    elif decision == "ajouter_geo":
        if entree.get("type") not in ("wfs", "wms"):
            return False
        # couches peut être une liste de noms simples (WFS, ou anciennes entrées WMS avant cet
        # ajout) ou une liste de dicts {"nom","titre","bbox_wgs84"} (WMS depuis _upsert_a_examiner
        # étendu) — _sauver_service_geo()/DATASETS_GEO n'ont besoin que des noms.
        noms_couches = [c["nom"] if isinstance(c, dict) else c
                        for c in entree.get("couches", [])][:10]
        geo_entry = {
            "id": dataset_id[:30].replace("-", "_"),
            "type": entree["type"],
            "url": entree.get("service_url", ""),
            "couches": noms_couches,
            "titre": entree.get("titre", ""),
            "producteur": entree.get("organisation", ""),
            "dossier": dataset_id[:30].replace("-", "_"),
            "theme": "environment",
        }
        _sauver_service_geo(geo_entry)
    elif decision == "ignorer":
        _upsert_historique(decouverte, entree, "ignorer")
    else:
        return False

    decouverte["a_examiner"] = [e for e in a_examiner if e["dataset_id"] != dataset_id]
    sauvegarder_decouverte(decouverte)
    return True


def resoudre_geo_confirme_en_masse(decouverte: dict) -> int:
    """
    Résout d'un coup toutes les entrées géo de decouverte["a_examiner"] avec nb_rm > 0 —
    même critère que l'auto-bypass de rechercher_et_filtrer_auto() (WFS: features RM
    confirmées, WMS: couches bbox-chevauchant RM). Couvre le reliquat d'entrées confirmées
    détectées avant l'existence de cet auto-bypass. Retourne le nombre d'entrées résolues.
    """
    a_traiter = [e["dataset_id"] for e in decouverte.get("a_examiner", [])
                 if e.get("type") in ("wfs", "wms") and (e.get("nb_rm") or 0) > 0]
    for dataset_id in a_traiter:
        resoudre_a_examiner(decouverte, dataset_id, "ajouter_geo")
    return len(a_traiter)


# alias rétro-compatible (dashboard.py, anciens imports)
resoudre_wfs_confirmes_en_masse = resoudre_geo_confirme_en_masse


def reanalyser_wms_a_examiner(decouverte: dict) -> dict:
    """
    Rattrapage one-shot : re-probe le GetCapabilities de chaque WMS encore dans
    decouverte["a_examiner"] pour déterminer si ses couches chevauchent la bbox RM.
    - nb_couches_rm > 0 → ajouté à data/geo_services.json (couches RM uniquement)
    - nb_couches_rm == 0 → exclu automatiquement
    Nécessaire car les entrées existantes ont été créées avec l'ancien nb_rm=0 hardcodé
    dans analyser_wms(), avant que nb_rm soit aligné sur nb_couches_rm.

    Retourne un dict de stats : {"ajoutes": N, "exclus": N, "echecs": N}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from connectors.analyseurs import analyser_wms
    from connectors.geo_services import nettoyer_url_ogc

    a_examiner = decouverte.get("a_examiner", [])
    wms_entries = [e for e in a_examiner if e.get("type") == "wms"]
    if not wms_entries:
        return {"ajoutes": 0, "exclus": 0, "echecs": 0}

    print(f"[reanalyse WMS] {len(wms_entries)} WMS à re-analyser...", flush=True)
    stats = {"ajoutes": 0, "exclus": 0, "echecs": 0}

    def _reanalyser_un(entry):
        url = entry.get("service_url", "")
        did = entry.get("dataset_id", "")
        titre = entry.get("titre", "")
        if not url:
            return did, None, "pas d'URL"
        try:
            result = analyser_wms(url, dataset_id=did, titre=titre)
            return did, result, None
        except Exception as e:
            return did, None, str(e)

    done = 0
    with ThreadPoolExecutor(max_workers=8) as exc:
        futures = {exc.submit(_reanalyser_un, e): e for e in wms_entries}
        for fut in as_completed(futures):
            done += 1
            if done % 50 == 0 or done == len(wms_entries):
                print(f"[reanalyse WMS]   {done}/{len(wms_entries)}...", flush=True)
            did, result, erreur = fut.result()
            entry = futures[fut]
            if result is None:
                stats["echecs"] += 1
                continue
            nb_rm = result.get("nb_rm", 0)
            if nb_rm > 0:
                couches_rm = result.get("couches", [])
                geo_entry = {
                    "id": did[:30].replace("-", "_"),
                    "type": "wms",
                    "url": result.get("url", ""),
                    "couches": couches_rm[:10],
                    "titre": entry.get("titre", ""),
                    "producteur": entry.get("organisation", ""),
                    "dossier": did[:30].replace("-", "_"),
                    "theme": "environment",
                }
                _sauver_service_geo(geo_entry)
                stats["ajoutes"] += 1
            else:
                if did not in decouverte["exclus"]:
                    decouverte["exclus"].append(did)
                stats["exclus"] += 1

    decouverte["a_examiner"] = [e for e in a_examiner if e.get("type") != "wms"
                                or (e.get("nb_rm") or 0) > 0]
    sauvegarder_decouverte(decouverte)
    print(f"[reanalyse WMS] Terminé : {stats['ajoutes']} ajoutés, "
          f"{stats['exclus']} exclus, {stats['echecs']} échecs", flush=True)
    return stats


def nettoyer_wms_geo_services() -> dict:
    """
    Re-probe les couches WMS de data/geo_services.json avec un GetMap au centre de RM
    pour vérifier qu'elles ont réellement des données à cet endroit. Supprime les couches
    sans données (et les services vides qui en résultent).

    Nécessaire après un import massif basé uniquement sur la bbox, qui ne distingue pas
    un service national (couvre RM) d'un service départemental dont la bbox déclarée
    couvre toute la France mais les données sont limitées.

    Retourne un dict de stats : {"couches_ok": N, "couches_supprimees": N, "services_vides": N}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from connectors.geo_services import wms_probe_donnees_rm, _cache_probes_wms
    _cache_probes_wms.clear()

    GEO_FILE = os.path.join("data", "geo_services.json")
    if not os.path.exists(GEO_FILE):
        return {"couches_ok": 0, "couches_supprimees": 0, "services_vides": 0}

    with open(GEO_FILE, encoding="utf-8") as f:
        entries = json.load(f)

    wms_entries = [e for e in entries if e.get("type") == "wms"]
    if not wms_entries:
        return {"couches_ok": 0, "couches_supprimees": 0, "services_vides": 0}

    total_couches = sum(len(e.get("couches", [])) for e in wms_entries)
    print(f"[nettoyage WMS] {len(wms_entries)} services, {total_couches} couches à vérifier...",
          flush=True)

    def _verifier_couche(entry, couches):
        base_url = entry.get("url", "")
        results = []
        for c in couches:
            nom = c["nom"] if isinstance(c, dict) else c
            if not nom or not base_url:
                results.append((c, True))  # pas de nom → garder (sera vérifié au moisson)
                continue
            ok = wms_probe_donnees_rm(base_url, nom)
            results.append((c, ok))
        return results

    stats = {"couches_ok": 0, "couches_supprimees": 0, "services_vides": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=8) as exc:
        futures = {exc.submit(_verifier_couche, e, e.get("couches", [])): e
                   for e in wms_entries}
        for fut in as_completed(futures):
            done += 1
            if done % 20 == 0 or done == len(wms_entries):
                print(f"[nettoyage WMS]   {done}/{len(wms_entries)}...", flush=True)
            entry = futures[fut]
            results = fut.result()
            couches_ok = [c for c, ok in results if ok]
            couches_ko = [c for c, ok in results if not ok]
            stats["couches_ok"] += len(couches_ok)
            stats["couches_supprimees"] += len(couches_ko)
            entry["couches"] = couches_ok

    # Supprimer les services qui n'ont plus aucune couche
    avant = len(entries)
    entries = [e for e in entries if e.get("type") != "wms" or e.get("couches")]
    stats["services_vides"] = avant - len(entries)

    with open(GEO_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print(f"[nettoyage WMS] Terminé : {stats['couches_ok']} couches OK, "
          f"{stats['couches_supprimees']} supprimées, "
          f"{stats['services_vides']} services vidés", flush=True)
    return stats


def marquer_a_examiner_verifie(decouverte: dict, dataset_id: str, sans_ressource: bool,
                                raison: str | None = None) -> None:
    """
    Persiste le résultat d'une vérification de ressource (disponible ou non) sur une entrée de
    decouverte["a_examiner"], sans la retirer du backlog — alimente la liste séparée "sans
    ressource / inaccessible" de la revue manuelle web (dashboard.py) et évite qu'une entrée déjà
    vérifiée (dans un sens ou dans l'autre) soit re-vérifiée inutilement par un futur passage en
    masse (voir verifier_ressources_a_examiner()). Ne fait rien si l'entrée n'existe plus
    (résolue entre-temps par un autre onglet/session).
    """
    for entree in decouverte.get("a_examiner", []):
        if entree["dataset_id"] == dataset_id:
            entree["ressource_verifiee"] = True
            entree["sans_ressource"] = sans_ressource
            entree["raison_indisponible"] = raison if sans_ressource else None
            sauvegarder_decouverte(decouverte)
            return


def marquer_a_examiner_echec(decouverte: dict, dataset_id: str, message: str) -> None:
    """
    Persiste un échec transitoire (réseau/parsing) du bouton "Analyser" manuel sur une entrée de
    decouverte["a_examiner"] — jusqu'ici ce cas ne laissait aucune trace (contrairement à l'échec
    permanent "aucune ressource exploitable", qui route déjà vers l'onglet « Sans ressource » via
    marquer_a_examiner_verifie()). Fixe "raison" avec le même préfixe "analyse échouée" que la
    cascade automatisée utilise déjà, pour que /examen reclasse l'entrée dans l'onglet « Analyse
    échouée » sans logique de partitionnement supplémentaire côté client.
    """
    for entree in decouverte.get("a_examiner", []):
        if entree["dataset_id"] == dataset_id:
            entree["raison"] = f"analyse échouée (manuel) : {message}"
            sauvegarder_decouverte(decouverte)
            return


def verifier_ressources_a_examiner(decouverte: dict) -> dict:
    """
    Rattrapage en masse pour le backlog a_examiner existant (entrées ajoutées avant que
    _upsert_a_examiner() ne classe automatiquement "sans_ressource" à la découverte) : pour
    chaque entrée tabulaire pas encore vérifiée, récupère les métadonnées data.gouv.fr
    (get_dataset_metadata — pas de téléchargement) et classe via trouver_ressource_analysable()/
    _format_analysable(), exactement comme _upsert_a_examiner() le fait gratuitement pour les
    nouvelles entrées. Persiste au fur et à mesure via marquer_a_examiner_verifie() (une entrée
    déjà vérifiée par ce run ne serait pas perdue si le run est interrompu).

    Retourne {"total": int, "verifies": int, "sans_ressource": int} pour affichage par
    l'appelant (cli.py / dashboard.py).
    """
    from concurrent.futures import ThreadPoolExecutor
    from connectors.datagouv import get_dataset_metadata

    a_verifier = [e for e in decouverte.get("a_examiner", [])
                  if e.get("type", "tabulaire") == "tabulaire" and not e.get("ressource_verifiee")]
    stats = {"total": len(a_verifier), "verifies": 0, "sans_ressource": 0}
    if not a_verifier:
        return stats

    def _verifier_un(entree: dict) -> tuple:
        did = entree["dataset_id"]
        try:
            metadata = get_dataset_metadata(did)
        except Exception:
            return did, None  # échec réseau transitoire — retenté au prochain passage
        ressource = trouver_ressource_analysable(metadata)
        fmt = _format_analysable(ressource) if ressource else None
        if ressource is None or fmt is None:
            return did, (True, "Aucune ressource dans un format pris en charge pour la revue manuelle.")
        return did, (False, None)

    with ThreadPoolExecutor(max_workers=10) as executor:
        for did, resultat in executor.map(_verifier_un, a_verifier):
            if resultat is None:
                continue
            sans_ressource, raison = resultat
            marquer_a_examiner_verifie(decouverte, did, sans_ressource, raison)
            stats["verifies"] += 1
            if sans_ressource:
                stats["sans_ressource"] += 1

    return stats


# ---------------------------------------------------------------------------
# Import de la revue manuelle (dans src/review.py) - re-export des symboles
# publics pour backward-compat (dashboard.py, cli.py).
# ---------------------------------------------------------------------------

from review import (
    _TYPES_VARIABLES,
    _compter_lignes_variable,
    _construire_champs_manuels,
    analyser_apercu_revue,
    analyser_lignes_revue,
    revue_manuelle_a_examiner,
)

# ---------------------------------------------------------------------------
# Évolution 4 : re-analyse des candidats sans champ géo
# ---------------------------------------------------------------------------

def _reanalyser_candidats_sans_champ(decouverte: dict) -> None:
    """Re-analyse les candidats sans champ géo pour détecter SIREN, lat/lon ajoutés depuis."""
    from concurrent.futures import ThreadPoolExecutor
    from connectors.datagouv import get_dataset_metadata

    sans_champ = [
        c for c in decouverte.get("candidats", [])
        if not any(c.get(ch) for ch in ("champ_cp", "champ_ville", "champ_iris", "champ_epci",
                                         "champ_adresse", "champ_siren",
                                         "champ_lat", "champ_circonscription"))
    ]
    if not sans_champ:
        return

    print(f"\n{len(sans_champ)} candidat(s) sans champ géo — re-analyse (SIREN, lat/lon…)")

    def _analyser_un(candidat):
        try:
            meta = get_dataset_metadata(candidat["dataset_id"])
            return candidat, analyser_dataset(meta, verbose=False)
        except Exception:
            return candidat, None

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [(c, ex.submit(_analyser_un, c)) for c in sans_champ]

    modifies = 0
    for candidat, fut in futs:
        try:
            _, result = fut.result()
        except Exception:
            result = None
        if result and any(result.get(ch) for ch in
                          ("champ_cp", "champ_ville", "champ_iris", "champ_epci",
                           "champ_adresse", "champ_siren",
                           "champ_lat", "champ_dep", "champ_circonscription")):
            candidat["champ_cp"] = result["champ_cp"]
            candidat["champ_ville"] = result["champ_ville"]
            candidat["champ_iris"] = result.get("champ_iris")
            candidat["champ_epci"] = result.get("champ_epci")
            candidat["champ_adresse"] = result.get("champ_adresse")
            candidat["champ_siren"] = result.get("champ_siren")
            candidat["champ_dep"] = result.get("champ_dep")
            candidat["champ_lat"] = result.get("champ_lat")
            candidat["champ_lon"] = result.get("champ_lon")
            candidat["champ_circonscription"] = result.get("champ_circonscription")
            candidat["nb_rm"] = result["nb_rm"]
            print(f"  ✓ {candidat['titre'][:60]} — {result['nb_rm']} lignes RM")
            modifies += 1
        else:
            print(f"  - {candidat['titre'][:60]} — champ toujours inconnu")

    if modifies:
        sauvegarder_decouverte(decouverte)
        print(f"  {modifies} candidat(s) mis à jour — lancez harvest_batch.py pour les moissonner.\n")


# ---------------------------------------------------------------------------
# Évolution 3 : harvest automatique en fin de session
# ---------------------------------------------------------------------------

def _harvest_nouveaux_candidats(decouverte: dict, ids_avant_session: set) -> None:
    """Moissonne les candidats confirmés pendant cette session (évite le re-téléchargement grâce au cache)."""
    nouveaux = [
        c for c in decouverte.get("candidats", [])
        if c["dataset_id"] not in ids_avant_session
        and any(c.get(ch) for ch in ("champ_cp", "champ_ville", "champ_iris", "champ_adresse",
                                      "champ_siren", "champ_epci", "champ_lat",
                                      "champ_circonscription"))
    ]
    if not nouveaux:
        return

    from harvest_batch import traiter_candidat as _harvest
    from state import charger_state, sauvegarder_state

    print(f"\n{len(nouveaux)} nouveau(x) candidat(s) avec données RM.")
    choix = input("  Lancer le harvest maintenant ? (o/n) ").strip().lower()
    if choix != "o":
        print("  → Harvest ignoré. Lancez harvest_batch.py quand vous voulez.\n")
        return

    state = charger_state()
    ok, vides, echecs = 0, 0, 0
    for candidat in nouveaux:
        print(f"  {candidat['titre'][:65]}")
        try:
            res = _harvest(candidat, state)
            if res["statut"] in ("ok", "cache"):
                nb = res.get("nb_rm", "?")
                print(f"    → {nb} lignes RM" + (" (cache)" if res["statut"] == "cache" else f" ({res.get('format', '')})"))
                sauvegarder_state(state)
                ok += 1
            elif res["statut"] == "vide":
                print(f"    → 0 lignes RM ({res['raison']})")
                vides += 1
            else:
                print(f"    → échec : {res.get('raison', '')}")
                echecs += 1
        except Exception as e:
            print(f"    → erreur : {e}")
            echecs += 1
    print(f"  Harvest : {ok} OK, {vides} vides, {echecs} échecs.")


# ---------------------------------------------------------------------------
# Boucle interactive principale
# ---------------------------------------------------------------------------

def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _purger_cache(jours=30)
    print("=== Découverte interactive de JDD éligibles ===")
    if RECHERCHE_STRUCTUREE:
        labels = [r["label"] for r in REQUETES_STRUCTUREES]
        print(f"Requêtes : {', '.join(labels)}\n")
    else:
        print(f"Mots-clés recherchés : {', '.join(KEYWORDS)}\n")

    decouverte = charger_decouverte()
    # Snapshot avant session pour identifier les nouveaux candidats (évolution 3)
    ids_candidats_avant_session = {c["dataset_id"] for c in decouverte.get("candidats", [])}
    # Évolution 4 : re-analyser les candidats sans champ géo identifié
    _n_sans_champ = sum(
        1 for c in decouverte.get("candidats", [])
        if not any(c.get(ch) for ch in ("champ_cp", "champ_ville", "champ_iris", "champ_epci",
                                         "champ_adresse", "champ_siren",
                                         "champ_lat", "champ_circonscription"))
    )
    if _n_sans_champ:
        _rep = input(f"\n{_n_sans_champ} candidat(s) sans champ géo — re-analyser maintenant ? (o/N) ").strip().lower()
        if _rep == "o":
            _reanalyser_candidats_sans_champ(decouverte)

    echecs_ids = set(decouverte["echecs"])
    sans_ressource_ids = set(decouverte["sans_ressource"])
    # deja_vus exclut les échecs pour qu'ils ne soient pas filtrés dans les candidats
    deja_vus = set(decouverte["vus"]) - echecs_ids

    # Repropose les analyses échouées en priorité
    echecs_datasets = []
    passer_echecs = False
    if decouverte["echecs"]:
        n = len(decouverte["echecs"])
        print(f"{n} JDD dont l'analyse avait échoué (erreur téléchargement ou parsing).")
        choix_echecs = input(f"  (r)eproposer interactivement  (p)asser jusqu'au prochain run ? ").strip().lower()
        if choix_echecs == "p":
            passer_echecs = True
            print(f"  → {n} JDD passés — ils reviendront au prochain run.\n")
        else:
            echecs_datasets = fetcher_datasets_par_ids(decouverte["echecs"])
            print(f"  → {len(echecs_datasets)} JDD récupérés.\n")

    # --- Choix du point de départ ---
    def _ts(path):
        t = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        age = datetime.datetime.now() - t
        age_str = f"{int(age.total_seconds()//3600)}h" if age.total_seconds() > 3600 else f"{int(age.total_seconds()//60)}min"
        return t.strftime("%d/%m %H:%M"), age_str

    has_api = os.path.exists(RESULTATS_API_FILE)
    has_pf  = os.path.exists(PREFILTRES_FILE)

    print("Point de départ :")
    print("  (n) Nouvelle recherche API + préfiltrage")
    if has_api:
        ts_a, age_a = _ts(RESULTATS_API_FILE)
        print(f"  (a) Résultats API du {ts_a} (il y a {age_a}) + refaire le préfiltrage")
    if has_pf:
        ts_p, age_p = _ts(PREFILTRES_FILE)
        print(f"  (p) Résultats pré-filtrés du {ts_p} (il y a {age_p}) → trier directement")
    options_valides = "n" + ("a" if has_api else "") + ("p" if has_pf else "")
    choix_depart = input(f"Choix [{'/'.join(options_valides)}] : ").strip().lower()
    if choix_depart not in options_valides:
        choix_depart = "n"

    echecs_ids_fetched = {ds["id"] for ds in echecs_datasets}

    def _filtrer(datasets, ignorer_deja_vus=False):
        return _filtrer_communs(datasets, decouverte, ignorer_deja_vus=ignorer_deja_vus,
                                 ids_ignores_supp=echecs_ids_fetched, deja_vus=deja_vus)

    candidats_nouveaux = []

    if choix_depart == "p":
        # Charger directement les résultats pré-filtrés (analyse déjà faite)
        # ignorer_deja_vus=True car ces datasets ont été marqués vus au pré-filtrage
        with open(PREFILTRES_FILE, encoding="utf-8") as f:
            prefiltres_bruts = json.load(f)
        candidats_nouveaux = _filtrer(prefiltres_bruts, ignorer_deja_vus=True)
        _resultats_auto = {}
        print(f"  → {len(candidats_nouveaux)} JDD chargés (après filtres mis à jour).\n")

    else:
        # Recherche API (nouvelle ou depuis cache)
        datasets_trouves = []
        ids_trouves = set()

        if choix_depart == "a":
            with open(RESULTATS_API_FILE, encoding="utf-8") as f:
                datasets_trouves = json.load(f)
            ids_trouves = {ds["id"] for ds in datasets_trouves}
            print(f"  → {len(datasets_trouves)} JDD chargés depuis le cache API.\n")
        else:
            if RECHERCHE_STRUCTUREE:
                for requete in REQUETES_STRUCTUREES:
                    print(f"Recherche : {requete['label']}...")
                    resultats, total = _paginer(requete["params"])
                    print(f"  {total} résultats au total, {len(resultats)} récupérés ({NB_PAGES} pages)")
                    for ds in resultats:
                        if ds["id"] not in ids_trouves:
                            datasets_trouves.append(ds)
                            ids_trouves.add(ds["id"])
            else:
                for keyword in KEYWORDS:
                    print(f"Recherche : « {keyword} »...")
                    resultats, total = rechercher_datasets(keyword)
                    print(f"  {total} résultats au total, {len(resultats)} récupérés ({NB_PAGES} pages)")
                    for ds in resultats:
                        if ds["id"] not in ids_trouves:
                            datasets_trouves.append(ds)
                            ids_trouves.add(ds["id"])
            with open(RESULTATS_API_FILE, "w", encoding="utf-8") as f:
                json.dump(datasets_trouves, f, ensure_ascii=False)

        candidats_nouveaux = _filtrer(datasets_trouves)

        # Analyse automatique : description → en-têtes → analyse RM en parallèle
        if candidats_nouveaux:
            total_pf = len(candidats_nouveaux)
            print(f"\nAnalyse automatique de {total_pf} candidats...", flush=True)
            auto_ajoutes, a_presenter, ignores = [], [], 0
            echecs_pf = []
            done_pf = 0
            with ThreadPoolExecutor(max_workers=10) as pf_exec:
                future_to_ds = {pf_exec.submit(pre_filtrer, ds): ds for ds in candidats_nouveaux}
                for fut in as_completed(future_to_ds):
                    ds = future_to_ds[fut]
                    done_pf += 1
                    print(f"\r  {done_pf}/{total_pf} analysés...", end="", flush=True)
                    try:
                        verdict, result = fut.result()
                    except Exception as e:
                        verdict, result = "presenter", None
                        echecs_pf.append((ds["id"], str(e)))
                    if verdict == "skip":
                        decouverte["vus"].append(ds["id"])
                        ignores += 1
                    elif verdict == "candidat":
                        if result.get("type") in ("wfs", "wms"):
                            # Services géo : pas de colonnes tabulaires, redirect vers DATASETS_GEO
                            a_presenter.append((ds, result))
                        else:
                            candidat = {
                                "dataset_id": ds["id"],
                                "titre": ds["title"],
                                "dossier": ds["id"][:30].replace("-", "_"),
                                "champ_cp":      result["champ_cp"],
                                "champ_ville":   result["champ_ville"],
                                "champ_iris":    result.get("champ_iris"),
                                "champ_adresse": result.get("champ_adresse"),
                                "nb_rm":         result["nb_rm"],
                                "last_modified":  result.get("last_modified", ""),
                            }
                            decouverte["candidats"].append(candidat)
                            decouverte["vus"].append(ds["id"])
                            auto_ajoutes.append((ds, result))
                    else:  # "presenter"
                        a_presenter.append((ds, result))
            print()  # saut de ligne après le \r
            sauvegarder_decouverte(decouverte)
            print(f"  {ignores} sans marqueurs géo → ignorés")
            if echecs_pf:
                print(f"  {len(echecs_pf)} analyse(s) en échec (exception) → à présenter manuellement :")
                for ds_id, raison in echecs_pf:
                    print(f"    ✗ {ds_id} : {raison}")
            print(f"  {len(auto_ajoutes)} avec données RM → ajoutés automatiquement")
            for ds, result in auto_ajoutes:
                print(f"    ✓ {ds['title'][:60]}  ({result['nb_rm']} lignes RM)")
            print(f"  {len(a_presenter)} à examiner manuellement (0 RM ou échec)")
            candidats_nouveaux = [ds for ds, _ in a_presenter]
            # Garder le résultat d'analyse pour éviter de retélécharger lors de l'affichage
            _resultats_auto = {ds["id"]: result for ds, result in a_presenter}
            # Marquer comme vus pour ne pas les re-analyser lors d'une future recherche API
            for ds in candidats_nouveaux:
                if ds["id"] not in decouverte["vus"]:
                    decouverte["vus"].append(ds["id"])
            sauvegarder_decouverte(decouverte)
        else:
            _resultats_auto = {}

        # Sauvegarder les candidats à présenter (pour reprise via option p)
        with open(PREFILTRES_FILE, "w", encoding="utf-8") as f:
            json.dump(candidats_nouveaux, f, ensure_ascii=False)

    # Les échecs passent en premier
    candidats = echecs_datasets + candidats_nouveaux
    print(f"\n{len(candidats)} JDD à examiner", end="")
    if echecs_datasets:
        print(f" (dont {len(echecs_datasets)} ré-analyse(s) échouée(s))", end="")
    print("\n")


    executor = ThreadPoolExecutor(max_workers=10)
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
            ressource = trouver_ressource_analysable(ds)

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
            # Dictionnaire de colonnes → essayer les autres ressources CSV du dataset
            if extrait == "__DICTIONNAIRE__":
                extrait = "(dictionnaire de colonnes)"
                for r in ds.get("resources", []):
                    if r is ressource or (r.get("format") or "").lower() != "csv":
                        continue
                    candidat_extrait = telecharger_extrait_csv(r.get("url", ""))
                    if candidat_extrait and candidat_extrait not in ("__BINAIRE__", "__DICTIONNAIRE__") \
                            and not candidat_extrait.startswith("("):
                        extrait = candidat_extrait
                        ressource = r
                        break
            if extrait == "__BINAIRE__":
                nb_format_non_supporte += 1
                fmt = ressource.get("format", "?").upper()
                if is_echec:
                    print(f"\n  (!) Echec {ds['title'][:50]!r} : "
                          f"fichier binaire déclaré {fmt} — retiré des échecs.")
                    decouverte["echecs"] = [j for j in decouverte["echecs"] if j != did]
                    decouverte["echecs_n"].pop(did, None)
                    sauvegarder_decouverte(decouverte)
                continue
            if extrait.startswith("(Impossible de télécharger"):
                # Si déjà exclu définitivement, on ignore silencieusement
                if did in decouverte["exclus"]:
                    continue
                # Erreur réseau : l'analyse en arrière-plan échouera aussi → enregistrer echec
                afficher_fiche(ds, extrait)
                n = decouverte["echecs_n"].get(did, 0) + 1
                decouverte["echecs_n"][did] = n
                if did not in decouverte["echecs"]:
                    decouverte["echecs"].append(did)
                decouverte["vus"] = [v for v in decouverte["vus"] if v != did]
                if n >= 3:
                    print(f"  URL inaccessible ({n} fois) — skip définitif recommandé.")
                    choix = input("  (s)kip définitif  (p)asser ? ").strip().lower()
                    if choix != "p":
                        decouverte["echecs"] = [v for v in decouverte["echecs"] if v != did]
                        decouverte["echecs_n"].pop(did, None)
                        decouverte["exclus"].append(did)
                        decouverte["vus"].append(did)
                        print("  Skip définitif enregistré.")
                    else:
                        print("  Sera reproposé à la prochaine session.")
                else:
                    print(f"  URL inaccessible ({n}/3) — sera reproposé à la prochaine session.")
                sauvegarder_decouverte(decouverte)
                continue
            afficher_fiche(ds, extrait, resultat=_resultats_auto.get(did))

            # Auto-analyse si le dataset est clairement IRIS
            titre_desc = (ds.get("title", "") + " " + (ds.get("description", "") or "")).lower()
            est_iris = bool(re.search(r'\biris\b', titre_desc))
            if est_iris and not is_echec:
                print("  [IRIS] Analyse automatique lancée en arrière-plan.")
                future = executor.submit(analyser_dataset, ds, False)
                en_cours.append((ds, future))
                print(f"  ({len(en_cours)} analyse(s) en cours.)")
                continue

            if is_echec:
                n_echecs = decouverte["echecs_n"].get(did, 0)
                suffixe = f" — {n_echecs} échec(s) précédent(s)" if n_echecs else ""
                choix = input(
                    f"\n(s)kip définitif  (p)asser  (a)nalyse  (x)exception  (q)uitter ?{suffixe} "
                ).strip().lower()
            else:
                choix = input(
                    "\n(s)kip  (p)asser  (a)nalyse  (x)exception  (q)uitter ? "
                ).strip().lower()

            # Affiche immédiatement les analyses terminées pendant la lecture
            if en_cours:
                traiter_finis()

            if choix == "q":
                break
            elif choix == "p":
                continue  # passe sans enregistrer — reviendra la prochaine session
            elif choix == "x":
                org_name = (ds.get("organization") or {}).get("name", "")
                suggestion = f" [suggestion : {org_name}]" if org_name else ""
                terme = input(f"  Terme à exclure{suggestion} : ").strip()
                if terme:
                    decouverte["exclusions_termes"].append(terme)
                    sauvegarder_decouverte(decouverte)
                    print(f"  Terme {terme!r} ajouté — les JDD contenant ce terme dans le titre ou l'org seront filtrés.")
                continue  # passe le JDD courant, le terme filtrera les suivants
            elif choix == "a":
                future = executor.submit(analyser_dataset, ds, False)
                en_cours.append((ds, future))
                print(f"  Analyse lancée en arrière-plan ({len(en_cours)} en cours).")
                continue  # pas ajouté à vus pour l'instant
            else:
                # skip définitif (s ou autre)
                if is_echec:
                    # Retire des échecs
                    decouverte["echecs"] = [
                        i for i in decouverte["echecs"] if i != ds["id"]
                    ]
                decouverte["exclus"].append(ds["id"])

            decouverte["vus"].append(ds["id"])
            sauvegarder_decouverte(decouverte)

    except KeyboardInterrupt:
        print("\n\nInterruption clavier.")

    # Attend et affiche toutes les analyses restantes (dans l'ordre de complétion)
    if en_cours:
        total_restants = len(en_cours)
        print(f"\n{total_restants} analyse(s) en cours, attente des résultats...")
        print("(Ctrl+C pour abandonner les analyses restantes)\n")
        futures_map = {fut: ds_a for ds_a, fut in en_cours}
        en_attente = set(futures_map.keys())
        terminees = 0
        try:
            while en_attente:
                done, en_attente = wait(en_attente, timeout=15, return_when=FIRST_COMPLETED)
                for fut in done:
                    ds_a = futures_map[fut]
                    terminees += 1
                    print(f"\n  [{terminees}/{total_restants}] Résultat reçu : {ds_a['title'][:50]}")
                    try:
                        resultat = fut.result()
                    except Exception as e:
                        msg = str(e) or type(e).__name__
                        print(f"  Erreur inattendue : {msg}")
                        resultat = None
                    traiter_resultat(ds_a, resultat, decouverte)
                if en_attente:
                    titres = [futures_map[f]["title"][:40] for f in list(en_attente)[:3]]
                    suite = "…" if len(en_attente) > 3 else ""
                    print(f"  [{terminees}/{total_restants}] {len(en_attente)} en cours : {', '.join(titres)}{suite}")
        except KeyboardInterrupt:
            print("\nAbandon des analyses restantes — elles seront reproposées.")

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

    # Évolution 3 : harvest automatique des nouveaux candidats
    _harvest_nouveaux_candidats(decouverte, ids_candidats_avant_session)


if __name__ == "__main__":
    main()
