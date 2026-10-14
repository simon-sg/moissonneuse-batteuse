"""
Réparation ponctuelle : dédoublonne les médias FILE des `rudi_metadata.json` sur disque.

Cause : deux ressources sources au même titre produisent le même slug, donc le même nom de
fichier filtré. `available_formats` porte alors deux médias FILE identiques (même
`media_name`, même `media_id`). À la publication, le nœud les envoie au portail sous le même
identifiant et Kalim plante à l'intégration (bug A7 : IllegalStateException « Duplicate key »
→ rapport `ERR-500`) : le JDD reste `refused` côté nœud alors qu'il est déjà intégré au
portail. (Le générateur `harvest_batch` ne produit plus ces doublons, et `publier_dataset()`
les filtre désormais côté toolkit ; ce script rattrape les fiches déjà écrites.)

Pour chaque dossier concerné, le script retire les doublons d'`available_formats`, réécrit
le `rudi_metadata.json`, puis re-publie le JDD sur le nœud (PUT producteur normal, cf.
`publier_dataset()`) : le portail met à jour la fiche et le JDD repasse en `published`.

Les dossiers `refused` en `ERR-112` (organisation productrice non encore validée en UI) ne
sont pas concernés ici.

Usage :
    python3 src/reparer_medias_dupliques.py [--appliquer]

    Sans `--appliquer`, dry-run : n'écrit rien, liste seulement les dossiers concernés.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rudi_harvest_toolkit.rudi_client import (
    publier_dataset, charger_conf_rudi)
from rudi_harvest_toolkit.publish import enregistrer_local_id_publie
from publish_rudi import _fichiers_a_uploader

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CONF_RUDI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "rudi_node.json")


def dedoublonner_available_formats(rudi_metadata: dict) -> int:
    """
    Retire d'`available_formats` les médias FILE dupliqués (même `media_name`, garde la
    première occurrence). Retourne le nombre de doublons retirés (0 = rien à faire).
    """
    formats = rudi_metadata.get("available_formats", [])
    vus: set[str] = set()
    uniques = []
    for media in formats:
        if media.get("media_type") == "FILE":
            nom = media.get("media_name", "")
            if nom in vus:
                continue
            vus.add(nom)
        uniques.append(media)
    if len(uniques) == len(formats):
        return 0
    rudi_metadata["available_formats"] = uniques
    return len(formats) - len(uniques)


def dossiers_a_duplication() -> list[tuple[str, int]]:
    """Liste [(dossier, nb_doublons)] des dossiers dont le rudi_metadata.json a des doublons."""
    resultats = []
    for nom in sorted(os.listdir(DATA_DIR)):
        chemin_meta = os.path.join(DATA_DIR, nom, "rudi_metadata.json")
        if not os.path.isfile(chemin_meta):
            continue
        try:
            with open(chemin_meta, encoding="utf-8") as f:
                rudi_metadata = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  AVERTISSEMENT : {chemin_meta} illisible ({e}), ignoré.")
            continue
        nb = sum(
            1 for i, m in enumerate(rudi_metadata.get("available_formats", []))
            if m.get("media_type") == "FILE"
            and m.get("media_name") in {
                x.get("media_name") for x in rudi_metadata["available_formats"][:i]
                if x.get("media_type") == "FILE"
            }
        )
        if nb:
            resultats.append((nom, nb))
    return resultats


def main(appliquer: bool | None = None) -> None:
    if appliquer is None:
        appliquer = "--appliquer" in sys.argv[1:]

    concernees = dossiers_a_duplication()
    if not concernees:
        print("✅ Aucun rudi_metadata.json avec des médias FILE dupliqués.")
        return

    print(f"{len(concernees)} dossier(s) avec des médias FILE dupliqués "
          f"({'correction + re-publication' if appliquer else 'dry-run — rien ne sera écrit'}) :")
    for nom, nb in concernees:
        print(f"  • {nom}  ({nb} doublon(s))")

    if not appliquer:
        print("\nRelancer avec --appliquer pour dédoublonner et re-publier.")
        return

    conf_rudi = charger_conf_rudi(CONF_RUDI_FILE)
    if not conf_rudi:
        print("src/conf/rudi_node.json absent — impossible de re-publier (correction locale seule).")

    print()
    ok, echecs = 0, 0
    for nom, _ in concernees:
        dossier_path = os.path.join(DATA_DIR, nom)
        chemin_meta = os.path.join(dossier_path, "rudi_metadata.json")
        print(f"--- {nom} ---")
        try:
            with open(chemin_meta, encoding="utf-8") as f:
                rudi_metadata = json.load(f)
            nb = dedoublonner_available_formats(rudi_metadata)
            with open(chemin_meta, "w", encoding="utf-8") as f:
                json.dump(rudi_metadata, f, ensure_ascii=False, indent=2)
            print(f"  {nb} média(s) FILE dupliqué(s) retiré(s) du rudi_metadata.json.")
            if conf_rudi:
                fichiers = _fichiers_a_uploader(dossier_path, rudi_metadata)
                publier_dataset(conf=conf_rudi, rudi_metadata=rudi_metadata,
                                fichiers_filtres=fichiers)
                enregistrer_local_id_publie(rudi_metadata.get("local_id"))
            ok += 1
        except Exception as e:
            print(f"  ERREUR : {e}")
            echecs += 1
        print()

    print(f"=== Terminé : {ok} corrigé(s), {echecs} échec(s) sur {len(concernees)} ===")
    print("Les JDD concernés repasseront en `published` une fois le rapport portail revenu "
          "(revérifier `metadata_status` côté nœud, ou relancer `reparer_jdd_refuses.py`).")


if __name__ == "__main__":
    main()
