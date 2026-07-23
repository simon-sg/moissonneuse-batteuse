"""
Publie sur les nœuds RUDI les JDD déjà moissonnés mais pas encore marqués
publiés (`rudi_publie` dans state.json / state_insee.json / state_geo.json).

Travaille uniquement à partir des rudi_metadata.json déjà sur disque : ne
retélécharge ni ne refiltre rien. Utile pour rattraper une publication ratée
(nœud injoignable au moment de la moisson) ou une moisson faite avant la
configuration des nœuds, sans refaire tout le téléchargement.

`rudi_publie` est un dict {nom_noeud: bool} (un booléen hérité est lu comme
{"docker": <valeur>}). Trois états, trois comportements — c'est ce qui permet
d'ajouter un nœud sans lui repousser rétroactivement tout l'historique :

    clé absente  → jamais tenté, hors périmètre  → PAS de rattrapage
    False        → tenté et échoué               → rattrapage
    True         → publié                        → rien à faire

Usage :
    python3 src/publish_rudi.py                      # rattrape tous les nœuds
    python3 src/publish_rudi.py --noeud source       # un seul nœud
    python3 src/publish_rudi.py --noeud source --retroactif   # force l'historique
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connectors.rudi_node import (publier_dataset, charger_conf_rudi, charger_confs_rudi,
                                   toutes_metadonnees_rudi, supprimer_dataset,
                                   supprimer_organisation, _creer_writer)
from conf.datasets import DATASETS_GEO
from state import (charger_etat, sauvegarder_etat, construire_index_dossier,
                   lire_rudi_publie, ecrire_rudi_publie)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_TAB_FILE = os.path.join(DATA_DIR, "state.json")
STATE_INSEE_FILE = os.path.join(DATA_DIR, "state_insee.json")
STATE_OEB_FILE = os.path.join(DATA_DIR, "state_oeb.json")
STATE_BDNB_FILE = os.path.join(DATA_DIR, "state_bdnb.json")
STATE_GEO_FILE = os.path.join(DATA_DIR, "state_geo.json")


def _fichiers_a_uploader(dossier_path: str, rudi_metadata: dict) -> list[str]:
    """Reconstruit la liste ordonnée des fichiers FILE en tête de available_formats
    (avant les entrées SERVICE) — c'est l'ordre attendu par publier_dataset()."""
    fichiers = []
    for media in rudi_metadata.get("available_formats", []):
        if media.get("media_type") != "FILE":
            break
        fichiers.append(os.path.join(dossier_path, media.get("media_name", "")))
    return fichiers


def _noeuds_a_rattraper(rudi_publie: dict, confs: list[dict], retroactif: bool) -> list[dict]:
    """Sélectionne les nœuds pour lesquels ce JDD doit être (re)publié.

    Voir la docstring du module : une clé absente signifie « hors périmètre »
    et n'est rattrapée que sur demande explicite (`--retroactif`)."""
    cibles = []
    for conf in confs:
        etat = rudi_publie.get(conf.get("nom"))
        if etat is True:
            continue
        if etat is False or retroactif:
            cibles.append(conf)
    return cibles


