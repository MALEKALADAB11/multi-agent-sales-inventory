"""Tests de _app_stubs — l'isolation des bouchons `app.*` des bancs hors-ligne.

Ce mode de défaillance est invisible en lançant les fichiers un par un : le
faux `app` mourait avec le processus. Il n'apparaissait que sur `pytest tests`
en bloc, sous la forme de 8 erreurs de collecte
(« No module named 'app.core'; 'app' is not a package »). D'où ces tests, qui
le reproduisent sans dépendre de l'ordre de collecte.
"""
import importlib
import sys
import types
import unittest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

import _app_stubs  # noqa: E402


class TestAppStubs(unittest.TestCase):

    def test_le_vrai_paquet_parent_nest_pas_remplace(self):
        """C'est LE bug d'origine : bouchonner une feuille ne doit pas
        transformer `app` en module sans __path__."""
        stub = types.ModuleType("app.inventory.services.orchestrator")
        stubs = _app_stubs.install({"app.inventory.services.orchestrator": stub})
        try:
            self.assertTrue(hasattr(sys.modules["app"], "__path__"))
            # Un paquet non lié au bouchon reste importable pendant l'installation.
            self.assertIsNotNone(importlib.import_module("app.core.shutdown"))
        finally:
            stubs.restore()

    def test_le_bouchon_est_actif_puis_defait(self):
        stub = types.ModuleType("app.inventory.services.orchestrator")
        stub.create_orchestrator = lambda **kw: "factice"
        stubs = _app_stubs.install({"app.inventory.services.orchestrator": stub})

        from app.inventory.services.orchestrator import create_orchestrator
        self.assertEqual(create_orchestrator(), "factice")

        stubs.restore()

        real = importlib.import_module("app.inventory.services.orchestrator")
        self.assertIsNot(real, stub)
        self.assertTrue(callable(real.create_orchestrator))
        # L'attribut posé sur le parent est retiré, pas laissé pointer sur le bouchon.
        self.assertIsNot(getattr(sys.modules["app.inventory.services"],
                                 "orchestrator", None), stub)

    def test_deux_jeux_de_bouchons_sont_independants(self):
        """Chaque fichier de test défait les siens sans toucher à ceux d'un autre."""
        a = types.ModuleType("app.inventory.utils.llm_factory")
        b = types.ModuleType("app.inventory.agents.decision.nodes")
        set_a = _app_stubs.install({"app.inventory.utils.llm_factory": a})
        set_b = _app_stubs.install({"app.inventory.agents.decision.nodes": b})
        try:
            set_a.restore()
            self.assertIs(sys.modules.get("app.inventory.agents.decision.nodes"), b)
            self.assertIsNot(sys.modules.get("app.inventory.utils.llm_factory"), a)
        finally:
            set_b.restore()

    def test_paquets_absents_bouchonnes_comme_paquets(self):
        """Sans codebase app/ (CI nue), les parents créés doivent rester des
        paquets — sinon on reproduit le bug qu'on corrige."""
        leaf = types.ModuleType("paquet_inexistant_xyz.sous.feuille")
        stubs = _app_stubs.install({"paquet_inexistant_xyz.sous.feuille": leaf})
        try:
            self.assertEqual(sys.modules["paquet_inexistant_xyz"].__path__, [])
            self.assertIs(sys.modules["paquet_inexistant_xyz.sous.feuille"], leaf)
        finally:
            stubs.restore()
        self.assertNotIn("paquet_inexistant_xyz", sys.modules)


if __name__ == "__main__":
    unittest.main()
