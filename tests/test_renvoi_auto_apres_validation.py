"""Tests de renvoi_auto_apres_validation — logique pure (filtre refused/organisation),
aucun réseau réel (toolkit entièrement mocké)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import renvoi_auto_apres_validation as rav


def _ressource(local_id, org_id=None, status="refused"):
    r = {"local_id": local_id, "metadata_status": status, "global_id": local_id}
    if org_id is not None:
        r["producer"] = {"organization_id": org_id}
    return r


class TestForce(unittest.TestCase):
    def _patcher(self, metadonnees, organisations_validees=("org-ok",)):
        patchers = [
            mock.patch.object(rav, "charger_conf_rudi", return_value={"url": "http://noeud"}),
            mock.patch.object(rav, "organisations_nouvellement_validees", return_value=[]),
            mock.patch.object(rav, "toutes_metadonnees_rudi", return_value=metadonnees),
            mock.patch.object(rav, "local_ids_publies_ce_run", return_value=set()),
            mock.patch.object(rav, "lister_organisations",
                               return_value=[{"organization_id": o} for o in organisations_validees]),
            mock.patch.object(rav, "marquer_organisations_traitees"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_sans_force_ignore_les_refused_dont_l_organisation_n_est_pas_validee(self):
        metadonnees = [
            _ressource("d1", org_id="org-ok"),       # organisation validée -> renvoyé
            _ressource("d2", org_id="org-pas-ok"),   # organisation PAS validée -> ignoré
            _ressource("d3"),                        # pas d'organisation -> renvoyé (ne peut pas conclure ERR-112)
        ]
        self._patcher(metadonnees)
        with mock.patch.object(rav, "renvoyer_ressources_connues",
                                return_value={"ok": 2, "echecs": []}) as renvoi:
            rav.main(force=False)
        renvoyes = [m["local_id"] for m in renvoi.call_args[0][1]]
        self.assertEqual(sorted(renvoyes), ["d1", "d3"])

    def test_force_renvoie_aussi_les_refused_dont_l_organisation_n_est_pas_validee(self):
        metadonnees = [
            _ressource("d1", org_id="org-ok"),
            _ressource("d2", org_id="org-pas-ok"),
            _ressource("d3"),
        ]
        self._patcher(metadonnees)
        with mock.patch.object(rav, "renvoyer_ressources_connues",
                                return_value={"ok": 3, "echecs": []}) as renvoi:
            rav.main(force=True)
        renvoyes = [m["local_id"] for m in renvoi.call_args[0][1]]
        self.assertEqual(sorted(renvoyes), ["d1", "d2", "d3"])

    def test_jamais_une_ressource_deja_publiee_ce_run_meme_avec_force(self):
        metadonnees = [_ressource("d1", org_id="org-pas-ok")]
        self._patcher(metadonnees)
        with mock.patch.object(rav, "local_ids_publies_ce_run", return_value={"d1"}), \
             mock.patch.object(rav, "renvoyer_ressources_connues") as renvoi:
            rav.main(force=True)
        renvoi.assert_not_called()

    def test_jamais_une_ressource_non_refused(self):
        metadonnees = [_ressource("d1", org_id="org-pas-ok", status="published")]
        self._patcher(metadonnees)
        with mock.patch.object(rav, "renvoyer_ressources_connues") as renvoi:
            rav.main(force=True)
        renvoi.assert_not_called()


if __name__ == "__main__":
    unittest.main()
