"""Tests de la gestion d'état (state*.json)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from state import (charger_etat, sauvegarder_etat, dataset_a_change, construire_index_dossier,
                   lire_rudi_publie, ecrire_rudi_publie, compter_publies, compter_publies_noeud)


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


class TestLireRudiPublie(unittest.TestCase):
    """Tests de migration douce bool → dict pour rudi_publie."""

    def test_valeur_absente(self):
        """Entrée écrite avant l'existence du flag : à rattraper, pas à oublier."""
        self.assertEqual(lire_rudi_publie({}), {"docker": False})

    def test_valeur_none(self):
        self.assertEqual(lire_rudi_publie({"rudi_publie": None}), {"docker": False})

    def test_dict_vide(self):
        """Moisson faite avant qu'un nœud ne soit configuré : à rattraper aussi."""
        self.assertEqual(lire_rudi_publie({"rudi_publie": {}}), {"docker": False})

    def test_booleen_herite_true(self):
        """Un booléen hérité est lu comme {'docker': True}."""
        self.assertEqual(lire_rudi_publie({"rudi_publie": True}), {"docker": True})

    def test_booleen_herite_false(self):
        self.assertEqual(lire_rudi_publie({"rudi_publie": False}), {"docker": False})

    def test_dict_present(self):
        val = {"docker": True, "source": False}
        self.assertEqual(lire_rudi_publie({"rudi_publie": val}), val)

    def test_dict_retourne_est_une_copie(self):
        """Muter le résultat ne doit pas modifier l'état sous-jacent."""
        entree = {"rudi_publie": {"docker": True}}
        lire_rudi_publie(entree)["docker"] = False
        self.assertEqual(entree["rudi_publie"], {"docker": True})

    def test_valeur_inattendue(self):
        """Valeur illisible : republier est idempotent, oublier est définitif."""
        self.assertEqual(lire_rudi_publie({"rudi_publie": "oui"}), {"docker": False})


class TestEcrireRudiPublie(unittest.TestCase):
    def test_ecrit_dict(self):
        entree = {}
        ecrire_rudi_publie(entree, {"docker": True, "source": False})
        self.assertEqual(entree["rudi_publie"], {"docker": True, "source": False})

    def test_ecrase_valeur_precedente(self):
        entree = {"rudi_publie": True}
        ecrire_rudi_publie(entree, {"docker": False, "source": True})
        self.assertEqual(entree["rudi_publie"], {"docker": False, "source": True})


class TestCompterPublies(unittest.TestCase):
    def test_aucun(self):
        self.assertEqual(compter_publies({}), 0)

    def test_un_noeud(self):
        state = {"jdd-a": {"rudi_publie": {"docker": True}},
                 "jdd-b": {"rudi_publie": {"docker": False}}}
        self.assertEqual(compter_publies(state), 1)

    def test_multi_noeuds(self):
        state = {"jdd-a": {"rudi_publie": {"docker": True, "source": True}},
                 "jdd-b": {"rudi_publie": {"docker": False, "source": False}}}
        self.assertEqual(compter_publies(state), 1)

    def test_legacy_bool(self):
        state = {"jdd-a": {"rudi_publie": True}}
        self.assertEqual(compter_publies(state), 1)


class TestCompterPubliesNoeud(unittest.TestCase):
    def test_compte_par_noeud(self):
        state = {"jdd-a": {"rudi_publie": {"docker": True, "source": False}},
                 "jdd-b": {"rudi_publie": {"docker": True, "source": True}}}
        self.assertEqual(compter_publies_noeud(state, "docker"), 2)
        self.assertEqual(compter_publies_noeud(state, "source"), 1)
        self.assertEqual(compter_publies_noeud(state, "inexistant"), 0)


if __name__ == "__main__":
    unittest.main()
