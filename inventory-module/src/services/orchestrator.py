"""
Inventory Analysis Orchestrator
================================
Drives the analysis pipeline and passes the structured baseline report
to downstream agents when they are available.

"""

import sys
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from src.agents.analysis_agent import create_analysis_agent, USE_LLM
from config.settings import settings

logger = logging.getLogger(__name__)  # FIX 3: logger was never defined


class InventoryOrchestrator:
    """
    Orchestrates inventory analysis across multiple SKUs with parallel processing.

    Supports multiple LLM providers through centralized configuration.
    """

    def __init__(self, provider: str = None, api_key: str = None, use_llm: bool = None):
        """
        Initialize the orchestrator with flexible LLM provider support.

        Args:
            provider: LLM provider ("groq", "ollama", "openai", "anthropic")
                     If None, uses LLM_PROVIDER from .env (defaults to "ollama")
            api_key: API key for cloud providers (groq, openai, anthropic)
                    If None, reads from .env (e.g., GROQ_API_KEY)

        Examples:
            # Use default provider from .env
            orchestrator = InventoryOrchestrator()

            # Use specific provider
            orchestrator = InventoryOrchestrator(provider="groq")

            # Override API key (legacy pattern, backward compatible)
            orchestrator = InventoryOrchestrator(api_key="gsk_...")

            # Disable LLM for fast live-broadcast mode
            orchestrator = InventoryOrchestrator(use_llm=False)
        """
        # Backward compatibility: if api_key passed without provider, assume Groq
        if api_key and not provider:
            provider = "groq"

        self.provider = provider or settings.llm_provider
        self.use_llm = use_llm
        self.analysis_agent = create_analysis_agent(provider=provider, api_key=api_key, use_llm=use_llm)

        print(
            f"[Orchestrator] provider={self.provider} | "
            f"use_llm={self.analysis_agent.use_llm} | "
            f"llm_class={self.analysis_agent.llm.__class__.__name__}"
        )

    def analyze_sku(
        self,
        sku: str,
        store_id: str = "STORE-001",
        business_objective: str = "balanced",
    ) -> Dict[str, Any]:
        result = self.analysis_agent.run(
            sku=sku,
            store_id=store_id,
            business_objective=business_objective,
        )
        self._log_result(sku, result)
        return result

    def _analyze_sku_safe(
        self,
        sku: str,
        store_id: str,
        business_objective: str,
    ) -> Dict[str, Any]:
        try:
            return self.analyze_sku(sku, store_id, business_objective)
        except Exception as e:
            logger.error("[SKU %s] Error: %s", sku, e)
            return {"sku": sku, "store_id": store_id, "error": str(e)}

    def analyze_batch(
        self,
        skus: List[str],
        store_id: str = "STORE-001",
        business_objective: str = "balanced",
        max_workers: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Analyze multiple SKUs IN PARALLEL using ThreadPoolExecutor.

        Performance improvement:
        - Before: Sequential loop → 6 SKUs × 10s = 60s
        - After:  Parallel execution → max(10s, 10s, ...) = ~10s

        Args:
            skus: List of SKU codes to analyze
            store_id: Store identifier
            business_objective: Business objective (cost|balanced|service_level|competitive)
            max_workers: Maximum parallel threads (default: 6)

        Returns:
            List of analysis results in the SAME ORDER as input SKUs
        """
        if not skus:
            return []

        print(f"\n{'='*70}")
        print(f"BATCH ANALYSIS  |  Store: {store_id}  |  SKUs: {len(skus)}")
        print(f"Processing {len(skus)} SKUs in parallel (max_workers={max_workers})")
        print(f"Provider: {self.provider} | use_llm={self.use_llm if self.use_llm is not None else USE_LLM}")
        print(f"{'='*70}\n")

        results_dict = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_sku = {
                executor.submit(
                    self._analyze_sku_safe,
                    sku, store_id, business_objective
                ): sku
                for sku in skus
            }
            for future in as_completed(future_to_sku):
                sku = future_to_sku[future]
                try:
                    results_dict[sku] = future.result()
                except Exception as e:
                    logger.error("[INVENTORY] SKU %s unexpected error: %s", sku, e)
                    results_dict[sku] = {
                        "sku":      sku,
                        "store_id": store_id,
                        "error":    str(e),
                    }

        # FIX 1: summary moved before return so it actually executes
        results  = [results_dict[s] for s in skus]
        critical = sum(1 for r in results
                       if r.get("analysis_report", {})
                           .get("risk_assessment", {})
                           .get("level") == "CRITICAL")
        high     = sum(1 for r in results
                       if r.get("analysis_report", {})
                           .get("risk_assessment", {})
                           .get("level") == "HIGH")
        errors   = sum(1 for r in results if "error" in r)

        logger.info(
            "[INVENTORY] ✅ Batch done — Critical: %d | High: %d | Errors: %d",
            critical, high, errors
        )

        return results

    # ── Read-only accessors ───────────────────────────────────────────────

    def get_risk_level(self, result: Dict[str, Any]) -> Optional[str]:
        return (
            result.get("analysis_report", {})
                  .get("risk_assessment", {})
                  .get("level")
        )

    def get_days_of_stock(self, result: Dict[str, Any]) -> Optional[float]:
        return (
            result.get("analysis_report", {})
                  .get("metrics", {})
                  .get("days_of_stock_remaining")
        )

    def get_formula_order_qty(self, result: Dict[str, Any]) -> Optional[float]:
        return (
            result.get("analysis_report", {})
                  .get("metrics", {})
                  .get("formula_order_qty")
        )

    # ── Log minimal par SKU ───────────────────────────────────────────────

    @staticmethod
    def _log_result(sku: str, result: Dict[str, Any]) -> None:
        if "error" in result:
            logger.warning("[SKU %s] ❌ Error: %s", sku, result["error"])
            return

        report      = result.get("analysis_report", {})
        risk        = report.get("risk_assessment", {})
        metrics     = report.get("metrics", {})
        stock       = report.get("stock", {})        # FIX 2a: was undefined
        constraints = report.get("constraints", {})  # FIX 2b: was undefined

        print(f"\nReport type         : {report.get('report_type', 'N/A')}")
        print(f"Lifecycle stage     : {stock.get('lifecycle_stage', 'N/A')}")
        print(f"Risk level          : {risk.get('level', 'N/A')}")
        print(f"Overstock flag      : {risk.get('overstock_flag', False)}")
        print(f"Days of stock       : {metrics.get('days_of_stock_remaining', 'N/A')}")
        print(f"Reorder point       : {metrics.get('reorder_point', 'N/A')}")
        print(f"EOQ                 : {metrics.get('eoq', 'N/A')}")
        print(f"Formula order qty   : {metrics.get('formula_order_qty', 'N/A')}  (max(EOQ, MOQ) — not a decision)")
        print(f"Replenishment cost  : {metrics.get('total_replenishment_cost', 'N/A')} DT")
        print(f"MOQ binding         : {constraints.get('moq_is_binding', False)}")
        print(f"High cost flag      : {constraints.get('high_cost_flag', False)}")
        print(f"Rationale           : {risk.get('rationale', '')}")
        print(f"Objective note      : {report.get('objective_note', '')}")
        print(f"Reasoning source    : {report.get('reasoning_source', 'unknown')}\n")


# ══════════════════════════════════════════════════════════════════════════════
# Factory function for backward compatibility
# ══════════════════════════════════════════════════════════════════════════════

def create_orchestrator(provider: str = None, api_key: str = None, use_llm: bool = None) -> InventoryOrchestrator:
    """
    Factory function to create an InventoryOrchestrator.

    Args:
        provider: LLM provider ("groq", "ollama", "openai", "anthropic")
                 If None, uses LLM_PROVIDER from .env
        api_key: API key for cloud providers. If None, reads from .env

    Returns:
        Configured InventoryOrchestrator instance

    Examples:
        # Use default from .env
        orchestrator = create_orchestrator()

        # Use Groq
        orchestrator = create_orchestrator(provider="groq")

        # Use Groq with explicit API key (legacy pattern)
        orchestrator = create_orchestrator(api_key="gsk_...")
    """
    return InventoryOrchestrator(provider=provider, api_key=api_key, use_llm=use_llm)