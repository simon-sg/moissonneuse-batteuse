"""
Rattrapage des entrées de data/geo_services.json « contaminées » par le bug des couches
génériques assignées à plusieurs fiches data.gouv différentes partageant le même GeoServer.

Contexte : avant le scoring par titre (score_titre_couche()/selectionner_couches_pertinentes()
dans connectors/geo_services.py), la détection géo retenait toutes les couches dont la bbox
chevauchait RM. Quand plusieurs fiches data.gouv pointaient vers le même service (ex.
https://data.geopf.fr/wfs/ows, https://geobretagne.fr/geoserver/dreal_b/wfs), chaque entrée
de geo_services.json a reçu le même paquet de couches génériques, sans rapport avec le JDD
qu'elle décrit — d'où des titres différents avec des couches quasi-identiques (Jaccard ~ 1.0).

Ce script :
  1. Détecte les groupes de services partagés suspects via discover.detecter_services_partages().
  2. Re-sonde chaque entrée affectée pour identifier les couches réellement pertinentes pour
     son titre (scoring par titre, seuil plus strict qu'en découverte : SEUIL_RATTRAPAGE_AUTO).
  3. Corrige automatiquement les entrées dont le meilleur score >= SEUIL_RATTRAPAGE_AUTO :
     nouvelle liste de couches, suppression de data/<dossier>/rudi_metadata.json (force la
     régénération par harvest_geo.py), suppression des fichiers .geojson orphelins (WFS),
     invalidation de state_geo["_rudi_publie"][<dossier>] (le JDD devra être republié).
  4. Route les autres vers la revue manuelle : retrait de geo_services.json + ajout au backlog
     decouverte["a_examiner"] (le dossier data/<dossier>/ est laissé tel quel, à purger
     manuellement) — sans toucher aux fichiers ni au nœud RUDI.

Ne publie/dépublie JAMAIS sur le nœud RUDI (pas d'import connectors.rudi_node) : les entrées
déjà publiées avec un contenu probablement erroné sont listées dans le rapport final sous
« ACTIONS MANUELLES REQUISES — dépublication », à traiter séparément via cli.py (menu ménage).

Usage :
  python3 src/reanalyser_geo_services_partages.py            # dry-run : rapport seul, aucune écriture
  python3 src/reanalyser_geo_services_partages.py --appliquer  # écrit réellement
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.geo_services import (
    selectionner_couches_pertinentes,
    wfs_lister_couches,
    wms_couches_dans_rm,
    wms_get_capabilities,
)
from discover import (
    charger_decouverte,
    detecter_services_partages,
    sauvegarder_decouverte,
)
from rudi_harvest_toolkit.state import charger_etat, sauvegarder_etat

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
GEO_FILE = os.path.join(DATA_DIR, "geo_services.json")
STATE_GEO_FILE = os.path.join(DATA_DIR, "state_geo.json")

# Plus strict que le seuil de découverte (0.34 dans selectionner_couches_pertinentes) : un
# titre qui correspond vraiment aux couches du service le dépasse largement ; en dessous, on
# préfère re-router vers la revue humaine plutôt que de risquer un nouveau faux appariement.
SEUIL_RATTRAPAGE_AUTO = 0.5


def _slug_typename(typename: str) -> str:
    """Miroir de harvest_geo._slug_typename : nom de fichier safe pour un typename WFS/OGC."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", typename.split(":")[-1])[:50]


def _resoudre_entree(entry: dict) -> tuple[float, list[str]]:
    """Re-sonde une entrée affectée et retourne (meilleur_score, nouvelles_couches).

    - WMS : GetCapabilities complet, puis couches bbox-chevauchant RM (probe GetMap pour les
      bbox absentes/très larges, sans titre_jdd) ; meilleur score calculé sur ce résultat ;
      couches retenues = wms_couches_dans_rm(..., titre_jdd=entry["titre"]) — la sélection
      canonique, qui peut contenir plusieurs couches cohérentes avec le titre.
    - WFS : GetCapabilities (wfs_lister_couches) → {"nom","titre"}, puis scoring texte seul.
      Pas de probe GetFeature ici (trop coûteux en masse) : la vérification « a des données
      RM » sera refaite par harvest_geo.py au prochain run normal.
    """
    type_ = entry.get("type")
    titre = entry.get("titre", "")
    url = entry.get("url", "")
    if type_ == "wms":
        caps = wms_get_capabilities(url)
        couches_brutes = wms_couches_dans_rm(caps, base_url=url)
        _, meilleur_score = selectionner_couches_pertinentes(couches_brutes, titre)
        nouvelles = wms_couches_dans_rm(caps, base_url=url, titre_jdd=titre)
        nouvelles_couches = [c["nom"] for c in nouvelles]
    elif type_ == "wfs":
        couches = wfs_lister_couches(url)
        retenues, meilleur_score = selectionner_couches_pertinentes(couches, titre)
        nouvelles_couches = [c["nom"] for c in retenues]
    else:
        raise ValueError(f"type de service non géré : {type_!r}")
    return meilleur_score, nouvelles_couches


