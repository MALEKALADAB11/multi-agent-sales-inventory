"""
graph.py — Orchestrateur LangGraph v3 + Langfuse Observabilite Totale
======================================================================
Pipeline : Analyste -> Stratege -> Coach
Chaque cycle = 1 trace Langfuse hierarchique :
  trace (cycle)
    |-- span (agent-analyste)
    |     |-- span (receive_pos) ... span (save_memory)
    |     |-- generation (analyste-llm-summary)
    |-- span (agent-stratege)
    |     |-- span (fetch_context) ... span (build_output)
    |     |-- span (rag_search)
    |     |-- generation (stratege-llm-strategy)
    |-- span (agent-coach)  [si message present]
    |     |-- generation (coach-llm)
    |-- score (cycle-quality)
    |-- score (analyste-quality)
    |-- score (stratege-quality)
"""
import logging
import time
import uuid
from datetime import datetime

from langgraph.graph import StateGraph, END

from core.state import SalesAgentState, initial_state
from data.json_service import JsonDataService
from data.postgres_provider import normalize_store_id
from mcp_servers.timefm.tools import TimesFMTools

logger = logging.getLogger(__name__)


# ── Agent logger (non bloquant) ───────────────────────────────────────────────
try:
    from agent_logger import (
        log_cycle, log_node_start, log_node_complete,
        log_node_error, enrich_rag_from_cycle,
    )
    _LOGGING_ENABLED = True
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


# ── Langfuse Tracer ───────────────────────────────────────────────────────────
try:
    from langfuse_observer import LangfuseTracer, get_langfuse, trace_full_cycle
    _LANGFUSE_OK = True
except ImportError:
    _LANGFUSE_OK = False
    logger.warning("[ORCHESTRATOR] langfuse_observer non disponible")

# ── Console tracer (fallback visuel) ──────────────────────────────────────────
try:
    from orchestration.tracer import CycleTracer
    _console_tracer = CycleTracer(show_state=True)
except ImportError:
    class _FakeTracer:
        def cycle_start(self, *a, **k): pass
        def cycle_end(self, *a, **k): pass
        def router_decision(self, *a, **k): pass
    _console_tracer = _FakeTracer()


# ══════════════════════════════════════════════════════════════════════════════
# Wrappers async avec Langfuse spans
# ══════════════════════════════════════════════════════════════════════════════

async def _run_analyst(state: dict) -> dict:
    if not _ANALYST_OK:
        logger.warning("[ORCHESTRATOR] Analyst non disponible")
        return state
    try:
        agent = get_analyst_agent()
        t0 = time.time()
        result = await agent.ainvoke(state)
        duration = (time.time() - t0) * 1000

        # Langfuse span pour l'agent analyste
        if _LANGFUSE_OK:
            lf = get_langfuse()
            if lf:
                cycle_id = _get_cycle_id(state)
                lf.span(
                    trace_id=cycle_id,
                    name="agent-analyste",
                    input={
                        "store_id": _get_store_id(state),
                        "trigger": (state.get("metrics") or {}).get("triggered_by", "unknown"),
                    },
                    output={
                        "urgency": result.get("urgency_level", "?"),
                        "gap_pct": round(float(result.get("gap_objectif", 0) or 0), 1),
                        "ca_today": round(float((result.get("pos_data") or {}).get("current_revenue", 0) or 0)),
                        "forecast_eod": round(float(result.get("forecast_eod", 0) or 0)),
                        "summary": str(result.get("analyst_summary", ""))[:200],
                        "nodes_executed": int((result.get("metrics") or {}).get("nodes_executed", 0)),
                    },
                    metadata={"duration_ms": round(duration), "agent": "analyste"},
                )

        logger.info(
            f"[ORCHESTRATOR] Analyste | "
            f"urgence={result.get('urgency_level', '?')} | "
            f"gap={result.get('gap_objectif', 0):.1f}% | "
            f"{duration:.0f}ms"
        )
        return result
    except Exception as e:
        logger.warning(f"[ORCHESTRATOR] Analyste erreur: {str(e)[:80]}")
        return state


