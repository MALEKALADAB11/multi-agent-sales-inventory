"""
Inventory Analysis Agent
========================
Pipeline position : 1st
Feeds into        : Context Agent → Decision Agent

Two-node LangGraph graph:
  fetch  → batch_inventory_data() — pure Python, no subprocess, no async
  reason → one LLM call for objective_note + risk_rationale prose only

All structured numbers are parsed deterministically from tool output text.
The LLM never touches numbers.

Performance knobs (change these, nothing else):
  USE_LLM      = False  →  skip LLM entirely, rule-based prose, instant.
                            Use when out of Groq tokens or during heavy dev.
  LLM_TIMEOUT_S = 8    →  hard timeout per SKU LLM call. If Groq doesn't
                            respond in time, rule-based fallback fires and
                            that thread is unblocked immediately.
"""

from typing import TypedDict, Annotated, Sequence, Dict, Any
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, END
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import operator
import json
import re
import logging

from config.settings import settings
from src.tools.internal.mcp_wrappers import batch_inventory_data

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Performance knobs
# ---------------------------------------------------------------------------

USE_LLM       = True   # set False to skip LLM and use rule-based prose
LLM_TIMEOUT_S = 8      # seconds before giving up on Groq and falling back


# ---------------------------------------------------------------------------
# Robust text parser
# ---------------------------------------------------------------------------

