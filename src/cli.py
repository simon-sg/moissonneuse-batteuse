"""
Point d'entrée unique du pipeline moissonneuse-batteuse.

Usage : python3 src/cli.py

Menu interactif qui guide vers les différentes actions (découverte, moisson
tabulaire/INSEE/géo, catalogue), permet de lancer le pipeline complet en une
fois, et propose des options de purge des données existantes.
"""
import json
import os
import shutil
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discover
import main as moisson_tabulaire
import harvest_batch
import harvest_insee
import harvest_oeb
import harvest_bdnb
import harvest_geo
import catalogue
import publish_rudi
import enrichir_descriptions
import enrichir_organisations
import reanalyser_faux_positifs
from conf.datasets import DATASETS, DATASETS_GEO, DATASETS_INSEE, DATASETS_OEB, DATASETS_BDNB
from connectors.rudi_node import charger_conf_rudi

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CONF_RUDI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json")


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _formater_taille(octets: float) -> str:
    for unite in ("o", "Ko", "Mo", "Go", "To"):
        if octets < 1024:
            return f"{octets:.0f} {unite}" if unite == "o" else f"{octets:.1f} {unite}"
        octets /= 1024
    return f"{octets:.1f} Po"


def _taille_chemin(chemin: str) -> int:
    if os.path.isfile(chemin):
        return os.path.getsize(chemin)
    if not os.path.isdir(chemin):
        return 0
    total = 0
    for racine, _dirs, fichiers in os.walk(chemin):
        for f in fichiers:
            try:
                total += os.path.getsize(os.path.join(racine, f))
            except OSError:
                pass
    return total


def _formater_duree(secs: float) -> str:
    if secs < 60:
        return f"{secs:.1f}s"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def _confirmer(message: str, mot_cle: str | None = None) -> bool:
    """Demande confirmation. Si mot_cle est fourni, exige sa saisie exacte (action très destructrice)."""
    if mot_cle:
        print(f"\n⚠️  {message}")
        saisie = input(f"  Tapez {mot_cle!r} pour confirmer (ou Entrée pour annuler) : ").strip()
        return saisie == mot_cle
    rep = input(f"\n{message} (oui/N) ").strip().lower()
    return rep == "oui"


def _executer(label: str, fn, *args, **kwargs) -> bool:
    """Exécute une étape avec gestion uniforme des erreurs/interruptions — ne tue jamais le menu."""
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    t0 = time.time()
    try:
        fn(*args, **kwargs)
        print(f"\n[{label}] terminé en {_formater_duree(time.time() - t0)}.")
        return True
    except KeyboardInterrupt:
        print(f"\n[{label}] interrompu par l'utilisateur après {_formater_duree(time.time() - t0)}.")
        return False
    except SystemExit as e:
        print(f"\n[{label}] arrêté (code {e.code}) après {_formater_duree(time.time() - t0)}.")
        return False
    except Exception as e:
        print(f"\n[{label}] ERREUR après {_formater_duree(time.time() - t0)} : {e}")
        traceback.print_exc()  # trace complète — indispensable pour diagnostiquer un run cron a posteriori
        return False


def _avec_argv(argv: list, fn, *args, **kwargs):
    """Exécute fn() avec sys.argv temporairement remplacé (pour harvest_insee.py qui lit sys.argv[1:])."""
    ancien = sys.argv
    sys.argv = argv
    try:
        return fn(*args, **kwargs)
    finally:
        sys.argv = ancien


# ---------------------------------------------------------------------------
# Actions de moisson
# ---------------------------------------------------------------------------

def action_decouverte():
    _executer("Découverte interactive", discover.main)


def action_revue_manuelle():
    _executer("Revue manuelle du backlog (a_examiner)", discover.revue_manuelle_a_examiner)


def action_moisson_tabulaire():
    _executer("Moisson tabulaire (data.gouv.fr)", _avec_argv, ["main.py"], moisson_tabulaire.main)


def action_moisson_batch():
    _executer("Moisson batch (candidats découverts)", _avec_argv, ["harvest_batch.py"], harvest_batch.main)


