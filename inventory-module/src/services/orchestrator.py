import sys
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from src.agents.analysis_agent import create_analysis_agent

logger = logging.getLogger(__name__)


class InventoryOrchestrator:

    def __init__(self):
        self.analysis_agent = create_analysis_agent()

    def analyze_sku(
        self,
        sku: str,
        store_id: str = "STORE-001",
        business_objective: str = "balanced",
    ) -> Dict[str, Any]:
        # ── Log minimal — une ligne par SKU ──────────────────────────────
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
        max_workers: int = 6,
    ) -> List[Dict[str, Any]]:
        if not skus:
            return []

        # ── Log minimal pour le batch ─────────────────────────────────────
        logger.info("[INVENTORY] Batch %d SKUs | store=%s | obj=%s",
                    len(skus), store_id, business_objective)

        results_dict: Dict[str, Any] = {}

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

        # Résumé du batch
        results   = [results_dict[s] for s in skus]
        critical  = sum(1 for r in results
                        if r.get("analysis_report", {})
                            .get("risk_assessment", {})
                            .get("level") == "CRITICAL")
        high      = sum(1 for r in results
                        if r.get("analysis_report", {})
                            .get("risk_assessment", {})
                            .get("level") == "HIGH")
        errors    = sum(1 for r in results if "error" in r)

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

        report  = result.get("analysis_report", {})
        risk    = report.get("risk_assessment", {})
        metrics = report.get("metrics", {})

        level   = risk.get("level", "?")
        dos     = metrics.get("days_of_stock_remaining", 0)
        replen  = metrics.get("total_replenishment_cost", 0)

        # Afficher seulement les SKUs à risque ou overstock
        if level in ("CRITICAL", "HIGH"):
            logger.warning(
                "[SKU %s] ⚠️  %s | Stock: %.1fd | Replen: %.0f DT | %s",
                sku, level, dos, replen,
                risk.get("rationale", "")[:60]
            )
        else:
            logger.debug(
                "[SKU %s] %s | Stock: %.1fd", sku, level, dos
            )


def create_orchestrator() -> InventoryOrchestrator:
    return InventoryOrchestrator()