async def _run_stratege(state: dict) -> dict:
    if not _STRATEGE_OK:
        logger.warning("[ORCHESTRATOR] Stratege non disponible")
        return state
    try:
        agent = get_stratege_agent()
        t0 = time.time()
        result = await agent.ainvoke(state)
        duration = (time.time() - t0) * 1000
        nb_actions = len(result.get("strategie_actions") or [])

        # Langfuse span pour l'agent stratege
        if _LANGFUSE_OK:
            lf = get_langfuse()
            if lf:
                cycle_id = _get_cycle_id(state)
                lf.span(
                    trace_id=cycle_id,
                    name="agent-stratege",
                    input={
                        "urgency": state.get("urgency_level", "?"),
                        "gap_pct": round(float(state.get("gap_objectif", 0) or 0), 1),
                        "analyst_summary": str(state.get("analyst_summary", ""))[:150],
                    },
                    output={
                        "nb_actions": nb_actions,
                        "rag_used": result.get("rag_used", False),
                        "nb_rag_scripts": result.get("nb_rag_scripts", 0),
                        "cause_racine": str(result.get("cause_racine", ""))[:100],
                        "focus_produits": (result.get("focus_produits") or [])[:3],
                        "strategie": str(result.get("strategie", ""))[:200],
                    },
                    metadata={"duration_ms": round(duration), "agent": "stratege"},
                )

        logger.info(
            f"[ORCHESTRATOR] Stratege | "
            f"{nb_actions} actions | "
            f"RAG={result.get('rag_used', False)} | "
            f"{duration:.0f}ms"
        )
        return result
    except Exception as e:
        logger.warning(f"[ORCHESTRATOR] Stratege erreur: {str(e)[:80]}")
        return state


async def _run_coach(state: dict) -> dict:
    pos_data = state.get("pos_data") or {}
    message = pos_data.get("coach_message", "")
    if not message:
        return state
    if not _COACH_OK:
        return state
    try:
        agent = get_coach_agent()
        t0 = time.time()
        result = await agent.ainvoke(state)
        duration = (time.time() - t0) * 1000

        # Langfuse span pour l'agent coach
        if _LANGFUSE_OK:
            lf = get_langfuse()
            if lf:
                cycle_id = _get_cycle_id(state)
                ctx = result.get("coach_context") or {}
                conseil_result = ctx.get("conseil_result") or {}
                lf.span(
                    trace_id=cycle_id,
                    name="agent-coach",
                    input={
                        "advisor": ctx.get("advisor_name", ""),
                        "message": message[:150],
                        "question_type": ctx.get("question_type", "opening"),
                    },
                    output={
                        "conseil": str(result.get("conseil_final", ""))[:200],
                        "confidence": conseil_result.get("confidence", 0),
                        "rag_used": conseil_result.get("rag_used", False),
                        "source": conseil_result.get("source", ""),
                    },
                    metadata={"duration_ms": round(duration), "agent": "coach"},
                )

        logger.info(
            f"[ORCHESTRATOR] Coach | "
            f"conseil={str(result.get('conseil_final', ''))[:60]} | "
            f"{duration:.0f}ms"
        )
        return result
    except Exception as e:
        logger.warning(f"[ORCHESTRATOR] Coach erreur: {str(e)[:80]}")
        return state


# ── Routing ───────────────────────────────────────────────────────────────────

def route_after_analyst(state: dict) -> str:
    urgency = state.get("urgency_level", "LOW")
    gap = state.get("gap_objectif", 0)
    _console_tracer.router_decision("analyste", "stratege",
                                     f"urgence={urgency} . gap={gap:.1f}%")

    # Langfuse span pour la decision de routage
    if _LANGFUSE_OK:
        lf = get_langfuse()
        if lf:
            cycle_id = _get_cycle_id(state)
            lf.span(
                trace_id=cycle_id,
                name="router/analyste->stratege",
                input={"urgency": urgency, "gap_pct": round(float(gap), 1)},
                output={"next": "stratege", "reason": f"urgence={urgency} gap={gap:.1f}%"},
                metadata={"router": "post-analyste"},
            )
    return "stratege"


def route_after_stratege(state: dict) -> str:
    pos_data = state.get("pos_data") or {}
    has_msg = bool(pos_data.get("coach_message", ""))
    next_node = "coach" if has_msg else "__end__"

    if _LANGFUSE_OK:
        lf = get_langfuse()
        if lf:
            cycle_id = _get_cycle_id(state)
            nb_actions = len(state.get("strategie_actions") or [])
            lf.span(
                trace_id=cycle_id,
                name=f"router/stratege->{next_node}",
                input={"has_coach_message": has_msg, "nb_actions": nb_actions},
                output={"next": next_node},
                metadata={"router": "post-stratege"},
            )

    return "coach" if has_msg else END


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_cycle_id(state: dict) -> str:
    return (state.get("metrics") or {}).get("cycle_id", "") or state.get("cycle_id", "unknown")

def _get_store_id(state: dict) -> str:
    return (state.get("pos_data") or {}).get("store_id", "I63") or state.get("store_id", "I63")


# ══════════════════════════════════════════════════════════════════════════════
# Build Graph
# ══════════════════════════════════════════════════════════════════════════════

