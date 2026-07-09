"""
nodes.py — Agent Stratège FINAL
================================
v4 + Langfuse + OpenRouter pour generate_strategy
Flow : fetch_context → rag_search → analyze_context → generate_strategy → build_output
"""

import json
import logging
import os
import re
import time
from datetime import datetime

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.agent_logger import AgentLogger
from app.sales.core.config import get_config
from app.sales.core.state import SalesAgentState
from app.sales.coaching.agents.stratege.tools import fetch_full_context
from app.core.config import DEFAULT_STORE_ID
from app.sales.coaching.agents.stratege.prompts import (
    STRATEGE_SYSTEM_PROMPT,
    STRATEGE_USER_PROMPT,
)

logger = logging.getLogger(__name__)
config = get_config()

OLLAMA_URL        = os.getenv("OLLAMA_BASE_URL",   "http://localhost:11434")
OLLAMA_MODEL      = os.getenv("OLLAMA_MODEL",       "llama3.2:latest")
OPENROUTER_KEY    = os.getenv("OPENROUTER_API_KEY", "")
# Modèle héritage — fallback si factory non disponible
OPENROUTER_MODEL  = os.getenv("OPENROUTER_MODEL",   "nvidia/nemotron-3-super-120b-a12b:free")
OPENROUTER_URL    = "https://openrouter.ai/api/v1/chat/completions"
# Recherche RAG déléguée à app.sales.data.rag_retriever (Milvus + fallback corpus)
COLLECTION        = "coaching_scripts"

USE_OPENROUTER = bool(OPENROUTER_KEY)


# ══════════════════════════════════════════════════════════════════════════════
# LANGFUSE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_trace(state: dict):
    try:
        from app.core.langfuse import get_langfuse
        lf = get_langfuse()
        if lf is None:
            return None
        cid = _cycle_id(state)
        sid = _store_id(state)
        return lf.trace(
            id         = f"{cid}-stratege",
            name       = "stratege-cycle",
            user_id    = sid,
            session_id = f"session-{sid}-{datetime.now().strftime('%Y%m%d')}",
            tags       = ["stratege", sid],
            input      = {"gap_pct": state.get("gap_objectif", 0),
                          "urgency": state.get("urgency_level", "?"),
                          "store_id": sid},
        )
    except Exception:
        return None


def _lf_span(trace, name, input_data, output_data, latency_ms,
             level="DEFAULT", metadata=None):
    try:
        if trace is None:
            return
        trace.span(name=name, input=input_data, output=output_data,
                   metadata={"latency_ms": round(latency_ms), **(metadata or {})})
    except Exception as e:
        logger.debug(f"[LANGFUSE] span {name}: {e}")


def _lf_generation(trace, name, model, system_prompt,
                   user_prompt, response, latency_ms, metadata=None):
    try:
        if trace is None:
            return
        trace.generation(
            name=name, model=model,
            input=[{"role": "system", "content": system_prompt[:600]},
                   {"role": "user",   "content": user_prompt[:600]}],
            output=response[:800],
            metadata={"latency_ms": round(latency_ms), **(metadata or {})},
        )
    except Exception as e:
        logger.debug(f"[LANGFUSE] generation {name}: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cycle_id(state):
    return (state.get("metrics") or {}).get("cycle_id", "") or state.get("cycle_id", "unknown")

def _store_id(state):
    return (state.get("pos_data") or {}).get("store_id", DEFAULT_STORE_ID) or state.get("store_id", DEFAULT_STORE_ID)

def _update_metrics(state, key, value):
    metrics = dict(state.get("metrics") or {})
    metrics[key] = value
    metrics["nodes_executed"] = int(metrics.get("nodes_executed", 0)) + 1
    return metrics

def get_llm():
    """
    Fallback local Ollama — appelé uniquement après échec d'OpenRouter
    (_call_openrouter_stratege) dans node_generate_strategy.

    NE PAS appeler get_smart_llm() ici : cette factory route elle-même vers
    OpenRouter dès que LLM_PROVIDER=openrouter (cas par défaut du projet),
    donc "le fallback" retombait en réalité sur OpenRouter — quand OpenRouter
    est rate-limited (429 free-models-per-day), ce fallback échouait avec la
    même erreur au lieu de basculer vers un modèle local indépendant.
    """
    return ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_URL,
                      temperature=0.2, num_predict=600, num_ctx=3500)


# ══════════════════════════════════════════════════════════════════════════════
# HELPER STOCK
# ══════════════════════════════════════════════════════════════════════════════

async def _get_critical_stock(store_id=DEFAULT_STORE_ID, threshold=5):
    try:
        import asyncpg
        conn = await asyncpg.connect(host="localhost", port=5432,
            database="ooredoo_sales", user="postgres", password="admin", timeout=3)
        try:
            rows = await conn.fetch("""
                SELECT s.sku,
                       s.product_name,
                       COALESCE(s.stock_dispo, 0) AS stock_qty,
                       s.stock_risk
                FROM sales.vw_stock_enriched s
                WHERE s.store_id = $1
                  AND (s.stock_risk IN ('rupture','critical')
                       OR COALESCE(s.stock_dispo, 0) <= $2)
                ORDER BY COALESCE(s.stock_dispo, 0) ASC LIMIT 5
            """, store_id, threshold)
            return [{"sku": str(r["sku"]), "product_name": str(r["product_name"]),
                     "stock_qty": int(r["stock_qty"]), "risk_level": str(r["stock_risk"])}
                    for r in rows]
        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"[STRATEGE STOCK] Fallback: {e}")
        return [{"sku":"5021240","product_name":"iPhone 16 Pro",
                 "stock_qty":3,"risk_level":"critical"}]


# ══════════════════════════════════════════════════════════════════════════════
# NODE 1 — Fetch Context
# ══════════════════════════════════════════════════════════════════════════════

