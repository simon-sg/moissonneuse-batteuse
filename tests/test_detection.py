"""Tests de la cascade de détection de colonnes et du délimiteur CSV."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from connectors.analyseurs import _detecter_champs, _detecter_delimiteur

# Indices du tuple retourné par _detecter_champs
CP, VILLE, IRIS, DEP, EPCI, ADRESSE, SIREN, LAT, LON, CIRCO = range(10)


class TestDetecterChamps(unittest.TestCase):
    def test_cp_ville(self):
        champs = _detecter_champs(["code_postal", "commune", "valeur"])
        self.assertEqual(champs[CP], "code_postal")
        self.assertEqual(champs[VILLE], "commune")

    def test_iris_prioritaire(self):
        champs = _detecter_champs(["code_insee", "adresse", "siret"])
        self.assertEqual(champs[IRIS], "code_insee")
        # cascade mutuellement exclusive : adresse/siren non retenus
        self.assertIsNone(champs[ADRESSE])
        self.assertIsNone(champs[SIREN])

    def test_adresse_seule(self):
        champs = _detecter_champs(["adresse", "valeur"])
        self.assertEqual(champs[ADRESSE], "adresse")

    def test_siren_dernier_avant_geo(self):
        champs = _detecter_champs(["siret", "valeur"])
        self.assertEqual(champs[SIREN], "siret")

    def test_lat_lon(self):
        champs = _detecter_champs(["latitude", "longitude", "valeur"])
        self.assertEqual(champs[LAT], "latitude")
        self.assertEqual(champs[LON], "longitude")

    def test_circonscription_dernier_recours(self):
        champs = _detecter_champs(["code_circonscription", "valeur"])
        self.assertEqual(champs[CIRCO], "code_circonscription")

    def test_rien_detecte(self):
        champs = _detecter_champs(["foo", "bar"])
        self.assertTrue(all(c is None for c in champs))


class TestDetecterDelimiteur(unittest.TestCase):
    def test_point_virgule(self):
        self.assertEqual(_detecter_delimiteur("a;b;c\n1;2;3"), ";")

    def test_virgule(self):
        self.assertEqual(_detecter_delimiteur("a,b,c\n1,2,3"), ",")

    def test_tabulation(self):
        self.assertEqual(_detecter_delimiteur("a\tb\tc\n1\t2\t3"), "\t")

    def test_entete_avec_virgules_dans_guillemets(self):
        # en-tête majoritairement ';' avec une virgule piégée
        sample = 'code;"libellé, complet";valeur\n35238;"Rennes, ville";1'
        self.assertEqual(_detecter_delimiteur(sample), ";")

    def test_fallback_virgule(self):
        self.assertEqual(_detecter_delimiteur("colonne_unique\nvaleur"), ",")


if __name__ == "__main__":
    unittest.main()
