"""
Vidage du nœud RUDI local et de son portail — deux niveaux.

- ``vider_noeud_proprement()`` — procédure **officielle** (celle apprise le 2026-10-14) :
  supprime tous les jeux de données (nœud via l'API manager, portail via Kalim — un
  ``DELETE /kalim/v1/resources/{id}`` est un *déclassement*, pas une destruction : le
  JDD part dans ``rudi_archive``), puis **demande** le détachement (``linked_producer``)
  de toutes les organisations. Chaque demande de détachement passe par sa propre tâche
  BPMN (``UserTask_2``, groupe MODERATOR) : elle n'aboutit qu'après validation d'un
  modérateur dans l'UI Strukture. Les organisations ne sont donc supprimées qu'ensuite
  (``demander_suppression_organisation()`` + ``supprimer_organisation()``) — jamais
  avant, sous peine de ``409`` (contrainte de clé étrangère ``linked_producer``).

  Dernière étape, toujours exécutée : réinitialisation de ``rudi_publie`` dans l'état
  local (``reinitialiser_rudi_publie()``) pour les seuls JDD supprimés du nœud — sans
  ça, le fast-path « rien n'a changé » des scripts de moisson laisserait l'ancien flag
  intact, et un pipeline relancé sur un nœud vidé ne republierait jamais rien.

  En mode ``interactif`` (menu terminal), la fonction s'arrête au milieu pour laisser
  l'utilisateur valider dans l'UI (``input()``) ; sinon (dashboard) elle **sonde** le
  portail en ré-essayant la suppression des organisations encore rattachées, jusqu'à
  validation ou délai maximal.

  **Périmètre — uniquement ce que le pipeline a créé.** Contrairement à la purge radicale
  ci-dessous, cette action ne touche **jamais** les JDD/organisations déposés par d'autres
  utilisateurs : elle ne supprime qu'1) les JDD ayant une contrepartie locale
  (``data/<dossier>/rudi_metadata.json`` — même critère que ``publish_rudi.menage_rudi_one_shot()``)
  et 2) les organisations productrices de ces JDD qui ne sont référencées par aucun JDD
  laissé en place (une organisation partagée avec un JDD tiers est donc épargnée).

- ``purger_noeud_portail()`` — procédure **radicale** documentée (``REF_ENV_RUDI_LOCAL.md``
  § « Procédure de vidage complet nœud + portail ») : sauvegarde, puis suppression directe
  (Mongo du nœud, SQL ``strukture_data``, API Dataverse) **sans passer par le workflow
  BPMN** — donc sans validation modérateur, ni une par une dans l'UI. À réserver au
  repartir-de-zéro (gros volumes). ``provider``/``node_provider`` ne sont jamais touchés
  (raccordement nœud↔portail). Réinitialise aussi ``rudi_publie`` dans **tout** l'état
  local (plus rien n'existe côté nœud/portail, donc pas de scoping à faire).

Journalisation : tout passe par ``journal`` (``print`` par défaut — capturé par le
dashboard web). Aucune fonction ``input()`` hors du mode ``interactif``, pour rester
utilisable depuis le dashboard.
"""
import json
import os
import subprocess
import time

from rudi_harvest_toolkit.rudi_client import (
    charger_conf_rudi, toutes_metadonnees_rudi, lister_organisations,
    supprimer_dataset, supprimer_dataset_portail,
    supprimer_organisation, demander_detachement_producteur,
    demander_suppression_organisation)
from rudi_harvest_toolkit.state import charger_etat, sauvegarder_etat, construire_index_dossier

from connectors.http import session
from connectors.rudi_portal import _DOCKER_DIR

CONF_RUDI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

STATE_TAB_FILE = os.path.join(DATA_DIR, "state.json")
STATE_INSEE_FILE = os.path.join(DATA_DIR, "state_insee.json")
STATE_OEB_FILE = os.path.join(DATA_DIR, "state_oeb.json")
STATE_BDNB_FILE = os.path.join(DATA_DIR, "state_bdnb.json")
STATE_GEO_FILE = os.path.join(DATA_DIR, "state_geo.json")

