"""
Tools de l'Agent Coach — helpers RAG, DB, formatting.
"""
import logging
import os
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv
# Load .env from project root
load_dotenv(Path(__file__).resolve().parent.parent.parent.parent.parent.parent / ".env")


logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MILVUS_URI = "http://localhost:19530"
COLLECTION = "coaching_scripts"
EMBED_DIM  = 768

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}


# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_client_encoding("UTF8")
    return conn


# La table coach_interactions est créée par les migrations Alembic
# (db/migrations, baseline 0001). Plus aucun DDL au runtime.


def get_advisor_history(advisor_name: str, limit: int = 5) -> list:
    """Récupère l'historique des interactions du conseiller."""
    try:
        conn = get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT message, response, gap_pct, urgency,
                       rag_used, conseil_type, created_at
                FROM coach_interactions
                WHERE advisor_name = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (advisor_name, limit))
            rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"[COACH TOOLS] get_advisor_history: {e}")
        return []


def save_interaction(
    advisor_name:   str,
    store_id:       str,
    message:        str,
    response:       str,
    gap_pct:        float,
    urgency:        str,
    rag_used:       bool,
    nb_rag_scripts: int   = 0,
    conseil_type:   str   = "general",
    confidence:     float = 0.0,
):
    """Sauvegarde une interaction coach dans PostgreSQL."""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO coach_interactions
                    (advisor_name, store_id, message, response,
                     gap_pct, urgency, rag_used, nb_rag_scripts,
                     conseil_type, confidence)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                advisor_name[:100],
                store_id[:20],
                message[:500],
                response[:1000],
                round(float(gap_pct), 2),
                urgency[:10],
                bool(rag_used),
                int(nb_rag_scripts),
                conseil_type[:30],
                round(float(confidence), 3),
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[COACH TOOLS] save_interaction: {e}")


# ── Embeddings ────────────────────────────────────────────────────────────────

def embed_text(text: str, timeout: int = 15) -> Optional[list]:
    """Génère un embedding via Ollama nomic-embed-text."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": text[:800]},
            timeout=timeout,
        )
        resp.raise_for_status()
        emb = resp.json().get("embedding", [])
        if not emb:
            return None
        if len(emb) < EMBED_DIM:
            emb = emb + [0.0] * (EMBED_DIM - len(emb))
        elif len(emb) > EMBED_DIM:
            emb = emb[:EMBED_DIM]
        return emb
    except Exception as e:
        logger.warning(f"[COACH TOOLS] embed_text: {e}")
        return None


# ── RAG ───────────────────────────────────────────────────────────────────────

def search_rag(
    query:    str,
    hour:     int,
    top_k:   int = 3,
    store_id: str = "I63",
) -> list:
    """Recherche synchrone dans Milvus."""
    try:
        from pymilvus import MilvusClient
        client = MilvusClient(uri=MILVUS_URI)

        emb = embed_text(query)
        if emb is None:
            return []

        results = client.search(
            collection_name = COLLECTION,
            data            = [emb],
            limit           = top_k + 2,
            output_fields   = [
                "pg_id", "categorie", "situation", "action",
                "produit", "argument", "impact",
                "heure_min", "heure_max",
            ],
        )

        scripts = []
        for hit in results[0]:
            e     = hit["entity"]
            score = float(hit["distance"])
            # Bonus créneau horaire
            if e.get("heure_min", 0) <= hour <= e.get("heure_max", 24):
                score += 0.12
            # store_id supprimé — collection générique multi-boutiques
            scripts.append({
                "score":      round(score, 3),
                "categorie":  e.get("categorie", ""),
                "situation":  e.get("situation", ""),
                "action":     e.get("action", ""),
                "produit":    e.get("produit", ""),
                "argument":   e.get("argument", ""),
                "impact":     e.get("impact", ""),
            })

        return sorted(scripts, key=lambda x: x["score"], reverse=True)[:top_k]

    except Exception as e:
        logger.warning(f"[COACH TOOLS] search_rag: {e}")
        return []


def format_rag_for_prompt(scripts: list) -> str:
    """Formate les scripts RAG pour injection dans le prompt."""
    if not scripts:
        return "Aucun script similaire disponible."
    lines = []
    for i, s in enumerate(scripts, 1):
        lines.append(
            f"[Script #{i} — {s['categorie']} | score {s['score']:.2f}]\n"
            f"  Situation : {s['situation'][:120]}\n"
            f"  Action    : {s['action'][:120]}\n"
            f"  Produit   : {s['produit']}\n"
            f"  Argument  : {s['argument'][:150]}\n"
            f"  Impact    : {s['impact']}"
        )
    return "\n\n".join(lines)


def format_history_for_prompt(history: list) -> str:
    """Formate l'historique du conseiller pour le prompt."""
    if not history:
        return "Première interaction de la journée."
    lines = []
    for h in history[:3]:
        ts  = h.get("created_at")
        heure = ts.strftime("%H:%M") if hasattr(ts, "strftime") else "?"
        lines.append(
            f"[{heure}] Question : {str(h.get('message',''))[:60]}… "
            f"| Gap était : {h.get('gap_pct', 0):.0f}% "
            f"| RAG : {'✓' if h.get('rag_used') else '✗'}"
        )
    return "\n".join(lines)


