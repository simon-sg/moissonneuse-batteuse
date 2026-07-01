"""
Harvest des publications INSEE directes (fichiers non disponibles sur data.gouv.fr).

Usage :
  python3 src/harvest_insee.py            # toutes les publications
  python3 src/harvest_insee.py bic-iris   # une publication par ID

Résultats :
  data/<dossier>/<slug>-rennesmetropole.csv   — lignes filtrées Rennes Métropole
  data/<dossier>/rudi_metadata.json           — métadonnées RUDI (pour catalogue + nœud)
  data/state_insee.json                       — cache (évite re-téléchargements)
"""
import csv
import datetime
import json
import os
import sys
import uuid

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.datasets import DATASETS_INSEE
from connectors.insee import resoudre_url, extraire_membres, extraire_dictionnaire
from translation.description_secours import generer_complement
from connectors.rudi_node import publier_dataset, charger_conf_rudi
from harvest_batch import filtrer_csv_bytes, sauvegarder_csv, _slugifier
from discover import _detecter_champs

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "state_insee.json")

_HEADERS = {"User-Agent": "moissonneuse-batteuse/1.0 (projet open-data Rennes Métropole)"}


# ---------------------------------------------------------------------------
# State / cache
# ---------------------------------------------------------------------------

def _charger_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[state] {STATE_FILE} illisible ou corrompu ({e}), repart d'un état vide.")
            return {}
    return {}


def _sauvegarder_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _inchange(pub_id: str, url: str, state: dict) -> bool:
    """Retourne True si url+taille+date-modif correspondent à la dernière entrée en cache."""
    entree = state.get(pub_id, {})
    if entree.get("url") != url:
        return False
    try:
        r = requests.head(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        size = r.headers.get("Content-Length")
        lm   = r.headers.get("Last-Modified")
        # Inchangé si taille identique ET (pas de Last-Modified OU date identique)
        if size and str(size) == str(entree.get("content_length")):
            if not lm or lm == entree.get("last_modified"):
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Métadonnées RUDI
# ---------------------------------------------------------------------------

_BBOX_RM = {
    "bounding_box": {
        "west_longitude": -2.08, "east_longitude": -1.37,
        "south_latitude": 47.89, "north_latitude": 48.27,
    }
}
_LICENCE_ETALAB = {
    "licence_type": "STANDARD",
    "licence_label": "etalab-2.0",
    "licence_uri": "https://www.etalab.gouv.fr/licence-ouverte-open-licence",
}
def _generer_rudi_metadata(pub: dict, fichiers_data: list[tuple[str, int]],
                            date_maj: str | None,
                            fichiers_dict: list[str] | None = None,
                            entetes_colonnes: list[str] | None = None) -> dict:
    """Génère le bloc rudi_metadata.json pour une publication INSEE directe.

    fichiers_data    : [(nom_fichier, nb_rm), ...]
    date_maj         : Last-Modified HTTP (ou None)
    entetes_colonnes : colonnes du fichier filtré (si connues) — les publications INSEE
                       n'ont jamais de description source, on la complète systématiquement
    """
    url_page = pub["url_page"]
    zone = "Rennes Métropole"
    titre = f"{pub['titre']} — {zone}"
    synopsis = f"{pub['titre'][:110]} — données filtrées sur {zone}."[:150]
    producteur_nom = "Institut national de la statistique et des études économiques (Insee)"
    theme = pub.get("theme", "society")

    # local_id déterministe : même publication = même ID à chaque run
    local_id = str(uuid.uuid5(uuid.NAMESPACE_URL, url_page))

    medias_filtres = [
        {
            "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_page}/filtered/{nom}")),
            "media_type": "FILE",
            "media_name": nom,
            "media_caption": f"{nom} — données filtrées sur {zone} (CSV)",
            "connector": {
                "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
                "interface_contract": "dwnl",
            },
        }
        for nom, _ in fichiers_data
    ]
    media_source = {
        "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_page}/source")),
        "media_type": "SERVICE",
        "media_name": "source-insee",
        "media_caption": "Publication complète (France entière) sur insee.fr",
        "connector": {
            "url": pub.get("url_direct", url_page),
            "interface_contract": "dwnl",
        },
    }


    medias_dict = [
        {
            "media_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url_page}/dict/{nom}")),
            "media_type": "FILE",
            "media_name": nom,
            "media_caption": f"Dictionnaire des variables — {nom} (CSV)",
            "connector": {
                "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
                "interface_contract": "dwnl",
            },
        }
        for nom in (fichiers_dict or [])
    ]
    dates = {}
    if date_maj:
        # Last-Modified HTTP ex: "Wed, 12 Feb 2025 09:41:37 GMT" → "2025-02-12T00:00:00Z"
        try:
            from email.utils import parsedate_to_datetime
            dates["updated"] = parsedate_to_datetime(date_maj).strftime("%Y-%m-%dT00:00:00Z")
        except Exception:
            dates["updated"] = datetime.date.today().isoformat() + "T00:00:00Z"

    description = (
        f"Données INSEE filtrées sur {zone}.\n\n"
        f"Source : {url_page}\n\n"
        + generer_complement(theme=theme, producteur=producteur_nom, zone=zone,
                              colonnes=entetes_colonnes)
    )

    return {
        "local_id": local_id,
        "resource_title": titre,
        "synopsis": [{"lang": "fr", "text": synopsis}],
        "summary": [{"lang": "fr", "text": description}],
        "theme": theme,
        "keywords": ["insee", zone.lower(), pub["id"]],
        "producer": {"organization_name": producteur_nom},
        "contacts": [],
        "available_formats": medias_filtres + medias_dict + [media_source],
        "dataset_dates": dates,
        "storage_status": "online",
        "access_condition": {
            "licence": _LICENCE_ETALAB,
            "confidentiality": {"restricted_access": False, "gdpr_sensitive": False},
        },
        "geography": _BBOX_RM,
        "metadata_info": {"metadata_source": url_page},
    }



