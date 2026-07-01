"""
Point d'entrée unique pour une exécution planifiée (cron/Jenkins) du pipeline complet,
découverte de nouveaux JDD incluse — sans aucune interaction humaine.

Usage : python3 src/harvest_auto.py

Enchaîne :
  1. Découverte automatique (discover.rechercher_et_filtrer_auto()) — recherche sur
     data.gouv.fr, ajout automatique des candidats tabulaires avec données RM détectées,
     mise en attente (decouverte["a_examiner"]) des cas ambigus (0 RM, échec d'analyse,
     services WFS/WMS) pour revue différée via le tableau de bord web.
  2. Démarrage du nœud RUDI local (conteneur Podman) si configuré et pas déjà démarré,
     avec attente qu'il réponde réellement avant de lancer la publication.
  3. Pipeline complet existant (cli.executer_pipeline_complet()) : moisson
     tabulaire/batch/INSEE/OEB/BDNB/géo → catalogue → publication RUDI.

Code de sortie 0 si toutes les étapes ont réussi, 1 sinon (pour Jenkins/cron).
Chaque étape est déjà tolérante aux pannes partielles (source indisponible, nœud RUDI
injoignable...) — ce script se contente d'orchestrer, sans logique métier propre.

Logs : chaque exécution écrit l'intégralité de sa sortie (identique à la console) dans
logs/harvest_auto_<horodatage>.log — un fichier par run, pour retrouver facilement les
erreurs d'une exécution cron précise sans dépouiller un unique fichier qui grossirait
indéfiniment. `cli._executer()` imprime la trace complète (pas seulement le message) de
toute exception levée par une étape, donc le log suffit à diagnostiquer sans reproduire.
"""
import datetime
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discover
import cli
from connectors import rudi_node

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

# Délai maximum d'attente du nœud RUDI après démarrage du conteneur (l'appli interne
# met plusieurs secondes à démarrer après que Podman rapporte le conteneur "running").
_NOEUD_RUDI_TENTATIVES = 20
_NOEUD_RUDI_DELAI_S = 3


class _TeeFichier:
    """Écrit à la fois sur la sortie standard d'origine et dans le fichier de log du run
    (même principe que le _Tee de dashboard.py, mais vers un fichier plutôt qu'un buffer web)."""

    def __init__(self, fichier, original):
        self._fichier = fichier
        self._original = original

    def write(self, s):
        self._fichier.write(s)
        self._original.write(s)
        return len(s)

    def flush(self):
        self._fichier.flush()
        self._original.flush()


def _demarrer_noeud_rudi() -> None:
    """Démarre le conteneur du nœud RUDI si nécessaire et attend qu'il réponde.
    N'échoue jamais : si le nœud reste indisponible, la publication de cette exécution
    sera simplement différée (rudi_publie=false, rattrapée au prochain run)."""
    conf = rudi_node.charger_conf_rudi()
    if not conf:
        print("[Nœud RUDI] non configuré (src/conf/rudi_node.json absent) — publication ignorée cette fois.")
        return

    statut = rudi_node.statut_conteneur()
    if statut.get("etat") != "running":
        print(f"[Nœud RUDI] conteneur non démarré (état={statut.get('etat')!r}) — démarrage...")
        ok, message = rudi_node.demarrer_conteneur()
        print(f"[Nœud RUDI] {message}")
        if not ok:
            return
    else:
        print("[Nœud RUDI] conteneur déjà démarré.")

    for tentative in range(1, _NOEUD_RUDI_TENTATIVES + 1):
        if rudi_node.noeud_pret(conf):
            print(f"[Nœud RUDI] prêt (après {tentative} tentative(s)).")
            return
        time.sleep(_NOEUD_RUDI_DELAI_S)
    print(f"[Nœud RUDI] toujours indisponible après {_NOEUD_RUDI_TENTATIVES * _NOEUD_RUDI_DELAI_S}s — "
          "la publication sera tentée quand même, puis rattrapée au prochain run si elle échoue.")


def _executer_pipeline() -> int:
    debut = time.time()
    print(f"\n{'=' * 60}\nDécouverte automatique (non-interactive)\n{'=' * 60}")
    decouverte = discover.charger_decouverte()
    try:
        stats_decouverte = discover.rechercher_et_filtrer_auto(decouverte)
        print(f"  {stats_decouverte['analyses']} JDD analysé(s) — "
              f"{stats_decouverte['candidats_auto']} candidat(s) ajouté(s) automatiquement, "
              f"{stats_decouverte['a_examiner']} en attente d'examen (backlog cumulé), "
              f"{stats_decouverte['ignores']} ignoré(s) sans marqueur géo, "
              f"{stats_decouverte['echecs_analyse']} échec(s) d'analyse.")
    except Exception as e:
        print(f"  ERREUR pendant la découverte automatique : {e}")
        traceback.print_exc()
        stats_decouverte = None

    print(f"\n{'=' * 60}\nNœud RUDI\n{'=' * 60}")
    _demarrer_noeud_rudi()

    resultats = cli.executer_pipeline_complet()

    duree = time.time() - debut
    print(f"\n{'=' * 60}\nRésumé harvest_auto.py\n{'=' * 60}")
    if stats_decouverte is not None:
        print(f"  Découverte : {stats_decouverte['candidats_auto']} nouveau(x) candidat(s), "
              f"{stats_decouverte['a_examiner']} en attente d'examen")
    else:
        print("  Découverte : ÉCHEC (voir erreur ci-dessus)")
    for label, ok in resultats:
        print(f"  {'✓' if ok else '✗'} {label}")
    print(f"  Durée totale : {duree / 60:.1f} min")

    succes = (stats_decouverte is not None) and all(ok for _, ok in resultats)
    return 0 if succes else 1


def main() -> int:
    os.makedirs(LOG_DIR, exist_ok=True)
    horodatage = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    chemin_log = os.path.join(LOG_DIR, f"harvest_auto_{horodatage}.log")
    ancien_stdout = sys.stdout
    with open(chemin_log, "w", encoding="utf-8") as f:
        sys.stdout = _TeeFichier(f, ancien_stdout)
        try:
            code = _executer_pipeline()
        finally:
            sys.stdout = ancien_stdout
    print(f"\nLog complet de ce run : {chemin_log}")
    return code


if __name__ == "__main__":
    sys.exit(main())
