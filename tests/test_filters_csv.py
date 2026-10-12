import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from filters.csv import slugifier, tronquer_slug


class TestTronquerSlug(unittest.TestCase):
    def test_reproduit_exemple_simon(self):
        """"Compilation de 3 963 données INSEE portant sur 35 397 communes de France"
        doit donner "compilation-de-3-963-donnees-insee-portant" une fois tronqué à 45,
        pas une coupure en plein milieu d'un mot."""
        titre = "Compilation de 3 963 données INSEE portant sur 35 397 communes de France"
        self.assertEqual(
            tronquer_slug(slugifier(titre), 45),
            "compilation-de-3-963-donnees-insee-portant",
        )

    def test_garde_le_dernier_mot_si_la_coupure_tombe_juste_sur_une_frontiere(self):
        slug = "un-deux-trois-quatre-cinq-six-sept"
        # slug[:20] == "un-deux-trois-quatre" et slug[20] == "-" : la coupure dure tombe
        # déjà sur une frontière de mot, "quatre" ne doit pas être perdu pour rien.
        self.assertEqual(tronquer_slug(slug, 20), "un-deux-trois-quatre")

    def test_recule_si_la_coupure_tombe_en_plein_mot(self):
        slug = "un-deux-trois-quatre-cinq-six-sept"
        # slug[:19] == "un-deux-trois-quatr" (coupe "quatre" en plein milieu) : doit reculer
        # jusqu'au mot entier précédent.
        self.assertEqual(tronquer_slug(slug, 19), "un-deux-trois")

    def test_slug_plus_court_que_max_len_inchange(self):
        self.assertEqual(tronquer_slug("court-slug", 45), "court-slug")

    def test_pas_de_tiret_dans_la_fenetre_tronque_dur(self):
        """Un seul mot sans tiret avant max_len : pas de rsplit possible, coupure dure."""
        self.assertEqual(tronquer_slug("motunique", 5), "motun")


if __name__ == "__main__":
    unittest.main()
