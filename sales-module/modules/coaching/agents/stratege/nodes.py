"""
Nodes LangGraph de l'Agent Stratège v2.
Nodes :
  1. node_fetch_context      — météo + fériés + événements
  2. node_rag_search         — recherche Milvus scripts similaires  ← NOUVEAU
  3. node_analyze_context    — cause racine + facteurs              ← NOUVEAU (remplace analyze_root_cause)
  4. node_generate_strategy  — LLM + RAG → actions concrètes
  5. node_build_output       — formatage final
"""
import json
import logging
import os
import re
import requests
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

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MILVUS_URI = "http://localhost:19530"
EMBED_DIM  = 768


# ─────────────────────────────────────────────────────────────────
# LLM
# ─────────────────────────────────────────────────────────────────

def get_llm() -> ChatOllama:
    return ChatOllama(
        model       = os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
        base_url    = OLLAMA_URL,
        temperature = 0.2,
        num_predict = 400,
        num_ctx     = 2048,
    )


# ─────────────────────────────────────────────────────────────────
# NODE 1 — Fetch contexte externe
# ─────────────────────────────────────────────────────────────────

async def node_fetch_context(state: SalesAgentState) -> dict:
    pos_data = state.get("pos_data", {})
    store_id = pos_data.get("store_id", "OOR_LAC_01")

    logger.info(f"[STRATEGE] Node 1 — Fetch contexte pour {store_id}")

    context = await fetch_full_context(store_id)

    summary = context.get("summary", {})
    logger.info(
        f"[STRATEGE] Node 1 — Météo: {summary.get('weather_icon','')} "
        f"{summary.get('weather_label','')} | "
        f"Férié: {summary.get('is_holiday', False)} | "
        f"Promos: {summary.get('active_promos', 0)}"
    )

    return {"external_context": context}


# ─────────────────────────────────────────────────────────────────
# NODE 2 — RAG Search (Milvus)                          ← NOUVEAU
# ─────────────────────────────────────────────────────────────────

async def node_rag_search(state: SalesAgentState) -> dict:
    """
    Recherche les scripts de vente similaires dans Milvus
    en fonction du contexte actuel (gap, urgence, météo, heure).
    """
    logger.info("[STRATEGE] Node 2 — RAG search Milvus")

    gap_pct   = float(state.get("gap_objectif", 0))
    urgency   = state.get("urgency_level", "MEDIUM")
    ext_ctx   = state.get("external_context", {})
    summary   = ext_ctx.get("summary", {})
    hour      = datetime.now().hour
    dow       = datetime.now().weekday()
    is_rainy  = summary.get("weather_effect", 0) <= -0.10

    # ── Construire la requête RAG ─────────────────────────
    parts = []
    if gap_pct > 60:   parts.append("gap critique très éloigné objectif urgent action immédiate")
    elif gap_pct > 40: parts.append("gap critique objectif loin closing intensif")
    elif gap_pct > 20: parts.append("gap modéré performance améliorer upsell")
    else:              parts.append("performance correcte optimiser panier moyen")

    if 16 <= hour <= 18: parts.append("pic trafic heure de pointe maximiser conversion")
    elif hour >= 19:     parts.append("soirée closing rapide dernières heures fermeture")
    elif hour <= 11:     parts.append("matin ouverture faible trafic proactif")

    if is_rainy:   parts.append("météo pluie couvert accessoires protection résistants")
    if dow >= 5:   parts.append("week-end famille multi-lignes groupes")
    if urgency == "HIGH": parts.append("urgence haute closing immédiat")

    rag_query = " ".join(parts)
    logger.info(f"[STRATEGE RAG] Requête: '{rag_query[:80]}'")

    # ── Embedding via Ollama ──────────────────────────────
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": rag_query},
            timeout=15,
        )
        emb = resp.json().get("embedding", [])
        if not emb:
            raise ValueError("Embedding vide")
        if len(emb) < EMBED_DIM:
            emb = emb + [0.0] * (EMBED_DIM - len(emb))
        elif len(emb) > EMBED_DIM:
            emb = emb[:EMBED_DIM]
    except Exception as e:
        logger.warning(f"[STRATEGE RAG] Embedding échoué: {e}")
        return {**state, "rag_context": [], "rag_used": False, "nb_rag_scripts": 0}

    # ── Recherche Milvus ──────────────────────────────────
    try:
        from pymilvus import MilvusClient
        client  = MilvusClient(uri=MILVUS_URI)
        results = client.search(
            collection_name = "coaching_scripts",
            data            = [emb],
            limit           = 5,
            output_fields   = [
                "pg_id", "categorie", "situation", "action",
                "produit", "argument", "impact",
                "heure_min", "heure_max", "jour_semaine", "store_id",
            ],
        )
        scripts = []
        for hit in results[0]:
            e     = hit["entity"]
            score = float(hit["distance"])
            if e.get("heure_min", 0) <= hour <= e.get("heure_max", 24):
                score += 0.12
            if e.get("store_id", "ALL") in ("ALL", "I63"):
                score += 0.05
            scripts.append({
                "score":     round(score, 3),
                "categorie": e.get("categorie", ""),
                "situation": e.get("situation", ""),
                "action":    e.get("action", ""),
                "produit":   e.get("produit", ""),
                "argument":  e.get("argument", ""),
                "impact":    e.get("impact", ""),
            })

        scripts = sorted(scripts, key=lambda x: x["score"], reverse=True)[:3]
        logger.info(
            f"[STRATEGE RAG] {len(scripts)} scripts "
            f"(top: {scripts[0]['categorie'] if scripts else 'N/A'})"
        )

        # Log RAG feedback
        try:
            from agent_logger import log_rag_feedback
            cycle_id = (state.get("metrics") or {}).get("cycle_id", "unknown")
            log_rag_feedback(
                cycle_id = cycle_id, query = rag_query,
                scripts  = scripts, store_id = "I63",
                context  = {"gap_pct": gap_pct, "urgency": urgency, "hour": hour},
            )
        except Exception:
            pass

        return {
            **state,
            "rag_context":    scripts,
            "rag_query":      rag_query,
            "rag_used":       len(scripts) > 0,
            "nb_rag_scripts": len(scripts),
        }

    except Exception as e:
        logger.warning(f"[STRATEGE RAG] Milvus: {e}")
        return {**state, "rag_context": [], "rag_used": False, "nb_rag_scripts": 0}