def action_moisson_insee(ids: str | None = None):
    """ids : IDs séparés par des espaces, ou "" pour toutes les publications.
    Si None (appel depuis le menu terminal), demande interactivement."""
    if ids is None:
        ids = input("IDs INSEE à traiter, séparés par des espaces (Entrée = toutes les publications) : ").strip()
    argv = ["harvest_insee.py"] + ids.split()
    _executer("Moisson INSEE", _avec_argv, argv, harvest_insee.main)


def action_moisson_bdnb():
    _executer("Moisson BDNB (bâtiments)", _avec_argv, ["harvest_bdnb.py"], harvest_bdnb.main)


def action_moisson_oeb(ids: str | None = None):
    """ids : IDs OEB séparés par des espaces, ou "" pour tous les JDD configurés."""
    if ids is None:
        ids = input("IDs OEB à traiter, séparés par des espaces (Entrée = tous) : ").strip()
    argv = ["harvest_oeb.py"] + ids.split()
    _executer("Moisson OEB", _avec_argv, argv, harvest_oeb.main)


def action_moisson_geo():
    _executer("Moisson géo (WFS/WMS/OGC API)", _avec_argv, ["harvest_geo.py"], harvest_geo.main)


def action_catalogue():
    _executer("Génération du catalogue", catalogue.main)


def action_publier_rudi():
    _executer("Publication sur le nœud RUDI", publish_rudi.main)


def action_enrichir_descriptions():
    _executer("Enrichissement des descriptions (JDD avec métadonnées vides/quasi vides)",
               enrichir_descriptions.main)


def action_enrichir_organisations():
    _executer("Enrichissement des descriptions de producteurs (nœud RUDI)",
               enrichir_organisations.main)


def action_verifier_backlog_examen():
    def _verifier():
        decouverte = discover.charger_decouverte()
        stats = discover.verifier_ressources_a_examiner(decouverte)
        if stats["total"] == 0:
            print("Aucune entrée à vérifier (tout le backlog tabulaire est déjà classé).")
        else:
            print(f"{stats['verifies']}/{stats['total']} JDD vérifié(s) — "
                  f"{stats['sans_ressource']} sans ressource exploitable.")
    _executer("Vérification des ressources du backlog « à examiner »", _verifier)


def action_menage_rudi():
    def _detecter_et_proposer():
        orphelins = publish_rudi.menage_rudi_one_shot()
        conf = charger_conf_rudi()
        if not conf:
            if orphelins:
                print("src/conf/rudi_node.json absent — suppression des orphelins impossible.")
            return
        if orphelins:
            print(f"\n{len(orphelins)} orphelin(s) détecté(s) — vous pouvez les supprimer du nœud RUDI.")
            if _confirmer("Supprimer ces datasets orphelins du nœud RUDI ?", mot_cle="SUPPRIMER"):
                ok, echecs = publish_rudi.supprimer_datasets(conf, orphelins)
                print(f"\nSupprimé(s) : {ok}, échec(s) : {echecs}")

        inutilisees = publish_rudi.menage_organisations()
        if inutilisees:
            print(f"\n{len(inutilisees)} organisation(s) non utilisée(s) — vous pouvez les supprimer du nœud RUDI.")
            if _confirmer("Supprimer ces organisations non utilisées du nœud RUDI ?", mot_cle="SUPPRIMER"):
                ok, echecs = publish_rudi.supprimer_organisations(conf, inutilisees)
                print(f"\nOrganisations supprimées : {ok}, échec(s) : {echecs}")

    _executer("Ménage RUDI (orphelins + organisations inutilisées)", _detecter_et_proposer)


def action_reanalyser_wms():
    """Re-analyse les WMS du backlog a_examiner : ajoute ceux avec couches RM, exclut les autres."""
    def _reanalyser():
        decouverte = discover.charger_decouverte()
        stats = discover.reanalyser_wms_a_examiner(decouverte)
        if stats["ajoutes"] or stats["exclus"]:
            print(f"\nRelancez : python3 src/harvest_geo.py")

    _executer("Re-analyse des WMS du backlog", _reanalyser)