async def node_fetch_context(state: SalesAgentState) -> dict:
    cid = _cycle_id(state); sid = _store_id(state)
    log = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("fetch_context", state)
    t0 = time.time(); trace = _get_trace(state)
    logger.info(f"[STRATEGE] Node 1 — Fetch contexte pour {sid}")
    try:
        context = await fetch_full_context(sid)
        summary = context.get("summary", {})
        duration = (time.time() - t0) * 1000
        _lf_span(trace, "fetch_context", {"store_id": sid},
            {"weather": summary.get("weather_label",""), "effect": summary.get("weather_effect",0),
             "is_holiday": summary.get("is_holiday",False), "active_promos": summary.get("active_promos",0)},
            duration)
        logger.info(f"[STRATEGE] Node 1 — {summary.get('weather_icon','')} {summary.get('weather_label','')} | effet={summary.get('weather_effect',0):+.0%}")
        output = {**state, "external_context": context}
        log.node_done("fetch_context", log_id, output, duration, {"weather": summary.get("weather_label","")})
        return {**output, "metrics": _update_metrics(state, "stratege_context_ms", round(duration))}
    except Exception as e:
        duration = (time.time() - t0) * 1000
        _lf_span(trace, "fetch_context", {"store_id": sid}, {"error": str(e)}, duration, "ERROR")
        log.node_error("fetch_context", log_id, e, state)
        fallback = {"summary":{"weather_label":"Tunis","weather_icon":"🌤️","weather_effect":0.0,
                               "temperature":22,"is_holiday":False,"active_promos":0},
                    "holidays":{},"events":{},"heatmap":{}}
        return {**state, "external_context": fallback,
                "metrics": _update_metrics(state, "stratege_context_ms", round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 2 — RAG Search
# ══════════════════════════════════════════════════════════════════════════════

async def node_rag_search(state: SalesAgentState) -> dict:
    cid = _cycle_id(state); sid = _store_id(state)
    log = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("rag_search", state)
    t0 = time.time(); trace = _get_trace(state)
    logger.info("[STRATEGE] Node 2 — RAG search Milvus")

    gap_pct  = float(state.get("gap_objectif", 0))
    urgency  = state.get("urgency_level", "MEDIUM")
    ext_ctx  = state.get("external_context") or {}
    summary  = ext_ctx.get("summary", {})
    hour     = datetime.now().hour
    dow      = datetime.now().weekday()
    is_rainy = float(summary.get("weather_effect", 0)) <= -0.10
    temp     = float(summary.get("temperature", 22))

    parts = []
    if gap_pct > 60:   parts.append("gap critique très éloigné objectif urgent action immédiate closing")
    elif gap_pct > 40: parts.append("gap critique objectif loin bundle terminal forfait urgent")
    elif gap_pct > 20: parts.append("gap modéré performance insuffisante upsell améliorer")
    else:              parts.append("performance correcte optimiser panier moyen accessoires services")
    if 16 <= hour <= 18: parts.append("pic trafic heure de pointe maximiser conversion 16h")
    elif hour >= 19:     parts.append("soirée closing express dernières heures fermeture")
    elif hour <= 11:     parts.append("matin ouverture faible trafic appels sortants proactif")
    if is_rainy:         parts.append("pluie météo défavorable accessoires résistants eau AirPods Watch")
    elif temp > 32:      parts.append("canicule été climatisation confort boutique visites longues")
    if dow >= 5:         parts.append("week-end famille multi-lignes groupe décision collective")
    elif dow == 3:       parts.append("jeudi fin semaine décision urgence weekend stock limité")
    elif dow == 0:       parts.append("lundi faible trafic prospection appels sortants B2B")
    if urgency == "HIGH":       parts.append("urgence haute closing immédiat bundle premium avance postpayé")
    elif urgency == "CRITICAL": parts.append("crise critique mobilisation équipe bundle maximum")

    rag_query = " ".join(parts)
    scripts = []; rag_txt = ""; rag_source = "none"

    # Retriever unifié : Milvus sémantique + fallback lexical corpus embarqué
    # (timeouts courts — plus jamais 120s d'attente embedding dans un cycle).
    try:
        import asyncio as _aio
        from app.sales.data.rag_retriever import search_scripts, format_scripts_block
        rag_result = await _aio.to_thread(search_scripts, rag_query, hour, 3)
        scripts    = rag_result["scripts"]
        rag_source = rag_result["source"]

        # Bonus météo : par temps de pluie, remonter les scripts météo
        if is_rainy and scripts:
            for s in scripts:
                if "meteo" in str(s.get("categorie", "")).lower():
                    s["score"] = round(s["score"] + 0.08, 3)
            scripts = sorted(scripts, key=lambda x: x["score"], reverse=True)

        if scripts:
            rag_txt = "\n\n" + format_scripts_block(scripts, max_n=3)
        logger.info(f"[STRATEGE RAG] {len(scripts)} scripts | source={rag_source} | "
                    f"top={scripts[0]['categorie'] if scripts else 'N/A'}")
    except Exception as e:
        logger.warning(f"[STRATEGE RAG] Erreur: {str(e)[:80]}")

    duration = (time.time() - t0) * 1000
    rag_used = len(scripts) > 0
    _lf_span(trace, "rag_search",
        {"query": rag_query[:300], "gap_pct": gap_pct, "urgency": urgency, "is_rainy": is_rainy},
        {"nb_scripts": len(scripts), "rag_used": rag_used,
         "top_score": scripts[0]["score"] if scripts else 0,
         "source": rag_source},
        duration, metadata={"collection": COLLECTION})

    output = {**state, "rag_context": scripts, "rag_query": rag_query,
              "rag_used": rag_used, "nb_rag_scripts": len(scripts), "rag_txt": rag_txt}
    log.node_done("rag_search", log_id, output, duration,
                  {"nb_scripts": len(scripts), "rag_used": rag_used})
    return {**output, "metrics": _update_metrics(state, "stratege_rag_ms", round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 3 — Analyze Context
# ══════════════════════════════════════════════════════════════════════════════

async def node_analyze_context(state: SalesAgentState) -> dict:
    cid = _cycle_id(state); sid = _store_id(state)
    log = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("analyze_context", state)
    t0 = time.time(); trace = _get_trace(state)
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
    if w_eff <= -0.25:  factors.append(f"Météo très défavorable ({summary.get('weather_label','')}) — impact trafic {w_eff:.0%}")
    elif w_eff <= -0.10: factors.append(f"Météo défavorable ({summary.get('weather_label','')}) — impact trafic {w_eff:.0%}")
    elif w_eff >= 0.08: factors.append(f"Beau temps ({summary.get('weather_label','')}) — trafic +{w_eff:.0%}")
    if holidays.get("is_holiday_today"):
        factors.append(f"Jour férié : {holidays.get('today_holiday',{}).get('name','')}")
    if gap_pct > 50:    factors.append(f"Gap structurel très élevé ({gap_pct:.1f}%)")
    elif gap_pct > 30:  factors.append(f"Gap élevé ({gap_pct:.1f}%)")
    elif gap_pct > 15:  factors.append(f"Gap modéré ({gap_pct:.1f}%)")
    if hour >= 18 and gap_pct > 10: factors.append(f"Pression critique — {hrs_rem}h restantes")
    elif hour >= 16 and gap_pct > 20: factors.append(f"Pic 16h actif — {hrs_rem}h restantes")
    promos = int(summary.get("active_promos", 0))
    if promos > 0 and gap_pct > 15: factors.append(f"{promos} promotion(s) Ooredoo actives")
    if rag_ok and nb_scr > 0: factors.append(f"RAG : {nb_scr} scripts similaires disponibles")

    cause_racine = factors[0] if factors else f"Gap {gap_pct:.1f}% sans facteur externe majeur"
    stock_alerts = await _get_critical_stock(store_id=sid, threshold=5)
    alerts = _generate_alerts(gap_pct=gap_pct, urgency=urgency, hour=hour, hrs_rem=hrs_rem,
        w_eff=w_eff, weather_label=summary.get("weather_label",""),
        weather_icon=summary.get("weather_icon","🌤️"), promos=promos, cr=cr, dt_val=dt_val,
        rag_ok=rag_ok, is_rainy=w_eff<=-0.10, stock_alerts=stock_alerts)

    duration = (time.time() - t0) * 1000
    _lf_span(trace, "analyze_context",
        {"gap_pct": gap_pct, "urgency": urgency, "hour": hour, "weather_effect": w_eff},
        {"cause_racine": cause_racine[:100], "nb_factors": len(factors),
         "nb_alerts": len(alerts), "stock_critiques": len(stock_alerts)},
        duration, level="WARNING" if urgency in ("HIGH","CRITICAL") else "DEFAULT")

    logger.info(f"[STRATEGE] Cause: {cause_racine[:70]} | Facteurs: {len(factors)} | Alertes: {len(alerts)}")
    output = {**state, "root_cause": cause_racine, "cause_racine": cause_racine,
              "context_factors": factors, "real_time_alerts": alerts}
    log.node_done("analyze_context", log_id, output, duration,
                  {"nb_factors": len(factors), "cause": cause_racine[:60]})
    return {**output, "metrics": _update_metrics(state, "stratege_analyze_ms", round(duration))}


def _generate_alerts(gap_pct, urgency, hour, hrs_rem, w_eff, weather_label,
                     weather_icon, promos, cr, dt_val, rag_ok, is_rainy, stock_alerts=None):
    alerts = []; now_str = datetime.now().strftime("%H:%M")
    stock_alerts = stock_alerts or []
    if stock_alerts:
        top = stock_alerts[0]; qty = top["stock_qty"]; pname = top["product_name"]
        alerts.append({"id":f"alert-stock-{now_str}","type":"stock",
            "level":"critical" if qty<=2 else "warning","icon":"📦",
            "title":f"Stock {'rupture' if qty==0 else 'critique'} — {pname}",
            "message":f"{qty} unité(s) restante(s)" if qty>0 else f"Rupture — {pname}",
            "action":"Proposer alternative premium","cta":"Demander au coach","timestamp":now_str})
        for s in stock_alerts[1:3]:
            alerts.append({"id":f"alert-stock-{s['sku']}-{now_str}","type":"stock",
                "level":"warning","icon":"⚠️","title":f"Stock bas — {s['product_name']}",
                "message":f"{s['stock_qty']} unité(s)","action":"Vérifier réapprovisionnement",
                "cta":"Demander au coach","timestamp":now_str})
    else:
        alerts.append({"id":f"alert-stock-{now_str}","type":"stock","level":"info",
            "icon":"📦","title":"Stock nominal","message":"Aucun SKU en rupture",
            "action":"Vérifier stock","cta":"Demander au coach","timestamp":now_str})
    if abs(w_eff) >= 0.10:
        alerts.append({"id":f"alert-meteo-{now_str}","type":"weather","level":"info",
            "icon":weather_icon,
            "title":f"{weather_label} — {'Opportunité accessoires' if is_rainy else f'Trafic {w_eff:+.0%}'}",
            "message":"Demande accessoires +40%" if is_rainy else "Conditions favorables",
            "action":"AirPods Pro 3 (279 TND) + Apple Watch S10 (449 TND)" if is_rainy else "Focus terminaux premium",
            "cta":"Demander au coach","timestamp":now_str})
    if 15 <= hour <= 17:
        alerts.append({"id":f"alert-pic-{now_str}","type":"traffic","level":"warning",
            "icon":"⚡","title":f"Pic trafic attendu {hour}h-{hour+1}h",
            "message":"Créneau le plus fort — 21% du CA journalier",
            "action":"Pré-qualifier les clients — script express 3 min",
            "cta":"Demander au coach","timestamp":now_str})
    if gap_pct > 30 and hrs_rem <= 4:
        gap_tnd = max(0, dt_val - cr)
        alerts.append({"id":f"alert-gap-{now_str}","type":"gap",
            "level":"critical" if gap_pct>50 else "warning",
            "icon":"🚨" if gap_pct>50 else "⚠️",
            "title":f"Gap {gap_pct:.0f}% — {hrs_rem}h restantes",
            "message":f"{gap_tnd:.0f} TND à combler · Bundle iPhone = solution",
            "action":"iPhone 16 Pro + Forfait 5G Max + Assurance = 1 357 TND",
            "cta":"Plan d'action coach","timestamp":now_str})
    if promos > 0:
        alerts.append({"id":f"alert-promo-{now_str}","type":"promo","level":"info",
            "icon":"🎯","title":f"{promos} offre(s) Ooredoo active(s)",
            "message":"Forfait 5G Max · iPhone 16 Pro · Box Fibre installation offerte",
            "action":"Mentionner la promotion dans chaque pitch",
            "cta":"Script promo","timestamp":now_str})
    return alerts


# ══════════════════════════════════════════════════════════════════════════════
# NODE 4 — Generate Strategy (OpenRouter ou Ollama)
# ══════════════════════════════════════════════════════════════════════════════

_OR_MODEL_ROTATION = [
    os.getenv("OPENROUTER_MODEL",          "nvidia/nemotron-3-super-120b-a12b:free"),
    os.getenv("OPENROUTER_MODEL_FALLBACK", "openai/gpt-oss-120b:free"),
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]

async def _call_openrouter_stratege(system_prompt: str, user_msg: str,
                                    urgency: str) -> tuple[str, float]:
    """Appel OpenRouter avec rotation automatique sur rate-limit."""
    t0 = time.time()
    import httpx
    for model in _OR_MODEL_ROTATION:
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_KEY}",
                        "Content-Type":  "application/json",
                        "HTTP-Referer":  "https://github.com/MALEKALADAB11/multi-agent-sales-inventory",
                        "X-Title":       "AI Sales Coach Ooredoo - Stratege",
                    },
                    json={
                        "model":       model,
                        "max_tokens":  800,
                        "temperature": 0.1,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user",   "content": user_msg},
                        ],
                    },
                )
            data = resp.json()
            if "error" in data:
                code = data["error"].get("code", 0)
                msg  = data["error"].get("message", "?")[:80]
                logger.warning(f"[STRATEGE OR] {model} → {code}: {msg}")
                if code in (429, 503):
                    continue  # rate-limit → essayer le modèle suivant
                return "", (time.time() - t0) * 1000
            content = data["choices"][0]["message"]["content"].strip()
            ms = (time.time() - t0) * 1000
            logger.info(f"[STRATEGE OR] OK — {len(content)} chars | {ms:.0f}ms | {model}")
            return content, ms
        except Exception as e:
            logger.warning(f"[STRATEGE OR] {model} exception: {str(e)[:60]}")
            continue
    return "", (time.time() - t0) * 1000


