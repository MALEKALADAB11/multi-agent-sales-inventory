"""
nodes.py — Agent Stratège v3
=============================
Flow : fetch_context → rag_search → analyze_context → generate_strategy → build_output

Le Stratège est le cerveau commercial :
  - Reçoit l'analyse de l'Analyste (gap, urgence, CA)
  - Enrichit avec le contexte externe (météo, fériés, promos)
  - Recherche les scripts RAG similaires dans Milvus
  - Génère 3 actions commerciales concrètes via LLM
  - Produit les alertes temps réel pour les conseillers
"""

import json
import logging
import os
import re
import time
import requests
from datetime import datetime

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from agent_logger import AgentLogger
from core.config import get_config
from core.state import SalesAgentState
from modules.coaching.agents.stratege.tools import fetch_full_context
from modules.coaching.agents.stratege.prompts import (
    STRATEGE_SYSTEM_PROMPT,
    STRATEGE_USER_PROMPT,
)

logger = logging.getLogger(__name__)
config = get_config()

OLLAMA_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL",    "llama3.2:latest")
MILVUS_URI   = "http://localhost:19530"
EMBED_DIM    = 768
COLLECTION   = "coaching_scripts"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cycle_id(state: dict) -> str:
    return (state.get("metrics") or {}).get("cycle_id", "") or state.get("cycle_id", "unknown")

def _store_id(state: dict) -> str:
    return (state.get("pos_data") or {}).get("store_id", "ALL") or state.get("store_id", "ALL")

def _update_metrics(state: dict, key: str, value) -> dict:
    metrics = dict(state.get("metrics") or {})
    metrics[key] = value
    metrics["nodes_executed"] = int(metrics.get("nodes_executed", 0)) + 1
    return metrics

def get_llm() -> ChatOllama:
    return ChatOllama(
        model=OLLAMA_MODEL, base_url=OLLAMA_URL,
        temperature=0.2, num_predict=600, num_ctx=3500,
    )


# ══════════════════════════════════════════════════════════════════════════════
# NODE 1 — Fetch Context (météo + fériés + événements)
# ══════════════════════════════════════════════════════════════════════════════