# ─────────────────────────────────────────────────────────────────
# NODE 3 — Analyze Context (cause racine)               ← NOUVEAU
# ─────────────────────────────────────────────────────────────────

async def node_analyze_context(state: SalesAgentState) -> dict:
    """
    Identifie la cause racine du gap et les facteurs contextuels.
    Remplace node_analyze_root_cause avec enrichissement RAG.
    """
    logger.info("[STRATEGE] Node 3 — Analyze context")

    gap_pct  = float(state.get("gap_objectif", 0))
    urgency  = state.get("urgency_level", "LOW")
    context  = state.get("external_context", {})
    summary  = context.get("summary", {})
    hour     = datetime.now().hour
    hrs_rem  = max(0, 20 - hour)
    is_rainy = summary.get("weather_effect", 0) <= -0.10
    rag_ok   = state.get("rag_used", False)

    factors = []

    # Météo
    w_eff = summary.get("weather_effect", 0)
    if w_eff <= -0.20:
        factors.append(f"Météo défavorable ({summary.get('weather_label','')} — impact {w_eff:.0%})")
    elif w_eff <= -0.10:
        factors.append(f"Météo légèrement défavorable ({summary.get('weather_label','')})")

    # Jour férié
    holidays = context.get("holidays", {})
    if holidays.get("is_holiday_today"):
        factors.append(f"Jour férié: {holidays.get('today_holiday',{}).get('name','')} — comportement atypique")

    # Gap
    if gap_pct > 40:
        factors.append(f"Gap structurel élevé ({gap_pct:.1f}%) — sous-performance conseillers")
    elif gap_pct > 20:
        factors.append(f"Gap modéré ({gap_pct:.1f}%) — rythme insuffisant")

    # Pression temporelle
    if hour >= 16 and gap_pct > 20:
        factors.append(f"Pression temporelle — {hrs_rem}h restantes")

    # Promos non exploitées
    promos = summary.get("active_promos", 0)
    if promos > 0 and gap_pct > 15:
        factors.append(f"{promos} promotion(s) Ooredoo actives non exploitées")

    # RAG disponible
    nb_scripts = state.get("nb_rag_scripts", 0)
    if rag_ok and nb_scripts > 0:
        factors.append(f"RAG: {nb_scripts} scripts similaires disponibles")

    cause_racine = factors[0] if factors else f"Gap de {gap_pct:.1f}% sans facteur externe majeur"

    logger.info(f"[STRATEGE] Cause racine: {cause_racine[:60]}")
    logger.info(f"[STRATEGE] Facteurs: {len(factors)}")

    return {
        **state,
        "root_cause":      cause_racine,
        "cause_racine":    cause_racine,
        "context_factors": factors,
    }


