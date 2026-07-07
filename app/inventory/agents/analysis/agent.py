"""
Inventory Analysis Agent
=========================
Modular architecture:
  Folder: src/agents/analysis/
  Nodes:  fetch → compute → reason
  DB-first with CSV fallback
  Two-layer risk classification
  LLM as evaluator (not narrator) — detects cross-dimensional conflicts
"""

import logging
from typing import Dict, Any, Optional, TypedDict, Annotated, Sequence
import operator

from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)

import sys
from pathlib import Path

from app.inventory.agents.analysis.nodes import fetch_node, compute_node, create_reason_node
from app.inventory.utils.llm_factory import get_llm, get_fast_llm
from app.core.config import DEFAULT_STORE_ID

try:
    from app.inventory.repositories.inventory_repo import SyncInventoryRepo
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    logger.warning("SyncInventoryRepo not importable — DB writes disabled.")


# ═══════════════════════════════════════════════════════════════════════════
# Agent State
# ═══════════════════════════════════════════════════════════════════════════

class AgentState(TypedDict):
    messages:           Annotated[Sequence[BaseMessage], operator.add]
    sku:                str
    store_id:           str
    business_objective: str
    fetch_data:         Dict[str, Any]
    computed_metrics:   Dict[str, Any]
    preloaded_stock:    Dict[str, Any]   # batch pre-fetch: {sku: stock_current}
    preloaded_product:  Dict[str, Any]   # batch pre-fetch: product row for this SKU


# ═══════════════════════════════════════════════════════════════════════════
# Agent Class
# ═══════════════════════════════════════════════════════════════════════════

