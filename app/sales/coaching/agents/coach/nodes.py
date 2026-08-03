"""
nodes.py — Agent Coach FINAL v4
================================
OpenRouter injecté dans node_generate_conseil.
RAG correctement injecté depuis coach_context.
"""
import logging
import os
import re
import time
from datetime import datetime

from app.core.agent_logger import AgentLogger
from app.core.http import get_http_client
from app.sales.core.state import SalesAgentState
from .tools import (
    search_rag, format_rag_for_prompt, format_history_for_prompt,
    format_actions_for_prompt, get_advisor_history, save_interaction,
    build_fallback_response,
)
from .prompts import (
    get_specialized_prompt, get_opening_prompt,
    detect_question_type, TONE_BY_URGENCY,
)

logger = logging.getLogger(__name__)

OLLAMA_URL       = os.getenv("OLLAMA_BASE_URL",    "http://localhost:11434")
OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL",        "llama3.2:latest")
OPENROUTER_KEY   = os.getenv("OPENROUTER_API_KEY",  "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL",    "openai/gpt-oss-120b:free")
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"

USE_OPENROUTER = bool(OPENROUTER_KEY)


def _cycle_id(state):
    return (state.get("metrics") or {}).get("cycle_id","") or state.get("cycle_id","unknown")

def _store_id_from_ctx(ctx):
    return ctx.get("store_id","ALL")

def _update_metrics(state, key, value):
    metrics = dict(state.get("metrics") or {})
    metrics[key] = value
    metrics["nodes_executed"] = int(metrics.get("nodes_executed",0)) + 1
    return metrics


# ══════════════════════════════════════════════════════════════════════════════
# NODE 1 — Load Context
# ══════════════════════════════════════════════════════════════════════════════