MISTRAL_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
_MISTRAL_MODEL_ROTATION = [
    os.getenv("MISTRAL_MODEL_SMART", "mistral-large-latest"),
    os.getenv("MISTRAL_MODEL",       "mistral-small-latest"),
]


async def _call_mistral_stratege(system_prompt: str, user_msg: str,
                                 urgency: str) -> tuple[str, float]:
    """Appel Mistral direct (quota indépendant d'OpenRouter) — tentative 1."""
    t0 = time.time()
    if not MISTRAL_KEY:
        return "", 0.0
    import httpx
    for model in _MISTRAL_MODEL_ROTATION:
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(
                    MISTRAL_URL,
                    headers={
                        "Authorization": f"Bearer {MISTRAL_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model":       model,
                        "max_tokens":  800,
                        "temperature": 0.1,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user",   "content": user_msg},
                        ],
                    },
                )
            if resp.status_code == 429:
                logger.warning(f"[STRATEGE MISTRAL] {model} → 429 rate limit")
                continue
            data = resp.json()
            if resp.status_code >= 400:
                err = data.get("error")
                msg = err.get("message") if isinstance(err, dict) else data.get("message", "?")
                logger.warning(f"[STRATEGE MISTRAL] {model} → {resp.status_code}: {str(msg)[:80]}")
                return "", (time.time() - t0) * 1000
            content = data["choices"][0]["message"]["content"].strip()
            ms = (time.time() - t0) * 1000
            logger.info(f"[STRATEGE MISTRAL] OK — {len(content)} chars | {ms:.0f}ms | {model}")
            return content, ms
        except Exception as e:
            logger.warning(f"[STRATEGE MISTRAL] {model} exception: {str(e)[:60]}")
            continue
    return "", (time.time() - t0) * 1000


