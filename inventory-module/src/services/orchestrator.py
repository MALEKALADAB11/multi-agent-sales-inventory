"""
Inventory Analysis Orchestrator
================================
Drives the analysis pipeline and passes the structured baseline report
to downstream agents when they are available.

"""

import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from src.agents.analysis_agent import create_analysis_agent
from config.settings import settings


class InventoryOrchestrator:

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.groq_api_key
        self.analysis_agent = create_analysis_agent(api_key=self.api_key)

    def analyze_sku(
        self,
        sku: str,
        store_id: str = "STORE-001",
        business_objective: str = "balanced",
    ) -> Dict[str, Any]:
        print(f"\n{'='*70}")
        print(f"ANALYSIS PIPELINE  |  SKU: {sku}  |  Store: {store_id}")
        print(f"Objective: {business_objective}")
        print(f"{'='*70}")

        result = self.analysis_agent.run(
            sku=sku,
            store_id=store_id,
            business_objective=business_objective,
        )
        self._log_result(result)
        return result

    def _analyze_sku_safe(
        self,
        sku: str,
        store_id: str,
        business_objective: str,
    ) -> Dict[str, Any]:
        """
        Thread-safe wrapper for analyze_sku.
        Catches exceptions and returns error dict instead of raising.
        """
        try:
            return self.analyze_sku(sku, store_id, business_objective)
        except Exception as e:
            print(f"[ERROR] SKU {sku}: {e}")
            return {"sku": sku, "store_id": store_id, "error": str(e)}

    def analyze_batch(
        self,
        skus: List[str],
        store_id: str = "STORE-001",
        business_objective: str = "balanced",
        max_workers: int = 6,  # Process up to 6 SKUs in parallel
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
        print(f"{'='*70}\n")

        # Create a mapping to preserve input order
        results_dict = {}
        
        # Use ThreadPoolExecutor for parallel processing
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all SKUs for parallel processing
            future_to_sku = {
                executor.submit(
                    self._analyze_sku_safe,
                    sku,
                    store_id,
                    business_objective
                ): sku
                for sku in skus
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_sku):
                sku = future_to_sku[future]
                try:
                    result = future.result()
                    results_dict[sku] = result
                except Exception as e:
                    print(f"[ERROR] Unexpected failure for SKU {sku}: {e}")
                    results_dict[sku] = {
                        "sku": sku,
                        "store_id": store_id,
                        "error": f"Unexpected error: {str(e)}"
                    }
        
        # Return results in the same order as input SKUs
        return [results_dict[sku] for sku in skus]

    # ------------------------------------------------------------------
    # Read-only accessors for downstream agents
    # ------------------------------------------------------------------

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
        """
        max(EOQ, MOQ) — the mathematically derived quantity floor.
        This is NOT a recommendation. The Decision Agent reads this
        alongside Context Agent output to set the actual order quantity.
        """
        return (
            result.get("analysis_report", {})
                  .get("metrics", {})
                  .get("formula_order_qty")
        )

    # ------------------------------------------------------------------
    # Internal logging
    # ------------------------------------------------------------------

    @staticmethod
    def _log_result(result: Dict[str, Any]) -> None:
        if "error" in result:
            print(f"[ERROR] {result['error']}")
            return

        report      = result.get("analysis_report", {})
        risk        = report.get("risk_assessment", {})
        metrics     = report.get("metrics", {})
        constraints = report.get("constraints", {})
        stock       = report.get("stock_status", {})

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
        print(f"Objective note      : {report.get('objective_note', '')}\n")


def create_orchestrator(api_key: str = None) -> InventoryOrchestrator:
    return InventoryOrchestrator(api_key=api_key)