"""
Nodes LangGraph de l'Agent Stratège.
"""
import json
import logging
import os
import re
from datetime import datetime

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from core.config import get_config
from core.state import SalesAgentState
from modules.coaching.agents.stratege.tools import fetch_full_context
from modules.coaching.agents.stratege.prompts import (
    STRATEGE_SYSTEM_PROMPT,
    STRATEGE_USER_PROMPT,
)

logger = logging.getLogger(__name__)
config = get_config()


# ─────────────────────────────────────────────────────────────────
# LLM
# ─────────────────────────────────────────────────────────────────

def get_llm() -> ChatOllama:
    return ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0.2,
        num_predict=400,
        num_ctx=2048,
    )


# ─────────────────────────────────────────────────────────────────
# NODE 1 — Fetch contexte externe
# ─────────────────────────────────────────────────────────────────

async def node_fetch_context(state: SalesAgentState) -> dict:
    pos_data = state.get("pos_data", {})
    store_id = pos_data.get("store_id", "OOR_LAC_01")

    logger.info(f"[STRATEGE] Node 1 — Fetch contexte externe pour {store_id}")

    context = await fetch_full_context(store_id)

    logger.info(
        f"[STRATEGE] Node 1 — Contexte: "
        f"{context['summary']['weather_icon']} "
        f"{context['summary']['weather_label']} | "
        f"Férié: {context['summary']['is_holiday']} | "
        f"Promos: {context['summary']['active_promos']}"
    )

    return {"external_context": context}


# ─────────────────────────────────────────────────────────────────
# NODE 2 — Analyse cause racine
# ─────────────────────────────────────────────────────────────────

async def node_analyze_root_cause(state: SalesAgentState) -> dict:
    pos_data      = state.get("pos_data", {})
    gap_pct       = state.get("gap_objectif", 0.0)
    urgency_level = state.get("urgency_level", "MEDIUM")
    context       = state.get("external_context", {})
    summary       = context.get("summary", {})

    factors = []

    # ── Météo ─────────────────────────────────────────────
    weather_effect = summary.get("weather_effect", 0)
    if weather_effect <= -0.20:
        factors.append(
            f"Météo défavorable ({summary.get('weather_label', '')} "
            f"— impact trafic {weather_effect:.0%})"
        )
    elif weather_effect <= -0.10:
        factors.append(
            f"Météo légèrement défavorable "
            f"({summary.get('weather_label', '')})"
        )

    # ── Jour férié ────────────────────────────────────────
    if summary.get("is_holiday"):
        factors.append(
            f"Jour férié: {summary.get('holiday_name', '')} "
            f"— comportement client atypique"
        )

    # ── Gap ───────────────────────────────────────────────
    if gap_pct > 40:
        factors.append(
            f"Gap structurel élevé ({gap_pct:.1f}%) "
            f"— sous-performance des conseillers"
        )
    elif gap_pct > 20:
        factors.append(
            f"Gap modéré ({gap_pct:.1f}%) "
            f"— rythme de vente insuffisant"
        )

    # ── Pression temporelle ───────────────────────────────
    current_hour    = datetime.now().hour
    hours_remaining = max(0, 20 - current_hour)
    if current_hour >= 16 and gap_pct > 20:
        factors.append(
            f"Pression temporelle — seulement {hours_remaining}h restantes"
        )

    # ── Promos non exploitées ─────────────────────────────
    active_promos = summary.get("active_promos", 0)
    if active_promos > 0 and gap_pct > 15:
        factors.append(
            f"{active_promos} promotion(s) Ooredoo actives "
            f"non suffisamment exploitées"
        )

    cause_racine = factors[0] if factors else (
        f"Gap de {gap_pct:.1f}% sans facteur externe majeur"
    )

    logger.info(f"[STRATEGE] Node 2 — Cause racine: {cause_racine}")
    logger.info(f"[STRATEGE] Node 2 — Facteurs: {factors}")

    return {
        "root_cause":      cause_racine,
        "context_factors": factors,
    }


# ─────────────────────────────────────────────────────────────────
# NODE 3 — Génération stratégie LLM
# ─────────────────────────────────────────────────────────────────

