"""
Rattrapage offline de la corruption d'accents dans les CSV déjà moissonnés.

Bug corrigé dans filters/harvest.py::_detecter_encodage_bytes (2026-07-21) :
l'ancienne détection tolérait jusqu'à 10 caractères de remplacement dans
l'échantillon avant de renoncer à l'UTF-8 — un CSV réellement Windows-1252
(le cas quasi systématique hors UTF-8 sur data.gouv.fr) passait souvent sous
ce seuil, et le fichier entier était ensuite décodé avec errors="replace" :
chaque octet accentué non conforme se retrouvait gravé en "�" (U+FFFD) dans
le CSV filtré final — celui publié tel quel sur RUDI.

Cette corruption n'est PAS récupérable depuis le fichier déjà filtré : l'octet
d'origine est perdu, remplacé. La seule réparation possible est de relancer la
moisson du JDD concerné avec le détecteur corrigé, depuis la ressource source
(cache disque data/cache/ si encore présent, sinon re-téléchargement ciblé —
uniquement pour les JDD détectés corrompus, pas un re-scan massif).

Ce script ne fait que DÉTECTER les fichiers corrompus (présence de U+FFFD,
signature fiable de l'ancien bug — un CSV correctement décodé n'en contient
jamais) et, avec --appliquer, supprimer l'entrée state.json/state_insee.json/
state_oeb.json/state_bdnb.json du JDD concerné pour forcer sa re-moisson
complète au prochain run normal. Il ne télécharge ni ne réécrit aucun fichier
lui-même — c'est le script de moisson d'origine (déjà corrigé) qui s'en charge.

Usage :
    python3 src/reencoder_accents.py               # dry-run : rapport seul
    python3 src/reencoder_accents.py --appliquer   # réinitialise les états ciblés

Après --appliquer, relancer la moisson des JDD listés (main.py pour un dataset
DATASETS, harvest_batch.py pour un candidat découvert, harvest_insee.py/
harvest_oeb.py/harvest_bdnb.py selon la source indiquée), puis catalogue.py →
publish_rudi.py (nœud démarré) pour republier les fichiers corrigés.
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from state import charger_etat, sauvegarder_etat, construire_index_dossier

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

STATE_FILES = {
    "tabulaire": os.path.join(DATA_DIR, "state.json"),
    "insee": os.path.join(DATA_DIR, "state_insee.json"),
    "oeb": os.path.join(DATA_DIR, "state_oeb.json"),
    "bdnb": os.path.join(DATA_DIR, "state_bdnb.json"),
}

_DOSSIERS_EXCLUS = {"cache"}


def detecter_fichiers_corrompus() -> dict[str, list[tuple[str, int]]]:
    """Scanne data/<dossier>/*.csv pour des caractères de remplacement U+FFFD.
    Retourne {dossier: [(nom_fichier, nb_remplacements), ...]}."""
    par_dossier: dict[str, list[tuple[str, int]]] = {}
    if not os.path.isdir(DATA_DIR):
        return par_dossier
    for dossier in sorted(os.listdir(DATA_DIR)):
        if dossier in _DOSSIERS_EXCLUS:
            continue
        chemin_dossier = os.path.join(DATA_DIR, dossier)
        if not os.path.isdir(chemin_dossier):
            continue
        for chemin in glob.glob(os.path.join(chemin_dossier, "*.csv")):
            try:
                with open(chemin, encoding="utf-8", errors="strict") as f:
                    contenu = f.read()
            except UnicodeDecodeError:
                continue  # pas de l'UTF-8 propre du tout : hors périmètre de ce rattrapage
            n = contenu.count("�")
            if n:
                par_dossier.setdefault(dossier, []).append((os.path.basename(chemin), n))
    return par_dossier


def executer(appliquer: bool = False) -> int:
    corrompus = detecter_fichiers_corrompus()
    if not corrompus:
        print("Aucun CSV moissonné ne contient de caractère de remplacement (U+FFFD).")
        return 0

    etats = {source: charger_etat(chemin) for source, chemin in STATE_FILES.items()}
    index = construire_index_dossier(*etats.items())

    print(f"{len(corrompus)} dossier(s) avec accents corrompus détectés "
          f"({'APPLICATION' if appliquer else 'dry-run'}) :\n")

    a_reinitialiser = []   # [(dossier, source, cle), ...]
    sans_correspondance = []
    for dossier in sorted(corrompus):
        fichiers = corrompus[dossier]
        total = sum(n for _, n in fichiers)
        correspondance = index.get(dossier)
        if correspondance:
            source, cle = correspondance
            print(f"  {dossier}  ({len(fichiers)} fichier(s), {total} car. remplacés)  "
                  f"→ {source}[{cle!r}] sera réinitialisé")
            a_reinitialiser.append((dossier, source, cle))
        else:
            print(f"  {dossier}  ({len(fichiers)} fichier(s), {total} car. remplacés)  "
                  f"[!] aucune correspondance d'état — reprise manuelle nécessaire "
                  f"(service géo ? dossier renommé ?)")
            sans_correspondance.append(dossier)
        for nom, n in fichiers:
            print(f"      {nom} : {n}")

    if not appliquer:
        print("\nDry-run terminé — rien n'a été modifié. Relancer avec --appliquer.")
        return 0

    sources_touchees = set()
    for dossier, source, cle in a_reinitialiser:
        if cle in etats[source]:
            del etats[source][cle]
            sources_touchees.add(source)
            print(f"→ {source}[{cle!r}] supprimé (force la re-moisson complète)")

    for source in sources_touchees:
        sauvegarder_etat(STATE_FILES[source], etats[source])

    print("\nApplication terminée. Relancer la moisson des sources concernées "
          f"({', '.join(sorted(sources_touchees)) or 'aucune'}) pour reconstruire "
          "les fichiers avec le détecteur d'encodage corrigé, par exemple :")
    if "tabulaire" in sources_touchees:
        print("  python3 src/main.py          # si un dossier listé est un DATASETS (MyDataBall)")
        print("  python3 src/harvest_batch.py # si un dossier listé est un candidat découvert")
    if "insee" in sources_touchees:
        print("  python3 src/harvest_insee.py")
    if "oeb" in sources_touchees:
        print("  python3 src/harvest_oeb.py")
    if "bdnb" in sources_touchees:
        print("  python3 src/harvest_bdnb.py")
    print("Puis : python3 src/catalogue.py → python3 src/publish_rudi.py (nœud démarré).")
    if sans_correspondance:
        print(f"\n[!] {len(sans_correspondance)} dossier(s) sans correspondance d'état, "
              f"non traités : {', '.join(sans_correspondance)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Détecte les CSV moissonnés avec des accents corrompus (U+FFFD) "
                    "et force leur re-moisson avec le détecteur d'encodage corrigé.")
    parser.add_argument("--appliquer", action="store_true",
                        help="supprime les entrées state concernées (défaut : dry-run)")
    args = parser.parse_args()
    return executer(appliquer=args.appliquer)


if __name__ == "__main__":
    sys.exit(main())
