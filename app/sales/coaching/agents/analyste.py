import logging
import os
import time
from datetime import datetime, timedelta
from app.sales.core.state import AgentState
from app.sales.data.json_service import JsonDataService
from app.sales.mcp.timefm.tools import TimesFMTools

_DB_CFG = {
    "host":     os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")),
    "port":     int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))),
    "dbname":   os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "ooredoo_sales")),
    "user":     os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")),
    "password": os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "root")),
}

_SEASONAL_WEIGHTS = {
    9: 0.05, 10: 0.07, 11: 0.09, 12: 0.10,
    13: 0.08, 14: 0.09, 15: 0.10, 16: 0.11,
    17: 0.13, 18: 0.11, 19: 0.05, 20: 0.02,
}

logger = logging.getLogger(__name__)

URGENCY_HIGH   = 30.0
URGENCY_MEDIUM = 15.0


class AnalysteAgent:

    def __init__(
        self,
        json_svc: JsonDataService,
        timefm:   TimesFMTools,
        tracer=None
    ):
        self.json_svc = json_svc
        self.timefm   = timefm
        self.tracer   = tracer

    async def run(self, state: AgentState) -> AgentState:
        started = datetime.utcnow()

        if self.tracer:
            self.tracer.node_start(
                "APP02 · Analyste",
                "observe → reason → decide → write"
            )

        try:
            state = await self._execute(state)

            duration = (datetime.utcnow() - started).total_seconds() * 1000
            state["metrics"]["APP02"] = {
                "status":      "ok",
                "duration_ms": round(duration, 2)
            }
            state["metrics"].setdefault(
                "agents_called", []
            ).append("APP02")

            if self.tracer:
                self.tracer.node_end("APP02", "ok")

        except Exception as e:
            logger.error("[APP02] Error: %s", e)
            state["errors"].append(f"APP02: {str(e)}")
            state["niveau_urgence"] = "UNKNOWN"

            if self.tracer:
                self.tracer.error("APP02", str(e))
                self.tracer.node_end("APP02", "error")

        return state

    async def _execute(self, state: AgentState) -> AgentState:
        store_id = (state.get("pos_data") or {}).get("store_id", "store-lac2")
        t        = self.tracer

        # ── Step 1 — Lire POS ─────────────────────────
        if t: t.step(1, "Lire données POS")

        metrics   = self.json_svc.get_store_metrics()
        ca_today  = metrics["ca_today"]
        ca_target = float(
            sum(x["ca_target"] for x in self.json_svc.get_targets())
        )
        n_tx = len(self.json_svc.get_transactions_today())

        if t:
            t.tool_call(
                "json_svc.get_store_metrics",
                inputs  = {"store_id": store_id},
                outputs = {
                    "ca_today":     round(ca_today, 2),
                    "ca_target":    ca_target,
                    "transactions": n_tx
                }
            )

        state["pos_data"] = {
            "ca_today":     ca_today,
            "ca_target":    ca_target,
            "transactions": n_tx,
            "collected_at": datetime.utcnow().isoformat()
        }

        # ── Step 2 — Calculer gap ─────────────────────
        if t: t.step(2, "Calculer gap objectif")

        gap = self._compute_gap(ca_today, ca_target)
        state["ecart_objectif"] = gap

        if t:
            t.tool_call(
                "_compute_gap",
                inputs  = {
                    "ca_realized": round(ca_today, 2),
                    "ca_target":   ca_target
                },
                outputs = {"gap_pct": gap}
            )

        # ── Step 3 — Prophet forecast (SQL fallback si erreur) ──────
        if t: t.step(3, "Appeler Prophet (TimesFMTools)")

        hour_now      = datetime.utcnow().hour
        sales_history = self.json_svc.get_hourly_ca()
        forecast_model = "Prophet-Meta"
        try:
            forecast = await self.timefm.forecast_eod(
                store_id      = store_id,
                ca_realized   = ca_today,
                sales_history = sales_history,
                hour_current  = hour_now,
            )
        except Exception as exc:
            logger.warning("[APP02] TimesFM/Prophet failed (%s) — using SQL 7-day rolling fallback", exc)
            forecast = self._sql_rolling_forecast(store_id, ca_today, hour_now)
            forecast_model = "SQL-rolling-7d"

        state["forecast_eod"]     = forecast["eod"]
        state["forecast_ci_low"]  = forecast.get("ci_low",  forecast["eod"] * 0.90)
        state["forecast_ci_high"] = forecast.get("ci_high", forecast["eod"] * 1.10)
        state["forecast_mape"]    = forecast.get("mape",    0.12)

        if t:
            t.tool_call(
                f"timefm.forecast_eod ({forecast_model})",
                inputs  = {
                    "ca_realized":  round(ca_today, 2),
                    "hour_current": hour_now,
                    "model":        forecast_model,
                },
                outputs = {
                    "eod":     state["forecast_eod"],
                    "ci_low":  state["forecast_ci_low"],
                    "ci_high": state["forecast_ci_high"],
                    "mape":    state["forecast_mape"],
                },
            )

        # ── Step 4 — Détecter urgence ─────────────────
        if t: t.step(4, "Détecter niveau d'urgence")

        urgence = self._detect_urgency(gap)
        state["niveau_urgence"] = urgence

        if t:
            t.tool_call(
                "_detect_urgency",
                inputs  = {"gap_pct": gap},
                outputs = {"niveau_urgence": urgence}
            )

        # ── State write summary ───────────────────────
        if t:
            t.state_write({
                "ecart_objectif":   state["ecart_objectif"],
                "niveau_urgence":   state["niveau_urgence"],
                "forecast_eod":     state["forecast_eod"],
                "forecast_mape":    state["forecast_mape"],
                "pos_data":         state["pos_data"]
            })

        return state

    def _compute_gap(self, ca_realized: float, ca_target: float) -> float:
        if ca_target == 0:
            return 0.0
        return round(((ca_target - ca_realized) / ca_target) * 100, 2)

    def _detect_urgency(self, gap_pct: float) -> str:
        if gap_pct >= URGENCY_HIGH:
            return "HIGH"
        if gap_pct >= URGENCY_MEDIUM:
            return "MEDIUM"
        return "LOW"

    def _sql_rolling_forecast(self, store_id: str, ca_today: float, hour_current: int) -> dict:
        """7-day rolling average fallback when Prophet/TimesFM is unavailable."""
        avg_daily_ca = 8000.0  # default if SQL also fails
        try:
            import psycopg2, psycopg2.extras
            conn = psycopg2.connect(**_DB_CFG, connect_timeout=6)
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cur.execute("""
                    SELECT
                        date_only AS day,
                        SUM(lig_ttc) AS ca
                    FROM sales.transactions_rt
                    WHERE store_id = %s
                      AND date_only >= CURRENT_DATE - INTERVAL '8 days'
                      AND date_only <  CURRENT_DATE
                    GROUP BY 1
                    ORDER BY 1 DESC
                    LIMIT 7
                """, (store_id,))
                rows = cur.fetchall()
                if rows:
                    avg_daily_ca = sum(float(r["ca"] or 0) for r in rows) / len(rows)
            finally:
                cur.close(); conn.close()
        except Exception as e:
            logger.warning("[APP02 SQL-fallback] %s — using default 8000 DT", e)

        # Project remaining hours using seasonal weights
        weight_done = sum(
            _SEASONAL_WEIGHTS.get(h, 0.08)
            for h in range(9, min(hour_current + 1, 21))
        )
        weight_done      = max(weight_done, 0.01)
        weight_remaining = sum(
            _SEASONAL_WEIGHTS.get(h, 0.08)
            for h in range(hour_current + 1, 21)
        )

        # Scale avg_daily_ca by today's pace relative to historical average
        if avg_daily_ca > 0 and weight_done > 0:
            pace_ratio = (ca_today / (avg_daily_ca * weight_done)) if avg_daily_ca * weight_done > 0 else 1.0
            pace_ratio = min(pace_ratio, 1.5)  # cap at 150% of normal pace
        else:
            pace_ratio = 1.0

        eod_addition = avg_daily_ca * weight_remaining * pace_ratio
        eod          = max(ca_today + eod_addition, ca_today * 1.02)

        return {
            "eod":     round(eod, 2),
            "ci_low":  round(eod * 0.88, 2),
            "ci_high": round(eod * 1.12, 2),
            "mape":    0.12,
            "model":   "SQL-rolling-7d",
        }