async def node_generate_strategy(state: SalesAgentState) -> dict:
    cid = _cycle_id(state); sid = _store_id(state)
    log = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("generate_strategy", state)
    t0 = time.time(); trace = _get_trace(state)

    pos_data        = state.get("pos_data", {})
    gap_pct         = float(state.get("gap_objectif", 0))
    gap_amount      = float(state.get("gap_amount", 0))
    urgency_level   = state.get("urgency_level", "MEDIUM")
    analyst_summary = state.get("analyst_summary", "")
    context         = state.get("external_context") or {}
    root_cause      = state.get("root_cause", "")
    factors         = state.get("context_factors", [])
    rag_scripts     = state.get("rag_context") or []
    rag_txt         = state.get("rag_txt", "")
    hour            = datetime.now().hour
    hours_remaining = max(0, 20 - hour)
    summary         = context.get("summary", {})
    holidays        = context.get("holidays", {})
    events          = context.get("events", {})

    system_with_rag = STRATEGE_SYSTEM_PROMPT + rag_txt

    analyst_data = json.dumps({
        "urgency_level": urgency_level, "gap_pct": round(gap_pct, 1),
        "gap_amount_tnd": round(gap_amount),
        "ca_actuel_tnd": round(float(pos_data.get("current_revenue", 0))),
        "objectif_tnd": round(float(pos_data.get("daily_target", 1007))),
        "analyst_summary": analyst_summary[:200], "root_cause": root_cause[:150],
        "context_factors": factors[:4], "rag_scripts_used": len(rag_scripts),
    }, indent=2, ensure_ascii=False)

    weather_data = json.dumps({
        "label": summary.get("weather_label",""), "icon": summary.get("weather_icon",""),
        "effet_trafic": f"{summary.get('weather_effect',0):+.0%}",
        "temperature": summary.get("temperature",22),
        "is_rainy": float(summary.get("weather_effect",0)) <= -0.10,
    }, indent=2, ensure_ascii=False)

    holidays_data = json.dumps({
        "is_holiday_today": holidays.get("is_holiday_today", False),
        "holiday_name": (holidays.get("today_holiday",{}) or {}).get("name",""),
    }, indent=2, ensure_ascii=False)

    events_data = json.dumps({
        "active_promotions": [{"title": e.get("title",""), "price": e.get("price","")}
                               for e in (events.get("promotions",[]) or [])[:2]],
    }, indent=2, ensure_ascii=False)

    user_msg = STRATEGE_USER_PROMPT.format(
        analyst_data=analyst_data, weather_data=weather_data,
        holidays_data=holidays_data, events_data=events_data,
        current_time=datetime.now().strftime("%H:%M"), hours_remaining=hours_remaining,
    )

    # Boucle de feedback : taux de suivi des incitations + rejets HITL récents
    # réinjectés pour que le Stratège adapte ses actions (jamais bloquant).
    try:
        import asyncio as _aio
        from app.core.feedback_service import get_learning_context_sync
        fb_ctx = await _aio.to_thread(get_learning_context_sync, sid, None)
        if fb_ctx:
            user_msg += "\n\n## FEEDBACK TERRAIN RÉCENT (adapte tes actions)\n" + fb_ctx
    except Exception as _fb_err:
        logger.debug(f"[STRATEGE] feedback context skipped: {_fb_err}")

    strategie_data = {}; llm_ok = False; llm_latency_ms = 0.0
    llm_response = ""; model_used = ""

    # ── Essayer Mistral d'abord (quota indépendant), puis OpenRouter, puis Ollama ──
    if MISTRAL_KEY:
        logger.info("[STRATEGE] Node 4 — Appel Mistral direct...")
        llm_response, llm_latency_ms = await _call_mistral_stratege(
            system_with_rag, user_msg, urgency_level)
        model_used = "mistral"

    if not llm_response and USE_OPENROUTER:
        logger.info(f"[STRATEGE] Node 4 — Appel OpenRouter ({OPENROUTER_MODEL})...")
        llm_response, llm_latency_ms = await _call_openrouter_stratege(
            system_with_rag, user_msg, urgency_level)
        model_used = OPENROUTER_MODEL

    if not llm_response:
        logger.info(f"[STRATEGE] Node 4 — Appel Ollama ({OLLAMA_MODEL})...")
        try:
            llm = get_llm()
            t_llm = time.time()
            response = await llm.ainvoke([
                SystemMessage(content=system_with_rag),
                HumanMessage(content=user_msg),
            ])
            llm_latency_ms = (time.time() - t_llm) * 1000
            llm_response = response.content.strip()
            model_used = OLLAMA_MODEL
        except Exception as e:
            logger.warning(f"[STRATEGE] Ollama fallback: {str(e)[:60]}")
            log.fallback("generate_strategy", log_id, str(e), (time.time()-t0)*1000)

    # ── Parser le JSON ────────────────────────────────────────────────────────
    if llm_response:
        content = llm_response
        if "```" in content:
            parts = content.split("```")
            content = parts[1] if len(parts) > 1 else parts[0]
            if content.startswith("json"): content = content[4:]
            content = content.strip()
        try:
            strategie_data = json.loads(content)
            logger.info(f"[STRATEGE] JSON OK — {len(strategie_data.get('actions',[]))} actions | {model_used}")
            llm_ok = True
        except json.JSONDecodeError:
            logger.warning("[STRATEGE] JSON tronqué — extraction regex")
            strategie_data = _extract_from_partial_json(content)
            if strategie_data.get("actions"): llm_ok = True

    _lf_generation(trace, "stratege-llm-strategy", model_used,
        system_with_rag[:600], user_msg[:600], llm_response[:800], llm_latency_ms,
        metadata={"urgency": urgency_level, "gap_pct": gap_pct,
                  "nb_rag": len(rag_scripts), "llm_ok": llm_ok,
                  "model": model_used})

    if not strategie_data.get("actions"):
        strategie_data = _make_fallback_strategy(gap_pct, urgency_level, summary,
                                                  hours_remaining, rag_scripts)

    total_duration = (time.time() - t0) * 1000
    _lf_span(trace, "generate_strategy",
        {"urgency": urgency_level, "gap_pct": gap_pct, "nb_rag": len(rag_scripts)},
        {"nb_actions": len(strategie_data.get("actions",[])), "llm_ok": llm_ok,
         "model": model_used, "focus": strategie_data.get("focus_produits",[])[:3]},
        total_duration)

    output = {**state, "strategie_data": strategie_data}
    log.node_done("generate_strategy", log_id, output, total_duration,
                  {"llm_ok": llm_ok, "nb_actions": len(strategie_data.get("actions",[]))})
    return {**output, "metrics": _update_metrics(state, "stratege_llm_ms", round(total_duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 5 — Build Output
# ══════════════════════════════════════════════════════════════════════════════

async def node_build_output(state: SalesAgentState) -> dict:
    cid = _cycle_id(state); sid = _store_id(state)
    log = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("build_output", state)
    t0 = time.time(); trace = _get_trace(state)

    strategie_data   = state.get("strategie_data") or {}
    context          = state.get("external_context") or {}
    root_cause       = state.get("root_cause", "")
    factors          = state.get("context_factors", [])
    gap_pct          = float(state.get("gap_objectif", 0))
    urgency_level    = state.get("urgency_level", "MEDIUM")
    rag_used         = state.get("rag_used", False)
    nb_scripts       = state.get("nb_rag_scripts", 0)
    hour             = datetime.now().hour
    hrs_remaining    = max(0, 20 - hour)
    summary          = context.get("summary", {})
    real_time_alerts = state.get("real_time_alerts", [])

    if not strategie_data.get("actions"):
        strategie_data = _make_fallback_strategy(gap_pct, urgency_level, summary,
                                                  hrs_remaining, state.get("rag_context") or [])

    actions           = strategie_data.get("actions", [])[:3]
    focus_produits    = strategie_data.get("focus_produits", [])
    message_manager   = strategie_data.get("message_manager", "")
    strategie_summary = strategie_data.get("strategie_summary", "")
    cause_racine      = strategie_data.get("cause_racine", root_cause)

    if not strategie_summary:
        strategie_summary = (f"Gap {gap_pct:.1f}% — {cause_racine[:80]}. "
                             f"Focus: {', '.join(focus_produits[:2]) or 'produits premium'}.")

    validated_actions = []
    for i, a in enumerate(actions, 1):
        validated_actions.append({
            "priorite":       int(a.get("priorite", i)),
            "action":         str(a.get("action", ""))[:200],
            "produit_cible":  str(a.get("produit_cible", ""))[:100],
            "argument_vente": str(a.get("argument_vente", ""))[:250],
            "impact_estime":  str(a.get("impact_estime", ""))[:100],
        })

    if validated_actions and real_time_alerts:
        for alert in real_time_alerts:
            if alert.get("type") == "gap" and validated_actions:
                a = validated_actions[0]
                alert["action"] = f"{a['produit_cible']} — {a['argument_vente'][:80]}"

    context_signals = _build_context_signals(context, factors)
    heatmap         = context.get("heatmap") or _build_heatmap(urgency_level, summary)
    duration        = (time.time() - t0) * 1000

    _lf_span(trace, "build_output",
        {"nb_actions": len(validated_actions), "nb_alerts": len(real_time_alerts)},
        {"strategie_summary": strategie_summary[:150], "cause_racine": cause_racine[:100],
         "focus_produits": focus_produits[:3], "rag_used": rag_used},
        duration)

    # ── Score qualité Langfuse ────────────────────────────────────────────────
    try:
        from app.core.langfuse import get_langfuse
        lf = get_langfuse()
        if lf:
            metrics   = state.get("metrics") or {}
            total_ms  = sum(metrics.get(k,0) for k in
                ["stratege_context_ms","stratege_rag_ms","stratege_analyze_ms","stratege_llm_ms"])
            uw = {"CRITICAL":1.0,"HIGH":0.8,"MEDIUM":0.5,"LOW":0.2}.get(urgency_level,0.5)
            aw = min(1.0, len(validated_actions) / 3)
            rw = 0.2 if rag_used else 0.0
            sw = max(0.0, 1.0 - total_ms / 60000)
            lf.score(trace_id=f"{_cycle_id(state)}-stratege", name="stratege-quality",
                     value=round(uw*0.35 + aw*0.35 + rw + sw*0.1, 3),
                     comment=f"urgency={urgency_level} gap={gap_pct:.1f}% actions={len(validated_actions)}")
            lf.flush()
    except Exception:
        pass

    logger.info(f"[STRATEGE] ✓ {len(validated_actions)} actions | RAG={'✓' if rag_used else '✗'}({nb_scripts}) | alertes={len(real_time_alerts)}")

    output = {**state,
        "strategie": strategie_summary, "strategie_actions": validated_actions,
        "focus_produits": focus_produits, "message_manager": message_manager,
        "cause_racine": cause_racine, "context_heatmap": heatmap,
        "context_signals": context_signals, "external_context": context,
        "real_time_alerts": real_time_alerts, "route_to": "coach"}
    log.node_done("build_output", log_id, output, duration,
                  {"nb_actions": len(validated_actions), "nb_alerts": len(real_time_alerts)})

    metrics = dict(state.get("metrics") or {})
    metrics["stratege_build_ms"] = round(duration)
    metrics["nodes_executed"] = int(metrics.get("nodes_executed", 0)) + 1
    metrics["stratege_total_ms"] = sum(metrics.get(k,0) for k in
        ["stratege_context_ms","stratege_rag_ms","stratege_analyze_ms",
         "stratege_llm_ms","stratege_build_ms"])
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 5b — Self-Critique (Reflexion Pattern)
# ══════════════════════════════════════════════════════════════════════════════

async def node_self_critique_stratege(state: SalesAgentState) -> dict:
    """
    Auto-évaluation des actions stratégiques avant transmission au CoachAgent.
    Pattern Reflexion (Shinn et al. 2023).

    Vérifie :
      1. Cohérence avec le gap (les actions aident-elles à combler l'écart ?)
      2. Cohérence avec le stock (recommande-t-on des produits disponibles ?)
      3. Spécificité (chaque action a un produit_cible et un argument précis ?)
      4. Confidence globale >= 0.6

    Si critique NOK (score < 0.6) → critique_feedback injecté pour historique.
    Le CoachAgent reçoit toujours les actions (pas de re-boucle en prod).
    """
    cid = _cycle_id(state); sid = _store_id(state)
    log = AgentLogger("stratege", cid, sid)
    log_id = log.node_start("self_critique", state)
    t0 = time.time()
    logger.info("[STRATEGE] Node 5b — self_critique")

    actions   = state.get("strategie_actions") or []
    gap_pct   = float(state.get("gap_objectif", 0))
    urgency   = state.get("urgency_level", "MEDIUM")
    rag_used  = state.get("rag_used", False)
    cause     = state.get("cause_racine", "")

    checks: dict = {}
    total_score = 0.0
    feedback_parts = []

    # Vérif 1 — Au moins 3 actions générées
    nb_actions = len(actions)
    check1_ok = nb_actions >= 2
    check1_score = min(1.0, nb_actions / 3)
    checks["nb_actions"] = {"passed": check1_ok, "score": check1_score,
                             "reason": f"{nb_actions}/3 actions"}
    total_score += check1_score * 0.25
    if not check1_ok:
        feedback_parts.append(f"Seulement {nb_actions} action(s) — générer au moins 3.")

    # Vérif 2 — Chaque action a produit_cible et argument_vente non-vides
    complete_actions = sum(
        1 for a in actions
        if a.get("produit_cible") and a.get("argument_vente")
    )
    check2_ok = complete_actions == nb_actions
    check2_score = complete_actions / max(nb_actions, 1)
    checks["actions_complete"] = {"passed": check2_ok, "score": check2_score,
                                   "reason": f"{complete_actions}/{nb_actions} complètes"}
    total_score += check2_score * 0.35
    if not check2_ok:
        feedback_parts.append("Certaines actions manquent de produit_cible ou d'argument.")

    # Vérif 3 — Actions cohérentes avec l'urgence (si CRITICAL/HIGH, au moins 1 action
    #            dont l'impact_estime dépasse le gap résiduel)
    if urgency in ("CRITICAL", "HIGH") and actions and gap_pct > 20:
        gap_tnd = float((state.get("pos_data") or {}).get("daily_target", 1000)) * gap_pct / 100
        def _parse_impact(s: str) -> float:
            import re as _re
            m = _re.search(r"[\d\s]+", s.replace(" ", "").replace(",", "."))
            return float(m.group().replace(" ", "")) if m else 0.0

        high_ticket = any(_parse_impact(a.get("impact_estime", "0")) >= max(gap_tnd * 0.3, 50) for a in actions)
        check3_ok = high_ticket
        check3_score = 1.0 if high_ticket else 0.4
    else:
        check3_ok = True
        check3_score = 1.0
    checks["urgency_coherence"] = {
        "passed": check3_ok, "score": check3_score,
        "reason": "impact suffisant pour combler le gap" if check3_ok else f"aucune action couvrant ≥30% du gap ({gap_pct:.0f}%)"
    }
    total_score += check3_score * 0.25
    if not check3_ok:
        feedback_parts.append(f"Urgence {urgency} : prévoir une action à impact >= {gap_pct*0.3:.0f}% du gap.")

    # Vérif 4 — RAG utilisé si gap > 20%
    if gap_pct > 20:
        check4_ok = rag_used
        check4_score = 1.0 if rag_used else 0.6
    else:
        check4_ok = True
        check4_score = 1.0
    checks["rag_usage"] = {"passed": check4_ok, "score": check4_score,
                            "reason": "RAG scripts disponibles" if rag_used else "sans scripts RAG"}
    total_score += check4_score * 0.15
    if not check4_ok:
        feedback_parts.append("Gap > 20% sans scripts RAG — les arguments manquent de précision.")

    critique_passed = total_score >= 0.6
    critique_feedback = " | ".join(feedback_parts) if feedback_parts else "✓ Stratégie valide"

    # ── HITL trigger : critique NOK + urgence critique + gap sévère ─────────
    _HITL_THRESHOLD_GAP = 40.0
    _HITL_URGENCIES = ("CRITICAL", "HIGH")
    hitl_required = (
        not critique_passed
        and urgency in _HITL_URGENCIES
        and gap_pct >= _HITL_THRESHOLD_GAP
    )
    if hitl_required:
        try:
            import asyncio
            from app.api.hitl import submit_hitl_review
            asyncio.create_task(submit_hitl_review(
                store_id       = _store_id(state),
                cycle_id       = _cycle_id(state),
                urgency_level  = urgency,
                gap_pct        = gap_pct,
                critique_score = round(total_score, 3),
                critique_feedback = critique_feedback,
                strategie_summary = state.get("strategie", "") or state.get("strategie_data", {}).get("strategie_summary", ""),
                actions        = state.get("strategie_actions", [])[:3],
                source         = "sales",
            ))
            logger.warning("[HITL] Review soumis — urgency=%s gap=%.0f%% score=%.2f",
                           urgency, gap_pct, total_score)
        except Exception as _hitl_err:
            logger.warning("[HITL] submit échoué (non-bloquant): %s", _hitl_err)

    duration = (time.time() - t0) * 1000
    logger.info(
        "[STRATEGE] Self-critique — score=%.2f passed=%s hitl=%s | %s",
        total_score, critique_passed, hitl_required, critique_feedback[:80],
    )

    output = {
        **state,
        "critique_score":    round(total_score, 3),
        "critique_passed":   critique_passed,
        "critique_checks":   checks,
        "critique_feedback": critique_feedback,
        "critique_cycles":   int(state.get("critique_cycles", 0)) + 1,
        "hitl_required":     hitl_required,
    }
    log.node_done("self_critique", log_id, output, duration,
                  {"score": total_score, "passed": critique_passed})

    metrics = dict(state.get("metrics") or {})
    metrics["stratege_critique_ms"] = round(duration)
    metrics["nodes_executed"] = int(metrics.get("nodes_executed", 0)) + 1
    return {**output, "metrics": metrics}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _extract_from_partial_json(content):
    result = {}
    for field in ["cause_racine","strategie_summary","message_manager"]:
        m = re.search(rf'"{field}"\s*:\s*"([^"]+)"', content)
        if m: result[field] = m.group(1)
    fp = re.search(r'"focus_produits"\s*:\s*\[([^\]]+)\]', content)
    if fp: result["focus_produits"] = re.findall(r'"([^"]+)"', fp.group(1))
    actions_raw = re.findall(
        r'"action"\s*:\s*"([^"]+)"[^}]*?"produit_cible"\s*:\s*"([^"]+)"', content, re.DOTALL)
    if actions_raw:
        result["actions"] = [{"priorite":i+1,"action":a[0],"produit_cible":a[1],
                               "argument_vente":"","impact_estime":""}
                              for i,a in enumerate(actions_raw[:3])]
    return result


def _make_fallback_strategy(gap_pct, urgency, summary, hours_remaining, rag_scripts=None):
    rag_scripts = rag_scripts or []
    w_eff = float(summary.get("weather_effect", 0))
    is_rainy = w_eff <= -0.10; w_label = summary.get("weather_label", "")
    if rag_scripts:
        actions = [{"priorite":i,"action":s["action"][:150],"produit_cible":s["produit"][:100],
                    "argument_vente":s["argument"][:200],"impact_estime":s["impact"][:100]}
                   for i,s in enumerate(rag_scripts[:3],1)]
        focus = list(dict.fromkeys(s["produit"] for s in rag_scripts[:3]))
    elif is_rainy:
        actions = [
            {"priorite":1,"action":"Proposer AirPods Pro 3 résistants eau","produit_cible":"AirPods Pro 3","argument_vente":"Certifiés IPX4 résistants à l'eau","impact_estime":"+279 TND panier"},
            {"priorite":2,"action":"Cross-sell Box Fibre 1Go","produit_cible":"Box Fibre 1Go","argument_vente":"Fibre 59 TND/mois installation offerte","impact_estime":"+59 TND récurrent"},
            {"priorite":3,"action":"Assurance Premium systématique","produit_cible":"Assurance Premium","argument_vente":"9 TND/mois remplacement 48h","impact_estime":"Marge 80%"},
        ]; focus = ["AirPods Pro 3","Box Fibre 1Go","Assurance Premium"]
    elif gap_pct > 40:
        actions = [
            {"priorite":1,"action":"Bundle iPhone 16 Pro + Forfait 5G Max + Assurance","produit_cible":"iPhone 16 Pro","argument_vente":"0 DT aujourd'hui, 54 TND/mois sur 24 mois","impact_estime":"+1 357 TND"},
            {"priorite":2,"action":"Convertir recharges vers Forfait Flexi 25Go","produit_cible":"Forfait Flexi 25Go","argument_vente":"3 recharges = 30 TND. Flexi = 29 TND + illimité","impact_estime":"+29 TND récurrent"},
            {"priorite":3,"action":"Closing express clients indécis","produit_cible":"Samsung Galaxy A55 5G","argument_vente":"Il reste 2 unités — offre 0% expire ce soir","impact_estime":"+899 TND"},
        ]; focus = ["iPhone 16 Pro","Forfait 5G Max","Forfait Flexi 25Go"]
    else:
        actions = [
            {"priorite":1,"action":"Upsell Assurance Premium sur 100% ventes terminaux","produit_cible":"Assurance Premium","argument_vente":"9 TND/mois remplacement 48h garanti","impact_estime":"Marge 80%"},
            {"priorite":2,"action":"Cross-sell Box Fibre 1Go","produit_cible":"Box Fibre 1Go","argument_vente":"Fibre 1Go 59 TND/mois","impact_estime":"+59 TND récurrent"},
            {"priorite":3,"action":"Proposer Cloud Backup 1To","produit_cible":"Cloud Backup 1To","argument_vente":"15 TND/mois photos sauvegardées automatiquement","impact_estime":"Marge haute"},
        ]; focus = ["Assurance Premium","Box Fibre 1Go","Cloud Backup 1To"]
    return {
        "cause_racine": f"Gap {gap_pct:.1f}% — {w_label or 'performance à améliorer'}",
        "actions": actions, "focus_produits": focus,
        "message_manager": f"Gap {gap_pct:.0f}% | {hours_remaining}h | {'URGENT' if gap_pct>40 else 'À surveiller'}",
        "strategie_summary": (f"Gap {gap_pct:.1f}% avec {hours_remaining}h restantes. "
                              f"{'Météo — accessoires priorité.' if is_rainy else ''} Focus: {', '.join(focus[:2])}."),
    }


def _build_context_signals(context, factors):
    signals = []; summary = context.get("summary",{}); holidays = context.get("holidays",{}); events = context.get("events",{})
    w_eff = float(summary.get("weather_effect",0))
    signals.append({"type":"weather","level":"high" if w_eff<=-0.25 else "med" if w_eff<=-0.10 else "low",
        "label":f"{summary.get('weather_icon','☀️')} {summary.get('weather_label','Normal')} — {w_eff:+.0%} trafic","value":w_eff})
    if holidays.get("is_holiday_today"):
        signals.append({"type":"holiday","level":"high","label":f"🎉 {holidays.get('today_holiday',{}).get('name','')}","value":1.0})
    else:
        next_hol = holidays.get("next_holiday") or {}
        if next_hol.get("name"):
            days = next_hol.get("days_until", 0)
            signals.append({"type":"holiday","level":"med" if days<=7 else "low",
                "label":f"📅 {next_hol['name']} dans {days}j","value":0.5 if days<=7 else 0.2})
    all_promos = (events.get("promotions",[]) or []) + (events.get("new_offers",[]) or [])
    if all_promos:
        signals.append({"type":"event","level":"med","label":f"🎯 {len(all_promos)} offre(s) — {all_promos[0].get('title','')[:40]}","value":0.3})
    return signals


def _build_heatmap(urgency, summary):
    is_rainy = float(summary.get("weather_effect",0)) <= -0.10
    if urgency in ("HIGH","CRITICAL"): t=["med","high","high","crit","crit","high","med","low"]; r=["low","med","high","high","crit","crit","high","med"]
    elif urgency=="MEDIUM":            t=["low","med","med","high","high","med","med","low"]; r=["low","low","med","med","high","med","med","low"]
    else:                              t=["low","low","med","med","med","low","low","low"]; r=["low","low","low","med","med","low","low","low"]
    return {"hours":["11AM","12PM","1PM","2PM","3PM","4PM","5PM","6PM"],
            "traffic":t,"weather":["high"]*8 if is_rainy else ["low"]*8,
            "stock":["low","low","med","high","high","crit","crit","high"],
            "event":["low","low","low","low","med","med","high","high"],"risk":r}


async def node_analyze_root_cause(state: SalesAgentState) -> dict:
    return await node_analyze_context(state)