def _filtrer_dict_variables(contenu: bytes) -> bytes:
    """Retourne le dictionnaire filtré : uniquement les lignes de définition de variables
    (COD_MOD vide). Élimine les milliers de lignes de modalités géographiques (codes IRIS,
    communes) qui gonflent le fichier sans apporter d'information utile."""
    import io
    texte = contenu.decode("utf-8-sig", errors="replace")
    if texte.count("\ufffd") > 10:
        texte = contenu.decode("latin-1")
    # Détection délimiteur
    premiere = texte.split("\n")[0]
    delim = max(";", "\t", ",", "|", key=lambda d: premiere.count(d))

    reader = csv.DictReader(io.StringIO(texte), delimiter=delim)
    if reader.fieldnames is None:
        return contenu  # pas de header reconnu → on laisse intact

    # Colonne COD_MOD (insensible à la casse et aux espaces)
    col_mod = next((c for c in reader.fieldnames if c.strip().upper() == "COD_MOD"), None)
    if col_mod is None:
        return contenu  # pas de colonne COD_MOD → fichier d'autre format, on laisse intact

    lignes = [row for row in reader if not row.get(col_mod, "").strip()]

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=reader.fieldnames, delimiter=delim,
                            lineterminator="\n")
    writer.writeheader()
    writer.writerows(lignes)
    return out.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------

