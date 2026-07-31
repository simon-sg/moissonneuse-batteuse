import json
import socket
import subprocess
import time
import uuid
import os
from urllib.parse import urlparse

from rudi_node_write.rudi_node_writer import RudiNodeWriter
from rudi_node_write.connectors.rudi_node_auth import RudiNodeAuth
from rudi_node_write.rudi_types.rudi_media import RudiMediaFile

from connectors.http import session

_CONF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "conf")

# Nom du conteneur Podman du nœud RUDI local (voir moissonneur-master/run_rudi_node.sh).
CONTENEUR_RUDI = "rudinode"


def charger_conf_rudi() -> dict | None:
    """Charge la config du nœud RUDI (src/conf/rudi_node.json), ou None si absente.

    La config est un dict ``url``/``url_catalog``/``usr``/``pwd``. ``url_catalog`` est
    facultatif : voir ``url_catalog()`` pour le repli.
    """
    chemin = os.path.join(_CONF_DIR, "rudi_node.json")
    if not os.path.isfile(chemin):
        return None
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Cycle de vie du conteneur Podman (statut / démarrage / arrêt)
# ---------------------------------------------------------------------------

def statut_conteneur(nom: str = CONTENEUR_RUDI) -> dict:
    """Interroge Podman sur l'état d'un conteneur.

    Retourne {"podman_installe": bool, "existe": bool, "etat": str|None}.
    Ne lève jamais — podman absent, conteneur inexistant ou délai dépassé
    sont tous des résultats normaux à afficher, pas des erreurs à propager.
    """
    try:
        r = subprocess.run(
            ["podman", "inspect", "--format", "{{.State.Status}}", nom],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return {"podman_installe": False, "existe": False, "etat": None}
    except subprocess.TimeoutExpired:
        return {"podman_installe": True, "existe": None, "etat": None}

    if r.returncode != 0:
        return {"podman_installe": True, "existe": False, "etat": None}
    return {"podman_installe": True, "existe": True, "etat": r.stdout.strip()}


def demarrer_conteneur(nom: str = CONTENEUR_RUDI) -> tuple[bool, str]:
    """Démarre un conteneur Podman."""
    try:
        r = subprocess.run(
            ["podman", "start", nom],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return False, "podman n'est pas installé ou introuvable dans le PATH."
    except subprocess.TimeoutExpired:
        return False, "podman start : délai dépassé."

    if r.returncode == 0:
        return True, f"Conteneur « {nom} » démarré."
    return False, (r.stderr or r.stdout).strip() or "Échec du démarrage du conteneur."


def arreter_conteneur(nom: str = CONTENEUR_RUDI) -> tuple[bool, str]:
    """Arrête un conteneur Podman."""
    try:
        r = subprocess.run(
            ["podman", "stop", nom],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return False, "podman n'est pas installé ou introuvable dans le PATH."
    except subprocess.TimeoutExpired:
        return False, "podman stop : délai dépassé."

    if r.returncode == 0:
        return True, f"Conteneur « {nom} » arrêté."
    return False, (r.stderr or r.stdout).strip() or "Échec de l'arrêt du conteneur."


def noeud_pret(conf: dict) -> bool:
    """Vérifie que le nœud est vraiment exploitable, pas seulement qu'il écoute.

    On sonde `/manager/conf` — l'endpoint que la lib de publication interroge en premier
    (catalogPubUrl, storagePubUrl, backPath). Un simple GET sur la racine ne suffit pas :
    juste après un `podman start`, la racine répond déjà 200 alors que `/manager/conf`
    renvoie encore 500 pendant ~20 s, et toute publication lancée dans cet intervalle
    échoue sur « This node version is not compatible with this object »."""
    try:
        r = session.get(conf["url"].rstrip("/") + "/manager/conf", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# État Git (réutilisable par rudi_portal.py)
# ---------------------------------------------------------------------------

def _etat_git(chemin: str) -> dict:
    """État Git d'un dépôt local : branche, nombre d'avance sur origin/HEAD, patché ou non.

    Retourne {"disponible": bool, "branche": str|None, "commits_avance": int|None, "patche": bool|None}.
    ``patche`` vaut ``None`` (jamais ``False``) quand ``commits_avance`` n'a pas pu être déterminé.
    Ne lève jamais.
    """
    result = {"disponible": False, "branche": None, "commits_avance": None, "patche": None}
    try:
        if not os.path.isdir(os.path.join(chemin, ".git")):
            return result
        r = subprocess.run(
            ["git", "-C", chemin, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            result["branche"] = r.stdout.strip()
        r = subprocess.run(
            ["git", "-C", chemin, "rev-list", "--count", "origin/HEAD..HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            n = int(r.stdout.strip())
            result["commits_avance"] = n
            result["patche"] = n > 0
        result["disponible"] = True
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return result


# ---------------------------------------------------------------------------
# État des modules du nœud source (process natifs, pas de conteneur)
# ---------------------------------------------------------------------------

_CHEMIN_NOEUD_SOURCE = "/media/simon/DATA4T/Dev/rudi-node-build/rudi-node-container/src"
MODULES_NOEUD_SOURCE = {
    "catalog": {"dossier": "rudi-catalog", "port": 4030},
    "storage": {"dossier": "rudi-storage", "port": 4031},
    "manager": {"dossier": "rudi-manager", "port": 4032},
    "jwtauth": {"dossier": "rudi-jwtauth", "port": 4033},
}


def etat_modules_noeud_source(conf: dict | None = None) -> list[dict]:
    """État de chaque module du nœud RUDI source (4 process natifs).

    Combine la sonde HTTP (port ouvert) et l'état Git du clone du sous-module.
    Pour le manager, réutilise ``noeud_pret(conf)`` (vérifie /manager/conf, pas juste le port).
    Ne lève jamais.
    """
    if conf:
        hote = urlparse(conf.get("url", "")).hostname or "localhost"
    else:
        hote = "localhost"
    modules = []
    for nom_mod, info in MODULES_NOEUD_SOURCE.items():
        port = info["port"]
        # Sonde HTTP
        up = False
        try:
            if nom_mod == "manager" and conf:
                up = noeud_pret(conf)
            else:
                r = session.get(f"http://{hote}:{port}/", timeout=3)
                up = r.status_code < 500
        except Exception:
            pass
        # État Git
        chemin_git = os.path.join(_CHEMIN_NOEUD_SOURCE, info["dossier"])
        git = _etat_git(chemin_git)
        modules.append({
            "module": nom_mod,
            "port": port,
            "up": up,
            "git": git,
        })
    return modules


def noeud_source_actif(conf: dict) -> bool:
    """Au moins un des 4 modules du nœud source répond déjà."""
    return any(m["up"] for m in etat_modules_noeud_source(conf))


# ---------------------------------------------------------------------------
# Cycle de vie du nœud source (process natifs)
# ---------------------------------------------------------------------------

_SCRIPT_NOEUD_SOURCE = "/media/simon/DATA4T/Dev/rudi-node-build/start-source-node.sh"
_PATTERN_PROCESSUS_NOEUD_SOURCE = r"run-rudinode-(catalog|storage|manager|jwtauth)\.js"
_MONGO_PORT_NOEUD_SOURCE = 4027
_MONGO_TENTATIVES = 15
_MONGO_DELAI_S = 1


def _mongo_source_accessible() -> bool:
    """Le port Mongo du nœud source accepte-t-il une connexion TCP ?"""
    try:
        with socket.create_connection(("127.0.0.1", _MONGO_PORT_NOEUD_SOURCE), timeout=1):
            return True
    except OSError:
        return False


def demarrer_noeud_source(conf: dict) -> tuple[bool, str]:
    """Démarre le nœud RUDI source : Mongo dédié (Podman, rudi-source-mongo) si besoin,
    puis les 4 process natifs via start-source-node.sh (le script se termine seul, les
    process survivent en tâche de fond — subprocess.run() ne bloque pas leur durée de vie)."""
    if noeud_source_actif(conf):
        return True, "Nœud source déjà démarré."
    mongo = statut_conteneur("rudi-source-mongo")
    if mongo.get("etat") != "running":
        ok, msg = demarrer_conteneur("rudi-source-mongo")
        if not ok:
            return False, f"MongoDB du nœud source n'a pas pu démarrer : {msg}"
    if not _mongo_source_accessible():
        # « podman start » rend la main dès que le conteneur est marqué running, mais
        # mongod met encore quelques secondes à finir sa récupération WiredTiger avant
        # d'accepter des connexions — un simple `time.sleep(2)` s'est révélé insuffisant
        # à l'usage (start-source-node.sh échoue alors avec « MongoDB n'est pas
        # accessible » alors que le conteneur tourne). On attend le port réellement.
        for _ in range(_MONGO_TENTATIVES):
            time.sleep(_MONGO_DELAI_S)
            if _mongo_source_accessible():
                break
        else:
            return False, (f"MongoDB (rudi-source-mongo) ne répond toujours pas après "
                            f"{_MONGO_TENTATIVES * _MONGO_DELAI_S}s.")
    try:
        r = subprocess.run(["bash", _SCRIPT_NOEUD_SOURCE], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, f"Script introuvable : {_SCRIPT_NOEUD_SOURCE}"
    except subprocess.TimeoutExpired:
        return False, "start-source-node.sh : délai dépassé (60s)."
    if r.returncode != 0:
        return False, ((r.stderr or r.stdout).strip()[-2000:] or "Échec du démarrage du nœud source.")
    return True, "Nœud source démarré (logs : rudi-node-container/source-logs/)."


def arreter_noeud_source() -> tuple[bool, str]:
    """Arrête les 4 process natifs (pkill par motif — idempotent, pkill retourne 1 si rien ne
    matche). MongoDB n'est volontairement pas arrêté (partagé, coût nul à laisser tourner)."""
    try:
        subprocess.run(
            ["pkill", "-f", _PATTERN_PROCESSUS_NOEUD_SOURCE],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return False, "pkill introuvable."
    return True, "Nœud source arrêté (MongoDB laissé tel quel)."


def url_catalog(conf: dict) -> str:
    """URL de l'API catalog du nœud, sans slash final.

    Prend `url_catalog` de la config si présent ; sinon dérive l'URL du manager sur
    le port 3030 — la disposition de l'image Docker. Ce repli est faux dès qu'un nœud
    n'est pas bâti sur cette image : le nœud source expose son catalog sur 4030, et
    sans ce champ il irait interroger le catalog de son voisin (ou, celui-ci arrêté,
    retomberait silencieusement sur la version par défaut).
    """
    if conf.get("url_catalog"):
        return conf["url_catalog"].rstrip("/")
    parsed = urlparse(conf["url"].rstrip("/"))
    return f"{parsed.scheme}://{parsed.hostname}:3030/catalog"


def _api_version(conf: dict) -> str:
    """Récupère la version de l'API catalog du nœud RUDI."""
    try:
        return session.get(f"{url_catalog(conf)}/version", timeout=5).text.strip()
    except Exception as e:
        print(f"  [RUDI] AVERTISSEMENT : impossible de récupérer la version de l'API ({e}), valeur par défaut 1.4.0 utilisée.")
        return "1.4.0"


def _creer_writer(conf: dict) -> RudiNodeWriter:
    auth = RudiNodeAuth(usr=conf["usr"], pwd=conf["pwd"])
    return RudiNodeWriter(pm_url=conf["url"] + "/manager", auth=auth)


def _media_id_v4(existant: dict | None, nom_fichier: str) -> str:
    """Réutilise le media_id existant sur le nœud (UUIDv4), sinon en génère un."""
    if existant:
        for m in existant.get("available_formats", []):
            if m.get("media_name") == nom_fichier:
                return m["media_id"]
    return str(uuid.uuid4())


def toutes_metadonnees_rudi(conf: dict) -> list[dict]:
    """Retourne la liste de tous les datasets enregistrés sur le nœud RUDI."""
    writer = _creer_writer(conf)
    return writer.filter_metadata_list({})


def publier_dataset(
    conf: dict,
    rudi_metadata: dict,
    fichiers_filtres: list[str],
) -> None:
    """
    Publie un dataset sur le nœud RUDI local.

    conf             : dict avec url/usr/pwd (contenu de rudi_node.json)
    rudi_metadata    : dict au format RUDI (issu de traduire_metadonnees())
    fichiers_filtres : liste de chemins vers les fichiers filtrés à uploader
                       dans le même ordre que available_formats[0..n-1]
    """
    writer = _creer_writer(conf)

    # Cherche si le dataset existe déjà sur le nœud (pour réutiliser global_id et media_ids)
    local_id = rudi_metadata["local_id"]
    existants = writer.filter_metadata_list({"local_id": local_id})
    existant = existants[0] if existants else None

    rudi_metadata["global_id"] = existant["global_id"] if existant else str(uuid.uuid4())
    rudi_metadata.setdefault("metadata_info", {})["api_version"] = _api_version(conf)

    # organization_id : réutilise celui du nœud si l'org existe, sinon UUID v4
    org = rudi_metadata["producer"]
    org_name = org.get("organization_name", "")
    org_existante = next(
        (o for o in writer.organization_list if o.get("organization_name") == org_name),
        None,
    )
    if org_existante:
        # Si l'org existante n'a pas de summary mais l'entrant en a, enrichir
        if not org_existante.get("organization_summary") and org.get("organization_summary"):
            try:
                org_mise_a_jour = {**org_existante,
                                   "organization_caption": org.get("organization_caption", ""),
                                   "organization_summary": org.get("organization_summary", "")}
                writer.connector.put_catalog("organizations", org_mise_a_jour)
                org_existante = org_mise_a_jour
                print(f"  [RUDI] organisation « {org_name} » enrichie (caption + summary)")
            except Exception as e:
                print(f"  [RUDI] AVERTISSEMENT : impossible d'enrichir l'org « {org_name} » : {e}")
        rudi_metadata["producer"] = org_existante
    elif "organization_id" not in org:
        org["organization_id"] = str(uuid.uuid4())

    # theme : vérifie que la valeur est dans les thèmes acceptés par ce nœud
    themes_valides = writer.themes or []
    if themes_valides and rudi_metadata.get("theme") not in themes_valides:
        print(f"  [RUDI] AVERTISSEMENT : thème '{rudi_metadata.get('theme')}' non reconnu par le nœud. "
              f"Thèmes valides : {themes_valides}. Précisez 'theme' dans datasets.py.")
        raise ValueError(f"Thème RUDI invalide pour ce nœud : {rudi_metadata.get('theme')!r}")

    # contacts : le nœud exige au moins un contact avec email valide. Le nœud déduplique
    # les contacts par email — un email de repli partagé ferait collapser tous les
    # fallbacks sur un seul contact (voir connectors/contacts.py::contacter_pardefaut) —
    # donc l'email de repli est unique par nom de contact/producteur.
    from connectors.contacts import email_par_defaut
    contacts_source = rudi_metadata.get("contacts", [])
    if contacts_source:
        # Contacts extraits de la source (data.gouv.fr, WFS/WMS) — on les "booste" contre le nœud
        rudi_contacts = []
        for c in contacts_source:
            nom_contact = c.get("contact_name", org_name or "Contact")
            contact = writer.connector.get_or_create_contact_with_info(
                contact_name=nom_contact,
                contact_email=c.get("email") or email_par_defaut(nom_contact),
            )
            rudi_contacts.append(contact.to_json())
        rudi_metadata["contacts"] = rudi_contacts
    else:
        # Fallback : contact générique au nom de l'organisation productrice
        contact = writer.connector.get_or_create_contact_with_info(
            contact_name=org_name or "Contact",
            contact_email=email_par_defaut(org_name or "Contact"),
        )
        rudi_metadata["contacts"] = [contact.to_json()]

    medias = rudi_metadata["available_formats"]

    # Médias FILE écartés (upload refusé) — ne doivent pas faire échouer le reste du
    # dataset ; typiquement une variante JSON trop verbeuse d'un CSV déjà publiable
    # (ex: sirene-v3-consolidee, JSON à 1,4 Go pour une limite nœud de 500 Mo).
    noms_ecartes: set[str] = set()

    for i, chemin in enumerate(fichiers_filtres):
        if not os.path.isfile(chemin):
            print(f"  [RUDI] fichier introuvable, ignoré : {chemin}")
            continue
        nom = os.path.basename(chemin)
        media_id = _media_id_v4(existant, medias[i].get("media_name", nom))
        medias[i]["media_id"] = media_id
        caption = medias[i].get("media_caption", "")
        print(f"  [RUDI] upload {nom} (media_id={media_id[:8]}…)")
        try:
            media_info: RudiMediaFile = writer.post_local_file_and_media_info(
                file_local_path=chemin,
                media_id=media_id,
            )
        except Exception as e:
            if "too big to be uploaded" in str(e):
                taille_mo = os.path.getsize(chemin) / 1_048_576
                print(f"  [RUDI] {nom} ({taille_mo:.0f} Mo) dépasse la limite de taille du "
                      f"nœud — média écarté, le reste du dataset est publié quand même.")
                noms_ecartes.add(nom)
                continue
            raise
        # Remplace le media dict par celui retourné (contient file_type, file_size, checksum…)
        medias[i] = json.loads(str(media_info))
        if caption:
            medias[i]["media_caption"] = caption
        print(f"  [RUDI] URL storage : {medias[i]['connector']['url']}")

    # Nettoyage Local→Rudi : retirer les médias FILE dont le fichier local a disparu
    # ou dont l'upload a été écarté ci-dessus (trop volumineux pour le nœud)
    noms_locaux = {os.path.basename(p) for p in fichiers_filtres if os.path.isfile(p)}
    medias[:] = [m for m in medias
                 if m.get("media_type") != "FILE"
                 or (m.get("media_name") in noms_locaux and m.get("media_name") not in noms_ecartes)]

    # Corrige les media_ids des entrées non uploadées (SERVICE vers data.gouv.fr,
    # source-metadata…) — la lib RUDI valide que tous les media_id sont des UUID v4.
    # Les FILE restants ont tous été uploadés ci-dessus (les manquants viennent
    # d'être retirés), leur media_id est déjà correct : on ne touche que le reste.
    for m in medias:
        if m.get("media_type") != "FILE":
            m["media_id"] = _media_id_v4(existant, m.get("media_name", ""))

    print(f"  [RUDI] push métadonnées (local_id={local_id[:8]}…)")
    writer.put_metadata(rudi_metadata)
    print(f"  [RUDI] publié.")


def supprimer_organisation(conf: dict, org_id: str) -> bool:
    """
    Supprime une organisation du nœud RUDI par son organization_id.
    Retourne True si la suppression a réussi.
    """
    writer = _creer_writer(conf)
    try:
        writer.connector.delete_org_with_id(org_id)
        print(f"  [RUDI] organisation {org_id[:12]}… supprimée du nœud.")
        return True
    except Exception as e:
        print(f"  [RUDI] Erreur suppression organisation {org_id[:12]}… : {e}")
        return False


def supprimer_contact(conf: dict, contact_id: str) -> bool:
    """
    Supprime un contact du nœud RUDI par son contact_id.
    Retourne True si la suppression a réussi.
    """
    writer = _creer_writer(conf)
    try:
        writer.connector.del_catalog(f"contacts/{contact_id}")
        print(f"  [RUDI] contact {contact_id[:12]}… supprimé du nœud.")
        return True
    except Exception as e:
        print(f"  [RUDI] Erreur suppression contact {contact_id[:12]}… : {e}")
        return False


def supprimer_dataset(conf: dict, global_id: str) -> bool:
    """
    Supprime un dataset du nœud RUDI par son global_id.
    Retourne True si la suppression a réussi.
    """
    writer = _creer_writer(conf)
    try:
        writer.connector.del_catalog(f"resources/{global_id}")
        print(f"  [RUDI] dataset {global_id[:12]}… supprimé du nœud.")
        return True
    except Exception as e:
        print(f"  [RUDI] Erreur suppression {global_id[:12]}… : {e}")
        return False
