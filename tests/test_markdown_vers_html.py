"""Tests du convertisseur Markdown léger -> HTML — logique pure, aucun réseau."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from translation.markdown_vers_html import markdown_vers_html


class TestMarkdownVersHtml(unittest.TestCase):
    def test_vide(self):
        self.assertEqual(markdown_vers_html(""), "")
        self.assertEqual(markdown_vers_html(None), "")
        self.assertEqual(markdown_vers_html("   \n\n  "), "")

    def test_paragraphe_simple(self):
        self.assertEqual(markdown_vers_html("Un texte simple."), "<p>Un texte simple.</p>")

    def test_deux_paragraphes(self):
        html = markdown_vers_html("Premier paragraphe.\n\nSecond paragraphe.")
        self.assertEqual(html, "<p>Premier paragraphe.</p>\n<p>Second paragraphe.</p>")

    def test_retour_a_la_ligne_simple_devient_br(self):
        html = markdown_vers_html("Première phrase.\nSeconde phrase.")
        self.assertEqual(html, "<p>Première phrase.<br>Seconde phrase.</p>")

    def test_gras(self):
        self.assertEqual(markdown_vers_html("Du **texte en gras** ici."),
                          "<p>Du <strong>texte en gras</strong> ici.</p>")

    def test_italique_etoile_et_tiret(self):
        self.assertEqual(markdown_vers_html("Du *texte en italique*."),
                          "<p>Du <em>texte en italique</em>.</p>")
        self.assertEqual(markdown_vers_html("Du _texte en italique_."),
                          "<p>Du <em>texte en italique</em>.</p>")

    def test_liste_a_puces(self):
        html = markdown_vers_html("- simplification à 5m\n- simplification à 50m")
        self.assertEqual(html, "<ul><li>simplification à 5m</li><li>simplification à 50m</li></ul>")

    def test_liste_numerotee(self):
        html = markdown_vers_html("1. premier\n2. second")
        self.assertEqual(html, "<ol><li>premier</li><li>second</li></ol>")

    def test_lien_markdown(self):
        html = markdown_vers_html("Voir [le site](https://example.org/page).")
        self.assertEqual(
            html,
            '<p>Voir <a href="https://example.org/page" target="_blank" '
            'rel="noopener noreferrer">le site</a>.</p>',
        )

    def test_lien_schema_non_http_degrade_en_texte(self):
        html = markdown_vers_html("Cliquer [ici](javascript:alert(1)) surtout pas.")
        self.assertNotIn("javascript:", html)
        self.assertNotIn("<a ", html)
        self.assertIn("ici", html)

    def test_titre_pseudo_markdown(self):
        html = markdown_vers_html("## Descriptif du contenu\n\nLa suite.")
        self.assertEqual(html, "<p><strong>Descriptif du contenu</strong></p>\n<p>La suite.</p>")

    def test_echappement_html_de_la_source(self):
        html = markdown_vers_html("Une balise <script>alert(1)</script> & une esperluette.")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&amp;", html)

    def test_cas_reel_data_gouv_osm_communes(self):
        # Extrait réel de la description data.gouv.fr du JDD "Découpage administratif
        # communal français issu d'OpenStreetMap" (API data.gouv.fr, consulté en 2026).
        texte = (
            "Exports du découpage administratif français au niveau communal.\n\n"
            "**Descriptif du contenu des fichiers \"communes\"**\n\n"
            "**Format**\n\n"
            "Ces fichiers sont proposés au format shapefile, avec plusieurs niveaux de "
            "détails:\n\n"
            "- simplification à 5m\n"
            "- simplification à 50m\n"
            "- simplification à 100m\n\n"
            "Plus d'infos: [http://prev.openstreetmap.fr/36680-communes]"
            "(http://prev.openstreetmap.fr/36680-communes)"
        )
        html = markdown_vers_html(texte)
        # Pas de syntaxe Markdown littérale résiduelle dans la sortie.
        self.assertNotIn("**", html)
        self.assertNotIn("- simplification", html)
        self.assertIn("<ul>", html)
        self.assertIn("<strong>Descriptif du contenu des fichiers", html)
        self.assertIn('<a href="http://prev.openstreetmap.fr/36680-communes"', html)


if __name__ == "__main__":
    unittest.main()
