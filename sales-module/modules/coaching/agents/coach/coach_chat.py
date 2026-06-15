"""
coach_chat.py — Coach Chat FINAL v6
=====================================
OpenRouter (gpt-oss-120b:free) → Fallback Ollama
Prompt ultra-directif avec few-shot dynamique par type de question.
"""
import logging
import os
import re
import time
import requests
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/coach", tags=["coach"])

OLLAMA_URL       = os.getenv("OLLAMA_BASE_URL",    "http://localhost:11434")
OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL",        "llama3.2:latest")
OPENROUTER_KEY   = os.getenv("OPENROUTER_API_KEY",  "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL",    "openai/gpt-oss-120b:free")
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"
MILVUS_URI       = "http://localhost:19530"
COLLECTION       = "coaching_scripts"
EMBED_DIM        = 768

USE_OPENROUTER = bool(OPENROUTER_KEY)

CATALOG_STR = """\
  • iPhone 16 Pro: 1 299 TND | Samsung A55 5G: 899 TND | Galaxy S25 Ultra: 1 599 TND | INFINIX NOTE 40: 349 TND
  • Forfait 5G Max: 49 TND/mois | Forfait Flexi 25Go: 29 TND/mois | Unlimited: 69 TND/mois | Famille 5G: 120 TND/mois
  • Box Fibre 1Go: 59 TND/mois | Box 4G+: 39 TND/mois
  • Assurance Premium: 9 TND/mois | Cloud Backup 1To: 15 TND/mois | TV Streaming: 12 TND/mois
  • AirPods Pro 3: 279 TND | Apple Watch S10: 449 TND
  • Bundle max: iPhone 16 Pro + Forfait 5G Max + Assurance = ~1 357 TND (avance postpayé)"""

PATTERNS = {
    "script":    ["script","comment vendre","que dire","pitch","argumentaire","présenter","donne-moi un script"],
    "objection": ["objection","trop cher","hésite","refuse","concurrent","pas besoin","dit que","réticent"],
    "closing":   ["closing","signature","convaincre","décider","finaliser","indécis","comment closer"],
    "meteo":     ["météo","pluie","couvert","soleil","accessoire","il pleut"],
    "upsell":    ["upsell","ajouter","accessoire","bundle","assurance","cloud","après la vente","vient d'acheter"],
    "forfait":   ["forfait","recharge","5g","data","internet","fibre","box","convertir"],
    "objectif":  ["objectif","gap","rattraper","manque","en retard","combler","plan","comment faire"],
}