def telecharger_zip(pub_id: str, url: str) -> str:
    """Télécharge un ZIP en streaming vers un fichier temporaire. Retourne le chemin."""
    chemin_tmp = os.path.join(DATA_DIR, f"_tmp_{pub_id}.zip")
    r = requests.get(url, headers=_HEADERS, timeout=120, stream=True)
    r.raise_for_status()
    total_attendu = int(r.headers.get("Content-Length", 0))
    total = 0
    try:
        with open(chemin_tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                total += len(chunk)
                if total_attendu:
                    pct = total * 100 // total_attendu
                    print(f"  [{pub_id}] {total / 1024 / 1024:.0f}/{total_attendu / 1024 / 1024:.0f} Mo ({pct}%)",
                          end="\r")
                else:
                    print(f"  [{pub_id}] {total / 1024 / 1024:.1f} Mo...", end="\r")
    except BaseException:
        if os.path.exists(chemin_tmp):
            os.remove(chemin_tmp)
        raise
    print()
    return chemin_tmp


# ---------------------------------------------------------------------------
# Traitement d'une publication
# ---------------------------------------------------------------------------

def traiter_publication(pub: dict, state: dict) -> dict:
    pub_id = pub["id"]
    dossier = os.path.join(DATA_DIR, pub["dossier"])
    os.makedirs(dossier, exist_ok=True)

    print(f"\n--- {pub['titre']} ---")

    # 1. Résoudre l'URL (URL directe → fallback scraping)
    url = resoudre_url(pub)
    if not url:
        return {"statut": "echec", "raison": "URL introuvable (direct + scraping)"}

    # 2. Cache : vérifier si le fichier a changé
    if _inchange(pub_id, url, state):
        nb_rm = state.get(pub_id, {}).get("nb_rm", "?")
        print(f"  → Cache (inchangé, {nb_rm} lignes RM)")
        return {"statut": "cache", "nb_rm": nb_rm}

    # 3. Métadonnées HTTP (pour la détection de changement future)
    try:
        r_head = requests.head(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        content_length = r_head.headers.get("Content-Length")
        last_modified  = r_head.headers.get("Last-Modified")
    except Exception:
        content_length, last_modified = None, None

    # 4. Téléchargement
    print(f"  Téléchargement : {url}")
    try:
        chemin_zip = telecharger_zip(pub_id, url)
    except Exception as e:
        return {"statut": "echec", "raison": f"téléchargement : {e}"}

    # 5. Extraction des membres CSV
    try:
        membres = extraire_membres(pub, chemin_zip)
    except Exception as e:
        os.remove(chemin_zip)
        return {"statut": "echec", "raison": f"extraction ZIP : {e}"}
    if not membres:
        os.remove(chemin_zip)
        return {"statut": "echec", "raison": "aucun membre CSV correspondant dans le ZIP"}

    champ_iris    = pub.get("champ_iris")
    champ_iris_ou = pub.get("champ_iris_ou")   # 2e colonne géo : garde si l'UNE OU L'AUTRE est RM
    champ_cp      = pub.get("champ_cp")
    champ_ville   = pub.get("champ_ville")
    champ_adresse = pub.get("champ_adresse")

    # 6. Filtrage et sauvegarde
    nb_rm_total = 0
    fichiers_data: list[tuple[str, int]] = []   # (nom_csv, nb_rm)
    chemins_csv:   list[str]            = []    # chemins absolus (pour publier_dataset)
    dernieres_entetes: list[str]        = []    # colonnes du dernier fichier filtré (pour la description de secours)

    for nom_membre, contenu_csv in membres:
        print(f"  Filtrage : {nom_membre}")
        try:
            lignes, entetes = filtrer_csv_bytes(
                contenu_csv, champ_cp, champ_ville, champ_iris, champ_adresse
            )
        except Exception as e:
            print(f"    → Erreur de filtrage : {e}")
            continue

        # Failsafe : si 0 lignes et champ_iris absent des colonnes réelles, auto-détection
        if not lignes and champ_iris and champ_iris not in (entetes or []):
            auto = _detecter_champs(entetes)[2]  # indice 2 = champ_iris
            if auto and auto != champ_iris:
                print(f"    Champ '{champ_iris}' absent — essai auto-détecté : '{auto}'")
                try:
                    lignes, entetes = filtrer_csv_bytes(contenu_csv, champ_cp, champ_ville, auto, champ_adresse)
                except Exception as e:
                    print(f"    → Erreur (auto) : {e}")

        # OR-filtre : ajouter les lignes où le 2e champ géo est en RM (ex: commune de travail)
        if champ_iris_ou and entetes and champ_iris_ou in entetes:
            try:
                lignes_ou, _ = filtrer_csv_bytes(contenu_csv, None, None, champ_iris_ou, None)
                cles = {tuple(r.values()) for r in lignes}
                ajouts = 0
                for r in lignes_ou:
                    k = tuple(r.values())
                    if k not in cles:
                        lignes.append(r)
                        cles.add(k)
                        ajouts += 1
                if ajouts:
                    print(f"    + {ajouts} lignes via {champ_iris_ou} (travail en RM)")
            except Exception as e:
                print(f"    → Erreur OR-filtre ({champ_iris_ou}) : {e}")

        if not lignes:
            print(f"    → 0 lignes RM")
            if entetes:
                print(f"    Colonnes disponibles : {entetes[:12]}")
            continue

        print(f"    → {len(lignes)} lignes RM")
        if entetes:
            dernieres_entetes = entetes
        slug = _slugifier(os.path.splitext(os.path.basename(nom_membre))[0])
        nom_csv = f"{slug}-rennesmetropole.csv"
        chemin_csv = os.path.join(dossier, nom_csv)
        sauvegarder_csv(lignes, chemin_csv)
        print(f"    → {chemin_csv}")
        nb_rm_total += len(lignes)
        fichiers_data.append((nom_csv, len(lignes)))
        chemins_csv.append(chemin_csv)

    # 7. Dictionnaire des variables (si présent dans le ZIP)
    noms_dict: list[str] = []
    chemins_dict: list[str] = []
    for nom_dict, contenu_dict in extraire_dictionnaire(pub, chemin_zip):
        chemin_dict = os.path.join(dossier, os.path.basename(nom_dict))
        contenu_filtre = _filtrer_dict_variables(contenu_dict)
        with open(chemin_dict, "wb") as f:
            f.write(contenu_filtre)
        nb_var = contenu_filtre.count(b"\n") - 1  # lignes hors header
        noms_dict.append(os.path.basename(nom_dict))
        chemins_dict.append(chemin_dict)
        print(f"  → dictionnaire sauvegardé : {os.path.basename(nom_dict)} ({nb_var} variables)")

    # ZIP temporaire libéré dès que l'extraction est terminée
    os.remove(chemin_zip)

    # 8. Générer rudi_metadata.json (catalogue + nœud RUDI)
    rudi_publie = True  # rien à publier (pas de fichiers_data) -> rien à retenter non plus
    if fichiers_data:
        rudi_publie = False
        rudi_meta = _generer_rudi_metadata(pub, fichiers_data, last_modified,
                                            fichiers_dict=noms_dict,
                                            entetes_colonnes=dernieres_entetes)
        chemin_rudi = os.path.join(dossier, "rudi_metadata.json")
        with open(chemin_rudi, "w", encoding="utf-8") as f:
            json.dump(rudi_meta, f, ensure_ascii=False, indent=2)
        print(f"  → rudi_metadata.json généré")

        # 9. Publication optionnelle sur le nœud RUDI
        conf_rudi = charger_conf_rudi()
        if conf_rudi:
            try:
                publier_dataset(conf=conf_rudi, rudi_metadata=rudi_meta,
                                fichiers_filtres=chemins_csv + chemins_dict)
                print(f"  [RUDI] Publié.")
                rudi_publie = True
            except Exception as e:
                print(f"  [RUDI] Erreur publication : {e}")
        else:
            print(f"  [RUDI] rudi_node.json absent — publication ignorée.")

    # 10. Mettre à jour le cache. Le téléchargement/filtrage est acquis dès qu'on
    # arrive ici ; `rudi_publie` distingue séparément si la publication a réussi.
    # Si elle a échoué ou a été ignorée, `src/publish_rudi.py` republiera depuis
    # rudi_metadata.json sans tout re-télécharger.
    state[pub_id] = {
        "url": url,
        "content_length": content_length,
        "last_modified":  last_modified,
        "date_harvest":   datetime.date.today().isoformat(),
        "nb_rm": nb_rm_total,
        "dossier": pub["dossier"],
        "rudi_publie": rudi_publie,
    }

    if nb_rm_total == 0:
        return {"statut": "vide", "raison": "0 lignes RM après filtrage de tous les membres"}
    return {"statut": "ok", "nb_rm": nb_rm_total}


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def main() -> None:
    # Filtre optionnel : python3 harvest_insee.py bic-iris filosofi-commune
    ids_demandes = set(sys.argv[1:])
    publications = [
        p for p in DATASETS_INSEE
        if not ids_demandes or p["id"] in ids_demandes
    ]
    if ids_demandes and not publications:
        ids_valides = [p["id"] for p in DATASETS_INSEE]
        print(f"ID(s) inconnu(s). IDs valides : {', '.join(ids_valides)}")
        sys.exit(1)

    print(f"=== Harvest INSEE direct — {len(publications)} publication(s) ===\n")
    state = _charger_state()

    ok, cache, echecs, vides = [], [], [], []

    for pub in publications:
        res = traiter_publication(pub, state)
        _sauvegarder_state(state)  # sauvegarde immédiate après chaque publication

        statut = res["statut"]
        if statut == "ok":
            print(f"  ✓ {pub['id']} — {res['nb_rm']} lignes RM")
            ok.append(pub["id"])
        elif statut == "cache":
            cache.append(pub["id"])
        elif statut == "vide":
            print(f"  ! {pub['id']} — vide : {res['raison']}")
            vides.append(pub["id"])
        else:
            print(f"  ✗ {pub['id']} — ÉCHEC : {res['raison']}")
            echecs.append(pub["id"])

    print(f"\n=== Terminé ===")
    print(f"  OK     : {len(ok)}")
    print(f"  Cache  : {len(cache)}")
    print(f"  Vides  : {len(vides)}")
    print(f"  Échecs : {len(echecs)}")

    if echecs:
        print(f"\n  Consultez data/state_insee.json pour l'état détaillé.")


if __name__ == "__main__":
    main()
