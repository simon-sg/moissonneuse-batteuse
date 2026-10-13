"""Tests de l'enrichissement des organisations productrices (logo) — logique pure, sans réseau.

Deux volets :
  - les helpers purs (`_nom_fichier_logo`, état des logos) ;
  - `enrichir()` : découplage description / logo (une org dont la description est déjà
    complète doit quand même recevoir son logo ; rien n'est redéposé au portail si rien
    n'a changé).
"""
import contextlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import enrichir_organisations as eo


class TestNomFichierLogo(unittest.TestCase):
    def test_png(self):
        self.assertEqual(eo._nom_fichier_logo("Ministère de la Culture", "image/png"),
                         "minist-re-de-la-culture.png")

    def test_jpeg(self):
        self.assertEqual(eo._nom_fichier_logo("OpenStreetMap", "image/jpeg"),
                         "openstreetmap.jpg")

    def test_nom_vide_retombe_sur_logo(self):
        self.assertEqual(eo._nom_fichier_logo("", "image/png"), "logo.png")

    def test_tronque_a_60_caracteres(self):
        nom = eo._nom_fichier_logo("A" * 200, "image/png")
        self.assertLessEqual(len(nom) - len(".png"), 60)


class TestEtatLogo(unittest.TestCase):
    def test_fichier_absent_retourne_ensemble_vide(self):
        with patch.object(eo, "_ETAT_LOGO_FILE",
                          os.path.join(tempfile.mkdtemp(), "absent.json")):
            self.assertEqual(eo._charger_etat_logo(), set())

    def test_fichier_corrompu_retourne_ensemble_vide(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "etat.json")
            with open(chemin, "w", encoding="utf-8") as f:
                f.write("{pas du json")
            with patch.object(eo, "_ETAT_LOGO_FILE", chemin):
                self.assertEqual(eo._charger_etat_logo(), set())

    def test_aller_retour(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "etat.json")
            with patch.object(eo, "_ETAT_LOGO_FILE", chemin):
                eo._sauvegarder_etat_logo({"id-b", "id-a"})
                self.assertEqual(eo._charger_etat_logo(), {"id-a", "id-b"})
            with open(chemin, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["organisations_traitees"], ["id-a", "id-b"])

    def test_les_none_sont_ignores_a_la_sauvegarde(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "etat.json")
            with patch.object(eo, "_ETAT_LOGO_FILE", chemin):
                eo._sauvegarder_etat_logo({"id-a", None})
                self.assertEqual(eo._charger_etat_logo(), {"id-a"})


class _FauxConnector:
    def __init__(self):
        self.puts = []

    def put_catalog(self, ressource, obj):
        self.puts.append((ressource, obj))


class _FauxWriter:
    def __init__(self, orgs):
        self.organization_list = orgs
        self.connector = _FauxConnector()


def _org(nom="Ministère de la Culture", org_id="o1", summary="s", url="u"):
    return {
        "organization_name": nom,
        "organization_id": org_id,
        "organization_summary": summary,
        "organization_url": url,
    }


class TestEnrichirOrganisations(unittest.TestCase):
    def _lancer(self, orgs, *, conf="defaut", etat=None, logo=None, desc=None, etat_saves=None):
        writer = _FauxWriter(orgs)
        if conf == "defaut":
            conf = {"url_portail": "http://portail"}
        etat = set() if etat is None else etat
        etat_saves = etat_saves if etat_saves is not None else []

        def _sauver(ids):
            etat_saves.append(set(ids))

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("builtins.print"))
            stack.enter_context(patch("enrichir_organisations.charger_conf_rudi", return_value=conf))
            stack.enter_context(patch("enrichir_organisations._creer_writer", return_value=writer))
            stack.enter_context(patch("enrichir_organisations._charger_etat_logo", return_value=etat))
            stack.enter_context(patch("enrichir_organisations._sauvegarder_etat_logo", side_effect=_sauver))
            stack.enter_context(patch("enrichir_organisations.enrichir_organisation",
                                      return_value=desc or {}))
            stack.enter_context(patch("enrichir_organisations.telecharger_logo",
                                      return_value=(b"img", "image/png") if logo else None))
            stack.enter_context(patch("enrichir_organisations.televerser_piece_jointe",
                                      return_value="img-uuid"))
            m_portail = stack.enter_context(patch(
                "enrichir_organisations.demander_mise_a_jour_organisation", return_value=True))
            m_logo = stack.enter_context(patch("enrichir_organisations.logo_wikipedia_pour",
                                               return_value=logo))
            eo.enrichir()
        return writer, m_portail, etat_saves, m_logo

    def test_description_complete_mais_logo_manquant_recoit_le_logo(self):
        writer, m_portail, etat_saves, _ = self._lancer([_org()], logo="http://logo")
        m_portail.assert_called_once()
        self.assertEqual(m_portail.call_args.kwargs.get("image_id"), "img-uuid")
        self.assertEqual(len(writer.connector.puts), 1)
        self.assertTrue(etat_saves and "o1" in etat_saves[-1])

    def test_rien_de_nouveau_ne_redepose_pas(self):
        writer, m_portail, etat_saves, _ = self._lancer([_org()], logo=None)  # description complète
        m_portail.assert_not_called()
        self.assertEqual(writer.connector.puts, [])
        self.assertEqual(etat_saves, [])

    def test_logo_deja_soumis_est_ignore(self):
        writer, m_portail, _, m_logo = self._lancer([_org()], logo="http://logo", etat={"o1"})
        m_portail.assert_not_called()
        m_logo.assert_not_called()
        self.assertEqual(writer.connector.puts, [])

    def test_description_manquante_met_a_jour_le_noeud_sans_logo(self):
        desc = {"organization_caption": "c", "organization_summary": "s"}
        writer, m_portail, _, _ = self._lancer([_org(summary="", url="")], logo=None, desc=desc)
        self.assertEqual(len(writer.connector.puts), 1)
        _ressource, obj = writer.connector.puts[0]
        self.assertEqual(obj["organization_summary"], "s")
        m_portail.assert_called_once()
        self.assertIsNone(m_portail.call_args.kwargs.get("image_id"))

    def test_sans_portail_met_a_jour_le_noeud_sans_tenter_de_logo(self):
        desc = {"organization_summary": "s"}
        writer, _m_portail, etat_saves, m_logo = self._lancer(
            [_org(summary="", url="")], conf={"url": "http://noeud"}, desc=desc)
        self.assertEqual(len(writer.connector.puts), 1)
        m_logo.assert_not_called()
        self.assertEqual(etat_saves, [])


if __name__ == "__main__":
    unittest.main()
