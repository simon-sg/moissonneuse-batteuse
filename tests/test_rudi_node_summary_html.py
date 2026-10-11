"""
Tests de _reinjecter_summary_html() / de la sérialisation RudiMetadata sous-jacente —
logique pure, aucun réseau (aucun appel à publier_dataset() ni au nœud RUDI).

Contexte : rudi_node_write (1.4.4, dernière version publiée) ne modélise pas `html` sur
summary[] (RichDictionaryEntry, ajouté côté portail en v3.3.2 — voir
rudi_harvest_toolkit.markdown). _put_metadata() réinjecte ce champ après la
désérialisation/resérialisation de la lib, via _reinjecter_summary_html(). Ces tests
figent aussi le détour par to_json_str() plutôt que to_json() : ce dernier a un bug dans
la lib (clean_nones() ramène les champs liste-de-Serializable à des list Python nues, ce
qui fait porter le test isinstance(val, Serializable) du bloc liste sur la liste
elle-même plutôt que sur chaque élément — toujours faux, les objets ressortent non
convertis). Si une future version de la lib corrige ce point ou modélise `html` elle-même,
ces tests le détecteront.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rudi_node_write.rudi_types.rudi_meta import RudiMetadata

from rudi_harvest_toolkit.rudi_client import _reinjecter_summary_html

_META_MINIMALE = {
    "global_id": "e8b513a1-8d0e-4824-9a7d-1087fc66af9d",
    "local_id": "local-test",
    "resource_title": "Jeu de test",
    "synopsis": [{"lang": "fr", "text": "Synopsis de test"}],
    "summary": [{"lang": "fr", "text": "Un **gras** et une liste.", "html": "<p>Un <strong>gras</strong> et une liste.</p>"}],
    "theme": "education",
    "keywords": ["test"],
    "producer": {"organization_id": "fa557d8b-0892-47aa-809b-6da59081e0aa", "organization_name": "Producteur test"},
    "contacts": [{"contact_id": "f275bed9-6b62-43f1-b617-a392896a617c", "contact_name": "Contact test",
                  "email": "contact@example.org"}],
    "available_formats": [{
        "media_id": "9de29661-a53a-4eea-835c-b0799e181636",
        "media_type": "SERVICE",
        "media_name": "source",
        "connector": {"url": "https://example.org/data", "interface_contract": "dwnl"},
    }],
    "dataset_dates": {"created": "2023-04-12T02:00:38.000Z", "updated": "2023-04-12T02:00:38.000Z"},
    "storage_status": "online",
    "access_condition": {
        "licence": {"licence_type": "STANDARD", "licence_label": "etalab-2.0"},
        "confidentiality": {"restricted_access": False, "gdpr_sensitive": False},
    },
    "metadata_info": {"api_version": "1.4.0", "metadata_source": "https://example.org"},
}


class TestSerialisationSummaryHtml(unittest.TestCase):
    def test_to_json_perd_html_sans_reinjection(self):
        """Documente la perte en l'absence de réinjection (confirme le besoin du correctif)."""
        meta_obj = RudiMetadata.from_json(_META_MINIMALE)
        meta_json = json.loads(meta_obj.to_json_str(ensure_ascii=True))
        self.assertNotIn("html", meta_json["summary"][0])
        self.assertEqual(meta_json["summary"][0]["text"], "Un **gras** et une liste.")

    def test_reinjection_restaure_html(self):
        meta_obj = RudiMetadata.from_json(_META_MINIMALE)
        meta_json = json.loads(meta_obj.to_json_str(ensure_ascii=True))
        patched = _reinjecter_summary_html(_META_MINIMALE, meta_json)
        self.assertEqual(patched["summary"][0]["html"], "<p>Un <strong>gras</strong> et une liste.</p>")
        self.assertEqual(patched["summary"][0]["text"], "Un **gras** et une liste.")

    def test_sans_html_source_rien_a_reinjecter(self):
        meta_sans_html = json.loads(json.dumps(_META_MINIMALE))
        del meta_sans_html["summary"][0]["html"]
        meta_json = {"summary": [{"lang": "fr", "text": "x"}]}
        patched = _reinjecter_summary_html(meta_sans_html, dict(meta_json))
        self.assertEqual(patched, meta_json)

    def test_to_json_direct_cassait_la_liste_summary(self):
        """Garde-fou : documente pourquoi _put_metadata() utilise to_json_str() et pas
        to_json() — ce dernier renvoie des objets RudiDictionaryEntry non convertis pour
        les champs liste (bug de clean_nones() dans rudi_node_write, voir docstring)."""
        meta_obj = RudiMetadata.from_json(_META_MINIMALE)
        meta_json_direct = meta_obj.to_json()
        self.assertTrue(
            any(not isinstance(e, dict) for e in meta_json_direct["summary"]),
            "ce test doit être mis à jour si rudi_node_write corrige ce comportement",
        )


if __name__ == "__main__":
    unittest.main()