def build_graph(json_svc: JsonDataService, timefm: TimesFMTools):
    graph = StateGraph(SalesAgentState)

    graph.add_node("analyste", _run_analyst)
    graph.add_node("stratege", _run_stratege)
    graph.add_node("coach", _run_coach)

    graph.set_entry_point("analyste")
    graph.add_conditional_edges("analyste", route_after_analyst, {
        "stratege": "stratege",
    })
    graph.add_conditional_edges("stratege", route_after_stratege, {
        "coach": "coach",
        END: END,
    })
    graph.add_edge("coach", END)

    logger.info(
        f"[ORCHESTRATOR] Graphe construit -- "
        f"Analyste={'Y' if _ANALYST_OK else 'N'} | "
        f"Stratege={'Y' if _STRATEGE_OK else 'N'} | "
        f"Coach={'Y' if _COACH_OK else 'N'} | "
        f"Langfuse={'Y' if _LANGFUSE_OK else 'N'}"
    )
    return graph.compile()


# ══════════════════════════════════════════════════════════════════════════════
# CycleOrchestrator — avec Langfuse trace hierarchique complete
# ══════════════════════════════════════════════════════════════════════════════

class CycleOrchestrator:
    def __init__(self, json_svc: JsonDataService, timefm: TimesFMTools):
        self.graph = build_graph(json_svc, timefm)
        self.json_svc = json_svc
        self.timefm = timefm
        self.last_state = None
        self.last_result = None
        self.cycle_count = 0

    async def run_cycle(
        self,
        store_id: str = "store-lac2",
        triggered_by: str = "cron",
    ) -> dict:
        cycle_id = f"cycle-{uuid.uuid4().hex[:8]}"
        self.cycle_count += 1
        started = datetime.utcnow()
        internal_id = normalize_store_id(store_id)

        _console_tracer.cycle_start(cycle_id, store_id, triggered_by)

        # ── Langfuse : creer la trace racine du cycle ─────────────────────
        lf_trace = None
        if _LANGFUSE_OK:
            lf = get_langfuse()
            if lf:
                lf_trace = lf.trace(
                    id=cycle_id,
                    name="coaching-cycle",
                    user_id=internal_id,
                    session_id=f"session-{internal_id}-{datetime.now().strftime('%Y%m%d')}",
                    tags=["cycle", internal_id, triggered_by,
                          f"cycle-{self.cycle_count}"],
                    input={
                        "trigger": triggered_by,
                        "store_id": internal_id,
                        "cycle_number": self.cycle_count,
                        "timestamp": datetime.now().isoformat(),
                    },
                )

        # ── Etat initial ──────────────────────────────────────────────────
        state = initial_state(
            store_id=internal_id,
            cycle_id=cycle_id,
            triggered_by=triggered_by,
        )

        errors_count = 0
        final_state = state

        try:
            final_state = await self.graph.ainvoke(state)
            errors_count = len(final_state.get("errors") or [])
        except Exception as e:
            errors_count = 1
            logger.error(f"[ORCHESTRATOR] Cycle {cycle_id} erreur: {e}")
            if _LOGGING_ENABLED:
                log_node_error(-1, cycle_id, "orchestrator", "run_cycle", e, internal_id)

            # Langfuse : marquer l'erreur
            if _LANGFUSE_OK and lf_trace:
                try:
                    lf = get_langfuse()
                    lf.span(trace_id=cycle_id, name="cycle-error",
                            output={"error": str(e)[:300]}, level="ERROR")
                except Exception:
                    pass

        # ── Metriques totales ─────────────────────────────────────────────
        total_ms = (datetime.utcnow() - started).total_seconds() * 1000
        metrics = dict((final_state or {}).get("metrics") or {})
        metrics["total_ms"] = round(total_ms)
        metrics["cycle_id"] = cycle_id
        metrics["completed_at"] = datetime.utcnow().isoformat()
        final_state = {**(final_state or state), "metrics": metrics}

        # ── Extraire les metriques finales ─────────────────────────────────
        urgency = final_state.get("urgency_level", "?")
        gap = float(final_state.get("gap_objectif", 0) or 0)
        nb_act = len(final_state.get("strategie_actions") or [])
        rag_used = final_state.get("rag_used", False)
        nb_rag = int(final_state.get("nb_rag_scripts") or 0)
        forecast_eod = float(final_state.get("forecast_eod", 0) or 0)
        analyst_summary = str(final_state.get("analyst_summary", ""))
        conseil_final = str(final_state.get("conseil_final", ""))

        analyste_ms = float(metrics.get("analyste_llm_ms", 0) or 0)
        stratege_ms = float(metrics.get("stratege_llm_ms", 0) or 0)
        coach_ms = float(metrics.get("coach_llm_ms", 0) or 0)

        # ── Langfuse : finaliser la trace avec output + scores ────────────
        if _LANGFUSE_OK:
            lf = get_langfuse()
            if lf:
                try:
                    # Update trace output
                    if lf_trace:
                        lf_trace.update(output={
                            "urgency": urgency,
                            "gap_pct": round(gap, 1),
                            "nb_actions": nb_act,
                            "rag_used": rag_used,
                            "nb_rag_scripts": nb_rag,
                            "forecast_eod": round(forecast_eod),
                            "errors": errors_count,
                            "total_ms": round(total_ms),
                            "conseil": conseil_final[:150],
                        })

                    # Score cycle-quality
                    uw = {"CRITICAL": 1.0, "HIGH": 0.8, "MEDIUM": 0.5, "LOW": 0.2}.get(urgency, 0.5)
                    aw = min(1.0, nb_act / 3) if nb_act else 0
                    rw = 0.15 if rag_used else 0.0
                    sw = max(0.0, 1.0 - total_ms / 60000)
                    ew = 0.0 if errors_count == 0 else -0.2
                    score_val = round(max(0, uw * 0.30 + aw * 0.30 + rw + sw * 0.15 + ew + 0.10), 3)
                    lf.score(
                        trace_id=cycle_id, name="cycle-quality",
                        value=score_val,
                        comment=(f"urgency={urgency} gap={gap:.1f}% "
                                 f"actions={nb_act} rag={rag_used} "
                                 f"errors={errors_count} {total_ms:.0f}ms"),
                    )

                    # Score cycle-speed
                    speed_score = max(0.0, 1.0 - total_ms / 120000)
                    lf.score(
                        trace_id=cycle_id, name="cycle-speed",
                        value=round(speed_score, 3),
                        comment=f"total={total_ms:.0f}ms analyste={analyste_ms:.0f}ms "
                                f"stratege={stratege_ms:.0f}ms coach={coach_ms:.0f}ms",
                    )

                    # Score cycle-completeness
                    completeness = 0.0
                    if final_state.get("analyst_summary"): completeness += 0.33
                    if final_state.get("strategie_actions"): completeness += 0.34
                    if final_state.get("conseil_final"): completeness += 0.33
                    lf.score(
                        trace_id=cycle_id, name="cycle-completeness",
                        value=round(completeness, 3),
                        comment=f"analyste={'Y' if final_state.get('analyst_summary') else 'N'} "
                                f"stratege={'Y' if final_state.get('strategie_actions') else 'N'} "
                                f"coach={'Y' if final_state.get('conseil_final') else 'N'}",
                    )

                    lf.flush()
                    logger.debug(f"[LANGFUSE] Cycle {cycle_id} trace complete | quality={score_val}")
                except Exception as e:
                    logger.debug(f"[LANGFUSE] Cycle finalize: {e}")

        # ── Log cycle via agent_logger ─────────────────────────────────────
        if _LOGGING_ENABLED:
            log_cycle(
                cycle_id=cycle_id, state=final_state, total_ms=total_ms,
                triggered_by=triggered_by, store_id=internal_id,
                nodes_executed=int(metrics.get("nodes_executed", 0)),
                errors_count=errors_count, rag_used=rag_used,
                nb_rag_scripts=nb_rag,
            )
            if errors_count == 0 and final_state.get("strategie_actions"):
                try:
                    enrich_rag_from_cycle(cycle_id, final_state, internal_id)
                except Exception:
                    pass

        # ── Console ────────────────────────────────────────────────────────
        _console_tracer.cycle_end(
            cycle_id, total_ms,
            nodes=int(metrics.get("nodes_executed", 0)),
            urgency=urgency, gap=gap,
            eod=forecast_eod,
        )

        logger.info(
            f"[ORCHESTRATOR] Cycle {cycle_id} | "
            f"store={internal_id} | {total_ms:.0f}ms | "
            f"urgence={urgency} | gap={gap:.1f}% | "
            f"actions={nb_act} | RAG={'Y' if rag_used else 'N'}({nb_rag}) | "
            f"erreurs={errors_count}"
        )

        self.last_state = final_state
        self.last_result = {
            "cycle_id": cycle_id,
            "store_id": internal_id,
            "niveau_urgence": urgency,
            "ecart_objectif": gap,
            "forecast_eod": forecast_eod,
            "analyst_summary": analyst_summary[:200],
            "strategie": str(final_state.get("strategie", ""))[:200],
            "nb_actions": nb_act,
            "rag_used": rag_used,
            "total_ms": round(total_ms),
        }

        return final_state
