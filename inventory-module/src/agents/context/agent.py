"""
Inventory Context Agent
========================
Modular architecture:
  Folder: src/agents/context/
  Nodes:  fetch_signals → interpret
  Two jobs:
    1. Learn from history what impact past events/promotions had on this
       product category (real observed uplifts from inventory.sales_history PG)
    2. Assess the current moment through that historical lens and produce
       a calibrated demand_uplift_pct for the next 7 days

Output feeds directly into the decision agent — the demand_uplift_pct
is applied to baseline metrics to produce adjusted EOQ, safety stock,
and reorder point.

Persists to:
  inventory.context_adjustments  — timestamped audit trail (monitoring reads this)
  inventory.agent_runs           — agent health tracking
"""

import logging
from datetime import date, timedelta
from typing import Annotated, Any, Dict, Optional, Sequence, TypedDict
import operator

from langchain_core.messages import BaseMessage
from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[4]))

from src.agents.context.nodes import fetch_signals_node, create_interpret_node
from src.utils.llm_factory import get_llm, get_fast_llm

try:
    from db.repositories.inventory_repo import SyncInventoryRepo
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    logger.warning("SyncInventoryRepo not importable — DB writes disabled.")


# Confidence string → float for DB storage
_CONFIDENCE_MAP = {"high": 0.9, "medium": 0.6, "low": 0.3}


# ═══════════════════════════════════════════════════════════════════════════
# Agent State
# ═══════════════════════════════════════════════════════════════════════════

class ContextAgentState(TypedDict):
    messages:       Annotated[Sequence[BaseMessage], operator.add]
    sku:            str
    store_id:       str
    signals:        Dict[str, Any]    # populated by fetch_signals_node
    context_report: Dict[str, Any]    # populated by interpret_node


# ═══════════════════════════════════════════════════════════════════════════
# Agent Class
# ═══════════════════════════════════════════════════════════════════════════

