"""
Tests — Coach Chat v11 : Stratège câblé serveur-side + RAG unifié + prompt cross-domaine

Couvre:
  - RAG unifié : fallback lexical corpus quand Milvus/Ollama indisponibles
  - format_scripts_block : injection prompt complète (situation/argument/impact)
  - _build_system_prompt v11 : sections ancrage/méthode/catalogue présentes
  - _build_situation : cross-domaine (ventes + stock + stratège) quel que soit l'intent
  - _get_stratege_for_chat : succès, timeout borné (warm arrière-plan), orchestrateur absent
  - StrategieOutput.extras : extraction du contexte riche du state Stratège

Run: pytest tests/test_coach_v11.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import pytest

from app.sales.data import rag_retriever as rr
from app.sales.coaching.agents.coach import coach_chat as cc
from app.sales.coaching.orchestrator.coach_stratege_orchestrator import (
    StrategieOutput, _extract_extras,
)


# ─────────────────────────────────────────────────────────────────────────────
# RAG unifié — fallback lexical
# ─────────────────────────────────────────────────────────────────────────────

class TestRagFallback:

    def test_corpus_fallback_objection_prix(self):
        scripts = rr._corpus_fallback("client hesite trop cher comment closer", 16, 3)
        assert len(scripts) >= 1
        assert scripts[0]["score"] > 0.3
        # champs complets pour l'injection prompt
        for s in scripts:
            for field in ("categorie", "situation", "action", "argument", "impact"):
                assert s[field]

    def test_corpus_fallback_empty_query(self):
        assert rr._corpus_fallback("", 12, 3) == []

    def test_search_scripts_degrades_to_corpus(self, monkeypatch):
        """Milvus down + embeddings down → le RAG répond quand même via le corpus."""
        monkeypatch.setattr(rr, "_get_client", lambda: None)
        monkeypatch.setattr(rr, "_embed", lambda text: None)
        res = rr.search_scripts("objection trop cher iphone", hour=15, top_k=3)
        assert res["source"] == "corpus"
        assert len(res["scripts"]) >= 1

    def test_search_scripts_never_raises(self, monkeypatch):
        monkeypatch.setattr(rr, "_get_client", lambda: None)
        monkeypatch.setattr(rr, "_load_corpus", lambda: [])
        res = rr.search_scripts("nimporte quoi xyz", hour=10)
        assert res == {"scripts": [], "relevant": False, "source": "none"}

    def test_format_scripts_block(self):
        scripts = rr._corpus_fallback("client hesite trop cher", 16, 2)
        block = rr.format_scripts_block(scripts, max_n=2)
        assert "SCRIPTS TERRAIN" in block
        assert "Impact observé" in block

    def test_format_scripts_block_empty(self):
        assert rr.format_scripts_block([]) == ""


# ─────────────────────────────────────────────────────────────────────────────
# Prompt système v11
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemPromptV11:

    def test_contains_core_sections(self):
        sp = cc._build_system_prompt("CATALOG_SENTINEL")
        for marker in ("MÉTHODE", "ANCRAGE DONNÉES", "TON STYLE", "JAMAIS",
                       "CATALOG_SENTINEL", "ACTIONS STRATÈGE", "SCRIPTS TERRAIN"):
            assert marker in sp, f"section manquante: {marker}"

    def test_anti_hallucination_rules(self):
        sp = cc._build_system_prompt("x")
        assert "jamais de ta mémoire" in sp
        assert "tu n'en crées pas un" in sp

    def test_coach_rag_delegates_to_shared_retriever(self, monkeypatch):
        calls = {}
        def fake_search(query, hour=None, top_k=3, min_score=0.32):
            calls["query"] = query
            return {"scripts": [{"score": 0.9, "categorie": "closing", "situation": "s",
                                 "action": "a", "produit": "p", "argument": "g", "impact": "i"}],
                    "relevant": True, "source": "milvus"}
        monkeypatch.setattr("app.sales.data.rag_retriever.search_scripts", fake_search)
        scripts, relevant = cc._search_rag_sync("comment closer", 16)
        assert relevant is True and len(scripts) == 1
        assert "closer" in calls["query"]


# ─────────────────────────────────────────────────────────────────────────────
# Bloc situation cross-domaine
# ─────────────────────────────────────────────────────────────────────────────

def _make_situation(mode, qtype, **overrides):
    kwargs = dict(
        advisor_name="Ahmed", store_id="L01", hour=15, ca=400, target=1007,
        perf=39.7, gap=607, hours_left=5, urgency="HIGH", weather="Tunis",
        cause="", mode=mode, qtype=qtype,
        actions=[{"priorite": 1, "action": "Bundle iPhone",
                  "produit_cible": "iPhone 16 Pro",
                  "argument_vente": "54 TND/mois", "impact_estime": "+1357 TND"}],
        rag_scripts=[], top_sellers=[{"nom": "iPhone 16 Pro", "qty": 7}],
        recent_tx=[{"nom": "A55", "qty": 1, "ttc": 899, "heure": 14}],
        inv_ctx={"stats": {"total": 50, "ruptures": 2, "critiques": 3, "ok_count": 40},
                 "alerts": [{"nom": "iPhone 16 Pro", "qty": 2, "level": "critical", "jours": 1}],
                 "agent_recos": [{"type": "reorder", "nom": "iPhone 16 Pro", "qty": 20}],
                 "top_sellers": [], "kpi_terminaux": 3, "kpi_forfaits": 5},
        advisor_profile=None,
        strat_extras={"strategie_summary": "Pluie: accessoires waterproof",
                      "focus_produits": ["AirPods Pro 3"],
                      "weather_label": "Pluie", "weather_effect": -0.2},
    )
    kwargs.update(overrides)
    return cc._build_situation(**kwargs)


class TestSituationCrossDomain:

    @pytest.mark.parametrize("mode,qtype", [
        ("coaching", "script"),
        ("inventory", "alerte"),
        ("cross_domain", "cross_domain"),
        ("conversation", "recap"),
        ("conversation", "general"),
    ])
    def test_both_domains_always_visible(self, mode, qtype):
        """Quel que soit l'intent : ventes ET stock ET stratège présents."""
        s = _make_situation(mode, qtype)
        assert "STRATÉGIE DU JOUR" in s
        assert "top produits 7j" in s          # sortie agent analyste (sales)
        assert "Ruptures : 2" in s             # état stock (inventory)
        assert "Agent Décision" in s           # sortie agent décision (inventory)

    def test_weather_from_stratege_overrides_frontend(self):
        s = _make_situation("coaching", "script")
        assert "Pluie (trafic -20%)" in s

    def test_cross_domain_consigne(self):
        s = _make_situation("cross_domain", "cross_domain")
        assert "combine les deux" in s

    def test_inventory_mode_detailed_stock(self):
        s = _make_situation("inventory", "alerte")
        assert "KPI jour : 3 terminaux" in s

    def test_no_strat_no_crash(self):
        s = _make_situation("conversation", "general", actions=[], strat_extras={})
        assert "SITUATION Ahmed" in s
        assert "STRATÉGIE DU JOUR" not in s


