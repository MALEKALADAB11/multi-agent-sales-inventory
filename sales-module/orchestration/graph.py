"""
graph.py — Orchestrateur LangGraph avec logging complet pour monitoring.
Chaque cycle est tracé, loggé et sauvegardé dans PostgreSQL.
"""
import logging
import uuid
from datetime import datetime

from langgraph.graph import StateGraph, END

from core.state import AgentState, initial_state
from modules.coaching.agents.analyste import AnalysteAgent
from data.json_service import JsonDataService
from mcp_servers.timefm.tools import TimesFMTools
from orchestration.tracer import CycleTracer

logger = logging.getLogger(__name__)
tracer = CycleTracer(show_state=True)

# ── Import agent_logger (non bloquant si absent) ──────────────────────────────
try:
    from agent_logger import (
        log_cycle, log_node_start, log_node_complete,
        log_node_error, enrich_rag_from_cycle,
        setup_monitoring_tables,
    )
    _LOGGING_ENABLED = True
    # Setup tables au démarrage
    setup_monitoring_tables()
    logger.info("[ORCHESTRATOR] Agent logger activé ✅")
except ImportError:
    _LOGGING_ENABLED = False
    logger.warning("[ORCHESTRATOR] agent_logger non trouvé — logging désactivé")

    def log_cycle(*a, **k):         pass
    def log_node_start(*a, **k):    return -1
    def log_node_complete(*a, **k): pass
    def log_node_error(*a, **k):    pass
    def enrich_rag_from_cycle(*a, **k): pass


def build_graph(json_svc: JsonDataService, timefm: TimesFMTools) -> StateGraph:
    analyste = AnalysteAgent(json_svc, timefm, tracer=tracer)
    graph    = StateGraph(AgentState)
    graph.add_node("analyste", analyste.run)

    def route_after_analyste(state: AgentState) -> str:
        urgence = state.get("niveau_urgence", "LOW")
        errors  = state.get("errors", [])
        if errors:
            tracer.router_decision("analyste", "END", f"errors: {errors[0]}")
            return END
        tracer.router_decision("analyste", "END", f"urgence={urgence} · APP05 à venir")
        return END

    graph.set_entry_point("analyste")
    graph.add_conditional_edges("analyste", route_after_analyste)
    return graph.compile()


class CycleOrchestrator:
    def __init__(self, json_svc: JsonDataService, timefm: TimesFMTools):
        self.graph       = build_graph(json_svc, timefm)
        self.json_svc    = json_svc
        self.last_state: AgentState | None = None
        self.cycle_count = 0
        self._errors_count   = 0
        self._nodes_executed = 0

    async def run_cycle(
        self,
        store_id:     str = "store-lac2",
        triggered_by: str = "cron",
    ) -> AgentState:
        cycle_id = f"cycle-{uuid.uuid4().hex[:8]}"
        self.cycle_count += 1
        self._errors_count   = 0
        self._nodes_executed = 0

        tracer.cycle_start(cycle_id, store_id, triggered_by)

        # ── State initial enrichi ──────────────────────────────────────────
        state = initial_state(
            store_id     = store_id,
            cycle_id     = cycle_id,
            triggered_by = triggered_by,
        )
        state["metrics"] = {
            "cycle_id":       cycle_id,
            "started_at":     datetime.utcnow().isoformat(),
            "store_id":       store_id,
            "triggered_by":   triggered_by,
            "total_ms":       0,
            "nodes_executed": 0,
            "analyste_ms":    0,
            "stratege_ms":    0,
            "rag_ms":         0,
            "llm_ms":         0,
            "llm_calls":      0,
        }

        # ── Log démarrage cycle ────────────────────────────────────────────
        log_id = log_node_start(
            cycle_id   = cycle_id,
            agent_name = "orchestrator",
            node_name  = "cycle_start",
            input_state = {"store_id": store_id, "triggered_by": triggered_by},
            store_id   = "I63",
        )

        started = datetime.utcnow()
        result  = None
        try:
            result = await self.graph.ainvoke(state)
            self._nodes_executed += 1
        except Exception as e:
            self._errors_count += 1
            log_node_error(
                log_id     = log_id,
                cycle_id   = cycle_id,
                agent_name = "orchestrator",
                node_name  = "cycle_run",
                error      = e,
                context    = {"store_id": store_id},
                store_id   = "I63",
            )
            logger.error(f"[ORCHESTRATOR] Cycle {cycle_id} error: {e}")

        duration_ms = (datetime.utcnow() - started).total_seconds() * 1000

        if result is None:
            result = dict(state)
        if "metrics" not in result or result["metrics"] is None:
            result["metrics"] = {}

        result["metrics"]["total_ms"]       = round(duration_ms, 2)
        result["metrics"]["completed_at"]   = datetime.utcnow().isoformat()
        result["metrics"]["nodes_executed"] = self._nodes_executed

        self.last_state = result

        # ── Log fin cycle ──────────────────────────────────────────────────
        log_node_complete(
            log_id      = log_id,
            output_state = {
                "urgency_level":  result.get("urgency_level", "LOW"),
                "gap_pct":        result.get("gap_objectif", 0),
                "analyst_summary": (result.get("analyst_summary") or "")[:200],
                "nb_actions":     len(result.get("strategie_actions") or []),
            },
            duration_ms = duration_ms,
            metadata    = {
                "cycle_count":     self.cycle_count,
                "nodes_executed":  self._nodes_executed,
                "errors":          self._errors_count,
            },
        )

        # ── Sauvegarder le cycle complet ──────────────────────────────────
        rag_used       = bool(result.get("rag_used"))
        nb_rag_scripts = int(result.get("nb_rag_scripts") or 0)

        log_cycle(
            cycle_id       = cycle_id,
            state          = result,
            total_ms       = duration_ms,
            triggered_by   = triggered_by,
            store_id       = "I63",
            nodes_executed = self._nodes_executed,
            errors_count   = self._errors_count,
            rag_used       = rag_used,
            nb_rag_scripts = nb_rag_scripts,
        )

        # ── Enrichir le RAG avec les nouvelles actions ─────────────────────
        if result.get("strategie_actions"):
            try:
                enrich_rag_from_cycle(
                    cycle_id = cycle_id,
                    state    = result,
                    store_id = "I63",
                )
            except Exception as e:
                logger.warning(f"[ORCHESTRATOR] RAG enrichissement: {e}")

        tracer.cycle_end(result)
        return result

    def get_last_state(self) -> AgentState | None:
        return self.last_state