"""
coach_chat_rag.py — Endpoint FastAPI Coach Chat avec RAG Milvus.
Remplace ou complète l'endpoint /api/v1/coach/chat dans main.py

Ajouter dans main.py :
    from coach_chat_rag import router as coach_rag_router
    app.include_router(coach_rag_router)
"""

from fastapi import APIRouter
from pydantic import BaseModel
from datetime import datetime
import logging
import os

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/coach", tags=["coach"])


class CoachChatRequest(BaseModel):
    message:      str
    advisor_name: str
    store_id:     str = "I63"
    context:      dict = {}


COACH_SYSTEM_PROMPT = """Tu es le Coach IA d'Ooredoo Tunisie — expert en vente terrain.
Tu aides les conseillers en boutique à améliorer leurs performances en temps réel.

Tes réponses sont :
- CONCRÈTES : produit exact, argument précis, script de vente
- COURTES : max 3 phrases
- BASÉES sur les données réelles de la boutique
- EN FRANÇAIS naturel (pas de jargon technique)

Si tu as des exemples de scripts qui ont fonctionné (fournis dans le contexte RAG),
utilise-les pour enrichir ta réponse.

Catalogue produits clés Ooredoo :
- Forfait Flexi 25Go, Flexi 55Go
- PORTABLE XIAOMI REDMI NOTE 15 8/256 GO
- PORTABLE REDMI 15C 8/256 GO
- PAIEMENT FACTURE POSTPAYE + AVANCE POSTPAYE
- Forfait MIFI PRE 80Go
- Forfait 8Go, Forfait 30 Go
"""


@router.post("/chat")
async def coach_chat(payload: CoachChatRequest):
    """
    Coach Chat avec RAG — répond aux questions des conseillers.
    Enrichi par les scripts de vente historiques de la boutique I63.
    """
    current_hour = datetime.now().hour
    store_id     = payload.store_id
    advisor_name = payload.advisor_name
    message      = payload.message
    context      = payload.context

    # ── RAG: récupérer scripts pertinents ────────────────────────────────────
    rag_context = ""
    try:
        from data.rag_retriever import get_coach_chat_context
        rag_result = await get_coach_chat_context(
            advisor_name = advisor_name,
            question     = message,
            store_id     = store_id,
            current_hour = current_hour,
        )
        if rag_result["available"]:
            rag_context = rag_result["rag_context"]
            logger.info(f"[COACH] RAG: {len(rag_result['scripts'])} scripts pour '{message[:40]}'")
    except Exception as e:
        logger.warning(f"[COACH] RAG non disponible: {e}")

    # ── Construire le prompt ─────────────────────────────────────────────────
    ca_actuel  = context.get("current_revenue", 0)
    objectif   = context.get("daily_target", 1007)
    gap_pct    = round((objectif - ca_actuel) / objectif * 100, 1) if objectif else 0
    meteo      = context.get("weather", "")

    context_str = (
        f"Conseiller: {advisor_name} | "
        f"Boutique: {store_id} | "
        f"Heure: {current_hour}h | "
        f"CA aujourd'hui: {ca_actuel:.0f} TND / Objectif: {objectif:.0f} TND | "
        f"Gap: {gap_pct:.1f}% | "
        f"Météo: {meteo}"
    )

    user_content = f"""Contexte boutique: {context_str}

{rag_context}

Question du conseiller {advisor_name}: {message}

Réponds de façon concrète et actionnable (max 3 phrases, produit et argument précis):"""

    # ── Appel LLM ────────────────────────────────────────────────────────────
    try:
        llm = ChatOllama(
            model       = os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
            base_url    = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature = 0.3,
            num_predict = 200,
            num_ctx     = 2048,
        )

        response = await llm.ainvoke([
            SystemMessage(content=COACH_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ])

        reply = response.content.strip()
        source = "llm+rag" if rag_context else "llm"

        # Sauvegarder l'échange en PostgreSQL
        _save_chat_log(store_id, advisor_name, message, reply, rag_context)

        return {
            "reply":     reply,
            "source":    source,
            "timestamp": datetime.now().isoformat(),
            "rag_used":  bool(rag_context),
        }

    except Exception as e:
        logger.error(f"[COACH] LLM erreur: {e}")
        # Fallback basé sur le RAG seul
        if rag_context:
            fallback = _rag_fallback_reply(message, rag_context, advisor_name, gap_pct, current_hour)
        else:
            fallback = _static_fallback(message, gap_pct, current_hour)

        return {
            "reply":     fallback,
            "source":    "fallback",
            "timestamp": datetime.now().isoformat(),
            "rag_used":  bool(rag_context),
        }


def _rag_fallback_reply(message: str, rag_context: str, advisor_name: str, gap_pct: float, hour: int) -> str:
    """Fallback basé sur les scripts RAG si Ollama échoue."""
    lines = [l for l in rag_context.split("\n") if l.startswith("-") or "action" in l.lower()]
    if lines:
        return f"Basé sur les ventes similaires : {lines[0].strip()}. Gap {gap_pct:.0f}% — focus sur produits à forte valeur maintenant."
    return f"Concentre-toi sur les Forfaits Flexi 25Go et AVANCE POSTPAYE — top produits à {hour}h pour I63."


def _static_fallback(message: str, gap_pct: float, hour: int) -> str:
    """Fallback statique si tout échoue."""
    msg = message.lower()
    if any(x in msg for x in ["gap", "retard", "objectif"]):
        return f"Gap {gap_pct:.0f}% — priorité au bundle Terminal + Forfait. Avance postpayé pour faciliter la décision client. Chaque transaction compte."
    if any(x in msg for x in ["produit", "vendre", "quoi"]):
        return f"À {hour}h: top produits I63 = Forfait Flexi 25Go et Paiement Facture Postpayé. Après paiement facture → proposer upgrade immédiatement."
    if any(x in msg for x in ["client", "hésit", "convainc"]):
        return "Script closing: 'Avec l'avance postpayé, vous partez avec le terminal aujourd'hui sans frais supplémentaires.' → Signature immédiate."
    return f"Focus sur Forfait Flexi 25Go et terminaux Xiaomi. À {hour}h, les clients I63 achètent principalement des forfaits data."


def _save_chat_log(store_id: str, advisor_name: str, question: str, reply: str, rag_used: str):
    """Sauvegarde l'échange chat dans PostgreSQL pour enrichir le RAG futur."""
    try:
        import psycopg2, json, os
        os.environ['PGCLIENTENCODING'] = 'UTF8'
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="ooredoo_sales",
            user="postgres", password="admin"
        )
        conn.set_client_encoding('UTF8')
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO coaching_cards
                    (store_id, cycle_id, urgence, gap_pct, analyst_summary,
                     strategie, actions, cause_racine, contexte, statut)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                store_id,
                f"chat-{datetime.now().strftime('%H%M%S')}",
                "CHAT",
                0.0,
                f"Q: {question[:200]}",
                reply[:500],
                json.dumps([]),
                f"Coach Chat — {advisor_name}",
                json.dumps({"rag_preview": rag_used[:200] if rag_used else ""}),
                "CHAT_LOG",
            ))
        conn.commit()
        conn.close()
    except Exception:
        pass  # Non bloquant