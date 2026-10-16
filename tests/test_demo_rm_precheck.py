"""Tests de demo_rm_precheck — logique pure (découverte disque, enregistrement), aucun
réseau réel (le writer RUDI est entièrement mocké)."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import demo_rm_precheck as drp


class _FauxWriter:
    def __init__(self, par_local_id):
        self._par_local_id = par_local_id

    def filter_metadata_list(self, filtre):
        lid = filtre.get("local_id")
        global_id = self._par_local_id.get(lid)
        return [{"global_id": global_id}] if global_id else []


class TestDossiersLocaux(unittest.TestCase):
    def test_balaie_tous_les_dossiers_avec_rudi_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            for nom, meta in [("d1", {"local_id": "lid-1"}), ("d2", {"local_id": "lid-2"})]:
                dossier = os.path.join(tmp, nom)
                os.makedirs(dossier)
                with open(os.path.join(dossier, "rudi_metadata.json"), "w", encoding="utf-8") as f:
                    json.dump(meta, f)
            # dossier sans rudi_metadata.json : ignoré
            os.makedirs(os.path.join(tmp, "d3-vide"))
            with mock.patch.object(drp, "DATA_DIR", tmp):
                resultats = drp._dossiers_locaux()
        self.assertEqual([nom for nom, _ in resultats], ["d1", "d2"])

    def test_dossier_sans_data_dir_valide_retourne_vide(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(drp, "DATA_DIR", tmp):
                self.assertEqual(drp._dossiers_locaux(), [])


class TestEnregistrerPublications(unittest.TestCase):
    def test_enregistre_tous_les_dossiers_pas_seulement_les_datasets_cibles(self):
        """Généralisé le 2026-10-16 : une salve de JDD divers (pas dans DATASETS) doit
        être enregistrée, pas seulement les 2 DATASETS curés."""
        with tempfile.TemporaryDirectory() as tmp:
            for nom, lid in [("mydataball_communes", "lid-mdb"), ("un-autre-jdd", "lid-autre")]:
                dossier = os.path.join(tmp, nom)
                os.makedirs(dossier)
                with open(os.path.join(dossier, "rudi_metadata.json"), "w", encoding="utf-8") as f:
                    json.dump({"local_id": lid}, f)
            chemin_publies = os.path.join(tmp, "_publies_par_nous.json")
            writer = _FauxWriter({"lid-mdb": "gid-mdb", "lid-autre": "gid-autre"})
            with mock.patch.object(drp, "DATA_DIR", tmp), \
                 mock.patch.object(drp, "CHEMIN_PUBLIES", chemin_publies), \
                 mock.patch.object(drp, "_creer_writer", return_value=writer):
                publies = drp.enregistrer_publications({})
        self.assertEqual(publies["lid-mdb"]["global_id"], "gid-mdb")
        self.assertEqual(publies["lid-autre"]["global_id"], "gid-autre")
        self.assertEqual(publies["lid-autre"]["dossier"], "un-autre-jdd")

    def test_dossier_absent_du_noeud_nest_pas_enregistre(self):
        with tempfile.TemporaryDirectory() as tmp:
            dossier = os.path.join(tmp, "pas-encore-publie")
            os.makedirs(dossier)
            with open(os.path.join(dossier, "rudi_metadata.json"), "w", encoding="utf-8") as f:
                json.dump({"local_id": "lid-x"}, f)
            chemin_publies = os.path.join(tmp, "_publies_par_nous.json")
            writer = _FauxWriter({})
            with mock.patch.object(drp, "DATA_DIR", tmp), \
                 mock.patch.object(drp, "CHEMIN_PUBLIES", chemin_publies), \
                 mock.patch.object(drp, "_creer_writer", return_value=writer):
                publies = drp.enregistrer_publications({})
        self.assertEqual(publies, {})


if __name__ == "__main__":
    unittest.main()
