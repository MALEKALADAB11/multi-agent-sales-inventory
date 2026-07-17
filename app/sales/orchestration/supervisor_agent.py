"""
SupervisorAgent — Master LangGraph Orchestrator
================================================
Implements §16 (Pseudo-code LangGraph) from ARCHITECTURE_AGENTIQUE_COMMUNICATION.md.

Graph topology:
  START → initialize_state
        → [sales_branch, knowledge_branch, context_branch, inventory_branch]  (parallel)
        → merge_outputs
        → coach_agent
        → guardrail_agent
        ├─ approve   → notify_frontend
        ├─ rewrite   → coach_agent  (max 1 iteration)
        ├─ escalate  → human_validation → notify_frontend
        └─ block     → safe_fallback → notify_frontend
        → save_memory → END

Branch implementations:
  sales_branch      — wraps existing CycleOrchestrator (Analyst → Strategist)
  knowledge_branch  — wraps StrategistAgent.node_rag_search (already exists)
  context_branch    — wraps fetch_full_context from stratege.tools
  inventory_branch  — wraps InventoryDecisionAgent (exists in inventory-module)
  coach_agent       — wraps cross_domain scoring (rank_products)
  guardrail_agent   — new module from Sprint 5 S5.1
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, Optional
from app.core.config import DEFAULT_STORE_ID

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Node: initialize_state
# ═══════════════════════════════════════════════════════════════════════════════

async def node_initialize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Bootstrap the RetailState for a new cycle."""
    cycle_id = state.get("cycle_id") or f"cycle-{uuid.uuid4().hex[:8]}"
    return {
        **state,
        "cycle_id":      cycle_id,
        "agents_invoked": [],
        "errors":         [],
        "metrics":        {},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Node: sales_branch  (Analyst → Strategist)
# ═══════════════════════════════════════════════════════════════════════════════

async def node_sales_branch(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the Sales branch: AnalystAgent (ReAct) → StrategistAgent (Reflexion).
    Delegates to the existing cycle orchestration for the store.
    Reuses the singleton CycleOrchestrator created at app startup (main.py)
    instead of building a new one per cycle — CycleOrchestrator.__init__ takes
    (json_svc, timefm), not store_id.
    """
    t0 = time.time()
    store_id = state.get("store_id", DEFAULT_STORE_ID)

    try:
        from main import app as _main_app
        orchestrator = _main_app.state.orchestrator
        result = await orchestrator.run_cycle(
            store_id=store_id,
            triggered_by=state.get("trigger_type", "supervisor"),
        )
        ms = round((time.time() - t0) * 1000)
        agents = list(state.get("agents_invoked", [])) + ["analyst", "strategist"]
        return {
            **state,
            **result,
            "agents_invoked": agents,
            "metrics": {**state.get("metrics", {}), "sales_branch_ms": ms},
        }
    except Exception as e:
        logger.warning("[Supervisor] sales_branch failed: %s", e)
        errors = list(state.get("errors", [])) + [f"sales_branch: {e}"]
        return {
            **state,
            "errors": errors,
            "urgency_level": state.get("urgency_level", "MEDIUM"),
            "agents_invoked": list(state.get("agents_invoked", [])) + ["analyst_failed"],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Node: knowledge_branch  (RAG retrieval)
# ═══════════════════════════════════════════════════════════════════════════════

async def node_knowledge_branch(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the Knowledge/RAG branch: retrieve relevant sales scripts from Milvus.
    Reuses node_rag_search from the Strategist.
    """
    t0 = time.time()
    try:
        from app.sales.coaching.agents.stratege.nodes import node_rag_search
        result = await node_rag_search(state)
        ms = round((time.time() - t0) * 1000)
        agents = list(state.get("agents_invoked", [])) + ["knowledge_rag"]
        return {
            **state,
            **result,
            "agents_invoked": agents,
            "metrics": {**state.get("metrics", {}), "knowledge_branch_ms": ms},
        }
    except Exception as e:
        logger.warning("[Supervisor] knowledge_branch failed: %s", e)
        errors = list(state.get("errors", [])) + [f"knowledge_branch: {e}"]
        return {**state, "errors": errors, "rag_context": {}, "retrieved_scripts": []}


# ═══════════════════════════════════════════════════════════════════════════════
# Node: context_branch  (Sentinel / external signals)
# ═══════════════════════════════════════════════════════════════════════════════

async def node_context_branch(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the Context/Sentinel branch: weather, events, promotions, QoS signals.
    Reuses fetch_full_context from stratege.tools.
    """
    t0 = time.time()
    store_id = state.get("store_id", DEFAULT_STORE_ID)
    try:
        from app.sales.coaching.agents.stratege.tools import fetch_full_context
        context = await fetch_full_context(store_id)
        ms = round((time.time() - t0) * 1000)
        agents = list(state.get("agents_invoked", [])) + ["context_sentinel"]
        return {
            **state,
            "external_context": context,
            "context_report":   context.get("summary", {}),
            "agents_invoked":   agents,
            "metrics": {**state.get("metrics", {}), "context_branch_ms": ms},
        }
    except Exception as e:
        logger.warning("[Supervisor] context_branch failed: %s", e)
        errors = list(state.get("errors", [])) + [f"context_branch: {e}"]
        return {**state, "errors": errors, "external_context": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# Node: inventory_branch  (InventoryAnalysis → InventoryDecision)
# ═══════════════════════════════════════════════════════════════════════════════

async def node_inventory_branch(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the Inventory branch for the top-priority SKU in the store.
    Uses InventoryDecisionAgent (existing in inventory-module).
    Result is written to state.inventory_decisions and inventory_snapshot.
    """
    t0 = time.time()
    store_id = state.get("store_id", DEFAULT_STORE_ID)

    try:
        # analyze_store is a FastAPI route handler with Query(...) defaults —
        # calling it directly requires passing every kwarg explicitly, otherwise
        # unpassed params receive the Query() sentinel object instead of their
        # literal default.
        # fast=True: rule-based orchestrator, no per-SKU LLM call — measured
        # fast=False at 143 SKUs to still be running past the 100s cycle budget
        # (verification 2026-07-02). fast=True is what the route's own docstring
        # recommends for WS broadcasts. EOQ/reorder-point/riskLevel are still
        # computed deterministically; only the LLM-narrated recommendation text
        # is unavailable in this mode.
        from app.inventory.api.routes import analyze_store
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: analyze_store(
                store_id=store_id,
                business_objective="avoid_stockout",
                force_refresh=False,
                fast=True,
                page=1,
                page_size=200,
            ),
        )
        ms = round((time.time() - t0) * 1000)
        agents = list(state.get("agents_invoked", [])) + ["inventory_analysis", "inventory_decision"]
        items     = result.get("items", []) if isinstance(result, dict) else []
        # fast=True doesn't populate decision_result (no LLM narration), so
        # "recommendation" stays None — fall back to riskLevel/formulaOrderQty
        # (still deterministic and actionable) to decide what counts as a decision.
        decisions = [
            it for it in items
            if it.get("recommendation") or it.get("riskLevel") in ("critical", "high")
        ]
        critical  = [
            it for it in items
            if it.get("riskLevel") in ("critical", "high")
            or it.get("orderTiming") in ("immediate", "this_week")
        ]

        return {
            **state,
            "inventory_decisions":   decisions,
            "critical_stock_alerts": critical,
            "agents_invoked":        agents,
            "metrics": {**state.get("metrics", {}), "inventory_branch_ms": ms},
        }
    except Exception as e:
        logger.warning("[Supervisor] inventory_branch failed: %s", e)
        errors = list(state.get("errors", [])) + [f"inventory_branch: {e}"]
        return {**state, "errors": errors, "inventory_decisions": [], "critical_stock_alerts": []}


# ═══════════════════════════════════════════════════════════════════════════════
# Node: merge_outputs  (fan-in from 4 parallel branches)
# ═══════════════════════════════════════════════════════════════════════════════

async def node_merge_outputs(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge outputs from 4 parallel branches into the unified RetailState.
    Computes aggregate metrics and logs what was received.
    """
    inv_count  = len(state.get("inventory_decisions", []))
    script_count = len(state.get("retrieved_scripts", []))
    gap_pct    = float(state.get("gap_pct", 0))
    urgency    = state.get("urgency_level", "MEDIUM")

    logger.info(
        "[Supervisor] merge_outputs — gap=%.0f%% urgency=%s inv_decisions=%d rag_scripts=%d errors=%d",
        gap_pct, urgency, inv_count, script_count, len(state.get("errors", [])),
    )

    return {
        **state,
        "agents_invoked": list(state.get("agents_invoked", [])) + ["merge"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Node: coach_agent  (cross-domain fusion)
# ═══════════════════════════════════════════════════════════════════════════════

async def node_coach_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    CoachAgent Cross-Domain: fuse Sales + Inventory + RAG + Context
    using the 6-criteria weighted scoring formula (S5.2).
    """
    t0 = time.time()
    store_id   = state.get("store_id", DEFAULT_STORE_ID)
    advisor_id = state.get("advisor_id", "")

    try:
        from app.sales.coaching.agents.coach.cross_domain_tools import (
            get_sales_context,
            get_recommendable_products,
            retrieve_advisor_history,
            rank_products,
        )

        sales_ctx, products, advisor = await asyncio.gather(
            get_sales_context(store_id, advisor_id),
            asyncio.get_event_loop().run_in_executor(
                None, get_recommendable_products, store_id,
                state.get("gap_amount", 0), None, 15
            ),
            asyncio.get_event_loop().run_in_executor(
                None, retrieve_advisor_history, advisor_id, store_id
            ),
        )

        scored = rank_products(
            products        = products,
            sales_context   = sales_ctx,
            advisor_history = advisor,
            top_n           = 3,
        )

        top_product = scored[0] if scored else {}
        ms = round((time.time() - t0) * 1000)

        # Build recommendation dict (consumed by guardrail + frontend)
        critical_alerts = state.get("critical_stock_alerts", [])
        avoid_skus = [a.get("sku") for a in critical_alerts[:2] if a.get("sku")]

        coach_recommendation = {
            "priority":          state.get("urgency_level", "MEDIUM"),
            "product_to_push":   top_product.get("name", ""),
            "product_to_avoid":  avoid_skus[0] if avoid_skus else None,
            "message_for_advisor": (
                f"Poussez {top_product.get('name','')} "
                f"({top_product.get('recommendation_reason','')})."
                + (f" Évitez {avoid_skus[0]} — stock critique." if avoid_skus else "")
            ),
            "business_justification": top_product.get("recommendation_reason", ""),
            "confidence": top_product.get("final_score", 0.5),
        }

        agents = list(state.get("agents_invoked", [])) + ["coach_cross_domain"]
        return {
            **state,
            "coach_recommendation":  coach_recommendation,
            "scored_products":       scored,
            "gap_amount":            float(sales_ctx.get("gap_amount", 0)),
            "agents_invoked":        agents,
            "metrics": {**state.get("metrics", {}), "coach_agent_ms": ms},
        }

    except Exception as e:
        logger.warning("[Supervisor] coach_agent failed: %s", e)
        errors = list(state.get("errors", [])) + [f"coach_agent: {e}"]
        return {
            **state,
            "errors": errors,
            "coach_recommendation": {
                "priority": "MEDIUM",
                "product_to_push": "",
                "message_for_advisor": "Consultez le catalogue disponible.",
                "confidence": 0.3,
            },
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Node: guardrail_agent
# ═══════════════════════════════════════════════════════════════════════════════

async def node_guardrail(state: Dict[str, Any]) -> Dict[str, Any]:
    """Run the Guardrail Agent (S5.1) on the coach recommendation."""
    try:
        from app.sales.coaching.agents.guardrail.guardrail_agent import guardrail_node
        return guardrail_node(state)
    except Exception as e:
        logger.warning("[Supervisor] guardrail failed: %s", e)
        return {**state, "guardrail_status": "APPROVE", "guardrail_issues": []}


def route_after_guardrail(state: Dict[str, Any]) -> str:
    return state.get("guardrail_status", "APPROVE").lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Node: notify_frontend
# ═══════════════════════════════════════════════════════════════════════════════

async def node_notify_frontend(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Publish validated recommendation to WebSocket broadcast.
    Uses existing _broadcast infrastructure from main.py.
    """
    store_id = state.get("store_id", "")
    guardrail = state.get("guardrail_status", "APPROVE")
    reco      = state.get("coach_recommendation", {})

    if guardrail == "BLOCK":
        reco = {"message_for_advisor": state.get("guardrail_safe_fallback", ""), "priority": "LOW"}

    try:
        import json
        payload = json.dumps({
            "type":               "coach_recommendation",
            "cycle_id":           state.get("cycle_id", ""),
            "store_id":           store_id,
            "recommendation":     reco,
            "guardrail_status":   guardrail,
            "guardrail_issues":   state.get("guardrail_issues", []),
            "requires_hitl":      state.get("requires_human_validation", False),
            "scored_products":    state.get("scored_products", [])[:3],
            "urgency_level":      state.get("urgency_level", ""),
            "timestamp":          __import__("datetime").datetime.utcnow().isoformat(),
        })

        # Fire-and-forget broadcast — main.py's _broadcast is in scope at runtime
        from main import _broadcast
        await _broadcast(store_id, payload)
        logger.info("[Supervisor] notify_frontend — %s guardrail=%s", store_id, guardrail)
    except Exception as e:
        logger.warning("[Supervisor] notify_frontend: %s", e)

    return {**state, "agents_invoked": list(state.get("agents_invoked", [])) + ["notify_frontend"]}


# ═══════════════════════════════════════════════════════════════════════════════
# Node: safe_fallback
# ═══════════════════════════════════════════════════════════════════════════════

async def node_safe_fallback(state: Dict[str, Any]) -> Dict[str, Any]:
    """Replace blocked recommendation with a safe default."""
    fallback_msg = state.get("guardrail_safe_fallback") or (
        "Consultez le catalogue produits disponibles et contactez le manager "
        "pour valider l'action commerciale appropriée."
    )
    safe_reco = {
        "priority":            "LOW",
        "product_to_push":     "",
        "message_for_advisor": fallback_msg,
        "confidence":          0.0,
    }
    return {**state, "coach_recommendation": safe_reco,
            "agents_invoked": list(state.get("agents_invoked", [])) + ["safe_fallback"]}


# ═══════════════════════════════════════════════════════════════════════════════
# Node: human_validation (HITL)
# ═══════════════════════════════════════════════════════════════════════════════

async def node_human_validation(state: Dict[str, Any]) -> Dict[str, Any]:
    """Submit strategy for HITL review via hitl_router.submit_hitl_review."""
    try:
        from app.api.hitl import submit_hitl_review
        review_id = await submit_hitl_review(
            store_id          = state.get("store_id", ""),
            cycle_id          = state.get("cycle_id", ""),
            urgency_level     = state.get("urgency_level", "HIGH"),
            gap_pct           = float(state.get("gap_pct", 0)),
            critique_score    = float(state.get("critique_score", 0)),
            critique_feedback = state.get("guardrail_feedback", "Escalated by guardrail"),
            strategie_summary = state.get("strategie", ""),
            actions           = state.get("strategie_actions", [])[:3],
            source            = "guardrail_escalate",
        )
        return {**state, "hitl_required": True, "hitl_review_id": review_id,
                "agents_invoked": list(state.get("agents_invoked", [])) + ["human_validation"]}
    except Exception as e:
        logger.warning("[Supervisor] human_validation submit failed: %s", e)
        return {**state, "hitl_required": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Node: save_memory
# ═══════════════════════════════════════════════════════════════════════════════

async def node_save_memory(state: Dict[str, Any]) -> Dict[str, Any]:
    """Persist cycle summary to PostgreSQL coach_interactions for future RAG."""
    try:
        import asyncpg, json as _json, os
        db_url = os.getenv("DATABASE_URL", "postgresql://postgres:root@localhost:5432/ooredoo_sales")
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO public.coach_interactions
                    (store_id, advisor_name, cycle_id, urgency_level,
                     recommendation, guardrail_status, created_at)
                VALUES ($1,$2,$3,$4,$5::jsonb,$6, NOW())
                ON CONFLICT DO NOTHING
            """,
            state.get("store_id", ""),
            state.get("advisor_id", ""),
            state.get("cycle_id", ""),
            state.get("urgency_level", ""),
            _json.dumps(state.get("coach_recommendation", {})),
            state.get("guardrail_status", "APPROVE"),
            )
        await pool.close()
    except Exception as e:
        logger.debug("[Supervisor] save_memory: %s", e)

    return {**state, "agents_invoked": list(state.get("agents_invoked", [])) + ["save_memory"]}


# ═══════════════════════════════════════════════════════════════════════════════
# Graph Factory
# ═══════════════════════════════════════════════════════════════════════════════

def build_supervisor_graph():
    """
    Build and compile the full SupervisorAgent LangGraph.

    Topology:
      START → initialize_state
            → parallel: [sales_branch, knowledge_branch, context_branch, inventory_branch]
            → merge_outputs → coach_agent → guardrail_agent
            ├─ approve   → notify_frontend → save_memory → END
            ├─ rewrite   → coach_agent  (one extra loop)
            ├─ escalate  → human_validation → notify_frontend → save_memory → END
            └─ block     → safe_fallback → notify_frontend → save_memory → END
    """
    from langgraph.graph import StateGraph, END, START
    from app.sales.core.retail_state import RetailState

    workflow = StateGraph(RetailState)

    # Register nodes
    workflow.add_node("initialize_state",    node_initialize_state)
    workflow.add_node("sales_branch",        node_sales_branch)
    workflow.add_node("knowledge_branch",    node_knowledge_branch)
    workflow.add_node("context_branch",      node_context_branch)
    workflow.add_node("inventory_branch",    node_inventory_branch)
    workflow.add_node("merge_outputs",       node_merge_outputs)
    workflow.add_node("coach_agent",         node_coach_agent)
    workflow.add_node("guardrail_agent",     node_guardrail)
    workflow.add_node("safe_fallback",       node_safe_fallback)
    workflow.add_node("human_validation",    node_human_validation)
    workflow.add_node("notify_frontend",     node_notify_frontend)
    workflow.add_node("save_memory",         node_save_memory)

    # Entry
    workflow.set_entry_point("initialize_state")

    # Parallel dispatch — LangGraph fan-out (each branch starts from initialize_state)
    workflow.add_edge("initialize_state", "sales_branch")
    workflow.add_edge("initialize_state", "knowledge_branch")
    workflow.add_edge("initialize_state", "context_branch")
    workflow.add_edge("initialize_state", "inventory_branch")

    # Fan-in → merge
    workflow.add_edge("sales_branch",     "merge_outputs")
    workflow.add_edge("knowledge_branch", "merge_outputs")
    workflow.add_edge("context_branch",   "merge_outputs")
    workflow.add_edge("inventory_branch", "merge_outputs")

    # Sequential pipeline
    workflow.add_edge("merge_outputs", "coach_agent")
    workflow.add_edge("coach_agent",   "guardrail_agent")

    # Guardrail routing
    workflow.add_conditional_edges(
        "guardrail_agent",
        route_after_guardrail,
        {
            "approve":  "notify_frontend",
            "rewrite":  "coach_agent",      # one extra pass
            "escalate": "human_validation",
            "block":    "safe_fallback",
        },
    )

    workflow.add_edge("safe_fallback",    "notify_frontend")
    workflow.add_edge("human_validation", "notify_frontend")
    workflow.add_edge("notify_frontend",  "save_memory")
    workflow.add_edge("save_memory",      END)

    return workflow.compile()


# ── Module-level compiled graph (singleton per process) ─────────────────────

_supervisor_graph = None
_supervisor_lock  = __import__("threading").Lock()


def get_supervisor_graph():
    global _supervisor_graph
    if _supervisor_graph is None:
        with _supervisor_lock:
            if _supervisor_graph is None:
                logger.info("[Supervisor] Compiling SupervisorAgent graph — once per process")
                _supervisor_graph = build_supervisor_graph()
                logger.info("[Supervisor] Graph compiled ✓")
    return _supervisor_graph
