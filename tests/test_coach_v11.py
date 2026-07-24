"""
Tests — Coach Chat v11 : Stratège câblé serveur-side + RAG unifié + prompt cross-domaine

Couvre:
  - Moteur RAG : abstention sur hors-sujet, dépriorisation des ruptures, citations
  - Dégradation : RAG muet → le coach refuse d'inventer prix et SKU
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

from app.sales.data.rag import (
    DOMAIN_PRODUCT, DOMAIN_SALES_SCRIPT, RetrievalResult, RetrievedDocument,
    format_context_block,
)
from app.sales.data.rag.rerank import is_relevant, score_documents
from app.sales.coaching.agents.coach import coach_chat as cc
from app.sales.coaching.orchestrator.coach_stratege_orchestrator import (
    StrategieOutput, _extract_extras,
)


# ─────────────────────────────────────────────────────────────────────────────
# RAG unifié — fallback lexical
# ─────────────────────────────────────────────────────────────────────────────

class TestRagEngine:
    """Le moteur RAG : scoring, abstention, formatage. Aucun service requis."""

    def _doc(self, **kw):
        d = RetrievedDocument(
            doc_id=kw.get("doc_id", "x1"), domain=kw.get("domain", DOMAIN_SALES_SCRIPT),
            title=kw.get("title", "titre"), text=kw.get("text", "objection prix trop cher"),
            sku=kw.get("sku", ""), produit=kw.get("produit", ""),
            payload=kw.get("payload", {}),
        )
        d.cosine = kw.get("cosine", 0.70)
        d.bm25 = kw.get("bm25", 15.0)
        d.bm25_rank = kw.get("bm25_rank", 0)
        d.dense_rank = kw.get("dense_rank", 0)
        return d

    def test_abstention_sur_hors_sujet(self):
        """Une requête hors-sujet ne doit PAS être déclarée pertinente.

        Régression : « recette du couscous » ramenait un conseil de coaching à 0,69
        parce que le score post-boosts (créneau + boutique + fraîcheur) dépassait le
        seuil. L'abstention juge désormais les preuves brutes, pas le score final.
        """
        faible = [self._doc(cosine=0.44, bm25=5.8)]
        assert is_relevant(faible) is False

    def test_pertinent_si_cosinus_solide(self):
        assert is_relevant([self._doc(cosine=0.62, bm25=0.0)]) is True

    def test_pertinent_si_lexical_fort(self):
        """Un SKU exact peut avoir un cosinus médiocre mais un BM25 écrasant."""
        assert is_relevant([self._doc(cosine=0.40, bm25=31.0)]) is True

    def test_produit_en_rupture_est_deprecie(self):
        """Ne jamais faire remonter un produit qu'on ne peut pas vendre."""
        dispo = self._doc(doc_id="a", domain=DOMAIN_PRODUCT, sku="111",
                          payload={"stock_dispo": 5})
        rompu = self._doc(doc_id="b", domain=DOMAIN_PRODUCT, sku="222",
                          payload={"stock_dispo": 0})
        ranked = score_documents([dispo, rompu], "iphone",
                                 out_of_stock_skus=frozenset({"222"}))
        assert ranked[0].doc_id == "a"
        assert ranked[-1].boosts.get("rupture") == -0.40

    def test_marge_inconnue_nest_pas_zero(self):
        """« marge 0 % » est un mensonge quand la marge n'est pas renseignée."""
        doc = self._doc(domain=DOMAIN_PRODUCT, produit="IPHONE", sku="1",
                        payload={"prix_ttc": 6349.0, "marge_pct": None,
                                 "stock_dispo": None, "live": True})
        block = format_context_block([doc])
        assert "marge non renseignée" in block
        assert "marge 0" not in block

    def test_bloc_cite_ses_sources(self):
        doc = self._doc(payload={"situation": "client hésite", "action": "closer",
                                 "argument": "arg", "impact": "imp"})
        block = format_context_block([doc])
        assert "[S1]" in block

    def test_bloc_vide_si_aucun_document(self):
        assert format_context_block([]) == ""


class TestCoachRagWiring:

    def test_coach_delegue_au_moteur(self, monkeypatch):
        captured = {}

        def fake_retrieve(query, **kw):
            captured["query"] = query
            captured["store_id"] = kw.get("store_id")
            r = RetrievalResult()
            r.docs = [RetrievedDocument(doc_id="d", domain=DOMAIN_SALES_SCRIPT,
                                        title="t", text="txt")]
            r.relevant, r.mode = True, "hybrid"
            return r

        monkeypatch.setattr("app.sales.data.rag.retrieve", fake_retrieve)
        docs, relevant, block = cc._search_rag_sync("comment closer", 16, "I63")
        assert relevant is True and len(docs) == 1
        assert captured["query"] == "comment closer"
        assert captured["store_id"] == "I63"

    def test_rag_indisponible_ne_leve_pas(self, monkeypatch):
        def boom(query, **kw):
            raise RuntimeError("Milvus down")
        monkeypatch.setattr("app.sales.data.rag.retrieve", boom)
        docs, relevant, block = cc._search_rag_sync("closer", 16, "I63")
        assert docs == [] and relevant is False and block == ""

    def test_prompt_avertit_quand_rag_vide(self):
        """Sans documents, le coach doit se taire sur les prix — pas inventer.

        Régression : Milvus et Ollama tombés, le coach a proposé « Galaxy A54
        (SKU 5020160) à 399 TND » — ce SKU est un Samsung X160 à 120 TND.
        """
        situation = cc._build_situation(
            advisor_name="Malek", store_id="I63", hour=16, ca=650, target=1007,
            perf=64.5, gap=357, hours_left=4, urgency="HIGH", weather="", cause="",
            mode="inventory", qtype="alerte", actions=[], rag_scripts=[],
            top_sellers=[], recent_tx=[], inv_ctx={}, rag_block="",
        )
        assert "AUCUN DOCUMENT" in situation
        assert "n'écris ni prix, ni SKU" in situation


# ─────────────────────────────────────────────────────────────────────────────
# Prompt système v11
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemPromptV11:

    def test_contains_core_sections(self):
        sp = cc._build_system_prompt("CATALOG_SENTINEL")
        for marker in ("MÉTHODE", "ANCRAGE DONNÉES", "TON STYLE", "JAMAIS",
                       "CATALOG_SENTINEL", "ACTIONS STRATÈGE",
                       "HIÉRARCHIE DES SOURCES", "cite sa référence"):
            assert marker in sp, f"section manquante: {marker}"

    def test_anti_hallucination_rules(self):
        sp = cc._build_system_prompt("x")
        assert "jamais de ta mémoire" in sp
        assert "tu n'en crées pas un" in sp

    def test_interdit_sku_sans_fiche(self):
        """Régression : le coach a associé un nom de produit au SKU d'un autre."""
        sp = cc._build_system_prompt("x")
        assert "QUE s'il figure dans une fiche [P*]" in sp
        assert "Ne jamais associer le nom d'un produit au SKU ou au prix d'un autre" in sp


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