async def node_load_context(state: SalesAgentState) -> dict:
    cid = _cycle_id(state)
    log = AgentLogger("coach", cid, "ALL")
    log_id = log.node_start("load_context", state)
    t0 = time.time()
    logger.info("[COACH] Node 1 — load_context")

    now = datetime.now(); hour = now.hour
    pos_data  = state.get("pos_data") or {}
    ca_today  = float(pos_data.get("current_revenue", 0))
    ca_target = float(pos_data.get("daily_target", 1007))
    nb_ventes = int(pos_data.get("nb_transactions_today", 0))
    gap_pct   = float(state.get("gap_objectif", 0))
    gap_tnd   = max(0.0, ca_target - ca_today)
    urgency   = state.get("urgency_level", "MEDIUM")
    forecast_eod = float(state.get("forecast_eod", 0))
    analyst_sum  = state.get("analyst_summary", "") or ""
    actions   = state.get("strategie_actions", []) or []
    cause_racine = state.get("cause_racine", "") or ""
    focus     = state.get("focus_produits", []) or []

    ext_ctx       = state.get("external_context") or {}
    weather_sum   = ext_ctx.get("summary", {}) or {}
    weather_str   = f"{weather_sum.get('weather_icon','🌤️')} {weather_sum.get('weather_label','Tunis')} {weather_sum.get('temperature',22)}°C".strip()
    weather_effect = float(weather_sum.get("weather_effect", 0))
    is_rainy      = weather_effect <= -0.10

    message      = pos_data.get("coach_message","") or (state.get("metrics") or {}).get("coach_message","") or ""
    advisor_name = pos_data.get("advisor_name","") or (state.get("metrics") or {}).get("advisor_name","Conseiller") or "Conseiller"
    performance  = round((ca_today/ca_target)*100,1) if ca_target > 0 else 0
    coach_score  = round(min(1.0, performance/100*0.9+0.05),2)
    hours_left   = max(0, 20-hour)
    question_type = detect_question_type(message) if message else "opening"
    store_id     = pos_data.get("store_id","ALL")

    coach_context = {
        "advisor_name":advisor_name,"store_id":store_id,
        "current_hour":hour,"hours_left":hours_left,
        "ca_today":ca_today,"ca_target":ca_target,
        "gap_pct":gap_pct,"gap_tnd":gap_tnd,"nb_ventes":nb_ventes,
        "urgency":urgency,"forecast_eod":forecast_eod,
        "analyst_summary":analyst_sum,"actions":actions,
        "cause_racine":cause_racine,"focus_produits":focus,
        "weather":weather_str,"weather_effect":weather_effect,"is_rainy":is_rainy,
        "performance":performance,"coach_score":coach_score,
        "message":message,"question_type":question_type,
        "rag_txt":"","rag_scripts":[],"history_txt":"","history":[],
    }

    logger.info(f"[COACH] {advisor_name} | perf={performance:.0f}% | gap={gap_pct:.0f}% ({gap_tnd:.0f} TND) | type={question_type}")
    duration = (time.time()-t0)*1000
    output = {**state, "coach_context": coach_context}
    log.node_done("load_context", log_id, output, duration,
                  {"advisor":advisor_name,"performance":performance,"question_type":question_type})
    return {**output, "metrics": _update_metrics(state,"coach_context_ms",round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 2 — RAG Search
# ══════════════════════════════════════════════════════════════════════════════

async def node_rag_search(state: SalesAgentState) -> dict:
    cid = _cycle_id(state); ctx = state.get("coach_context",{})
    sid = _store_id_from_ctx(ctx)
    log = AgentLogger("coach", cid, sid)
    log_id = log.node_start("rag_search", state)
    t0 = time.time()
    logger.info("[COACH] Node 2 — rag_search")

    message       = ctx.get("message","")
    gap_pct       = ctx.get("gap_pct",0)
    hour          = ctx.get("current_hour", datetime.now().hour)
    is_rainy      = ctx.get("is_rainy",False)
    focus         = ctx.get("focus_produits",[])
    question_type = ctx.get("question_type","general")

    parts = []
    if message: parts.append(message[:150])
    type_queries = {
        "script":    "script vente démonstration argumentaire pitch présentation",
        "objection": "objection prix concurrent hésitation réfutation traitement",
        "closing":   "closing signature décision finaliser client hésitant indécis",
        "meteo":     "météo pluie couvert accessoires résistants eau protection",
        "upsell":    "upsell accessoire complément bundle panier après vente",
        "forfait":   "conversion recharge forfait 5G data illimitée abonnement",
        "objectif":  "rattraper gap objectif action urgente bundle fermeture",
        "general":   "conseil vente coaching performance boutique",
    }
    parts.append(type_queries.get(question_type,"conseil vente coaching"))
    if gap_pct > 40:   parts.append("gap critique urgent closing intensif bundle")
    elif gap_pct > 20: parts.append("gap modéré améliorer performance upsell")
    if is_rainy:       parts.append("pluie météo accessoires résistants eau AirPods Watch")
    if 16 <= hour <= 18: parts.append("pic trafic heure pointe 16h maximiser")
    elif hour >= 19:     parts.append("soirée closing rapide dernière chance fermeture")
    if focus: parts.extend(focus[:2])

    rag_query = " ".join(parts)
    scripts   = search_rag(rag_query, hour, top_k=3, store_id=sid)
    rag_txt   = format_rag_for_prompt(scripts)
    rag_used  = len(scripts) > 0 and bool(rag_txt.strip())

    if rag_used:
        logger.info(f"[COACH RAG] {len(scripts)} scripts | top={scripts[0]['categorie']} | score={scripts[0]['score']:.3f}")
    else:
        logger.info("[COACH RAG] Aucun script trouvé")

    updated_ctx = {**ctx, "rag_txt": rag_txt, "rag_scripts": scripts}
    duration = (time.time()-t0)*1000
    output = {**state, "rag_context":scripts, "rag_query":rag_query,
              "rag_used":rag_used, "nb_rag_scripts":len(scripts), "coach_context":updated_ctx}
    log.node_done("rag_search", log_id, output, duration,
                  {"nb_scripts":len(scripts),"rag_used":rag_used,"question_type":question_type})
    return {**output, "metrics": _update_metrics(state,"coach_rag_ms",round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 3 — Load Advisor History
# ══════════════════════════════════════════════════════════════════════════════

async def node_load_advisor_history(state: SalesAgentState) -> dict:
    cid = _cycle_id(state); ctx = state.get("coach_context",{})
    sid = _store_id_from_ctx(ctx)
    log = AgentLogger("coach", cid, sid)
    log_id = log.node_start("load_advisor_history", state)
    t0 = time.time()
    logger.info("[COACH] Node 3 — load_advisor_history")

    advisor_name = ctx.get("advisor_name","")
    history      = get_advisor_history(advisor_name, limit=5)
    history_txt  = format_history_for_prompt(history)
    nb_today     = sum(1 for h in history if _is_today(h.get("created_at")))
    logger.info(f"[COACH] Historique {advisor_name}: {len(history)} interactions | {nb_today} aujourd'hui")

    updated_ctx = {**ctx, "history":history, "history_txt":history_txt, "nb_today":nb_today}
    duration = (time.time()-t0)*1000
    output = {**state, "feedback_history":history, "coach_context":updated_ctx}
    log.node_done("load_advisor_history", log_id, output, duration,
                  {"advisor":advisor_name,"nb_history":len(history)})
    return {**output, "metrics": _update_metrics(state,"coach_history_ms",round(duration))}


def _is_today(dt):
    if not dt: return False
    try: return dt.date() == datetime.now().date() if hasattr(dt,"date") else False
    except Exception: return False


# ══════════════════════════════════════════════════════════════════════════════
# NODE 4 — Generate Conseil (OpenRouter → Ollama fallback)
# ══════════════════════════════════════════════════════════════════════════════

async def _call_openrouter_coach(system: str, user_prompt: str,
                                  urgency: str, question_type: str) -> tuple[str, float]:
    t0 = time.time()
    try:
        max_tokens  = 280 if urgency in ("HIGH","CRITICAL") else 380
        temperature = 0.20 if question_type in ("script","objection","closing") else 0.28
        resp = await get_http_client().post(OPENROUTER_URL, timeout=30,
            headers={"Authorization":f"Bearer {OPENROUTER_KEY}",
                     "Content-Type":"application/json",
                     "HTTP-Referer":"https://github.com/MALEKALADAB11/multi-agent-sales-inventory",
                     "X-Title":"AI Sales Coach Ooredoo"},
            json={"model":OPENROUTER_MODEL,"max_tokens":max_tokens,"temperature":temperature,
                  "messages":[{"role":"system","content":system},
                              {"role":"user","content":user_prompt}]})
        data = resp.json()
        if "error" in data:
            logger.warning(f"[COACH OR] {data['error'].get('message','?')[:100]}")
            return "", (time.time()-t0)*1000
        reply = data["choices"][0]["message"]["content"].strip()
        for prefix in ["Réponse:","Coach:","CoachAgent:","Here is","Based on"]:
            if reply.lower().startswith(prefix.lower()):
                reply = reply[len(prefix):].strip()
        if reply.startswith("{"):
            m = re.search(r'"(?:reply|response|conseil|message|text)"\s*:\s*"([^"]+)"', reply, re.IGNORECASE)
            reply = m.group(1).strip() if m else ""
        ms = (time.time()-t0)*1000
        logger.info(f"[COACH OR] OK — {len(reply)} chars | {ms:.0f}ms | {OPENROUTER_MODEL}")
        return reply, ms
    except Exception as e:
        logger.warning(f"[COACH OR] {str(e)[:80]}")
        return "", (time.time()-t0)*1000


async def node_generate_conseil(state: SalesAgentState) -> dict:
    cid = _cycle_id(state); ctx = state.get("coach_context",{})
    sid = _store_id_from_ctx(ctx)
    log = AgentLogger("coach", cid, sid)
    log_id = log.node_start("generate_conseil", state)
    t0 = time.time()
    logger.info("[COACH] Node 4 — generate_conseil")

    message       = ctx.get("message","")
    advisor_name  = ctx.get("advisor_name","Conseiller")
    question_type = ctx.get("question_type","general")
    prenom        = advisor_name.split()[-1].title() if advisor_name else "Conseiller"
    gap_pct       = float(ctx.get("gap_pct",0))
    gap_tnd       = float(ctx.get("gap_tnd",0))
    urgency       = ctx.get("urgency","MEDIUM")
    ca_today      = float(ctx.get("ca_today",0))
    ca_target     = float(ctx.get("ca_target",1007))
    nb_ventes     = int(ctx.get("nb_ventes",0))
    hour          = int(ctx.get("current_hour",datetime.now().hour))
    hours_left    = int(ctx.get("hours_left",1))
    weather       = ctx.get("weather","Tunis")
    weather_effect = float(ctx.get("weather_effect",0))
    forecast_eod  = float(ctx.get("forecast_eod",0))
    performance   = float(ctx.get("performance",0))
    coach_score   = float(ctx.get("coach_score",0))
    actions       = ctx.get("actions",[])

    # RAG depuis ctx (injecté par node_rag_search)
    rag_txt = ctx.get("rag_txt","")
    if not rag_txt:
        rag_scripts_raw = state.get("rag_context",[])
        if rag_scripts_raw:
            rag_txt = format_rag_for_prompt(rag_scripts_raw)
            logger.info(f"[COACH] RAG reconstruit depuis state.rag_context ({len(rag_scripts_raw)} scripts)")

    history_txt = ctx.get("history_txt","")
    actions_txt = format_actions_for_prompt(actions)

    # ── Sélectionner le prompt ─────────────────────────────────────────────────
    if question_type == "opening" or not message:
        user_prompt = get_opening_prompt(
            advisor_name=advisor_name, performance=performance, gap_pct=gap_pct,
            gap_tnd=gap_tnd, ca_today=ca_today, ca_target=ca_target,
            weather=weather, hours_left=hours_left, rag_context=rag_txt, actions_txt=actions_txt)
        tone = TONE_BY_URGENCY.get(urgency,"DYNAMIC tone")
        system = (f"Tu es le CoachAgent d'Ooredoo Tunisie. Génère un message d'ouverture "
                  f"personnalisé en français. Ton : {tone}. Max 100 mots. "
                  f"Commence par le prénom. Termine par un verbe d'action.")
    else:
        system, user_prompt = get_specialized_prompt(
            question_type=question_type, message=message, advisor_name=advisor_name,
            gap_pct=gap_pct, gap_tnd=gap_tnd, urgency=urgency, weather=weather,
            weather_effect=weather_effect, hours_left=hours_left, current_hour=hour,
            rag_context=rag_txt, actions_txt=actions_txt, performance=performance,
            ca_today=ca_today, ca_target=ca_target, nb_ventes=nb_ventes,
            forecast_eod=forecast_eod, coach_score=coach_score)

    rag_available = bool(rag_txt and rag_txt.strip() and
                         "Aucun script" not in rag_txt and "No similar" not in rag_txt)
    logger.info(f"[COACH] type={question_type} | urgence={urgency} | "
                f"RAG={'✓' if rag_available else '✗'} | perf={performance:.0f}% | "
                f"{'OpenRouter' if USE_OPENROUTER else 'Ollama'}")

    reply = ""; llm_ok = False; confidence = 0.65; llm_source = "fallback"

    # ── OpenRouter en priorité ─────────────────────────────────────────────────
    if USE_OPENROUTER:
        reply, _ = await _call_openrouter_coach(system, user_prompt, urgency, question_type)
        if reply:
            llm_ok = True
            confidence = 0.95 if rag_available else 0.88
            llm_source = f"openrouter+rag" if rag_available else "openrouter"

    # ── Fallback Ollama ────────────────────────────────────────────────────────
    if not reply:
        try:
            from langchain_ollama import ChatOllama
            from langchain_core.messages import HumanMessage, SystemMessage
            llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_URL,
                temperature=0.2 if question_type in ("script","objection") else 0.25,
                num_predict=180 if urgency in ("HIGH","CRITICAL") else 280, num_ctx=3000)
            resp = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content=user_prompt)])
            reply = resp.content.strip()
            for prefix in ["Réponse:","Coach:","CoachAgent:","IA:","Assistant:"]:
                if reply.startswith(prefix): reply = reply[len(prefix):].strip()
            llm_ok = True
            confidence = 0.88 if rag_available else 0.78
            llm_source = "ollama+rag" if rag_available else "ollama"
        except Exception as e:
            logger.warning(f"[COACH] LLM fallback: {str(e)[:60]}")
            log.fallback("generate_conseil", log_id, str(e), (time.time()-t0)*1000)
            reply = build_fallback_response(
                message=message or f"Accueil {advisor_name}", gap_pct=gap_pct,
                urgency=urgency, weather=weather, actions=actions,
                scripts=state.get("rag_context",[]), prenom=prenom)
            llm_source = "fallback+rag" if rag_available else "fallback"

    logger.info(f"[COACH] LLM OK — {len(reply)} chars | conf={confidence:.2f} | src={llm_source}")

    conseil_result = {"reply":reply,"source":llm_source,"rag_used":rag_available,
                      "confidence":confidence,"question_type":question_type,
                      "timestamp":datetime.now().isoformat()}
    duration = (time.time()-t0)*1000
    output = {**state, "conseil_final":reply, "coach_context":{**ctx,"conseil_result":conseil_result}}
    log.node_done("generate_conseil", log_id, output, duration,
                  {"llm_ok":llm_ok,"confidence":confidence,"reply_len":len(reply),"source":llm_source})
    return {**output, "metrics": _update_metrics(state,"coach_llm_ms",round(duration))}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 5 — Save Conseil