def main(noeuds: list[str] | None = None, retroactif: bool = False) -> None:
    confs = charger_confs_rudi()
    if not confs:
        print("Aucun nœud RUDI configuré (src/conf/rudi_nodes.json ni rudi_node.json) — "
              "impossible de publier.")
        return
    if noeuds:
        confs = [c for c in confs if c.get("nom") in noeuds]
        if not confs:
            print(f"Aucun nœud configuré ne porte le(s) nom(s) {', '.join(noeuds)}.")
            return

    print(f"Nœud(s) ciblé(s) : {', '.join(c.get('nom', '?') for c in confs)}"
          f"{' — mode RÉTROACTIF (inclut les JDD hors périmètre)' if retroactif else ''}\n")

    state_tab = charger_etat(STATE_TAB_FILE)
    state_insee = charger_etat(STATE_INSEE_FILE)
    state_oeb = charger_etat(STATE_OEB_FILE)
    state_bdnb = charger_etat(STATE_BDNB_FILE)
    state_geo = charger_etat(STATE_GEO_FILE)
    # setdefault (et non get) : on veut le dict réellement stocké dans state_geo,
    # pour que les mises à jour faites plus bas soient visibles à la relecture.
    geo_deja_publie = state_geo.setdefault("_rudi_publie", {})
    dossiers_geo = {c["dossier"] for c in DATASETS_GEO}

    # dossier -> ("tabulaire"|"insee"|"oeb"|"bdnb", clé dans l'état correspondant)
    index = construire_index_dossier(
        ("tabulaire", state_tab), ("insee", state_insee),
        ("oeb", state_oeb), ("bdnb", state_bdnb),
    )

    # Mappe source → dict d'état pour la mise à jour rudi_publie
    etats = {"tabulaire": state_tab, "insee": state_insee, "oeb": state_oeb, "bdnb": state_bdnb}

    a_publier = []   # [(dossier, source, cle_etat_ou_None, [confs de nœuds à rattraper])]
    inconnus = []
    for nom in sorted(os.listdir(DATA_DIR)):
        chemin_meta = os.path.join(DATA_DIR, nom, "rudi_metadata.json")
        if not os.path.isfile(chemin_meta):
            continue
        if nom in dossiers_geo:
            # state_geo["_rudi_publie"][dossier] porte directement la valeur (bool hérité ou dict)
            rp = lire_rudi_publie({"rudi_publie": geo_deja_publie.get(nom)})
            cibles = _noeuds_a_rattraper(rp, confs, retroactif)
            if cibles:
                a_publier.append((nom, "geo", None, cibles))
        elif nom in index:
            source, cle = index[nom]
            rp = lire_rudi_publie(etats[source][cle])
            cibles = _noeuds_a_rattraper(rp, confs, retroactif)
            if cibles:
                a_publier.append((nom, source, cle, cibles))
        else:
            inconnus.append(nom)

    if inconnus:
        print(f"{len(inconnus)} dossier(s) avec rudi_metadata.json mais sans correspondance dans "
              f"state.json/state_insee.json/DATASETS_GEO (config supprimée ?) — ignorés, à vérifier manuellement :")
        for nom in inconnus:
            print(f"  ? {nom}")
        print()

    if not a_publier:
        print("Rien à publier — tout est déjà marqué publié sur les nœuds ciblés.")
        return

    nb_pub = sum(len(c) for _, _, _, c in a_publier)
    print(f"=== Publication RUDI — {len(a_publier)} JDD, {nb_pub} publication(s) à faire ===\n")
    ok, echecs = 0, 0
    for dossier, source, cle, cibles in a_publier:
        dossier_path = os.path.join(DATA_DIR, dossier)
        noms_cibles = ", ".join(c.get("nom", "?") for c in cibles)
        print(f"--- {dossier} ({source}) → {noms_cibles} ---")

        if source == "geo":
            rp = lire_rudi_publie({"rudi_publie": geo_deja_publie.get(dossier)})
        else:
            rp = lire_rudi_publie(etats[source][cle])

        for conf_noeud in cibles:
            nom_noeud = conf_noeud.get("nom", "?")
            try:
                # Rechargé pour chaque nœud : publier_dataset() mute rudi_metadata en place
                # (global_id, producer, contacts, available_formats remplacés par les objets
                # du nœud). Repartir du fichier évite de propager l'empreinte d'un nœud sur
                # la fiche envoyée au suivant.
                with open(os.path.join(dossier_path, "rudi_metadata.json"), encoding="utf-8") as f:
                    rudi_metadata = json.load(f)
                fichiers = _fichiers_a_uploader(dossier_path, rudi_metadata)
                publier_dataset(conf=conf_noeud, rudi_metadata=rudi_metadata,
                                fichiers_filtres=fichiers)
                rp[nom_noeud] = True
                ok += 1
            except Exception as e:
                print(f"  [RUDI:{nom_noeud}] ERREUR : {e}")
                rp[nom_noeud] = False
                echecs += 1

        if source == "geo":
            geo_deja_publie[dossier] = rp
        else:
            ecrire_rudi_publie(etats[source][cle], rp)
        print()

    sauvegarder_etat(STATE_TAB_FILE, state_tab)
    sauvegarder_etat(STATE_INSEE_FILE, state_insee)
    sauvegarder_etat(STATE_OEB_FILE, state_oeb)
    sauvegarder_etat(STATE_BDNB_FILE, state_bdnb)
    sauvegarder_etat(STATE_GEO_FILE, state_geo)
    print(f"=== Terminé : {ok} publication(s) réussie(s), {echecs} échec(s) sur {nb_pub} ===")


