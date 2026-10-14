"""
Validation automatique des demandes d'organisation en attente côté portail
Strukture — remplace le clic manuel « valider » dans l'UI (rôle MODERATOR,
compte animateur de dev — voir ``REF_ENV_RUDI_LOCAL.md``).

**Réservé au portail local de dev/test** (même garde-fou que
``vider_rudi.purger_noeud_portail()``) : jamais destiné à un portail de
production, où cette validation doit rester un geste humain réfléchi.

Flux API confirmé dans le code source de ``rudi-portal-source``
(``TasksController``, ``OrganizationsController``, ``LinkedProducerController``,
``AbstractTaskServiceImpl``, ``organization-process.bpmn20.xml``,
``linked-producer-process.bpmn20.xml``) :

1. ``POST {url_portail}/authenticate`` (``login``/``password`` animateur,
   form-urlencoded) → le header de réponse ``Authorization`` porte le JWT,
   déjà préfixé ``"Bearer "`` (pas de corps JSON utile).
2. ``GET {url_portail}/strukture/v1/tasks?asAdmin=true`` — ``asAdmin`` est
   obligatoire (sans lui : 500 sec, confirmé en conditions réelles le
   2026-10-14). Le filtre serveur ``status`` correspond à une variable de
   process interne, PAS au champ ``Task.status`` visible dans la réponse —
   lui passer ``"PENDING"`` renvoie silencieusement ``[]``. On filtre donc
   entièrement côté client, sur ``assignee is None`` (jamais claim par
   quelqu'un d'autre) et ``asset.process_definition_key`` dans
   ``PROCESS_SEGMENTS`` (voir point 4 ci-dessous).
3. ``PUT .../{segment}/tasks/{id}/claim`` puis
   ``PUT .../{segment}/tasks/{id}/do-it/validated`` — aucun corps dans les
   deux cas ; ``do-it`` doit être appelé avec le **même** compte que le
   ``claim`` (``AbstractTaskServiceImpl.doIt()`` refuse sinon : « Task is not
   assigned to you »). ``"validated"`` est la seule action positive qui existe
   dans le BPMN (confirmé pour les ``UserTask`` des deux process) ; l'autre
   valeur possible est ``"refused"``.
4. **Deux process BPMN distincts**, chacun avec son propre segment d'URL
   (``PROCESS_SEGMENTS``) :
   - ``organization-process`` → ``/organizations/tasks/...`` (création/
     modification/archivage d'une organisation) ;
   - ``linked-producer-process`` → ``/linked-producers/tasks/...``
     (rattachement/détachement producteur↔organisation).
   Valider une tâche ``organization-process`` de **création** déclenche
   automatiquement, **dans le même appel** ``do-it/validated``
   (``OrganizationWorkflowContext.startAttachOrganizationToProvider()``,
   appelé par le script task BPMN « Demande de déclaration d'organisation
   acceptée »), la création d'une **nouvelle** tâche ``linked-producer-process``
   assignée à personne — c'est la « demande de rattachement » qui reste en
   attente si on ne revalide que les tâches listées *avant* ce premier
   ``do-it``. ``valider_toutes_organisations()`` boucle donc sur plusieurs
   passes (liste → validation → re-liste) jusqu'à ce qu'une passe ne trouve
   plus aucune tâche en attente, pour rattraper ces tâches engendrées en
   cascade.
"""
import os
from urllib.parse import urlparse

from connectors.http import session

HOTES_PORTAIL_LOCAL = {"rudi.localhost", "localhost", "127.0.0.1"}

# process_definition_key -> segment d'URL REST (voir point 4 du docstring module)
PROCESS_SEGMENTS = {
    "organization-process": "organizations",
    "linked-producer-process": "linked-producers",
}

# Nombre maximal de passes liste→validation→re-liste (une tâche organization-process
# validée engendre une nouvelle tâche linked-producer-process à la passe suivante ;
# 2 passes suffisent en pratique, 5 est une marge de sécurité contre une boucle infinie
# si un futur BPMN chaîne davantage de tâches).
MAX_PASSES = 5

CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "conf", "credentials_animateur.json")


class ErreurValidationOrganisations(Exception):
    pass


def _verifier_portail_local(url_portail: str) -> None:
    """Garde-fou dur : ne jamais valider automatiquement sur un portail qui n'est
    pas reconnu comme local — cette action doit rester un geste humain en
    production."""
    hote = urlparse(url_portail).hostname or ""
    if hote not in HOTES_PORTAIL_LOCAL:
        raise ErreurValidationOrganisations(
            f"Portail « {url_portail} » non reconnu comme local ({sorted(HOTES_PORTAIL_LOCAL)}) "
            f"— validation automatique refusée (réservée à un portail de dev/test).")


