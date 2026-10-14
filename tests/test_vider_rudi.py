"""Tests de la logique de vidage/purge RUDI (vider_rudi) — logique pure, aucun réseau.

Toutes les dépendances (toolkit RUDI, session HTTP, subprocess) sont remplacées par des
doubles : ces tests ne touchent ni le nœud, ni le portail, ni les conteneurs.
"""
import contextlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import vider_rudi


class _Rep:
    def __init__(self, data=None, status=200):
        self._data = data if data is not None else {}
        self.status_code = status
        self.text = ""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http")


class _SessionDataverse:
    """Liste factice de deux dataversal : renvoie 3 datasets (dont 1 fichier ignoré)."""
    def __init__(self):
        self.ids_detruits = []

    def get(self, url, headers=None, timeout=None):
        if "rudi_data" in url:
            return _Rep({"data": [{"type": "dataset", "id": 1}, {"type": "dataset", "id": 2}]})
        return _Rep({"data": [{"type": "dataset", "id": 3}, {"type": "file", "id": 9}]})

    def delete(self, url, headers=None, timeout=None):
        if "/destroy" in url:
            self.ids_detruits.append(int(url.rstrip("/").split("/")[-2]))
        return _Rep(status=200)


class TestVidagePropre(unittest.TestCase):
    def _patch_depot(self, stack, metadonnees=None, organisations=None, locaux=None):
        if metadonnees is None:
            metadonnees = [
                {"global_id": "g1", "local_id": "l1", "producer": {"organization_id": "o1"}},
                {"global_id": "g2", "local_id": "l2", "producer": {"organization_id": "o1"}},
            ]
        if organisations is None:
            organisations = [{"organization_id": "o1", "organization_name": "Org1"}]
        if locaux is None:
            locaux = {"l1", "l2"}
        stack.enter_context(mock.patch("vider_rudi.toutes_metadonnees_rudi",
                                       return_value=metadonnees))
        stack.enter_context(mock.patch("vider_rudi.lister_organisations",
                                       return_value=organisations))
        stack.enter_context(mock.patch("vider_rudi._local_ids_du_pipeline",
                                       return_value=locaux))
        for nom in ("supprimer_dataset", "supprimer_dataset_portail",
                    "demander_detachement_producteur", "demander_suppression_organisation",
                    "supprimer_organisation"):
            stack.enter_context(mock.patch(f"vider_rudi.{nom}", return_value=True))

    def test_supprime_jdd_et_organisations_non_interactif(self):
        with contextlib.ExitStack() as stack:
            self._patch_depot(stack)
            resume = vider_rudi.vider_noeud_proprement(
                {"url": "x"}, interactif=False, journal=lambda *_: None)
        self.assertEqual(resume["jdd"], {"total": 2, "supprimes": 2, "echecs": 0, "ignores": 0})
        self.assertEqual(resume["organisations"]["detachements"], 1)
        self.assertEqual(resume["organisations"]["supprimees"], 1)
        self.assertEqual(resume["organisations"]["echecs"], 0)

    def test_ignore_les_jdd_des_autres_utilisateurs(self):
        metadonnees = [
            {"global_id": "g1", "local_id": "l1", "producer": {"organization_id": "o1"}},
            {"global_id": "g9", "local_id": "tiers", "producer": {"organization_id": "o9"}},
        ]
        with contextlib.ExitStack() as stack:
            self._patch_depot(stack, metadonnees=metadonnees, locaux={"l1"})
            m_suppr = stack.enter_context(mock.patch("vider_rudi.supprimer_dataset", return_value=True))
            resume = vider_rudi.vider_noeud_proprement(
                {"url": "x"}, interactif=False, journal=lambda *_: None)
        self.assertEqual(resume["jdd"], {"total": 1, "supprimes": 1, "echecs": 0, "ignores": 1})
        m_suppr.assert_called_once_with({"url": "x"}, "g1")

    def test_epargne_organisation_partagee_avec_un_jdd_tiers(self):
        metadonnees = [
            {"global_id": "g1", "local_id": "l1", "producer": {"organization_id": "o1"}},
            {"global_id": "g9", "local_id": "tiers", "producer": {"organization_id": "o1"}},
        ]
        with contextlib.ExitStack() as stack:
            self._patch_depot(stack, metadonnees=metadonnees, locaux={"l1"},
                              organisations=[{"organization_id": "o1", "organization_name": "Org1"}])
            m_detach = stack.enter_context(mock.patch("vider_rudi.demander_detachement_producteur"))
            resume = vider_rudi.vider_noeud_proprement(
                {"url": "x"}, interactif=False, journal=lambda *_: None)
        m_detach.assert_not_called()
        self.assertEqual(resume["organisations"]["total"], 0)

    def test_sans_contrepartie_locale_ne_supprime_rien(self):
        metadonnees = [
            {"global_id": "g1", "local_id": "l1", "producer": {"organization_id": "o1"}},
        ]
        with contextlib.ExitStack() as stack:
            self._patch_depot(stack, metadonnees=metadonnees, locaux=set(),
                              organisations=[{"organization_id": "o1", "organization_name": "Org1"}])
            m_suppr = stack.enter_context(mock.patch("vider_rudi.supprimer_dataset"))
            m_detach = stack.enter_context(mock.patch("vider_rudi.demander_detachement_producteur"))
            resume = vider_rudi.vider_noeud_proprement(
                {"url": "x"}, interactif=False, journal=lambda *_: None)
        m_suppr.assert_not_called()
        m_detach.assert_not_called()
        self.assertEqual(resume["jdd"]["total"], 0)
        self.assertEqual(resume["jdd"]["ignores"], 1)

    def test_interactif_pause_puis_supprime(self):
        with contextlib.ExitStack() as stack:
            self._patch_depot(stack)
            faux_input = stack.enter_context(mock.patch("builtins.input", return_value=""))
            resume = vider_rudi.vider_noeud_proprement(
                {"url": "x"}, interactif=True, journal=lambda *_: None)
        self.assertEqual(faux_input.call_count, 1)
        self.assertEqual(resume["organisations"]["supprimees"], 1)

    def test_sans_organisations_ne_tente_rien(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("vider_rudi.toutes_metadonnees_rudi", return_value=[]))
            stack.enter_context(mock.patch("vider_rudi.lister_organisations", return_value=[]))
            stack.enter_context(mock.patch("vider_rudi._local_ids_du_pipeline", return_value=set()))
            m_suppr = stack.enter_context(mock.patch("vider_rudi.demander_suppression_organisation"))
            resume = vider_rudi.vider_noeud_proprement(
                {"url": "x"}, interactif=False, journal=lambda *_: None)
        m_suppr.assert_not_called()
        self.assertEqual(resume["organisations"]["supprimees"], 0)