class InventoryAnalysisAgent:
    """
    Analysis agent — computes baseline inventory metrics and risk assessment.

    The LangGraph graph is compiled ONCE at class level and reused across all
    instances. Previously, every worker in analyze_batch created a new instance
    which called workflow.compile() — LangGraph compile is expensive (validates
    graph, builds execution plan). With 110 SKUs × 3 agents = 330 compile()
    calls that alone accounts for most of the 15-minute load time.

    The compiled graph is stateless between runs — all per-SKU state is passed
    via the `invoke()` call, so sharing it across instances is safe.
    """

    # Class-level compiled graph cache: keyed by use_llm bool.
    # Built once on first instantiation, reused for all subsequent calls.
    _compiled_graphs: Dict[bool, Any] = {}
    _graph_lock = __import__("threading").Lock()

    _FORBIDDEN_KEYS = {"messages", "fetch_data"}

    def __init__(
        self,
        provider: str  = None,
        api_key:  str  = None,
        use_llm:  bool = True,
    ):
        self.use_llm = use_llm

        if use_llm:
            # FAST tier (OpenRouter) — analyse/évaluation de risque
            # Fallback sur le provider configuré si OpenRouter non disponible
            try:
                self.llm = get_fast_llm(api_key=api_key) if not provider else get_llm(provider=provider, api_key=api_key)
            except Exception:
                self.llm = get_llm(provider=provider, api_key=api_key)
        else:
            self.llm = None

        llm_class = self.llm.__class__.__name__ if self.llm else "None"
        logger.info(
            "[AnalysisAgent] provider=%s | llm_class=%s | use_llm=%s",
            provider or "default", llm_class, use_llm,
        )

        # Use cached compiled graph if already built for this use_llm mode.
        # Double-checked locking: fast path (no lock) for the common case.
        if use_llm not in InventoryAnalysisAgent._compiled_graphs:
            with InventoryAnalysisAgent._graph_lock:
                if use_llm not in InventoryAnalysisAgent._compiled_graphs:
                    logger.info("[AnalysisAgent] Compiling graph (use_llm=%s) — once per process", use_llm)
                    workflow = StateGraph(AgentState)
                    workflow.add_node("fetch",   fetch_node)
                    workflow.add_node("compute", compute_node)
                    workflow.add_node("reason",  create_reason_node(self.llm, use_llm))
                    workflow.set_entry_point("fetch")
                    workflow.add_edge("fetch",   "compute")
                    workflow.add_edge("compute", "reason")
                    workflow.add_edge("reason",  END)
                    InventoryAnalysisAgent._compiled_graphs[use_llm] = workflow.compile()
                    logger.info("[AnalysisAgent] Graph compiled and cached (use_llm=%s)", use_llm)

        self.graph = InventoryAnalysisAgent._compiled_graphs[use_llm]

    def run(
        self,
        sku:                str,
        store_id:           str = DEFAULT_STORE_ID,
        business_objective: str = "balanced",
        agent_run_id:       Optional[str] = None,
        preloaded_stock:    Optional[Dict[str, Any]] = None,
        preloaded_product:  Optional[Dict[str, Any]] = None,
        lf_span=None,
    ) -> Dict[str, Any]:
        """
        Run analysis for a single SKU.

        preloaded_stock:   {sku: stock_current} dict pre-fetched by orchestrator
                           batch run. fetch_node reads this instead of hitting DB.
        preloaded_product: product master row for this SKU, pre-fetched in batch.
        lf_span:           optional AgentSpan from InventoryPipelineTrace — when
                           provided, LangGraph nodes and LLM calls are auto-traced
                           in Langfuse via CallbackHandler.

        Returns:
            {
                "sku": str,
                "store_id": str,
                "business_objective": str,
                "analysis_report": { stock, forecast, metrics,
                                     risk_assessment, constraints,
                                     objective_note, analyst_flag,
                                     reasoning_source, report_type }
            }
        """
        try:
            _callbacks = [lf_span.callback_handler] if (lf_span and lf_span.callback_handler) else []
            result = self.graph.invoke(
                {
                    "messages":           [],
                    "sku":                sku,
                    "store_id":           store_id,
                    "business_objective": business_objective,
                    "fetch_data":         {},
                    "computed_metrics":   {},
                    "preloaded_stock":    preloaded_stock or {},
                    "preloaded_product":  preloaded_product or {},
                },
                config={"callbacks": _callbacks} if _callbacks else {},
            )

            metrics = result["computed_metrics"]

            for key in self._FORBIDDEN_KEYS:
                metrics.pop(key, None)

            effective_objective = result.get("business_objective", business_objective)

            metrics.update({
                "sku":               sku,
                "store_id":          store_id,
                "business_objective":effective_objective,
                "report_type":       "BASELINE",
            })

            final = {
                "sku":               sku,
                "store_id":          store_id,
                "business_objective":effective_objective,
                "analysis_report":   metrics,
            }

            self._persist_result(final, agent_run_id)
            return final

        except Exception as e:
            logger.error("AnalysisAgent failed for SKU=%s: %s", sku, e, exc_info=True)
            return {
                "sku":               sku,
                "store_id":          store_id,
                "business_objective":business_objective,
                "error":             str(e),
            }

    def _persist_result(
        self,
        result:       Dict[str, Any],
        agent_run_id: Optional[str],
    ) -> None:
        """
        Persist analysis results to DB.

        Updates:
          inventory.stock_levels → remaining_days_of_stock

        Note: Alert creation is handled by routes.py _build_alerts() to avoid
        duplicate write paths and ensure frontend has correct alert IDs.
        """
        if not _DB_AVAILABLE:
            return

        try:
            report   = result["analysis_report"]
            sku      = result["sku"]
            store_id = result["store_id"]

            # Update remaining_days_of_stock
            days_remaining = report["metrics"]["days_of_stock_remaining"]
            SyncInventoryRepo.upsert_stock_level_sync(
                sku=sku,
                store_id=store_id,
                stock_current=int(report["stock"]["current_stock"]),
                remaining_days_of_stock=days_remaining,
            )

        except Exception as e:
            logger.warning("Failed to persist analysis result for %s: %s", result.get("sku"), e)


# ═══════════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════════

def create_analysis_agent(
    provider: str  = None,
    api_key:  str  = None,
    use_llm:  bool = True,
) -> InventoryAnalysisAgent:
    """
    Factory function to create an InventoryAnalysisAgent.

    Args:
        provider: LLM provider ("groq", "ollama", "openai", "anthropic")
        api_key:  API key for cloud providers
        use_llm:  False for fast rule-based mode (no LLM call)
    """
    return InventoryAnalysisAgent(provider=provider, api_key=api_key, use_llm=use_llm)