def charger_credentials_animateur(chemin: str = CREDENTIALS_FILE) -> dict | None:
    """Charge {"login": ..., "password": ...} depuis un fichier local gitignoré.
    None si absent (voir conf/credentials_animateur.example.json pour le modèle)."""
    import json
    if not os.path.isfile(chemin):
        return None
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def se_connecter_animateur(url_portail: str, login: str, password: str) -> str:
    """POST /authenticate — renvoie le header Authorization (déjà "Bearer <jwt>")."""
    r = session.post(
        f"{url_portail.rstrip('/')}/authenticate",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"login": login, "password": password}, timeout=15)
    r.raise_for_status()
    auth = r.headers.get("Authorization") or r.headers.get("authorization")
    if not auth:
        raise ErreurValidationOrganisations(
            "Login animateur : pas de header Authorization dans la réponse.")
    return auth


def _lister_taches_organisation_en_attente(url_portail: str, auth: str) -> list[dict]:
    """``asAdmin=true`` est obligatoire — sans lui, l'endpoint renvoie un 500 sec (testé
    en conditions réelles). Pas de filtre serveur ``status`` : ce paramètre correspond à
    une variable de process interne (``TaskConstants.STATUS``, ex. ``"IN_PROGRESS"``),
    PAS au champ ``Task.status`` technique (``DRAFT``/``PENDING``/...) visible dans la
    réponse JSON — même nom, deux concepts différents ; lui passer ``"PENDING"`` renvoie
    silencieusement une liste vide. Le filtrage se fait donc entièrement côté client, sur
    les deux process BPMN couverts (``PROCESS_SEGMENTS`` : ``organization-process`` ET
    ``linked-producer-process``, voir point 4 du docstring module)."""
    r = session.get(f"{url_portail.rstrip('/')}/strukture/v1/tasks",
                     params={"asAdmin": "true"},
                     headers={"Authorization": auth}, timeout=30)
    r.raise_for_status()
    taches = r.json()
    return [t for t in taches
            if (t.get("asset") or {}).get("process_definition_key") in PROCESS_SEGMENTS
            and t.get("assignee") is None]


def _valider_tache(url_portail: str, auth: str, task: dict) -> None:
    segment = PROCESS_SEGMENTS[(task.get("asset") or {}).get("process_definition_key")]
    base = f"{url_portail.rstrip('/')}/strukture/v1/{segment}/tasks/{task['id']}"
    r = session.put(f"{base}/claim", headers={"Authorization": auth}, timeout=15)
    r.raise_for_status()
    r = session.put(f"{base}/do-it/validated", headers={"Authorization": auth}, timeout=15)
    r.raise_for_status()


def valider_toutes_organisations(url_portail: str, login: str, password: str,
                                 journal=None) -> dict:
    """Valide automatiquement toutes les demandes en attente côté portail, sur les
    deux process BPMN concernés : ``organization-process`` (création/modification/
    archivage d'une organisation) et ``linked-producer-process`` (rattachement/
    détachement producteur↔organisation). Retourne {"total": n, "validees": ok,
    "echecs": n} cumulés sur toutes les passes. Best-effort par tâche : une tâche en
    échec n'empêche pas de traiter les suivantes.

    Boucle en plusieurs passes (liste → validation → re-liste, jusqu'à ``MAX_PASSES``
    ou jusqu'à une passe sans **nouvelle** tâche) car valider une tâche
    ``organization-process`` de création engendre automatiquement une nouvelle tâche
    ``linked-producer-process`` (voir point 4 du docstring module) — invisible dans la
    liste récupérée avant cette validation. Une tâche déjà tentée (succès ou échec)
    n'est jamais retentée à la passe suivante — ``traitees`` évite qu'une tâche
    persistante en échec ne soit indéfiniment reprise jusqu'à ``MAX_PASSES``."""
    journal = journal or print
    _verifier_portail_local(url_portail)
    auth = se_connecter_animateur(url_portail, login, password)
    total = ok = echecs = 0
    traitees: set[str] = set()
    for _passe in range(MAX_PASSES):
        taches = [t for t in _lister_taches_organisation_en_attente(url_portail, auth)
                  if t["id"] not in traitees]
        if not taches:
            break
        journal(f"{len(taches)} demande(s) en attente de validation...")
        total += len(taches)
        for t in taches:
            traitees.add(t["id"])
            nom = (t.get("asset") or {}).get("name") or t["id"]
            try:
                _valider_tache(url_portail, auth, t)
                ok += 1
                journal(f"  ✔ {nom}")
            except Exception as e:
                echecs += 1
                journal(f"  ✗ {nom} : {e}")
    journal(f"\n{ok} validée(s), {echecs} échec(s) sur {total}.")
    return {"total": total, "validees": ok, "echecs": echecs}


def main() -> None:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from rudi_harvest_toolkit.rudi_client import charger_conf_rudi

    conf_rudi = charger_conf_rudi(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json"))
    if not conf_rudi or not conf_rudi.get("url_portail"):
        print("src/conf/rudi_node.json absent ou sans url_portail — validation impossible.")
        return
    creds = charger_credentials_animateur()
    if not creds:
        print(f"{CREDENTIALS_FILE} absent — voir conf/credentials_animateur.example.json.")
        return
    valider_toutes_organisations(conf_rudi["url_portail"], creds["login"], creds["password"])


if __name__ == "__main__":
    main()