def format_actions_for_prompt(actions: list) -> str:
    """Formate les actions du stratège pour le prompt."""
    if not actions:
        return "Analyse stratège en cours..."
    lines = []
    for a in actions[:3]:
        lines.append(
            f"  P{a.get('priorite', '?')}. {a.get('action', '')}\n"
            f"      → Produit : {a.get('produit_cible', '')}\n"
            f"      → Argument : {a.get('argument_vente', '')[:100]}\n"
            f"      → Impact : {a.get('impact_estime', '')}"
        )
    return "\n".join(lines)


# ── Détection du type de conseil ──────────────────────────────────────────────

def detect_conseil_type(message: str) -> str:
    """Détecte le type de conseil demandé pour le logging."""
    m = message.lower()
    if any(w in m for w in ["script", "comment vendre", "que dire"]):
        return "script"
    if any(w in m for w in ["objection", "refuse", "trop cher", "pas besoin"]):
        return "objection"
    if any(w in m for w in ["objectif", "gap", "rattraper", "comment atteindre"]):
        return "objectif"
    if any(w in m for w in ["météo", "pluie", "couvert", "soleil"]):
        return "meteo"
    if any(w in m for w in ["pic", "16h", "17h", "rush", "affluence"]):
        return "trafic"
    if any(w in m for w in ["closing", "signature", "décider", "hésit"]):
        return "closing"
    if any(w in m for w in ["assurance", "cloud", "service"]):
        return "service"
    if any(w in m for w in ["forfait", "5g", "data", "recharge"]):
        return "forfait"
    return "general"


# ── Fallback local ────────────────────────────────────────────────────────────

def build_fallback_response(
    message:  str,
    gap_pct:  float,
    urgency:  str,
    weather:  str,
    actions:  list,
    scripts:  list,
    prenom:   str,
) -> str:
    """Réponse de fallback enrichie si LLM indisponible."""
    m = message.lower()
    hours_left = max(0, 20 - datetime.now().hour)

    # Utiliser le meilleur script RAG si disponible
    if scripts:
        s = scripts[0]
        if any(w in m for w in ["script","quoi","faire","action","comment","priorité"]):
            return (
                f"{s['action']}\n\n"
                f"Produit : {s['produit']}\n"
                f"Argument : {s['argument']}\n"
                f"Impact : {s['impact']}"
            )

    # Utiliser la 1ère action du stratège
    if actions:
        a = actions[0]
        if any(w in m for w in ["action","faire","quoi","priorité","maintenant"]):
            return (
                f"Action prioritaire : {a.get('action','')}\n\n"
                f"Produit : {a.get('produit_cible','')}\n"
                f"Argument : {a.get('argument_vente','')}\n"
                f"Impact : {a.get('impact_estime','')}"
            )

    # Réponses thématiques
    if any(w in m for w in ["assurance"]):
        return "Propose l'Assurance Premium à 9 DT/mois juste après la signature du terminal. Argument : 'Un écran cassé = 280 DT. L'assurance = un café par semaine.' Taux d'acceptation 67%."

    if any(w in m for w in ["5g","forfait"]):
        return "Forfait 5G Max à 49 DT/mois : fais un test de vitesse live en boutique. La différence 4G vs 5G est immédiatement visible. Conversion +45% après démonstration."

    if any(w in m for w in ["objection","cher","prix"]):
        return "Recadre le prix en mensuel : '1299 DT sur 24 mois = 54 DT/mois — moins qu'un abonnement Netflix + Spotify.' Ferme avec l'avance postpayé Ooredoo."

    if any(w in m for w in ["météo","pluie","couvert"]):
        return "Par ce temps : pousse les AirPods Pro 3 (résistants eau, 279 DT) et l'Apple Watch S10 (étanche 50m, 449 DT). Argument : 'Parfait pour vos déplacements par tous les temps.'"

    if any(w in m for w in ["objectif","gap","rattraper"]):
        return (
            f"Gap {gap_pct:.0f}% avec {hours_left}h restantes. "
            f"Bundle iPhone 16 Pro + Forfait 5G Max + Assurance = panier ~1 357 DT. "
            f"Un seul client bien closé change la journée, {prenom} !"
        )

    # Défaut
    a_txt = actions[0].get('action','Focus bundle terminal + forfait') if actions else 'Focus bundle terminal + forfait'
    return (
        f"Gap {gap_pct:.0f}% — Urgence {urgency} — {hours_left}h restantes.\n\n"
        f"Action : {a_txt}\n"
        f"Produit : {actions[0].get('produit_cible','iPhone 16 Pro + Forfait 5G Max') if actions else 'iPhone 16 Pro + Forfait 5G Max'}"
    )
