"""Tests du connecteur Wikipédia — extraction du site officiel (P856) et du logo (P154) — aucun réseau."""
import os
import sys
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from connectors.wikipedia import _site_officiel, _logo_officiel, url_logo_wikimedia, telecharger_logo


def _reponse(status, payload):
    r = Mock(status_code=status)
    r.json.return_value = payload
    return r


def _reponse_binaire(status, content_type, contenu=b"\x89PNG"):
    r = Mock(status_code=status)
    r.headers = {"Content-Type": content_type}
    r.content = contenu
    return r


class TestSiteOfficiel(unittest.TestCase):
    @patch("connectors.wikipedia.session")
    def test_prend_la_premiere_url_http(self, sess):
        sess.get.return_value = _reponse(200, {"claims": {"P856": [
            {"mainsnak": {"datavalue": {"value": "https://www.insee.fr"}}},
        ]}})
        self.assertEqual(_site_officiel("Q156616"), "https://www.insee.fr")

    @patch("connectors.wikipedia.session")
    def test_propriete_absente_retourne_vide(self, sess):
        sess.get.return_value = _reponse(200, {"claims": {}})
        self.assertEqual(_site_officiel("Q1"), "")

    @patch("connectors.wikipedia.session")
    def test_valeur_non_http_ignoree(self, sess):
        sess.get.return_value = _reponse(200, {"claims": {"P856": [
            {"mainsnak": {"datavalue": {"value": "ftp://pas-une-page.fr"}}},
            {"mainsnak": {"datavalue": {"value": "https://bon.fr"}}},
        ]}})
        self.assertEqual(_site_officiel("Q1"), "https://bon.fr")

    @patch("connectors.wikipedia.session")
    def test_qid_vide_aucun_appel_reseau(self, sess):
        self.assertEqual(_site_officiel(""), "")
        sess.get.assert_not_called()

    @patch("connectors.wikipedia.session")
    def test_statut_non_200_retourne_vide(self, sess):
        sess.get.return_value = _reponse(404, {})
        self.assertEqual(_site_officiel("Q1"), "")

    @patch("connectors.wikipedia.session")
    def test_erreur_reseau_retourne_vide(self, sess):
        sess.get.side_effect = Exception("réseau indisponible")
        self.assertEqual(_site_officiel("Q1"), "")


class TestLogoOfficiel(unittest.TestCase):
    @patch("connectors.wikipedia.session")
    def test_prend_le_nom_de_fichier_commons(self, sess):
        sess.get.return_value = _reponse(200, {"claims": {"P154": [
            {"mainsnak": {"datavalue": {"value": "Ministère de la Culture.svg"}}},
        ]}})
        self.assertEqual(_logo_officiel("Q384602"), "Ministère de la Culture.svg")

    @patch("connectors.wikipedia.session")
    def test_propriete_absente_retourne_vide(self, sess):
        sess.get.return_value = _reponse(200, {"claims": {}})
        self.assertEqual(_logo_officiel("Q1"), "")

    @patch("connectors.wikipedia.session")
    def test_qid_vide_aucun_appel_reseau(self, sess):
        self.assertEqual(_logo_officiel(""), "")
        sess.get.assert_not_called()

    @patch("connectors.wikipedia.session")
    def test_statut_non_200_retourne_vide(self, sess):
        sess.get.return_value = _reponse(404, {})
        self.assertEqual(_logo_officiel("Q1"), "")

    @patch("connectors.wikipedia.session")
    def test_erreur_reseau_retourne_vide(self, sess):
        sess.get.side_effect = Exception("réseau indisponible")
        self.assertEqual(_logo_officiel("Q1"), "")


class TestUrlLogoWikimedia(unittest.TestCase):
    def test_encode_le_nom_de_fichier_et_pose_la_largeur(self):
        url = url_logo_wikimedia("Ministère de la Culture.svg")
        self.assertIn("Special:FilePath/", url)
        self.assertIn("Minist%C3%A8re%20de%20la%20Culture.svg", url)
        self.assertTrue(url.endswith("?width=512"))


class TestTelechargerLogo(unittest.TestCase):
    @patch("connectors.wikipedia.session")
    def test_png_accepte(self, sess):
        sess.get.return_value = _reponse_binaire(200, "image/png", b"\x89PNG...")
        self.assertEqual(telecharger_logo("https://ex/logo"), (b"\x89PNG...", "image/png"))

    @patch("connectors.wikipedia.session")
    def test_content_type_avec_charset_accepte(self, sess):
        sess.get.return_value = _reponse_binaire(200, "image/jpeg; charset=binary", b"xx")
        self.assertEqual(telecharger_logo("https://ex/logo"), (b"xx", "image/jpeg"))

    @patch("connectors.wikipedia.session")
    def test_svg_non_accepte(self, sess):
        sess.get.return_value = _reponse_binaire(200, "image/svg+xml", b"<svg/>")
        self.assertIsNone(telecharger_logo("https://ex/logo.svg"))

    @patch("connectors.wikipedia.session")
    def test_statut_non_200_retourne_none(self, sess):
        sess.get.return_value = _reponse_binaire(404, "image/png")
        self.assertIsNone(telecharger_logo("https://ex/logo"))

    @patch("connectors.wikipedia.session")
    def test_contenu_vide_retourne_none(self, sess):
        sess.get.return_value = _reponse_binaire(200, "image/png", b"")
        self.assertIsNone(telecharger_logo("https://ex/logo"))

    @patch("connectors.wikipedia.session")
    def test_erreur_reseau_retourne_none(self, sess):
        sess.get.side_effect = Exception("réseau indisponible")
        self.assertIsNone(telecharger_logo("https://ex/logo"))


if __name__ == "__main__":
    unittest.main()
