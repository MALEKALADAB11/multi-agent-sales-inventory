import logging
import time
from datetime import datetime
from core.state import AgentState
from data.json_service import JsonDataService
from mcp_servers.timefm.tools import TimesFMTools

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

        # ── Step 3 — Prophet forecast ─────────────────
        if t: t.step(3, "Appeler Prophet (TimesFMTools)")

        sales_history = self.json_svc.get_hourly_ca()
        forecast      = await self.timefm.forecast_eod(
            store_id      = store_id,
            ca_realized   = ca_today,
            sales_history = sales_history,
            hour_current  = datetime.utcnow().hour
        )

        state["forecast_eod"]     = forecast["eod"]
        state["forecast_ci_low"]  = forecast["ci_low"]
        state["forecast_ci_high"] = forecast["ci_high"]
        state["forecast_mape"]    = forecast["mape"]

        if t:
            t.tool_call(
                "timefm.forecast_eod (Prophet)",
                inputs  = {
                    "ca_realized":  round(ca_today, 2),
                    "hour_current": datetime.utcnow().hour,
                    "model":        forecast.get("model", "Prophet-Meta")
                },
                outputs = {
                    "eod":     forecast["eod"],
                    "ci_low":  forecast["ci_low"],
                    "ci_high": forecast["ci_high"],
                    "mape":    forecast["mape"]
                }
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