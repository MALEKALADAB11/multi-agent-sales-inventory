"""
Tests du simulateur de ventes — logique pure, sans base.

Le contrat de données (découverte des boutiques, catalogue, prix médian) est
couvert par les tests d'intégration ; ici on vérifie le comportement
déterministe qui n'a pas besoin de PostgreSQL :

1. le rythme suit la courbe d'objectif de CHAQUE boutique (profil propre) ;
2. une boutique en avance continue de vendre au ralenti, jamais zéro ;
3. hors des heures d'ouverture, aucune émission.
"""
import random

import pytest

from app.sales.data.realtime_simulator import (
    RealtimeSimulator, StoreContext, SIM_AHEAD_RATE, SIM_OPEN_HOUR, SIM_CLOSE_HOUR,
)


def _ctx(**kw) -> StoreContext:
    base = dict(
        store_id="TEST",
        advisors=[1, 2, 3],
        catalogue=[{"sku": 100, "name": "X", "price": 50.0, "stock": 10}],
        daily_target=1000.0,
        # Profil plat : 8h→20h, 13 heures à part égale.
        hourly_share={h: 1.0 / 13 for h in range(SIM_OPEN_HOUR, SIM_CLOSE_HOUR + 1)},
    )
    base.update(kw)
    return StoreContext(**base)


class TestUsability:

    def test_context_needs_advisors_catalogue_and_target(self):
        assert _ctx().is_usable()
        assert not _ctx(advisors=[]).is_usable()
        assert not _ctx(catalogue=[]).is_usable()
        assert not _ctx(daily_target=0).is_usable()


class TestPacing:

    def _sim(self):
        return RealtimeSimulator(store_ids=["TEST"])

    def test_behind_target_emits_almost_always(self, monkeypatch):
        """Très en retard sur l'objectif → quasi chaque tick déclenche une vente."""
        sim = self._sim()
        ctx = _ctx()
        # Milieu de journée, profil plat : ~50% du CA attendu.
        monkeypatch.setattr(
            "app.sales.data.realtime_simulator.datetime",
            _fixed_now(hour=14, minute=0))
        random.seed(0)
        hits = sum(sim._should_emit(ctx, ca_today=0.0) for _ in range(500))
        assert hits > 450, "un retard massif doit émettre presque à chaque tick"

    def test_ahead_of_target_still_trickles(self, monkeypatch):
        """En avance → débit résiduel non nul, jamais coupé."""
        sim = self._sim()
        ctx = _ctx()
        monkeypatch.setattr(
            "app.sales.data.realtime_simulator.datetime",
            _fixed_now(hour=14, minute=0))
        random.seed(0)
        # CA déjà à 3× l'objectif : la boutique est très en avance.
        hits = sum(sim._should_emit(ctx, ca_today=3000.0) for _ in range(2000))
        rate = hits / 2000
        assert 0 < rate, "une boutique en avance ne doit jamais cesser de vendre"
        assert abs(rate - SIM_AHEAD_RATE) < 0.05, "le débit résiduel doit suivre SIM_AHEAD_RATE"

    def test_closed_hours_never_emit(self, monkeypatch):
        sim = self._sim()
        ctx = _ctx()
        for h in (SIM_OPEN_HOUR - 1, SIM_CLOSE_HOUR + 1, 3):
            monkeypatch.setattr(
                "app.sales.data.realtime_simulator.datetime", _fixed_now(hour=h))
            assert not any(sim._should_emit(ctx, 0.0) for _ in range(50)), \
                f"aucune vente ne doit sortir à {h}h"

    def test_expected_ca_follows_store_profile(self):
        """Deux profils différents donnent deux attendus différents à la même heure."""
        sim = self._sim()
        morning = _ctx(hourly_share={9: 0.5, 10: 0.5})   # tout le matin
        evening = _ctx(hourly_share={18: 0.5, 19: 0.5})  # tout le soir
        import app.sales.data.realtime_simulator as mod
        orig = mod.datetime
        try:
            mod.datetime = _fixed_now(hour=11)
            # À 11h : le profil du matin a tout réalisé, celui du soir rien.
            assert sim._expected_ca_now(morning) == pytest.approx(1000.0)
            assert sim._expected_ca_now(evening) == pytest.approx(0.0)
        finally:
            mod.datetime = orig


class TestSnapshot:

    def test_snapshot_is_serialisable_and_empty_initially(self):
        snap = RealtimeSimulator(store_ids=["TEST"]).snapshot()
        assert snap["running"] is False
        assert snap["transactions"] == 0
        assert snap["stores"] == {}


# ── Utilitaire : fige datetime.now() ──────────────────────────────────────────

def _fixed_now(hour=12, minute=0):
    import datetime as _dt

    class _FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 22, hour, minute, 0)

    return _FixedDatetime
