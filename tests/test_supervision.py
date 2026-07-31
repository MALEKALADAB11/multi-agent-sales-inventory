"""Tests de la page Supervision Métier : agrégats feedback + scoring juge.

Sans DB ni LLM réels : on monkeypatch get_feedback_stats et on utilise une
fausse connexion pour vérifier la logique (maths de fenêtre, moyenne du juge,
construction des prompts).
"""
from types import SimpleNamespace

import app.core.feedback_service as fs
from app.core.feedback_service import _decided_totals, get_feedback_overview
from app.core.quality_service import (
    _build_inventory_scenario, _build_inventory_context,
    _build_sales_question, _insert_score,
)


# ── feedback_service ─────────────────────────────────────────────────────────

def test_decided_totals_consolide_les_trois_boucles():
    stats = {
        "incitations": {"followed": 5, "ignored": 3},
        "hitl":        {"approved": 2, "rejected": 1},
        "po":          {"accepted": 4, "cancelled": 2},
    }
    assert _decided_totals(stats) == (11, 6)


def test_overview_compare_fenetre_courante_et_baseline(monkeypatch):
    def fake_stats(store_id=None, days=14):
        # 30j : 6 suivis / 4 ignorés → 40% rejet ; 90j : 8/2 → 20% rejet
        followed, ignored = (6, 4) if days == 30 else (8, 2)
        return {
            "incitations": {"followed": followed, "ignored": ignored},
            "hitl":        {"approved": 0, "rejected": 0},
            "po":          {"accepted": 0, "cancelled": 0},
            "recent_rejections": ["trop cher"] if days == 30 else [],
        }
    monkeypatch.setattr(fs, "get_feedback_stats", fake_stats)

    ov = get_feedback_overview(None, window_days=30, baseline_days=90)
    assert ov["decided"] == 10
    assert ov["accept_rate"] == 60.0
    assert ov["reject_rate"] == 40.0
    assert ov["baseline_reject_rate"] == 20.0
    assert ov["reject_delta"] == 20.0          # +20 pt de rejet vs moyenne 3 mois
    assert ov["recent_rejections"] == ["trop cher"]


# ── quality_service (prompts + insertion) ────────────────────────────────────

def test_scenario_inventaire_cite_les_donnees_cles():
    rec = {"sku": 123, "store_id": "I63", "action": "ORDER",
           "urgency": "HIGH", "order_qty": 45, "confidence": 0.8}
    s = _build_inventory_scenario(rec)
    assert "123" in s and "ORDER" in s and "45" in s


def test_contexte_inventaire_sans_snapshot_est_partial():
    """Lignes d'avant la migration 0016 : repli sur les colonnes scalaires."""
    rec = {"sku": 123, "store_id": "I63", "action": "ORDER",
           "urgency": "HIGH", "order_qty": 45, "confidence": 0.8}
    ctx, level = _build_inventory_context(rec)
    assert level == "partial"
    assert '"order_qty": 45' in ctx and '"action": "ORDER"' in ctx


def test_contexte_inventaire_avec_snapshot_est_full():
    """Snapshot présent : le juge voit les rapports des agents, une ligne
    par rapport — même forme que le banc hors-ligne, sinon les deux notes
    d'ancrage ne mesurent pas la même chose."""
    rec = {"sku": 123, "store_id": "I63", "action": "ORDER", "order_qty": 45,
           "context_snapshot": {
               "sku": "SKU-1", "store_id": "I63",
               "baseline_report":   {"days_of_stock_remaining": 4.2},
               "context_report":    {"demand_multiplier": 1.3},
               "adjusted_metrics":  {"reorder_point": 60},
               "business_objective": "balanced",
           }}
    ctx, level = _build_inventory_context(rec)
    assert level == "full"
    assert "baseline_report: " in ctx and "context_report: " in ctx
    assert "days_of_stock_remaining" in ctx and "reorder_point" in ctx
    assert "business_objective: balanced" in ctx


def test_contexte_inventaire_accepte_un_snapshot_en_texte():
    """La colonne jsonb peut remonter en str selon le driver/curseur."""
    import json as _json
    snap = {"sku": "SKU-1", "baseline_report": {"days_of_stock_remaining": 4.2}}
    ctx, level = _build_inventory_context({"sku": 1, "context_snapshot": _json.dumps(snap)})
    assert level == "full" and "days_of_stock_remaining" in ctx


def test_question_vente_reflete_urgence_et_ecart():
    row = {"store_id": "I63", "urgency_level": "CRITICAL", "gap_pct": -23.4}
    q = _build_sales_question(row)
    assert "I63" in q and "CRITICAL" in q and "-23%" in q


class _FakeCursor:
    def __init__(self): self.executed = None
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params): self.executed = (sql, params)


class _FakeConn:
    def __init__(self): self._cur = _FakeCursor(); self.committed = False
    def cursor(self): return self._cur
    def commit(self): self.committed = True
    def rollback(self): pass


def test_insert_score_calcule_la_moyenne():
    j = SimpleNamespace(scores={"clarte": 4, "coherence": 2},
                        hallucination=False, verdict="ok", judge_model="mistral/x")
    conn = _FakeConn()
    ok = _insert_score(conn, domain="inventory", ref_id="r1", store_id="I63",
                       criteria_set="inventory", judgment=j, context_level="full")
    assert ok and conn.committed
    params = conn._cur.executed[1]
    # ordre INSERT : (domain, ref_id, store_id, mean_score, scores, criteria_set,
    #                 hallucination, verdict, judge_model, context_level)
    assert params[0] == "inventory"
    assert params[3] == 3.0                     # (4 + 2) / 2
    # Sans ce niveau persisté (migration 0016), une note obtenue sur un
    # contexte 'partial' se lit comme une note obtenue sur le contexte complet.
    assert params[9] == "full"