def _detect_type(msg):
    m = msg.lower()
    scores = {t: sum(1 for kw in kws if kw in m) for t, kws in PATTERNS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


def _search_rag(query, hour, top_k=3):
    try:
        resp = requests.post(f"{OLLAMA_URL}/api/embeddings",
            json={"model":"nomic-embed-text","prompt":query[:400]}, timeout=12)
        emb = resp.json().get("embedding",[])
        if not emb: return [], ""
        if len(emb) < EMBED_DIM: emb += [0.0]*(EMBED_DIM-len(emb))
        emb = emb[:EMBED_DIM]
        from pymilvus import MilvusClient
        client = MilvusClient(uri=MILVUS_URI)
        results = client.search(collection_name=COLLECTION, data=[emb], limit=top_k+1,
            output_fields=["categorie","situation","action","produit","argument","impact","heure_min","heure_max"])
        scripts = []
        for hit in results[0]:
            e = hit["entity"]; score = float(hit["distance"])
            if int(e.get("heure_min",0)) <= hour <= int(e.get("heure_max",24)): score += 0.12
            scripts.append({"score":round(score,3),"categorie":e.get("categorie",""),
                "situation":e.get("situation","")[:100],"action":e.get("action","")[:100],
                "produit":e.get("produit",""),"argument":e.get("argument","")[:120],"impact":e.get("impact","")})
        scripts = sorted(scripts, key=lambda x: x["score"], reverse=True)[:top_k]
        if not scripts: return [], ""
        lines = ["\nARGUMENTS PROUVÉS (RAG — déjà utilisés avec succès) :"]
        for i,s in enumerate(scripts,1):
            lines.append(f"#{i} [{s['categorie']}] {s['action']} | Argument: {s['argument']}")
        return scripts, "\n".join(lines)
    except Exception as e:
        logger.warning(f"[COACH RAG] {str(e)[:60]}")
        return [], ""


def _build_prompt(advisor_name, ca_today, ca_target, gap_pct, gap_tnd,
                  performance, nb_ventes, urgency, weather, hours_left,
                  current_hour, actions_txt, rag_txt, question_type, message):

    examples = {
        "script": f"""EXEMPLE DE BONNE RÉPONSE — Script de vente :
Script iPhone 16 Pro :
1. "Tu utilises ton téléphone principalement pour quoi ?"
2. Démo live puce A18 Pro — vitesse photo/vidéo incomparable.
3. "1 299 TND ou 54 TND/mois sur 24 mois via avance postpayé."
4. + Assurance Premium 9 TND/mois — remplacement 48h garanti.
5. "Noir titane ou blanc naturel ?"
Vas-y !""",

        "objection": f"""EXEMPLE DE BONNE RÉPONSE — Objection prix :
Réponds : "Je comprends — 1 299 TND = 54 TND/mois sur 24 mois via avance postpayé Ooredoo. C'est moins qu'un café par jour pour le dernier iPhone."
Close : "Noir titane ou blanc naturel ?"
À toi !""",

        "closing": f"""EXEMPLE DE BONNE RÉPONSE — Closing :
"Je vais être honnête — il nous reste 2 unités de ce modèle. Si tu repars sans, je ne peux pas garantir qu'il sera disponible demain."
Silence 3 secondes.
"On fait les papiers ?"
Maintenant !""",

        "upsell": f"""EXEMPLE DE BONNE RÉPONSE — Upsell :
Juste après la signature : "Félicitations ! Une dernière chose — l'Assurance Premium à 9 TND/mois protège ton investissement. Remplacement garanti en 48h en cas de casse ou vol. On l'ajoute ?"
Vas-y !""",

        "meteo": f"""EXEMPLE DE BONNE RÉPONSE — Météo pluie :
Pluie = opportunité — les clients restent plus longtemps en boutique.
AirPods Pro 3 (279 TND, IPX4) + Apple Watch S10 (449 TND, étanche 50m) en priorité.
Argument : "Parfait par ce temps — résistants à l'eau."
1 vente accessoire = objectif amorcé. Vas-y !""",

        "forfait": f"""EXEMPLE DE BONNE RÉPONSE — Conversion forfait :
1. "Tu recharges combien par mois en moyenne ?"
2. Calcul live : "3 recharges × 10 TND = 30 TND. Forfait Flexi = 29 TND + appels illimités + 25Go."
3. "Tu économises 1 TND et tu as 25× plus de data."
Activation maintenant — numéro conservé. Maintenant !""",

        "objectif": f"""EXEMPLE DE BONNE RÉPONSE — Plan objectif :
Gap {gap_tnd:.0f} TND avec {hours_left}h restantes. Plan :
1. Bundle iPhone 16 Pro + Forfait 5G Max = 1 348 TND via avance postpayé → objectif comblé en 1 vente.
2. En parallèle : Assurance Premium sur chaque terminal (+9 TND/mois).
Focus pic 16h. À toi !""",

        "general": f"""EXEMPLE DE BONNE RÉPONSE — Conseil général :
Tu es à {performance:.0f}% — encore {gap_tnd:.0f} TND avec {hours_left}h devant toi.
Focus : Assurance Premium (9 TND/mois) après chaque vente terminal.
AirPods Pro 3 (279 TND) sur chaque client Apple.
1 bundle suffit à tout changer. Maintenant !""",
    }

    example = examples.get(question_type, examples["general"])
    tone_map = {
        "CRITICAL": "CRITIQUE 🚨 — 2-3 phrases MAX, action unique, urgence absolue",
        "HIGH":     "URGENT ⚡ — court et percutant, 1 priorité absolue, très motivant",
        "MEDIUM":   "DYNAMIQUE — 3-4 phrases, 2 actions claires, confiant",
        "LOW":      "POSITIF 🏆 — encourageant, optimiser le panier, maintenir l'élan",
    }
    tone = tone_map.get(urgency, tone_map["MEDIUM"])
    rag_section = f"\n{rag_txt}" if rag_txt else ""
    actions_section = f"\nACTIONS STRATÈGE :\n{actions_txt}" if "Focus bundle" not in actions_txt else ""

    return f"""Tu es CoachAgent IA Ooredoo — coach de vente expert pour conseillers en boutique Tunisie.

GUARDRAILS (inviolables) :
✓ FRANÇAIS uniquement — tutoiement — ton humain et direct
✓ MAX 130 mots — chaque mot mérite sa place
✓ Prix EXACTS du catalogue uniquement — jamais d'invention
✓ Commence DIRECTEMENT par l'action — jamais "Bonjour" ni "En tant que coach"
✓ Termine par : "Vas-y !" / "Maintenant !" / "À toi !" / "Allez !"
✓ Vrais chiffres : {gap_tnd:.0f} TND de gap, {hours_left}h restantes, {performance:.0f}% atteint
✗ PAS de JSON — PAS de balises — PAS de "Here is" — PAS de "Based on"
✗ PAS de "0 TND" ni "0%" — utilise les vrais chiffres ci-dessus

CONTEXTE LIVE — {advisor_name} :
  CA : {ca_today:,.0f} / {ca_target:,.0f} TND ({performance:.0f}%)
  Gap : {gap_tnd:.0f} TND ({gap_pct:.1f}%) | {hours_left}h restantes
  Météo : {weather} | Heure : {current_hour}h | Ventes : {nb_ventes}
  Urgence : {urgency} | TON : {tone}
{actions_section}{rag_section}

CATALOGUE OOREDOO :
{CATALOG_STR}

QUESTION DE {advisor_name} : {message}

{example}

RÉPONSE DU COACH (français direct, MAX 130 mots, termine par verbe action) :"""


async def _call_openrouter(system_prompt, message, urgency, question_type):
    t0 = time.time()
    try:
        import httpx
        max_tokens  = 280 if urgency in ("HIGH","CRITICAL") else 380
        temperature = 0.20 if question_type in ("script","objection","closing") else 0.28
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(OPENROUTER_URL,
                headers={"Authorization":f"Bearer {OPENROUTER_KEY}",
                         "Content-Type":"application/json",
                         "HTTP-Referer":"https://github.com/MALEKALADAB11/multi-agent-sales-inventory",
                         "X-Title":"AI Sales Coach Ooredoo"},
                json={"model":OPENROUTER_MODEL,"max_tokens":max_tokens,"temperature":temperature,
                      "messages":[{"role":"system","content":system_prompt},
                                  {"role":"user","content":message}]})
        data = resp.json()
        if "error" in data:
            logger.warning(f"[COACH OR] {data['error'].get('message','?')[:100]}")
            return "", (time.time()-t0)*1000
        reply = data["choices"][0]["message"]["content"].strip()
        for prefix in ["Réponse:","Coach:","CoachAgent:","Here is","Based on","RÉPONSE DU COACH :"]:
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


async def _call_ollama(prompt, urgency, question_type):
    t0 = time.time()
    try:
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage
        llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_URL,
            temperature=0.15 if question_type in ("script","objection","closing") else 0.20,
            num_predict=160 if urgency in ("HIGH","CRITICAL") else 220,
            num_ctx=3000,
            stop=["QUESTION DE","GUARDRAILS","CATALOGUE","CONTEXTE LIVE",
                  "\n\n\n","Human:","Assistant:","[INST]"])
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        reply = resp.content.strip()
        for c in ["RÉPONSE DU COACH :","Coach :","CoachAgent :","Here is","Based on","```","---\n"]:
            if reply.startswith(c): reply = reply[len(c):].strip()
        if reply.startswith("{"):
            m = re.search(r'"(?:reply|response|conseil|message|text)"\s*:\s*"([^"]+)"', reply, re.IGNORECASE)
            reply = m.group(1).strip() if m else ""
        ms = (time.time()-t0)*1000
        logger.info(f"[COACH OLLAMA] OK — {len(reply)} chars | {ms:.0f}ms")
        return reply, ms
    except Exception as e:
        logger.warning(f"[COACH OLLAMA] {str(e)[:80]}")
        return "", (time.time()-t0)*1000


