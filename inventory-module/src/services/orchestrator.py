"""
Inventory Analysis Orchestrator
================================

WHAT WAS SLOW AND WHY:

1. workflow.compile() called 330 times per batch run
   Each worker created 3 new agent instances (analysis/context/decision),
   each calling workflow.compile() in __init__. LangGraph compile builds
   the full execution graph, validates edges, sets up checkpointers.
   110 SKUs x 3 agents x ~3s compile = the entire 15 minutes.
   FIX: compiled graph is now a class-level singleton in agent.py —
   compiled once per process, reused for every SKU.

2. Shared agents were wrongly avoided
   We created per-worker instances to avoid LangGraph state bleed.
   LangGraph's compiled graph IS stateless — state flows through invoke(),
   not stored on the graph object. Sharing is safe. Per-worker creation
   was the bug, not the fix.

3. 330+ DB connections per run (fixed in routes.py + inventory_repo.py)
   get_active_objective / get_stock_level / get_product called per SKU.
   Now pre-fetched once before the pool starts.
"""

import sys
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

logger = logging.getLogger(__name__)

from src.agents.analysis.agent import create_analysis_agent
from src.agents.context.agent  import create_context_agent
from src.agents.decision.agent import create_decision_agent
from config.settings import settings

try:
    from db.repositories.inventory_repo import SyncInventoryRepo
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    logger.warning("SyncInventoryRepo not importable — agent_run logging disabled.")

_BATCH_WORKERS = 8


