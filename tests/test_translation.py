"""Tests des traducteurs RUDI — logique pure, aucun réseau."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from translation.datagouv_to_rudi import traduire_metadonnees, THEMES_RUDI, _detecter_theme
from translation.description_secours import description_quasi_vide, generer_complement
from translation.rudi_builder import (
    construire_rudi_metadata, media_filtre, media_source, media_metadata_page,
    LICENCE_ETALAB, _parser_date_http,
)

_META_MIN = {
    "id": "abc123",
    "title": "Prix des carburants",
    "description": "Relevés quotidiens des prix des carburants dans les stations-service.",
    "organization": {"name": "DGCCRF"},
    "tags": ["carburant", "prix"],
    "license": "lov2",
    "resources": [],
}


class TestTraduireMetadonnees(unittest.TestCase):
    def test_theme_invalide_leve(self):
        with self.assertRaises(ValueError):
            traduire_metadonnees(_META_MIN, theme="pas-un-theme")

    def test_theme_auto_detecte_valide(self):
        meta = traduire_metadonnees(_META_MIN)
        self.assertIn(meta["theme"], THEMES_RUDI)

    def test_local_id_deterministe(self):
        a = traduire_metadonnees(_META_MIN, theme="economy")
        b = traduire_metadonnees(_META_MIN, theme="economy")
        self.assertEqual(a["local_id"], b["local_id"])

    def test_ordre_medias_file_avant_service(self):
        """publier_dataset mappe fichiers_filtres[i] → available_formats[i] :
        le préfixe FILE doit précéder les entrées SERVICE."""
        meta = traduire_metadonnees(
            _META_MIN, theme="economy",
            fichiers_filtres=[("f1.csv", 10, None), ("f2.csv", 5, None)],
            fichiers_dicts=[("dict.csv", None)],
        )
        types = [m["media_type"] for m in meta["available_formats"]]
        premier_service = types.index("SERVICE")
        self.assertNotIn("FILE", types[premier_service:])
        self.assertEqual(types[:premier_service], ["FILE"] * premier_service)

    def test_medias_source_presents(self):
        meta = traduire_metadonnees(_META_MIN, theme="economy")
        noms = [m["media_name"] for m in meta["available_formats"]]
        self.assertIn("source-data-gouv", noms)
        self.assertIn("source-metadata", noms)

    def test_licence_custom(self):
        meta_cc = dict(_META_MIN, license="cc-by")
        meta = traduire_metadonnees(meta_cc, theme="economy")
        self.assertEqual(meta["access_condition"]["licence"]["licence_type"], "CUSTOM")

    def test_licence_lov2(self):
        meta = traduire_metadonnees(_META_MIN, theme="economy")
        self.assertEqual(meta["access_condition"]["licence"]["licence_label"], "etalab-2.0")

    def test_description_courte_completee(self):
        meta_vide = dict(_META_MIN, description="")
        meta = traduire_metadonnees(meta_vide, theme="economy",
                                    entetes_colonnes=["cp", "ville", "prix"])
        summary = meta["summary"][0]["text"]
        self.assertIn("Jeu de données du thème", summary)


class TestDetecterTheme(unittest.TestCase):
    def test_transport(self):
        meta = dict(_META_MIN, title="Fréquentation du métro et des bus", description="", tags=[])
        self.assertEqual(_detecter_theme(meta), "transportation")

    def test_fallback_society(self):
        meta = dict(_META_MIN, title="zzz", description="zzz", tags=[])
        self.assertEqual(_detecter_theme(meta), "society")


class TestRudiBuilder(unittest.TestCase):
    def test_media_ids_deterministes(self):
        self.assertEqual(media_filtre("insee:x", "f.csv", "RM")["media_id"],
                         media_filtre("insee:x", "f.csv", "RM")["media_id"])
        self.assertNotEqual(media_filtre("insee:x", "f.csv", "RM")["media_id"],
                            media_filtre("insee:y", "f.csv", "RM")["media_id"])
        self.assertEqual(media_metadata_page("p", "https://u", "c")["media_id"],
                         media_metadata_page("p", "https://u", "c")["media_id"])

    def _construire(self, **surcharge):
        args = dict(
            local_id="00000000-0000-5000-8000-000000000001",
            titre="T — Rennes Métropole", synopsis="S", description="D",
            theme="economy", keywords=["a", "b", "a"],
            producteur_nom="Prod", url_source="https://src", url_fiche="https://fiche",
            medias=[],
        )
        args.update(surcharge)
        return construire_rudi_metadata(**args)

    def test_auto_append_source_et_metadata(self):
        meta = self._construire()
        noms = [m["media_name"] for m in meta["available_formats"]]
        self.assertEqual(noms, ["source-data", "source-metadata"])

    def test_pas_d_auto_append_si_desactive(self):
        meta = self._construire(ajouter_medias_source=False)
        self.assertEqual(meta["available_formats"], [])

    def test_keywords_dedupliques_ordre_conserve(self):
        meta = self._construire()
        self.assertEqual(meta["keywords"], ["a", "b"])

    def test_licence_defaut_et_surcharge(self):
        self.assertEqual(self._construire()["access_condition"]["licence"], LICENCE_ETALAB)
        custom = {"licence_type": "CUSTOM"}
        self.assertEqual(self._construire(licence=custom)["access_condition"]["licence"], custom)

    def test_dates_prime_sur_date_source(self):
        meta = self._construire(dates={"created": "2024-01-01T00:00:00Z"},
                                date_source="Wed, 12 Feb 2025 09:41:37 GMT")
        self.assertEqual(meta["dataset_dates"], {"created": "2024-01-01T00:00:00Z"})

    def test_metadata_dates_optionnel(self):
        sans = self._construire()
        self.assertNotIn("metadata_dates", sans["metadata_info"])
        avec = self._construire(metadata_dates={"updated": "2025-01-01T00:00:00Z"})
        self.assertIn("metadata_dates", avec["metadata_info"])


class TestParserDateHttp(unittest.TestCase):
    def test_format_http(self):
        self.assertEqual(_parser_date_http("Wed, 12 Feb 2025 09:41:37 GMT"),
                         "2025-02-12T00:00:00Z")

    def test_format_iso(self):
        self.assertEqual(_parser_date_http("2025-01-15T10:30:00.000Z"),
                         "2025-01-15T00:00:00Z")

    def test_none(self):
        self.assertIsNone(_parser_date_http(None))
        self.assertIsNone(_parser_date_http(""))


class TestDescriptionSecours(unittest.TestCase):
    def test_quasi_vide(self):
        self.assertTrue(description_quasi_vide(None))
        self.assertTrue(description_quasi_vide("   "))
        self.assertTrue(description_quasi_vide("Trop court."))
        self.assertFalse(description_quasi_vide("x" * 60))

    def test_complement_factuel(self):
        texte = generer_complement(theme="economy", producteur="INSEE",
                                   colonnes=["cp", "ville", "effectif"])
        self.assertIn("Jeu de données du thème", texte)
        self.assertIn("INSEE", texte)

    def test_complement_sans_colonnes(self):
        texte = generer_complement(theme="environment", producteur="OEB")
        self.assertTrue(texte.strip())


if __name__ == "__main__":
    unittest.main()
