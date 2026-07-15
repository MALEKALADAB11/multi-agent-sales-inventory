"""
Langfuse observability for the inventory multi-agent pipeline.

Trace hierarchy per SKU:
  trace  "inventory-pipeline"  (1 per SKU per batch run)
    ├─ span  "agent-analysis"
    │    └─ [auto] LangGraph nodes: fetch → compute → reason
    │         └─ [auto] generation  LLM call in reason node
    ├─ span  "agent-context"
    │    └─ [auto] LangGraph nodes: fetch_signals → interpret
    │         └─ [auto] generation  LLM call in interpret node
    └─ span  "agent-decision"
         └─ [auto] LangGraph node: decide
              └─ [auto] generation  LLM call in decide node

Auto-tracing of LangGraph nodes and LLM calls is done via the
Langfuse LangChain CallbackHandler passed to graph.invoke().
"""

import os
import time
import uuid
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── Singleton ──────────────────────────────────────────────────────────────────

_lf_instance = None


def _get_lf():
    global _lf_instance
    if _lf_instance is not None:
        return _lf_instance
    try:
        from langfuse import Langfuse
        _lf_instance = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-pfe-local"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-pfe-local"),
            host=os.getenv("LANGFUSE_HOST", "http://localhost:3001"),
            timeout=10,  # jamais de socket sans timeout dans le chemin de requête
        )
        return _lf_instance
    except Exception as exc:
        logger.debug("Langfuse unavailable: %s", exc)
        return None


def _make_callback(stateful_client):
    """Return a LangChain CallbackHandler linked to a Langfuse span/trace."""
    if stateful_client is None:
        return None
    try:
        from langfuse.callback import CallbackHandler
        return CallbackHandler(stateful_client=stateful_client)
    except Exception as exc:
        logger.debug("CallbackHandler unavailable: %s", exc)
        return None


# ── Noop fallback ──────────────────────────────────────────────────────────────

class _NoopSpan:
    """Silent no-op when Langfuse is disabled / unavailable."""
    callback_handler = None
    def end(self, output: Dict = None, error: str = None): pass


# ── Agent span ─────────────────────────────────────────────────────────────────

class AgentSpan:
    """Wraps a Langfuse span for one agent (analysis / context / decision)."""

    def __init__(self, span, sku: str):
        self._span = span
        self._sku  = sku
        self._t0   = time.time()
        # Callback handler auto-traces LangGraph node executions + LLM calls
        self.callback_handler = _make_callback(span)

    def end(self, output: Dict = None, error: str = None):
        try:
            ms = int((time.time() - self._t0) * 1000)
            if error:
                self._span.end(
                    output={"error": error},
                    level="ERROR",
                    status_message=error[:500],
                    metadata={"duration_ms": ms, "sku": self._sku},
                )
            else:
                self._span.end(
                    output=output or {},
                    level="DEFAULT",
                    metadata={"duration_ms": ms, "sku": self._sku},
                )
        except Exception as exc:
            logger.debug("AgentSpan.end failed: %s", exc)


# ── Pipeline trace ─────────────────────────────────────────────────────────────

