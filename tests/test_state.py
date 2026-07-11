"""Tests de la gestion d'état (state*.json)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from state import charger_etat, sauvegarder_etat, dataset_a_change, construire_index_dossier


class TestChargerEtat(unittest.TestCase):
    def test_fichier_absent(self):
        self.assertEqual(charger_etat("/chemin/inexistant/state.json"), {})

    def test_fichier_corrompu(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{ pas du json")
            chemin = f.name
        try:
            self.assertEqual(charger_etat(chemin), {})
        finally:
            os.unlink(chemin)

    def test_aller_retour(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "state.json")
            sauvegarder_etat(chemin, {"jdd": {"nb_rm": 3}})
            self.assertEqual(charger_etat(chemin), {"jdd": {"nb_rm": 3}})


class TestDatasetAChange(unittest.TestCase):
    def test_jamais_vu(self):
        self.assertTrue(dataset_a_change({}, "jdd", "2025-01-01"))

    def test_inchange(self):
        state = {"jdd": {"last_modified": "2025-01-01"}}
        self.assertFalse(dataset_a_change(state, "jdd", "2025-01-01"))

    def test_modifie(self):
        state = {"jdd": {"last_modified": "2025-01-01"}}
        self.assertTrue(dataset_a_change(state, "jdd", "2025-06-01"))


class TestConstruireIndexDossier(unittest.TestCase):
    def test_index_multi_sources(self):
        index = construire_index_dossier(
            ("tabulaire", {"jdd-a": {"dossier": "dossier-a"}}),
            ("insee", {"pub-b": {"dossier": "dossier-b"}, "sans-dossier": {}}),
        )
        self.assertEqual(index["dossier-a"], ("tabulaire", "jdd-a"))
        self.assertEqual(index["dossier-b"], ("insee", "pub-b"))
        self.assertEqual(len(index), 2)  # l'entrée sans dossier est ignorée


if __name__ == "__main__":
    unittest.main()
