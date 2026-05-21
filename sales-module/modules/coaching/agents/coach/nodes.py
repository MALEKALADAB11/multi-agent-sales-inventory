"""
Nodes LangGraph de l'Agent Coach Ooredoo.

Flow :
  node_load_context         → enrichit le state avec le contexte live
  node_rag_search           → cherche les scripts similaires dans Milvus
  node_load_advisor_history → charge l'historique PostgreSQL du conseiller
  node_generate_conseil     → génère le conseil via LLM + RAG + stratège
  node_save_conseil         → sauvegarde dans PostgreSQL + log monitoring
"""

import logging
import os
import re
import json
from datetime import datetime

from core.state import SalesAgentState
from .tools import (
    search_rag,
    format_rag_for_prompt,
    format_history_for_prompt,
    format_actions_for_prompt,
    get_advisor_history,
    save_interaction,
    ensure_interactions_table,
    detect_conseil_type,
    build_fallback_response,
    embed_text,
)
from .prompts import COACH_SYSTEM_PROMPT, COACH_USER_PROMPT

logger = logging.getLogger(__name__)

OLLAMA_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL",    "qwen2.5:0.5b")

# Créer la table au démarrage du module
ensure_interactions_table()


# ══════════════════════════════════════════════════════════════════════════════
# NODE 1 — Load Context
# ══════════════════════════════════════════════════════════════════════════════

async def node_load_context(state: SalesAgentState) -> dict:
    """
    Charge et enrichit le contexte pour l'Agent Coach.
    Extrait les données depuis le state partagé (issu de l'Analyste + Stratège).
    """
    logger.info("[COACH] Node 1 — load_context")

    now  = datetime.now()
    hour = now.hour

    # ── Données POS ───────────────────────────────────────────────────────
    pos_data = state.get("pos_data", {}) or {}
    ca_today = float(pos_data.get("current_revenue", 0))
    ca_target = float(pos_data.get("daily_target", 1007))
    nb_ventes = int(pos_data.get("nb_transactions_today", 0))

    # ── Analyste ──────────────────────────────────────────────────────────
    gap_pct     = float(state.get("gap_objectif", 0))
    urgency     = state.get("urgency_level", "MEDIUM")
    forecast    = float(state.get("forecast_eod", 0))
    analyst_sum = state.get("analyst_summary", "")

    # ── Stratège ──────────────────────────────────────────────────────────
    actions        = state.get("strategie_actions", []) or []
    cause_racine   = state.get("cause_racine", "")
    focus_produits = state.get("focus_produits", []) or []

    # ── Météo ─────────────────────────────────────────────────────────────
    ext_ctx = state.get("external_context", {}) or {}
    weather_sum = ext_ctx.get("summary", {}) or {}
    weather_str = (
        f"{weather_sum.get('weather_icon','🌤️')} "
        f"{weather_sum.get('weather_label','Tunis')} "
        f"{weather_sum.get('temperature', 22)}°C"
    ).strip()

    # ── Message entrant (depuis le chat ou le cycle) ───────────────────────
    # Le message peut être passé via pos_data["coach_message"] ou metrics
    message      = (
        pos_data.get("coach_message", "")
        or state.get("metrics", {}).get("coach_message", "")
        or ""
    )
    advisor_name = (
        pos_data.get("advisor_name", "")
        or state.get("metrics", {}).get("advisor_name", "Conseiller")
        or "Conseiller"
    )

    hours_left = max(0, 20 - hour)

    coach_context = {
        # Identité
        "advisor_name":  advisor_name,
        "store_id":      pos_data.get("store_id", "I63"),
        "current_hour":  hour,
        "hours_left":    hours_left,
        # POS
        "ca_today":      ca_today,
        "ca_target":     ca_target,
        "nb_ventes":     nb_ventes,
        # Analyse
        "gap_pct":       gap_pct,
        "urgency":       urgency,
        "forecast_eod":  forecast,
        "analyst_summary": analyst_sum,
        # Stratège
        "actions":       actions,
        "cause_racine":  cause_racine,
        "focus_produits": focus_produits,
        # Météo
        "weather":       weather_str,
        "is_rainy":      weather_sum.get("is_rainy", False),
        # Message à traiter
        "message":       message,
    }

    logger.info(
        f"[COACH] Contexte chargé: {advisor_name} | "
        f"CA={ca_today:.0f}/{ca_target:.0f} | "
        f"gap={gap_pct:.0f}% | urgence={urgency}"
    )

    return {**state, "coach_context": coach_context}


# ══════════════════════════════════════════════════════════════════════════════
# NODE 2 — RAG Search
# ══════════════════════════════════════════════════════════════════════════════

