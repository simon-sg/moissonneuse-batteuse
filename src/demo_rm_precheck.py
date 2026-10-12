"""Garde-fous pour publier le pipeline sur un nœud RUDI externe (démo) qui n'est pas le
nœud de dev local et peut être partagé avec d'autres équipes.

Contrairement au ménage générique de ``publish_rudi.py`` (qui compare "tout ce qui est sur
le nœud" à "tout ce qui est en local" — valide seulement si le nœud nous est dédié), ce
module ne suppose jamais que ce qui est sur le nœud nous appartient. Chaque fonction
vérifie explicitement, par ``local_id``/nom d'organisation/nom-ou-email de contact, qu'un
objet n'existe pas déjà avant de le considérer comme nôtre. Rien n'est jamais supprimé
depuis ce module — seulement vérifié/journalisé, pour une revue humaine avant suppression
effective via ``rudi_harvest_toolkit.rudi_client.supprimer_dataset/_organisation/_contact``.

Usage (depuis une instance pointant sur le nœud externe, jamais sur le nœud de dev local) :

    python3 src/demo_rm_precheck.py neutraliser   # après la moisson (rudi_node.json absent),
                                                   # avant tout rattrapage de publication
    python3 src/demo_rm_precheck.py verifier      # avant toute publication (STOP si collision)
    python3 src/demo_rm_precheck.py snapshot      # juste après "verifier", avant de publier
    python3 src/demo_rm_precheck.py enregistrer   # juste après chaque publication réussie
    python3 src/demo_rm_precheck.py diff          # avant un nettoyage, pour revue humaine
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rudi_node_write.connectors.rudi_node_auth import RudiNodeAuth
from rudi_node_write.rudi_node_writer import RudiNodeWriter
from rudi_harvest_toolkit.rudi_client import charger_conf_rudi, noeud_pret

from conf.datasets import DATASETS
from translation.datagouv_to_rudi import _local_id_depuis_dataset_id

CONF_RUDI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CHEMIN_PUBLIES = os.path.join(DATA_DIR, "_publies_par_nous.json")
CHEMIN_ORGS_AVANT = os.path.join(DATA_DIR, "_orgs_avant.json")
CHEMIN_CONTACTS_AVANT = os.path.join(DATA_DIR, "_contacts_avant.json")

# Nom/email du producteur et du contact générique utilisés par les 2 DATASETS MyDataBall,
# une fois le contact réel du JDD IRIS neutralisé (voir plan de démo — ne jamais publier
# l'identité d'une vraie personne sur un nœud externe).
NOM_ORGANISATION = "MyDataBall"
NOM_CONTACT = "MyDataBall"
EMAIL_CONTACT = "mydataball@example.org"

# Dossiers dont le contact moissonné (depuis la source data.gouv.fr) est en réalité
# l'identité d'une vraie personne (nom, email personnel) et non un contact générique du
# producteur — jamais à publier tel quel sur un nœud externe. Trouvé par relecture critique
# (opencode pass 3) : mydataball_iris_2022 porte "Nohn Kanidanan" / un email Gmail personnel,
# confirmé encore exact aujourd'hui sur l'API data.gouv.fr. mydataball_communes n'a lui qu'un
# contact déjà générique, pas listé ici.
CONTACTS_A_NEUTRALISER = {
    "mydataball_iris_2022": [{"contact_name": NOM_CONTACT, "email": EMAIL_CONTACT}],
}


class CollisionDetectee(Exception):
    """Levée quand un pré-check trouve, sur le nœud, un objet qui n'est pas à nous (STOP dur)."""


def _creer_writer(conf: dict) -> RudiNodeWriter:
    auth = RudiNodeAuth(usr=conf["usr"], pwd=conf["pwd"])
    return RudiNodeWriter(pm_url=conf["url"] + "/manager", auth=auth)


def _datasets_cibles() -> list[dict]:
    """Les DATASETS configurés, avec leur local_id déterministe précalculé (même formule
    que translation/datagouv_to_rudi.py — jamais réimplémentée ailleurs)."""
    return [{**d, "local_id": _local_id_depuis_dataset_id(d["dataset_id"])} for d in DATASETS]


def _charger_json(chemin: str):
    if not os.path.isfile(chemin):
        return None
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def _sauvegarder_json(chemin: str, donnees) -> None:
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2)