def action_reanalyser_a_examiner_tabulaire():
    """Re-analyse les JDD tabulaires du backlog a_examiner avec la cascade courante."""
    def _reanalyser():
        decouverte = discover.charger_decouverte()
        stats = discover.reanalyser_a_examiner_tabulaire(decouverte)
        if stats["promus_candidats"]:
            print(f"\nRelancez : python3 src/harvest_batch.py")

    _executer("Re-analyse tabulaire du backlog", _reanalyser)


def action_nettoyer_wms_geo():
    """Re-vérifie les couches WMS de geo_services.json avec un probe GetMap au centre de RM."""
    def _nettoyer():
        stats = discover.nettoyer_wms_geo_services()
        if stats["couches_supprimees"]:
            print(f"\nRelancez : python3 src/harvest_geo.py")

    _executer("Nettoyage des couches WMS de geo_services.json", _nettoyer)


def action_reanalyser_faux_positifs(appliquer_interactif: bool = True):
    """Dry-run du re-filtrage des faux positifs INSEE/CP (collisions dept 35),
    puis application après confirmation. Le dashboard appelle la variante
    non-interactive (dry-run seul, pas d'input())."""
    def _reanalyser():
        code = reanalyser_faux_positifs.executer(appliquer=False)
        if code != 0:
            return
        if not appliquer_interactif:
            print("\n(Dry-run seul — appliquer via l'action du CLI ou "
                  "python3 src/reanalyser_faux_positifs.py --appliquer)")
            return
        if _confirmer("Appliquer le re-filtrage (réécriture des fichiers filtrés + états) ?"):
            reanalyser_faux_positifs.executer(appliquer=True)
        else:
            print("  Application annulée (dry-run seul).")

    _executer("Ré-analyse des faux positifs INSEE/CP (dept 35)", _reanalyser)


# Étapes déterministes du pipeline complet (sans découverte, jamais interactives) —
# réutilisées telles quelles par dashboard.py pour le déclenchement web.
ETAPES_PIPELINE = [
    ("Moisson tabulaire (data.gouv.fr)", _avec_argv, [["main.py"], moisson_tabulaire.main], {}),
    ("Moisson batch (candidats découverts)", _avec_argv, [["harvest_batch.py"], harvest_batch.main], {}),
    ("Moisson INSEE (toutes les publications)", _avec_argv, [["harvest_insee.py"], harvest_insee.main], {}),
    ("Moisson OEB (toutes les publications)", _avec_argv, [["harvest_oeb.py"], harvest_oeb.main], {}),
    ("Moisson BDNB (bâtiments)", _avec_argv, [["harvest_bdnb.py"], harvest_bdnb.main], {}),
    ("Moisson géo (WFS/WMS/OGC API)", _avec_argv, [["harvest_geo.py"], harvest_geo.main], {}),
    ("Génération du catalogue", catalogue.main, [], {}),
    ("Publication sur le nœud RUDI", publish_rudi.main, [], {}),
]


