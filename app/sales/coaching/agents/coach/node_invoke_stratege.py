"""
node_invoke_stratege.py — Nœud 4 du CoachAgent v2.
Appelle le StrategistAgent via l'orchestrateur résilient.
Injecte strategie_actions dans le state pour node_generate_conseil.
"""

import logging
import time

from app.core.agent_logger import AgentLogger
from app.sales.core.state import SalesAgentState

logger = logging.getLogger(__name__)


def _cycle_id(state: dict) -> str:
    return (state.get("metrics") or {}).get("cycle_id", "") or state.get("cycle_id", "unknown")


def _store_id(state: dict) -> str:
    ctx = state.get("coach_context") or {}
    return ctx.get("store_id") or (state.get("pos_data") or {}).get("store_id", "ALL")


def _update_metrics(state: dict, key: str, value) -> dict:
    metrics = dict(state.get("metrics") or {})
    metrics[key] = value
    metrics["nodes_executed"] = int(metrics.get("nodes_executed", 0)) + 1
    return metrics


async def node_invoke_stratege_for_coach(state: SalesAgentState) -> dict:
    """
    Nœud 4 du CoachAgent v2.

    Appelle CoachStrategeOrchestrator.invoke(state) :
      - Cache hit  → <1ms
      - Stratège   → ~3-48s selon LLM
      - Fallback   → toujours des actions, CoachAgent jamais bloqué

    Ajoute au state :
      strategie_actions  : list[dict] — actions pour generate_conseil
      strategie_source   : str        — "success"|"cached"|"stale_cache"|"fallback"
      strategie_cached   : bool
    """
    cid = _cycle_id(state)
    sid = _store_id(state)
    log = AgentLogger("coach", cid, sid)
    log_id = log.node_start("invoke_stratege_for_coach", state)
    t0 = time.time()
    logger.info("[COACH] Node 4 — invoke_stratege_for_coach")

    # Vérifier si des actions sont déjà présentes (cycle planifié)
    existing_actions = state.get("strategie_actions") or []
    if existing_actions:
        logger.info(
            "[COACH-STRATEGE] Actions déjà présentes (%d) — skip orchestrateur",
            len(existing_actions),
        )
        duration = (time.time() - t0) * 1000
        output = {**state, "strategie_source": "pre_loaded", "strategie_cached": True}
        log.node_done("invoke_stratege_for_coach", log_id, output, duration,
                      {"source": "pre_loaded", "nb_actions": len(existing_actions)})
        return {**output, "metrics": _update_metrics(state, "coach_stratege_ms", round(duration))}

    try:
        from app.sales.coaching.orchestrator.bootstrap import get_orchestrator
        orchestrator = get_orchestrator()
        result = await orchestrator.invoke(state)

        logger.info(
            "[COACH-STRATEGE] %s | %d actions | cached=%s | %.0fms",
            result.source, result.nb_actions, result.cached, result.latency_ms,
        )

        duration = (time.time() - t0) * 1000
        output = {
            **state,
            "strategie_actions": result.actions,
            "strategie_source":  result.source,
            "strategie_cached":  result.cached,
        }
        log.node_done("invoke_stratege_for_coach", log_id, output, duration,
                      {"source": result.source, "nb_actions": result.nb_actions,
                       "cached": result.cached})
        return {**output, "metrics": _update_metrics(state, "coach_stratege_ms", round(duration))}

    except RuntimeError as e:
        # Orchestrateur non initialisé — fallback silencieux
        logger.warning("[COACH-STRATEGE] Orchestrateur non dispo: %s — coach continue sans stratégie", e)
        duration = (time.time() - t0) * 1000
        output = {**state, "strategie_actions": [], "strategie_source": "unavailable",
                  "strategie_cached": False}
        log.node_done("invoke_stratege_for_coach", log_id, output, duration,
                      {"source": "unavailable"})
        return {**output, "metrics": _update_metrics(state, "coach_stratege_ms", round(duration))}

    except Exception as e:
        logger.warning("[COACH-STRATEGE] Erreur inattendue: %s — fallback activé", e)
        duration = (time.time() - t0) * 1000
        log.node_error("invoke_stratege_for_coach", log_id, e, state)
        output = {**state, "strategie_actions": [], "strategie_source": "error",
                  "strategie_cached": False}
        return {**output, "metrics": _update_metrics(state, "coach_stratege_ms", round(duration))}
