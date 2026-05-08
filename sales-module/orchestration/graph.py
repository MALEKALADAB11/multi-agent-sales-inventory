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

# Tracer global — singleton
tracer = CycleTracer(show_state=True)


def build_graph(
    json_svc: JsonDataService,
    timefm:   TimesFMTools
) -> StateGraph:

    analyste = AnalysteAgent(json_svc, timefm, tracer=tracer)

    graph = StateGraph(AgentState)
    graph.add_node("analyste", analyste.run)

    def route_after_analyste(state: AgentState) -> str:
        urgence = state.get("niveau_urgence", "LOW")
        errors  = state.get("errors", [])

        if errors:
            tracer.router_decision(
                "analyste", "END",
                f"errors detected: {errors[0]}"
            )
            return END

        tracer.router_decision(
            "analyste", "END",
            f"urgence={urgence} · APP05 à venir"
        )
        return END

    graph.set_entry_point("analyste")
    graph.add_conditional_edges("analyste", route_after_analyste)

    return graph.compile()


class CycleOrchestrator:

    def __init__(
        self,
        json_svc: JsonDataService,
        timefm:   TimesFMTools
    ):
        self.graph       = build_graph(json_svc, timefm)
        self.json_svc    = json_svc
        self.last_state: AgentState | None = None
        self.cycle_count = 0

    async def run_cycle(
        self,
        store_id:     str = "store-lac2",
        triggered_by: str = "cron"
    ) -> AgentState:

        cycle_id = f"cycle-{uuid.uuid4().hex[:8]}"
        self.cycle_count += 1

        # ── Afficher début du cycle ───────────────────
        tracer.cycle_start(cycle_id, store_id, triggered_by)

        # ── Créer le state initial sans kwargs inconnus ──
        state = initial_state(store_id=store_id)

        # ── Ajouter les champs supplémentaires manuellement ──
        state["metrics"] = {
            "cycle_id":    cycle_id,
            "started_at":  datetime.utcnow().isoformat(),
            "store_id":    store_id,
            "triggered_by": triggered_by,
        }
        

        # ── Stocker cycle_id dans pos_data pour traçabilité ──
        if "pos_data" not in state or state["pos_data"] is None:
            state["pos_data"] = {}
        state["pos_data"]["cycle_id"]    = cycle_id
        state["pos_data"]["triggered_by"] = triggered_by

        started = datetime.utcnow()
        result  = await self.graph.ainvoke(state)

        duration = (datetime.utcnow() - started).total_seconds() * 1000

        # ── Guard : result peut être None ────────────────
        if result is None:
            result = dict(state)

        if "metrics" not in result or result["metrics"] is None:
            result["metrics"] = {}

        result["metrics"]["total_ms"]     = round(duration, 2)
        result["metrics"]["completed_at"] = datetime.utcnow().isoformat()

        self.last_state = result

        # ── Afficher fin du cycle ─────────────────────
        tracer.cycle_end(result)

        return result

    def get_last_state(self) -> AgentState | None:
        return self.last_state