async def node_fetch_context(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("fetch_context", state)
    t0     = time.time()

    logger.info(f"[STRATEGE] Node 1 — Fetch contexte pour {sid}")

    try:
        context = await fetch_full_context(sid)
        summary = context.get("summary", {})
        logger.info(
            f"[STRATEGE] Node 1 — {summary.get('weather_icon','')} "
            f"{summary.get('weather_label','')} | "
            f"effet={summary.get('weather_effect',0):+.0%} | "
            f"férié={summary.get('is_holiday', False)} | "
            f"promos={summary.get('active_promos', 0)}"
        )
        duration = (time.time() - t0) * 1000
        output   = {**state, "external_context": context}
        log.node_done("fetch_context", log_id, output, duration,
                      {"weather": summary.get("weather_label", ""),
                       "effect": summary.get("weather_effect", 0)})
        return {**output, "metrics": _update_metrics(state, "stratege_context_ms", round(duration))}

    except Exception as e:
        duration = (time.time() - t0) * 1000
        log.node_error("fetch_context", log_id, e, state)
        logger.warning(f"[STRATEGE] Node 1 fallback: {e}")
        fallback_ctx = {
            "summary": {
                "weather_label": "Tunis", "weather_icon": "🌤️",
                "weather_effect": 0.0, "temperature": 22,
                "is_holiday": False, "active_promos": 0,
            },
            "holidays": {}, "events": {}, "heatmap": {}
        }
        return {**state, "external_context": fallback_ctx,
                "metrics": _update_metrics(state, "stratege_context_ms", round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 2 — RAG Search (Milvus cosine similarity)
# ══════════════════════════════════════════════════════════════════════════════

async def node_rag_search(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("rag_search", state)
    t0     = time.time()

    logger.info("[STRATEGE] Node 2 — RAG search Milvus")

    gap_pct  = float(state.get("gap_objectif", 0))
    urgency  = state.get("urgency_level", "MEDIUM")
    ext_ctx  = state.get("external_context") or {}
    summary  = ext_ctx.get("summary", {})
    hour     = datetime.now().hour
    dow      = datetime.now().weekday()
    is_rainy = float(summary.get("weather_effect", 0)) <= -0.10
    temp     = float(summary.get("temperature", 22))

    # Construire la requête RAG contextuelle
    parts = []
    if gap_pct > 60:   parts.append("gap critique très éloigné objectif urgent action immédiate closing")
    elif gap_pct > 40: parts.append("gap critique objectif loin bundle terminal forfait urgent")
    elif gap_pct > 20: parts.append("gap modéré performance insuffisante upsell améliorer")
    else:              parts.append("performance correcte optimiser panier moyen accessoires services")

    if 16 <= hour <= 18: parts.append("pic trafic heure de pointe maximiser conversion 16h")
    elif hour >= 19:     parts.append("soirée closing express dernières heures fermeture")
    elif hour <= 11:     parts.append("matin ouverture faible trafic appels sortants proactif")

    if is_rainy:
        parts.append("pluie météo défavorable accessoires résistants eau protection AirPods Watch")
    elif temp > 32:
        parts.append("canicule été climatisation confort boutique visites longues démonstration")

    if dow >= 5:   parts.append("week-end famille multi-lignes groupe décision collective")
    elif dow == 3: parts.append("jeudi fin semaine décision urgence weekend stock limité")
    elif dow == 0: parts.append("lundi faible trafic prospection appels sortants B2B")

    if urgency == "HIGH":   parts.append("urgence haute closing immédiat bundle premium avance postpayé")
    elif urgency == "CRITICAL": parts.append("crise critique mobilisation équipe bundle maximum")

    rag_query = " ".join(parts)
    logger.info(f"[STRATEGE RAG] Requête: '{rag_query[:100]}'")

    scripts = []
    rag_txt = ""

    try:
        # Embedding via Ollama
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": rag_query},
            timeout=30,
        )
        emb = resp.json().get("embedding", [])
        if not emb:
            raise ValueError("Embedding vide")
        if len(emb) < EMBED_DIM: emb = emb + [0.0] * (EMBED_DIM - len(emb))
        emb = emb[:EMBED_DIM]

        # Recherche Milvus
        from pymilvus import MilvusClient
        client  = MilvusClient(uri=MILVUS_URI)
        results = client.search(
            collection_name=COLLECTION,
            data=[emb],
            limit=6,
            output_fields=["pg_id", "categorie", "situation", "action",
                           "produit", "argument", "impact",
                           "heure_min", "heure_max", "jour_semaine", "tags"],
        )

        for hit in results[0]:
            e     = hit["entity"]
            score = float(hit["distance"])
            # Bonus créneau horaire
            if int(e.get("heure_min", 0)) <= hour <= int(e.get("heure_max", 24)):
                score += 0.12
            # Bonus météo pluie + accessoires
            if is_rainy and "meteo" in str(e.get("categorie", "")).lower():
                score += 0.08
            scripts.append({
                "score":     round(score, 3),
                "categorie": e.get("categorie", ""),
                "situation": e.get("situation", ""),
                "action":    e.get("action", ""),
                "produit":   e.get("produit", ""),
                "argument":  e.get("argument", ""),
                "impact":    e.get("impact", ""),
                "tags":      e.get("tags", ""),
            })

        scripts = sorted(scripts, key=lambda x: x["score"], reverse=True)[:3]

        # Formatter pour injection dans le prompt
        if scripts:
            lines = ["\n\nRAG SCRIPTS (situations similaires réussies — utilise ces arguments):"]
            for i, s in enumerate(scripts, 1):
                lines.append(
                    f"\n[Script #{i} | {s['categorie']} | score={s['score']:.2f}]\n"
                    f"Situation : {s['situation'][:120]}\n"
                    f"Action    : {s['action'][:120]}\n"
                    f"Produit   : {s['produit']}\n"
                    f"Argument  : {s['argument'][:150]}\n"
                    f"Impact    : {s['impact']}"
                )
            rag_txt = "\n".join(lines)

        logger.info(
            f"[STRATEGE RAG] {len(scripts)} scripts | "
            f"top={scripts[0]['categorie'] if scripts else 'N/A'} | "
            f"score={scripts[0]['score'] if scripts else 0:.3f}"
        )

        if scripts:
            try:
                log.rag_log(
                    query       = rag_query,
                    scripts     = scripts,
                    action_used = scripts[0]["action"][:100],
                    context     = {"gap_pct": gap_pct, "urgency": urgency,
                                   "hour": hour, "is_rainy": is_rainy},
                )
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"[STRATEGE RAG] Erreur: {str(e)[:80]}")
        try:
            log.node_error("rag_search", log_id, e, state)
        except Exception:
            pass

    duration = (time.time() - t0) * 1000
    rag_used = len(scripts) > 0
    output   = {
        **state,
        "rag_context":    scripts,
        "rag_query":      rag_query,
        "rag_used":       rag_used,
        "nb_rag_scripts": len(scripts),
        "rag_txt":        rag_txt,  # texte formaté pour le prompt
    }
    log.node_done("rag_search", log_id, output, duration,
                  {"nb_scripts": len(scripts), "rag_used": rag_used,
                   "top_score": scripts[0]["score"] if scripts else 0})
    return {**output, "metrics": _update_metrics(state, "stratege_rag_ms", round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 3 — Analyze Context (cause racine + facteurs + alertes)
# ══════════════════════════════════════════════════════════════════════════════

async def node_analyze_context(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("analyze_context", state)
    t0     = time.time()

    logger.info("[STRATEGE] Node 3 — Analyze context")

    gap_pct  = float(state.get("gap_objectif", 0))
    urgency  = state.get("urgency_level", "LOW")
    context  = state.get("external_context") or {}
    summary  = context.get("summary", {})
    holidays = context.get("holidays", {})
    events   = context.get("events", {})
    hour     = datetime.now().hour
    hrs_rem  = max(0, 20 - hour)
    w_eff    = float(summary.get("weather_effect", 0))
    rag_ok   = state.get("rag_used", False)
    nb_scr   = state.get("nb_rag_scripts", 0)
    pos_data = state.get("pos_data") or {}
    cr       = float(pos_data.get("current_revenue", 0) or 0)
    dt_val   = float(pos_data.get("daily_target", 1007) or 1007)

    factors = []

    # Météo
    if w_eff <= -0.25:
        factors.append(f"Météo très défavorable ({summary.get('weather_label','')}) — impact trafic {w_eff:.0%} — clients captifs en boutique = opportunité accessoires +40%")
    elif w_eff <= -0.10:
        factors.append(f"Météo défavorable ({summary.get('weather_label','')}) — impact trafic {w_eff:.0%} — focus accessoires résistants eau")
    elif w_eff >= 0.08:
        factors.append(f"Beau temps ({summary.get('weather_label','')}) — trafic +{w_eff:.0%} — forte affluence attendue = maximiser chaque contact")

    # Jour férié
    if holidays.get("is_holiday_today"):
        factors.append(f"Jour férié : {holidays.get('today_holiday',{}).get('name','')} — comportement client atypique, familles présentes")

    # Gap
    if gap_pct > 50:
        factors.append(f"Gap structurel très élevé ({gap_pct:.1f}%) — rythme de vente très insuffisant — 1 bundle iPhone suffit à couvrir l'objectif")
    elif gap_pct > 30:
        factors.append(f"Gap élevé ({gap_pct:.1f}%) — sous-performance équipe — focus bundle terminal + forfait")
    elif gap_pct > 15:
        factors.append(f"Gap modéré ({gap_pct:.1f}%) — rythme à améliorer — upsell services récurrents")

    # Pression temporelle
    if hour >= 18 and gap_pct > 10:
        factors.append(f"Pression critique — {hrs_rem}h restantes avant fermeture — closing express requis")
    elif hour >= 16 and gap_pct > 20:
        factors.append(f"Pic 16h actif — {hrs_rem}h restantes — maximiser conversion maintenant")

    # Promos
    promos = int(summary.get("active_promos", 0))
    if promos > 0 and gap_pct > 15:
        factors.append(f"{promos} promotion(s) Ooredoo actives — argument commercial disponible")

    # RAG disponible
    if rag_ok and nb_scr > 0:
        factors.append(f"RAG : {nb_scr} scripts similaires disponibles — utiliser les arguments qui ont marché")

    cause_racine = factors[0] if factors else f"Gap {gap_pct:.1f}% sans facteur externe majeur identifié"

    # ── Générer les ALERTES temps réel pour les conseillers ──────────────
    alerts = _generate_alerts(
        gap_pct=gap_pct, urgency=urgency, hour=hour, hrs_rem=hrs_rem,
        w_eff=w_eff, weather_label=summary.get("weather_label",""),
        weather_icon=summary.get("weather_icon","🌤️"),
        promos=promos, cr=cr, dt_val=dt_val,
        rag_ok=rag_ok, is_rainy=w_eff <= -0.10,
    )

    logger.info(f"[STRATEGE] Cause: {cause_racine[:70]}")
    logger.info(f"[STRATEGE] Facteurs: {len(factors)} | Alertes: {len(alerts)}")

    duration = (time.time() - t0) * 1000
    output   = {
        **state,
        "root_cause":      cause_racine,
        "cause_racine":    cause_racine,
        "context_factors": factors,
        "real_time_alerts": alerts,  # ← alertes pour les conseillers
    }
    log.node_done("analyze_context", log_id, output, duration,
                  {"nb_factors": len(factors), "cause": cause_racine[:60],
                   "nb_alerts": len(alerts)})
    return {**output, "metrics": _update_metrics(state, "stratege_analyze_ms", round(duration))}


def _generate_alerts(
    gap_pct: float, urgency: str, hour: int, hrs_rem: int,
    w_eff: float, weather_label: str, weather_icon: str,
    promos: int, cr: float, dt_val: float,
    rag_ok: bool, is_rainy: bool,
) -> list:
    """
    Génère les alertes temps réel pour les conseillers.
    Ces alertes apparaissent dans le panel Alertes du Coach Chat.
    """
    alerts = []
    now_str = datetime.now().strftime("%H:%M")

    # Alerte 1 — Stock critique
    alerts.append({
        "id":       f"alert-stock-{now_str}",
        "type":     "stock",
        "level":    "critical",
        "icon":     "📦",
        "title":    "Stock critique — iPhone 15",
        "message":  "3 unités restantes · 91% risque rupture",
        "action":   "Proposer iPhone 16 Pro comme alternative premium",
        "cta":      "Demander au coach",
        "timestamp": now_str,
    })

    # Alerte 2 — Météo si impact significatif
    if abs(w_eff) >= 0.10:
        if is_rainy:
            alerts.append({
                "id":      f"alert-meteo-{now_str}",
                "type":    "weather",
                "level":   "info",
                "icon":    weather_icon,
                "title":   f"{weather_label} — Opportunité accessoires",
                "message": "Demande accessoires +40% · Signal actif",
                "action":  "AirPods Pro 3 (279 TND IPX4) + Apple Watch S10 (449 TND étanche)",
                "cta":     "Demander au coach",
                "timestamp": now_str,
            })
        else:
            alerts.append({
                "id":      f"alert-meteo-{now_str}",
                "type":    "weather",
                "level":   "info",
                "icon":    weather_icon,
                "title":   f"{weather_label} — Trafic {w_eff:+.0%}",
                "message": "Conditions favorables · Maximiser chaque contact",
                "action":  "Focus terminaux premium et bundles",
                "cta":     "Demander au coach",
                "timestamp": now_str,
            })

    # Alerte 3 — Pic de trafic
    if 15 <= hour <= 17:
        alerts.append({
            "id":      f"alert-pic-{now_str}",
            "type":    "traffic",
            "level":   "warning",
            "icon":    "⚡",
            "title":   f"Pic trafic attendu {hour}h-{hour+1}h",
            "message": f"Créneau le plus fort — {21}% du CA journalier",
            "action":  "Pré-qualifier les clients en attente — script express 3 min",
            "cta":     "Demander au coach",
            "timestamp": now_str,
        })

    # Alerte 4 — Gap urgent
    if gap_pct > 30 and hrs_rem <= 4:
        gap_tnd = max(0, dt_val - cr)
        alerts.append({
            "id":      f"alert-gap-{now_str}",
            "type":    "gap",
            "level":   "critical" if gap_pct > 50 else "warning",
            "icon":    "🚨" if gap_pct > 50 else "⚠️",
            "title":   f"Gap {gap_pct:.0f}% — {hrs_rem}h restantes",
            "message": f"{gap_tnd:.0f} TND à combler · Bundle iPhone = solution",
            "action":  "iPhone 16 Pro + Forfait 5G Max + Assurance = 1 357 TND",
            "cta":     "Plan d'action coach",
            "timestamp": now_str,
        })

    # Alerte 5 — Promo active
    if promos > 0:
        alerts.append({
            "id":      f"alert-promo-{now_str}",
            "type":    "promo",
            "level":   "info",
            "icon":    "🎯",
            "title":   f"{promos} offre(s) Ooredoo active(s)",
            "message": "Forfait 5G Max · iPhone 16 Pro · Box Fibre installation offerte",
            "action":  "Mentionner la promotion dans chaque pitch — urgence naturelle",
            "cta":     "Script promo",
            "timestamp": now_str,
        })

    return alerts


# ══════════════════════════════════════════════════════════════════════════════
# NODE 4 — Generate Strategy (LLM + RAG)
# ══════════════════════════════════════════════════════════════════════════════

async def node_generate_strategy(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("generate_strategy", state)
    t0     = time.time()

    pos_data        = state.get("pos_data", {})
    gap_pct         = float(state.get("gap_objectif", 0))
    gap_amount      = float(state.get("gap_amount", 0))
    urgency_level   = state.get("urgency_level", "MEDIUM")
    analyst_summary = state.get("analyst_summary", "")
    context         = state.get("external_context") or {}
    root_cause      = state.get("root_cause", "")
    factors         = state.get("context_factors", [])
    rag_scripts     = state.get("rag_context") or []
    rag_txt         = state.get("rag_txt", "")  # ← texte formaté injecté directement
    hour            = datetime.now().hour
    hours_remaining = max(0, 20 - hour)
    summary         = context.get("summary", {})
    weather         = context.get("weather", {})
    holidays        = context.get("holidays", {})
    events          = context.get("events", {})

    # System prompt enrichi avec RAG
    system_with_rag = STRATEGE_SYSTEM_PROMPT + rag_txt

    # Construire les données pour le user prompt
    analyst_data = json.dumps({
        "urgency_level":   urgency_level,
        "gap_pct":         round(gap_pct, 1),
        "gap_amount_tnd":  round(gap_amount),
        "ca_actuel_tnd":   round(float(pos_data.get("current_revenue", 0))),
        "objectif_tnd":    round(float(pos_data.get("daily_target", 1007))),
        "analyst_summary": analyst_summary[:200],
        "root_cause":      root_cause[:150],
        "context_factors": factors[:4],
        "rag_scripts_used": len(rag_scripts),
    }, indent=2, ensure_ascii=False)

    weather_data = json.dumps({
        "label":        summary.get("weather_label", ""),
        "icon":         summary.get("weather_icon", ""),
        "effet_trafic": f"{summary.get('weather_effect', 0):+.0%}",
        "temperature":  summary.get("temperature", 22),
        "is_rainy":     float(summary.get("weather_effect", 0)) <= -0.10,
    }, indent=2, ensure_ascii=False)

    holidays_data = json.dumps({
        "is_holiday_today": holidays.get("is_holiday_today", False),
        "holiday_name": (holidays.get("today_holiday", {}) or {}).get("name", ""),
        "next_holiday": (holidays.get("next_holiday", {}) or {}).get("name", ""),
    }, indent=2, ensure_ascii=False)

    events_data = json.dumps({
        "active_promotions": [
            {"title": e.get("title", ""), "price": e.get("price", "")}
            for e in (events.get("promotions", []) or [])[:2]
        ],
    }, indent=2, ensure_ascii=False)

    user_msg = STRATEGE_USER_PROMPT.format(
        analyst_data    = analyst_data,
        weather_data    = weather_data,
        holidays_data   = holidays_data,
        events_data     = events_data,
        current_time    = datetime.now().strftime("%H:%M"),
        hours_remaining = hours_remaining,
    )

    # Appel LLM
    strategie_data = {}
    llm_ok         = False

    try:
        logger.info(f"[STRATEGE] Node 4 — Appel LLM ({OLLAMA_MODEL})...")
        llm      = get_llm()
        response = await llm.ainvoke([
            SystemMessage(content=system_with_rag),
            HumanMessage(content=user_msg),
        ])

        content = response.content.strip()
        # Nettoyer markdown
        if "```" in content:
            parts   = content.split("```")
            content = parts[1] if len(parts) > 1 else parts[0]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        try:
            strategie_data = json.loads(content)
            logger.info(f"[STRATEGE] LLM JSON OK — {len(strategie_data.get('actions', []))} actions")
            llm_ok = True
        except json.JSONDecodeError:
            logger.warning("[STRATEGE] JSON tronqué — extraction regex")
            strategie_data = _extract_from_partial_json(content)
            if strategie_data.get("actions"):
                llm_ok = True

    except Exception as e:
        logger.warning(f"[STRATEGE] LLM fallback: {str(e)[:60]}")
        log.fallback("generate_strategy", log_id, str(e), (time.time()-t0)*1000)

    # Fallback si pas d'actions
    if not strategie_data.get("actions"):
        strategie_data = _make_fallback_strategy(
            gap_pct, urgency_level, summary, hours_remaining, rag_scripts
        )

    duration = (time.time() - t0) * 1000
    output   = {**state, "strategie_data": strategie_data}
    log.node_done("generate_strategy", log_id, output, duration,
                  {"llm_ok": llm_ok, "nb_actions": len(strategie_data.get("actions", [])),
                   "rag_used": len(rag_scripts) > 0})
    return {**output, "metrics": _update_metrics(state, "stratege_llm_ms", round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 5 — Build Output (formatage final + alertes enrichies)
# ══════════════════════════════════════════════════════════════════════════════

async def node_build_output(state: SalesAgentState) -> dict:
    cid    = _cycle_id(state)
    sid    = _store_id(state)
    log    = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("build_output", state)
    t0     = time.time()

    strategie_data = state.get("strategie_data") or {}
    context        = state.get("external_context") or {}
    root_cause     = state.get("root_cause", "")
    factors        = state.get("context_factors", [])
    gap_pct        = float(state.get("gap_objectif", 0))
    urgency_level  = state.get("urgency_level", "MEDIUM")
    rag_used       = state.get("rag_used", False)
    nb_scripts     = state.get("nb_rag_scripts", 0)
    hour           = datetime.now().hour
    hrs_remaining  = max(0, 20 - hour)
    summary        = context.get("summary", {})
    real_time_alerts = state.get("real_time_alerts", [])

    if not strategie_data.get("actions"):
        strategie_data = _make_fallback_strategy(
            gap_pct, urgency_level, summary, hrs_remaining,
            state.get("rag_context") or []
        )

    actions           = strategie_data.get("actions", [])[:3]
    focus_produits    = strategie_data.get("focus_produits", [])
    message_manager   = strategie_data.get("message_manager", "")
    strategie_summary = strategie_data.get("strategie_summary", "")
    cause_racine      = strategie_data.get("cause_racine", root_cause)

    if not strategie_summary:
        strategie_summary = (
            f"Gap {gap_pct:.1f}% — {cause_racine[:80]}. "
            f"Focus: {', '.join(focus_produits[:2]) or 'produits premium'}."
        )

    # Valider et enrichir les actions
    validated_actions = []
    for i, a in enumerate(actions, 1):
        validated_actions.append({
            "priorite":       int(a.get("priorite", i)),
            "action":         str(a.get("action", ""))[:200],
            "produit_cible":  str(a.get("produit_cible", ""))[:100],
            "argument_vente": str(a.get("argument_vente", ""))[:250],
            "impact_estime":  str(a.get("impact_estime", ""))[:100],
        })

    # Enrichir les alertes avec les actions du stratège
    if validated_actions and real_time_alerts:
        # Injecter l'action prioritaire dans l'alerte gap si elle existe
        for alert in real_time_alerts:
            if alert.get("type") == "gap" and validated_actions:
                a = validated_actions[0]
                alert["action"] = f"{a['produit_cible']} — {a['argument_vente'][:80]}"

    context_signals = _build_context_signals(context, factors)
    heatmap         = context.get("heatmap") or _build_heatmap(urgency_level, summary)

    logger.info(
        f"[STRATEGE] ✓ Output: {len(validated_actions)} actions | "
        f"RAG={'✓' if rag_used else '✗'}({nb_scripts}) | "
        f"météo={summary.get('weather_label','?')} | "
        f"alertes={len(real_time_alerts)}"
    )

    duration = (time.time() - t0) * 1000
    output   = {
        **state,
        "strategie":          strategie_summary,
        "strategie_actions":  validated_actions,
        "focus_produits":     focus_produits,
        "message_manager":    message_manager,
        "cause_racine":       cause_racine,
        "context_heatmap":    heatmap,
        "context_signals":    context_signals,
        "external_context":   context,
        "real_time_alerts":   real_time_alerts,  # ← alertes vers le frontend
        "route_to":           "coach",
    }
    log.node_done("build_output", log_id, output, duration,
                  {"nb_actions": len(validated_actions),
                   "nb_signals": len(context_signals),
                   "nb_alerts": len(real_time_alerts)})

    metrics = dict(state.get("metrics") or {})
    metrics["stratege_build_ms"] = round(duration)
    metrics["nodes_executed"]    = int(metrics.get("nodes_executed", 0)) + 1
    metrics["stratege_total_ms"] = sum(
        metrics.get(k, 0) for k in
        ["stratege_context_ms", "stratege_rag_ms",
         "stratege_analyze_ms", "stratege_llm_ms", "stratege_build_ms"]
    )
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _extract_from_partial_json(content: str) -> dict:
    result = {}
    for field in ["cause_racine", "strategie_summary", "message_manager"]:
        m = re.search(rf'"{field}"\s*:\s*"([^"]+)"', content)
        if m: result[field] = m.group(1)
    fp = re.search(r'"focus_produits"\s*:\s*\[([^\]]+)\]', content)
    if fp: result["focus_produits"] = re.findall(r'"([^"]+)"', fp.group(1))
    actions_raw = re.findall(
        r'"action"\s*:\s*"([^"]+)"[^}]*?"produit_cible"\s*:\s*"([^"]+)"',
        content, re.DOTALL
    )
    if actions_raw:
        result["actions"] = [
            {"priorite": i+1, "action": a[0], "produit_cible": a[1],
             "argument_vente": "", "impact_estime": ""}
            for i, a in enumerate(actions_raw[:3])
        ]
    return result


def _make_fallback_strategy(gap_pct, urgency, summary, hours_remaining, rag_scripts=None):
    rag_scripts = rag_scripts or []
    w_eff       = float(summary.get("weather_effect", 0))
    w_label     = summary.get("weather_label", "")
    is_rainy    = w_eff <= -0.10

    if rag_scripts:
        actions = [
            {"priorite": i, "action": s["action"][:150], "produit_cible": s["produit"][:100],
             "argument_vente": s["argument"][:200], "impact_estime": s["impact"][:100]}
            for i, s in enumerate(rag_scripts[:3], 1)
        ]
        focus = list(dict.fromkeys(s["produit"] for s in rag_scripts[:3]))
    elif is_rainy:
        actions = [
            {"priorite":1,"action":"Proposer AirPods Pro 3 résistants eau sur chaque client entrant","produit_cible":"AirPods Pro 3","argument_vente":"Certifiés IPX4 résistants à l'eau — parfaits par ce temps","impact_estime":"+279 TND panier"},
            {"priorite":2,"action":"Cross-sell Box Fibre 1Go aux clients captifs","produit_cible":"Box Fibre 1Go","argument_vente":"Restez connecté chez vous — fibre 59 TND/mois installation offerte","impact_estime":"+59 TND récurrent"},
            {"priorite":3,"action":"Assurance Premium systématique après chaque vente terminal","produit_cible":"Assurance Premium","argument_vente":"9 TND/mois = un café par semaine — remplacement 48h garanti","impact_estime":"Marge 80%"},
        ]
        focus = ["AirPods Pro 3", "Box Fibre 1Go", "Assurance Premium"]
    elif gap_pct > 40:
        actions = [
            {"priorite":1,"action":"Bundle iPhone 16 Pro + Forfait 5G Max + Assurance via avance postpayé","produit_cible":"iPhone 16 Pro","argument_vente":"0 DT aujourd'hui, 54 TND/mois sur 24 mois — partez avec l'iPhone maintenant","impact_estime":"+1 357 TND = objectif comblé"},
            {"priorite":2,"action":"Convertir clients recharge vers Forfait Flexi 25Go avec calcul économie","produit_cible":"Forfait Flexi 25Go","argument_vente":"3 recharges = 30 TND. Forfait Flexi = 29 TND + appels illimités + 25Go","impact_estime":"+29 TND récurrent"},
            {"priorite":3,"action":"Closing express 5 min sur clients indécis avec urgence stock","produit_cible":"Samsung Galaxy A55 5G","argument_vente":"Il nous reste 2 unités — l'offre financement 0% expire ce soir","impact_estime":"+899 TND panier"},
        ]
        focus = ["iPhone 16 Pro", "Forfait 5G Max", "Forfait Flexi 25Go"]
    else:
        actions = [
            {"priorite":1,"action":"Upsell Assurance Premium sur 100% des ventes terminaux","produit_cible":"Assurance Premium","argument_vente":"9 TND/mois — un café par semaine pour protéger votre investissement. Remplacement 48h","impact_estime":"Marge 80%, acceptation 67%"},
            {"priorite":2,"action":"Cross-sell Box Fibre 1Go aux acheteurs smartphones premium","produit_cible":"Box Fibre 1Go","argument_vente":"Profitez de votre 5G à la maison — fibre 1Go à 59 TND/mois","impact_estime":"+59 TND récurrent"},
            {"priorite":3,"action":"Proposer Cloud Backup 1To sur chaque vente terminal","produit_cible":"Cloud Backup 1To","argument_vente":"15 TND/mois — photos et contacts sauvegardés automatiquement chaque nuit","impact_estime":"Marge très haute récurrent"},
        ]
        focus = ["Assurance Premium", "Box Fibre 1Go", "Cloud Backup 1To"]

    cause = f"Gap {gap_pct:.1f}% — {w_label or 'performance à améliorer'}"
    return {
        "cause_racine":      cause,
        "actions":           actions,
        "focus_produits":    focus,
        "message_manager":   f"Gap {gap_pct:.0f}% | {hours_remaining}h restantes | {'URGENT' if gap_pct > 40 else 'À surveiller'}",
        "strategie_summary": (
            f"Gap {gap_pct:.1f}% avec {hours_remaining}h restantes. "
            f"{'Météo défavorable — accessoires en priorité.' if is_rainy else ''} "
            f"Focus: {', '.join(focus[:2])}."
        ),
    }


def _build_context_signals(context: dict, factors: list) -> list:
    signals  = []
    summary  = context.get("summary", {})
    holidays = context.get("holidays", {})
    events   = context.get("events", {})

    w_eff = float(summary.get("weather_effect", 0))
    signals.append({
        "type":  "weather",
        "level": "high" if w_eff <= -0.25 else "med" if w_eff <= -0.10 else "low",
        "label": f"{summary.get('weather_icon','☀️')} {summary.get('weather_label','Normal')} — {w_eff:+.0%} trafic",
        "value": w_eff,
    })

    if holidays.get("is_holiday_today"):
        signals.append({"type":"holiday","level":"high",
                        "label":f"🎉 {holidays.get('today_holiday',{}).get('name','')}","value":1.0})
    elif (holidays.get("next_holiday") or {}).get("days_until", 99) <= 3:
        nh = holidays.get("next_holiday", {})
        signals.append({"type":"holiday","level":"med",
                        "label":f"📅 {nh.get('name','')} dans {nh.get('days_until',0)}j","value":0.5})

    signals.append({"type":"stock","level":"high",
                    "label":"📦 iPhone 15 — stock critique (3 unités)","value":-0.3})

    all_promos = (events.get("promotions",[]) or []) + (events.get("new_offers",[]) or [])
    if all_promos:
        signals.append({"type":"event","level":"med",
                        "label":f"🎯 {len(all_promos)} offre(s) Ooredoo — {all_promos[0].get('title','')[:40]}",
                        "value":0.3})
    return signals


def _build_heatmap(urgency: str, summary: dict) -> dict:
    is_rainy = float(summary.get("weather_effect", 0)) <= -0.10
    if urgency in ("HIGH", "CRITICAL"):
        traffic = ["med","high","high","crit","crit","high","med","low"]
        risk    = ["low","med","high","high","crit","crit","high","med"]
    elif urgency == "MEDIUM":
        traffic = ["low","med","med","high","high","med","med","low"]
        risk    = ["low","low","med","med","high","med","med","low"]
    else:
        traffic = ["low","low","med","med","med","low","low","low"]
        risk    = ["low","low","low","med","med","low","low","low"]
    return {
        "hours":   ["11AM","12PM","1PM","2PM","3PM","4PM","5PM","6PM"],
        "traffic": traffic,
        "weather": ["high"]*8 if is_rainy else ["low"]*8,
        "stock":   ["low","low","med","high","high","crit","crit","high"],
        "event":   ["low","low","low","low","med","med","high","high"],
        "risk":    risk,
    }


# Alias compatibilité
async def node_analyze_root_cause(state: SalesAgentState) -> dict:
    return await node_analyze_context(state)