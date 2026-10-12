import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from publish_rudi import _fichiers_a_uploader


def _metadata(media_name: str) -> dict:
    return {
        "available_formats": [
            {"media_type": "FILE", "media_name": media_name},
            {"media_type": "SERVICE", "media_name": "source-metadata"},
        ]
    }


class TestFichiersAUploader(unittest.TestCase):
    def test_media_name_correspond_au_fichier_reel(self):
        """Cas INSEE/OEB/BDNB/géo : le fichier sur disque porte déjà le nom de media_name."""
        with tempfile.TemporaryDirectory() as dossier:
            chemin_reel = os.path.join(dossier, "dataset-rennesmetropole.csv")
            open(chemin_reel, "w").close()
            fichiers = _fichiers_a_uploader(dossier, _metadata("dataset-rennesmetropole.csv"))
        self.assertEqual(fichiers, [chemin_reel])

    def test_repli_sur_filtered_csv_si_media_name_absent_du_disque(self):
        """Cas main.py (DATASETS tabulaire) : seul filtered.csv existe, media_name diffère."""
        with tempfile.TemporaryDirectory() as dossier:
            chemin_reel = os.path.join(dossier, "filtered.csv")
            open(chemin_reel, "w").close()
            fichiers = _fichiers_a_uploader(dossier, _metadata("60f54b6c7da17f8ddd594a69-rennesmtropole.csv"))
        self.assertEqual(fichiers, [chemin_reel])

    def test_repli_sur_filtered_json_si_media_name_absent_du_disque(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin_reel = os.path.join(dossier, "filtered.json")
            open(chemin_reel, "w").close()
            fichiers = _fichiers_a_uploader(dossier, _metadata("un-autre-nom.json"))
        self.assertEqual(fichiers, [chemin_reel])

    def test_aucun_repli_disponible_renvoie_le_chemin_attendu_introuvable(self):
        """Ni media_name ni les noms de repli n'existent — le chemin (absent) attendu est
        quand même renvoyé, pour laisser publier_dataset() l'écarter explicitement avec son
        propre message, plutôt que de masquer silencieusement un vrai fichier manquant."""
        with tempfile.TemporaryDirectory() as dossier:
            fichiers = _fichiers_a_uploader(dossier, _metadata("absent.csv"))
        self.assertEqual(fichiers, [os.path.join(dossier, "absent.csv")])


if __name__ == "__main__":
    unittest.main()
