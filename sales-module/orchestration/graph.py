"""
graph.py — Orchestrateur LangGraph v2
=======================================
Pipeline complet : Analyste → Stratège → Coach
Chaque agent est un sous-graphe LangGraph compilé.
Logs complets dans PostgreSQL via AgentLogger.
"""
import logging
import uuid
from datetime import datetime

from langgraph.graph import StateGraph, END

from core.state import SalesAgentState, initial_state
from data.json_service import JsonDataService
from mcp_servers.timefm.tools import TimesFMTools

logger = logging.getLogger(__name__)

# ── Agent logger (non bloquant) ───────────────────────────────────────────────
try:
    from agent_logger import (
        log_cycle, log_node_start, log_node_complete,
        log_node_error, enrich_rag_from_cycle,
        setup_monitoring_tables,
    )
    _LOGGING_ENABLED = True
    logger.info("[ORCHESTRATOR] Agent logger activé ✅")
except ImportError:
    _LOGGING_ENABLED = False
    def log_cycle(*a, **k): pass
    def log_node_start(*a, **k): return -1
    def log_node_complete(*a, **k): pass
    def log_node_error(*a, **k): pass
    def enrich_rag_from_cycle(*a, **k): pass

# ── Import des agents LangGraph ───────────────────────────────────────────────
try:
    from modules.coaching.agents.analyst.agent import get_analyst_agent
    _ANALYST_OK = True
except ImportError as e:
    _ANALYST_OK = False
    logger.warning(f"[ORCHESTRATOR] Analyst agent: {e}")

try:
    from modules.coaching.agents.stratege.agent import get_stratege_agent
    _STRATEGE_OK = True
except ImportError as e:
    _STRATEGE_OK = False
    logger.warning(f"[ORCHESTRATOR] Stratege agent: {e}")

try:
    from modules.coaching.agents.coach.agent import get_coach_agent
    _COACH_OK = True
except ImportError as e:
    _COACH_OK = False
    logger.warning(f"[ORCHESTRATOR] Coach agent: {e}")

# ── Tracer (compatibilité ancienne version) ───────────────────────────────────
try:
    from orchestration.tracer import CycleTracer
    tracer = CycleTracer(show_state=True)
except ImportError:
    class _FakeTracer:
        def cycle_start(self, *a, **k): pass
        def cycle_end(self, *a, **k): pass
        def router_decision(self, *a, **k): pass
    tracer = _FakeTracer()


# ══════════════════════════════════════════════════════════════════════════════
# Wrappers async pour les agents LangGraph
# ══════════════════════════════════════════════════════════════════════════════

async def _run_analyst(state: dict) -> dict:
    """Wrapper Agent Analyste LangGraph."""
    if not _ANALYST_OK:
        logger.warning("[ORCHESTRATOR] Analyst non disponible — fallback")
        return state

    try:
        agent  = get_analyst_agent()
        result = await agent.ainvoke(state)
        logger.info(
            f"[ORCHESTRATOR] ✓ Analyste — "
            f"urgence={result.get('urgency_level','?')} | "
            f"gap={result.get('gap_objectif',0):.1f}% | "
            f"summary={str(result.get('analyst_summary',''))[:60]}"
        )
        return result
    except Exception as e:
        logger.warning(f"[ORCHESTRATOR] Analyste erreur: {str(e)[:80]}")
        return state


async def _run_stratege(state: dict) -> dict:
    """Wrapper Agent Stratège LangGraph."""
    if not _STRATEGE_OK:
        logger.warning("[ORCHESTRATOR] Stratège non disponible — fallback")
        return state

    try:
        agent  = get_stratege_agent()
        result = await agent.ainvoke(state)
        nb_a   = len(result.get("strategie_actions") or [])
        logger.info(
            f"[ORCHESTRATOR] ✓ Stratège — "
            f"{nb_a} actions | "
            f"RAG={result.get('rag_used',False)} | "
            f"cause={str(result.get('cause_racine',''))[:50]}"
        )
        return result
    except Exception as e:
        logger.warning(f"[ORCHESTRATOR] Stratège erreur: {str(e)[:80]}")
        return state


async def _run_coach(state: dict) -> dict:
    """Wrapper Agent Coach LangGraph (si message conseiller présent)."""
    pos_data = state.get("pos_data") or {}
    message  = pos_data.get("coach_message", "")
    if not message:
        return state  # Pas de message → coach non déclenché dans le cycle

    if not _COACH_OK:
        return state

    try:
        agent  = get_coach_agent()
        result = await agent.ainvoke(state)
        logger.info(
            f"[ORCHESTRATOR] ✓ Coach — "
            f"conseil={str(result.get('conseil_final',''))[:60]}"
        )
        return result
    except Exception as e:
        logger.warning(f"[ORCHESTRATOR] Coach erreur: {str(e)[:80]}")
        return state


# ── Routing conditionnel ──────────────────────────────────────────────────────

def route_after_analyst(state: dict) -> str:
    urgency = state.get("urgency_level", "LOW")
    gap     = state.get("gap_objectif", 0)
    tracer.router_decision(
        "analyste", "stratege",
        f"urgence={urgency} · gap={gap:.1f}%"
    )
    return "stratege"


def route_after_stratege(state: dict) -> str:
    pos_data = state.get("pos_data") or {}
    has_msg  = bool(pos_data.get("coach_message", ""))
    if has_msg:
        return "coach"
    return END


# ══════════════════════════════════════════════════════════════════════════════
# Build Graph
# ══════════════════════════════════════════════════════════════════════════════