class TestSuppressionOrganisation(unittest.TestCase):
    def test_portail_refuse_ne_supprime_pas_le_noeud(self):
        with mock.patch("vider_rudi.demander_suppression_organisation", return_value=False), \
             mock.patch("vider_rudi.supprimer_organisation") as m_noeud:
            ok = vider_rudi._supprimer_organisation(
                {"url": "x"}, {"organization_id": "o1", "organization_name": "Org1"}, lambda *_: None)
        self.assertFalse(ok)
        m_noeud.assert_not_called()

    def test_portail_ok_supprime_le_noeud(self):
        with mock.patch("vider_rudi.demander_suppression_organisation", return_value=True), \
             mock.patch("vider_rudi.supprimer_organisation", return_value=True):
            ok = vider_rudi._supprimer_organisation(
                {"url": "x"}, {"organization_id": "o1", "organization_name": "Org1"}, lambda *_: None)
        self.assertTrue(ok)

    def test_portail_absent_supprime_le_noeud(self):
        with mock.patch("vider_rudi.demander_suppression_organisation", return_value=None), \
             mock.patch("vider_rudi.supprimer_organisation", return_value=True):
            ok = vider_rudi._supprimer_organisation(
                {"url": "x"}, {"organization_id": "o1", "organization_name": "Org1"}, lambda *_: None)
        self.assertTrue(ok)


class TestPurgeRadicale(unittest.TestCase):
    def test_vider_strukture_respecte_l_ordre_fk(self):
        capture = {}

        def faux_executer(cmd, journal):
            capture["sql"] = cmd[-1]
            return 0, "", ""

        with mock.patch("vider_rudi._executer", side_effect=faux_executer):
            vider_rudi._vider_strukture(lambda *_: None)
        sql = capture["sql"]
        self.assertLess(sql.index("linked_producer"), sql.index("organization_member"))
        self.assertLess(sql.index("organization_member"),
                        sql.index("FROM strukture_data.organization;"))
        self.assertNotIn("FROM strukture_data.provider", sql)
        self.assertNotIn("node_provider", sql)

    def test_vider_dataverse_detruit_tous_les_datasets(self):
        faux = _SessionDataverse()
        with mock.patch("vider_rudi._lire_token_dataverse", return_value="tok"), \
             mock.patch("vider_rudi.session", faux):
            res = vider_rudi._vider_dataverse(lambda *_: None)
        self.assertEqual(res, {"ok": True, "supprimes": 3, "total": 3})
        self.assertEqual(sorted(faux.ids_detruits), [1, 2, 3])

    def test_vider_dataverse_sans_token(self):
        with mock.patch("vider_rudi._lire_token_dataverse", return_value=None):
            res = vider_rudi._vider_dataverse(lambda *_: None)
        self.assertFalse(res["ok"])
        self.assertEqual(res["supprimes"], 0)

    def test_purger_orchestre_les_etapes(self):
        with mock.patch("vider_rudi._sauvegarder", return_value="/tmp/backup"), \
             mock.patch("vider_rudi._vider_mongo", return_value={"ok": True}), \
             mock.patch("vider_rudi._vider_strukture", return_value={"ok": True}), \
             mock.patch("vider_rudi._vider_dataverse",
                        return_value={"ok": True, "supprimes": 0, "total": 0}):
            res = vider_rudi.purger_noeud_portail(journal=lambda *_: None)
        self.assertEqual(res["sauvegarde"], "/tmp/backup")
        self.assertTrue(res["mongo"]["ok"])
        self.assertTrue(res["strukture"]["ok"])

    def test_purger_sans_sauvegarde(self):
        with mock.patch("vider_rudi._sauvegarder") as m_sauv, \
             mock.patch("vider_rudi._vider_mongo", return_value={"ok": True}), \
             mock.patch("vider_rudi._vider_strukture", return_value={"ok": True}), \
             mock.patch("vider_rudi._vider_dataverse",
                        return_value={"ok": True, "supprimes": 0, "total": 0}):
            vider_rudi.purger_noeud_portail(sauvegarder=False, journal=lambda *_: None)
        m_sauv.assert_not_called()


class TestExecuter(unittest.TestCase):
    def test_commande_introuvable(self):
        with mock.patch("vider_rudi.subprocess.run", side_effect=FileNotFoundError):
            code, _out, _err = vider_rudi._executer(["inexistant"], lambda *_: None)
        self.assertEqual(code, 127)


if __name__ == "__main__":
    unittest.main()