def executer_pipeline_complet(etapes_supplementaires: list | None = None) -> list[tuple[str, bool]]:
    """Exécute ETAPES_PIPELINE (+ étapes optionnelles en tête, ex: découverte) et
    retourne [(label, ok), ...]. Pas d'input() ici — utilisable depuis le web.

    Les 4 étapes de moisson indépendantes (INSEE, OEB, BDNB, géo) sont exécutées
    en parallèle pour réduire la durée totale du pipeline."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    etapes = (etapes_supplementaires or []) + ETAPES_PIPELINE
    resultats = []

    # Identifier les étapes parallèles (INSEE, OEB, BDNB, géo)
    labels_parallèles = {
        "Moisson INSEE (toutes les publications)",
        "Moisson OEB (toutes les publications)",
        "Moisson BDNB (bâtiments)",
        "Moisson géo (WFS/WMS/OGC API)",
    }

    # Séparer : séquentiel (avant) → parallèle → séquentiel (après)
    idx_debut_par = next(
        (i for i, (l, *_) in enumerate(etapes) if l in labels_parallèles), len(etapes)
    )
    idx_fin_par = next(
        (i for i, (l, *_) in enumerate(etapes) if i > idx_debut_par and l not in labels_parallèles), len(etapes)
    )

    def _exec_etape(label, fn, args, kwargs):
        t0 = time.time()
        ok = False
        erreur = None
        print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
        try:
            fn(*args, **kwargs)
            ok = True
        except KeyboardInterrupt:
            print(f"\n[{label}] interrompu par l'utilisateur.")
        except SystemExit as e:
            print(f"\n[{label}] arrêté (code {e.code}).")
        except Exception as e:
            print(f"\n[{label}] ERREUR : {e}")
            traceback.print_exc()
            erreur = str(e)[:500]
        duree = time.time() - t0
        if ok:
            print(f"\n[{label}] terminé en {_formater_duree(duree)}.")
        return label, ok

    # 1. Étapes séquentielles avant le parallèle (tabulaire, batch)
    for label, fn, args, kwargs in etapes[:idx_debut_par]:
        resultats.append(_exec_etape(label, fn, args, kwargs))

    # 2. Étapes parallèles (INSEE || OEB || BDNB || géo)
    if idx_debut_par < idx_fin_par:
        print(f"\n--- Lancement de {idx_fin_par - idx_debut_par} moissons en parallèle ---")
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(_exec_etape, label, fn, args, kwargs): label
                for label, fn, args, kwargs in etapes[idx_debut_par:idx_fin_par]
            }
            for future in as_completed(futures):
                resultats.append(future.result())

    # 3. Étapes séquentielles après le parallèle (catalogue, publication RUDI)
    for label, fn, args, kwargs in etapes[idx_fin_par:]:
        resultats.append(_exec_etape(label, fn, args, kwargs))

    print(f"\n{'=' * 60}\nRésumé du pipeline complet\n{'=' * 60}")
    for label, ok in resultats:
        print(f"  {'✓' if ok else '✗'} {label}")
    return resultats


def action_pipeline_complet():
    print("\n=== Pipeline complet ===")
    print("Enchaîne : tabulaire → batch → INSEE → OEB → BDNB → géo → catalogue → RUDI.")
    print("La découverte n'est pas incluse par défaut (revue manuelle nécessaire).\n")

    etapes_supp = []
    if _confirmer("Inclure une session de découverte interactive avant la moisson ?"):
        etapes_supp.append(("Découverte interactive", discover.main, [], {}))

    executer_pipeline_complet(etapes_supp)


# ---------------------------------------------------------------------------
# État du projet
# ---------------------------------------------------------------------------

def etat_projet() -> dict:
    """Collecte l'état du projet sous forme de données pures (pas d'affichage) —
    utilisé par l'affichage terminal ci-dessous et par dashboard.py (API JSON)."""
    donnees = {
        "datasets_configures": {
            "tabulaire": len(DATASETS), "geo": len(DATASETS_GEO),
            "insee": len(DATASETS_INSEE), "oeb": len(DATASETS_OEB),
            "bdnb": len(DATASETS_BDNB),
        },
        "decouverte": None,
        "etat_moisson": {},
        "rudi_configure": os.path.isfile(CONF_RUDI_FILE),
    }

    chemin_decouverte = os.path.join(DATA_DIR, "decouverte.json")
    if os.path.isfile(chemin_decouverte):
        with open(chemin_decouverte, encoding="utf-8") as f:
            d = json.load(f)
        a_examiner = d.get("a_examiner", [])
        sr = [e for e in a_examiner if e.get("sans_ressource")]
        echec = [e for e in a_examiner if "analyse échouée" in e.get("raison", "")]
        donnees["decouverte"] = {
            "candidats": len(d.get("candidats", [])),
            "vus": len(d.get("vus", [])),
            "exclus": len(d.get("exclus", [])),
            "echecs": len(d.get("echecs", [])),
            "a_examiner": len(a_examiner),
            "a_examiner_sans_ressource": len(sr),
            "a_examiner_analyse_echouee": len(echec),
            "historique": len(d.get("historique", [])),
        }

    for nom_fichier, cle in (("state.json", "tabulaire_batch"), ("state_insee.json", "insee"),
                              ("state_oeb.json", "oeb"), ("state_bdnb.json", "bdnb")):
        chemin = os.path.join(DATA_DIR, nom_fichier)
        if os.path.isfile(chemin):
            with open(chemin, encoding="utf-8") as f:
                s = json.load(f)
            donnees["etat_moisson"][cle] = {
                "total": len(s),
                "rudi_publie": sum(1 for v in s.values() if v.get("rudi_publie")),
            }
        else:
            donnees["etat_moisson"][cle] = None

    chemin_state_geo = os.path.join(DATA_DIR, "state_geo.json")
    if os.path.isfile(chemin_state_geo):
        with open(chemin_state_geo, encoding="utf-8") as f:
            sg = json.load(f)
        couches = {k: v for k, v in sg.items() if not k.startswith("_")}
        # Géo publiés : state_geo["_rudi_publie"] = {dossier: bool}
        geo_rudi = sg.get("_rudi_publie", {})
        donnees["etat_moisson"]["geo"] = {
            "total": len(couches),
            "rudi_publie": sum(1 for v in geo_rudi.values() if v),
        }
    else:
        donnees["etat_moisson"]["geo"] = None

    n_dossiers = sum(
        1 for n in os.listdir(DATA_DIR)
        if n != "cache" and os.path.isdir(os.path.join(DATA_DIR, n))
    )
    taille_cache = _taille_chemin(os.path.join(DATA_DIR, "cache"))
    donnees["donnees"] = {
        "n_dossiers": n_dossiers,
        "taille_data_octets": _taille_chemin(DATA_DIR) - taille_cache,
        "taille_cache_octets": taille_cache,
    }
    return donnees


