"""Tests de la règle de rattrapage multi-nœuds (logique pure, sans réseau)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from publish_rudi import _noeuds_a_rattraper

DOCKER = {"nom": "docker", "url": "http://localhost:3032", "principal": True}
SOURCE = {"nom": "source", "url": "http://localhost:4032"}
CONFS = [DOCKER, SOURCE]


def _noms(confs):
    return [c["nom"] for c in confs]


class TestNoeudsARattraper(unittest.TestCase):
    """Trois états, trois comportements — voir la docstring de publish_rudi."""

    def test_publie_partout_rien_a_faire(self):
        rp = {"docker": True, "source": True}
        self.assertEqual(_noms(_noeuds_a_rattraper(rp, CONFS, False)), [])

    def test_echec_est_rattrape(self):
        rp = {"docker": True, "source": False}
        self.assertEqual(_noms(_noeuds_a_rattraper(rp, CONFS, False)), ["source"])

    def test_cle_absente_est_hors_perimetre(self):
        """Le cœur de « rien de rétroactif » : les 372 JDD publiés avant l'ajout du
        nœud source portent {"docker": True} et ne doivent pas partir vers source."""
        rp = {"docker": True}
        self.assertEqual(_noms(_noeuds_a_rattraper(rp, CONFS, False)), [])

    def test_retroactif_force_les_cles_absentes(self):
        rp = {"docker": True}
        self.assertEqual(_noms(_noeuds_a_rattraper(rp, CONFS, True)), ["source"])

    def test_retroactif_respecte_les_deja_publies(self):
        """--retroactif amorce un nœud, il ne republie pas ce qui est déjà en place."""
        rp = {"docker": True, "source": True}
        self.assertEqual(_noms(_noeuds_a_rattraper(rp, CONFS, True)), [])

    def test_etat_herite_rattrape_docker_seulement(self):
        """Entrée d'avant le multi-nœuds (lire_rudi_publie → {"docker": False})."""
        rp = {"docker": False}
        self.assertEqual(_noms(_noeuds_a_rattraper(rp, CONFS, False)), ["docker"])

    def test_noeud_non_configure_ignore(self):
        """Un nom présent dans l'état mais plus dans la config ne déclenche rien."""
        rp = {"docker": True, "ancien": False}
        self.assertEqual(_noms(_noeuds_a_rattraper(rp, [DOCKER], False)), [])


if __name__ == "__main__":
    unittest.main()
