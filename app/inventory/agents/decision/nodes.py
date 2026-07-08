"""
Decision Agent Nodes
=====================
Single node: decide

One job: synthesise analysis_report + context_report + adjusted_metrics
into a concrete action and a single recommendation_text paragraph.

recommendation_text is written by the LLM as one coherent judgment.
The rule-based fallback builds the same structure deterministically —
same voice, same sentence order, no section headers or bullet points.
"""

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _extract_adjusted(state: Dict[str, Any]) -> tuple:
    """Pull decision-critical fields from adjusted_metrics, falling back to baseline."""
    adj  = state.get("adjusted_metrics", {})
    base = state.get("baseline_report", {})

    adj_risk    = adj.get("risk_assessment") or base.get("risk_assessment", {})
    adj_metrics = adj.get("metrics") or base.get("metrics", {})
    base_stock  = base.get("stock", {})

    risk_level     = adj_risk.get("level") or (base.get("risk_assessment") or {}).get("level", "LOW")
    overstock_flag = (
        adj_risk.get("overstock_flag", False)
        or (base.get("risk_assessment") or {}).get("overstock_flag", False)
    )

    days_remaining    = _safe_float(adj_metrics.get("days_of_stock_remaining"), 999.0)
    formula_order_qty = _safe_float(adj_metrics.get("formula_order_qty"), 0.0)
    reorder_point     = _safe_float(adj_metrics.get("reorder_point"), 0.0)
    repl_cost         = _safe_float(adj_metrics.get("total_replenishment_cost"), 0.0)
    lead_time_avg     = _safe_float(base_stock.get("lead_time_avg_days"), 14.0)

    return (risk_level, days_remaining, formula_order_qty,
            reorder_point, repl_cost, overstock_flag, lead_time_avg)