def action_etat_projet():
    d = etat_projet()
    print(f"\n{'=' * 60}\nÉtat du projet\n{'=' * 60}")
    cfg = d["datasets_configures"]
    print(f"  Configurés : {cfg['tabulaire']} tabulaire(s), {cfg['geo']} géo, "
          f"{cfg['insee']} INSEE, {cfg['oeb']} OEB, {cfg['bdnb']} BDNB")

    if d["decouverte"]:
        dd = d["decouverte"]
        print(f"  Découverte : {dd['candidats']} candidat(s), "
              f"{dd['vus']} JDD vus, {dd['exclus']} exclu(s), {dd['echecs']} échec(s)")
        print(f"  Backlog « à examiner » : {dd['a_examiner']} entrée(s) "
              f"({dd['a_examiner_sans_ressource']} sans ressource, "
              f"{dd['a_examiner_analyse_echouee']} analyse échouée)")
        print(f"  Historique décisions : {dd['historique']}")
    else:
        print("  Découverte : aucun historique (data/decouverte.json absent)")

    for cle, label in (("tabulaire_batch", "tabulaire/batch"), ("insee", "INSEE"),
                        ("oeb", "OEB"), ("bdnb", "BDNB"), ("geo", "géo")):
        em = d["etat_moisson"].get(cle)
        if em:
            print(f"  État moisson {label} : {em['total']} JDD suivi(s), {em['rudi_publie']} publié(s) sur RUDI")
        else:
            print(f"  État moisson {label} : aucun")

    if d["rudi_configure"]:
        print("  Nœud RUDI : configuré (src/conf/rudi_node.json présent)")
    else:
        print("  Nœud RUDI : NON configuré — les publications seront ignorées")

    dn = d["donnees"]
    print(f"  Données moissonnées : {dn['n_dossiers']} dossier(s), {_formater_taille(dn['taille_data_octets'])}")
    print(f"  Cache de téléchargement : {_formater_taille(dn['taille_cache_octets'])}")


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------

_CACHE_TTL_SECS = 7 * 24 * 3600  # 7 jours


