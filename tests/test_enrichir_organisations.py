"""Tests de l'enrichissement des organisations productrices — logique pure, sans réseau.

Depuis le 2026-10-15 : local au nœud uniquement (la propagation portail et le logo ont
été retirés, `demander_mise_a_jour_organisation()` étant signalée cassée par l'équipe
RUDI) — voir le docstring de `enrichir_organisations.py`.
"""
import contextlib
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import enrichir_organisations as eo


class _FauxConnector:
    def __init__(self, leve=None):
        self.puts = []
        self._leve = leve

    def put_catalog(self, ressource, obj):
        if self._leve:
            raise self._leve
        self.puts.append((ressource, obj))


class _FauxWriter:
    def __init__(self, orgs, leve=None):
        self.organization_list = orgs
        self.connector = _FauxConnector(leve=leve)


def _org(nom="Ministère de la Culture", org_id="o1", summary="s", url="u"):
    return {
        "organization_name": nom,
        "organization_id": org_id,
        "organization_summary": summary,
        "organization_url": url,
    }


class TestEnrichirOrganisations(unittest.TestCase):
    def _lancer(self, orgs, *, desc=None, force=False, leve=None):
        writer = _FauxWriter(orgs, leve=leve)
        conf = {"url": "http://noeud"}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("builtins.print"))
            stack.enter_context(patch("enrichir_organisations.charger_conf_rudi", return_value=conf))
            stack.enter_context(patch("enrichir_organisations._creer_writer", return_value=writer))
            stack.enter_context(patch("enrichir_organisations.enrichir_organisation",
                                      return_value=desc or {}))
            eo.enrichir(force=force)
        return writer

    def test_sans_conf_ne_fait_rien(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("builtins.print"))
            stack.enter_context(patch("enrichir_organisations.charger_conf_rudi", return_value=None))
            creer = stack.enter_context(patch("enrichir_organisations._creer_writer"))
            eo.enrichir()
        creer.assert_not_called()

    def test_description_complete_rien_a_faire(self):
        writer = self._lancer([_org()])  # summary et url déjà présents
        self.assertEqual(writer.connector.puts, [])

    def test_description_manquante_met_a_jour_le_noeud(self):
        desc = {"organization_caption": "c", "organization_summary": "s"}
        writer = self._lancer([_org(summary="", url="")], desc=desc)
        self.assertEqual(len(writer.connector.puts), 1)
        _ressource, obj = writer.connector.puts[0]
        self.assertEqual(obj["organization_summary"], "s")
        self.assertEqual(obj["organization_caption"], "c")

    def test_ne_jamais_ecraser_une_valeur_existante_sans_force(self):
        desc = {"organization_summary": "nouvelle description generique"}
        writer = self._lancer([_org(summary="vraie description", url="u")], desc=desc)
        self.assertEqual(writer.connector.puts, [])  # rien de nouveau, summary déjà présent

    def test_force_reecrit_meme_si_deja_complet(self):
        desc = {"organization_summary": "nouvelle description"}
        writer = self._lancer([_org(summary="ancienne", url="u")], desc=desc, force=True)
        self.assertEqual(len(writer.connector.puts), 1)
        _ressource, obj = writer.connector.puts[0]
        self.assertEqual(obj["organization_summary"], "nouvelle description")

    def test_echec_put_noeud_est_compte_sans_lever(self):
        desc = {"organization_summary": "s"}
        # ne doit pas lever malgré l'échec du PUT
        self._lancer([_org(summary="", url="")], desc=desc, leve=Exception("nœud down"))

    def test_recherche_echouee_nest_pas_fatale(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("builtins.print"))
            writer = _FauxWriter([_org(summary="", url="")])
            stack.enter_context(patch("enrichir_organisations.charger_conf_rudi",
                                      return_value={"url": "http://noeud"}))
            stack.enter_context(patch("enrichir_organisations._creer_writer", return_value=writer))
            stack.enter_context(patch("enrichir_organisations.enrichir_organisation",
                                      side_effect=Exception("réseau indisponible")))
            eo.enrichir()
        self.assertEqual(writer.connector.puts, [])


if __name__ == "__main__":
    unittest.main()