def neutraliser_contacts_sensibles() -> None:
    """Remplace, dans les rudi_metadata.json déjà écrits sur disque, les contacts connus
    pour porter l'identité d'une vraie personne par le repli générique du producteur.

    Ne touche jamais le nœud — uniquement les fichiers locaux du clone de démo. À lancer
    après la moisson (``python3 src/main.py`` avec ``conf/rudi_node.json`` absent/vidé, pour
    que la publication inline soit un no-op) et avant le rattrapage de publication
    (``python3 src/publish_rudi.py``), jamais après coup : le contact réel ne doit jamais
    atteindre le nœud, même un instant.
    """
    for dossier, contacts_repli in CONTACTS_A_NEUTRALISER.items():
        chemin = os.path.join(DATA_DIR, dossier, "rudi_metadata.json")
        if not os.path.isfile(chemin):
            print(f"  [absent] {chemin} — rien à neutraliser (moisson pas encore lancée ?)")
            continue
        meta = _charger_json(chemin)
        avant = meta.get("contacts")
        if avant == contacts_repli:
            print(f"  [déjà neutralisé] {dossier}")
            continue
        meta["contacts"] = contacts_repli
        _sauvegarder_json(chemin, meta)
        print(f"  [neutralisé] {dossier} : {avant} -> {contacts_repli}")


def verifier_avant_publication(conf: dict) -> None:
    """Tous les pré-checks avant la première écriture sur le nœud externe. Lecture seule.

    Lève CollisionDetectee (ou RuntimeError pour un nœud non prêt/mal configuré) au premier
    problème — ne continue jamais "pour voir". Un re-run sur un dataset déjà publié par nous
    (local_id + global_id connus dans _publies_par_nous.json) n'est pas une collision.
    """
    print("=== Pré-checks avant publication (nœud externe, potentiellement partagé) ===")

    if not noeud_pret(conf):
        raise RuntimeError(f"Nœud non prêt : {conf['url']}/manager/conf ne répond pas 200.")
    print(f"  [OK] nœud joignable : {conf['url']}")

    if not conf.get("url_catalog"):
        raise RuntimeError(
            "rudi_node.json : 'url_catalog' absent — sur un nœud externe, ne jamais laisser "
            "dériver un port 3030 par défaut (c'est la disposition de l'image Docker locale, "
            "probablement fausse ailleurs)."
        )
    print(f"  [OK] url_catalog explicite : {conf['url_catalog']}")

    for dossier, contacts_attendus in CONTACTS_A_NEUTRALISER.items():
        chemin = os.path.join(DATA_DIR, dossier, "rudi_metadata.json")
        meta_locale = _charger_json(chemin)
        if meta_locale is None or meta_locale.get("contacts") != contacts_attendus:
            raise RuntimeError(
                f"{dossier} : le contact local n'est pas (encore) neutralisé "
                f"(attendu {contacts_attendus}, trouvé {meta_locale and meta_locale.get('contacts')}). "
                f"Lancer 'python3 src/demo_rm_precheck.py neutraliser' avant de publier — "
                f"ne jamais publier le contact réel source sur un nœud externe."
            )
    print("  [OK] contacts sensibles connus déjà neutralisés sur disque.")

    writer = _creer_writer(conf)  # échoue ici si l'auth (POST front/login) ne passe pas

    themes = writer.themes
    if not themes:
        print(
            "  [ATTENTION] le nœud ne déclare aucun thème valide — publier_dataset() ne "
            "vérifiera rien côté thème, ce n'est pas une confirmation de compatibilité."
        )
    elif "society" not in themes:
        raise RuntimeError(f"Thème 'society' (utilisé par les DATASETS MyDataBall) absent du nœud : {themes}")
    else:
        print("  [OK] authentification réussie, thème 'society' accepté par le nœud.")

    orgs_existantes = {o.get("organization_name"): o for o in writer.organization_list}
    contacts_existants = writer.contact_list
    publies_connus = _charger_json(CHEMIN_PUBLIES) or {}

    for d in _datasets_cibles():
        local_id = d["local_id"]
        existants = writer.filter_metadata_list({"local_id": local_id})
        if not existants:
            print(f"  [OK] {d['dossier']} : aucun dataset existant pour ce local_id, voie libre.")
            continue
        global_id_trouve = existants[0].get("global_id")
        global_id_connu = (publies_connus.get(local_id) or {}).get("global_id")
        if global_id_connu and global_id_trouve == global_id_connu:
            print(f"  [OK] {d['dossier']} : local_id déjà publié par nous (re-run), global_id={global_id_trouve[:8]}…")
            continue
        raise CollisionDetectee(
            f"{d['dossier']} : un dataset existe déjà sur le nœud pour local_id={local_id} "
            f"(global_id={global_id_trouve}) et ne correspond à aucune publication connue de "
            f"nous (absent ou différent dans {CHEMIN_PUBLIES}). Ce n'est pas à nous — STOP. "
            f"Vérifier avec l'équipe RM avant de continuer : publier écraserait ce dataset."
        )

    if NOM_ORGANISATION in orgs_existantes:
        raise CollisionDetectee(
            f"Une organisation nommée {NOM_ORGANISATION!r} existe déjà sur le nœud "
            f"(organization_id={orgs_existantes[NOM_ORGANISATION].get('organization_id')}) — "
            f"ce n'est pas à nous. publier_dataset() pourrait l'enrichir (caption/summary), ce "
            f"qui reste une écriture sur une donnée tierce. STOP, vérifier avec l'équipe RM."
        )
    print(f"  [OK] aucune organisation {NOM_ORGANISATION!r} existante.")

    for c in contacts_existants:
        if c.get("contact_name") == NOM_CONTACT or c.get("email") == EMAIL_CONTACT:
            raise CollisionDetectee(
                f"Un contact correspondant (nom={NOM_CONTACT!r} ou email={EMAIL_CONTACT!r}) "
                f"existe déjà sur le nœud (contact_id={c.get('contact_id')}) — STOP, vérifier "
                f"avec l'équipe RM avant de continuer."
            )
    print(f"  [OK] aucun contact {NOM_CONTACT!r}/{EMAIL_CONTACT!r} existant.")

    print("=== Tous les pré-checks sont passés — publication autorisée ===")