def menage_rudi_one_shot() -> list[dict]:
    """
    Interroge le nœud RUDI pour lister tous les datasets enregistrés et signale
    ceux qui n'ont plus de correspondance locale (rudi_metadata.json absent).

    Retourne la liste des datasets orphelins (dicts RUDI).

    N'opère que sur le nœud **principal** : le ménage supprime des données, on ne
    l'étend pas implicitement aux autres nœuds (voir action_menage_rudi du CLI).
    """
    conf_rudi = charger_conf_rudi()
    if not conf_rudi:
        print("Aucun nœud RUDI configuré — impossible d'interroger le nœud.")
        return []

    print(f"Récupération de tous les datasets depuis le nœud « {conf_rudi.get('nom', '?')} »…")
    try:
        tous = toutes_metadonnees_rudi(conf_rudi)
    except Exception as e:
        print(f"  ERREUR lors de l'interrogation du nœud : {e}")
        return []

    print(f"  {len(tous)} dataset(s) trouvé(s) sur le nœud.")

    # Collecte les local_id locaux (issus des rudi_metadata.json sur disque)
    locaux_ids: set[str] = set()
    for nom in sorted(os.listdir(DATA_DIR)):
        chemin_meta = os.path.join(DATA_DIR, nom, "rudi_metadata.json")
        if os.path.isfile(chemin_meta):
            with open(chemin_meta, encoding="utf-8") as f:
                meta = json.load(f)
                lid = meta.get("local_id", "")
                if lid:
                    locaux_ids.add(lid)

    # Détection des orphelins : présents sur le nœud, absents en local
    orphelins = [d for d in tous if d.get("local_id", "") not in locaux_ids]

    if not orphelins:
        print("  ✅ Tous les datasets du nœud RUDI ont une contrepartie locale.")
        return []

    print(f"\n⚠️  {len(orphelins)} dataset(s) orphelin(s) détecté(s) sur le nœud RUDI (plus de fichier local) :")
    for d in orphelins:
        titre = d.get("dataset_name", d.get("local_id", "?"))
        lid = d.get("local_id", "?")
        print(f"  • {titre}  (local_id={lid[:40]})")
    return orphelins


def menage_organisations() -> list[dict]:
    """
    Interroge le nœud RUDI pour lister les organisations déclarées mais non
    référencées par aucun dataset (producteur ni éditeur de métadonnées).

    Retourne la liste des organisations non utilisées (dicts RUDI).

    N'opère que sur le nœud **principal**, comme menage_rudi_one_shot().
    """
    conf_rudi = charger_conf_rudi()
    if not conf_rudi:
        print("Aucun nœud RUDI configuré — impossible d'interroger le nœud.")
        return []

    writer = _creer_writer(conf_rudi)
    try:
        toutes = {o["organization_name"]: o for o in writer.organization_list}
        utilisees = {o.get("organization_name") for o in writer.used_organization_list}
    except Exception as e:
        print(f"  ERREUR lors de l'interrogation du nœud : {e}")
        return []

    non_utilisees = [toutes[n] for n in sorted(set(toutes) - utilisees)]

    if not non_utilisees:
        print("  ✅ Toutes les organisations du nœud RUDI sont utilisées par au moins un dataset.")
        return []

    print(f"\n⚠️  {len(non_utilisees)} organisation(s) non utilisée(s) sur le nœud RUDI :")
    for o in non_utilisees:
        print(f"  • {o.get('organization_name', '?')}  (id={o.get('organization_id', '?')[:12]}…)")
    return non_utilisees


def supprimer_datasets(conf: dict, orphelins: list[dict]) -> tuple[int, int]:
    """Supprime les datasets orphelins du nœud RUDI. Retourne (ok, echecs)."""
    ok, echecs = 0, 0
    for d in orphelins:
        if supprimer_dataset(conf, d["global_id"]):
            ok += 1
        else:
            echecs += 1
    return ok, echecs


def supprimer_organisations(conf: dict, organisations: list[dict]) -> tuple[int, int]:
    """Supprime les organisations non utilisées du nœud RUDI. Retourne (ok, echecs)."""
    ok, echecs = 0, 0
    for o in organisations:
        if supprimer_organisation(conf, o["organization_id"]):
            ok += 1
        else:
            echecs += 1
    return ok, echecs


if __name__ == "__main__":
    import argparse

    parseur = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("--noeud", action="append", metavar="NOM",
                         help="Restreint le rattrapage à ce nœud (répétable). "
                              "Par défaut : tous les nœuds configurés.")
    parseur.add_argument("--retroactif", action="store_true",
                         help="Publie aussi les JDD hors périmètre du nœud (clé absente) — "
                              "sert à amorcer un nœud ajouté après coup.")
    args = parseur.parse_args()
    main(noeuds=args.noeud, retroactif=args.retroactif)