# ══════════════════════════════════════════════════════════════════════════════

async def node_save_conseil(state: SalesAgentState) -> dict:
    cid = _cycle_id(state); ctx = state.get("coach_context",{})
    sid = _store_id_from_ctx(ctx)
    log = AgentLogger("coach", cid, sid)
    log_id = log.node_start("save_conseil", state)
    t0 = time.time()
    logger.info("[COACH] Node 5 — save_conseil")

    result         = ctx.get("conseil_result",{})
    advisor_name   = ctx.get("advisor_name","")
    message        = ctx.get("message","")
    reply          = state.get("conseil_final","")
    gap_pct        = ctx.get("gap_pct",0)
    urgency        = ctx.get("urgency","MEDIUM")
    rag_used       = result.get("rag_used",False)
    nb_rag_scripts = state.get("nb_rag_scripts",0)
    question_type  = result.get("question_type","general")
    confidence     = result.get("confidence",0.0)

    if advisor_name:
        save_interaction(advisor_name=advisor_name, store_id=sid,
            message=message or f"[opening] {advisor_name}", response=reply,
            gap_pct=gap_pct, urgency=urgency, rag_used=rag_used,
            nb_rag_scripts=nb_rag_scripts, conseil_type=question_type, confidence=confidence)
        logger.info(f"[COACH] ✓ {advisor_name} | type={question_type} | conf={confidence:.2f} | RAG={'✓' if rag_used else '✗'}")

    duration = (time.time()-t0)*1000
    log.node_done("save_conseil", log_id, state, duration,
                  {"advisor":advisor_name,"conseil_type":question_type,"rag_used":rag_used})
    metrics = dict(state.get("metrics") or {})
    metrics["coach_save_ms"] = round(duration)
    metrics["nodes_executed"] = int(metrics.get("nodes_executed",0)) + 1
    metrics["coach_total_ms"] = sum(metrics.get(k,0) for k in
        ["coach_context_ms","coach_rag_ms","coach_history_ms","coach_llm_ms","coach_save_ms"])
    metrics["total_ms"] = (metrics.get("analyste_total_ms",0) +
                           metrics.get("stratege_total_ms",0) +
                           metrics.get("coach_total_ms",0))
    logger.info(f"[COACH] ✓ Cycle complet | total={metrics.get('total_ms',0):.0f}ms")
    return {**state, "metrics": metrics}
