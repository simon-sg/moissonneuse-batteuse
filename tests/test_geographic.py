"""Tests des filtres géographiques RM — logique pure, aucun réseau."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from filters.geographic import (
    normaliser, est_commune_rm, est_dans_rm, est_iris_rm, est_epci_rm,
    est_circonscription_rm, normaliser_circonscription, est_departement_rm,
    est_point_rm, est_adresse_rm, est_valeur_commune_rm,
)


class TestNormaliser(unittest.TestCase):
    def test_accents_et_casse(self):
        self.assertEqual(normaliser("Cesson-Sévigné"), "cesson sevigne")

    def test_underscores(self):
        self.assertEqual(normaliser("SAINT_GREGOIRE"), "saint gregoire")

    def test_espaces_bord(self):
        self.assertEqual(normaliser("  Rennes  "), "rennes")


class TestCommuneRM(unittest.TestCase):
    def test_commune_rm(self):
        self.assertTrue(est_commune_rm("Bruz"))
        self.assertTrue(est_commune_rm("l'hermitage"))
        self.assertTrue(est_commune_rm("La Chapelle-des-Fougeretz"))

    def test_hors_rm(self):
        self.assertFalse(est_commune_rm("Nantes"))
        self.assertFalse(est_commune_rm(""))


class TestDansRM(unittest.TestCase):
    def test_couple_valide(self):
        self.assertTrue(est_dans_rm("Bruz", "35170"))

    def test_rennes_trois_cp(self):
        for cp in ("35000", "35200", "35700"):
            self.assertTrue(est_dans_rm("Rennes", cp), cp)

    def test_cp_incoherent(self):
        self.assertFalse(est_dans_rm("Bruz", "35000"))

    def test_hors_rm(self):
        self.assertFalse(est_dans_rm("Nantes", "44000"))


class TestIrisEpci(unittest.TestCase):
    def test_code_insee(self):
        self.assertTrue(est_iris_rm("35238"))       # Rennes
        self.assertFalse(est_iris_rm("44109"))      # Nantes

    def test_code_iris_9_chiffres(self):
        self.assertTrue(est_iris_rm("352380101"))   # IRIS rennais
        self.assertFalse(est_iris_rm("441090101"))

    def test_epci(self):
        self.assertTrue(est_iris_rm("243500139"))   # EPCI RM accepté par est_iris_rm
        self.assertTrue(est_epci_rm("243500139"))
        self.assertFalse(est_epci_rm("243300316"))  # Bordeaux Métropole


class TestCirconscription(unittest.TestCase):
    def test_formats_equivalents(self):
        for forme in ("035-01", "35-01", "3501", "35 01"):
            self.assertEqual(normaliser_circonscription(forme), "035-01", forme)

    def test_appartenance(self):
        self.assertTrue(est_circonscription_rm("035-01"))
        self.assertFalse(est_circonscription_rm("044-01"))

    def test_valeur_trop_courte(self):
        self.assertIsNone(normaliser_circonscription("1"))
        self.assertFalse(est_circonscription_rm(""))


class TestDepartement(unittest.TestCase):
    def test_35(self):
        for forme in ("35", "035", "35 "):
            self.assertTrue(est_departement_rm(forme), forme)

    def test_autres(self):
        self.assertFalse(est_departement_rm("44"))
        self.assertFalse(est_departement_rm(""))


class TestPointRM(unittest.TestCase):
    # Rennes ≈ 48.11 N, -1.68 E ; Nantes ≈ 47.22 N, -1.55 E
    def test_lat_lon_separes(self):
        self.assertTrue(est_point_rm("48.11", "-1.68"))
        self.assertFalse(est_point_rm("47.22", "-1.55"))

    def test_valeurs_invalides(self):
        self.assertFalse(est_point_rm("abc", "def"))
        self.assertFalse(est_point_rm("", None))

    def test_colonne_combinee_lat_lon(self):
        self.assertTrue(est_point_rm("48.11,-1.68", None))
        self.assertTrue(est_point_rm("48.11;-1.68", None))

    def test_wkt_point(self):
        # WKT = lon lat
        self.assertTrue(est_point_rm("POINT(-1.68 48.11)", None))
        self.assertFalse(est_point_rm("POINT(-1.55 47.22)", None))

    def test_wkt_polygon_sommets(self):
        poly_rm = "POLYGON((-1.70 48.10, -1.66 48.10, -1.66 48.13, -1.70 48.13, -1.70 48.10))"
        self.assertTrue(est_point_rm(poly_rm, None))
        poly_hors = "POLYGON((-1.56 47.21, -1.54 47.21, -1.54 47.23, -1.56 47.23, -1.56 47.21))"
        self.assertFalse(est_point_rm(poly_hors, None))

    def test_geojson_geometry(self):
        self.assertTrue(est_point_rm('{"type": "Point", "coordinates": [-1.68, 48.11]}', None))
        self.assertFalse(est_point_rm('{"type": "Point", "coordinates": [-1.55, 47.22]}', None))
        # Feature complet avec geometry imbriquée
        self.assertTrue(est_point_rm(
            '{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-1.68, 48.11]}}', None))


class TestAdresseRM(unittest.TestCase):
    def test_cp_35_rm(self):
        self.assertTrue(est_adresse_rm("12 rue de la Paix, 35000 Rennes"))

    def test_nom_commune(self):
        self.assertTrue(est_adresse_rm("Mairie de Chartres-de-Bretagne"))

    def test_hors_rm(self):
        self.assertFalse(est_adresse_rm("5 cours des 50 Otages, 44000 Nantes"))
        self.assertFalse(est_adresse_rm(""))


class TestValeurCommuneRM(unittest.TestCase):
    def test_cp_insee_nom(self):
        self.assertTrue(est_valeur_commune_rm("35170"))    # CP Bruz
        self.assertTrue(est_valeur_commune_rm("35238"))    # INSEE Rennes
        self.assertTrue(est_valeur_commune_rm("Pacé"))

    def test_vide_et_hors(self):
        self.assertFalse(est_valeur_commune_rm(""))
        self.assertFalse(est_valeur_commune_rm("Vannes"))


if __name__ == "__main__":
    unittest.main()
