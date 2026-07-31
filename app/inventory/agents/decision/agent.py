"""
Inventory Decision Agent
=========================
Folder: src/agents/decision/
Node:   decide

Receives the outputs of BOTH the analysis agent and context agent — which
run in parallel in the orchestrator — and produces a concrete recommendation:
  reorder / (no action for hold/monitor)

If one agent's output is missing or errored, the decision agent degrades
gracefully using whatever it has. A missing context report means uplift=0.
A missing analysis report means the agent cannot run and returns an error.

Writes to:
  inventory.recommendations  — one row per actionable decision (reorder only)
  inventory.agent_runs       — own row opened/closed around the graph run
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Annotated, Any, Dict, Optional, Sequence, TypedDict
import operator

from langchain_core.messages import BaseMessage
from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)

import sys
from pathlib import Path

from app.inventory.agents.decision.nodes import create_decide_node, create_constraints_check_node
from app.inventory.agents.decision.tools import (
    action_to_recommendation_type,
    confidence_to_float,
)
from app.inventory.utils.llm_factory import get_llm, get_smart_llm

try:
    from app.inventory.repositories.inventory_repo import SyncInventoryRepo
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    logger.warning("SyncInventoryRepo not importable — DB writes disabled.")


# ═══════════════════════════════════════════════════════════════════════════
# Agent State
# ═══════════════════════════════════════════════════════════════════════════

class DecisionAgentState(TypedDict):
    messages:                Annotated[Sequence[BaseMessage], operator.add]
    sku:                     str
    store_id:                str
    business_objective:      str
    baseline_report:         Dict[str, Any]   # analysis_report from analysis agent
    context_report:          Dict[str, Any]   # context_report from context agent (may be empty)
    adjusted_metrics:        Dict[str, Any]   # re-computed with uplift applied
    decision:                Dict[str, Any]   # populated by decide node or constraints_check
    constraints_violations:  list             # populated by constraints_check node


# ═══════════════════════════════════════════════════════════════════════════
# Agent Class
# ═══════════════════════════════════════════════════════════════════════════

class InventoryDecisionAgent:
    """
    Decision agent — combines analysis + context outputs into a recommendation.

    Single-node graph (decide). Adjusted metrics are computed before the
    graph runs so the node receives fully-prepared data and stays pure.

    Graceful degradation:
      - context_report missing / errored → demand_uplift_pct = 0 (baseline used)
      - analysis_report missing → cannot run, returns error dict

    Class-level compiled graph cache (same pattern as InventoryAnalysisAgent):
      Compiled once per process per use_llm mode, reused across all SKUs.
      LangGraph compiled graphs are stateless — sharing is safe.
    """

    _compiled_graphs: Dict[bool, Any] = {}
    _graph_lock = __import__("threading").Lock()

    def __init__(
        self,
        provider: str  = None,
        api_key:  str  = None,
        use_llm:  bool = True,
    ):
        self.use_llm = use_llm
        if use_llm:
            # SMART tier (OpenRouter) — décision critique, raisonnement fort requis
            # Fallback sur provider configuré si OpenRouter non disponible
            try:
                self.llm = get_smart_llm(api_key=api_key) if not provider else get_llm(provider=provider, api_key=api_key)
            except Exception:
                self.llm = get_llm(provider=provider, api_key=api_key)
        else:
            self.llm = None

        llm_class = self.llm.__class__.__name__ if self.llm else "None"
        logger.info(
            "[DecisionAgent] provider=%s | llm_class=%s | use_llm=%s",
            provider or "default", llm_class, use_llm,
        )

        if use_llm not in InventoryDecisionAgent._compiled_graphs:
            with InventoryDecisionAgent._graph_lock:
                if use_llm not in InventoryDecisionAgent._compiled_graphs:
                    logger.info(
                        "[DecisionAgent] Compiling graph (use_llm=%s) — once per process", use_llm
                    )
                    workflow = StateGraph(DecisionAgentState)
                    workflow.add_node("constraints_check", create_constraints_check_node())
                    workflow.add_node("decide", create_decide_node(self.llm, use_llm))
                    workflow.set_entry_point("constraints_check")
                    # Route: if constraints_check already set a decision (hard block) → END
                    #        otherwise → decide
                    workflow.add_conditional_edges(
                        "constraints_check",
                        lambda s: END if s.get("decision") else "decide",
                    )
                    workflow.add_edge("decide", END)
                    InventoryDecisionAgent._compiled_graphs[use_llm] = workflow.compile()
                    logger.info(
                        "[DecisionAgent] Graph compiled and cached (use_llm=%s)", use_llm
                    )

        self.graph = InventoryDecisionAgent._compiled_graphs[use_llm]

    def run(
        self,
        sku:                str,
        store_id:           str,
        business_objective: str,
        analysis_report:    Dict[str, Any],
        context_report:     Dict[str, Any],
        agent_run_id:       Optional[str] = None,
        lf_span=None,
    ) -> Dict[str, Any]:
        """
        Produce a recommendation for one SKU.

        Args:
            analysis_report:  full dict from InventoryAnalysisAgent.run()["analysis_report"]
                              Required — returns error if absent.
            context_report:   dict from InventoryContextAgent.run()["context_report"]
                              Optional — missing means uplift=0, not an error.
            agent_run_id:     orchestrator's batch run UUID (for FK linkage)

        Returns:
            {
                "sku", "store_id", "business_objective",
                "decision": {
                    "action":             "ORDER"|"HOLD"|"MONITOR"|"EXPEDITE",
                    "order_qty":          int | None,
                    "order_qty_rationale": str,
                    "urgency":            "immediate"|"this_week"|"this_month"|"none",
                    "decision_rationale": str,
                    "confidence":         "high"|"medium"|"low",
                    "trade_offs":         str,
                    "escalate_to_human":  bool,
                    "escalation_reason":  str | None,
                    "reasoning_source":   "llm"|"rule_based_fallback",
                },
                "adjusted_metrics": { metrics, risk_assessment, ... },
                "recommendation_id": str | None,  # inventory.recommendations UUID if written
            }
        """
        if not analysis_report:
            return {
                "sku": sku, "store_id": store_id,
                "business_objective": business_objective,
                "error": "analysis_report is required but was empty",
            }

        # ── Open own agent_run row ────────────────────────────────────────
        own_run_id: Optional[str] = None
        if _DB_AVAILABLE:
            try:
                own_run_id = SyncInventoryRepo.start_agent_run(
                    agent_name="decision_agent",
                    store_id=store_id,
                )
            except Exception as e:
                logger.warning("[DecisionAgent] could not open agent_run: %s", e)

        try:
            # Normalise context_report — empty or errored → uplift=0
            ctx = context_report if (context_report and "error" not in context_report) else {}

            adjusted_metrics = self._compute_adjusted_metrics(analysis_report, ctx)

            _callbacks = [lf_span.callback_handler] if (lf_span and lf_span.callback_handler) else []
            result = self.graph.invoke(
                {
                    "messages":               [],
                    "sku":                    sku,
                    "store_id":               store_id,
                    "business_objective":     business_objective,
                    "baseline_report":        analysis_report,
                    "context_report":         ctx,
                    "adjusted_metrics":       adjusted_metrics,
                    "decision":               {},
                    "constraints_violations": [],
                },
                config={"callbacks": _callbacks} if _callbacks else {},
            )

            decision = result["decision"]

            # Persist to inventory.recommendations (reorder actions only)
            recommendation_id = self._persist_recommendation(
                sku=sku,
                store_id=store_id,
                decision=decision,
                agent_run_id=agent_run_id or own_run_id,
                context_snapshot=self._build_context_snapshot(
                    sku=sku,
                    store_id=store_id,
                    business_objective=business_objective,
                    baseline_report=analysis_report,
                    context_report=ctx,
                    adjusted_metrics=adjusted_metrics,
                ),
            )

            # Auto-suggest on the purchase-order Kanban — the card appears the
            # moment the agent decides, before any human has approved anything.
            # Best-effort only: a failure here must never break the decision
            # pipeline, the recommendation above is already committed.
            if recommendation_id:
                self._suggest_purchase_order(recommendation_id=recommendation_id)

            final = {
                "sku":                    sku,
                "store_id":               store_id,
                "business_objective":     business_objective,
                "decision":               decision,
                "adjusted_metrics":       adjusted_metrics,
                "recommendation_id":      recommendation_id,
                "constraints_violations": result.get("constraints_violations", []),
            }

            # ── Close agent_run — success ─────────────────────────────────
            if _DB_AVAILABLE and own_run_id:
                recs_generated = 1 if recommendation_id else 0
                try:
                    SyncInventoryRepo.complete_agent_run(
                        run_id=own_run_id,
                        status="completed",
                        items_processed=1,
                        alerts_generated=0,
                        recommendations_generated=recs_generated,
                        error_message=None,
                    )
                except Exception as e:
                    logger.warning("[DecisionAgent] could not close agent_run: %s", e)

            return final

        except Exception as e:
            logger.error("DecisionAgent failed for SKU=%s: %s", sku, e, exc_info=True)

            if _DB_AVAILABLE and own_run_id:
                try:
                    SyncInventoryRepo.complete_agent_run(
                        run_id=own_run_id,
                        status="failed",
                        items_processed=0,
                        alerts_generated=0,
                        error_message=str(e),
                    )
                except Exception:
                    pass

            return {
                "sku": sku, "store_id": store_id,
                "business_objective": business_objective,
                "error": str(e),
            }

    # ── Adjusted metrics ──────────────────────────────────────────────────────

    def _compute_adjusted_metrics(
        self,
        analysis_report: Dict[str, Any],
        context_report:  Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Re-run compute_inventory_metrics() with demand_uplift_pct from
        the context agent applied to avg_daily_demand.

        Falls back to baseline metrics unchanged if import fails.
        """
        try:
            from app.inventory.agents.analysis.tools import compute_inventory_metrics

            stock   = analysis_report.get("stock", {})
            forecast= analysis_report.get("forecast", {})
            cons    = analysis_report.get("constraints", {})
            obj     = analysis_report.get("business_objective", "balanced")

            demand_uplift_pct = float(context_report.get("demand_uplift_pct", 0.0))

            return compute_inventory_metrics(
                stock_current         = float(stock.get("current_stock", 0)),
                stock_in_transit      = float(stock.get("stock_in_transit", 0)),
                stock_min             = stock.get("stock_min"),
                stock_max             = stock.get("stock_max"),
                lead_time_avg         = float(stock.get("lead_time_avg_days", 14)),
                lead_time_std         = float(stock.get("lead_time_std_days", 3)),
                moq                   = float(cons.get("moq", 1)),
                unit_cost             = float(stock.get("unit_cost", 1)),
                holding_cost_pct      = float(stock.get("holding_cost_pct", 0.25)),
                order_cost            = float(stock.get("order_cost", 50)),
                lifecycle_stage       = str(stock.get("lifecycle_stage", "mature")),
                service_level_target  = float(stock.get("service_level_target", 0.95)),
                avg_daily_demand      = float(forecast.get("avg_daily_demand", 0)),
                demand_std            = float(forecast.get("demand_std_dev", 0)),
                total_30d_demand      = float(forecast.get("total_30d_demand", 0)),
                trend_direction       = str(forecast.get("trend_direction", "stable")),
                business_objective    = obj,
                promo_uplift_pct      = demand_uplift_pct,
            )
        except Exception as e:
            logger.warning(
                "[DecisionAgent] _compute_adjusted_metrics failed (%s) — using baseline", e
            )
            return {
                "stock":           analysis_report.get("stock", {}),
                "forecast":        analysis_report.get("forecast", {}),
                "metrics":         analysis_report.get("metrics", {}),
                "risk_assessment": analysis_report.get("risk_assessment", {}),
                "constraints":     analysis_report.get("constraints", {}),
            }

    # ── Persistence ───────────────────────────────────────────────────────────

    @staticmethod
    def _build_context_snapshot(
        *,
        sku:                str,
        store_id:           str,
        business_objective: str,
        baseline_report:    Dict[str, Any],
        context_report:     Dict[str, Any],
        adjusted_metrics:   Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Fige, avec la recommandation, les données que l'agent avait sous les yeux.

        Sert un seul consommateur : le juge LLM qui note `ancrage` en direct
        (app/core/quality_service.py). Ce critère consiste à retrouver chaque
        chiffre du texte dans les données de la décision — sans ces rapports, le
        juge n'a rien à recouper et note bas des chiffres corrects.

        Les clés reprennent volontairement celles de `build_context_string()`
        dans evals/run_inventory_recommendations.py : c'est ce qui permet de
        comparer une note obtenue en production à une note du banc hors-ligne.
        Les renommer ici casse cette comparabilité sans rien signaler.
        """
        return {
            "sku":                sku,
            "store_id":           store_id,
            "business_objective": business_objective,
            "baseline_report":    baseline_report or {},
            "context_report":     context_report or {},
            "adjusted_metrics":   adjusted_metrics or {},
        }

    def _persist_recommendation(
        self,
        sku:              str,
        store_id:         str,
        decision:         Dict[str, Any],
        agent_run_id:     Optional[str],
        context_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Write to inventory.recommendations if the action is actionable (ORDER/EXPEDITE).
        HOLD and MONITOR produce no DB row — they are not recommendations.

        recommendation_text comes directly from the decision dict — it was written
        by the LLM or constructed by the rule-based fallback in nodes.py.
        Both paths produce situation-specific text.

        Returns the UUID string of the inserted row, or None.
        """
        if not _DB_AVAILABLE:
            return None

        action    = decision.get("action", "HOLD")
        reco_type = action_to_recommendation_type(action)

        if reco_type is None:
            logger.debug(
                "[DecisionAgent] action=%s — no recommendation written for %s@%s",
                action, sku, store_id,
            )
            return None

        try:
            rec_id = SyncInventoryRepo.save_recommendation(
                sku=sku,
                store_id=store_id,
                agent_run_id=agent_run_id,
                recommendation_type=reco_type,
                recommendation_text=decision.get("recommendation_text", ""),
                suggested_quantity=decision.get("order_qty"),
                confidence=confidence_to_float(decision.get("confidence", "low")),
                urgency=decision.get("urgency"),
                context_snapshot=context_snapshot,
            )

            if rec_id:
                logger.info(
                    "[DecisionAgent] recommendation saved: %s@%s action=%s qty=%s id=%s",
                    sku, store_id, action, decision.get("order_qty"), rec_id,
                )
            return rec_id

        except Exception as e:
            logger.warning(
                "[DecisionAgent] _persist_recommendation failed for %s@%s: %s",
                sku, store_id, e,
            )
            return None

    def _suggest_purchase_order(self, recommendation_id: str) -> None:
        """
        Auto-creates a SUGGERE purchase order from the recommendation just
        persisted, and pushes it to the Kanban over WebSocket in real time.
        Never raises — logged as a warning on failure.
        """
        try:
            from app.inventory.repositories.supply_repo import SyncPurchaseOrderRepo
            from app.inventory.services.po_ws_bus import broadcast_po_suggested_sync

            po = SyncPurchaseOrderRepo.create_suggestion_from_recommendation(
                recommendation_id=recommendation_id,
            )
            if po:
                broadcast_po_suggested_sync(po)
                logger.info(
                    "[DecisionAgent] purchase order suggested: po_id=%s recommendation_id=%s",
                    po.get("po_id"), recommendation_id,
                )
        except Exception as e:
            logger.warning(
                "[DecisionAgent] _suggest_purchase_order failed for recommendation %s: %s",
                recommendation_id, e,
            )


# ═══════════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════════

def create_decision_agent(
    provider: str  = None,
    api_key:  str  = None,
    use_llm:  bool = True,
) -> InventoryDecisionAgent:
    return InventoryDecisionAgent(provider=provider, api_key=api_key, use_llm=use_llm)