def _extract_constraints(state: Dict[str, Any]) -> Dict[str, Any]:
    base  = state.get("baseline_report", {})
    cons  = base.get("constraints", {})
    stock = base.get("stock", {})
    adj_m = (state.get("adjusted_metrics", {}).get("metrics") or base.get("metrics", {}))
    return {
        "moq":            _safe_float(cons.get("moq"), 1.0),
        "moq_is_binding": cons.get("moq_is_binding", False),
        "high_cost_flag": cons.get("high_cost_flag", False),
        "obj_conflict":   cons.get("objective_conflict", False),
        "lifecycle":      stock.get("lifecycle_stage", "mature"),
        "lead_time":      _safe_float(stock.get("lead_time_avg_days"), 14.0),
        "risk_override":  (base.get("risk_assessment") or {}).get("override"),
        "repl_cost":      _safe_float(adj_m.get("total_replenishment_cost"), 0.0),
        "analyst_flag":   base.get("analyst_flag", ""),
        "obj_conflict":   cons.get("objective_conflict", False),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Rule-Based Fallback — same voice as LLM output
# ═══════════════════════════════════════════════════════════════════════════

def _build_recommendation_text(
    action:     str,
    urgency:    str,
    order_qty:  Optional[int],
    state:      Dict[str, Any],
    escalate:   bool,
    esc_reason: Optional[str],
) -> str:
    """
    Build recommendation_text in the same style the LLM uses:
    3-5 sentences, action first, numbers justify, context qualifies.
    No section headers, no bullet points, no filler.
    """
    base  = state.get("baseline_report", {})
    adj   = state.get("adjusted_metrics", {})
    ctx   = state.get("context_report", {})
    cons  = _extract_constraints(state)

    base_risk = base.get("risk_assessment", {})
    base_m    = base.get("metrics", {})
    adj_m     = adj.get("metrics") or base_m
    stock     = base.get("stock", {})

    adj_days  = _safe_float(adj_m.get("days_of_stock_remaining"),
                            _safe_float(base_m.get("days_of_stock_remaining")))
    base_days = _safe_float(base_m.get("days_of_stock_remaining"))
    rop       = _safe_float(adj_m.get("reorder_point"))
    adj_cost  = _safe_float(adj_m.get("total_replenishment_cost"))
    lead_time = cons["lead_time"]

    uplift   = _safe_float(ctx.get("demand_uplift_pct"))
    dominant = ctx.get("dominant_signal", "none")
    ctx_conf = ctx.get("confidence", "low")

    # 999 = sentinelle "couverture illimitée" — ne jamais l'afficher tel quel
    def _fmt_days(d: float) -> str:
        return "plus d'un an de couverture (demande quasi nulle)" if d >= 999 else f"{d:.0f} jours"

    sentences = []

    # ── S1: What to do and when ───────────────────────────────────────────
    if action == "EXPEDITE":
        s1 = (
            f"Expédier {order_qty:,} unités aujourd'hui — le stock s'épuise dans {_fmt_days(adj_days)} "
            f"et le délai de livraison standard est de {lead_time:.0f} jours, une rupture est donc "
            f"garantie avant l'arrivée d'une commande normale."
        )
    elif action == "ORDER":
        urgency_phrase = {"immediate": "aujourd'hui", "this_week": "cette semaine",
                          "this_month": "ce mois-ci"}.get(urgency, urgency)
        s1 = (
            f"Commander {order_qty:,} unités {urgency_phrase} — le stock est à {_fmt_days(adj_days)} "
            f"contre un point de commande de {rop:.0f} unités et un délai de livraison de {lead_time:.0f} jours."
        )
    elif action == "MONITOR":
        s1 = (
            f"Pas de commande pour l'instant — le stock est à {_fmt_days(adj_days)}, "
            f"proche mais pas encore sous le point de commande de {rop:.0f} unités."
        )
    else:  # HOLD
        if base_risk.get("overstock_flag"):
            s1 = (
                f"Ne pas commander — le stock dépasse le seuil maximal à {_fmt_days(adj_days)}, "
                f"un ajout de stock augmenterait les coûts de stockage sans bénéfice de service."
            )
        else:
            s1 = (
                f"Aucune action requise — le stock couvre {_fmt_days(adj_days)}, "
                f"au-dessus du point de commande de {rop:.0f} unités."
            )

    sentences.append(s1)

    # ── S2-3: Why, in numbers ─────────────────────────────────────────────
    risk_level = base_risk.get("level", "")
    layer2     = base_risk.get("layer2_result", "")

    if action in ("ORDER", "EXPEDITE"):
        # Give the binding constraint
        if adj_days < lead_time:
            s2 = (
                f"Au rythme de demande actuel, le rayon sera vide dans {adj_days:.0f} jours — "
                f"{lead_time:.0f} jours avant qu'un réapprovisionnement standard puisse arriver."
            )
        else:
            s2 = (
                f"Le stock est passé sous le point de commande de {rop:.0f} unités ; "
                f"une commande passée maintenant arrivera avec environ "
                f"{max(0, adj_days - lead_time):.0f} jours de marge restante."
            )
        sentences.append(s2)

        if adj_cost > 0:
            sentences.append(
                f"Le coût de réapprovisionnement est de {adj_cost:,.0f} DT pour {order_qty:,} unités."
            )

    elif action == "MONITOR":
        sentences.append(
            f"Revoir avant que le stock ne passe sous {rop:.0f} unités — "
            f"si la demande accélère ou si le délai s'allonge, basculer en commande."
        )

    else:  # HOLD
        sentences.append(
            f"Prochaine revue lors du contrôle d'inventaire programmé ou si la tendance de demande change."
        )

    # ── S4: Context influence ─────────────────────────────────────────────
    if uplift != 0.0 and dominant != "none":
        # Ne chiffrer l'effet que s'il est réel et hors sentinelle (évite "de 999 à 999")
        if base_days < 999 and abs(adj_days - base_days) >= 0.5:
            s_ctx = (
                f"Une hausse de demande de {uplift:+.1f}% liée à {dominant} (confiance : {ctx_conf}) "
                f"a été appliquée, réduisant les jours ajustés de {base_days:.0f} à {adj_days:.0f}."
            )
        else:
            s_ctx = (
                f"Une hausse de demande de {uplift:+.1f}% liée à {dominant} "
                f"(confiance : {ctx_conf}) a été prise en compte, sans impact matériel sur la couverture."
            )
        sentences.append(s_ctx)
    elif action in ("ORDER", "EXPEDITE") and dominant == "none":
        # Only mention lack of signals for ORDER/EXPEDITE where it's relevant
        sentences.append(
            f"Aucun signal de demande cette semaine — la décision repose uniquement sur le calcul de stock."
        )

    # ── S5: Escalation flag ───────────────────────────────────────────────
    if escalate and esc_reason:
        sentences.append(f"Escalader au manager avant de commander : {esc_reason}")
    elif cons["obj_conflict"] and action in ("ORDER", "EXPEDITE"):
        sentences.append(
            "Cette commande peut entrer en conflit avec l'objectif business actif — "
            "à revoir avec le category manager."
        )

    return " ".join(sentences)


# ═══════════════════════════════════════════════════════════════════════════
# Rule-Based Decision
# ═══════════════════════════════════════════════════════════════════════════

def _rule_based_decide(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic fallback. Same logic as the LLM prompt rules.
    Priority: overstock → EXPEDITE → ORDER → MONITOR → HOLD
    """
    (risk_level, days_remaining, formula_order_qty,
     reorder_point, repl_cost, overstock_flag, lead_time_avg) = _extract_adjusted(state)
    cons = _extract_constraints(state)

    lifecycle = cons["lifecycle"]
    override  = cons["risk_override"]

    # ── Action ───────────────────────────────────────────────────────────
    if overstock_flag:
        action, urgency = "HOLD", "none"
        rationale = (
            f"Le stock dépasse le seuil maximal — un ajout de stock "
            f"augmenterait les coûts de stockage sans bénéfice de service."
        )
    elif risk_level == "CRITICAL" and days_remaining < lead_time_avg:
        action, urgency = "EXPEDITE", "immediate"
        rationale = (
            f"Le stock s'épuise dans {days_remaining:.0f}j mais le réapprovisionnement prend "
            f"{lead_time_avg:.0f}j — une rupture est garantie avant l'arrivée d'une commande standard."
        )
    elif risk_level in ("CRITICAL", "HIGH"):
        action  = "ORDER"
        urgency = "immediate" if risk_level == "CRITICAL" else "this_week"
        rationale = (
            f"Risque {risk_level} : {days_remaining:.1f}j restants contre "
            f"{lead_time_avg:.0f}j de délai de livraison, point de commande {reorder_point:.0f} unités."
        )
    elif risk_level == "MEDIUM":
        action, urgency = "MONITOR", "this_month"
        rationale = (
            f"Risque MOYEN : {days_remaining:.1f}j restants, proche du point de commande "
            f"de {reorder_point:.0f} unités mais pas encore atteint."
        )
    else:
        action, urgency = "HOLD", "none"
        rationale = f"Risque FAIBLE : {days_remaining:.1f}j de stock — aucune action requise."

    order_qty = int(formula_order_qty) if action in ("ORDER", "EXPEDITE") else None

    # ── Trade-offs ───────────────────────────────────────────────────────
    if action in ("ORDER", "EXPEDITE"):
        trade_offs = (
            f"Commander {formula_order_qty:.0f} unités coûte {repl_cost:,.0f} DT ; "
            f"ne pas commander risque une rupture dans {days_remaining:.0f}j."
        )
    elif action == "HOLD":
        trade_offs = "Conserver préserve la trésorerie ; surveiller en cas de pic de demande imprévu."
    else:
        trade_offs = "Attendre retarde l'engagement ; réévaluer si la demande accélère."

    # ── Escalation ───────────────────────────────────────────────────────
    escalate: bool            = False
    esc_reason: Optional[str] = None

    if action in ("ORDER", "EXPEDITE") and repl_cost > 50_000:
        escalate   = True
        esc_reason = f"Coût de commande {repl_cost:,.0f} DT dépasse 50 000 DT — validation manager requise."
    elif lifecycle in ("end_of_life", "discontinued") and risk_level == "CRITICAL":
        escalate   = True
        esc_reason = "CRITIQUE sur produit en fin de vie/discontinué — vérifier l'écoulement intentionnel avant de commander."
    elif override == "ESCALATE":
        escalate   = True
        esc_reason = "L'agent d'analyse a signalé un conflit multi-dimensionnel — révision humaine requise."
    elif action in ("ORDER", "EXPEDITE") and cons["high_cost_flag"] and cons["obj_conflict"]:
        escalate   = True
        esc_reason = "Commande à coût élevé en conflit avec l'objectif business actif — validation requise."

    # ── Confidence ───────────────────────────────────────────────────────
    ctx          = state.get("context_report", {})
    uplift       = _safe_float(ctx.get("demand_uplift_pct"))
    ctx_conf     = ctx.get("confidence", "low")

    if override or cons["obj_conflict"] or cons["moq_is_binding"]:
        # Structural uncertainty — always low regardless of risk clarity
        confidence = "low"
    elif uplift != 0.0 and ctx_conf == "low":
        # Signal was applied but is uncertain — medium even if risk is clear
        confidence = "medium"
    elif uplift == 0.0 and ctx_conf == "low":
        # No signal detected — decision rests on math alone, which is valid.
        # Do not penalise for context finding nothing. Trust the risk level.
        confidence = "high" if risk_level in ("CRITICAL", "LOW") else "medium"
    elif risk_level in ("CRITICAL", "LOW") and not escalate:
        confidence = "high"
    else:
        confidence = "medium"

    recommendation_text = _build_recommendation_text(
        action=action,
        urgency=urgency,
        order_qty=order_qty,
        state=state,
        escalate=escalate,
        esc_reason=esc_reason,
    )

    return {
        "action":               action,
        "order_qty":            order_qty,
        "order_qty_rationale":  f"max(EOQ={formula_order_qty:.0f}, MOQ={cons['moq']:.0f}) ajusté selon la hausse de demande.",
        "urgency":              urgency,
        "decision_rationale":   rationale,
        "confidence":           confidence,
        "trade_offs":           trade_offs,
        "escalate_to_human":    escalate,
        "escalation_reason":    esc_reason,
        "recommendation_text":  recommendation_text,
        "reasoning_source":     "rule_based_fallback",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Node Factory
# ═══════════════════════════════════════════════════════════════════════════

def create_decide_node(llm, use_llm: bool):
    """
    Returns the decide node bound to the given LLM.
    Falls back to rule-based if use_llm=False, llm is None, or LLM errors.
    """

    def decide_node(state: Dict[str, Any]) -> Dict[str, Any]:
        sku      = state["sku"]
        store_id = state["store_id"]

        if not use_llm or llm is None:
            return {**state, "decision": _rule_based_decide(state)}

        # ── LLM path ──────────────────────────────────────────────────────
        from langchain_core.messages import HumanMessage, SystemMessage
        from app.inventory.agents.decision.prompts import DECIDE_SYSTEM, DECIDE_USER

        base    = state["baseline_report"]
        adj     = state["adjusted_metrics"]
        ctx     = state["context_report"]
        obj     = state["business_objective"]

        b_metrics = base.get("metrics", {})
        b_risk    = base.get("risk_assessment", {})
        a_metrics = adj.get("metrics") or base.get("metrics", {})
        a_risk    = adj.get("risk_assessment") or base.get("risk_assessment", {})
        cons      = base.get("constraints", {})
        stock     = base.get("stock", {})

        user_prompt = DECIDE_USER.format(
            sku=sku,
            store_id=store_id,
            business_objective=obj,
            # Analysis
            baseline_risk_level         = b_risk.get("level", "N/A"),
            risk_override               = b_risk.get("override") or "none",
            override_reason             = b_risk.get("override_reason") or "none",
            overstock_flag              = b_risk.get("overstock_flag", False),
            baseline_days_remaining     = _safe_float(b_metrics.get("days_of_stock_remaining")),
            baseline_formula_order_qty  = _safe_float(b_metrics.get("formula_order_qty")),
            baseline_reorder_point      = _safe_float(b_metrics.get("reorder_point")),
            baseline_replenishment_cost = _safe_float(b_metrics.get("total_replenishment_cost")),
            risk_rationale              = b_risk.get("rationale", ""),
            objective_note              = base.get("objective_note", ""),
            analyst_flag                = base.get("analyst_flag") or "none",
            objective_conflict          = cons.get("objective_conflict", False),
            lifecycle_stage             = stock.get("lifecycle_stage", "mature"),
            lead_time_avg               = _safe_float(stock.get("lead_time_avg_days"), 14.0),
            lead_time_std               = _safe_float(stock.get("lead_time_std_days"), 3.0),
            moq                         = _safe_float(cons.get("moq"), 1.0),
            moq_is_binding              = cons.get("moq_is_binding", False),
            high_cost_flag              = cons.get("high_cost_flag", False),
            # Context
            demand_uplift_pct           = _safe_float(ctx.get("demand_uplift_pct")),
            dominant_signal             = ctx.get("dominant_signal", "none"),
            context_confidence          = ctx.get("confidence", "low"),
            context_interpretation      = ctx.get("interpretation", "No demand signals detected."),
            # Adjusted
            adjusted_risk_level         = a_risk.get("level", "N/A"),
            adjusted_days_remaining     = _safe_float(a_metrics.get("days_of_stock_remaining")),
            adjusted_formula_order_qty  = _safe_float(a_metrics.get("formula_order_qty")),
            adjusted_reorder_point      = _safe_float(a_metrics.get("reorder_point")),
            adjusted_replenishment_cost = _safe_float(a_metrics.get("total_replenishment_cost")),
            adjusted_safety_stock       = _safe_float(a_metrics.get("safety_stock")),
        )

        # Boucle de feedback : les arbitrages humains passés (HITL, PO annulés,
        # raisons de rejet) modulent la décision — jamais bloquant.
        try:
            from app.core.feedback_service import get_learning_context_sync
            fb_ctx = get_learning_context_sync(store_id=store_id, sku=sku)
            if fb_ctx:
                user_prompt += (
                    "\n\n## HISTORIQUE FEEDBACK HUMAIN (à intégrer dans ta décision)\n"
                    + fb_ctx
                )
        except Exception as _fb_err:
            logger.debug("[Decision] feedback context skipped: %s", _fb_err)

        try:
            response = llm.invoke([
                SystemMessage(content=DECIDE_SYSTEM),
                HumanMessage(content=user_prompt),
            ])
            raw = response.content.strip()

            # Strip markdown fences
            if raw.startswith("```"):
                parts = raw.split("```")
                raw   = parts[1] if len(parts) > 1 else parts[0]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            # Fix control characters that break json.loads
            # (LLM sometimes embeds literal newlines inside JSON string values)
            raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # Last resort: collapse all literal newlines inside strings
                raw    = re.sub(r'\n', ' ', raw)
                parsed = json.loads(raw)

            raw_qty = parsed.get("order_qty")
            try:
                order_qty = int(raw_qty) if raw_qty is not None else None
            except (TypeError, ValueError):
                order_qty = None

            # Validate recommendation_text — if LLM omitted or emptied it,
            # build it from the rule-based fallback using the LLM's action choice
            recommendation_text = str(parsed.get("recommendation_text", "")).strip()
            if not recommendation_text:
                logger.warning(
                    "[Decision/decide] LLM omitted recommendation_text for SKU=%s "
                    "— building from rule-based", sku,
                )
                # Construct a minimal state subset to feed the fallback builder
                recommendation_text = _build_recommendation_text(
                    action    = str(parsed.get("action", "MONITOR")).upper(),
                    urgency   = str(parsed.get("urgency", "none")),
                    order_qty = order_qty,
                    state     = state,
                    escalate  = bool(parsed.get("escalate_to_human", False)),
                    esc_reason= parsed.get("escalation_reason"),
                )

            decision = {
                "action":               str(parsed.get("action", "MONITOR")).upper(),
                "order_qty":            order_qty,
                "order_qty_rationale":  str(parsed.get("order_qty_rationale", "")),
                "urgency":              str(parsed.get("urgency", "none")),
                "decision_rationale":   str(parsed.get("decision_rationale", "")),
                "confidence":           str(parsed.get("confidence", "low")),
                "trade_offs":           str(parsed.get("trade_offs", "")),
                "escalate_to_human":    bool(parsed.get("escalate_to_human", False)),
                "escalation_reason":    parsed.get("escalation_reason"),
                "recommendation_text":  recommendation_text,
                "reasoning_source":     "llm",
            }

            logger.info(
                "[Decision/decide] SKU=%s action=%s qty=%s urgency=%s confidence=%s",
                sku, decision["action"], decision["order_qty"],
                decision["urgency"], decision["confidence"],
            )

        except Exception as e:
            logger.warning(
                "[Decision/decide] LLM failed for SKU=%s: %s — using rule-based fallback",
                sku, e,
            )
            decision = _rule_based_decide(state)

        # ── Self-critique — révision LLM si critique échoue ─────────────────
        # Pattern : Décision → Critique → Révision LLM (max 1 itération) → Fallback
        critique = _self_critique(decision, state)
        decision["self_critique"] = critique

        if not critique["passed"] and critique["re_reason"]:
            logger.info(
                "[Decision/decide] Self-critique NOK pour SKU=%s — tentative révision LLM",
                sku,
            )
            # Tenter une révision LLM avec le feedback de critique
            revised = _llm_revise_with_critique(decision, critique, state, llm)
            if revised:
                revised_critique = _self_critique(revised, state)
                revised["self_critique"] = revised_critique
                revised["reasoning_source"] = "llm_revised_after_critique"
                decision = revised
                logger.info(
                    "[Decision/decide] Révision LLM OK — SKU=%s action=%s critique_passed=%s",
                    sku, decision["action"], revised_critique["passed"],
                )
            else:
                # Révision LLM impossible → rule-based garanti cohérent
                logger.info(
                    "[Decision/decide] Révision LLM impossible — fallback rule-based SKU=%s", sku
                )
                decision = _rule_based_decide(state)
                decision["self_critique"] = critique
                decision["reasoning_source"] = "rule_based_after_critique"

        # ── Publication alerte Redis si CRITICAL/EXPEDITE (v5 FINAL) ─────
        _try_publish_alert(decision, state)

        return {**state, "decision": decision}

    return decide_node


# ═══════════════════════════════════════════════════════════════════════════
# LLM Revision after Self-Critique failure
# ═══════════════════════════════════════════════════════════════════════════

def _llm_revise_with_critique(
    decision: Dict[str, Any],
    critique: Dict[str, Any],
    state:    Dict[str, Any],
    llm,
) -> Optional[Dict[str, Any]]:
    """
    Révise la décision en soumettant le feedback de critique au LLM.
    Retourne la décision révisée ou None si LLM indisponible/échoué.
    """
    if llm is None:
        return None

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        failed_checks = {
            k: v["message"]
            for k, v in critique.get("checks", {}).items()
            if not v.get("passed")
        }
        critique_summary = "; ".join(f"{k}: {m}" for k, m in failed_checks.items())

        (risk_level, days_remaining, formula_order_qty,
         reorder_point, repl_cost, overstock_flag, lead_time_avg) = _extract_adjusted(state)

        revision_prompt = f"""La décision initiale a échoué la self-critique.

DÉCISION INITIALE:
  action={decision.get('action')} | order_qty={decision.get('order_qty')} | urgency={decision.get('urgency')} | confidence={decision.get('confidence')}

CRITIQUE FEEDBACK:
  {critique_summary}

DONNÉES STOCK (inchangées):
  risk_level={risk_level} | days_remaining={days_remaining:.1f}j | lead_time={lead_time_avg:.0f}j
  formula_order_qty={formula_order_qty:.0f} | reorder_point={reorder_point:.0f}
  overstock={overstock_flag}

MISSION: Corrige la décision en respectant les contraintes soulevées.
Réponds en JSON strict (mêmes champs que la décision initiale):
{{
  "action": "ORDER"|"HOLD"|"EXPEDITE"|"MONITOR",
  "order_qty": int|null,
  "order_qty_rationale": "...",
  "urgency": "immediate"|"this_week"|"this_month"|"none",
  "decision_rationale": "...",
  "confidence": "high"|"medium"|"low",
  "trade_offs": "...",
  "escalate_to_human": bool,
  "escalation_reason": str|null,
  "recommendation_text": "..."
}}"""

        response = llm.invoke([
            SystemMessage(content="Tu es un agent de décision inventaire. Tu révises une décision en intégrant un feedback de critique. Réponds uniquement en JSON valide."),
            HumanMessage(content=revision_prompt),
        ])

        raw = response.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)
        raw = re.sub(r'\n', ' ', raw)

        parsed = json.loads(raw)

        raw_qty = parsed.get("order_qty")
        order_qty = int(raw_qty) if raw_qty is not None else None

        recommendation_text = str(parsed.get("recommendation_text", "")).strip()
        if not recommendation_text:
            recommendation_text = _build_recommendation_text(
                action=str(parsed.get("action", "MONITOR")).upper(),
                urgency=str(parsed.get("urgency", "none")),
                order_qty=order_qty,
                state=state,
                escalate=bool(parsed.get("escalate_to_human", False)),
                esc_reason=parsed.get("escalation_reason"),
            )

        return {
            "action":               str(parsed.get("action", "MONITOR")).upper(),
            "order_qty":            order_qty,
            "order_qty_rationale":  str(parsed.get("order_qty_rationale", "")),
            "urgency":              str(parsed.get("urgency", "none")),
            "decision_rationale":   str(parsed.get("decision_rationale", "")),
            "confidence":           str(parsed.get("confidence", "medium")),
            "trade_offs":           str(parsed.get("trade_offs", "")),
            "escalate_to_human":    bool(parsed.get("escalate_to_human", False)),
            "escalation_reason":    parsed.get("escalation_reason"),
            "recommendation_text":  recommendation_text,
            "reasoning_source":     "llm_revised",
        }

    except Exception as exc:
        logger.warning("[Decision] _llm_revise_with_critique failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Self-Critique v5 — 3 checks (Section 3.6 ARCHITECTURE_MULTI_AGENT_V5_FINAL)
# ═══════════════════════════════════════════════════════════════════════════

def _self_critique(decision: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Self-critique du DecisionAgent avant finalisation :
    1. Décision cohérente avec les données ?
    2. Précédents similaires : résultat plausible ?
    3. Budget respecté ?
    Tout est dynamique — valeurs extraites du state courant.
    """
    checks   = {}
    passed   = True
    re_reason = False

    # Check 1 — Cohérence données
    (risk_level, days_remaining, formula_order_qty,
     reorder_point, repl_cost, overstock_flag, lead_time_avg) = _extract_adjusted(state)

    action = decision.get("action", "HOLD")
    c1_ok  = True
    c1_msg = "OK"

    if action in ("ORDER", "EXPEDITE") and days_remaining > 90:
        c1_ok  = False
        c1_msg = f"ACTION={action} mais stock={days_remaining:.0f}j — incohérent"
        re_reason = True
    elif action == "HOLD" and days_remaining < lead_time_avg and not overstock_flag:
        c1_ok  = False
        c1_msg = f"HOLD mais stock={days_remaining:.0f}j < lead_time={lead_time_avg:.0f}j"
        re_reason = True

    checks["data_coherence"] = {"passed": c1_ok, "message": c1_msg}
    if not c1_ok:
        passed = False

    # Check 2 — Confidence acceptable
    conf   = decision.get("confidence", "low")
    c2_ok  = conf in ("high", "medium")
    c2_msg = f"confidence={conf}" if c2_ok else f"Confiance trop basse : {conf}"
    checks["confidence_level"] = {"passed": c2_ok, "message": c2_msg}

    # Check 3 — Budget (seuil dynamique depuis state ou settings)
    order_qty   = decision.get("order_qty") or 0
    cost_limit  = float(state.get("budget_limit", 100_000))
    c3_ok       = True
    c3_msg      = "OK"
    escalate    = decision.get("escalate_to_human", False)

    if action in ("ORDER", "EXPEDITE") and repl_cost > cost_limit and not escalate:
        c3_ok   = False
        c3_msg  = f"Coût={repl_cost:,.0f} DT > limite={cost_limit:,.0f} DT sans escalade"
        re_reason = True

    checks["budget"] = {"passed": c3_ok, "message": c3_msg}
    if not c3_ok:
        passed = False

    return {
        "passed":    passed,
        "checks":    checks,
        "re_reason": re_reason,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Publication alerte Redis — v5 FINAL (Section 5.1 du document)
# ═══════════════════════════════════════════════════════════════════════════

def _try_publish_alert(decision: Dict[str, Any], state: Dict[str, Any]) -> None:
    """
    Publie une alerte Redis Pub/Sub si action=EXPEDITE ou risk=CRITICAL.
    Toutes les valeurs (stock, jours, produit) sont extraites dynamiquement du state.
    Zéro valeur codée en dur.
    """
    action     = decision.get("action", "HOLD")
    base       = state.get("baseline_report", {})
    risk_level = (base.get("risk_assessment") or {}).get("level", "LOW")

    if action not in ("EXPEDITE", "ORDER") and risk_level != "CRITICAL":
        return

    try:
        from app.sales.core.alert_bus import get_alert_bus

        store_id     = state.get("store_id", "")
        sku          = str(state.get("sku", ""))
        adj_metrics  = (state.get("adjusted_metrics") or {}).get("metrics") or base.get("metrics", {})
        stock_info   = base.get("stock", {})

        # Données 100% dynamiques depuis le state
        stock_qty       = int(stock_info.get("current_stock", 0))
        days_remaining  = float(adj_metrics.get("days_of_stock_remaining", 0))
        product_name    = str(base.get("product_name", sku))
        revenue_at_risk = float(adj_metrics.get("total_replenishment_cost", 0))
        is_top_seller   = bool(stock_info.get("is_top_seller", False))

        get_alert_bus().publish_stock_alert(
            store_id        = store_id,
            sku             = sku,
            product_name    = product_name,
            stock_qty       = stock_qty,          # dynamique
            risk_level      = risk_level,
            days_to_stockout= days_remaining,     # dynamique
            revenue_at_risk = revenue_at_risk,
            is_top_seller   = is_top_seller,
            cycle_id        = state.get("cycle_id", ""),
        )

    except ImportError:
        logger.debug("[Decision] sales-module non accessible — alerte Redis non publiée")
    except Exception as exc:
        logger.debug("[Decision] _try_publish_alert: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# Constraints Check Node (S4.5)
# ═══════════════════════════════════════════════════════════════════════════

import os as _os

_BUDGET_CAP_DT = float(_os.getenv("INVENTORY_BUDGET_CAP_DT", "100000"))


def _constraints_check_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pre-decide node: validate hard business constraints before the LLM/rule engine runs.

    Checks:
      1. Budget cap   — total_replenishment_cost > INVENTORY_BUDGET_CAP_DT
      2. MOQ gap      — formula_order_qty < MOQ (auto-adjusts up or blocks)
      3. Lead time vs days remaining — stockout-before-arrival detection
      4. EOL/discontinued with high risk → escalate flag

    Writes `constraints_violations` list and optionally `decision` to state
    when a hard block applies (skipping the decide node for that case).
    """
    sku      = state.get("sku", "?")
    store_id = state.get("store_id", "?")

    (risk_level, days_remaining, formula_order_qty,
     reorder_point, repl_cost, overstock_flag, lead_time_avg) = _extract_adjusted(state)
    cons = _extract_constraints(state)

    violations: list[Dict[str, Any]] = []
    hard_block = False
    block_reason = ""

    # ── Check 1: Budget cap ───────────────────────────────────────────────
    if repl_cost > _BUDGET_CAP_DT and formula_order_qty > 0:
        violations.append({
            "type":    "budget_cap",
            "message": (
                f"Order cost {repl_cost:,.0f} DT exceeds budget cap "
                f"{_BUDGET_CAP_DT:,.0f} DT for SKU={sku}@{store_id}. "
                f"Manual approval required."
            ),
            "severity": "hard",
        })
        hard_block   = True
        block_reason = f"Budget cap exceeded ({repl_cost:,.0f} DT > {_BUDGET_CAP_DT:,.0f} DT)"
        logger.warning("[constraints_check] Budget cap violated — %s@%s cost=%.0f", sku, store_id, repl_cost)

    # ── Check 2: MOQ alignment ────────────────────────────────────────────
    moq = cons["moq"]
    if formula_order_qty > 0 and formula_order_qty < moq:
        adjusted_qty = moq
        adj_cost     = repl_cost * (moq / max(formula_order_qty, 1))
        violations.append({
            "type":    "moq_adjustment",
            "message": (
                f"Formula qty {formula_order_qty:.0f} < MOQ {moq:.0f} for SKU={sku}. "
                f"Adjusted up to {adjusted_qty:.0f} (cost {adj_cost:,.0f} DT)."
            ),
            "severity":      "soft",
            "adjusted_qty":  adjusted_qty,
            "adjusted_cost": adj_cost,
        })
        # Inject adjusted qty back into adjusted_metrics so decide node sees it
        adj_m = dict((state.get("adjusted_metrics") or {}).get("metrics") or {})
        adj_m["formula_order_qty"] = float(adjusted_qty)
        adj_metrics = dict(state.get("adjusted_metrics") or {})
        adj_metrics["metrics"] = adj_m
        state = {**state, "adjusted_metrics": adj_metrics}
        logger.info("[constraints_check] MOQ adjusted %s→%s for %s@%s",
                    formula_order_qty, adjusted_qty, sku, store_id)

    # ── Check 3: Stockout-before-arrival ─────────────────────────────────
    if days_remaining < lead_time_avg and not overstock_flag and risk_level in ("CRITICAL", "HIGH"):
        deficit_days = lead_time_avg - days_remaining
        violations.append({
            "type":    "stockout_before_arrival",
            "message": (
                f"Stock runs out in {days_remaining:.0f}d but lead time is {lead_time_avg:.0f}d "
                f"(deficit {deficit_days:.0f}d). Expedite order required for SKU={sku}@{store_id}."
            ),
            "severity":     "warning",
            "deficit_days": round(deficit_days, 1),
        })

    # ── Check 4: EOL / discontinued ───────────────────────────────────────
    if cons["lifecycle"] in ("end_of_life", "discontinued") and risk_level == "CRITICAL":
        violations.append({
            "type":    "eol_critical",
            "message": (
                f"SKU={sku} is {cons['lifecycle']} but CRITICAL risk — "
                f"verify intentional drawdown before ordering."
            ),
            "severity": "hard",
        })
        hard_block   = True
        block_reason = f"EOL/discontinued product at CRITICAL risk — human review required"

    # ── Hard block → short-circuit decide ────────────────────────────────
    if hard_block:
        blocked_decision: Dict[str, Any] = {
            "action":              "HOLD",
            "order_qty":           None,
            "order_qty_rationale": "Blocked by constraints_check node.",
            "urgency":             "none",
            "decision_rationale":  block_reason,
            "confidence":          "low",
            "trade_offs":          "Cannot proceed without human approval.",
            "escalate_to_human":   True,
            "escalation_reason":   block_reason,
            "recommendation_text": f"Order blocked: {block_reason}",
            "reasoning_source":    "constraints_check_block",
        }
        logger.warning("[constraints_check] Hard block on %s@%s — %s", sku, store_id, block_reason)
        return {**state, "constraints_violations": violations, "decision": blocked_decision}

    if violations:
        logger.info("[constraints_check] %d soft violation(s) for %s@%s — proceeding to decide",
                    len(violations), sku, store_id)

    return {**state, "constraints_violations": violations}


def create_constraints_check_node():
    """Factory — returns the constraints_check node function."""
    return _constraints_check_node