def _purger_cache() -> str:
    chemin = os.path.join(DATA_DIR, "cache")
    if os.path.isdir(chemin):
        shutil.rmtree(chemin)
    return "Cache vidé."


def _purger_cache_ancien() -> str:
    chemin = os.path.join(DATA_DIR, "cache")
    if not os.path.isdir(chemin):
        return "Aucun cache à nettoyer."
    maintenant = time.time()
    supprimes = 0
    for f in os.listdir(chemin):
        p = os.path.join(chemin, f)
        if os.path.isfile(p) and maintenant - os.path.getmtime(p) > _CACHE_TTL_SECS:
            os.remove(p)
            supprimes += 1
    return f"{supprimes} fichier(s) de cache antérieur(s) à 7 jours supprimé(s)."


def _purger_etat() -> str:
    n = 0
    for nom in ("state.json", "state_insee.json", "state_oeb.json", "state_bdnb.json", "state_geo.json"):
        chemin = os.path.join(DATA_DIR, nom)
        if os.path.isfile(chemin):
            os.remove(chemin)
            n += 1
    return f"{n} fichier(s) d'état supprimé(s)."


def _purger_sessions_decouverte() -> str:
    n = 0
    for nom in ("derniere_recherche.json", "derniers_prefiltres.json"):
        chemin = os.path.join(DATA_DIR, nom)
        if os.path.isfile(chemin):
            os.remove(chemin)
            n += 1
    return f"{n} fichier(s) de session supprimé(s)."


def _purger_geo_services() -> str:
    chemin = os.path.join(DATA_DIR, "geo_services.json")
    if os.path.isfile(chemin):
        os.remove(chemin)
        return "geo_services.json supprimé."
    return "Rien à supprimer."


def _purger_catalogue() -> str:
    n = 0
    for nom in ("catalogue.json", "catalogue.html"):
        chemin = os.path.join(DATA_DIR, nom)
        if os.path.isfile(chemin):
            os.remove(chemin)
            n += 1
    for racine, _dirs, fichiers in os.walk(DATA_DIR):
        for f in fichiers:
            if f.endswith("_viewer.html") or f.endswith("_map.html") or f == "wms_map.html":
                os.remove(os.path.join(racine, f))
                n += 1
    return f"{n} fichier(s) de catalogue supprimé(s) (régénérables via l'option catalogue)."


def _purger_historique_decouverte() -> str:
    chemin = os.path.join(DATA_DIR, "decouverte.json")
    exclus, exclusions_termes = [], []
    if os.path.isfile(chemin):
        with open(chemin, encoding="utf-8") as f:
            ancien = json.load(f)
        exclus = ancien.get("exclus", [])
        exclusions_termes = ancien.get("exclusions_termes", [])
    nouveau = {
        "vus": [], "candidats": [], "exclus": exclus,
        "echecs": [], "echecs_n": {}, "sans_ressource": [],
        "exclusions_termes": exclusions_termes, "a_examiner": [],
    }
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(nouveau, f, ensure_ascii=False, indent=2)
    chemin_resultats = os.path.join(DATA_DIR, "batch_resultats.json")
    if os.path.isfile(chemin_resultats):
        os.remove(chemin_resultats)
    return (f"decouverte.json réinitialisé — {len(exclus)} exclusion(s) et "
            f"{len(exclusions_termes)} terme(s) d'exclusion conservés.")


def _purger_donnees_moissonnees() -> str:
    n = 0
    for nom in sorted(os.listdir(DATA_DIR)):
        if nom == "cache":
            continue
        chemin = os.path.join(DATA_DIR, nom)
        if os.path.isdir(chemin):
            shutil.rmtree(chemin)
            n += 1
    # Sans ça, le prochain run croirait que rien n'a changé (last_modified inchangé
    # dans state.json) et ne re-moissonnerait rien malgré les dossiers supprimés.
    for nom in ("state.json", "state_insee.json", "state_oeb.json", "state_bdnb.json", "state_geo.json"):
        chemin = os.path.join(DATA_DIR, nom)
        if os.path.isfile(chemin):
            os.remove(chemin)
    return f"{n} dossier(s) de données moissonnées supprimé(s) (+ état de moisson réinitialisé)."