class InventoryPipelineTrace:
    """
    Root Langfuse trace for one SKU through the full inventory pipeline.

    Usage (orchestrator):
        trace = InventoryPipelineTrace(sku, store_id, objective, batch_id)
        trace.start()

        span = trace.agent_span("analysis", input_data={...})
        # pass span.callback_handler to agent.run()
        span.end(output={...})

        trace.finish(result)
    """

    def __init__(
        self,
        sku:                str,
        store_id:           str,
        business_objective: str,
        batch_id:           str = None,
    ):
        self.sku                = sku
        self.store_id           = store_id
        self.business_objective = business_objective
        self.batch_id           = batch_id
        self._lf                = _get_lf()
        self._trace             = None
        self._t0                = None

    def start(self):
        if self._lf is None:
            return
        # Échantillonnage : 1 trace par SKU par batch × batchs continus × N
        # boutiques saturait la file d'upload (« analytics-python queue is
        # full ») et le serveur Langfuse (CPU 90%+). On ne trace qu'une
        # fraction des SKUs — suffisant pour observer le pipeline.
        import random
        sample = float(os.getenv("LANGFUSE_INVENTORY_SAMPLE", "0.1"))
        if random.random() >= sample:
            return
        try:
            import datetime
            session = self.batch_id or f"{self.store_id}-{datetime.date.today().isoformat()}"
            self._trace = self._lf.trace(
                id=f"inv-{self.sku}-{self.store_id}-{uuid.uuid4().hex[:8]}",
                name="inventory-pipeline",
                input={
                    "sku":                self.sku,
                    "store_id":           self.store_id,
                    "business_objective": self.business_objective,
                },
                user_id=self.store_id,
                session_id=session,
                tags=["inventory", self.store_id, self.business_objective, f"sku-{self.sku}"],
                metadata={"sku": self.sku, "batch_id": self.batch_id},
            )
            self._t0 = time.time()
        except Exception as exc:
            logger.debug("Langfuse trace.start failed: %s", exc)

    def agent_span(self, name: str, input_data: Dict = None) -> "AgentSpan | _NoopSpan":
        """Open a child span for one agent."""
        if self._trace is None:
            return _NoopSpan()
        try:
            span = self._trace.span(
                name=f"agent-{name}",
                input=input_data or {},
                metadata={"agent": name, "sku": self.sku, "store_id": self.store_id},
            )
            return AgentSpan(span, self.sku)
        except Exception as exc:
            logger.debug("Langfuse agent_span failed: %s", exc)
            return _NoopSpan()

    def finish(self, result: Dict = None, error: str = None):
        """Close the trace with pipeline output and compute quality score."""
        if self._trace is None:
            return
        try:
            ms = int((time.time() - self._t0) * 1000) if self._t0 else 0

            if error:
                self._trace.update(
                    output={"error": error},
                    level="ERROR",
                    status_message=error[:500],
                    metadata={"duration_ms": ms},
                )
                self._score(0.0, f"pipeline error: {error[:100]}")
                return

            if not result:
                return

            report   = result.get("analysis_report", {})
            risk     = report.get("risk_assessment", {})
            metrics  = report.get("metrics", {})
            decision = result.get("decision_result", {}).get("decision", {})
            ctx      = result.get("context_result", {}).get("context_report", {})

            risk_level = (risk.get("level") or "").upper()
            action     = (decision.get("action") or "").upper()
            confidence = (decision.get("confidence") or "low").lower()

            has_error = bool(
                result.get("analysis_error")
                or "error" in result.get("decision_result", {})
            )

            self._trace.update(
                output={
                    "risk_level":        risk_level,
                    "action":            action,
                    "order_qty":         decision.get("order_qty"),
                    "confidence":        confidence,
                    "days_of_stock":     metrics.get("days_of_stock_remaining"),
                    "demand_uplift_pct": ctx.get("demand_uplift_pct"),
                    "escalate":          decision.get("escalate_to_human", False),
                },
                level="ERROR" if has_error else "DEFAULT",
                metadata={"duration_ms": ms},
            )

            # Quality score: is the action consistent with the risk?
            score = self._compute_score(risk_level, action, confidence, has_error)
            self._score(score, f"risk={risk_level} action={action} conf={confidence}")

        except Exception as exc:
            logger.debug("Langfuse trace.finish failed: %s", exc)

    # ── internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_score(risk_level: str, action: str, confidence: str, has_error: bool) -> float:
        if has_error:
            return 0.0
        # Correct: urgent risk → ORDER/EXPEDITE
        if risk_level in ("CRITICAL", "HIGH") and action in ("ORDER", "EXPEDITE"):
            base = 1.0
        # Correct: low risk → HOLD/MONITOR
        elif risk_level in ("LOW", "MEDIUM") and action in ("HOLD", "MONITOR"):
            base = 0.9
        # Mismatch: urgent risk but no action
        elif risk_level in ("CRITICAL", "HIGH") and action in ("HOLD", "MONITOR"):
            base = 0.3
        else:
            base = 0.7
        # Confidence modifier
        conf_bonus = {"high": 0.0, "medium": -0.05, "low": -0.15}.get(confidence, 0.0)
        return round(max(0.0, min(1.0, base + conf_bonus)), 3)

    def _score(self, value: float, comment: str = ""):
        try:
            self._lf.score(
                trace_id=self._trace.id,
                name="pipeline-quality",
                value=value,
                comment=comment,
            )
        except Exception as exc:
            logger.debug("Langfuse score failed: %s", exc)


# ── Batch session helper ───────────────────────────────────────────────────────

def log_batch_start(store_id: str, n_skus: int, objective: str) -> str:
    """
    Log a batch run start event. Returns a batch_id to pass to each
    InventoryPipelineTrace so all SKU traces share the same session in Langfuse.
    """
    batch_id = f"batch-{store_id}-{uuid.uuid4().hex[:8]}"
    lf = _get_lf()
    if lf is None:
        return batch_id
    try:
        lf.event(
            name="batch-start",
            input={"store_id": store_id, "n_skus": n_skus, "objective": objective},
            metadata={"batch_id": batch_id},
        )
    except Exception:
        pass
    return batch_id


def log_batch_end(batch_id: str, store_id: str, n_critical: int, n_high: int, n_errors: int, duration_ms: int):
    """Log batch completion summary as a Langfuse event."""
    lf = _get_lf()
    if lf is None:
        return
    try:
        lf.event(
            name="batch-end",
            input={"batch_id": batch_id, "store_id": store_id},
            output={
                "critical": n_critical,
                "high":     n_high,
                "errors":   n_errors,
            },
            metadata={"batch_id": batch_id, "duration_ms": duration_ms},
        )
        # PAS de lf.flush() ici : flush() attend la vidange complète de la
        # queue d'upload (bloquant, sans borne) dans le chemin de requête HTTP.
        # Le task manager Langfuse envoie en arrière-plan de toute façon.
    except Exception:
        pass
