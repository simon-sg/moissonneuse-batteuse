"""
Utilitaires de filtrage partagés pour la moisson batch.

Fonctions extraites de harvest_batch.py pour éviter la duplication avec
connectors/analyseurs.py et rendre le code réutilisable.

Contient :
- _detecter_delimiteur / _detecter_encodage_bytes / _detecter_encodage
- _ligne_est_rm — filtrage RM par ligne
- _extraire_csvs_zip — extraction de membres CSV d'un ZIP
"""

import itertools
import zipfile

import csv

from conf.discover import CACHE_DIR
from connectors.sirene import obtenir_sirens_rm
from filters.geographic import (
    est_dans_rm, est_commune_rm, normaliser, est_circonscription_rm,
    est_iris_rm, est_code_rm, est_epci_rm, est_point_rm, est_adresse_rm,
    est_departement_rm, detecter_nature_colonne,
    EPCI_SIREN_RM,
)
from conf.communes_rm import CODES_POSTAUX_RM, CODES_INSEE_RM


def _detecter_delimiteur(sample: str) -> str:
    """Détecte le délimiteur CSV en comptant les occurrences dans la première ligne."""
    premiere_ligne = sample.split("\n")[0]
    candidats = {d: premiere_ligne.count(d) for d in (";", "\t", "|", ",")}
    meilleur = max(candidats, key=candidats.get)
    if candidats[meilleur] >= 1:
        return meilleur
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        return dialect.delimiter
    except csv.Error:
        return ","


def _detecter_encodage_bytes(sample: bytes) -> str:
    """Détecte l'encodage d'un échantillon de bytes (utf-8-sig si décodable sans
    la moindre erreur, cp1252 sinon). Tolérer un peu d'UTF-8 invalide (ancien seuil
    à 10 caractères de remplacement) est un piège : le fichier est ensuite décodé
    en entier avec errors="replace", donc chaque octet accentué non conforme se
    retrouve gravé en "�" dans le CSV filtré final — publié tel quel sur RUDI. Un
    CSV réellement Windows-1252 (le cas quasi-systématique hors UTF-8 sur
    data.gouv.fr) ne décode jamais proprement en UTF-8 sur un échantillon de
    quelques Ko dès qu'il contient des accents ; un seul octet invalide suffit
    donc à trancher."""
    try:
        sample.decode("utf-8-sig", errors="strict")
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "cp1252"


def _detecter_encodage(chemin: str) -> str:
    """Détecte l'encodage d'un fichier texte (utf-8-sig ou cp1252). Échantillon large
    (256 Ko) pour limiter le risque qu'un CSV Windows-1252 ne porte ses premiers
    accents qu'au-delà de la fenêtre analysée."""
    with open(chemin, "rb") as f:
        sample = f.read(262144)
    return _detecter_encodage_bytes(sample)


def nature_champ_iris(champ_iris: str, rows, max_lignes: int = 5000) -> str:
    """Pré-passe de filtrage : détecte la nature ("insee", "cp" ou "inconnue")
    d'une colonne champ_iris sur un échantillon de lignes (dicts)."""
    return detecter_nature_colonne(
        str(row.get(champ_iris, "")) for row in itertools.islice(rows, max_lignes))


def _ligne_est_rm(row: dict, champ_cp, champ_ville, champ_iris, champ_adresse,
                   champ_siren=None, sirens_rm=None,
                   champ_epci=None, champ_lat=None, champ_lon=None,
                   champ_circonscription=None, champ_dep=None,
                   nature_iris="inconnue") -> bool:
    if champ_iris:
        ville = str(row.get(champ_ville, "")).strip() if champ_ville else None
        return est_code_rm(str(row.get(champ_iris, "")), ville, nature_iris)
    if champ_adresse:
        return est_adresse_rm(str(row.get(champ_adresse, "")))
    if champ_siren:
        val = str(row.get(champ_siren, "")).strip().replace(" ", "")
        return val.isdigit() and len(val) in (9, 14) and val[:9] in sirens_rm
    if champ_epci:
        return est_epci_rm(str(row.get(champ_epci, "")))
    if champ_lat:
        lon_val = str(row.get(champ_lon, "")).strip() if champ_lon else None
        return est_point_rm(str(row.get(champ_lat, "")).strip(), lon_val)
    if champ_circonscription:
        return est_circonscription_rm(str(row.get(champ_circonscription, "")))
    if champ_dep:
        return est_departement_rm(str(row.get(champ_dep, "")))
    cp = str(row.get(champ_cp, "")).strip() if champ_cp else ""
    ville = str(row.get(champ_ville, "")).strip() if champ_ville else ""
    if champ_cp and champ_ville:
        return est_dans_rm(ville, cp)
    if champ_ville:
        return est_commune_rm(ville)
    if champ_cp:
        return cp in CODES_POSTAUX_RM
    return False


def _extraire_csvs_zip(chemin: str) -> list[tuple[str, bytes]]:
    """Extrait les fichiers CSV d'une archive ZIP. Retourne [(nom_membre, contenu_csv), ...]."""
    with zipfile.ZipFile(chemin) as zf:
        return [
            (nom, zf.read(nom))
            for nom in zf.namelist()
            if nom.lower().endswith(".csv") and not nom.startswith("__MACOSX")
        ]