async def node_rag_search(state: SalesAgentState) -> dict:
    """
    Recherche les scripts de vente les plus pertinents dans Milvus.
    Construit la requête RAG depuis le contexte coach.
    """
    logger.info("[COACH] Node 2 — rag_search")

    ctx      = state.get("coach_context", {})
    message  = ctx.get("message", "")
    gap_pct  = ctx.get("gap_pct", 0)
    urgency  = ctx.get("urgency", "MEDIUM")
    hour     = ctx.get("current_hour", datetime.now().hour)
    is_rainy = ctx.get("is_rainy", False)
    focus    = ctx.get("focus_produits", [])
    store_id = ctx.get("store_id", "I63")

    # Construire la requête RAG
    parts = []

    # La question du conseiller est la priorité
    if message:
        parts.append(message[:200])

    # Contexte gap
    if gap_pct > 60:
        parts.append("gap critique très éloigné objectif urgent action immédiate")
    elif gap_pct > 40:
        parts.append("gap critique objectif loin closing intensif")
    elif gap_pct > 20:
        parts.append("gap modéré performance améliorer upsell")
    else:
        parts.append("performance correcte optimiser panier moyen")

    # Heure
    if 16 <= hour <= 18:
        parts.append("pic trafic heure de pointe maximiser conversion")
    elif hour >= 19:
        parts.append("soirée closing rapide dernières heures fermeture")
    elif hour <= 11:
        parts.append("matin ouverture faible trafic proactif")

    # Météo
    if is_rainy:
        parts.append("météo pluie accessoires résistants eau protection")

    # Focus produits du stratège
    if focus:
        parts.extend(focus[:2])

    rag_query = " ".join(parts)
    logger.info(f"[COACH RAG] Requête: '{rag_query[:100]}'")

    # Recherche Milvus
    scripts = search_rag(rag_query, hour, top_k=3, store_id=store_id)

    rag_txt = format_rag_for_prompt(scripts)
    rag_used = len(scripts) > 0

    logger.info(
        f"[COACH RAG] {len(scripts)} scripts trouvés "
        f"(top: {scripts[0]['categorie'] if scripts else 'N/A'})"
    )

    return {
        **state,
        "rag_context":    scripts,
        "rag_query":      rag_query,
        "rag_used":       rag_used,
        "nb_rag_scripts": len(scripts),
        "coach_context":  {**ctx, "rag_txt": rag_txt, "rag_scripts": scripts},
    }


# ══════════════════════════════════════════════════════════════════════════════
# NODE 3 — Load Advisor History
# ══════════════════════════════════════════════════════════════════════════════

