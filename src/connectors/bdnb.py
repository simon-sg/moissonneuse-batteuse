"""
Connecteur BDNB — Base de Données Nationale des Bâtiments (CSTB)
Données open-data par département : https://bdnb.io/download/

Structure du ZIP dep_XX_csv :
  csv/batiment_groupe.csv                      — bâtiments (9 colonnes, filtre: code_commune_insee)
  csv/batiment_groupe_dpe_representatif_logement.csv  — DPE représentatif logement
  csv/batiment_groupe_dpe_statistique_logement.csv    — DPE statistique logement
  csv/batiment_groupe_dpe_tertiaire.csv               — DPE tertiaire
  csv/batiment_groupe_dle_elec_multimillesime.csv     — consommation électrique (2018-2024)
  csv/batiment_groupe_dle_gaz_multimillesime.csv      — consommation gaz (2018-2024)
  csv/batiment_groupe_ffo_bat.csv                     — fichiers fonciers bâtiment
  (+ autres tables non extraites par défaut — voir harvest_bdnb.py)

Filtre géographique :
  Table batiment_groupe : colonne `code_commune_insee` (5c)
  Tables jointes       : colonne `batiment_groupe_id` ∈ IDs RM (issue du filtrage batiment_groupe)

URL du ZIP dep_35 (millésime 2026-02-a) :
  https://open-data.s3.fr-par.scw.cloud/bdnb_millesime_2026-02-a/
    millesime_2026-02-a_dep35/open_data_millesime_2026-02-a_dep35_csv.zip
"""
import csv
import io
import os
import zipfile

from connectors.http import session

_HEADERS = {"User-Agent": "moissonneuse-batteuse/1.0 (projet open-data Rennes Métropole)"}
_TIMEOUT_HEAD = 20
_TIMEOUT_GET  = 300   # ZIP de ~620 Mo

# Colonne commune dans batiment_groupe
CHAMP_COMMUNE   = "code_commune_insee"
CHAMP_BG_ID     = "batiment_groupe_id"
# Colonne geometry — exclue par défaut (WKT volumineux, peu utile en format tabulaire)
CHAMP_GEOM      = "geom_groupe"
CHAMP_GEOM_PARC = "geom_parcelle"

# Tables à extraire par défaut, avec leur méthode de filtrage :
#   "commune"  → filtre direct sur code_commune_insee
#   "bg_id"    → filtre via l'ensemble des batiment_groupe_id RM
TABLES_DEFAUT = [
    ("csv/batiment_groupe.csv",                         "commune"),
    ("csv/batiment_groupe_dpe_representatif_logement.csv", "bg_id"),
    ("csv/batiment_groupe_dpe_statistique_logement.csv",   "bg_id"),
    ("csv/batiment_groupe_dpe_tertiaire.csv",              "bg_id"),
    ("csv/batiment_groupe_dle_elec_multimillesime.csv",    "bg_id"),
    ("csv/batiment_groupe_dle_gaz_multimillesime.csv",     "bg_id"),
    ("csv/batiment_groupe_ffo_bat.csv",                    "bg_id"),
]


# ---------------------------------------------------------------------------
# Résolution / contrôle d'accès
# ---------------------------------------------------------------------------

def get_zip_info(url: str) -> tuple[str | None, str | None]:
    """HEAD sur l'URL du ZIP — retourne (Last-Modified, Content-Length)."""
    try:
        r = session.head(url, headers=_HEADERS, timeout=_TIMEOUT_HEAD, allow_redirects=True)
        r.raise_for_status()
        return r.headers.get("Last-Modified"), r.headers.get("Content-Length")
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Téléchargement du ZIP
# ---------------------------------------------------------------------------

def telecharger_zip(url: str, chemin_tmp: str) -> None:
    """Télécharge le ZIP dep_XX en streaming avec barre de progression."""
    r = session.get(url, headers=_HEADERS, timeout=_TIMEOUT_GET, stream=True)
    r.raise_for_status()
    total_attendu = int(r.headers.get("Content-Length", 0))
    total = 0
    try:
        with open(chemin_tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):  # 4 Mo
                f.write(chunk)
                total += len(chunk)
                if total_attendu:
                    pct = total * 100 // total_attendu
                    print(f"  {total / 1024 / 1024:.0f}/{total_attendu / 1024 / 1024:.0f} Mo ({pct}%)",
                          end="\r")
                else:
                    print(f"  {total / 1024 / 1024:.1f} Mo...", end="\r")
    except BaseException:
        if os.path.exists(chemin_tmp):
            os.remove(chemin_tmp)
        raise
    print()


# ---------------------------------------------------------------------------
# Extraction et filtrage des tables
# ---------------------------------------------------------------------------

def _lire_csv_zip(zf: zipfile.ZipFile, nom: str, delimiter: str = ";",
                  exclure_colonnes: set | None = None,
                  filtre=None) -> tuple[list[str], list[dict]]:
    """Lit une table CSV depuis le ZIP en streaming ligne par ligne.

    filtre : callable(row_dict) → bool, appliqué à chaque ligne pendant la lecture.
    Ne charge en mémoire que les lignes qui passent le filtre — jamais la table entière.
    """
    with zf.open(nom) as fbin:
        f = io.TextIOWrapper(fbin, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.DictReader(f, delimiter=delimiter)
        colonnes = [c for c in (reader.fieldnames or [])
                    if not exclure_colonnes or c not in exclure_colonnes]
        lignes = []
        for row in reader:
            if filtre is None or filtre(row):
                lignes.append({c: row.get(c, "") for c in colonnes if c in row})
    return colonnes, lignes


def extraire_batiments_rm(
    chemin_zip: str,
    codes_commune_rm: set[str],
    exclure_geom: bool = True,
) -> tuple[list[str], list[dict], set[str]]:
    """Extrait et filtre batiment_groupe.csv pour les communes RM en streaming.

    Retourne (colonnes, lignes_rm, ids_rm) :
    - ids_rm : set de batiment_groupe_id pour les bâtiments en RM
              (utilisé pour filtrer les tables jointes)
    """
    exclure = {CHAMP_GEOM} if exclure_geom else set()
    with zipfile.ZipFile(chemin_zip) as zf:
        colonnes, lignes_rm = _lire_csv_zip(
            zf, "csv/batiment_groupe.csv", exclure_colonnes=exclure,
            filtre=lambda row: row.get(CHAMP_COMMUNE, "") in codes_commune_rm,
        )
    ids_rm = {l[CHAMP_BG_ID] for l in lignes_rm if CHAMP_BG_ID in l}
    return colonnes, lignes_rm, ids_rm


def extraire_table_par_bg_id(
    chemin_zip: str,
    nom_table: str,
    ids_rm: set[str],
    exclure_geom: bool = True,
) -> tuple[list[str], list[dict]]:
    """Extrait et filtre une table par batiment_groupe_id ∈ ids_rm en streaming."""
    geoms = {CHAMP_GEOM, CHAMP_GEOM_PARC} if exclure_geom else set()
    with zipfile.ZipFile(chemin_zip) as zf:
        colonnes, lignes_rm = _lire_csv_zip(
            zf, nom_table, exclure_colonnes=geoms,
            filtre=lambda row: row.get(CHAMP_BG_ID, "") in ids_rm,
        )
    return colonnes, lignes_rm


# ---------------------------------------------------------------------------
# Sauvegarde CSV
# ---------------------------------------------------------------------------

def sauvegarder_csv(colonnes: list[str], lignes: list[dict], chemin: str) -> None:
    """Écrit un CSV UTF-8-sig avec séparateur point-virgule."""
    with open(chemin, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(lignes)
