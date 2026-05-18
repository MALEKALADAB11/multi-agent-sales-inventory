"""
nodes.py — Agent Stratège avec RAG Milvus intégré.
Remplace sales-module/modules/coaching/agents/stratege/nodes.py
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


# ── LLM ──────────────────────────────────────────────────────────────────────

def get_llm() -> ChatOllama:
    return ChatOllama(
        model    = os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature = 0.2,
        num_predict = 500,
        num_ctx     = 3000,  # Augmenté pour le RAG context
    )


# ── NODE 1 — Fetch contexte externe ──────────────────────────────────────────

async def node_fetch_context(state: SalesAgentState) -> dict:
    pos_data = state.get("pos_data", {})
    store_id = pos_data.get("store_id", "OOR_LAC_01")

    logger.info(f"[STRATEGE] Node 1 — Fetch contexte externe pour {store_id}")
    context = await fetch_full_context(store_id)

    logger.info(
        f"[STRATEGE] Node 1 — Contexte: "
        f"{context['summary'].get('weather_icon', '')} "
        f"{context['summary'].get('weather_label', '')} | "
        f"Férié: {context['summary'].get('is_holiday', False)} | "
        f"Promos: {context['summary'].get('active_promos', 0)}"
    )

    return {"external_context": context}


# ── NODE 2 — Analyse cause racine ─────────────────────────────────────────────

async def node_analyze_root_cause(state: SalesAgentState) -> dict:
    gap_pct  = state.get("gap_objectif", 0.0)
    context  = state.get("external_context", {})
    summary  = context.get("summary", {})
    factors  = []

    weather_effect = summary.get("weather_effect", 0)
    if weather_effect <= -0.20:
        factors.append(f"Météo défavorable ({summary.get('weather_label', '')} — impact trafic {weather_effect:.0%})")
    elif weather_effect <= -0.10:
        factors.append(f"Météo légèrement défavorable ({summary.get('weather_label', '')})")

    if summary.get("is_holiday"):
        factors.append(f"Jour férié: {summary.get('holiday_name', '')} — comportement client atypique")

    if gap_pct > 40:
        factors.append(f"Gap structurel élevé ({gap_pct:.1f}%) — sous-performance des conseillers")
    elif gap_pct > 20:
        factors.append(f"Gap modéré ({gap_pct:.1f}%) — rythme de vente insuffisant")

    current_hour    = datetime.now().hour
    hours_remaining = max(0, 20 - current_hour)
    if current_hour >= 16 and gap_pct > 20:
        factors.append(f"Pression temporelle — seulement {hours_remaining}h restantes")

    active_promos = summary.get("active_promos", 0)
    if active_promos > 0 and gap_pct > 15:
        factors.append(f"{active_promos} promotion(s) Ooredoo actives non suffisamment exploitées")

    cause_racine = factors[0] if factors else f"Gap de {gap_pct:.1f}% sans facteur externe majeur"

    logger.info(f"[STRATEGE] Node 2 — Cause racine: {cause_racine}")
    logger.info(f"[STRATEGE] Node 2 — Facteurs: {factors}")

    return {"root_cause": cause_racine, "context_factors": factors}


# ── NODE 3 — Génération stratégie LLM + RAG ───────────────────────────────────

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
    store_id        = pos_data.get("store_id", "I63")

    # ── RAG: récupérer les scripts pertinents ─────────────────────────────────
    rag_context = ""
    try:
        from data.rag_retriever import get_rag_context
        rag_result = await get_rag_context(
            store_id        = store_id,
            gap_pct         = gap_pct,
            current_hour    = current_hour,
            urgency         = urgency_level,
            context_summary = summary,
        )
        if rag_result["available"] and rag_result["scripts"]:
            rag_context = rag_result["rag_context"]
            logger.info(f"[STRATEGE] RAG: {len(rag_result['scripts'])} scripts récupérés")
        else:
            logger.info("[STRATEGE] RAG: non disponible — mode standard")
    except Exception as e:
        logger.warning(f"[STRATEGE] RAG erreur: {e}")

    # ── Construire le prompt ──────────────────────────────────────────────────
    analyst_data = json.dumps({
        "urgency_level":   urgency_level,
        "gap_pct":         gap_pct,
        "gap_amount_tnd":  gap_amount,
        "ca_actuel_tnd":   pos_data.get("current_revenue", 0),
        "objectif_tnd":    pos_data.get("daily_target", 1007),
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
        "holiday_name":     holidays.get("today_holiday", {}).get("name", "") if holidays.get("is_holiday_today") else "",
        "next_holiday":     holidays.get("next_holiday", {}).get("name", "") if holidays.get("next_holiday") else "",
        "days_until_next":  holidays.get("next_holiday", {}).get("days_until", 0) if holidays.get("next_holiday") else 0,
    }, indent=2, ensure_ascii=False)

    events_data = json.dumps({
        "active_promotions": [{"title": e.get("title"), "price": e.get("price", "")} for e in events.get("promotions", [])[:2]],
        "new_offers":        [{"title": e.get("title")} for e in events.get("new_offers", [])[:2]],
        "total_active":      len(events.get("active", [])),
    }, indent=2, ensure_ascii=False)

    user_msg = STRATEGE_USER_PROMPT.format(
        analyst_data    = analyst_data,
        weather_data    = weather_data,
        holidays_data   = holidays_data,
        events_data     = events_data,
        current_time    = datetime.now().strftime("%H:%M"),
        hours_remaining = hours_remaining,
    )

    # Ajouter le contexte RAG au prompt si disponible
    if rag_context:
        user_msg = rag_context + "\n\n" + user_msg

    # ── Appel LLM ─────────────────────────────────────────────────────────────
    llm            = get_llm()
    strategie_data = {}

    try:
        logger.info("[STRATEGE] Node 3 — Appel LLM pour stratégie...")
        response = await llm.ainvoke([
            SystemMessage(content=STRATEGE_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])

        content = response.content.strip()

        if "```" in content:
            parts   = content.split("```")
            content = parts[1] if len(parts) > 1 else parts[0]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        try:
            strategie_data = json.loads(content)
            logger.info(f"[STRATEGE] Node 3 — Stratégie JSON OK: {len(strategie_data.get('actions', []))} actions")
        except json.JSONDecodeError:
            logger.warning("[STRATEGE] JSON tronqué — extraction regex")
            strategie_data = _extract_from_partial_json(content)
            if not strategie_data.get("actions"):
                strategie_data = _make_fallback_strategy(gap_pct, urgency_level, summary, hours_remaining)
            logger.info(f"[STRATEGE] Node 3 — Stratégie (regex/fallback): {len(strategie_data.get('actions', []))} actions")

    except (Exception, BaseException) as e:
        logger.warning(f"[STRATEGE] LLM erreur: {e}")
        strategie_data = _make_fallback_strategy(gap_pct, urgency_level, summary, hours_remaining)

    # Sauvegarder en PostgreSQL pour enrichir le RAG futur
    _save_coaching_card(state, strategie_data, rag_context)

    return {"strategie_data": strategie_data}


# ── NODE 4 — Construction payload final ───────────────────────────────────────

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

    if not strategie_data.get("actions"):
        logger.warning("[STRATEGE] Node 4 — Pas d'actions → fallback")
        strategie_data = _make_fallback_strategy(gap_pct, urgency_level, summary, hours_remaining)

    actions           = strategie_data.get("actions", [])
    focus_produits    = strategie_data.get("focus_produits", [])
    message_manager   = strategie_data.get("message_manager", "")
    strategie_summary = strategie_data.get("strategie_summary", "")
    cause_racine      = strategie_data.get("cause_racine", root_cause)

    if not strategie_summary:
        strategie_summary = (
            f"Gap de {gap_pct:.1f}% — {cause_racine}. "
            f"Focus : {', '.join(focus_produits[:2]) if focus_produits else 'produits premium'}."
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


# ── Sauvegarder en PostgreSQL pour enrichir le RAG ───────────────────────────

def _save_coaching_card(state: dict, strategie_data: dict, rag_context: str):
    """Sauvegarde la carte de coaching dans PostgreSQL (table coaching_cards)."""
    try:
        import psycopg2, os
        DB_CONFIG = {
            "host": "localhost", "port": 5432,
            "dbname": "ooredoo_sales",
            "user": "postgres", "password": "admin",
        }
        os.environ['PGCLIENTENCODING'] = 'UTF8'
        conn = psycopg2.connect(**DB_CONFIG)
        conn.set_client_encoding('UTF8')

        pos_data  = state.get("pos_data", {})
        gap_pct   = state.get("gap_objectif", 0.0)
        gap_amt   = state.get("gap_amount", 0.0)
        feo       = state.get("forecast_eod", 0.0)
        urgency   = state.get("urgency_level", "MEDIUM")
        store_id  = pos_data.get("store_id", "I63")
        actions   = strategie_data.get("actions", [])
        cause     = strategie_data.get("cause_racine", "")
        strategie = strategie_data.get("strategie_summary", "")

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO coaching_cards
                    (store_id, cycle_id, urgence, gap_pct, gap_amount,
                     forecast_eod, analyst_summary, strategie, actions,
                     cause_racine, contexte, statut)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                store_id,
                f"cycle-{datetime.now().strftime('%H%M%S')}",
                urgency,
                round(gap_pct, 2),
                round(gap_amt, 2),
                round(float(feo), 2),
                state.get("analyst_summary", "")[:500],
                strategie[:500] if strategie else "",
                json.dumps(actions, ensure_ascii=False),
                cause[:300] if cause else "",
                json.dumps({"rag_used": bool(rag_context), "rag_preview": rag_context[:200]}, ensure_ascii=False),
                "GENERATED",
            ))
        conn.commit()
        conn.close()
        logger.info("[STRATEGE] Coaching card sauvegardée en PostgreSQL")
    except Exception as e:
        logger.warning(f"[STRATEGE] Save coaching card échoué: {e}")


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _extract_from_partial_json(content: str) -> dict:
    result = {}
    m = re.search(r'"cause_racine"\s*:\s*"([^"]+)"', content)
    if m: result["cause_racine"] = m.group(1)
    m = re.search(r'"strategie_summary"\s*:\s*"([^"]+)"', content)
    if m: result["strategie_summary"] = m.group(1)
    m = re.search(r'"message_manager"\s*:\s*"([^"]+)"', content)
    if m: result["message_manager"] = m.group(1)
    fp = re.search(r'"focus_produits"\s*:\s*\[([^\]]+)\]', content)
    if fp: result["focus_produits"] = re.findall(r'"([^"]+)"', fp.group(1))
    actions_raw = re.findall(
        r'"action"\s*:\s*"([^"]+)"[^}]*?"produit_cible"\s*:\s*"([^"]+)"',
        content, re.DOTALL
    )
    if actions_raw:
        result["actions"] = [
            {"priorite": i+1, "action": a[0], "produit_cible": a[1], "argument_vente": "", "impact_estime": ""}
            for i, a in enumerate(actions_raw[:3])
        ]
    return result


def _make_fallback_strategy(gap_pct, urgency, summary, hours_remaining) -> dict:
    weather_label  = summary.get("weather_label", "")
    weather_effect = summary.get("weather_effect", 0)
    active_promos  = summary.get("active_promos", 0)
    actions = []
    if weather_effect <= -0.15:
        actions.append({"priorite": 1, "action": "Focus ventes en boutique — conditions extérieures défavorables", "produit_cible": "Forfaits & Services", "argument_vente": f"Météo {weather_label} — clients captifs, proposer upgrades forfait", "impact_estime": "+15% conversions sur forfaits premium"})
    else:
        actions.append({"priorite": 1, "action": "Accueil proactif et démonstration produits phares", "produit_cible": "Smartphones & Box Fibre", "argument_vente": "Conditions favorables — maximiser ventes à forte valeur", "impact_estime": "+20% sur CA moyen par transaction"})
    if active_promos > 0:
        actions.append({"priorite": 2, "action": f"Exploiter les {active_promos} promotion(s) Ooredoo actives", "produit_cible": "Bundle Smartphone + Forfait", "argument_vente": "Offre combinée exclusive — économie garantie client", "impact_estime": f"+{gap_pct/max(hours_remaining,1):.0f}% CA/heure nécessaire"})
    else:
        actions.append({"priorite": 2, "action": f"Rattraper le gap de {gap_pct:.0f}% sur les heures restantes", "produit_cible": "Bundle Smartphone + Forfait", "argument_vente": "Offre combinée exclusive boutique — économie garantie", "impact_estime": f"+{gap_pct/max(hours_remaining,1):.0f}% CA/heure nécessaire"})
    if urgency == "HIGH":
        actions.append({"priorite": 3, "action": "Relance clients en attente et suivi pipeline", "produit_cible": "Tous produits", "argument_vente": "Contacter clients indécis de la journée — urgence fermeture", "impact_estime": "Récupération 2-3 ventes additionnelles"})
    return {
        "cause_racine":         f"Gap de {gap_pct:.1f}% — {weather_label or 'performance insuffisante'}",
        "facteurs_contextuels": [weather_label] if weather_label else [],
        "actions":              actions,
        "focus_produits":       ["Forfaits Premium", "Bundle Smartphone+Forfait"],
        "message_manager":      f"Gap {gap_pct:.0f}% — {hours_remaining}h restantes. Action immédiate requise.",
        "strategie_summary":    f"Gap de {gap_pct:.1f}% avec {hours_remaining}h restantes. Focus produits à forte valeur et bundles exclusifs.",
    }


def _build_context_signals(context, factors) -> list:
    signals = []
    summary  = context.get("summary", {})
    holidays = context.get("holidays", {})
    events   = context.get("events", {})
    weather_effect = summary.get("weather_effect", 0)
    if weather_effect <= -0.10:
        signals.append({"type": "weather", "level": "high" if weather_effect <= -0.25 else "med", "label": f"{summary.get('weather_icon', '')} {summary.get('weather_label', '')} — impact trafic {weather_effect:+.0%}", "value": weather_effect})
    else:
        signals.append({"type": "weather", "level": "low", "label": f"{summary.get('weather_icon', '☀️')} {summary.get('weather_label', 'Beau temps')} — favorable", "value": weather_effect})
    if holidays.get("is_holiday_today"):
        signals.append({"type": "holiday", "level": "high", "label": f"🎉 Jour férié: {holidays.get('today_holiday', {}).get('name', '')}", "value": 1})
    all_promos = events.get("promotions", []) + events.get("new_offers", [])
    if all_promos:
        signals.append({"type": "event", "level": "med", "label": f"🎯 {len(all_promos)} offre(s) active(s) Ooredoo", "value": 0.3})
    return signals