def _entree_a_examiner(entry: dict, score: float) -> dict:
    """Reconstruit une entrée du backlog a_examiner (format aligné sur _upsert_a_examiner()
    de discover.py pour les services géo) à partir d'une entrée geo_services.json."""
    return {
        "dataset_id": entry.get("id", ""),
        "titre": entry.get("titre", ""),
        "organisation": entry.get("producteur", ""),
        "url": entry.get("url", ""),
        "service_url": entry.get("url", ""),
        "type": entry.get("type", ""),
        "raison": f"service partagé — score de pertinence insuffisant "
                  f"(meilleur score: {score:.2f}), à choisir manuellement",
        "nb_rm": 0,
        "description": "",
        "champs_detectes": {
            "champ_cp": None,
            "champ_ville": None,
            "champ_iris": None,
            "champ_epci": None,
            "champ_adresse": None,
            "champ_dep": None,
            "champ_circonscription": None,
        },
        # Anciennes couches, pour référence humaine (le re-scoring est refait à la revue).
        "couches": entry.get("couches", []),
        "last_modified": "",
        "date_ajout": datetime.date.today().isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--appliquer", action="store_true",
                        help="Écrit réellement (sinon dry-run : aucun fichier modifié)")
    args = parser.parse_args()

    mode = "APPLIQUER" if args.appliquer else "dry-run"
    print(f"=== Rattrapage des services géo partagés ({mode}) ===\n")

    groupes = detecter_services_partages()
    ids_dans_groupe_suspect = {e["id"] for g in groupes for e in g.get("entrees", [])}

    with open(GEO_FILE, encoding="utf-8") as f:
        entries = json.load(f)

    state_geo = charger_etat(STATE_GEO_FILE)
    geo_deja_publie = state_geo.setdefault("_rudi_publie", {})
    decouverte = charger_decouverte() if args.appliquer else None

    # Toutes les entrées de geo_services.json viennent du même pipeline de détection bbox-only
    # (pre-correctif) : même une entrée « isolée » (aucun doublon détecté par Jaccard — ex. un
    # service utilisé par une seule fiche data.gouv connue localement) peut avoir reçu un paquet
    # de couches trop large sans lien avec son titre — c'est le cas du JDD "Recensement de la
    # population en Bretagne - Logement" qui a déclenché ce chantier, et qui n'apparaît dans
    # AUCUN groupe détecté (seule entrée WMS connue pour ce service). On re-score donc TOUTES les
    # entrées, pas seulement celles d'un groupe suspect détecté (qui reste une info de contexte).
    print(f"{len(groupes)} groupe(s) suspect(s) détecté(s) (même URL de base + couches "
          f"quasi-identiques entre titres différents), {len(ids_dans_groupe_suspect)} entrée(s) "
          f"concernée(s) au sein de ces groupes.\n"
          f"Re-analyse de la TOTALITÉ des {len(entries)} entrée(s) de geo_services.json "
          f"(un service partagé non détecté localement — un seul exemplaire connu — reste "
          f"soumis au même bug d'origine). Seuil de correction automatique : "
          f"score >= {SEUIL_RATTRAPAGE_AUTO}.\n")

    n_auto = 0
    n_manuel = 0
    n_echecs = 0
    n_inchangees = 0
    depublications = []

    for entry in entries:
        ident = entry.get("id", "")
        type_ = entry.get("type", "")
        titre = entry.get("titre", "") or "(sans titre)"
        dossier = entry.get("dossier", "") or ident
        anciennes = entry.get("couches", [])
        anciennes_noms = [c["nom"] if isinstance(c, dict) else str(c) for c in anciennes]

        print(f"--- {ident} [{type_}] « {titre} » ---")
        print(f"  url      : {entry.get('url', '')}")
        aff_couches = '; '.join(anciennes_noms[:8])
        print(f"  anciennes couches ({len(anciennes_noms)}) : {aff_couches}"
              + ("…" if len(anciennes_noms) > 8 else ""))

        try:
            meilleur_score, nouvelles_couches = _resoudre_entree(entry)
        except Exception as e:
            n_echecs += 1
            print(f"  ERREUR lors du re-sondage : {e}\n")
            continue
        print(f"  meilleur score vs titre : {meilleur_score:.2f}")

        if set(nouvelles_couches) == set(anciennes_noms):
            n_inchangees += 1
            print("  DECISION : AUCUN CHANGEMENT (couches déjà correctes)\n")
            continue

        if meilleur_score >= SEUIL_RATTRAPAGE_AUTO and nouvelles_couches:
            n_auto += 1
            aff_nouvelles = '; '.join(nouvelles_couches[:8])
            print(f"  DECISION : CORRIGER AUTOMATIQUEMENT ({len(nouvelles_couches)} couche(s) retenue(s))")
            print(f"    nouvelles couches : {aff_nouvelles}"
                  + ("…" if len(nouvelles_couches) > 8 else ""))

            orphelins = []
            if type_ == "wfs":
                gardes = {_slug_typename(c) for c in nouvelles_couches}
                dossier_path = os.path.join(DATA_DIR, dossier)
                if os.path.isdir(dossier_path):
                    orphelins = [os.path.join(dossier, nom) for nom in sorted(os.listdir(dossier_path))
                                 if nom.endswith(".geojson")
                                 and nom[:-len(".geojson")] not in gardes]
                if orphelins:
                    print(f"    à supprimer : {len(orphelins)} fichier(s) .geojson orphelin(s) "
                          f"({'; '.join(orphelins[:4])}…)")

            if args.appliquer:
                entry["couches"] = nouvelles_couches
                rudi_meta = os.path.join(DATA_DIR, dossier, "rudi_metadata.json")
                if os.path.isfile(rudi_meta):
                    os.remove(rudi_meta)
                    print(f"    supprimé data/{dossier}/rudi_metadata.json")
                for chemin_rel in orphelins:
                    os.remove(os.path.join(DATA_DIR, chemin_rel))
                if geo_deja_publie.get(dossier):
                    geo_deja_publie[dossier] = False
                    print(f"    rudi_publie remis à False ({dossier})")
        else:
            n_manuel += 1
            print(f"  DECISION : ROUTER VERS REVUE MANUELLE "
                  f"(score < {SEUIL_RATTRAPAGE_AUTO} ou aucune couche retenue)")
            dossier_path = os.path.join(DATA_DIR, dossier)
            if os.path.isdir(dossier_path):
                print(f"    data/{dossier}/ laissé sur disque (revue manuelle + purge à décider)")
            else:
                print(f"    (pas de dossier local data/{dossier}/)")
            if geo_deja_publie.get(dossier):
                depublications.append((dossier, ident, titre))
            if args.appliquer:
                entry["_a_retirer"] = True
                entree = _entree_a_examiner(entry, meilleur_score)
                a_examiner = decouverte.setdefault("a_examiner", [])
                decouverte["a_examiner"] = [e for e in a_examiner
                                            if e.get("dataset_id") != entree["dataset_id"]]
                decouverte["a_examiner"].append(entree)
                print(f"    ajouté au backlog a_examiner (raison : {entree['raison']})")
        print()

    if args.appliquer:
        entries = [e for e in entries if not e.get("_a_retirer")]
        with open(GEO_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        sauvegarder_etat(STATE_GEO_FILE, state_geo)
        sauvegarder_decouverte(decouverte)
        print(f"{len(entries)} entrée(s) restent dans geo_services.json — fichier réécrit.")

    verbe_auto = "corrigées" if args.appliquer else "à corriger"
    verbe_manuel = "routées" if args.appliquer else "à router"
    print(f"\n=== Terminé : {n_auto} entrée(s) {verbe_auto} automatiquement, "
          f"{n_manuel} {verbe_manuel} vers revue manuelle, {n_inchangees} déjà correctes "
          f"(aucun changement), {n_echecs} échec(s) d'analyse ===")

    if depublications:
        print("\nACTIONS MANUELLES REQUISES — dépublication")
        print("Les entrées suivantes sont (ou seraient, en dry-run) routées vers la revue")
        print("manuelle alors qu'elles sont déjà publiées sur le nœud RUDI (state_geo.json :")
        print("_rudi_publie=true) avec un contenu probablement erroné (couches génériques).")
        print("RIEN n'est dépublié automatiquement sur le nœud RUDI : après relecture,")
        print("dépublier via cli.py → menu « ménage RUDI » (datasets sans contrepartie locale) :")
        for dossier, ident, titre in depublications:
            print(f"  - dossier={dossier} | dataset_id={ident} | « {titre} »")

    if n_auto and not args.appliquer:
        print("\nRelancer avec --appliquer pour corriger réellement "
              "(réécriture de geo_services.json, suppression des fichiers orphelins, "
              "invalidation rudi_publie).")
    elif n_auto or n_manuel:
        print("\nPuis, pour prendre en compte les corrections :")
        print("  python3 src/harvest_geo.py      (re-moissonne les services corrigés)")
        print("  python3 src/catalogue.py        (régénère le catalogue / la visionneuse)")
        print("  python3 src/publish_rudi.py     (republie les JDD démarqués dans state_geo.json)")
    if n_echecs:
        print(f"\n{n_echecs} re-sondage(s) en échec (réseau/parsing) — entrées laissées en place, "
              "à relancer ou examiner manuellement.")


if __name__ == "__main__":
    main()