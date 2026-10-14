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
        # t2 écartée (assignee déjà pris), t4 écartée (process hors périmètre) ;
        # t1 (organization-process) ET t3 (linked-producer-process) retenues.
        self.assertEqual([t["id"] for t in resultat], ["t1", "t3"])
        _, _, kwargs = faux.appels[0]
        self.assertEqual(kwargs["params"], {"asAdmin": "true"})
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer x"})


class TestValiderToutesOrganisations(unittest.TestCase):
    def _routes_statiques(self, taches):
        """GET /tasks renvoie toujours la même liste — suffisant quand le test ne
        porte pas sur l'effet d'une validation sur les passes suivantes."""
        return {
            ("POST", "/authenticate"): _Rep(headers={"Authorization": "Bearer x"}),
            ("GET", "/strukture/v1/tasks"): _Rep(data=taches),
            ("PUT", "/claim"): _Rep(status=204),
            ("PUT", "/do-it/validated"): _Rep(status=204),
        }

    def test_valide_toutes_les_taches_en_attente(self):
        taches = [
            {"id": "t1", "assignee": None, "asset": {"process_definition_key": "organization-process", "name": "Org1"}},
            {"id": "t2", "assignee": None, "asset": {"process_definition_key": "organization-process", "name": "Org2"}},
        ]
        faux = _SessionFactice(self._routes_statiques(taches))
        with mock.patch.object(vop, "session", faux):
            bilan = vop.valider_toutes_organisations("http://rudi.localhost", "animateur", "pw",
                                                      journal=lambda *_: None)
        self.assertEqual(bilan, {"total": 2, "validees": 2, "echecs": 0})
        urls_put = [u for m, u, _ in faux.appels if m == "PUT"]
        self.assertEqual(len(urls_put), 4)  # claim + do-it pour chacune des 2 tâches
        self.assertTrue(all("/organizations/tasks/t1/" in u or "/organizations/tasks/t2/" in u
                            for u in urls_put))

    def test_valide_aussi_les_taches_de_rattachement_deja_en_attente(self):
        """Une tâche linked-producer-process déjà en attente (indépendamment de toute
        cascade) doit être validée via le bon segment d'URL, /linked-producers/..."""
        taches = [
            {"id": "lp1", "assignee": None,
             "asset": {"process_definition_key": "linked-producer-process", "name": "Rattachement1"}},
        ]
        faux = _SessionFactice(self._routes_statiques(taches))
        with mock.patch.object(vop, "session", faux):
            bilan = vop.valider_toutes_organisations("http://rudi.localhost", "animateur", "pw",
                                                      journal=lambda *_: None)
        self.assertEqual(bilan, {"total": 1, "validees": 1, "echecs": 0})
        urls_put = [u for m, u, _ in faux.appels if m == "PUT"]
        self.assertTrue(all("/linked-producers/tasks/lp1/" in u for u in urls_put))

    def test_cascade_rattachement_apres_validation_organisation(self):
        """Reproduit le bug rapporté : valider une organization-process de création
        engendre côté portail une NOUVELLE tâche linked-producer-process (le rattachement
        auto, voir OrganizationWorkflowContext.startAttachOrganizationToProvider), absente
        de la liste récupérée avant cette validation. Elle doit être rattrapée à la passe
        suivante, sans action manuelle ni ré-exécution du menu."""
        pendantes = [
            {"id": "org1", "assignee": None,
             "asset": {"process_definition_key": "organization-process", "name": "Org1"}},
        ]
        appels_do_it = []

        def get_tasks(url, **kwargs):
            return _Rep(data=list(pendantes))

        def put_route(url, **kwargs):
            if "/do-it/validated" in url and "/organizations/tasks/org1/" in url:
                appels_do_it.append(url)
                pendantes.remove(next(t for t in pendantes if t["id"] == "org1"))
                pendantes.append({"id": "lp-auto", "assignee": None,
                                   "asset": {"process_definition_key": "linked-producer-process",
                                             "name": "RattachementAuto"}})
                return _Rep(status=204)
            if "/do-it/validated" in url and "/linked-producers/tasks/lp-auto/" in url:
                appels_do_it.append(url)
                pendantes.remove(next(t for t in pendantes if t["id"] == "lp-auto"))
                return _Rep(status=204)
            return _Rep(status=204)  # claim, peu importe la cible

        faux = _SessionFactice({("POST", "/authenticate"): _Rep(headers={"Authorization": "Bearer x"})})
        faux.get = get_tasks
        faux.put = put_route

        with mock.patch.object(vop, "session", faux):
            bilan = vop.valider_toutes_organisations("http://rudi.localhost", "animateur", "pw",
                                                      journal=lambda *_: None)
        self.assertEqual(bilan, {"total": 2, "validees": 2, "echecs": 0})
        self.assertEqual(len(appels_do_it), 2)
        self.assertEqual(pendantes, [])

    def test_une_tache_en_echec_n_empeche_pas_les_autres(self):
        taches = [
            {"id": "t1", "assignee": None, "asset": {"process_definition_key": "organization-process"}},
            {"id": "t2", "assignee": None, "asset": {"process_definition_key": "organization-process"}},
        ]
        faux = _SessionFactice(self._routes_statiques(taches))

        def put_route(url, **kwargs):
            if "/t1/claim" in url:
                return _Rep(status=400)  # échoue dès le claim pour t1
            return _Rep(status=204)
        faux.put = put_route  # override direct, plus simple que le routage par fragment ici

        with mock.patch.object(vop, "session", faux):
            bilan = vop.valider_toutes_organisations("http://rudi.localhost", "animateur", "pw",
                                                      journal=lambda *_: None)
        # t1 échoue au claim mais n'est jamais retentée à une passe suivante (liste
        # statique dans ce test) — une seule passe, pas de boucle jusqu'à MAX_PASSES.
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