PURGE_ITEMS = [
    {"label": "Cache de téléchargement (intégral)",
     "taille": lambda: _taille_chemin(os.path.join(DATA_DIR, "cache")),
     "purger": _purger_cache,
     "impact": "Re-téléchargé automatiquement au prochain run. Aucune perte de données.",
     "destructeur": False},
    {"label": "Cache de téléchargement (fichiers > 7 jours)",
     "taille": lambda: _taille_chemin(os.path.join(DATA_DIR, "cache")),
     "purger": _purger_cache_ancien,
     "impact": "Supprime les fichiers du cache HTTP non accédés depuis 7 jours. Les fichiers encore utilisés seront re-téléchargés au prochain run.",
     "destructeur": False},
    {"label": "État de moisson (state.json, state_insee.json, state_oeb.json, state_bdnb.json, state_geo.json)",
     "taille": lambda: sum(_taille_chemin(os.path.join(DATA_DIR, n)) for n in
                            ("state.json", "state_insee.json", "state_oeb.json", "state_bdnb.json", "state_geo.json")),
     "purger": _purger_etat,
     "impact": "Force une re-vérification de TOUTES les sources au prochain run (re-téléchargements même si rien n'a changé).",
     "destructeur": False},
    {"label": "Sessions de découverte en attente",
     "taille": lambda: sum(_taille_chemin(os.path.join(DATA_DIR, n))
                            for n in ("derniere_recherche.json", "derniers_prefiltres.json")),
     "purger": _purger_sessions_decouverte,
     "impact": "Force une nouvelle recherche API au prochain lancement de la découverte.",
     "destructeur": False},
    {"label": "Services géo auto-découverts (geo_services.json)",
     "taille": lambda: _taille_chemin(os.path.join(DATA_DIR, "geo_services.json")),
     "purger": _purger_geo_services,
     "impact": "DATASETS_GEO perd les services détectés automatiquement (les entrées manuelles dans datasets.py restent).",
     "destructeur": False},
    {"label": "Catalogue généré (catalogue.json/html + visionneuses/cartes)",
     "taille": lambda: (_taille_chemin(os.path.join(DATA_DIR, "catalogue.json")) +
                         _taille_chemin(os.path.join(DATA_DIR, "catalogue.html"))),
     "purger": _purger_catalogue,
     "impact": "Régénérable via l'option catalogue.",
     "destructeur": False},
    {"label": "Historique de découverte (decouverte.json)",
     "taille": lambda: _taille_chemin(os.path.join(DATA_DIR, "decouverte.json")),
     "purger": _purger_historique_decouverte,
     "impact": "Réinitialise vus/candidats/echecs/sans_ressource/a_examiner. CONSERVE exclus et exclusions_termes (décisions manuelles).",
     "destructeur": False},
    {"label": "TOUTES les données moissonnées (dossiers data/<...>)",
     "taille": lambda: sum(_taille_chemin(os.path.join(DATA_DIR, n)) for n in os.listdir(DATA_DIR)
                            if n != "cache" and os.path.isdir(os.path.join(DATA_DIR, n))),
     "purger": _purger_donnees_moissonnees,
     "impact": "Supprime tous les fichiers téléchargés/filtrés/rudi_metadata.json et l'état associé. "
               "Force un re-téléchargement complet de tout le pipeline (peut être long).",
     "destructeur": True},
]


