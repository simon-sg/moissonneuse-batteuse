"""Tests de l'enrichissement des descriptions d'organisations — logique pure, aucun réseau."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from translation.organisation_secours import (
    _normaliser_nom_producteur, enrichir_organisation, _repli_factuel, _construire_resultat,
    logo_wikipedia_pour,
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

    @patch(_MOCK_WIKI)
    def test_site_officiel_wikidata_devient_organization_url(self, mock_wiki):
        mock_wiki.return_value = {
            "caption": "Administration française",
            "summary": "Le ministère de l'Intérieur est une administration.",
            "url": "https://fr.wikipedia.org/wiki/Ministere_de_l'Interieur",
            "site_officiel": "https://www.interieur.gouv.fr",
        }
        r = enrichir_organisation("MinistereInterieurTest")
        self.assertEqual(r["organization_url"], "https://www.interieur.gouv.fr")

    @patch(_MOCK_WIKI)
    def test_site_officiel_absent_pas_de_cle_url(self, mock_wiki):
        """Wikipédia sans P856 → aucune clé organization_url (pas de repli inventé)."""
        mock_wiki.return_value = {
            "caption": "Organisation test",
            "summary": "Une organisation de test.",
            "url": "https://fr.wikipedia.org/wiki/Organisation_test",
        }
        r = enrichir_organisation("OrganisationSansSiteOfficiel")
        self.assertNotIn("organization_url", r)


class TestLogoWikipediaPour(unittest.TestCase):
    @patch(_MOCK_WIKI)
    def test_logo_wikidata_devient_url_commons(self, mock_wiki):
        mock_wiki.return_value = {
            "caption": "Ministère français",
            "summary": "Le ministère de la Culture est...",
            "url": "https://fr.wikipedia.org/wiki/Ministere_de_la_Culture",
            "logo": "Ministère de la Culture.svg",
        }
        url = logo_wikipedia_pour("Ministère de la Culture")
        self.assertIn("Special:FilePath/", url)
        self.assertIn("Minist%C3%A8re%20de%20la%20Culture.svg", url)

    @patch(_MOCK_WIKI)
    def test_logo_absent_retourne_none(self, mock_wiki):
        mock_wiki.return_value = {
            "caption": "Organisation",
            "summary": "Une organisation.",
            "url": "https://fr.wikipedia.org/wiki/Organisation",
            "logo": "",
        }
        self.assertIsNone(logo_wikipedia_pour("OrganisationTestLogo"))

    @patch(_MOCK_WIKI)
    def test_alias_dict_sans_logo_retourne_none(self, mock_wiki):
        """Override manuel (dict) sans clé « logo » → aucun logo."""
        self.assertIsNone(logo_wikipedia_pour("Ecolab"))
        mock_wiki.assert_not_called()

    def test_alias_dict_avec_logo_retourne_l_url(self):
        with patch.dict("translation.organisation_secours.ALIAS_ORGANISATIONS",
                        {"OrganisationTestLogo": {"logo": "https://ex.org/logo.png"}}):
            self.assertEqual(logo_wikipedia_pour("OrganisationTestLogo"),
                             "https://ex.org/logo.png")

    @patch(_MOCK_WIKI)
    def test_wikipedia_inconnu_retourne_none(self, mock_wiki):
        mock_wiki.return_value = None
        self.assertIsNone(logo_wikipedia_pour("OrganisationInconnueXYZ"))


class TestConstruireResultat(unittest.TestCase):
    def test_url_incluse_si_presente(self):
        r = _construire_resultat("caption", "summary", "https://exemple.org")
        self.assertEqual(r["organization_url"], "https://exemple.org")

    def test_url_vide_omet_la_cle(self):
        r = _construire_resultat("caption", "summary", "")
        self.assertNotIn("organization_url", r)


class TestIntegrationRudiBuilder(unittest.TestCase):
    """Vérifie que le producteur pré-résolu par l'appelant (enrichir_organisation) traverse
    construire_rudi_metadata sans altération — la résolution a migré du builder vers
    l'appelant avec l'extraction de rudi-harvest-toolkit."""

    @patch(_MOCK_WIKI)
    def test_produit_caption_et_summary(self, mock_wiki):
        mock_wiki.return_value = {
            "caption": "Institut national de la statistique",
            "summary": "L'INSEE produit des statistiques.",
            "url": "https://fr.wikipedia.org/wiki/INSEE",
        }
        from rudi_harvest_toolkit.pivot import construire_rudi_metadata, LICENCE_ETALAB
        nom = "Institut national de la statistique et des études économiques (Insee)"
        producer = {"organization_name": nom}
        producer.update(enrichir_organisation(nom) or {})
        result = construire_rudi_metadata(
            local_id="test-123",
            titre="Titre test — Rennes Métropole",
            synopsis="Synopsis test",
            description="Description test",
            theme="economy",
            keywords=["test"],
            producer=producer,
            url_source="https://example.com",
            url_fiche="https://example.com",
            medias=[],
            contacts_source=[],
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
        from rudi_harvest_toolkit.pivot import construire_rudi_metadata, LICENCE_ETALAB
        with patch(_MOCK_WIKI, return_value=None):
            producer = {"organization_name": "data.gouv.fr"}
            producer.update(enrichir_organisation("data.gouv.fr", source_label="data.gouv.fr") or {})
            result = construire_rudi_metadata(
                local_id="test-456",
                titre="Titre test — Rennes Métropole",
                synopsis="Synopsis test",
                description="Description test",
                theme="economy",
                keywords=["test"],
                producer=producer,
                url_source="https://example.com",
                url_fiche="https://example.com",
                medias=[],
                contacts_source=[],
                ajouter_medias_source=False,
                licence=LICENCE_ETALAB,
            )
        producer = result["producer"]
        self.assertIn("organization_caption", producer)
        self.assertIn("organization_summary", producer)
        self.assertIn("data.gouv.fr", producer["organization_summary"])


if __name__ == "__main__":
    unittest.main()