async def node_load_advisor_history(state: SalesAgentState) -> dict:
    """
    Charge l'historique des interactions du conseiller depuis PostgreSQL.
    Permet au coach de personnaliser ses réponses selon l'historique.
    """
    logger.info("[COACH] Node 3 — load_advisor_history")

    ctx          = state.get("coach_context", {})
    advisor_name = ctx.get("advisor_name", "")

    history     = get_advisor_history(advisor_name, limit=5)
    history_txt = format_history_for_prompt(history)

    logger.info(
        f"[COACH] Historique {advisor_name}: "
        f"{len(history)} interactions récentes"
    )

    return {
        **state,
        "feedback_history": history,
        "coach_context": {
            **ctx,
            "history":     history,
            "history_txt": history_txt,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# NODE 4 — Generate Conseil
# ══════════════════════════════════════════════════════════════════════════════

async def node_generate_conseil(state: SalesAgentState) -> dict:
    """
    Génère le conseil personnalisé via LLM Ollama.
    Injecte le contexte RAG + stratège + historique dans le prompt.
    """
    logger.info("[COACH] Node 4 — generate_conseil")

    ctx          = state.get("coach_context", {})
    message      = ctx.get("message", "")
    advisor_name = ctx.get("advisor_name", "Conseiller")
    prenom       = advisor_name.split()[-1].title() if advisor_name else "Conseiller"
    gap_pct      = ctx.get("gap_pct", 0)
    urgency      = ctx.get("urgency", "MEDIUM")
    ca_today     = ctx.get("ca_today", 0)
    ca_target    = ctx.get("ca_target", 1007)
    nb_ventes    = ctx.get("nb_ventes", 0)
    hour         = ctx.get("current_hour", datetime.now().hour)
    hours_left   = ctx.get("hours_left", 1)
    weather      = ctx.get("weather", "Tunis")
    forecast     = ctx.get("forecast_eod", 0)
    actions      = ctx.get("actions", [])
    rag_txt      = ctx.get("rag_txt", "")
    history_txt  = ctx.get("history_txt", "")

    # ── Préparer le prompt ────────────────────────────────────────────────
    actions_txt = format_actions_for_prompt(actions)

    system_prompt = COACH_SYSTEM_PROMPT.format(
        advisor_name = advisor_name,
        ca_today     = ca_today,
        ca_target    = ca_target,
        gap_pct      = gap_pct,
        urgency      = urgency,
        nb_ventes    = nb_ventes,
        current_hour = hour,
        hours_left   = hours_left,
        weather      = weather,
        forecast_eod = forecast,
        actions_txt  = actions_txt,
        rag_txt      = rag_txt or "Aucun script RAG disponible.",
        history_txt  = history_txt or "Première interaction de la journée.",
    )

    # Message par défaut si vide (appel depuis le cycle)
    user_message = message or (
        f"Génère un message de coaching personnalisé pour moi "
        f"(gap {gap_pct:.0f}%, urgence {urgency}, {hours_left}h restantes)"
    )

    # ── Appel LLM ─────────────────────────────────────────────────────────
    reply      = ""
    llm_source = "fallback"
    confidence = 0.65

    try:
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = ChatOllama(
            model       = OLLAMA_MODEL,
            base_url    = OLLAMA_URL,
            temperature = 0.25,
            num_predict = 220,
            num_ctx     = 2500,
        )

        resp = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])

        reply = resp.content.strip()

        # Nettoyer les préfixes indésirables
        for prefix in ["Réponse:", "Coach:", "CoachAgent:", "IA:"]:
            if reply.startswith(prefix):
                reply = reply[len(prefix):].strip()

        llm_source = "llm+rag" if state.get("rag_used") else "llm"
        confidence = 0.91 if state.get("rag_used") else 0.82
        logger.info(f"[COACH] LLM OK ({len(reply)} chars) → {llm_source}")

    except Exception as e:
        logger.warning(f"[COACH] LLM fallback: {str(e)[:60]}")
        reply = build_fallback_response(
            message  = user_message,
            gap_pct  = gap_pct,
            urgency  = urgency,
            weather  = weather,
            actions  = actions,
            scripts  = state.get("rag_context", []),
            prenom   = prenom,
        )
        llm_source = "fallback+rag" if state.get("rag_used") else "fallback"
        confidence = 0.65

    # Détecter le type de conseil
    conseil_type = detect_conseil_type(user_message)

    # Stocker dans le state
    conseil_result = {
        "reply":        reply,
        "source":       llm_source,
        "rag_used":     state.get("rag_used", False),
        "confidence":   confidence,
        "conseil_type": conseil_type,
        "timestamp":    datetime.now().isoformat(),
    }

    logger.info(
        f"[COACH] Conseil généré: type={conseil_type} | "
        f"source={llm_source} | confidence={confidence:.2f}"
    )

    return {
        **state,
        "conseil_final": reply,
        "coach_context": {**ctx, "conseil_result": conseil_result},
    }


# ══════════════════════════════════════════════════════════════════════════════
# NODE 5 — Save Conseil
# ══════════════════════════════════════════════════════════════════════════════

async def node_save_conseil(state: SalesAgentState) -> dict:
    """
    Sauvegarde le conseil généré dans PostgreSQL.
    Log dans agent_logs pour le monitoring.
    """
    logger.info("[COACH] Node 5 — save_conseil")

    ctx    = state.get("coach_context", {})
    result = ctx.get("conseil_result", {})

    advisor_name   = ctx.get("advisor_name", "")
    store_id       = ctx.get("store_id", "I63")
    message        = ctx.get("message", "")
    reply          = state.get("conseil_final", "")
    gap_pct        = ctx.get("gap_pct", 0)
    urgency        = ctx.get("urgency", "MEDIUM")
    rag_used       = result.get("rag_used", False)
    nb_rag_scripts = state.get("nb_rag_scripts", 0)
    conseil_type   = result.get("conseil_type", "general")
    confidence     = result.get("confidence", 0.0)

    # Sauvegarde PostgreSQL
    if advisor_name and message:
        save_interaction(
            advisor_name   = advisor_name,
            store_id       = store_id,
            message        = message,
            response       = reply,
            gap_pct        = gap_pct,
            urgency        = urgency,
            rag_used       = rag_used,
            nb_rag_scripts = nb_rag_scripts,
            conseil_type   = conseil_type,
            confidence     = confidence,
        )
        logger.info(f"[COACH] Interaction sauvegardée → {advisor_name}")

    # Log monitoring
    try:
        from agent_logger import log_node_complete
        log_node_complete(
            log_id      = -1,
            output_state = {
                "conseil_type":   conseil_type,
                "rag_used":       rag_used,
                "nb_rag_scripts": nb_rag_scripts,
                "confidence":     confidence,
                "reply_length":   len(reply),
            },
            duration_ms  = 0,
            metadata     = {
                "advisor": advisor_name,
                "source":  result.get("source", "unknown"),
            },
        )
    except Exception:
        pass

    return {**state}