# ─────────────────────────────────────────────────────────────────
# NODE 4 — Génération stratégie LLM + RAG
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
    rag_scripts     = state.get("rag_context", [])

    hour            = datetime.now().hour
    hours_remaining = max(0, 20 - hour)
    summary         = context.get("summary", {})
    weather         = context.get("weather", {})
    holidays        = context.get("holidays", {})
    events          = context.get("events", {})

    # ── Contexte RAG pour le prompt ───────────────────────
    rag_txt = ""
    if rag_scripts:
        lines = ["Scripts similaires qui ont fonctionné (base Ooredoo):"]
        for i, s in enumerate(rag_scripts[:3], 1):
            lines.append(
                f"\n[Script #{i} — {s['categorie']} | score {s['score']:.2f}]\n"
                f"Situation: {s['situation'][:120]}\n"
                f"Action: {s['action'][:120]}\n"
                f"Produit: {s['produit']}\n"
                f"Argument: {s['argument'][:120]}\n"
                f"Impact: {s['impact']}"
            )
        rag_txt = "\n".join(lines)

    # ── Construire le prompt ──────────────────────────────
    analyst_data = json.dumps({
        "urgency_level":   urgency_level,
        "gap_pct":         gap_pct,
        "gap_amount_tnd":  gap_amount,
        "ca_actuel_tnd":   pos_data.get("current_revenue", 0),
        "objectif_tnd":    pos_data.get("daily_target", 1007),
        "analyst_summary": analyst_summary[:200],
        "root_cause":      root_cause,
        "context_factors": factors,
        "rag_available":   bool(rag_scripts),
    }, indent=2, ensure_ascii=False)

    weather_data = json.dumps({
        "label":        summary.get("weather_label", ""),
        "icon":         summary.get("weather_icon", ""),
        "effet_trafic": f"{summary.get('weather_effect', 0):+.0%}",
        "temperature":  weather.get("current", {}).get("temperature", 22),
        "is_rainy":     summary.get("weather_effect", 0) <= -0.10,
    }, indent=2, ensure_ascii=False)

    holidays_data = json.dumps({
        "is_holiday_today": holidays.get("is_holiday_today", False),
        "holiday_name":     (holidays.get("today_holiday", {}).get("name", "")
                            if holidays.get("is_holiday_today") else ""),
        "next_holiday":     (holidays.get("next_holiday", {}).get("name", "")
                            if holidays.get("next_holiday") else ""),
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
    }, indent=2, ensure_ascii=False)

    user_msg = STRATEGE_USER_PROMPT.format(
        analyst_data    = analyst_data,
        weather_data    = weather_data,
        holidays_data   = holidays_data,
        events_data     = events_data,
        current_time    = datetime.now().strftime("%H:%M"),
        hours_remaining = hours_remaining,
    )

    # Injecter le contexte RAG dans le prompt système
    system_with_rag = STRATEGE_SYSTEM_PROMPT
    if rag_txt:
        system_with_rag += f"\n\n{rag_txt}"

    # ── Appel LLM ─────────────────────────────────────────
    llm            = get_llm()
    strategie_data = {}

    try:
        logger.info("[STRATEGE] Node 4 — Appel LLM...")
        response = await llm.ainvoke([
            SystemMessage(content=system_with_rag),
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
            logger.info(f"[STRATEGE] LLM JSON OK — {len(strategie_data.get('actions',[]))} actions")
        except json.JSONDecodeError:
            logger.warning("[STRATEGE] JSON tronqué — extraction regex")
            strategie_data = _extract_from_partial_json(content)
            if not strategie_data.get("actions"):
                strategie_data = _make_fallback_strategy(
                    gap_pct, urgency_level, summary, hours_remaining, rag_scripts
                )

    except Exception as e:
        logger.warning(f"[STRATEGE] LLM fallback: {str(e)[:60]}")
        strategie_data = _make_fallback_strategy(
            gap_pct, urgency_level, summary, hours_remaining, rag_scripts
        )

    return {**state, "strategie_data": strategie_data}


# ─────────────────────────────────────────────────────────────────
# NODE 5 — Build Output
# ─────────────────────────────────────────────────────────────────

async def node_build_output(state: SalesAgentState) -> dict:
    strategie_data = state.get("strategie_data", {})
    context        = state.get("external_context", {})
    root_cause     = state.get("root_cause", "")
    factors        = state.get("context_factors", [])
    gap_pct        = state.get("gap_objectif", 0.0)
    urgency_level  = state.get("urgency_level", "MEDIUM")
    rag_used       = state.get("rag_used", False)
    nb_scripts     = state.get("nb_rag_scripts", 0)

    hour            = datetime.now().hour
    hours_remaining = max(0, 20 - hour)
    summary         = context.get("summary", {})

    if not strategie_data.get("actions"):
        logger.warning("[STRATEGE] Node 5 — Fallback actions")
        strategie_data = _make_fallback_strategy(
            gap_pct, urgency_level, summary, hours_remaining,
            state.get("rag_context", [])
        )

    actions           = strategie_data.get("actions", [])
    focus_produits    = strategie_data.get("focus_produits", [])
    message_manager   = strategie_data.get("message_manager", "")
    strategie_summary = strategie_data.get("strategie_summary", "")
    cause_racine      = strategie_data.get("cause_racine", root_cause)

    if not strategie_summary:
        strategie_summary = (
            f"Gap {gap_pct:.1f}% — {cause_racine}. "
            f"Focus: {', '.join(focus_produits[:2]) if focus_produits else 'produits premium'}."
        )

    context_signals = _build_context_signals(context, factors)
    heatmap         = context.get("heatmap", {})

    logger.info(
        f"[STRATEGE] Output: {len(actions)} actions | "
        f"RAG={'✓' if rag_used else '✗'}({nb_scripts}) | "
        f"météo={summary.get('weather_label','?')}"
    )

    return {
        **state,
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
    result = {}
    for field in ["cause_racine", "strategie_summary", "message_manager"]:
        m = re.search(rf'"{field}"\s*:\s*"([^"]+)"', content)
        if m:
            result[field] = m.group(1)
    fp = re.search(r'"focus_produits"\s*:\s*\[([^\]]+)\]', content)
    if fp:
        result["focus_produits"] = re.findall(r'"([^"]+)"', fp.group(1))
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


def _make_fallback_strategy(
    gap_pct: float,
    urgency: str,
    summary: dict,
    hours_remaining: int,
    rag_scripts: list = None,
) -> dict:
    """Fallback enrichi avec les scripts RAG si disponibles."""
    rag_scripts    = rag_scripts or []
    weather_label  = summary.get("weather_label", "")
    weather_effect = summary.get("weather_effect", 0)
    is_rainy       = weather_effect <= -0.10
    active_promos  = summary.get("active_promos", 0)

    # Utiliser les scripts RAG si disponibles
    if rag_scripts:
        actions = []
        for i, s in enumerate(rag_scripts[:3], 1):
            actions.append({
                "priorite":       i,
                "action":         s["action"][:150],
                "produit_cible":  s["produit"][:100],
                "argument_vente": s["argument"][:200],
                "impact_estime":  s["impact"][:100],
            })
        focus = list({s["produit"] for s in rag_scripts[:3]})
    else:
        # Fallback par contexte
        if is_rainy:
            actions = [
                {"priorite":1,"action":"Focus accessoires résistants eau","produit_cible":"AirPods Pro 3","argument_vente":"Certifiés résistants eau, parfaits par ce temps","impact_estime":"+120 DT panier"},
                {"priorite":2,"action":"Proposer Box Fibre aux clients captifs","produit_cible":"Box Fibre 1Go","argument_vente":"Restez connecté chez vous avec la fibre 1Go","impact_estime":"+59 DT récurrent"},
                {"priorite":3,"action":"Upsell Assurance Premium","produit_cible":"Assurance Premium","argument_vente":"9 DT/mois protège votre investissement","impact_estime":"Marge 80%"},
            ]
        elif gap_pct > 40:
            actions = [
                {"priorite":1,"action":"Bundle terminal + forfait avec avance postpayé","produit_cible":"iPhone 16 Pro + Forfait 5G Max","argument_vente":"Partez avec l'iPhone aujourd'hui, 54 DT/mois sur 24 mois","impact_estime":"+340 DT panier"},
                {"priorite":2,"action":"Convertir clients recharge vers forfait","produit_cible":"Forfait Flexi 25Go","argument_vente":"Même prix que 3 recharges + appels illimités","impact_estime":"+29 DT récurrent"},
                {"priorite":3,"action":"Closing express clients pressés","produit_cible":"Samsung A55 5G + Forfait","argument_vente":"3 minutes et vous repartez avec votre nouveau smartphone","impact_estime":"+250 DT panier"},
            ]
        else:
            actions = [
                {"priorite":1,"action":"Upsell assurance sur toutes les ventes terminaux","produit_cible":"Assurance Premium","argument_vente":"9 DT/mois = un café par semaine pour protéger votre iPhone","impact_estime":"Marge 80%"},
                {"priorite":2,"action":"Cross-sell Box Fibre aux clients smartphone","produit_cible":"Box Fibre 1Go","argument_vente":"Profitez pleinement de votre 5G à la maison","impact_estime":"+59 DT récurrent"},
                {"priorite":3,"action":"Proposer Cloud Backup sur chaque vente","produit_cible":"Cloud Backup","argument_vente":"5 DT/mois pour ne jamais perdre vos photos","impact_estime":"Marge très haute récurrent"},
            ]
        focus = [a["produit_cible"] for a in actions[:2]]

    cause = f"Gap {gap_pct:.1f}% — {weather_label or 'performance insuffisante'}"
    return {
        "cause_racine":      cause,
        "context_factors":   [weather_label] if weather_label else [],
        "actions":           actions,
        "focus_produits":    focus,
        "message_manager":   f"Gap {gap_pct:.0f}% — {hours_remaining}h restantes. Action immédiate.",
        "strategie_summary": (
            f"Gap {gap_pct:.1f}% avec {hours_remaining}h restantes. "
            f"{'Contexte météo défavorable.' if is_rainy else ''} "
            f"Focus: {', '.join(focus[:2])}."
        ),
    }


def _build_context_signals(context: dict, factors: list) -> list:
    signals  = []
    summary  = context.get("summary", {})
    holidays = context.get("holidays", {})
    events   = context.get("events", {})

    w_eff = summary.get("weather_effect", 0)
    signals.append({
        "type":  "weather",
        "level": "high" if w_eff <= -0.25 else "med" if w_eff <= -0.10 else "low",
        "label": f"{summary.get('weather_icon','☀️')} {summary.get('weather_label','Normal')} — impact {w_eff:+.0%}",
        "value": w_eff,
    })

    if holidays.get("is_holiday_today"):
        signals.append({
            "type":"holiday","level":"high",
            "label":f"🎉 Jour férié: {holidays.get('today_holiday',{}).get('name','')}",
            "value":1,
        })
    elif holidays.get("next_holiday") and holidays["next_holiday"].get("days_until",99) <= 3:
        signals.append({
            "type":"holiday","level":"med",
            "label":f"📅 {holidays['next_holiday']['name']} dans {holidays['next_holiday']['days_until']}j",
            "value":0.5,
        })

    signals.append({"type":"stock","level":"high","label":"📦 iPhone 15 — stock critique (3 unités)","value":-0.3})

    all_promos = events.get("promotions",[]) + events.get("new_offers",[])
    if all_promos:
        signals.append({
            "type":"event","level":"med",
            "label":f"🎯 {len(all_promos)} offre(s) Ooredoo — {all_promos[0].get('title','')[:40]}",
            "value":0.3,
        })

    return signals


# ─────────────────────────────────────────────────────────────────
# Alias pour compatibilité (ancien nom utilisé dans certains imports)
# ─────────────────────────────────────────────────────────────────
async def node_analyze_root_cause(state: SalesAgentState) -> dict:
    """Alias vers node_analyze_context pour compatibilité."""
    return await node_analyze_context(state)