class InventoryOrchestrator:

    def __init__(self, provider: str = None, api_key: str = None, use_llm: bool = True):
        if api_key and not provider:
            provider = "groq"

        self.provider = provider or settings.llm_provider
        self.use_llm  = use_llm
        self.api_key  = api_key

        # Agents are shared across all calls — safe because the compiled
        # LangGraph graph is stateless (state passes through invoke()).
        self._analysis_agent = create_analysis_agent(provider=provider, api_key=api_key, use_llm=use_llm)
        self._context_agent  = create_context_agent(provider=provider,  api_key=api_key, use_llm=use_llm)
        self._decision_agent = create_decision_agent(provider=provider, api_key=api_key, use_llm=use_llm)

        logger.info(
            "[Orchestrator] provider=%s | use_llm=%s | llm_class=%s",
            self.provider, self.use_llm,
            self._analysis_agent.llm.__class__.__name__ if self._analysis_agent.llm else "None",
        )

    # ── Single SKU ────────────────────────────────────────────────────────────

    def analyze_sku(
        self,
        sku:                str,
        store_id:           str = "I63",
        business_objective: str = "balanced",
        agent_run_id:       Optional[str] = None,
    ) -> Dict[str, Any]:
        # DB is the sole source of truth for the active objective.
        # The caller's business_objective is only a fallback when DB is unavailable.
        resolved_objective   = business_objective
        service_level_target = 0.95
        if _DB_AVAILABLE:
            try:
                obj = SyncInventoryRepo.get_active_objective()
                if obj:
                    resolved_objective = (
                        obj.get("objective_type")
                        or obj.get("label")
                        or business_objective
                    )
                    meta = obj.get("metadata") or {}
                    if isinstance(meta, str):
                        import json
                        try:
                            meta = json.loads(meta)
                        except Exception:
                            meta = {}
                    service_level_target = float(
                        meta.get("service_level_target")
                        or obj.get("target_value")
                        or 0.95
                    )
                    logger.info(
                        "[Orchestrator] Single-SKU objective from DB: %s | SL target: %.2f",
                        resolved_objective, service_level_target,
                    )
            except Exception as exc:
                logger.warning("[Orchestrator] get_active_objective failed: %s", exc)

        return self._run_pipeline(
            sku, store_id, resolved_objective, agent_run_id,
            analysis_agent=self._analysis_agent,
            context_agent=self._context_agent,
            decision_agent=self._decision_agent,
            service_level_target=service_level_target,
        )

    # ── Batch ─────────────────────────────────────────────────────────────────

    def analyze_batch(
        self,
        skus:               List[str],
        store_id:           str = "I63",
        business_objective: str = "balanced",
        max_workers:        int = _BATCH_WORKERS,
    ) -> List[Dict[str, Any]]:
        if not skus:
            return []

        n_workers = min(max_workers, len(skus))
        logger.info(
            "[Orchestrator] Batch start — store=%s SKUs=%d objective=%s workers=%d",
            store_id, len(skus), business_objective, n_workers,
        )

        agent_run_id = None
        if _DB_AVAILABLE:
            try:
                agent_run_id = SyncInventoryRepo.start_agent_run(
                    agent_name="analysis_agent", store_id=store_id,
                )
                if agent_run_id:
                    logger.info("[Orchestrator] batch agent_run started: %s", agent_run_id)
            except Exception as exc:
                logger.warning("[Orchestrator] could not open batch agent_run: %s", exc)

        # ── 3 batch DB queries instead of 330 ────────────────────────────────
        # Previously fetch_node called get_active_objective + get_stock_level
        # + get_product for every SKU. Pre-fetch all three here once.

        resolved_objective    = business_objective
        service_level_target  = 0.95   # default — overridden from DB objective below
        if _DB_AVAILABLE:
            try:
                obj = SyncInventoryRepo.get_active_objective()
                if obj:
                    resolved_objective = (
                        obj.get("objective_type")
                        or obj.get("label")
                        or business_objective
                    )
                    # service_level_target lives in the metadata JSONB column,
                    # with target_value as a fallback (both columns populated by seeds).
                    meta = obj.get("metadata") or {}
                    if isinstance(meta, str):
                        import json
                        try:
                            meta = json.loads(meta)
                        except Exception:
                            meta = {}
                    service_level_target = float(
                        meta.get("service_level_target")
                        or obj.get("target_value")
                        or 0.95
                    )
                    logger.info(
                        "[Orchestrator] Active objective from DB: %s | SL target: %.2f",
                        resolved_objective, service_level_target,
                    )
            except Exception as exc:
                logger.warning("[Orchestrator] get_active_objective failed: %s", exc)

        preloaded_stock: Dict[str, Any] = {}
        if _DB_AVAILABLE:
            try:
                batch = SyncInventoryRepo.get_stock_levels_batch(skus, store_id)
                if batch:
                    preloaded_stock = batch
                    logger.info("[Orchestrator] Pre-fetched stock for %d/%d SKUs", len(batch), len(skus))
            except Exception as exc:
                logger.warning("[Orchestrator] get_stock_levels_batch failed: %s", exc)

        preloaded_products: Dict[str, Any] = {}
        if _DB_AVAILABLE:
            try:
                prod_batch = SyncInventoryRepo.get_products_batch(skus)
                if prod_batch:
                    # Inject service_level_target from the active objective into every
                    # product dict. The column was removed from inv.products (it belongs
                    # on the objective, not the product). The analysis agent's compute_node
                    # reads product.get("service_level_target", 0.95) — this keeps it working.
                    for product in prod_batch.values():
                        product["service_level_target"] = service_level_target
                    preloaded_products = prod_batch
                    logger.info(
                        "[Orchestrator] Pre-fetched products for %d/%d SKUs | SL=%.2f injected",
                        len(prod_batch), len(skus), service_level_target,
                    )
            except Exception as exc:
                logger.warning("[Orchestrator] get_products_batch failed: %s", exc)

        # ── Parallel workers using SHARED agents ──────────────────────────────
        # Safe: compiled LangGraph graph is stateless — invoke() takes all
        # per-SKU state as input, nothing is stored on the graph object.
        # DO NOT create new agent instances per worker — that triggers
        # workflow.compile() 330 times which is the entire 15-minute cost.
        analysis_agent = self._analysis_agent
        context_agent  = self._context_agent
        decision_agent = self._decision_agent

        def _worker(sku: str) -> Dict[str, Any]:
            try:
                return self._run_pipeline(
                    sku, store_id, resolved_objective, agent_run_id,
                    analysis_agent=analysis_agent,
                    context_agent=context_agent,
                    decision_agent=decision_agent,
                    preloaded_stock=preloaded_stock,
                    preloaded_products=preloaded_products,
    
                )
            except Exception as exc:
                logger.error("[Orchestrator] SKU %s worker error: %s", sku, exc)
                return {"sku": sku, "store_id": store_id, "error": str(exc)}

        results_dict: Dict[str, Dict] = {}
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            future_to_sku = {pool.submit(_worker, sku): sku for sku in skus}
            for future in as_completed(future_to_sku):
                sku = future_to_sku[future]
                try:
                    results_dict[sku] = future.result()
                except Exception as exc:
                    logger.error("[Orchestrator] future raised for SKU %s: %s", sku, exc)
                    results_dict[sku] = {"sku": sku, "store_id": store_id, "error": str(exc)}

        results  = [results_dict.get(s, {"sku": s, "error": "missing"}) for s in skus]
        critical = sum(1 for r in results if r.get("analysis_report", {}).get("risk_assessment", {}).get("level") == "CRITICAL")
        high     = sum(1 for r in results if r.get("analysis_report", {}).get("risk_assessment", {}).get("level") == "HIGH")
        errors   = sum(1 for r in results if "error" in r)

        logger.info(
            "[Orchestrator] Batch done — CRITICAL:%d HIGH:%d Errors:%d / %d SKUs",
            critical, high, errors, len(skus),
        )

        if _DB_AVAILABLE and agent_run_id:
            try:
                SyncInventoryRepo.complete_agent_run(
                    run_id=agent_run_id,
                    status="failed" if errors == len(skus) else "completed",
                    items_processed=len(skus) - errors,
                    alerts_generated=critical + high,
                    error_message=f"{errors} SKU(s) failed" if errors else None,
                )
                logger.info("[Orchestrator] batch agent_run closed: %s", agent_run_id)
            except Exception as exc:
                logger.warning("[Orchestrator] could not close batch agent_run: %s", exc)

        return results

    # ── Core pipeline ─────────────────────────────────────────────────────────

    def _run_pipeline(
        self,
        sku:                  str,
        store_id:             str,
        business_objective:   str,
        agent_run_id:         Optional[str],
        analysis_agent,
        context_agent,
        decision_agent,
        preloaded_stock:      Dict[str, Any] = None,
        preloaded_products:   Dict[str, Any] = None,
        service_level_target: float = 0.95,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "sku":                sku,
            "store_id":           store_id,
            "business_objective": business_objective,
        }

        # Step 1: Analysis
        analysis_report: Dict[str, Any] = {}
        try:
            raw = analysis_agent.run(
                sku, store_id, business_objective, agent_run_id,
                preloaded_stock=preloaded_stock,
                preloaded_product=preloaded_products.get(sku) if preloaded_products else None,

            )
            result["analysis_report"] = raw.get("analysis_report", {})
            analysis_report           = result["analysis_report"]
        except Exception as exc:
            logger.error("[Orchestrator] analysis_agent failed SKU=%s: %s", sku, exc)
            result["analysis_error"] = str(exc)

        if not analysis_report:
            result["decision_result"] = {
                "sku": sku, "store_id": store_id,
                "error": "analysis_report unavailable — pipeline aborted",
            }
            self._log_result(sku, result)
            return result

        # Step 2: Context
        context_report: Dict[str, Any] = {}
        try:
            ctx_raw                  = context_agent.run(sku, store_id, agent_run_id)
            result["context_result"] = ctx_raw
            context_report           = ctx_raw.get("context_report", {})
        except Exception as exc:
            logger.warning("[Orchestrator] context_agent failed SKU=%s: %s", sku, exc)
            result["context_result"] = {"sku": sku, "store_id": store_id, "error": str(exc)}

        # Step 3: Decision
        try:
            dec_raw                   = decision_agent.run(
                sku=sku, store_id=store_id, business_objective=business_objective,
                analysis_report=analysis_report, context_report=context_report,
                agent_run_id=agent_run_id,
            )
            result["decision_result"] = dec_raw
        except Exception as exc:
            logger.error("[Orchestrator] decision_agent failed SKU=%s: %s", sku, exc)
            result["decision_result"] = {"sku": sku, "store_id": store_id, "error": str(exc)}

        self._log_result(sku, result)
        return result

    # ── Accessors ─────────────────────────────────────────────────────────────

    def get_risk_level(self, result: Dict[str, Any]) -> Optional[str]:
        return result.get("analysis_report", {}).get("risk_assessment", {}).get("level")

    def get_decision_action(self, result: Dict[str, Any]) -> Optional[str]:
        return result.get("decision_result", {}).get("decision", {}).get("action")

    def get_days_of_stock(self, result: Dict[str, Any]) -> Optional[float]:
        return result.get("analysis_report", {}).get("metrics", {}).get("days_of_stock_remaining")

    # ── Logging ───────────────────────────────────────────────────────────────

    @staticmethod
    def _log_result(sku: str, result: Dict[str, Any]) -> None:
        if "error" in result and not result.get("analysis_report"):
            logger.warning("[SKU %s] pipeline error: %s", sku, result.get("error", result.get("analysis_error")))
            return
        report  = result.get("analysis_report", {})
        risk    = report.get("risk_assessment", {})
        metrics = report.get("metrics", {})
        logger.debug(
            "[SKU %s] risk=%s days=%.1f rop=%s formulaQty=%s",
            sku,
            risk.get("level", "N/A"),
            float(metrics.get("days_of_stock_remaining") or 0),
            metrics.get("reorder_point", "N/A"),
            metrics.get("formula_order_qty", "N/A"),
        )


def create_orchestrator(
    provider: str  = None,
    api_key:  str  = None,
    use_llm:  bool = True,
) -> InventoryOrchestrator:
    return InventoryOrchestrator(provider=provider, api_key=api_key, use_llm=use_llm)