_FICHIERS_ETAT = {"tabulaire": STATE_TAB_FILE, "insee": STATE_INSEE_FILE,
                  "oeb": STATE_OEB_FILE, "bdnb": STATE_BDNB_FILE}

RACINE_DEPOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS_RUDI_DIR = os.path.join(os.path.dirname(RACINE_DEPOT), "_backups_rudi")

# Étapes de la purge radicale : accès direct aux conteneurs locaux (voir REF_ENV_RUDI_LOCAL.md).
CONTENEUR_MONGO = "rudi-source-mongo"
BASE_MONGO = "rudi_catalog_src"
CONTENEUR_STRUKTURE_DB = "rudiplatform-database-1"
STRUKTURE_DB_USER = "rudi"
STRUKTURE_DB_NAME = "rudi"
CONTENEUR_DATAVERSE_DB = "rudiplatform-dataverse-database-1"
DATAVERSE_DB_USER = "dataverse"
DATAVERSE_DB_NAME = "dataverse"
DATAVERSE_API = "http://localhost:8081/api"

# Intervalle de sondage du dashboard, et délai maximal global par défaut.
DELAI_SONDAGE = 10
ATTENTE_MAX_S = 900


def _executer(cmd: list[str], journal) -> tuple[int, str, str]:
    """Exécute une commande locale (podman/docker) en journalisant sa sortie.
    Ne lève jamais : une commande absente ou un délai dépassé devient un code d'échec."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        journal(f"    commande introuvable : {cmd[0]}")
        return 127, "", "commande introuvable"
    except subprocess.TimeoutExpired:
        journal(f"    délai dépassé ({cmd[0]} …)")
        return 124, "", "délai dépassé"
    sortie = "\n".join(l for l in (r.stdout, r.stderr) if l.strip())
    if sortie.strip():
        journal("    " + sortie.strip().replace("\n", "\n    "))
    return r.returncode, r.stdout, r.stderr


# ---------------------------------------------------------------------------
# Vidage propre (procédure officielle)
# ---------------------------------------------------------------------------

def _supprimer_organisation(conf, org, journal) -> bool:
    """Supprime une organisation (portail puis nœud). ``False`` = encore rattachée ou
    échec (ré-essayable). La copie locale n'est pas supprimée tant que le portail refuse,
    pour ne pas laisser une organisation orpheline sur le nœud."""
    oid = org.get("organization_id")
    nom = org.get("organization_name", oid)
    statut_portail = demander_suppression_organisation(conf, oid)
    if statut_portail is False:
        journal(f"  « {nom} » : suppression portail refusée (détachement non encore validé ?)")
        return False
    if not supprimer_organisation(conf, oid):
        journal(f"  « {nom} » : suppression sur le nœud échouée")
        return False
    return True


def _supprimer_organisations(conf, orgs, journal):
    """Tente la suppression d'une liste d'organisations. Renvoie (ok, echecs, restantes)."""
    ok, echecs, restantes = 0, 0, []
    for org in orgs:
        if _supprimer_organisation(conf, org, journal):
            ok += 1
        else:
            echecs += 1
            restantes.append(org)
    return ok, echecs, restantes


def _supprimer_organisations_avec_attente(conf, orgs, attente_max_s, journal):
    """Sonde le portail jusqu'à ce que toutes les organisations soient détachées (donc
    supprimables) ou que ``attente_max_s`` soit écoulé. Renvoie (supprimees, restantes).
    Utilisé hors mode interactif (dashboard) : pas de pause ``input()``."""
    restantes = list(orgs)
    total_ok = 0
    t0 = time.time()
    journal("En attente de la validation des détachements dans l'UI Strukture …")
    while restantes:
        ok, _echecs, restantes = _supprimer_organisations(conf, restantes, journal)
        total_ok += ok
        if not restantes:
            break
        if time.time() - t0 >= attente_max_s:
            journal(f"  {len(restantes)} organisation(s) encore rattachée(s) après "
                    f"{int(attente_max_s)}s — détachement non validé dans l'UI Strukture ?")
            break
        journal(f"  … {len(restantes)} en attente de validation du détachement "
                f"(nouvel essai dans {DELAI_SONDAGE}s)")
        time.sleep(DELAI_SONDAGE)
    return total_ok, restantes


def _dossiers_locaux() -> dict[str, str]:
    """dossier → ``local_id``, pour tous les ``data/<dossier>/rudi_metadata.json`` locaux."""
    mapping: dict[str, str] = {}
    if not os.path.isdir(DATA_DIR):
        return mapping
    for nom in os.listdir(DATA_DIR):
        chemin = os.path.join(DATA_DIR, nom, "rudi_metadata.json")
        if not os.path.isfile(chemin):
            continue
        try:
            with open(chemin, encoding="utf-8") as f:
                lid = json.load(f).get("local_id")
        except (OSError, ValueError):
            continue
        if lid:
            mapping[nom] = lid
    return mapping


def _local_ids_du_pipeline() -> set[str]:
    """``local_id`` des JDD réellement moissonnés par le pipeline : ceux qui possèdent un
    ``data/<dossier>/rudi_metadata.json`` local. Un JDD déposé sur le nœud par un autre
    utilisateur n'a jamais de contrepartie locale ici — même critère que
    ``publish_rudi.menage_rudi_one_shot()`` (qui liste l'inverse : les « orphelins »)."""
    return set(_dossiers_locaux().values())


def reinitialiser_rudi_publie(local_ids: set[str] | None, journal) -> int:
    """Remet ``rudi_publie`` à ``False`` dans les 5 ``state*.json`` pour que
    ``publish_rudi.py`` retente la publication au prochain passage du pipeline.

    Sans ça, le fast-path « rien n'a changé depuis le dernier run » de chaque script
    de moisson (``last_modified`` inchangé) laisse silencieusement l'ancien flag
    ``rudi_publie: true`` intact — même après avoir vidé le nœud/portail — et
    ``publish_rudi.py`` (qui ne regarde que ce flag) ne republie alors jamais les JDD
    qu'on vient pourtant de faire disparaître de la cible. Ne touche ni
    ``data/cache/`` ni les fichiers déjà filtrés/traduits sur disque : zéro
    retéléchargement, zéro refiltrage, juste une republication directe depuis les
    ``rudi_metadata.json`` déjà présents (voir le docstring de ``publish_rudi.py``).

    ``local_ids=None`` : réinitialise TOUT l'état (purge radicale — plus rien n'existe
    côté nœud/portail). Sinon, restreint aux dossiers locaux correspondant à ces
    ``local_id`` (vidage propre, scope JDD-du-pipeline-supprimés-du-nœud) — n'affecte
    jamais l'état des JDD laissés en place. Retourne le nombre d'entrées modifiées."""
    etats = {source: charger_etat(chemin) for source, chemin in _FICHIERS_ETAT.items()}
    index = construire_index_dossier(*etats.items())
    state_geo = charger_etat(STATE_GEO_FILE)
    geo_publie = state_geo.setdefault("_rudi_publie", {})

    if local_ids is None:
        dossiers_cibles = set(index) | set(geo_publie)
    else:
        mapping = _dossiers_locaux()
        dossiers_cibles = {d for d, lid in mapping.items() if lid in local_ids}

    n = 0
    sources_modifiees = set()
    geo_modifie = False
    for dossier in dossiers_cibles:
        source_cle = index.get(dossier)
        if source_cle:
            source, cle = source_cle
            if etats[source][cle].get("rudi_publie"):
                etats[source][cle]["rudi_publie"] = False
                n += 1
                sources_modifiees.add(source)
        if dossier in geo_publie:
            del geo_publie[dossier]
            n += 1
            geo_modifie = True

    for source in sources_modifiees:
        sauvegarder_etat(_FICHIERS_ETAT[source], etats[source])
    if geo_modifie:
        sauvegarder_etat(STATE_GEO_FILE, state_geo)

    if n:
        journal(f"  {n} entrée(s) d'état repassée(s) « à republier » (rudi_publie) — "
                f"publish_rudi.py les republiera depuis les rudi_metadata.json déjà sur "
                f"disque, sans retéléchargement ni refiltrage.")
    return n


def _producteur_id(metadonnee: dict) -> str | None:
    """``organization_id`` de l'organisation productrice d'un JDD (None si absente)."""
    return (metadonnee.get("producer") or {}).get("organization_id")


def vider_noeud_proprement(conf: dict | None = None, interactif: bool = True,
                           attente_max_s: float = ATTENTE_MAX_S, journal=None) -> dict:
    """Vidage propre : supprime les JDD **du pipeline** (nœud + portail), dépose le
    détachement de leurs organisations productrices puis, après validation modérateur,
    les supprime. Ne touche jamais aux JDD/organisations déposés par d'autres utilisateurs
    (voir le périmètre décrit dans le docstring du module)."""
    journal = journal or print
    conf = conf or charger_conf_rudi(CONF_RUDI_FILE)
    if not conf:
        journal("src/conf/rudi_node.json absent — vidage impossible.")
        return {"erreur": "rudi_node.json absent"}

    resume: dict = {}

    # 0. Périmètre : ne garder que ce que le pipeline a créé.
    locaux_ids = _local_ids_du_pipeline()
    toutes = toutes_metadonnees_rudi(conf)
    a_supprimer = [d for d in toutes if d.get("local_id") in locaux_ids]
    ignorees = len(toutes) - len(a_supprimer)
    if not locaux_ids:
        journal("\n⚠️  Aucune contrepartie locale (data/<dossier>/rudi_metadata.json) — "
                "impossible d'identifier les JDD du pipeline : rien ne sera supprimé.")

    # 1. Jeux de données du pipeline (nœud + portail)
    suffixe = f" — {ignorees} JDD d'autres utilisateurs ignoré(s)" if ignorees else ""
    journal(f"\n{len(a_supprimer)} jeu(x) de données du pipeline à supprimer (nœud + portail)"
            f"{suffixe}…")
    ok = echecs = 0
    for d in a_supprimer:
        gid = d.get("global_id")
        if gid and supprimer_dataset(conf, gid):
            ok += 1
        else:
            echecs += 1
        if gid:
            supprimer_dataset_portail(conf, gid)
    resume["jdd"] = {"total": len(a_supprimer), "supprimes": ok, "echecs": echecs,
                     "ignores": ignorees}

    # 2. Détachement des organisations productrices de ces JDD (validation modérateur
    #    requise ensuite). On épargne toute organisation encore référencée par un JDD
    #    laissé en place (déposé par un tiers) : la supprimer casserait son dataset.
    supprimes_gids = {d.get("global_id") for d in a_supprimer}
    prod_pipeline = {_producteur_id(d) for d in a_supprimer} - {None}
    prod_tiers = {_producteur_id(d) for d in toutes if d.get("global_id") not in supprimes_gids} - {None}
    org_ids = prod_pipeline - prod_tiers
    orgs = [o for o in lister_organisations(conf) if o.get("organization_id") in org_ids]
    journal(f"\n{len(orgs)} organisation(s) du pipeline : demande de détachement du producteur…")
    detaches = 0
    for org in orgs:
        if demander_detachement_producteur(conf, org["organization_id"]):
            detaches += 1
    resume["organisations"] = {"total": len(orgs), "detachements": detaches,
                               "supprimees": 0, "echecs": 0, "en_attente": 0}

    # 2.b/3. Validation modérateur → suppression
    if orgs:
        if interactif:
            input("\nValidez les demandes de détachement dans l'UI Strukture, "
                  "puis appuyez sur Entrée pour supprimer les organisations…")
            ok_orgs, ech_orgs, restantes = _supprimer_organisations(conf, orgs, journal)
        else:
            ok_orgs, restantes = _supprimer_organisations_avec_attente(
                conf, orgs, attente_max_s, journal)
            ech_orgs = len(restantes)
        resume["organisations"].update({
            "supprimees": ok_orgs, "echecs": ech_orgs, "en_attente": len(restantes)})

    # 4. Réinitialisation de l'état local (rudi_publie) des JDD supprimés, pour que le
    #    prochain passage du pipeline les republie au lieu de les fast-pather en silence.
    local_ids_supprimes = {d.get("local_id") for d in a_supprimer} - {None}
    resume["rudi_publie_reinitialises"] = reinitialiser_rudi_publie(local_ids_supprimes, journal)

    return resume


# ---------------------------------------------------------------------------
# Purge radicale (procédure documentée)
# ---------------------------------------------------------------------------

def _dossier_sauvegarde(nom: str) -> str:
    dossier = os.path.join(BACKUPS_RUDI_DIR, f"{time.strftime('%Y-%m-%d')}_{nom}")
    os.makedirs(dossier, exist_ok=True)
    return dossier


def _sauvegarder(journal, nom: str = "purge_auto") -> str:
    """Sauvegarde Mongo (nœud) + Strukture/kalim + Dataverse dans ``_backups_rudi/``.
    Best-effort : une étape en échec est journalisée, les suivantes sont tentées."""
    dossier = _dossier_sauvegarde(nom)
    journal(f"Sauvegarde dans {dossier} …")
    _executer(["podman", "exec", CONTENEUR_MONGO, "sh", "-c",
               f"mongodump --db {BASE_MONGO} --archive=/tmp/rudi_backup.archive --gzip"], journal)
    _executer(["podman", "cp", f"{CONTENEUR_MONGO}:/tmp/rudi_backup.archive",
               os.path.join(dossier, "rudi_catalog_src_backup.archive.gz")], journal)
    _executer(["docker", "exec", CONTENEUR_STRUKTURE_DB, "pg_dump", "-U", STRUKTURE_DB_USER,
               "-d", STRUKTURE_DB_NAME, "-Fc", "-f", "/tmp/strukture_backup.dump"], journal)
    _executer(["docker", "cp", f"{CONTENEUR_STRUKTURE_DB}:/tmp/strukture_backup.dump",
               os.path.join(dossier, "rudi_strukture_kalim_backup.dump")], journal)
    _executer(["docker", "exec", CONTENEUR_DATAVERSE_DB, "pg_dump", "-U", DATAVERSE_DB_USER,
               "-d", DATAVERSE_DB_NAME, "-Fc", "-f", "/tmp/dataverse_backup.dump"], journal)
    _executer(["docker", "cp", f"{CONTENEUR_DATAVERSE_DB}:/tmp/dataverse_backup.dump",
               os.path.join(dossier, "dataverse_backup.dump")], journal)
    return dossier


def _vider_mongo(journal) -> dict:
    script = ("db.metadatas.deleteMany({});"
              "db.media.deleteMany({});"
              "db.organizations.deleteMany({});"
              "db.contacts.deleteMany({});"
              "db.reports.deleteMany({});")
    code, _out, _err = _executer(["podman", "exec", CONTENEUR_MONGO, "mongosh", "--quiet",
                                  BASE_MONGO, "--eval", script], journal)
    return {"ok": code == 0}


def _vider_strukture(journal) -> dict:
    # Ordre imposé par les FK (NO ACTION, pas de cascade) ; provider/node_provider hors périmètre.
    sql = ("BEGIN;"
           "DELETE FROM strukture_data.linked_producer;"
           "DELETE FROM strukture_data.organization_member;"
           "DELETE FROM strukture_data.organization;"
           "COMMIT;")
    code, _out, _err = _executer(["docker", "exec", CONTENEUR_STRUKTURE_DB, "psql",
                                  "-U", STRUKTURE_DB_USER, "-d", STRUKTURE_DB_NAME,
                                  "-v", "ON_ERROR_STOP=1", "-c", sql], journal)
    return {"ok": code == 0}


def _lire_token_dataverse() -> str | None:
    chemin = os.path.join(_DOCKER_DIR, "config", "kalim", "kalim.properties")
    if not os.path.isfile(chemin):
        return None
    with open(chemin, encoding="utf-8") as f:
        for ligne in f:
            if ligne.strip().startswith("dataverse.api.token="):
                return ligne.split("=", 1)[1].strip()
    return None


def _vider_dataverse(journal) -> dict:
    """Détruit tous les datasets Dataverse (rudi_data + rudi_archive) via l'API officielle
    ``/api/datasets/{id}/destroy`` (elle gère Postgres **et** l'index Solr ; ne jamais
    bricoler le SQL Dataverse directement)."""
    token = _lire_token_dataverse()
    if not token:
        journal("  token Dataverse introuvable (config/kalim/kalim.properties) — étape sautée")
        return {"ok": False, "supprimes": 0, "total": 0}
    entetes = {"X-Dataverse-key": token}
    ids = []
    for alias in ("rudi_data", "rudi_archive"):
        try:
            r = session.get(f"{DATAVERSE_API}/dataverses/{alias}/contents",
                            headers=entetes, timeout=30)
            r.raise_for_status()
            for item in r.json().get("data", []):
                if item.get("type") == "dataset" and item.get("id") is not None:
                    ids.append(item["id"])
        except Exception as e:
            journal(f"  liste Dataverse « {alias} » : {e}")
    journal(f"  {len(ids)} dataset(s) Dataverse à détruire…")
    supprimes = 0
    for dsid in ids:
        try:
            r = session.delete(f"{DATAVERSE_API}/datasets/{dsid}/destroy",
                               headers=entetes, timeout=30)
            if r.status_code < 400:
                supprimes += 1
            else:
                journal(f"    destroy {dsid} : HTTP {r.status_code} — {r.text[:120]}")
        except Exception as e:
            journal(f"    destroy {dsid} : {e}")
    return {"ok": supprimes == len(ids), "supprimes": supprimes, "total": len(ids)}


def purger_noeud_portail(sauvegarder: bool = True, journal=None) -> dict:
    """Purge radicale : sauvegarde puis vide le nœud (Mongo), le référentiel d'organisations
    du portail (SQL ``strukture_data``) et les datasets Dataverse — sans validation modérateur."""
    journal = journal or print
    resume: dict = {"sauvegarde": _sauvegarder(journal) if sauvegarder else None}
    journal("\nSuppression de toutes les organisations dans le nœud :")
    resume["mongo"] = _vider_mongo(journal)
    journal("\nSuppression de toutes les organisations dans le portail (Strukture) :")
    resume["strukture"] = _vider_strukture(journal)
    journal("\nSuppression de tous les jeux de données du portail (Dataverse) :")
    resume["dataverse"] = _vider_dataverse(journal)
    journal("\nRéinitialisation de l'état de publication local (rudi_publie) :")
    resume["rudi_publie_reinitialises"] = reinitialiser_rudi_publie(None, journal)
    return resume


def main() -> None:
    import sys
    if len(sys.argv) < 2 or sys.argv[1] not in ("propre", "purge"):
        print("Usage : python3 src/vider_rudi.py [propre|purge]")
        print("  propre : supprime les JDD **du pipeline** (nœud + portail) et demande le détachement de leurs organisations")
        print("  purge  : supprime TOUS les JDD + organisations (nœud + portail) sans validation modérateur")
        return
    if sys.argv[1] == "propre":
        vider_noeud_proprement(interactif=True)
    else:
        purger_noeud_portail(sauvegarder=True)


if __name__ == "__main__":
    main()
