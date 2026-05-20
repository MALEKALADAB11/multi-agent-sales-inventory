"""
coach_chat_rag.py — Router FastAPI pour l'Agent Coach LangGraph.
"""
import logging
import os
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/coach", tags=["coach"])

try:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sales-module"))
    from modules.coaching.agents.coach.agent import get_coach_agent
    _COACH_AGENT_AVAILABLE = True
    logger.info("[COACH ROUTER] Agent Coach LangGraph chargé ✅")
except ImportError as e:
    _COACH_AGENT_AVAILABLE = False
    logger.warning(f"[COACH ROUTER] Agent Coach non disponible: {e}")


async def _llm_fallback(request: dict) -> dict:
    message      = request.get("message", "")
    advisor_name = request.get("advisor_name", "Conseiller")
    context      = request.get("context", {})
    gap_pct      = float(context.get("gap_pct", 0))
    urgency      = context.get("urgency", "MEDIUM")
    actions      = context.get("strategie_actions", [])
    weather      = context.get("weather", "")
    hours_left   = max(0, 20 - datetime.now().hour)

    try:
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage, SystemMessage

        rag_txt = ""
        try:
            from data.rag_retriever import get_coach_chat_context
            rag = await get_coach_chat_context(
                advisor_name=advisor_name, question=message,
                store_id="I63", current_hour=datetime.now().hour,
            )
            if rag.get("available"):
                rag_txt = rag.get("rag_context", "")
        except Exception:
            pass

        actions_txt = "\n".join([
            f"P{a.get('priorite','')}. {a.get('action','')} → {a.get('produit_cible','')}"
            for a in actions[:3]
        ]) or "Analyse en cours..."

        system = (
            f"Tu es le CoachAgent IA d'Ooredoo Tunisie.\n"
            f"Conseiller : {advisor_name} | CA : {context.get('current_revenue',0):,.0f}/"
            f"{context.get('daily_target',1007):,.0f} TND | Gap : {gap_pct:.0f}% | "
            f"Urgence : {urgency} | Météo : {weather} | {hours_left}h restantes\n"
            f"Actions Stratège :\n{actions_txt}\n"
            f"{rag_txt}\n"
            f"Catalogue : iPhone 16 Pro 1299 DT | Samsung A55 5G 899 DT | "
            f"Forfait 5G Max 49 DT/mois | Box Fibre 1Go 59 DT/mois | "
            f"Assurance Premium 9 DT/mois | AirPods Pro 3 279 DT\n"
            f"Règles : français direct, max 120 mots, commence par l'action."
        )
        llm = ChatOllama(
            model=os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0.3, num_predict=180, num_ctx=1500,
        )
        resp = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content=message)])
        return {"reply": resp.content.strip(), "source": "llm+rag" if rag_txt else "llm",
                "rag_used": bool(rag_txt), "timestamp": datetime.now().isoformat()}

    except Exception as e:
        logger.warning(f"[COACH ROUTER] LLM fallback: {str(e)[:60]}")
        return {
            "reply": (
                f"Gap {gap_pct:.0f}% — Urgence {urgency}. "
                f"Focus : Assurance Premium sur chaque vente terminal. "
                f"Bundle Smartphone + Forfait 5G = panier optimal. {hours_left}h restantes."
            ),
            "source": "fallback", "rag_used": False,
            "timestamp": datetime.now().isoformat(),
        }


@router.post("/chat")
async def coach_chat(request: dict):
    message      = request.get("message", "").strip()
    advisor_name = request.get("advisor_name", "Conseiller")
    store_id     = request.get("store_id", "store-lac2")
    context      = request.get("context", {})

    if not message:
        return JSONResponse({"reply": "", "source": "empty", "rag_used": False})

    if _COACH_AGENT_AVAILABLE:
        try:
            agent = get_coach_agent()
            from core.state import initial_state
            state = initial_state(store_id="OOR_LAC_01")

            weather_str = context.get("weather", "")
            state["pos_data"] = {
                "store_id": "I63",
                "current_revenue": float(context.get("current_revenue", 0)),
                "daily_target":    float(context.get("daily_target",    1007)),
                "nb_transactions_today": int(context.get("nb_ventes",   0)),
                "coach_message":   message,
                "advisor_name":    advisor_name,
            }
            state["gap_objectif"]      = float(context.get("gap_pct",       0))
            state["urgency_level"]     = context.get("urgency",         "MEDIUM")
            state["forecast_eod"]      = float(context.get("forecast_eod",  0))
            state["analyst_summary"]   = context.get("analyst_summary",  "")
            state["strategie"]         = context.get("strategie",         "")
            state["strategie_actions"] = context.get("strategie_actions", [])
            state["cause_racine"]      = context.get("cause_racine",      "")
            state["focus_produits"]    = context.get("focus_produits",    [])
            state["external_context"]  = {
                "summary": {
                    "weather_icon":   weather_str.split()[0] if weather_str else "🌤️",
                    "weather_label":  " ".join(weather_str.split()[1:]) if weather_str else "Tunis",
                    "weather_effect": -0.10 if "pluie" in weather_str.lower() else 0.0,
                    "temperature":    22,
                    "is_rainy":       "pluie" in weather_str.lower() or "🌧" in weather_str,
                    "is_sunny":       "☀️" in weather_str,
                },
                "current_hour": datetime.now().hour,
                "day_of_week":  datetime.now().weekday(),
                "is_weekend":   datetime.now().weekday() >= 5,
                "is_peak_hour": datetime.now().hour in [16, 17],
                "is_closing":   datetime.now().hour >= 19,
            }

            result      = await agent.ainvoke(state)
            conseil     = result.get("conseil_final", "")
            coach_ctx   = result.get("coach_context", {})
            conseil_res = coach_ctx.get("conseil_result", {})

            if conseil:
                return JSONResponse({
                    "reply":      conseil,
                    "source":     conseil_res.get("source", "llm"),
                    "rag_used":   result.get("rag_used", False),
                    "confidence": conseil_res.get("confidence", 0.80),
                    "nb_scripts": result.get("nb_rag_scripts", 0),
                    "timestamp":  datetime.now().isoformat(),
                })
        except Exception as e:
            logger.warning(f"[COACH ROUTER] LangGraph error: {str(e)[:80]}")

    result = await _llm_fallback(request)
    return JSONResponse(result)


@router.get("/history/{advisor_name}")
async def get_coach_history(advisor_name: str, limit: int = 10):
    try:
        from modules.coaching.agents.coach.tools import get_advisor_history
        history = get_advisor_history(advisor_name, limit=limit)
        return JSONResponse({"advisor": advisor_name, "history": history, "total": len(history)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/stats")
async def get_coach_stats():
    try:
        from modules.coaching.agents.coach.tools import get_conn
        from psycopg2.extras import RealDictCursor
        conn = get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT COUNT(*) AS total, AVG(confidence) AS avg_conf,
                       SUM(CASE WHEN rag_used THEN 1 ELSE 0 END) AS rag_count,
                       COUNT(DISTINCT advisor_name) AS nb_advisors
                FROM coach_interactions
                WHERE created_at >= NOW() - INTERVAL '24 hours'
            """)
            stats = dict(cur.fetchone() or {})
            cur.execute("""
                SELECT conseil_type, COUNT(*) AS nb
                FROM coach_interactions
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY conseil_type ORDER BY nb DESC
            """)
            stats["by_type"] = [dict(r) for r in cur.fetchall()]
        conn.close()
        stats["langgraph_available"] = _COACH_AGENT_AVAILABLE
        return JSONResponse(stats)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)