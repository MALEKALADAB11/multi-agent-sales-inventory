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

4. Analysis + Context ran sequentially within each SKU pipeline.
   These two agents are independent — neither needs the other's output.
   FIX: _run_pipeline now runs analysis_agent and context_agent in
   parallel using a 2-thread inner pool, then feeds both results to
   decision_agent. This cuts per-SKU wall time roughly in half for
   LLM-enabled runs.

5. Redis Alert Bus: CRITICAL/EXPEDITE decisions now dispatched async
   via dispatch_alerts_sync() without blocking the pipeline.

6. "cannot schedule new futures after interpreter shutdown" on every SKU
   The startup pre-warm runs a multi-minute batch in a background thread.
   Stopping uvicorn mid-run put CPython into its exit sequence, which flips
   concurrent.futures' global shutdown flag — every per-SKU nested pool then
   failed to submit at once, one ERROR per SKU.
   FIX: the pipeline is now shutdown-aware (app/core/shutdown.py). Workers
   stop pulling new SKUs once shutdown starts, and phase 1 offloads only the
   context agent (1 thread instead of 2), falling back to a plain sequential
   run when no thread can be scheduled.
"""

import asyncio
import sys
import logging
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
    as_completed,
    Future,
)

from app.core.shutdown import is_shutting_down, is_shutdown_error


logger = logging.getLogger(__name__)

from app.inventory.agents.analysis.agent import create_analysis_agent
from app.inventory.agents.context.agent  import create_context_agent
from app.inventory.agents.decision.agent import create_decision_agent
from app.inventory.config.settings import settings
from app.core.config import DEFAULT_STORE_ID


# ── Passerelle sync -> async ──────────────────────────────────────────────────
#
# Ce module est entièrement synchrone et tourne dans des threads worker, mais
# doit appeler des coroutines (decision_agent.run await le pool asyncpg pour
# chercher les produits complémentaires).
#
# Surtout PAS un asyncio.run() par appel : app.core.db indexe le pool asyncpg
# par boucle d'événements dans une WeakKeyDictionary. Chaque boucle éphémère
# ouvre donc un pool neuf (min_size=2) que personne ne ferme quand la boucle
# meurt — avec 80 SKUs par run, PostgreSQL était saturé en quelques minutes
# ("sorry, too many clients already"), y compris les slots superutilisateur.
#
# Une boucle unique et persistante, dans un thread dédié, garantit exactement
# UN pool asyncpg réutilisé par tous les appels.
_loop_lock = threading.Lock()
_bg_loop: "asyncio.AbstractEventLoop | None" = None


def _get_bg_loop() -> "asyncio.AbstractEventLoop":
    global _bg_loop
    with _loop_lock:
        if _bg_loop is not None and not _bg_loop.is_closed():
            return _bg_loop
        loop = asyncio.new_event_loop()
        threading.Thread(
            target=loop.run_forever,
            name="inventory-async",
            daemon=True,
        ).start()
        _bg_loop = loop
        return loop


# Budget d'un SKU côté décision. Il doit couvrir le pire cas de la chaîne de
# secours LLM — un essai par provider (openrouter → groq → ollama), chacun
# borné par settings.llm_request_timeout_s — plus les écritures DB (recommanda-
# tion + suggestion de bon de commande). Une valeur inférieure re-crée le bug
# d'origine : le SKU expire ici alors que la chaîne travaille encore.
_LLM_CHAIN_LENGTH = 3
_DECISION_TIMEOUT_S = max(120.0, settings.llm_request_timeout_s * _LLM_CHAIN_LENGTH + 30.0)


def _await_sync(coro, timeout: float = _DECISION_TIMEOUT_S):
    """Exécute une coroutine sur la boucle de fond partagée et rend son résultat."""
    future = asyncio.run_coroutine_threadsafe(coro, _get_bg_loop())
    try:
        return future.result(timeout)
    except FutureTimeoutError:
        # `.result(timeout)` rend la main SANS arrêter la coroutine. Laissée
        # telle quelle, elle continue d'occuper la boucle de fond partagée (et,
        # si elle en est encore à la requête asyncpg des produits complémen-
        # taires, une connexion du pool) pour un SKU déjà déclaré en échec.
        # Le cancel l'interrompt au prochain point d'await ; l'étape bloquante
        # déjà partie dans un thread, elle, est bornée par le timeout HTTP du
        # client LLM (settings.llm_request_timeout_s) — rien d'autre ne peut
        # la libérer.
        future.cancel()
        raise


try:
    from app.inventory.utils.langfuse_inventory import (
        InventoryPipelineTrace,
        log_batch_start,
        log_batch_end,
    )
    _LF_AVAILABLE = True
except Exception:
    _LF_AVAILABLE = False
    logger.debug("langfuse_inventory not available — Langfuse tracing disabled.")

try:
    from app.inventory.repositories.inventory_repo import SyncInventoryRepo
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False
    logger.warning("SyncInventoryRepo not importable — agent_run logging disabled.")

try:
    from app.inventory.services.redis_alert_bus import dispatch_alerts_sync
    _REDIS_ALERTS = True
except Exception:
    _REDIS_ALERTS = False
    logger.debug("redis_alert_bus not available — critical alerts not dispatched.")

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
        store_id:           str = DEFAULT_STORE_ID,
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
        store_id:           str = DEFAULT_STORE_ID,
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

        import time as _time
        _batch_t0 = _time.time()
        batch_id = log_batch_start(store_id, len(skus), business_objective) if _LF_AVAILABLE else f"batch-{store_id}"

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
                    # product dict. The column was removed from inventory.products (it belongs
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
            # Le processus s'arrête : ne pas entamer un nouveau SKU. Sans ce
            # garde-fou, chaque worker allait au bout et échouait sur son pool
            # imbriqué ("cannot schedule new futures after interpreter shutdown").
            if is_shutting_down():
                return {"sku": sku, "store_id": store_id, "skipped": "shutdown"}
            try:
                return self._run_pipeline(
                    sku, store_id, resolved_objective, agent_run_id,
                    analysis_agent=analysis_agent,
                    context_agent=context_agent,
                    decision_agent=decision_agent,
                    preloaded_stock=preloaded_stock,
                    preloaded_products=preloaded_products,
                    batch_id=batch_id,
                )
            except Exception as exc:
                if is_shutdown_error(exc) or is_shutting_down():
                    logger.info("[Orchestrator] SKU %s abandonné (arrêt en cours)", sku)
                    return {"sku": sku, "store_id": store_id, "skipped": "shutdown"}
                logger.error("[Orchestrator] SKU %s worker error: %s", sku, exc)
                return {"sku": sku, "store_id": store_id, "error": str(exc)}

        results_dict: Dict[str, Dict] = {}
        aborted = False
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            future_to_sku: Dict[Future, str] = {}
            for sku in skus:
                try:
                    future_to_sku[pool.submit(_worker, sku)] = sku
                except RuntimeError as exc:
                    if not is_shutdown_error(exc):
                        raise
                    aborted = True
                    break
            for future in as_completed(future_to_sku):
                sku = future_to_sku[future]
                try:
                    results_dict[sku] = future.result()
                except Exception as exc:
                    logger.error("[Orchestrator] future raised for SKU %s: %s", sku, exc)
                    results_dict[sku] = {"sku": sku, "store_id": store_id, "error": str(exc)}

        interrupted = aborted or is_shutting_down()
        if interrupted:
            logger.warning(
                "[Orchestrator] Batch interrompu par l'arrêt du processus — %d/%d SKUs traités",
                len(results_dict), len(skus),
            )

        # Un SKU non soumis pendant un arrêt n'est pas une erreur métier :
        # il est marqué `skipped` pour ne pas faire passer le run en "failed".
        _missing = {"skipped": "shutdown"} if interrupted else {"error": "missing"}
        results  = [
            results_dict.get(s, {"sku": s, "store_id": store_id, **_missing}) for s in skus
        ]
        critical = sum(1 for r in results if r.get("analysis_report", {}).get("risk_assessment", {}).get("level") == "CRITICAL")
        high     = sum(1 for r in results if r.get("analysis_report", {}).get("risk_assessment", {}).get("level") == "HIGH")
        errors   = sum(1 for r in results if "error" in r)
        skipped  = sum(1 for r in results if r.get("skipped"))

        _batch_ms = int((_time.time() - _batch_t0) * 1000)
        logger.info(
            "[Orchestrator] Batch done — CRITICAL:%d HIGH:%d Errors:%d Skipped:%d / %d SKUs (%dms)",
            critical, high, errors, skipped, len(skus), _batch_ms,
        )
        if _LF_AVAILABLE:
            log_batch_end(batch_id, store_id, critical, high, errors, _batch_ms)

        if _DB_AVAILABLE and agent_run_id:
            try:
                _msgs = []
                if errors:
                    _msgs.append(f"{errors} SKU(s) failed")
                if skipped:
                    _msgs.append(f"{skipped} SKU(s) skipped (shutdown)")
                SyncInventoryRepo.complete_agent_run(
                    run_id=agent_run_id,
                    status="failed" if errors == len(skus) else "completed",
                    items_processed=len(skus) - errors - skipped,
                    alerts_generated=critical + high,
                    error_message="; ".join(_msgs) or None,
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
        batch_id:             str = None,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "sku":                sku,
            "store_id":           store_id,
            "business_objective": business_objective,
        }

        # ── Langfuse root trace for this SKU ──────────────────────────────────
        lf_trace = None
        if _LF_AVAILABLE:
            lf_trace = InventoryPipelineTrace(sku, store_id, business_objective, batch_id)
            lf_trace.start()

        # ── Phase 1: Analysis + Context en PARALLÈLE ──────────────────────────
        # Ces deux agents sont indépendants — aucun n'a besoin de la sortie
        # de l'autre. Les exécuter en parallèle coupe le temps par SKU ~2×
        # pour les runs LLM-enabled.
        #
        # Seul le contexte est déporté sur un thread : l'analyse tourne dans le
        # thread courant, qui sinon attendrait les bras croisés. Même
        # parallélisme, moitié moins de threads (1 au lieu de 2 par SKU), et
        # une seule soumission susceptible d'échouer à l'arrêt du processus.

        analysis_span = lf_trace.agent_span(
            "analysis",
            input_data={"sku": sku, "store_id": store_id, "objective": business_objective},
        ) if lf_trace else None
        context_span = lf_trace.agent_span(
            "context",
            input_data={"sku": sku, "store_id": store_id},
        ) if lf_trace else None

        analysis_report: Dict[str, Any] = {}
        context_report:  Dict[str, Any] = {}

        def _run_context():
            return context_agent.run(sku, store_id, agent_run_id, lf_span=context_span)

        # Pendant l'arrêt de l'interpréteur, submit() lève RuntimeError : on
        # bascule alors sur une exécution séquentielle plutôt que de perdre le SKU.
        inner_pool: Optional[ThreadPoolExecutor] = None
        context_future: Optional[Future] = None
        if not is_shutting_down():
            try:
                inner_pool     = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inv-ctx")
                context_future = inner_pool.submit(_run_context)
            except RuntimeError as exc:
                if not is_shutdown_error(exc):
                    raise
                logger.debug("[Orchestrator] SKU=%s contexte en séquentiel (arrêt en cours)", sku)
                context_future = None

        try:
            # Analyse dans le thread courant, en parallèle du contexte.
            try:
                raw                       = analysis_agent.run(
                    sku, store_id, business_objective, agent_run_id,
                    preloaded_stock=preloaded_stock,
                    preloaded_product=(
                        preloaded_products.get(sku) if preloaded_products else None
                    ),
                    lf_span=analysis_span,
                )
                result["analysis_report"] = raw.get("analysis_report", {})
                analysis_report           = result["analysis_report"]
                if analysis_span:
                    risk = analysis_report.get("risk_assessment", {})
                    analysis_span.end(output={
                        "risk_level":       risk.get("level"),
                        "days_of_stock":    analysis_report.get("metrics", {}).get("days_of_stock_remaining"),
                        "reasoning_source": analysis_report.get("reasoning_source"),
                    })
            except Exception as exc:
                logger.error("[Orchestrator] analysis_agent failed SKU=%s: %s", sku, exc)
                result["analysis_error"] = str(exc)
                if analysis_span:
                    analysis_span.end(error=str(exc))

            # Collect context result (non-blocking for decision pipeline)
            try:
                ctx_raw                  = context_future.result() if context_future else _run_context()
                result["context_result"] = ctx_raw
                context_report           = ctx_raw.get("context_report", {})
                if context_span:
                    context_span.end(output={
                        "demand_uplift_pct":  context_report.get("demand_uplift_pct"),
                        "dominant_signal":    context_report.get("dominant_signal"),
                        "confidence":         context_report.get("confidence"),
                        # v2 fields — the double-counting guard (forecast_source
                        # aware) runs later in decision_span, since this agent
                        # runs in parallel with analysis and never sees
                        # forecast_source. See decision/agent.py.
                        "impact_window_days": context_report.get("impact_window_days"),
                        "context_volatility":  context_report.get("context_volatility"),
                    })
            except Exception as exc:
                logger.warning("[Orchestrator] context_agent failed SKU=%s: %s", sku, exc)
                result["context_result"] = {"sku": sku, "store_id": store_id, "error": str(exc)}
                if context_span:
                    context_span.end(error=str(exc))
        finally:
            if inner_pool is not None:
                inner_pool.shutdown(wait=False)

        if not analysis_report:
            result["decision_result"] = {
                "sku": sku, "store_id": store_id,
                "error": "analysis_report unavailable — pipeline aborted",
            }
            if lf_trace:
                lf_trace.finish(error="analysis_report unavailable")
            self._log_result(sku, result)
            return result

        # ── Phase 2: Decision (séquentiel — dépend de analysis + context) ─────
        decision_span = lf_trace.agent_span(
            "decision",
            input_data={
                "sku": sku,
                "risk_level": analysis_report.get("risk_assessment", {}).get("level"),
                "demand_uplift_pct": context_report.get("demand_uplift_pct"),
                # forecast_source only becomes known here (Phase 2, sequential) —
                # the decision agent is where the demand_sensing double-counting
                # guard actually applies it, see decision/agent.py.
                "forecast_source": analysis_report.get("forecast_source"),
            },
        ) if lf_trace else None
        try:
            # decision_agent.run est une coroutine depuis l'ajout de la
            # recherche de produits complémentaires (elle await le pool
            # asyncpg). _run_pipeline, lui, est synchrone et tourne dans un
            # thread worker : sans _await_sync, on récupérait l'objet coroutine
            # au lieu du résultat — d'où « 'coroutine' object has no attribute
            # 'get' » sur chaque SKU, et zéro recommandation produite.
            dec_raw                   = _await_sync(decision_agent.run(
                sku=sku, store_id=store_id, business_objective=business_objective,
                analysis_report=analysis_report, context_report=context_report,
                agent_run_id=agent_run_id,
                lf_span=decision_span,
            ))
            result["decision_result"] = dec_raw
            if decision_span:
                decision = dec_raw.get("decision", {})
                decision_span.end(output={
                    "action":     decision.get("action"),
                    "order_qty":  decision.get("order_qty"),
                    "urgency":    decision.get("urgency"),
                    "confidence": decision.get("confidence"),
                    "escalate":   decision.get("escalate_to_human"),
                })
        except FutureTimeoutError:
            # `.result(timeout)` lève un TimeoutError dont str() est vide : sans
            # message explicite le log se terminait sur « : » et ne disait rien.
            msg = f"timeout after {_DECISION_TIMEOUT_S:.0f}s"
            logger.error("[Orchestrator] decision_agent %s SKU=%s", msg, sku)
            result["decision_result"] = {"sku": sku, "store_id": store_id, "error": msg}
            if decision_span:
                decision_span.end(error=msg)
        except Exception as exc:
            # Le type est indispensable : plusieurs exceptions d'infrastructure
            # (Timeout, CancelledError) ont un str() vide.
            msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            logger.error(
                "[Orchestrator] decision_agent failed SKU=%s: %s", sku, msg, exc_info=True
            )
            result["decision_result"] = {"sku": sku, "store_id": store_id, "error": msg}
            if decision_span:
                decision_span.end(error=msg)

        # ── Redis Alert Bus: dispatch alertes CRITICAL/EXPEDITE ───────────────
        if _REDIS_ALERTS:
            decision_dict = result.get("decision_result", {}).get("decision", {})
            if decision_dict:
                adjusted = result.get("decision_result", {}).get("adjusted_metrics", {})
                risk_in_dec = adjusted.get("risk_assessment", {}).get("level", "")
                days_adj    = adjusted.get("metrics", {}).get("days_of_stock_remaining")
                dispatch_record = {
                    "sku":                    sku,
                    "action":                 decision_dict.get("action"),
                    "risk_level":             risk_in_dec,
                    "days_of_stock_remaining": days_adj,
                    "order_qty":              decision_dict.get("order_qty"),
                    "urgency":                decision_dict.get("urgency"),
                    "escalate_to_human":      decision_dict.get("escalate_to_human"),
                    "confidence":             decision_dict.get("confidence"),
                }
                try:
                    dispatch_alerts_sync([dispatch_record], store_id)
                except Exception as exc:
                    logger.debug("[Orchestrator] Redis alert dispatch skipped: %s", exc)

        if lf_trace:
            lf_trace.finish(result=result)
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