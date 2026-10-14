"""Tests de valider_organisations_portail — logique pure, aucun réseau réel."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import valider_organisations_portail as vop


class _Rep:
    def __init__(self, data=None, status=200, headers=None):
        self._data = data if data is not None else {}
        self.status_code = status
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _SessionFactice:
    """Session factice routée par méthode+fragment d'URL. Piste les appels."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.appels = []

    def _repondre(self, methode, url, kwargs):
        self.appels.append((methode, url, kwargs))
        for (m, fragment), reponse in self.routes.items():
            if m == methode and fragment in url:
                return reponse() if callable(reponse) else reponse
        raise AssertionError(f"Route non mockée : {methode} {url}")

    def post(self, url, **kwargs):
        return self._repondre("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._repondre("GET", url, kwargs)

    def put(self, url, **kwargs):
        return self._repondre("PUT", url, kwargs)


class TestVerifierPortailLocal(unittest.TestCase):
    def test_accepte_les_hotes_locaux(self):
        for url in ("http://rudi.localhost", "http://localhost:8088", "http://127.0.0.1:4030"):
            vop._verifier_portail_local(url)  # ne lève pas

    def test_refuse_un_hote_distant(self):
        with self.assertRaises(vop.ErreurValidationOrganisations):
            vop._verifier_portail_local("https://portail-production.example.org")


class TestSeConnecterAnimateur(unittest.TestCase):
    def test_renvoie_le_header_authorization(self):
        faux = _SessionFactice({
            ("POST", "/authenticate"): _Rep(headers={"Authorization": "Bearer jwt123"}),
        })
        with mock.patch.object(vop, "session", faux):
            auth = vop.se_connecter_animateur("http://rudi.localhost", "animateur", "pw")
        self.assertEqual(auth, "Bearer jwt123")
        methode, url, kwargs = faux.appels[0]
        self.assertEqual(kwargs["data"], {"login": "animateur", "password": "pw"})

    def test_leve_si_pas_de_header_authorization(self):
        faux = _SessionFactice({("POST", "/authenticate"): _Rep(headers={})})
        with mock.patch.object(vop, "session", faux):
            with self.assertRaises(vop.ErreurValidationOrganisations):
                vop.se_connecter_animateur("http://rudi.localhost", "animateur", "pw")


class TestListerTachesOrganisationEnAttente(unittest.TestCase):
    def test_filtre_process_et_assignee(self):
        taches = [
            {"id": "t1", "assignee": None, "asset": {"process_definition_key": "organization-process"}},
            {"id": "t2", "assignee": "quelqu_un", "asset": {"process_definition_key": "organization-process"}},
            {"id": "t3", "assignee": None, "asset": {"process_definition_key": "linked-producer-process"}},
            {"id": "t4", "assignee": None, "asset": {}},
        ]
        faux = _SessionFactice({("GET", "/strukture/v1/tasks"): _Rep(data=taches)})
        with mock.patch.object(vop, "session", faux):
            resultat = vop._lister_taches_organisation_en_attente("http://rudi.localhost", "Bearer x")
        self.assertEqual([t["id"] for t in resultat], ["t1"])
        _, _, kwargs = faux.appels[0]
        self.assertEqual(kwargs["params"], {"asAdmin": "true"})
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer x"})


class TestValiderToutesOrganisations(unittest.TestCase):
    def _routes(self, taches, echec_do_it_pour=None):
        def do_it(tid):
            if tid == echec_do_it_pour:
                return _Rep(status=400)
            return _Rep(status=204)
        return {
            ("POST", "/authenticate"): _Rep(headers={"Authorization": "Bearer x"}),
            ("GET", "/strukture/v1/tasks"): _Rep(data=taches),
        }

    def test_valide_toutes_les_taches_en_attente(self):
        taches = [
            {"id": "t1", "assignee": None, "asset": {"process_definition_key": "organization-process", "name": "Org1"}},
            {"id": "t2", "assignee": None, "asset": {"process_definition_key": "organization-process", "name": "Org2"}},
        ]
        faux = _SessionFactice(self._routes(taches))
        faux.routes[("PUT", "/claim")] = _Rep(status=204)
        faux.routes[("PUT", "/do-it/validated")] = _Rep(status=204)
        with mock.patch.object(vop, "session", faux):
            bilan = vop.valider_toutes_organisations("http://rudi.localhost", "animateur", "pw",
                                                      journal=lambda *_: None)
        self.assertEqual(bilan, {"total": 2, "validees": 2, "echecs": 0})
        urls_put = [u for m, u, _ in faux.appels if m == "PUT"]
        self.assertEqual(len(urls_put), 4)  # claim + do-it pour chacune des 2 tâches
        self.assertTrue(all("/organizations/tasks/t1/" in u or "/organizations/tasks/t2/" in u
                            for u in urls_put))

    def test_une_tache_en_echec_n_empeche_pas_les_autres(self):
        taches = [
            {"id": "t1", "assignee": None, "asset": {"process_definition_key": "organization-process"}},
            {"id": "t2", "assignee": None, "asset": {"process_definition_key": "organization-process"}},
        ]
        faux = _SessionFactice(self._routes(taches))

        def put_route(url, **kwargs):
            if "/t1/claim" in url:
                return _Rep(status=400)  # échoue dès le claim pour t1
            return _Rep(status=204)
        faux.put = put_route  # override direct, plus simple que le routage par fragment ici

        with mock.patch.object(vop, "session", faux):
            bilan = vop.valider_toutes_organisations("http://rudi.localhost", "animateur", "pw",
                                                      journal=lambda *_: None)
        self.assertEqual(bilan, {"total": 2, "validees": 1, "echecs": 1})

    def test_refuse_un_portail_non_local_sans_appel_reseau(self):
        faux = _SessionFactice({})
        with mock.patch.object(vop, "session", faux):
            with self.assertRaises(vop.ErreurValidationOrganisations):
                vop.valider_toutes_organisations("https://portail-prod.example.org", "a", "pw")
        self.assertEqual(faux.appels, [])


class TestChargerCredentialsAnimateur(unittest.TestCase):
    def test_absent_renvoie_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(vop.charger_credentials_animateur(os.path.join(tmp, "absent.json")))

    def test_present_renvoie_le_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            chemin = os.path.join(tmp, "credentials_animateur.json")
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump({"login": "animateur@rennesmetropole.fr", "password": "pw"}, f)
            self.assertEqual(vop.charger_credentials_animateur(chemin),
                            {"login": "animateur@rennesmetropole.fr", "password": "pw"})


if __name__ == "__main__":
    unittest.main()
