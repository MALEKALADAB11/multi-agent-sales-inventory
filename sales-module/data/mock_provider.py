"""
Mock Provider JSON — Données Ooredoo dynamiques qui évoluent dans le temps.
"""
import json
import math
import random
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)
MOCK_DIR = Path(__file__).parent / "mock"


def _load(filename: str) -> dict:
    with open(MOCK_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


class MockDataProvider:

    def __init__(self):
        self._stores      = _load("stores.json")["stores"]
        self._pos_base    = _load("pos_realtime.json")
        self._pos_history = _load("pos_history.json")
        self._timesfm     = _load("timesfm_predictions.json")
        logger.info("[MOCK] Données Ooredoo JSON chargées.")

    def _compute_dynamic_revenue(self, store_id: str) -> float:
        base     = self._pos_base.get(store_id, {})
        base_ca  = base.get("current_revenue_tnd", 7850.0)
        target   = base.get("daily_target_tnd", 18000.0)

        now        = datetime.now()
        hour       = now.hour
        minute     = now.minute
        second     = now.second
        open_hour  = 9
        close_hour = 20
        total_hours = close_hour - open_hour

        elapsed        = max(0, hour - open_hour) + minute / 60 + second / 3600
        progress_ratio = min(1.0, elapsed / total_hours)

        s_curve    = (math.sin((progress_ratio * math.pi) - (math.pi / 2)) + 1) / 2
        expected_ca = target * s_curve * 0.75
        variation   = random.uniform(-0.05, 0.08)
        dynamic_ca  = expected_ca * (1 + variation)
        dynamic_ca  = max(base_ca, dynamic_ca)

        seconds_since_open = elapsed * 3600
        tx_interval = 120
        pending_tx  = int(seconds_since_open / tx_interval) % 5
        tx_boost    = pending_tx * random.uniform(150, 800)

        return round(dynamic_ca + tx_boost, 2)

    def _compute_dynamic_sellers(self, store_id: str, total_revenue: float) -> list:
        base    = self._pos_base.get(store_id, {})
        sellers = base.get("sellers", [])
        if not sellers:
            return []

        weights    = [0.35, 0.28, 0.22, 0.15][:len(sellers)]
        total_w    = sum(weights)
        normalized = [w / total_w for w in weights]

        dynamic_sellers = []
        for i, s in enumerate(sellers):
            share     = normalized[i] if i < len(normalized) else 0.15
            rev       = round(total_revenue * share * random.uniform(0.92, 1.08), 2)
            nb_ventes = max(1, int(rev / random.uniform(200, 600)))
            dynamic_sellers.append({
                "name":          s.get("name", f"Vendeur {i+1}"),
                "revenue_today": rev,
                "nb_ventes":     nb_ventes,
            })

        return dynamic_sellers

    def _compute_dynamic_transactions(self, store_id: str) -> int:
        base    = self._pos_base.get(store_id, {})
        base_tx = base.get("nb_transactions_today", 16)
        now     = datetime.now()
        elapsed = max(0, now.hour - 9) + now.minute / 60
        growth  = random.uniform(0.95, 1.10)
        return max(base_tx, int(base_tx * (elapsed / 6) * growth))

    async def fetch_pos_data(self, store_id: str) -> dict:
        base = self._pos_base.get(store_id)
        if not base:
            raise ValueError(f"Store inconnu: {store_id}")

        dynamic_revenue = self._compute_dynamic_revenue(store_id)
        dynamic_sellers = self._compute_dynamic_sellers(store_id, dynamic_revenue)
        dynamic_tx      = self._compute_dynamic_transactions(store_id)

        return {
            **base,
            "daily_target":          base["daily_target_tnd"],
            "current_revenue":       dynamic_revenue,
            "current_revenue_tnd":   dynamic_revenue,
            "nb_transactions_today": dynamic_tx,
            "sellers":               dynamic_sellers,
            "current_hour":          datetime.now().hour,
            "snapshot_time":         datetime.now().strftime("%H:%M"),
        }

    async def fetch_pos_history(self, store_id: str) -> list[dict]:
        history  = self._pos_history.get(store_id, [])
        now      = datetime.now()
        enriched = []

        for tx in history:
            h, m = map(int, tx["time"].split(":"))
            if h > now.hour or (h == now.hour and m > now.minute):
                continue

            tx_time     = now.replace(hour=h, minute=m, second=0, microsecond=0)
            minutes_ago = int((now - tx_time).total_seconds() / 60)
            base_rev    = tx["revenue_tnd"]
            varied      = round(base_rev * random.uniform(0.90, 1.12), 2)

            enriched.append({
                **tx,
                "transaction_time": tx_time,
                "minutes_ago":      max(0, minutes_ago),
                "revenue":          varied,
            })

        return enriched

    async def fetch_timesfm_prediction(self, store_id: str, **kwargs) -> dict:
        base   = self._pos_base.get(store_id, {})
        target = base.get("daily_target_tnd", 18000.0)

        now             = datetime.now()
        current_hour    = now.hour
        hours_elapsed   = max(1, current_hour - 9)
        hours_remaining = max(1, 20 - current_hour)

        dynamic_ca  = self._compute_dynamic_revenue(store_id)

        # ── Taux horaire réaliste ─────────────────────────────
        # Éviter la division par 1h le matin → utiliser progression attendue
        if hours_elapsed <= 1:
            # Début de journée → forecast basé sur tendance historique
            expected_daily = target * 0.85
            forecast_eod   = min(round(expected_daily), int(target * 1.10))
        else:
            hourly_rate  = dynamic_ca / hours_elapsed
            forecast_rem = round(hourly_rate * hours_remaining * random.uniform(0.85, 1.05))
            max_allowed  = int(target * 1.10)
            forecast_eod = min(round(dynamic_ca + forecast_rem), max_allowed)

        ci_spread = round(forecast_eod * 0.07)

        logger.info(
            f"[MOCK] TimesFM {store_id} → "
            f"CA={dynamic_ca:,.0f} | "
            f"EOD={forecast_eod:,.0f} (max={int(target*1.10):,.0f})"
        )

        return {
            "forecast_end_of_day":     forecast_eod,
            "forecast_end_of_day_tnd": forecast_eod,
            "forecast_remaining":      max(0, forecast_eod - round(dynamic_ca)),
            "forecast_remaining_tnd":  max(0, forecast_eod - round(dynamic_ca)),
            "forecast_hourly": [
                round(min(dynamic_ca / max(hours_elapsed,1) * random.uniform(0.80, 1.10),
                        target / 11))
                for _ in range(hours_remaining)
            ],
            "confidence_interval": {
                "low":  max(round(dynamic_ca), forecast_eod - ci_spread),
                "high": min(forecast_eod + ci_spread, int(target * 1.10)),
            },
            "model_version": "timesfm-1.0-dynamic",
            "source":        "dynamic_mock",
    }
    async def fetch_pos_context(self, store_id: str) -> dict | None:
        return None

    def list_stores(self) -> list[str]:
        return [s["store_id"] for s in self._stores]


def get_data_provider() -> MockDataProvider:
    return MockDataProvider()