"""Tests des helpers de l'enrichissement des organisations (logo) — logique pure, aucun réseau."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import enrichir_organisations as eo


class TestNomFichierLogo(unittest.TestCase):
    def test_png(self):
        self.assertEqual(eo._nom_fichier_logo("Ministère de la Culture", "image/png"),
                         "minist-re-de-la-culture.png")

    def test_jpeg(self):
        self.assertEqual(eo._nom_fichier_logo("OpenStreetMap", "image/jpeg"),
                         "openstreetmap.jpg")

    def test_nom_vide_retombe_sur_logo(self):
        self.assertEqual(eo._nom_fichier_logo("", "image/png"), "logo.png")

    def test_tronque_a_60_caracteres(self):
        nom = eo._nom_fichier_logo("A" * 200, "image/png")
        self.assertLessEqual(len(nom) - len(".png"), 60)


class TestEtatLogo(unittest.TestCase):
    def test_fichier_absent_retourne_ensemble_vide(self):
        with patch.object(eo, "_ETAT_LOGO_FILE",
                          os.path.join(tempfile.mkdtemp(), "absent.json")):
            self.assertEqual(eo._charger_etat_logo(), set())

    def test_fichier_corrompu_retourne_ensemble_vide(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "etat.json")
            with open(chemin, "w", encoding="utf-8") as f:
                f.write("{pas du json")
            with patch.object(eo, "_ETAT_LOGO_FILE", chemin):
                self.assertEqual(eo._charger_etat_logo(), set())

    def test_aller_retour(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "etat.json")
            with patch.object(eo, "_ETAT_LOGO_FILE", chemin):
                eo._sauvegarder_etat_logo({"id-b", "id-a"})
                self.assertEqual(eo._charger_etat_logo(), {"id-a", "id-b"})
            with open(chemin, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["organisations_traitees"], ["id-a", "id-b"])

    def test_les_none_sont_ignores_a_la_sauvegarde(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "etat.json")
            with patch.object(eo, "_ETAT_LOGO_FILE", chemin):
                eo._sauvegarder_etat_logo({"id-a", None})
                self.assertEqual(eo._charger_etat_logo(), {"id-a"})


if __name__ == "__main__":
    unittest.main()