# ─────────────────────────────────────────────────────────────────────────────
# _get_stratege_for_chat — orchestrateur résilient
# ─────────────────────────────────────────────────────────────────────────────

class _FakeOrchestrator:
    def __init__(self, actions=None, delay=0.0):
        self._actions = actions if actions is not None else []
        self._delay = delay

    async def invoke(self, state):
        if self._delay:
            await asyncio.sleep(self._delay)
        return StrategieOutput(
            actions=self._actions, source="success", latency_ms=1.0,
            extras={"cause_racine": "Gap test", "focus_produits": ["X"]},
        )


class TestStrategeForChat:

    def _patch_orch(self, monkeypatch, orch):
        import app.sales.coaching.orchestrator.bootstrap as bs
        monkeypatch.setattr(bs, "_orchestrator", orch)

    def test_success_returns_actions_and_extras(self, monkeypatch):
        actions = [{"priorite": 1, "action": "a", "produit_cible": "p",
                    "argument_vente": "v", "impact_estime": "i"}]
        self._patch_orch(monkeypatch, _FakeOrchestrator(actions=actions))
        res = asyncio.run(cc._get_stratege_for_chat("L01", 400, 1007, "HIGH"))
        assert res["source"] == "success"
        assert res["actions"] == actions
        assert res["extras"]["cause_racine"] == "Gap test"

    def test_timeout_returns_warming_without_blocking(self, monkeypatch):
        self._patch_orch(monkeypatch, _FakeOrchestrator(delay=5.0))
        monkeypatch.setattr(cc, "COACH_STRATEGE_TIMEOUT", 0.05)

        async def run():
            import time as _t
            t0 = _t.monotonic()
            res = await cc._get_stratege_for_chat("L01", 400, 1007, "HIGH")
            return res, _t.monotonic() - t0

        res, elapsed = asyncio.run(run())
        assert res["source"] == "warming"
        assert res["actions"] == []
        assert elapsed < 1.0  # le chat n'attend pas les 5s du stratège

    def test_orchestrator_missing_returns_empty(self, monkeypatch):
        self._patch_orch(monkeypatch, None)
        res = asyncio.run(cc._get_stratege_for_chat("L01", 400, 1007, "MEDIUM"))
        assert res == {"actions": [], "source": "none", "extras": {}}


# ─────────────────────────────────────────────────────────────────────────────
# StrategieOutput.extras — extraction du contexte riche
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractExtras:

    def test_full_state(self):
        state = {
            "cause_racine": "Gap 58% pluie",
            "strategie": "Résumé stratégie",
            "focus_produits": ["AirPods Pro 3"],
            "message_manager": "msg",
            "critique_score": 0.82,
            "rag_used": True,
            "nb_rag_scripts": 3,
            "external_context": {"summary": {
                "weather_label": "Pluie", "weather_effect": -0.2,
                "is_holiday": False, "holiday_name": "",
            }},
        }
        ex = _extract_extras(state)
        assert ex["cause_racine"] == "Gap 58% pluie"
        assert ex["weather_label"] == "Pluie"
        assert ex["focus_produits"] == ["AirPods Pro 3"]
        assert ex["critique_score"] == 0.82

    def test_empty_state(self):
        ex = _extract_extras({})
        assert ex["cause_racine"] == ""
        assert ex["focus_produits"] == []