def build_graph(json_svc: JsonDataService, timefm: TimesFMTools):
    graph = StateGraph(SalesAgentState)

    # ── Nodes ─────────────────────────────────────────────────────────────
    graph.add_node("analyste", _run_analyst)
    graph.add_node("stratege", _run_stratege)
    graph.add_node("coach",    _run_coach)

    # ── Edges ─────────────────────────────────────────────────────────────
    graph.set_entry_point("analyste")
    graph.add_conditional_edges("analyste", route_after_analyst, {
        "stratege": "stratege",
    })
    graph.add_conditional_edges("stratege", route_after_stratege, {
        "coach": "coach",
        END:     END,
    })
    graph.add_edge("coach", END)

    logger.info(
        f"[ORCHESTRATOR] Graphe construit — "
        f"Analyste={'✓' if _ANALYST_OK else '✗'} | "
        f"Stratège={'✓' if _STRATEGE_OK else '✗'} | "
        f"Coach={'✓' if _COACH_OK else '✗'}"
    )
    return graph.compile()


# ══════════════════════════════════════════════════════════════════════════════
# CycleOrchestrator
# ══════════════════════════════════════════════════════════════════════════════

class CycleOrchestrator:
    def __init__(self, json_svc: JsonDataService, timefm: TimesFMTools):
        self.graph       = build_graph(json_svc, timefm)
        self.json_svc    = json_svc
        self.timefm      = timefm
        self.last_state  = None
        self.last_result = None
        self.cycle_count = 0

    async def run_cycle(
        self,
        store_id:     str = "store-lac2",
        triggered_by: str = "cron",
    ) -> dict:
        cycle_id = f"cycle-{uuid.uuid4().hex[:8]}"
        self.cycle_count += 1
        started  = datetime.utcnow()

        # Mapper store_id → internal
        STORE_MAP = {
            "store-lac2": "I63",
            "OOR_LAC_01": "I63",
            "I63": "I63",

            "store-menzah": "M23",
            "M23": "M23",

            "store-sfax": "S47",
            "S47": "S47",
        }

        internal_id = STORE_MAP.get(store_id, "I63")
        tracer.cycle_start(cycle_id, store_id, triggered_by)

        # ── État initial ───────────────────────────────────────────────────
        state = initial_state(
            store_id     = internal_id,
            cycle_id     = cycle_id,
            triggered_by = triggered_by,
        )

        errors_count = 0
        final_state  = state

        try:
            final_state = await self.graph.ainvoke(state)
            errors_count = len(final_state.get("errors") or [])

        except Exception as e:
            errors_count = 1
            logger.error(f"[ORCHESTRATOR] Cycle {cycle_id} erreur: {e}")
            if _LOGGING_ENABLED:
                log_node_error(
                    log_id     = -1,
                    cycle_id   = cycle_id,
                    agent_name = "orchestrator",
                    node_name  = "run_cycle",
                    error      = e,
                    store_id   = "I63",
                )

        # ── Métriques totales ──────────────────────────────────────────────
        total_ms = (datetime.utcnow() - started).total_seconds() * 1000
        metrics  = dict(final_state.get("metrics") or {})
        metrics["total_ms"]       = round(total_ms)
        metrics["cycle_id"]       = cycle_id
        metrics["completed_at"]   = datetime.utcnow().isoformat()
        final_state = {**final_state, "metrics": metrics}

        # ── Log cycle complet ──────────────────────────────────────────────
        if _LOGGING_ENABLED:
            log_cycle(
                cycle_id       = cycle_id,
                state          = final_state,
                total_ms       = total_ms,
                triggered_by   = triggered_by,
                store_id       = "I63",
                nodes_executed = int(metrics.get("nodes_executed", 0)),
                errors_count   = errors_count,
                rag_used       = bool(final_state.get("rag_used")),
                nb_rag_scripts = int(final_state.get("nb_rag_scripts") or 0),
            )
            # Enrichissement RAG si cycle réussi
            if errors_count == 0 and final_state.get("strategie_actions"):
                try:
                    enrich_rag_from_cycle(cycle_id, final_state, "I63")
                except Exception:
                    pass

        # ── Résumé console ─────────────────────────────────────────────────
        urgency  = final_state.get("urgency_level", "?")
        gap      = final_state.get("gap_objectif",  0)
        nb_act   = len(final_state.get("strategie_actions") or [])
        rag_used = final_state.get("rag_used", False)

        tracer.cycle_end(
            cycle_id, total_ms,
            nodes   = int(metrics.get("nodes_executed", 0)),
            urgency = urgency,
            gap     = gap,
            eod     = final_state.get("forecast_eod", 0),
        )

        logger.info(
            f"[ORCHESTRATOR] ✅ Cycle {cycle_id} terminé | "
            f"{total_ms:.0f}ms | urgence={urgency} | gap={gap:.1f}% | "
            f"actions={nb_act} | RAG={'✓' if rag_used else '✗'} | "
            f"erreurs={errors_count}"
        )

        self.last_state  = final_state
        self.last_result = {
            "cycle_id":       cycle_id,
            "niveau_urgence": urgency,
            "ecart_objectif": gap,
            "forecast_eod":   final_state.get("forecast_eod", 0),
            "analyst_summary": final_state.get("analyst_summary", ""),
            "strategie":      final_state.get("strategie", ""),
            "nb_actions":     nb_act,
            "rag_used":       rag_used,
            "total_ms":       round(total_ms),
        }

        return final_state