class InventoryContextAgent:
    """
    Context agent — interprets external demand signals into a calibrated
    demand uplift percentage.

    Two-node workflow:
      1. fetch_signals: gather historical patterns, live promotions, weather,
                        holidays, and DB events (pure Python, no LLM)
      2. interpret:     LLM synthesises all signals into demand_uplift_pct,
                        interpretation, confidence, and dominant_signal.
                        Rule-based fallback produces the same structure.

    Output dict is passed directly to the decision agent in memory.
    The DB write (inventory.context_adjustments) is for the monitoring module only.

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
            # FAST tier (OpenRouter) — interprétation de signaux de contexte
            try:
                self.llm = get_fast_llm(api_key=api_key) if not provider else get_llm(provider=provider, api_key=api_key)
            except Exception:
                self.llm = get_llm(provider=provider, api_key=api_key)
        else:
            self.llm = None

        llm_class = self.llm.__class__.__name__ if self.llm else "None"
        logger.info(
            "[ContextAgent] provider=%s | llm_class=%s | use_llm=%s",
            provider or "default", llm_class, use_llm,
        )

        if use_llm not in InventoryContextAgent._compiled_graphs:
            with InventoryContextAgent._graph_lock:
                if use_llm not in InventoryContextAgent._compiled_graphs:
                    logger.info(
                        "[ContextAgent] Compiling graph (use_llm=%s) — once per process", use_llm
                    )
                    workflow = StateGraph(ContextAgentState)
                    workflow.add_node("fetch_signals", fetch_signals_node)
                    workflow.add_node("interpret",     create_interpret_node(self.llm, use_llm))
                    workflow.set_entry_point("fetch_signals")
                    workflow.add_edge("fetch_signals", "interpret")
                    workflow.add_edge("interpret",     END)
                    InventoryContextAgent._compiled_graphs[use_llm] = workflow.compile()
                    logger.info(
                        "[ContextAgent] Graph compiled and cached (use_llm=%s)", use_llm
                    )

        self.graph = InventoryContextAgent._compiled_graphs[use_llm]

    def run(
        self,
        sku:          str,
        store_id:     str = "I63",
        agent_run_id: Optional[str] = None,
        lf_span=None,
    ) -> Dict[str, Any]:
        """
        Run context analysis for a single SKU.

        Returns:
            {
                "sku":            str,
                "store_id":       str,
                "context_report": {
                    "demand_uplift_pct": float,
                    "interpretation":   str,
                    "confidence":       str,
                    "dominant_signal":  str,
                    "reasoning_source": str,
                    "signals": {
                        "weather":             {...},
                        "promotions":          [...],
                        "holidays":            [...],
                        "events":              [...],
                        "historical_patterns": {...},
                    }
                }
            }

        On failure, returns dict with "error" key instead of context_report.
        Decision agent handles this by using demand_uplift_pct=0.
        """
        # ── Open agent_run row ────────────────────────────────────────────
        # Always open our own row so every agent execution is visible in
        # monitoring, whether called standalone or from the orchestrator.
        own_run_id: Optional[str] = None
        if _DB_AVAILABLE:
            try:
                own_run_id = SyncInventoryRepo.start_agent_run(
                    agent_name="context_agent",
                    store_id=store_id,
                )
                logger.debug("[ContextAgent] agent_run started: %s", own_run_id)
            except Exception as e:
                logger.warning("[ContextAgent] could not open agent_run: %s", e)

        # agent_run_id from orchestrator is kept for context_adjustments linkage
        # own_run_id is used for this agent's agent_runs row
        effective_run_id = own_run_id or agent_run_id

        try:
            _callbacks = [lf_span.callback_handler] if (lf_span and lf_span.callback_handler) else []
            result = self.graph.invoke(
                {
                    "messages":       [],
                    "sku":            sku,
                    "store_id":       store_id,
                    "signals":        {},
                    "context_report": {},
                },
                config={"callbacks": _callbacks} if _callbacks else {},
            )

            context_report = result["context_report"]
            signals        = context_report.pop("signals", {})

            # Reshape signals into the documented output structure
            context_report["signals"] = {
                "weather":             signals.get("weather", {}),
                "promotions":          signals.get("promotions", []),
                "holidays":            signals.get("holidays", []),
                "events":              signals.get("events", []),
                "historical_patterns": signals.get("historical_patterns", {}),
            }

            final = {
                "sku":            sku,
                "store_id":       store_id,
                "context_report": context_report,
            }

            self._persist_result(final, effective_run_id)

            # ── Close agent_run row — success ─────────────────────────────
            if _DB_AVAILABLE and own_run_id:
                try:
                    SyncInventoryRepo.complete_agent_run(
                        run_id           = own_run_id,
                        status           = "completed",
                        items_processed  = 1,
                        alerts_generated = 0,
                        error_message    = None,
                    )
                    logger.debug("[ContextAgent] agent_run completed: %s", own_run_id)
                except Exception as e:
                    logger.warning("[ContextAgent] could not close agent_run: %s", e)

            return final

        except Exception as e:
            logger.error(
                "ContextAgent failed for SKU=%s store=%s: %s", sku, store_id, e, exc_info=True
            )

            # ── Close agent_run row — failure ─────────────────────────────
            if _DB_AVAILABLE and own_run_id:
                try:
                    SyncInventoryRepo.complete_agent_run(
                        run_id           = own_run_id,
                        status           = "failed",
                        items_processed  = 0,
                        alerts_generated = 0,
                        error_message    = str(e),
                    )
                except Exception as ce:
                    logger.warning("[ContextAgent] could not close agent_run on failure: %s", ce)

            return {
                "sku":      sku,
                "store_id": store_id,
                "error":    str(e),
            }

    def _persist_result(
        self,
        result:       Dict[str, Any],
        agent_run_id: Optional[str],
    ) -> None:
        """
        Persist context result to inventory.context_adjustments.

        This write is for the monitoring module's audit trail.
        The orchestrator does NOT re-read this row — it uses the in-memory dict.
        """
        if not _DB_AVAILABLE:
            return

        try:
            report   = result["context_report"]
            sku      = result["sku"]
            store_id = result["store_id"]

            confidence_float = _CONFIDENCE_MAP.get(
                str(report.get("confidence", "low")).lower(), 0.3
            )

            saved = SyncInventoryRepo.save_context_adjustment(
                sku=sku,
                store_id=store_id,
                valid_from=date.today(),
                valid_to=date.today() + timedelta(days=7),
                demand_uplift_pct=float(report.get("demand_uplift_pct", 0.0)),
                dominant_signal=str(report.get("dominant_signal", "none")),
                confidence=confidence_float,
                interpretation=str(report.get("interpretation", "")),
                agent_run_id=agent_run_id,
            )

            if saved:
                logger.debug(
                    "[PERSIST] context_adjustment saved: %s@%s uplift=%.1f%%",
                    sku, store_id, report.get("demand_uplift_pct", 0.0),
                )
            else:
                logger.warning(
                    "[PERSIST] context_adjustment save returned False: %s@%s",
                    sku, store_id,
                )

        except Exception as e:
            logger.warning(
                "Failed to persist context result for %s: %s", result.get("sku"), e
            )


# ═══════════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════════

def create_context_agent(
    provider: str  = None,
    api_key:  str  = None,
    use_llm:  bool = True,
) -> InventoryContextAgent:
    """
    Factory function to create an InventoryContextAgent.

    Args:
        provider: LLM provider ("groq", "ollama", "openai", "anthropic")
        api_key:  API key for cloud providers
        use_llm:  False for fast rule-based mode (no LLM call)
    """
    return InventoryContextAgent(provider=provider, api_key=api_key, use_llm=use_llm)