def menu_purge():
    while True:
        print(f"\n{'=' * 60}\nPurge de données existantes\n{'=' * 60}")
        for i, item in enumerate(PURGE_ITEMS, 1):
            marqueur = "  ⚠️ DESTRUCTEUR" if item["destructeur"] else ""
            print(f"  {i}. {item['label']} ({_formater_taille(item['taille']())}){marqueur}")
        print("  0. Retour au menu principal")

        choix = input("\nChoix (un numéro, ou plusieurs séparés par des virgules) : ").strip()
        if choix in ("0", ""):
            return

        indices = [int(c) - 1 for c in choix.split(",") if c.strip().isdigit()]
        if not indices:
            print("Choix invalide.")
            continue

        for idx in indices:
            if not (0 <= idx < len(PURGE_ITEMS)):
                print(f"  (ignoré : {idx + 1} hors plage)")
                continue
            item = PURGE_ITEMS[idx]
            print(f"\n— {item['label']} —")
            print(f"  Impact : {item['impact']}")
            if item["destructeur"]:
                confirme = _confirmer(f"Confirmer la suppression définitive : {item['label']} ?", mot_cle="SUPPRIMER")
            else:
                confirme = _confirmer(f"Supprimer : {item['label']} ?")
            if confirme:
                print(f"  → {item['purger']()}")
            else:
                print("  Annulé.")


# ---------------------------------------------------------------------------
# Menu principal
# ---------------------------------------------------------------------------

ACTIONS = [
    # --- Moisson ---
    ("1", "Découverte interactive (data.gouv.fr + WFS/WMS)", action_decouverte),
    ("2", "Revue manuelle du backlog (a_examiner)", action_revue_manuelle),
    ("3", "Moisson batch — candidats découverts", action_moisson_batch),
    ("4", "Moisson INSEE — publications directes", action_moisson_insee),
    ("5", "Moisson OEB — Observatoire env. Bretagne", action_moisson_oeb),
    ("6", "Moisson BDNB — bâtiments (DPE, énergie)", action_moisson_bdnb),
    ("7", "Moisson géo — WFS/WMS/OGC API", action_moisson_geo),
    # --- Pipeline & publication ---
    ("8", "Pipeline complet (tabulaire → batch → INSEE → OEB → BDNB → géo → catalogue → RUDI)", action_pipeline_complet),
    ("9", "(Re)générer le catalogue", action_catalogue),
    ("10", "Publier sur le nœud RUDI (rattrapage)", action_publier_rudi),
    ("11", "Enrichir les descriptions vides/quasi vides", action_enrichir_descriptions),
    # --- Maintenance ---
    ("12", "Enrichir les descriptions de producteurs (nœud RUDI)", action_enrichir_organisations),
    ("13", "Vérifier le backlog « à examiner »", action_verifier_backlog_examen),
    ("14", "Re-analyser les WMS du backlog", action_reanalyser_wms),
    ("15", "Re-analyser les JDD tabulaires du backlog (nouvelle cascade)", action_reanalyser_a_examiner_tabulaire),
    ("16", "Nettoyer les WMS de geo_services.json", action_nettoyer_wms_geo),
    ("17", "Ménage RUDI (orphelins + organisations inutilisées)", action_menage_rudi),
    ("18", "Ré-analyser les faux positifs INSEE/CP (dept 35)", action_reanalyser_faux_positifs),
    # --- Données & infos ---
    ("19", "Purger des données existantes", menu_purge),
    ("20", "État du projet", action_etat_projet),
]

SECTIONS = [
    (0, "Moisson"),
    (7, "Pipeline & publication"),
    (11, "Maintenance"),
    (18, "Données & infos"),
]


def menu_principal():
    while True:
        print(f"\n{'=' * 60}")
        print("  Moissonneuse-batteuse — Rennes Métropole")
        print(f"{'=' * 60}")
        idx_section = 0
        for i, (cle, label, _fn) in enumerate(ACTIONS):
            if idx_section < len(SECTIONS) and i == SECTIONS[idx_section][0]:
                print(f"\n  --- {SECTIONS[idx_section][1]} ---")
                idx_section += 1
            print(f"  {cle}. {label}")
        print("\n  0. Quitter")

        choix = input("\nChoix : ").strip()
        if choix == "0":
            print("Au revoir.")
            return

        for cle, _label, fn in ACTIONS:
            if choix == cle:
                fn()
                input("\nAppuyez sur Entrée pour revenir au menu…")
                break
        else:
            print("Choix invalide.")


if __name__ == "__main__":
    try:
        menu_principal()
    except KeyboardInterrupt:
        print("\n\nInterrompu.")
