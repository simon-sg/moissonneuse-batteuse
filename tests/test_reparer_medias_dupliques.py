import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from reparer_medias_dupliques import dedoublonner_available_formats


def _file(nom):
    return {"media_type": "FILE", "media_name": nom, "media_id": "x"}


def _service(nom):
    return {"media_type": "SERVICE", "media_name": nom}


class TestDedoublonnerAvailableFormats(unittest.TestCase):
    def test_retire_les_doublons_en_gardant_la_premiere_occurrence(self):
        meta = {"available_formats": [
            _file("a.csv"), _file("b.json"), _file("a.csv"), _file("b.json"),
            _service("source-metadata"),
        ]}
        self.assertEqual(dedoublonner_available_formats(meta), 2)
        noms = [m["media_name"] for m in meta["available_formats"]]
        self.assertEqual(noms, ["a.csv", "b.json", "source-metadata"])

    def test_aucun_doublon_renvoie_zero_et_ne_modifie_rien(self):
        meta = {"available_formats": [_file("a.csv"), _file("b.json"), _service("source-metadata")]}
        initial = [dict(m) for m in meta["available_formats"]]
        self.assertEqual(dedoublonner_available_formats(meta), 0)
        self.assertEqual(meta["available_formats"], initial)

    def test_les_non_file_ne_sont_jamais_dedoublonnes(self):
        """Deux SERVICE au même nom restent (ils portent des URLs différentes)."""
        meta = {"available_formats": [_file("a.csv"), _service("dup"), _service("dup")]}
        self.assertEqual(dedoublonner_available_formats(meta), 0)


if __name__ == "__main__":
    unittest.main()
