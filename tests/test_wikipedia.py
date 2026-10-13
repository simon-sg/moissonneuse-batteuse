"""Tests du connecteur Wikipédia — extraction du site officiel Wikidata (P856) — aucun réseau."""
import os
import sys
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from connectors.wikipedia import _site_officiel


def _reponse(status, payload):
    r = Mock(status_code=status)
    r.json.return_value = payload
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


if __name__ == "__main__":
    unittest.main()
