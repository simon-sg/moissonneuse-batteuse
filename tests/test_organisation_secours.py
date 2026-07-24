"""Tests de l'enrichissement des descriptions d'organisations — logique pure, aucun réseau."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from translation.organisation_secours import (
    _normaliser_nom_producteur, enrichir_organisation, _repli_factuel,
)

_MOCK_WIKI = "connectors.wikipedia.resumer_wikipedia"


class TestNormaliserNom(unittest.TestCase):
    def test_retire_acronyme_final(self):
        self.assertEqual(_normaliser_nom_producteur("Insee (Insee)"), "Insee")

    def test_retire_acronyme_chiffres(self):
        self.assertEqual(_normaliser_nom_producteur("BDNB (BDNB)"), "BDNB")

    def test_retire_prefixe_fournisseur(self):
        self.assertEqual(_normaliser_nom_producteur("fournisseur / Etalab"), "Etalab")

    def test_trim_espaces(self):
        self.assertEqual(_normaliser_nom_producteur("  Insee  "), "Insee")

    def test_nom_sans_modif(self):
        self.assertEqual(_normaliser_nom_producteur("Rennes Métropole"), "Rennes Métropole")

    def test_vide(self):
        self.assertEqual(_normaliser_nom_producteur(""), "")


class TestRepliFactuel(unittest.TestCase):
    def test_source_defaut(self):
        r = _repli_factuel(None, None)
        self.assertIn("portails open data", r["organization_summary"])
        self.assertEqual(r["organization_caption"], "Producteur de données ouvertes")
        self.assertNotIn("Page producteur", r["organization_summary"])

    def test_avec_source_label(self):
        r = _repli_factuel("data.gouv.fr", None)
        self.assertIn("data.gouv.fr", r["organization_summary"])

    def test_avec_page_url(self):
        r = _repli_factuel("insee.fr", "https://www.insee.fr/producteurs")
        self.assertIn("Page producteur : https://www.insee.fr/producteurs", r["organization_summary"])


class TestEnrichirOrganisation(unittest.TestCase):
    @patch(_MOCK_WIKI)
    def test_alias_override_manuel(self, mock_wiki):
        mock_wiki.return_value = None
        r = enrichir_organisation("data.gouv.fr")
        self.assertIsNotNone(r)
        self.assertNotIn("Wikipédia", r.get("organization_summary", ""))
        self.assertIn("portails open data", r.get("organization_summary", ""))

    @patch(_MOCK_WIKI)
    def test_alias_titre_wikipedia(self, mock_wiki):
        mock_wiki.return_value = {
            "caption": "Office public de l'habitat",
            "summary": "L'Observatoire de l'Environnement en Bretagne est un organisme.",
            "url": "https://fr.wikipedia.org/wiki/OEB",
        }
        r = enrichir_organisation("Observatoire de l'Environnement en Bretagne (OEB)")
        self.assertIsNotNone(r)
        self.assertIn("Observatoire de l'Environnement en Bretagne", r.get("organization_summary", ""))
        self.assertIn("source : Wikipédia", r.get("organization_summary", ""))
        mock_wiki.assert_called_once_with("Observatoire de l'Environnement en Bretagne")

    @patch(_MOCK_WIKI)
    def test_wikipedia_inconnu_repli(self, mock_wiki):
        mock_wiki.return_value = None
        r = enrichir_organisation("OrganisationInconnueXYZ", source_label="example.org")
        self.assertIsNotNone(r)
        self.assertIn("example.org", r["organization_summary"])
        self.assertIn("Producteur de données ouvertes", r["organization_caption"])

    @patch(_MOCK_WIKI)
    def test_wikipedia_sans_repli_defaut(self, mock_wiki):
        mock_wiki.return_value = None
        r = enrichir_organisation("OrganisationBidon")
        self.assertIsNotNone(r)
        self.assertIn("portails open data", r["organization_summary"])

    @patch(_MOCK_WIKI)
    def test_nom_avec_acronyme_resolu_via_alias(self, mock_wiki):
        """L'acronyme est retiré, puis le nom court matche l'alias Wikipédia."""
        mock_wiki.return_value = {
            "caption": "Bureau d'études",
            "summary": "Le CSTB est un organisme de recherche.",
            "url": "https://fr.wikipedia.org/wiki/CSTB",
        }
        r = enrichir_organisation("Centre Scientifique et Technique du Bâtiment (CSTB)")
        self.assertIsNotNone(r)
        self.assertIn("CSTB", r.get("organization_summary", ""))
        # L'alias doit résoudre le nom sans acronyme
        mock_wiki.assert_called_once_with("Centre scientifique et technique du bâtiment")

    @patch(_MOCK_WIKI)
    def test_wikipedia_sans_extract_repli(self, mock_wiki):
        """Si Wikipédia retourne une caption mais pas d'extract, on retombe au repli factuel."""
        mock_wiki.return_value = None
        r = enrichir_organisation("OrgWikiCaption")
        self.assertIsNotNone(r)
        self.assertEqual(r.get("organization_caption"), "Producteur de données ouvertes")


class TestIntegrationRudiBuilder(unittest.TestCase):
    """Vérifie que construire_rudi_metadata intègre correctement l'enrichissement."""

    @patch(_MOCK_WIKI)
    def test_produit_caption_et_summary(self, mock_wiki):
        mock_wiki.return_value = {
            "caption": "Institut national de la statistique",
            "summary": "L'INSEE produit des statistiques.",
            "url": "https://fr.wikipedia.org/wiki/INSEE",
        }
        from translation.rudi_builder import construire_rudi_metadata, LICENCE_ETALAB
        result = construire_rudi_metadata(
            local_id="test-123",
            titre="Titre test — Rennes Métropole",
            synopsis="Synopsis test",
            description="Description test",
            theme="economy",
            keywords=["test"],
            producteur_nom="Institut national de la statistique et des études économiques (Insee)",
            url_source="https://example.com",
            url_fiche="https://example.com",
            medias=[],
            ajouter_medias_source=False,
            licence=LICENCE_ETALAB,
        )
        producer = result["producer"]
        self.assertEqual(producer["organization_name"],
                         "Institut national de la statistique et des études économiques (Insee)")
        self.assertIn("organization_caption", producer)
        self.assertIn("organization_summary", producer)
        self.assertIn("source : Wikipédia", producer["organization_summary"])

    def test_echec_wikipedia_repli_factuel(self):
        from translation.rudi_builder import construire_rudi_metadata, LICENCE_ETALAB
        with patch(_MOCK_WIKI, return_value=None):
            result = construire_rudi_metadata(
                local_id="test-456",
                titre="Titre test — Rennes Métropole",
                synopsis="Synopsis test",
                description="Description test",
                theme="economy",
                keywords=["test"],
                producteur_nom="data.gouv.fr",
                url_source="https://example.com",
                url_fiche="https://example.com",
                medias=[],
                ajouter_medias_source=False,
                licence=LICENCE_ETALAB,
                source_producteur="data.gouv.fr",
            )
        producer = result["producer"]
        self.assertIn("organization_caption", producer)
        self.assertIn("organization_summary", producer)
        self.assertIn("data.gouv.fr", producer["organization_summary"])


if __name__ == "__main__":
    unittest.main()