async def _get_pos_from_pg(store_id):
    try:
        import asyncpg
        conn = await asyncpg.connect(host="localhost",port=5432,database="ooredoo_sales",
            user="postgres",password="admin",timeout=3)
        try:
            row = await conn.fetchrow(
                "SELECT ca_total,nb_transactions FROM sales.vw_ca_par_boutique "
                "WHERE store_id=$1 ORDER BY date_only DESC LIMIT 1",store_id)
            if row: return {"ca_total":float(row["ca_total"] or 0),"nb_transactions":int(row["nb_transactions"] or 0)}
        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"[COACH PG] {e}")
    return {"ca_total":0,"nb_transactions":0}


@router.post("/chat")
async def coach_chat(request: dict):
    t_total = time.time()
    message      = (request.get("message") or "").strip()
    advisor_name = request.get("advisor_name") or "Conseiller"
    store_id     = request.get("store_id") or "I63"
    ctx          = request.get("context") or {}
    if not message:
        return JSONResponse({"reply":"","source":"empty","rag_used":False})

    now = datetime.now(); current_hour = now.hour; hours_left = max(1, 20-current_hour)

    ca_today  = float(ctx.get("current_revenue") or ctx.get("ca_today") or ctx.get("ca_actuel") or 0)
    ca_target = float(ctx.get("daily_target") or ctx.get("ca_target") or 1007)
    gap_pct   = float(ctx.get("gap_pct") or ctx.get("ecart_objectif") or ctx.get("gap_objectif") or 0)
    urgency   = str(ctx.get("urgency") or ctx.get("niveau_urgence") or ctx.get("urgency_level") or "MEDIUM").upper()
    weather   = str(ctx.get("weather") or (ctx.get("store_context") or {}).get("weather") or "🌤️ Tunis")
    nb_ventes = int(ctx.get("nb_ventes") or ctx.get("nb_transactions_today") or 0)

    if ca_today == 0:
        pg = await _get_pos_from_pg(store_id)
        ca_today = pg["ca_total"]; nb_ventes = nb_ventes or pg["nb_transactions"]

    gap_tnd     = max(0.0, ca_target - ca_today)
    performance = round((ca_today/ca_target*100),1) if ca_target > 0 else 0.0
    if gap_pct == 0 and ca_target > 0:
        gap_pct = round((gap_tnd/ca_target)*100,1)

    actions = ctx.get("strategie_actions") or []
    actions_txt = "\n".join(
        f"  P{a.get('priorite','?')}. {a.get('action','')} → {a.get('produit_cible','')} — {a.get('argument_vente','')[:80]}"
        for a in actions[:3]) if actions else "  Focus bundle terminal + forfait + assurance"

    question_type = _detect_type(message)

    rag_query = f"{message} {question_type}"
    if gap_pct > 40:               rag_query += " gap critique urgent bundle"
    if "pluie" in weather.lower(): rag_query += " pluie accessoires résistants eau"
    if 16 <= current_hour <= 18:   rag_query += " pic trafic 16h maximiser"
    elif current_hour >= 19:        rag_query += " soirée closing express"

    scripts, rag_txt = _search_rag(rag_query, current_hour, top_k=3)
    rag_used = len(scripts) > 0

    logger.info(f"[COACH CHAT] {advisor_name} | {question_type} | gap={gap_pct:.1f}% | "
                f"RAG={'✓' if rag_used else '✗'} | {'OpenRouter' if USE_OPENROUTER else 'Ollama'} | {urgency}")

    prompt = _build_prompt(advisor_name=advisor_name, ca_today=ca_today, ca_target=ca_target,
        gap_pct=gap_pct, gap_tnd=gap_tnd, performance=performance, nb_ventes=nb_ventes,
        urgency=urgency, weather=weather, hours_left=hours_left, current_hour=current_hour,
        actions_txt=actions_txt, rag_txt=rag_txt, question_type=question_type, message=message)

    reply = ""; llm_ok = False; confidence = 0.65; source = "fallback"; model_used = ""; llm_ms = 0.0

    if USE_OPENROUTER:
        reply, llm_ms = await _call_openrouter(prompt, message, urgency, question_type)
        if reply:
            llm_ok = True; model_used = OPENROUTER_MODEL
            confidence = 0.95 if rag_used else 0.88
            source = "openrouter+rag" if rag_used else "openrouter"
        else:
            logger.warning("[COACH] OpenRouter échoué → fallback Ollama")
            reply, llm_ms = await _call_ollama(prompt, urgency, question_type)
            if reply:
                llm_ok = True; model_used = OLLAMA_MODEL; confidence = 0.72; source = "ollama_fallback"
    else:
        reply, llm_ms = await _call_ollama(prompt, urgency, question_type)
        if reply:
            llm_ok = True; model_used = OLLAMA_MODEL
            confidence = 0.85 if rag_used else 0.75
            source = "ollama+rag" if rag_used else "ollama"

    if not reply:
        if scripts:
            s = scripts[0]
            reply = f"{s['action']}\n\n{s['argument']}\nImpact : {s['impact']}\nVas-y !"
            source = "rag_fallback"; confidence = 0.70
        elif gap_pct > 30:
            reply = (f"⚡ Gap {gap_tnd:.0f} TND — {hours_left}h restantes.\n"
                     f"Bundle iPhone 16 Pro + Forfait 5G Max = 1 348 TND via avance postpayé.\n'0 DT aujourd'hui, 54 TND/mois sur 24 mois.' Maintenant !")
        else:
            reply = (f"{performance:.0f}% atteint — {gap_tnd:.0f} TND avec {hours_left}h.\n"
                     f"Assurance Premium (9 TND/mois) sur chaque terminal. À toi !")
        source = "fallback"

    total_ms = (time.time()-t_total)*1000

    try:
        from langfuse_observer import trace_coach_chat
        trace_coach_chat(store_id=store_id, advisor_name=advisor_name,
            message=message, response=reply, question_type=question_type,
            rag_used=rag_used, nb_rag_scripts=len(scripts), confidence=confidence,
            latency_ms=total_ms, gap_pct=gap_pct, urgency=urgency)
    except Exception: pass

    try:
        from modules.coaching.agents.coach.tools import save_interaction
        save_interaction(advisor_name=advisor_name, store_id=store_id,
            message=message, response=reply, gap_pct=gap_pct, urgency=urgency,
            rag_used=rag_used, nb_rag_scripts=len(scripts),
            conseil_type=question_type, confidence=confidence)
    except Exception: pass

    return JSONResponse({
        "reply": reply, "source": source, "model": model_used,
        "rag_used": rag_used, "confidence": confidence,
        "question_type": question_type, "nb_rag_scripts": len(scripts),
        "latency_ms": round(total_ms),
        "context_used": {"advisor":advisor_name,"ca_today":ca_today,"ca_target":ca_target,
                         "gap_pct":gap_pct,"performance":performance,"urgency":urgency,"hour":current_hour},
        "rag_scripts": scripts[:2] if scripts else [],
    })