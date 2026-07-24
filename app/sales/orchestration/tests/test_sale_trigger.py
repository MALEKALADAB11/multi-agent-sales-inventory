"""
Tests du déclenchement sur nouvelle vente.

Les deux propriétés qui comptent :

1. **Coalescence** — dix ventes rapprochées ne doivent produire qu'un recalcul.
   Sans cela le déclencheur amplifierait la charge au lieu de la lisser.
2. **Parcimonie de l'étage 2** — le cycle complet (donc les appels LLM facturés)
   ne part que sur un changement matériel, jamais sur une vente anodine.
"""
import asyncio
import time
from unittest.mock import patch

from app.sales.orchestration.sale_trigger import (
    SaleEventTrigger, TriggerConfig, _StoreState,
)


def _analysis(urgency="MEDIUM", gap=20.0, feasibility="ACHIEVABLE", eod=900.0):
    return {
        "store_id": "I63", "eod_forecast": eod, "gap_pct": gap,
        "urgency_level": urgency, "feasibility": feasibility,
        "current_ca": 500.0, "daily_target": 1000.0,
    }


def _fast_config(**kw):
    base = dict(debounce_s=0.05, max_wait_s=0.2, heartbeat_s=999,
                min_full_cycle_s=0.0, gap_delta_pts=5.0, tick_s=0.02)
    base.update(kw)
    return TriggerConfig(**base)


class TestCoalescence:

    def test_ten_sales_produce_one_analysis(self):
        async def run():
            calls = []
            async def _record(sid, analysis):
                calls.append(sid)

            trig = SaleEventTrigger(on_analysis=_record, config=_fast_config())
            with patch("app.sales.coaching.agents.analyst.ts_engine.analyze_store") as m:
                async def _fake(sid):
                    return _analysis()
                m.side_effect = _fake
                trig.start()
                for _ in range(10):
                    trig.notify_sale("I63", 50.0)
                    await asyncio.sleep(0.005)
                await asyncio.sleep(0.4)
                trig.stop()
            assert trig.stats["sales_seen"] == 10
            assert trig.stats["analyses"] == 1, "les ventes doivent être fusionnées"
            assert calls == ["I63"], "la diffusion doit partir une seule fois"
        asyncio.run(run())

    def test_pending_counters_reset_after_analysis(self):
        trig = SaleEventTrigger(config=_fast_config())
        trig.notify_sale("I63", 100.0)
        trig.notify_sale("I63", 50.0)
        st = trig._stores["I63"]
        assert st.pending_sales == 2 and st.pending_amount == 150.0

    def test_notify_sale_is_cheap_and_synchronous(self):
        """Le chemin d'encaissement ne doit rien attendre."""
        trig = SaleEventTrigger(config=_fast_config())
        t0 = time.perf_counter()
        for _ in range(10_000):
            trig.notify_sale("I63", 10.0)
        assert time.perf_counter() - t0 < 0.5
        assert trig.stats["sales_seen"] == 10_000

    def test_unknown_store_is_ignored(self):
        trig = SaleEventTrigger(config=_fast_config())
        trig.notify_sale("", 10.0)
        assert trig.stats["sales_seen"] == 0


class TestFullCycleParsimony:
    """L'étage 2 coûte des appels LLM : il ne part que s'il le faut."""

    def _trigger(self, **kw):
        return SaleEventTrigger(config=_fast_config(**kw))

    def test_first_cycle_always_fires(self):
        trig = self._trigger()
        assert trig._full_cycle_reason(_StoreState(), _analysis()) == "premier_cycle"

    def test_stable_situation_does_not_fire(self):
        trig = self._trigger()
        st = _StoreState(last_full_cycle_at=time.monotonic(), last_urgency="MEDIUM",
                         last_gap_pct=20.0, last_feasibility="ACHIEVABLE")
        assert trig._full_cycle_reason(st, _analysis()) is None

    def test_urgency_escalation_fires(self):
        trig = self._trigger()
        st = _StoreState(last_full_cycle_at=time.monotonic(), last_urgency="MEDIUM",
                         last_gap_pct=20.0, last_feasibility="ACHIEVABLE")
        reason = trig._full_cycle_reason(st, _analysis(urgency="HIGH"))
        assert reason and "urgence" in reason

    def test_urgency_de_escalation_does_not_fire(self):
        """Une amélioration ne justifie pas de réécrire le conseil."""
        trig = self._trigger()
        st = _StoreState(last_full_cycle_at=time.monotonic(), last_urgency="HIGH",
                         last_gap_pct=20.0, last_feasibility="ACHIEVABLE")
        assert trig._full_cycle_reason(st, _analysis(urgency="MEDIUM")) is None

    def test_large_gap_move_fires(self):
        trig = self._trigger()
        st = _StoreState(last_full_cycle_at=time.monotonic(), last_urgency="MEDIUM",
                         last_gap_pct=20.0, last_feasibility="ACHIEVABLE")
        reason = trig._full_cycle_reason(st, _analysis(gap=30.0))
        assert reason and "gap" in reason

    def test_small_gap_move_does_not_fire(self):
        trig = self._trigger()
        st = _StoreState(last_full_cycle_at=time.monotonic(), last_urgency="MEDIUM",
                         last_gap_pct=20.0, last_feasibility="ACHIEVABLE")
        assert trig._full_cycle_reason(st, _analysis(gap=22.0)) is None

    def test_feasibility_degradation_fires(self):
        trig = self._trigger()
        st = _StoreState(last_full_cycle_at=time.monotonic(), last_urgency="MEDIUM",
                         last_gap_pct=20.0, last_feasibility="ACHIEVABLE")
        reason = trig._full_cycle_reason(st, _analysis(feasibility="VERY_HARD"))
        assert reason and "faisabilité" in reason

    def test_min_interval_blocks_even_on_escalation(self):
        """Le garde-fou de coût prime sur l'escalade d'urgence."""
        trig = self._trigger(min_full_cycle_s=600)
        st = _StoreState(last_full_cycle_at=time.monotonic(), last_urgency="LOW",
                         last_gap_pct=20.0, last_feasibility="ACHIEVABLE")
        assert trig._full_cycle_reason(st, _analysis(urgency="CRITICAL")) is None


class TestSnapshot:

    def test_snapshot_exposes_config_and_stats(self):
        trig = SaleEventTrigger(config=_fast_config())
        trig.notify_sale("I63", 10.0)
        snap = trig.snapshot()
        assert snap["stats"]["sales_seen"] == 1
        assert "debounce_s" in snap["config"]
        assert snap["stores"]["I63"]["pending_sales"] == 1