async def node_generate_strategy(state: SalesAgentState) -> dict:
    pos_data        = state.get("pos_data", {})
    gap_pct         = state.get("gap_objectif", 0.0)
    gap_amount      = state.get("gap_amount", 0.0)
    urgency_level   = state.get("urgency_level", "MEDIUM")
    analyst_summary = state.get("analyst_summary", "")
    context         = state.get("external_context", {})
    root_cause      = state.get("root_cause", "")
    factors         = state.get("context_factors", [])

    current_hour    = datetime.now().hour
    hours_remaining = max(0, 20 - current_hour)
    summary         = context.get("summary", {})
    weather         = context.get("weather", {})
    holidays        = context.get("holidays", {})
    events          = context.get("events", {})

    # ── Construire le prompt ──────────────────────────────
    analyst_data = json.dumps({
        "urgency_level":   urgency_level,
        "gap_pct":         gap_pct,
        "gap_amount_tnd":  gap_amount,
        "ca_actuel_tnd":   pos_data.get("current_revenue", 0),
        "objectif_tnd":    pos_data.get("daily_target", 18000),
        "analyst_summary": analyst_summary[:200],
        "root_cause":      root_cause,
        "context_factors": factors,
    }, indent=2, ensure_ascii=False)

    weather_data = json.dumps({
        "label":        summary.get("weather_label", ""),
        "icon":         summary.get("weather_icon", ""),
        "effet_trafic": f"{summary.get('weather_effect', 0):+.0%}",
        "rain_hours":   summary.get("rain_hours", [])[:3],
        "best_hours":   summary.get("best_hours", [])[:3],
        "temperature":  weather.get("current", {}).get("temperature", 0),
    }, indent=2, ensure_ascii=False)

    holidays_data = json.dumps({
        "is_holiday_today": holidays.get("is_holiday_today", False),
        "holiday_name":     (
            holidays.get("today_holiday", {}).get("name", "")
            if holidays.get("is_holiday_today") else ""
        ),
        "next_holiday":     (
            holidays.get("next_holiday", {}).get("name", "")
            if holidays.get("next_holiday") else ""
        ),
        "days_until_next":  (
            holidays.get("next_holiday", {}).get("days_until", 0)
            if holidays.get("next_holiday") else 0
        ),
    }, indent=2, ensure_ascii=False)

    events_data = json.dumps({
        "active_promotions": [
            {"title": e.get("title"), "price": e.get("price", "")}
            for e in events.get("promotions", [])[:2]
        ],
        "new_offers": [
            {"title": e.get("title")}
            for e in events.get("new_offers", [])[:2]
        ],
        "total_active": len(events.get("active", [])),
    }, indent=2, ensure_ascii=False)

    user_msg = STRATEGE_USER_PROMPT.format(
        analyst_data    = analyst_data,
        weather_data    = weather_data,
        holidays_data   = holidays_data,
        events_data     = events_data,
        current_time    = datetime.now().strftime("%H:%M"),
        hours_remaining = hours_remaining,
    )

    # ── Appel LLM ─────────────────────────────────────────
    llm            = get_llm()
    strategie_data = {}

    try:
        logger.info("[STRATEGE] Node 3 — Appel LLM pour stratégie...")
        response = await llm.ainvoke([
            SystemMessage(content=STRATEGE_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])

        content = response.content.strip()

        # Nettoyage markdown
        if "```" in content:
            parts   = content.split("```")
            content = parts[1] if len(parts) > 1 else parts[0]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        # ── Parse JSON complet ────────────────────────────
        try:
            strategie_data = json.loads(content)
            logger.info(
                f"[STRATEGE] Node 3 — Stratégie JSON OK: "
                f"{len(strategie_data.get('actions', []))} actions"
            )

        except json.JSONDecodeError:
            # ── JSON tronqué → regex ──────────────────────
            logger.warning("[STRATEGE] JSON tronqué — extraction regex")
            strategie_data = _extract_from_partial_json(content)

            if not strategie_data.get("actions"):
                logger.warning("[STRATEGE] Regex sans actions → fallback")
                strategie_data = _make_fallback_strategy(
                    gap_pct, urgency_level, summary, hours_remaining
                )

            logger.info(
                f"[STRATEGE] Node 3 — Stratégie (regex/fallback): "
                f"{len(strategie_data.get('actions', []))} actions"
            )

    except Exception as e:
        logger.warning(f"[STRATEGE] LLM erreur: {e}")
        strategie_data = _make_fallback_strategy(
            gap_pct, urgency_level, summary, hours_remaining
        )

    return {"strategie_data": strategie_data}


# ─────────────────────────────────────────────────────────────────
# NODE 4 — Construction payload final
# ─────────────────────────────────────────────────────────────────

async def node_build_output(state: SalesAgentState) -> dict:
    strategie_data  = state.get("strategie_data", {})
    context         = state.get("external_context", {})
    root_cause      = state.get("root_cause", "")
    factors         = state.get("context_factors", [])
    gap_pct         = state.get("gap_objectif", 0.0)
    urgency_level   = state.get("urgency_level", "MEDIUM")

    current_hour    = datetime.now().hour
    hours_remaining = max(0, 20 - current_hour)
    summary         = context.get("summary", {})

    # ── Fallback si pas d'actions ─────────────────────────
    if not strategie_data.get("actions"):
        logger.warning("[STRATEGE] Node 4 — Pas d'actions → fallback")
        strategie_data = _make_fallback_strategy(
            gap_pct, urgency_level, summary, hours_remaining
        )

    # ── Extraire les champs ───────────────────────────────
    actions           = strategie_data.get("actions", [])
    focus_produits    = strategie_data.get("focus_produits", [])
    message_manager   = strategie_data.get("message_manager", "")
    strategie_summary = strategie_data.get("strategie_summary", "")
    cause_racine      = strategie_data.get("cause_racine", root_cause)

    if not strategie_summary:
        strategie_summary = (
            f"Gap de {gap_pct:.1f}% — {cause_racine}. "
            f"Focus : "
            f"{', '.join(focus_produits[:2]) if focus_produits else 'produits premium'}."
        )

    heatmap         = context.get("heatmap", {})
    context_signals = _build_context_signals(context, factors)

    logger.info(f"[STRATEGE] Node 4 — Output construit")
    logger.info(f"[STRATEGE] Stratégie: {strategie_summary[:100]}")
    logger.info(f"[STRATEGE] Actions  : {len(actions)}")

    return {
        "strategie":         strategie_summary,
        "strategie_actions": actions,
        "focus_produits":    focus_produits,
        "message_manager":   message_manager,
        "cause_racine":      cause_racine,
        "context_heatmap":   heatmap,
        "context_signals":   context_signals,
        "external_context":  context,
        "route_to":          "coach",
    }


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _extract_from_partial_json(content: str) -> dict:
    """Extrait les champs clés d'un JSON potentiellement tronqué."""
    result = {}

    # cause_racine
    m = re.search(r'"cause_racine"\s*:\s*"([^"]+)"', content)
    if m: result["cause_racine"] = m.group(1)

    # strategie_summary
    m = re.search(r'"strategie_summary"\s*:\s*"([^"]+)"', content)
    if m: result["strategie_summary"] = m.group(1)

    # message_manager
    m = re.search(r'"message_manager"\s*:\s*"([^"]+)"', content)
    if m: result["message_manager"] = m.group(1)

    # focus_produits
    fp = re.search(r'"focus_produits"\s*:\s*\[([^\]]+)\]', content)
    if fp:
        result["focus_produits"] = re.findall(r'"([^"]+)"', fp.group(1))

    # actions
    actions_raw = re.findall(
        r'"action"\s*:\s*"([^"]+)"[^}]*?"produit_cible"\s*:\s*"([^"]+)"',
        content, re.DOTALL
    )
    if actions_raw:
        result["actions"] = [
            {
                "priorite":       i + 1,
                "action":         a[0],
                "produit_cible":  a[1],
                "argument_vente": "",
                "impact_estime":  "",
            }
            for i, a in enumerate(actions_raw[:3])
        ]

    return result


def _make_fallback_strategy(
    gap_pct: float,
    urgency: str,
    summary: dict,
    hours_remaining: int
) -> dict:
    """Stratégie de fallback sans LLM."""
    weather_label  = summary.get("weather_label", "")
    weather_effect = summary.get("weather_effect", 0)
    active_promos  = summary.get("active_promos", 0)

    actions = []

    # Action 1 — Météo
    if weather_effect <= -0.15:
        actions.append({
            "priorite":       1,
            "action":         "Focus ventes en boutique — conditions extérieures défavorables",
            "produit_cible":  "Forfaits & Services",
            "argument_vente": (
                f"Météo {weather_label} — clients captifs, "
                f"proposer upgrades forfait"
            ),
            "impact_estime":  "+15% conversions sur forfaits premium",
        })
    else:
        actions.append({
            "priorite":       1,
            "action":         "Accueil proactif et démonstration produits phares",
            "produit_cible":  "Smartphones & Box Fibre",
            "argument_vente": "Conditions favorables — maximiser ventes à forte valeur",
            "impact_estime":  "+20% sur CA moyen par transaction",
        })

    # Action 2 — Promotions ou gap
    if active_promos > 0:
        actions.append({
            "priorite":       2,
            "action":         f"Exploiter les {active_promos} promotion(s) Ooredoo actives",
            "produit_cible":  "Bundle Smartphone + Forfait",
            "argument_vente": "Offre combinée exclusive — économie garantie client",
            "impact_estime":  (
                f"+{gap_pct/max(hours_remaining,1):.0f}% CA/heure nécessaire"
            ),
        })
    else:
        actions.append({
            "priorite":       2,
            "action":         f"Rattraper le gap de {gap_pct:.0f}% sur les heures restantes",
            "produit_cible":  "Bundle Smartphone + Forfait",
            "argument_vente": "Offre combinée exclusive boutique — économie garantie",
            "impact_estime":  (
                f"+{gap_pct/max(hours_remaining,1):.0f}% CA/heure nécessaire"
            ),
        })

    # Action 3 — Urgence HIGH
    if urgency == "HIGH":
        actions.append({
            "priorite":       3,
            "action":         "Relance clients en attente et suivi pipeline",
            "produit_cible":  "Tous produits",
            "argument_vente": (
                "Contacter clients indécis de la journée — urgence fermeture"
            ),
            "impact_estime":  "Récupération 2-3 ventes additionnelles",
        })

    cause = (
        f"Gap de {gap_pct:.1f}% — "
        f"{weather_label or 'performance insuffisante'}"
    )
    summary_str = (
        f"Gap de {gap_pct:.1f}% avec {hours_remaining}h restantes. "
        f"Contexte : {weather_label or 'normal'}. "
        f"Focus produits à forte valeur et bundles exclusifs."
    )

    return {
        "cause_racine":        cause,
        "facteurs_contextuels": [weather_label] if weather_label else [],
        "actions":             actions,
        "focus_produits":      ["Forfaits Premium", "Bundle Smartphone+Forfait"],
        "message_manager":     (
            f"Gap {gap_pct:.0f}% — {hours_remaining}h restantes. "
            f"Action immédiate requise."
        ),
        "strategie_summary":   summary_str,
    }


def _build_context_signals(context: dict, factors: list) -> list:
    """Construit les signaux contextuels pour le frontend."""
    signals  = []
    summary  = context.get("summary", {})
    holidays = context.get("holidays", {})
    events   = context.get("events", {})

    # ── Météo ─────────────────────────────────────────────
    weather_effect = summary.get("weather_effect", 0)
    if weather_effect <= -0.10:
        signals.append({
            "type":  "weather",
            "level": "high" if weather_effect <= -0.25 else "med",
            "label": (
                f"{summary.get('weather_icon', '')} "
                f"{summary.get('weather_label', '')} "
                f"— impact trafic {weather_effect:+.0%}"
            ),
            "value": weather_effect,
        })
    else:
        signals.append({
            "type":  "weather",
            "level": "low",
            "label": (
                f"{summary.get('weather_icon', '☀️')} "
                f"{summary.get('weather_label', 'Beau temps')} — favorable"
            ),
            "value": weather_effect,
        })

    # ── Jours fériés ──────────────────────────────────────
    if holidays.get("is_holiday_today"):
        signals.append({
            "type":  "holiday",
            "level": "high",
            "label": (
                f"🎉 Jour férié: "
                f"{holidays.get('today_holiday', {}).get('name', '')}"
            ),
            "value": 1,
        })
    elif (
        holidays.get("next_holiday") and
        holidays["next_holiday"].get("days_until", 99) <= 3
    ):
        signals.append({
            "type":  "holiday",
            "level": "med",
            "label": (
                f"📅 Prochain férié: "
                f"{holidays['next_holiday']['name']} "
                f"dans {holidays['next_holiday']['days_until']}j"
            ),
            "value": 0.5,
        })

    # ── Stock critique ────────────────────────────────────
    signals.append({
        "type":  "stock",
        "level": "high",
        "label": "📦 iPhone 15 — stock critique (3 unités)",
        "value": -0.3,
    })

    # ── Promotions Ooredoo ────────────────────────────────
    all_promos = (
        events.get("promotions", []) +
        events.get("new_offers", [])
    )
    if all_promos:
        signals.append({
            "type":  "event",
            "level": "med",
            "label": (
                f"🎯 {len(all_promos)} offre(s) active(s) Ooredoo — "
                f"{all_promos[0].get('title', '')[:40]}"
            ),
            "value": 0.3,
        })

    return signals