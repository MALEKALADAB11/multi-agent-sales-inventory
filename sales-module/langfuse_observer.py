"""
langfuse_observer.py — Observabilité centralisée v2 (SDK 2.x)
==============================================================
Compatible Langfuse server v2.95 + SDK langfuse==2.60.0

Trace automatiquement :
  - Cycles agents complets (Analyste + Stratège)
  - Appels LLM (latence, tokens, modèle)
  - Recherches RAG Milvus
  - Interactions Coach Chat
  - Scores de qualité

Déploiement : sales-module/langfuse_observer.py
"""

import os
import time
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Singleton Langfuse ────────────────────────────────────────────────────────
_lf = None

def get_langfuse():
    global _lf
    if _lf is not None:
        return _lf
    try:
        from langfuse import Langfuse
        _lf = Langfuse(
            public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-pfe-local"),
            secret_key = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-pfe-local"),
            host       = os.getenv("LANGFUSE_HOST",       "http://localhost:3001"),
        )
        logger.info("✅ Langfuse v2 connecté → %s", os.getenv("LANGFUSE_HOST", "http://localhost:3001"))
        return _lf
    except Exception as e:
        logger.warning(f"⚠️ Langfuse indisponible: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# trace_full_cycle — Cycle Analyste + Stratège
# ══════════════════════════════════════════════════════════════════════════════

def trace_full_cycle(
    cycle_id: str,
    store_id: str,
    urgency: str,
    gap_pct: float,
    analyst_summary: str,
    nb_actions: int,
    rag_used: bool,
    nb_rag_scripts: int,
    total_ms: float,
    analyst_ms: float = 0,
    stratege_ms: float = 0,
    errors: list = None,
) -> None:
    lf = get_langfuse()
    if lf is None:
        return
    try:
        trace = lf.trace(
            id         = cycle_id,
            name       = "full-cycle",
            user_id    = store_id,
            session_id = f"session-{store_id}-{datetime.now().strftime('%Y%m%d')}",
            tags       = ["full-cycle", store_id, urgency],
            input      = {"store_id": store_id, "cycle_id": cycle_id},
            output     = {
                "urgency":         urgency,
                "gap_pct":         gap_pct,
                "analyst_summary": analyst_summary[:200],
                "nb_actions":      nb_actions,
                "rag_used":        rag_used,
                "nb_rag_scripts":  nb_rag_scripts,
            },
            metadata = {
                "total_ms":    round(total_ms),
                "analyst_ms":  round(analyst_ms),
                "stratege_ms": round(stratege_ms),
                "errors":      errors or [],
            },
        )

        # Span Analyste
        trace.span(
            name    = "analyste",
            input   = {"store_id": store_id},
            output  = {"summary": analyst_summary[:100], "latency_ms": round(analyst_ms)},
            metadata = {"agent": "analyste"},
        )

        # Span Stratège
        trace.span(
            name    = "stratege",
            input   = {"gap_pct": gap_pct, "urgency": urgency},
            output  = {"nb_actions": nb_actions, "rag_used": rag_used, "latency_ms": round(stratege_ms)},
            metadata = {"agent": "stratege", "nb_rag_scripts": nb_rag_scripts},
        )

        # Score qualité cycle
        urgency_w = {"CRITICAL":1.0,"HIGH":0.8,"MEDIUM":0.5,"LOW":0.2}.get(urgency, 0.5)
        action_w  = min(1.0, nb_actions / 3)
        speed_w   = max(0.0, 1.0 - total_ms / 60000)
        score_val = round(urgency_w * 0.3 + action_w * 0.4 + speed_w * 0.3, 3)

        lf.score(
            trace_id = cycle_id,
            name     = "cycle-quality",
            value    = score_val,
            comment  = f"urgency={urgency} gap={gap_pct:.1f}% actions={nb_actions} {total_ms:.0f}ms",
        )
        lf.flush()
        logger.debug(f"[LANGFUSE] Cycle tracé: {cycle_id} score={score_val}")
    except Exception as e:
        logger.warning(f"[LANGFUSE] trace_full_cycle: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# trace_llm_call — Appel LLM générique
# ══════════════════════════════════════════════════════════════════════════════

def trace_llm_call(
    trace_id: str,
    store_id: str,
    agent_name: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response: str,
    latency_ms: float,
    metadata: dict = None,
) -> None:
    lf = get_langfuse()
    if lf is None:
        return
    try:
        import uuid
        trace = lf.trace(
            id         = f"{trace_id}-{agent_name}",
            name       = f"{agent_name}-llm",
            user_id    = store_id,
            session_id = f"session-{store_id}-{datetime.now().strftime('%Y%m%d')}",
            tags       = [agent_name, "llm", store_id],
        )
        trace.generation(
            name  = f"{agent_name}-generation",
            model = model,
            input = [
                {"role": "system", "content": system_prompt[:400]},
                {"role": "user",   "content": user_prompt[:400]},
            ],
            output   = response[:800],
            metadata = {"latency_ms": round(latency_ms), **(metadata or {})},
        )
        lf.flush()
    except Exception as e:
        logger.warning(f"[LANGFUSE] trace_llm_call: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# trace_rag_search — Recherche RAG Milvus
# ══════════════════════════════════════════════════════════════════════════════

def trace_rag_search(
    store_id: str,
    agent_name: str,
    query: str,
    nb_results: int,
    top_score: float,
    latency_ms: float,
) -> None:
    lf = get_langfuse()
    if lf is None:
        return
    try:
        import uuid
        trace = lf.trace(
            id      = f"rag-{uuid.uuid4().hex[:8]}",
            name    = f"{agent_name}-rag",
            user_id = store_id,
            tags    = [agent_name, "rag", store_id],
            input   = {"query": query[:300]},
            output  = {"nb_results": nb_results, "top_score": round(top_score, 3)},
            metadata = {
                "latency_ms":  round(latency_ms),
                "collection":  "coaching_scripts",
                "embed_model": "nomic-embed-text",
            },
        )
        lf.flush()
    except Exception as e:
        logger.warning(f"[LANGFUSE] trace_rag_search: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# trace_coach_chat — Interaction Coach Chat
# ══════════════════════════════════════════════════════════════════════════════

def trace_coach_chat(
    store_id: str,
    advisor_name: str,
    message: str,
    response: str,
    question_type: str,
    rag_used: bool,
    nb_rag_scripts: int,
    confidence: float,
    latency_ms: float,
    gap_pct: float = 0,
    urgency: str = "MEDIUM",
) -> None:
    lf = get_langfuse()
    if lf is None:
        return
    try:
        import uuid
        trace_id = f"coach-{uuid.uuid4().hex[:8]}"
        trace = lf.trace(
            id         = trace_id,
            name       = "coach-chat",
            user_id    = f"{store_id}-{advisor_name}",
            session_id = f"chat-{store_id}-{datetime.now().strftime('%Y%m%d')}",
            tags       = ["coach-chat", store_id, question_type, urgency],
            input      = {"message": message, "advisor": advisor_name},
            output     = {"response": response[:500]},
            metadata   = {
                "question_type":  question_type,
                "rag_used":       rag_used,
                "nb_rag_scripts": nb_rag_scripts,
                "confidence":     confidence,
                "latency_ms":     round(latency_ms),
                "gap_pct":        gap_pct,
                "urgency":        urgency,
            },
        )

        # Generation LLM
        trace.generation(
            name   = "coach-llm-response",
            model  = os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
            input  = [{"role": "user", "content": message}],
            output = response[:500],
            metadata = {
                "latency_ms":    round(latency_ms),
                "question_type": question_type,
                "rag_used":      rag_used,
            },
        )

        # Score qualité réponse
        quality = min(1.0, confidence * (1.2 if rag_used else 1.0))
        lf.score(
            trace_id = trace_id,
            name     = "coach-response-quality",
            value    = round(quality, 3),
            comment  = f"type={question_type} rag={nb_rag_scripts} conf={confidence:.2f} {latency_ms:.0f}ms",
        )
        lf.flush()
        logger.debug(f"[LANGFUSE] Coach chat tracé: {advisor_name} type={question_type} conf={confidence:.2f}")
    except Exception as e:
        logger.warning(f"[LANGFUSE] trace_coach_chat: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# trace_stratege_node — Node Stratège individuel
# ══════════════════════════════════════════════════════════════════════════════

def trace_stratege_node(
    cycle_id: str,
    store_id: str,
    node_name: str,
    input_data: dict,
    output_data: dict,
    latency_ms: float,
    error: str = None,
) -> None:
    lf = get_langfuse()
    if lf is None:
        return
    try:
        import uuid
        trace = lf.trace(
            id      = f"{cycle_id}-{node_name}",
            name    = f"stratege-{node_name}",
            user_id = store_id,
            tags    = ["stratege", node_name, store_id],
            input   = input_data,
            output  = output_data if not error else {"error": error},
            metadata = {
                "latency_ms": round(latency_ms),
                "node":       node_name,
                "status":     "error" if error else "ok",
            },
        )
        lf.flush()
    except Exception as e:
        logger.warning(f"[LANGFUSE] trace_stratege_node: {e}")