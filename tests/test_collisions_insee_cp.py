"""Tests des collisions codes INSEE / codes postaux du dept 35 — logique pure, aucun réseau.

Dans le 35, certains codes INSEE de communes hors RM sont identiques à des CP de
communes RM (35132 = INSEE Hirel, hors RM, ET CP Vezin-le-Coquet, RM) et
inversement (35120 = INSEE Gévezé, RM, ET CP Dol-de-Bretagne, hors RM).
Voir filters/geographic.py (classer_code_rm, est_code_rm, detecter_nature_colonne).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from filters.geographic import (
    classer_code_rm, est_code_rm, detecter_nature_colonne,
)
from filters.harvest import _ligne_est_rm, nature_champ_iris
from connectors.analyseurs import deviner_champ_iris


class TestClasserCodeRM(unittest.TestCase):
    def test_rm_sans_ambiguite(self):
        self.assertEqual(classer_code_rm("35353"), "rm")   # INSEE Vezin, jamais un CP
        self.assertEqual(classer_code_rm("35510"), "rm")   # CP Cesson, jamais un INSEE

    def test_ambigus(self):
        self.assertEqual(classer_code_rm("35132"), "amb_cp")     # CP Vezin / INSEE Hirel
        self.assertEqual(classer_code_rm("35120"), "amb_insee")  # INSEE Gévezé / CP Dol

    def test_double_rm(self):
        # RM sous les deux interprétations (L'Hermitage / Chartres-de-Bretagne)
        self.assertEqual(classer_code_rm("35131"), "rm")

    def test_hors_rm(self):
        self.assertEqual(classer_code_rm("35300"), "hors_rm")  # Fougères, INSEE et CP
        self.assertEqual(classer_code_rm("99999"), "hors_rm")

    def test_epci_et_iris(self):
        self.assertEqual(classer_code_rm("243500139"), "rm")   # EPCI
        self.assertEqual(classer_code_rm("353530102"), "rm")   # IRIS 9 chiffres de Vezin
        # IRIS 9 chiffres de Hirel — un IRIS n'est jamais un CP : PAS d'ambiguïté
        self.assertEqual(classer_code_rm("351320000"), "hors_rm")

    def test_valeurs_non_code(self):
        self.assertEqual(classer_code_rm(""), "hors_rm")
        self.assertEqual(classer_code_rm("123"), "hors_rm")
        self.assertEqual(classer_code_rm("Pacé"), "rm")        # nom de commune RM
        self.assertEqual(classer_code_rm("Vannes"), "hors_rm")


class TestArbitrageVille(unittest.TestCase):
    def test_ville_hors_rm_du_35(self):
        # La ligne dit elle-même être une commune du 35 hors RM
        self.assertEqual(classer_code_rm("35132", "Hirel"), "hors_rm")
        self.assertEqual(classer_code_rm("35132", "HIREL"), "hors_rm")
        self.assertEqual(classer_code_rm("35120", "Dol-de-Bretagne"), "hors_rm")

    def test_ville_rm_corroboree(self):
        self.assertEqual(classer_code_rm("35132", "Vezin-le-Coquet"), "rm")  # corroboration CP
        self.assertEqual(classer_code_rm("35120", "Gévezé"), "rm")

    def test_ville_junk_inchange(self):
        self.assertEqual(classer_code_rm("35132", "Oui"), "amb_cp")

    def test_suffixe_cedex(self):
        self.assertEqual(classer_code_rm("35000", "Rennes cedex 9"), "rm")


class TestEstCodeRM(unittest.TestCase):
    def test_tranchage_par_nature(self):
        self.assertFalse(est_code_rm("35132", nature="insee"))  # le bug d'origine, corrigé
        self.assertTrue(est_code_rm("35132", nature="cp"))
        self.assertFalse(est_code_rm("35132"))                  # défaut inconnu = strict INSEE
        self.assertFalse(est_code_rm("35120", nature="cp"))
        self.assertTrue(est_code_rm("35120", nature="insee"))
        self.assertTrue(est_code_rm("35120"))                   # défaut inconnu = strict INSEE


class TestDetecterNatureColonne(unittest.TestCase):
    def test_colonne_insee(self):
        self.assertEqual(detecter_nature_colonne(["35001", "35047", "35238", "35132"]), "insee")

    def test_colonne_cp(self):
        self.assertEqual(detecter_nature_colonne(["35000", "35510", "35690", "35120"]), "cp")

    def test_que_des_ambigus(self):
        self.assertEqual(detecter_nature_colonne(["35132", "35120"]), "inconnue")

    def test_hors_35_non_discriminant(self):
        self.assertEqual(detecter_nature_colonne(["75056", "69123"]), "inconnue")


class TestDevinerChampIrisGardeFou(unittest.TestCase):
    def test_code_postal_jamais_iris(self):
        self.assertIsNone(deviner_champ_iris(["code_postal", "nom"]))
        self.assertIsNone(deviner_champ_iris(["Code postal lieu de cours", "nom"]))


class TestLigneEstRMIntegration(unittest.TestCase):
    """Cas réels : RNIC (colonne CP taguée iris) et fichier des décès (INSEE + ville)."""

    def test_colonne_cp_taguee_iris_cas_rnic(self):
        # Colonne de nature CP mal taguée champ_iris : la pré-passe nature corrige.
        rows = [
            {"code_postal": "35000", "nom": "a"},   # CP Rennes — RM
            {"code_postal": "35510", "nom": "b"},   # CP Cesson — RM
            {"code_postal": "35690", "nom": "c"},   # CP Acigné — RM
            {"code_postal": "35132", "nom": "d"},   # CP Vezin (INSEE Hirel) — RM en nature cp
            {"code_postal": "35300", "nom": "e"},   # Fougères — hors RM
        ]
        nature = nature_champ_iris("code_postal", iter(rows))
        self.assertEqual(nature, "cp")
        gardees = [r["nom"] for r in rows
                   if _ligne_est_rm(r, None, None, "code_postal", None, nature_iris=nature)]
        self.assertEqual(gardees, ["a", "b", "c", "d"])

    def test_colonne_insee_avec_ville_cas_deces(self):
        # Colonne INSEE + colonne ville : 35132 = INSEE Hirel doit être rejeté.
        rows = [
            {"insee": "35238", "commune": "Rennes"},            # RM
            {"insee": "35001", "commune": "Acigné"},            # RM
            {"insee": "35047", "commune": "Bruz"},              # RM
            {"insee": "35132", "commune": "Hirel"},             # hors RM — le bug d'origine
            {"insee": "35120", "commune": "Gévezé"},            # RM (INSEE Gévezé)
        ]
        nature = nature_champ_iris("insee", iter(rows))
        self.assertEqual(nature, "insee")
        gardees = [r["commune"] for r in rows
                   if _ligne_est_rm(r, None, "commune", "insee", None, nature_iris=nature)]
        self.assertEqual(gardees, ["Rennes", "Acigné", "Bruz", "Gévezé"])

    def test_ville_hirel_rejetee_meme_sans_nature(self):
        # Même avec nature inconnue, l'arbitrage ville rejette Hirel.
        row = {"insee": "35132", "commune": "Hirel"}
        self.assertFalse(_ligne_est_rm(row, None, "commune", "insee", None))


if __name__ == "__main__":
    unittest.main()