def _s(text: str, label: str, default: str = "N/A") -> str:
    m = re.search(rf"^\s*{re.escape(label)}\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else default


def _f(text: str, label: str, default: float = 0.0) -> float:
    raw = _s(text, label, "")
    if not raw:
        return default
    token = raw.split()[0]
    cleaned = re.sub(r"[^\d.\-]", "", token)
    try:
        return float(cleaned)
    except ValueError:
        return default


def _parse_tool_outputs(raw: Dict[str, str]) -> Dict[str, Any]:
    """
    Convert the three raw tool-output strings into a structured Python dict.
    Deterministic — the LLM never touches these values.
    """
    s = raw["stock_status"]
    f = raw["forecast"]
    m = raw["metrics"]

    current_stock     = _f(m, "Stock level")
    lead_time_avg     = _f(s, "Lead time avg")
    lead_time_std     = _f(s, "Lead time std")
    moq               = _f(s, "MOQ")
    unit_cost         = _f(s, "Unit cost")
    holding_cost_pct  = _f(s, "Holding cost pct", 0.25)
    order_cost        = _f(s, "Order cost")
    lifecycle_stage   = _s(s, "Lifecycle stage", "mature")
    service_level_tgt = _f(s, "Service lvl tgt", 0.95)

    avg_daily  = _f(f, "Daily avg")
    demand_std = _f(f, "Daily std dev")
    total_30d  = _f(f, "Total demand")
    trend      = _s(f, "Trend", "stable")

    days_remaining  = _f(m, "Days of stock remaining")
    risk_level      = _s(m, "Risk level", "MEDIUM")
    risk_rationale  = _s(m, "Risk rationale", "")
    overstock       = "YES" in _s(m, "Overstock flag", "No").upper()

    reorder_point   = _f(m, "Reorder point")
    eoq             = _f(m, "EOQ")
    recommended_qty = _f(m, "Recommended order qty")
    total_replen    = _f(m, "Total replenishment cost")
    z_score         = _f(m, "Z-score", 1.645)

    ss_match = re.search(r"^\s*=\s*([\d.]+)\s+units\s*$", m, re.MULTILINE)
    safety_stock = (
        float(ss_match.group(1)) if ss_match
        else _f(m, "Safety stock cost") / unit_cost if unit_cost else 0.0
    )

    sl_match = re.search(r"adjusted to\s+([\d.]+)%", m)
    if sl_match:
        eff_sl = float(sl_match.group(1)) / 100
    else:
        eff_sl = service_level_tgt / 100 if service_level_tgt > 1 else service_level_tgt

    if service_level_tgt > 1:
        service_level_tgt = service_level_tgt / 100

    moq_is_binding    = (eoq > 0) and (eoq < moq)
    holding_cost_dt   = (recommended_qty / 2) * unit_cost * holding_cost_pct
    safety_stock_cost = round(safety_stock * unit_cost, 2)
    high_cost_flag    = total_replen > 50_000
    high_holding_flag = holding_cost_dt > 10_000

    return {
        "stock_status": {
            "current_stock":        current_stock,
            "lead_time_avg_days":   lead_time_avg,
            "lead_time_std_days":   lead_time_std,
            "moq":                  moq,
            "unit_cost":            unit_cost,
            "holding_cost_pct":     holding_cost_pct,
            "order_cost":           order_cost,
            "lifecycle_stage":      lifecycle_stage,
            "service_level_target": service_level_tgt,
        },
        "forecast": {
            "avg_daily_demand": avg_daily,
            "demand_std_dev":   demand_std,
            "total_30d_demand": total_30d,
            "trend_direction":  trend,
        },
        "metrics": {
            "days_of_stock_remaining":   days_remaining,
            "effective_service_level":   eff_sl,
            "z_score":                   z_score,
            "safety_stock":              safety_stock,
            "safety_stock_cost_dt":      safety_stock_cost,
            "reorder_point":             reorder_point,
            "eoq":                       eoq,
            # formula_order_qty = max(EOQ, MOQ) — mathematical floor, NOT a decision.
            # The Decision Agent sets actual order qty after context + constraints.
            "formula_order_qty":         recommended_qty,
            "holding_cost_per_cycle_dt": round(holding_cost_dt, 2),
            "total_replenishment_cost":  total_replen,
        },
        "risk_assessment": {
            "level":          risk_level,
            "overstock_flag": overstock,
            "rationale":      risk_rationale,
        },
        "constraints": {
            "moq":               moq,
            "moq_is_binding":    moq_is_binding,
            "moq_binding_note":  (
                f"EOQ ({eoq:.0f}) < MOQ ({moq:.0f}) — ordering more than optimal"
                if moq_is_binding else
                f"EOQ ({eoq:.0f}) ≥ MOQ ({moq:.0f}) — EOQ drives order size"
            ),
            "high_cost_flag":            high_cost_flag,
            "high_holding_flag":         high_holding_flag,
            "holding_cost_per_cycle_dt": round(holding_cost_dt, 2),
            "safety_stock_cost_dt":      safety_stock_cost,
        },
        "objective_note": "",  # filled by LLM or rule-based fallback below
    }


def _rule_based_prose(objective: str, structured: Dict[str, Any]) -> Dict[str, str]:
    """
    Generates the two prose fields without the LLM.
    Called when USE_LLM=False, or when LLM times out / returns 429.
    Structurally identical to LLM output — downstream code sees no difference.
    """
    sl  = structured["metrics"]["effective_service_level"]
    lc  = structured["stock_status"]["lifecycle_stage"]
    dos = structured["metrics"]["days_of_stock_remaining"]
    lt  = structured["stock_status"]["lead_time_avg_days"]
    rat = structured["risk_assessment"]["rationale"]

    return {
        "objective_note": (
            f"Objective '{objective}' with lifecycle '{lc}' "
            f"set effective SL to {sl:.0%}."
        ),
        "risk_rationale": rat if rat and rat != "N/A" else (
            f"Stock covers {dos:.1f}d vs {lt:.0f}d lead time."
        ),
    }


# ---------------------------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    messages:           Annotated[Sequence[BaseMessage], operator.add]
    sku:                str
    store_id:           str
    business_objective: str
    raw_data:           Dict[str, str]
    structured:         Dict[str, Any]


# ---------------------------------------------------------------------------
# LLM prompt — two prose fields only, everything else is Python
# ---------------------------------------------------------------------------

REASON_SYSTEM = """\
You are a senior inventory analyst writing a brief analytical note.
You will receive structured inventory data for one SKU.

Return ONLY valid JSON with exactly these two keys — no markdown, no fences:
{
  "objective_note": "<2-3 sentences: how did the business_objective and lifecycle_stage
                     combine to set the effective service level, and what single metric
                     most needs watching given that combination>",
  "risk_rationale": "<1-2 sentences in plain business language: what is the actual
                     stockout or overstock risk, referencing days-of-stock vs lead time>"
}"""

REASON_USER = """\
SKU: {sku}  |  Store: {store_id}  |  Objective: {business_objective}

STOCK STATUS:
{stock_status}

FORECAST:
{forecast}

COMPUTED METRICS:
{metrics}
"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class InventoryAnalysisAgent:
    _FORBIDDEN_KEYS = (
        "recommendation", "order_quantity", "timing",
        "action", "suggested_order",
    )

    def __init__(self, api_key: str = None):
        api_key = api_key or settings.groq_api_key
        if not api_key:
            raise ValueError("GROQ_API_KEY not found in environment")

        self.llm = ChatGroq(
            model=settings.llm_model,
            api_key=api_key,
            temperature=0.0,
        )

        wf = StateGraph(AgentState)
        wf.add_node("fetch",  self._fetch_node)
        wf.add_node("reason", self._reason_node)
        wf.set_entry_point("fetch")
        wf.add_edge("fetch", "reason")
        wf.add_edge("reason", END)
        self.graph = wf.compile()

    def _fetch_node(self, state: AgentState) -> Dict[str, Any]:
        """
        Pure Python data fetch — no LLM, no network, no async.
        Safe to run in parallel threads (each SKU is fully independent).
        Uses _DataCache in stock_tools so CSV files are only read once
        per process lifetime regardless of how many SKUs are in the batch.
        """
        raw = batch_inventory_data(
            sku=state["sku"],
            store_id=state["store_id"],
            business_objective=state["business_objective"],
        )
        structured = _parse_tool_outputs(raw)
        return {"raw_data": raw, "structured": structured}

    def _reason_node(self, state: AgentState) -> Dict[str, Any]:
        """
        LLM call with hard timeout. Three possible paths:

          Path 1 — USE_LLM=False:
            Skip LLM entirely. Rule-based prose fills both fields. Instant.

          Path 2 — LLM responds within LLM_TIMEOUT_S:
            Use LLM output. Thread proceeds normally.

          Path 3 — LLM times out or returns 429/error:
            Rule-based fallback fires immediately. Thread is unblocked.
            No retry, no hang, no crash. The batch continues.

        The output structure is identical in all three paths — downstream
        code (routes.py, frontend) cannot tell which path was taken.
        """
        structured = state["structured"]

        # Path 1: LLM disabled
        if not USE_LLM:
            reasoning = _rule_based_prose(state["business_objective"], structured)
            updated = dict(structured)
            updated["objective_note"] = reasoning["objective_note"]
            updated["risk_assessment"]["rationale"] = reasoning["risk_rationale"]
            return {"structured": updated, "messages": []}

        # Paths 2 & 3: attempt LLM with hard timeout
        raw = state["raw_data"]
        prompt = REASON_USER.format(
            sku=state["sku"],
            store_id=state["store_id"],
            business_objective=state["business_objective"],
            stock_status=raw["stock_status"],
            forecast=raw["forecast"],
            metrics=raw["metrics"],
        )

        reasoning = None
        msgs      = []

        def _call_llm():
            return self.llm.invoke([
                SystemMessage(content=REASON_SYSTEM),
                HumanMessage(content=prompt),
            ])

        try:
            # Run in a thread so we can apply a hard wall-clock timeout.
            # The Groq client has no built-in timeout parameter.
            with ThreadPoolExecutor(max_workers=1) as ex:
                future   = ex.submit(_call_llm)
                response = future.result(timeout=LLM_TIMEOUT_S)
            reasoning = _parse_reasoning(response.content)
            msgs      = [response]
            logger.debug("LLM OK for SKU %s", state["sku"])

        except FuturesTimeoutError:
            logger.warning(
                "LLM timed out (%ds) for SKU %s — rule-based fallback.",
                LLM_TIMEOUT_S, state["sku"],
            )
        except Exception as e:
            logger.warning(
                "LLM failed (%s) for SKU %s — rule-based fallback.",
                e, state["sku"],
            )

        if reasoning is None:
            reasoning = _rule_based_prose(state["business_objective"], structured)

        updated = dict(structured)
        updated["objective_note"] = reasoning.get("objective_note", "")
        updated["risk_assessment"]["rationale"] = reasoning.get(
            "risk_rationale", updated["risk_assessment"]["rationale"]
        )
        return {"structured": updated, "messages": msgs}

    def run(
        self,
        sku: str,
        store_id: str = "STORE-001",
        business_objective: str = "balanced",
    ) -> Dict[str, Any]:
        try:
            result = self.graph.invoke({
                "messages":           [],
                "sku":                sku,
                "store_id":           store_id,
                "business_objective": business_objective,
                "raw_data":           {},
                "structured":         {},
            })

            report = result["structured"]
            for key in self._FORBIDDEN_KEYS:
                report.pop(key, None)

            report.update({
                "sku":                sku,
                "store_id":           store_id,
                "business_objective": business_objective,
                "report_type":        "BASELINE",
            })

            return {
                "sku":                sku,
                "store_id":           store_id,
                "business_objective": business_objective,
                "analysis_report":    report,
            }

        except Exception as e:
            logger.error("InventoryAnalysisAgent failed for SKU=%s: %s", sku, e, exc_info=True)
            return {
                "sku":                sku,
                "store_id":           store_id,
                "business_objective": business_objective,
                "error":              str(e),
            }


def _parse_reasoning(text: str) -> Dict[str, str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            l for l in cleaned.splitlines() if not l.strip().startswith("```")
        ).strip()
    try:
        start = cleaned.find("{")
        end   = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end])
    except json.JSONDecodeError as e:
        logger.warning("Reasoning JSON parse failed: %s", e)
    return {"objective_note": cleaned[:400], "risk_rationale": ""}


def create_analysis_agent(api_key: str = None) -> InventoryAnalysisAgent:
    return InventoryAnalysisAgent(api_key=api_key)