def snapshot_avant(conf: dict) -> None:
    """Snapshot orgs+contacts juste après verifier_avant_publication(), avant de publier —
    filet de sécurité secondaire pour la revue de nettoyage (le mécanisme principal reste la
    recherche ciblée par local_id de enregistrer_publications())."""
    writer = _creer_writer(conf)
    _sauvegarder_json(CHEMIN_ORGS_AVANT, writer.organization_list)
    _sauvegarder_json(CHEMIN_CONTACTS_AVANT, writer.contact_list)
    print(f"Snapshot écrit : {CHEMIN_ORGS_AVANT}")
    print(f"Snapshot écrit : {CHEMIN_CONTACTS_AVANT}")


def enregistrer_publications(conf: dict) -> dict:
    """À lancer juste après une publication réussie : retrouve le vrai global_id de chaque
    dataset connu par recherche local_id (jamais par diff temporel — immune à l'activité
    d'un tiers sur un nœud partagé), et le journalise pour le nettoyage et les re-runs."""
    writer = _creer_writer(conf)
    publies = _charger_json(CHEMIN_PUBLIES) or {}
    for d in _datasets_cibles():
        local_id = d["local_id"]
        trouves = writer.filter_metadata_list({"local_id": local_id})
        if not trouves:
            print(f"  [ABSENT] {d['dossier']} : pas (encore) trouvé sur le nœud.")
            continue
        global_id = trouves[0]["global_id"]
        publies[local_id] = {"dataset_id": d["dataset_id"], "dossier": d["dossier"], "global_id": global_id}
        print(f"  {d['dossier']} : global_id={global_id}")
    _sauvegarder_json(CHEMIN_PUBLIES, publies)
    return publies


def calculer_diff_nettoyage(conf: dict) -> dict:
    """Calcule ce qui est nettoyable, pour revue humaine avant toute suppression effective.
    Ne supprime jamais rien lui-même."""
    writer = _creer_writer(conf)
    publies = _charger_json(CHEMIN_PUBLIES) or {}
    orgs_avant_ids = {o.get("organization_id") for o in (_charger_json(CHEMIN_ORGS_AVANT) or [])}
    contacts_avant_ids = {c.get("contact_id") for c in (_charger_json(CHEMIN_CONTACTS_AVANT) or [])}

    orgs_nouvelles = [o for o in writer.organization_list if o.get("organization_id") not in orgs_avant_ids]
    contacts_nouveaux = [c for c in writer.contact_list if c.get("contact_id") not in contacts_avant_ids]

    rapport = {
        "datasets_a_supprimer": list(publies.values()),
        "organisations_a_supprimer": orgs_nouvelles,
        "contacts_a_supprimer": contacts_nouveaux,
        "limitations_connues": [
            "Les médias (fichiers) uploadés côté storage ne sont pas supprimables par ce "
            "toolkit — supprimer_dataset() ne retire que la fiche catalog.",
        ],
    }
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return rapport


def _charger_conf_ou_sortir() -> dict:
    conf = charger_conf_rudi(CONF_RUDI_FILE)
    if not conf:
        sys.exit(f"Config introuvable ou invalide : {CONF_RUDI_FILE}")
    return conf


if __name__ == "__main__":
    ACTIONS_SANS_CONF = {"neutraliser": neutraliser_contacts_sensibles}
    ACTIONS_AVEC_CONF = {
        "verifier": verifier_avant_publication,
        "snapshot": snapshot_avant,
        "enregistrer": enregistrer_publications,
        "diff": calculer_diff_nettoyage,
    }
    action = sys.argv[1] if len(sys.argv) > 1 else None
    if action in ACTIONS_SANS_CONF:
        ACTIONS_SANS_CONF[action]()
    elif action in ACTIONS_AVEC_CONF:
        try:
            ACTIONS_AVEC_CONF[action](_charger_conf_ou_sortir())
        except CollisionDetectee as e:
            sys.exit(f"\nSTOP — collision détectée, publication refusée :\n{e}")
    else:
        toutes = list(ACTIONS_SANS_CONF) + list(ACTIONS_AVEC_CONF)
        sys.exit(f"Usage : python3 {sys.argv[0]} <{'|'.join(toutes)}>")
