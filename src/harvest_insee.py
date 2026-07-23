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
import glob
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conf.datasets import DATASETS_INSEE
from connectors.insee import resoudre_url, extraire_membres, extraire_dictionnaire
from connectors.http import session
from translation.description_secours import generer_complement
from connectors.rudi_publish import publier_si_configue
from harvest_batch import filtrer_csv_bytes
from translation.rudi_builder import (
    LICENCE_ETALAB, construire_rudi_metadata,
    media_filtre, media_dict,
)
from filters.csv import slugifier, sauvegarder_csv
from filters.harvest import _detecter_encodage_bytes
from connectors.analyseurs import _detecter_champs
from state import charger_etat, sauvegarder_etat

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "state_insee.json")

_HEADERS = {"User-Agent": "moissonneuse-batteuse/1.0 (projet open-data Rennes Métropole)"}


def _inchange(pub_id: str, url: str, state: dict, dossier: str) -> bool:
    """Retourne True si url+taille+date-modif correspondent à la dernière entrée en cache.

    insee.fr ne renvoie ni Content-Length ni Last-Modified sur ces URLs (constaté en
    pratique) : quand aucun validateur HTTP n'est exploitable, on se fie à l'URL déjà
    comparée (une publication INSEE est un fichier statique par millésime — URL
    identique = même édition), à condition que le fichier filtré soit toujours présent
    sur disque (garde-fou si data/<dossier>/ a été purgé sans purger state_insee.json).
    """
    entree = state.get(pub_id, {})
    if entree.get("url") != url:
        return False
    if not glob.glob(os.path.join(dossier, "*-rennesmetropole.csv")):
        return False
    try:
        r = session.head(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        size = r.headers.get("Content-Length")
        lm   = r.headers.get("Last-Modified")
        if size:
            # Inchangé si taille identique ET (pas de Last-Modified OU date identique)
            return str(size) == str(entree.get("content_length")) and (not lm or lm == entree.get("last_modified"))
    except Exception as e:
        print(f"  ⚠ sonde HEAD impossible ({e}) — publication considérée inchangée")
    # Pas de validateur HTTP exploitable : l'URL inchangée fait foi.
    return True


# ---------------------------------------------------------------------------
# Métadonnées RUDI
# ---------------------------------------------------------------------------

_PRODUCTEUR = "Institut national de la statistique et des études économiques (Insee)"
_ZONE = "Rennes Métropole"


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
    cle_stable = pub["id"]
    theme = pub.get("theme", "society")

    medias = [media_filtre(f"insee:{cle_stable}", nom, _ZONE) for nom, _ in fichiers_data]
    medias += [media_dict(f"insee:{cle_stable}", nom) for nom in (fichiers_dict or [])]

    description = (
        f"Données INSEE filtrées sur {_ZONE}.\n\n"
        f"Source : {url_page}\n\n"
        + generer_complement(theme=theme, producteur=_PRODUCTEUR, zone=_ZONE,
                              colonnes=entetes_colonnes)
    )

    return construire_rudi_metadata(
        local_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"insee:{cle_stable}")),
        titre=f"{pub['titre']} — {_ZONE}",
        synopsis=f"{pub['titre'][:110]} — données filtrées sur {_ZONE}."[:150],
        description=description,
        theme=theme,
        keywords=["insee", _ZONE.lower(), pub["id"]],
        producteur_nom=_PRODUCTEUR,
        url_source=pub.get("url_direct", url_page),
        url_fiche=url_page,
        medias=medias,
        date_source=date_maj,
        metadata_source_label="insee.fr",
    )



def _filtrer_dict_variables(contenu: bytes) -> bytes:
    """Retourne le dictionnaire filtré : uniquement les lignes de définition de variables
    (COD_MOD vide). Élimine les milliers de lignes de modalités géographiques (codes IRIS,
    communes) qui gonflent le fichier sans apporter d'information utile."""
    import io
    texte = contenu.decode(_detecter_encodage_bytes(contenu), errors="replace")
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
    r = session.get(url, headers=_HEADERS, timeout=120, stream=True)
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
    if _inchange(pub_id, url, state, dossier):
        nb_rm = state.get(pub_id, {}).get("nb_rm", "?")
        print(f"  → Cache (inchangé, {nb_rm} lignes RM)")
        return {"statut": "cache", "nb_rm": nb_rm}

    # 3. Métadonnées HTTP (pour la détection de changement future)
    try:
        r_head = session.head(url, headers=_HEADERS, timeout=15, allow_redirects=True)
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
        slug = slugifier(os.path.splitext(os.path.basename(nom_membre))[0])
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
        rudi_publie = publier_si_configue(rudi_meta, chemins_csv + chemins_dict)

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
    state = charger_etat(STATE_FILE)

    ok, cache, echecs, vides = [], [], [], []

    for pub in publications:
        res = traiter_publication(pub, state)
        sauvegarder_etat(STATE_FILE, state)  # sauvegarde immédiate après chaque publication

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
