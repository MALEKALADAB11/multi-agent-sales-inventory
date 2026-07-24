"""
agent_outputs.py — Sorties vivantes de TOUS les agents, lues en base.

Séparation volontaire avec le RAG :

    RAG (Milvus)        → connaissance stable : scripts, playbooks, catalogue,
                          décisions passées et leur résultat mesuré.
    agent_outputs (PG)  → état vivant : ce que chaque agent a produit au dernier
                          cycle. Jamais indexé, toujours relu.

Indexer les alertes ou le forecast dans Milvus ferait citer au coach une rupture
résolue il y a deux heures. L'état vivant se lit, il ne se mémorise pas.

Agents couverts :
  • analysis_agent  → résumé analyste, cause racine, forecast fin de journée
  • context_agent   → uplift de demande, signal dominant (météo/promo/événement)
  • decision_agent  → recommandations en attente, escalades humaines
  • alerting        → alertes ouvertes avec l'action recommandée par l'agent
  • forecasting     → demande prévue à 24 h par SKU, avec intervalle de confiance
  • pulse temps réel→ CA, transactions, ruptures observées à l'instant
  • santé des runs  → dernier statut de chaque agent (un agent muet est un signal)
"""

import logging
import threading
import time

import psycopg2
import psycopg2.extras

from app.core.config import config

logger = logging.getLogger(__name__)

_DB_CFG = {
    "host":     config.DB_HOST,
    "port":     config.DB_PORT,
    "dbname":   config.DB_NAME,
    "user":     config.DB_USER,
    "password": config.DB_PASSWORD,
}

# Les cycles tournent toutes les 15 min ; 60 s de cache absorbe une rafale de
# messages sans jamais servir un état franchement périmé.
_TTL_SECONDS = 60
_cache: dict[str, tuple[dict, float]] = {}
_lock = threading.Lock()

EMPTY: dict = {
    "analyst": {}, "context": {}, "decision": {}, "alerts": [],
    "forecast": [], "pulse": {}, "agents_health": [],
}


# ── Requêtes : une par agent, toutes bornées dans le temps ────────────────────

_Q_ANALYST = """
    SELECT analyst_summary, cause_racine, forecast_eod, urgency_level,
           gap_pct, gap_amount, ca_today, ca_target, nb_actions, created_at
      FROM monitoring.cycle_logs
     WHERE store_id = %s AND analyst_summary IS NOT NULL
     ORDER BY created_at DESC
     LIMIT 1
"""

_Q_CONTEXT = """
    SELECT demand_uplift_pct, dominant_signal, interpretation,
           weather_impact, promo_impact, event_impact, holiday_impact,
           confidence, created_at
      FROM inventory.context_adjustments
     WHERE store_id = %s
       AND created_at >= NOW() - INTERVAL '24 hours'
     ORDER BY created_at DESC
     LIMIT 1
"""

_Q_DECISION = """
    SELECT COUNT(*) FILTER (WHERE status = 'pending')                AS en_attente,
           COUNT(*) FILTER (WHERE escalate_to_human)                 AS escalades,
           COUNT(*) FILTER (WHERE urgency IN ('high', 'critical'))   AS urgentes,
           MAX(created_at)                                           AS derniere
      FROM inventory.recommendations
     WHERE store_id = %s
       AND created_at >= NOW() - INTERVAL '24 hours'
"""

_Q_ESCALATIONS = """
    SELECT COALESCE(p.nom, r.sku::text) AS nom, r.escalation_reason,
           r.order_qty, r.confidence
      FROM inventory.recommendations r
      LEFT JOIN sales.produits p ON p.sku = r.sku
     WHERE r.store_id = %s AND r.escalate_to_human AND r.status = 'pending'
     ORDER BY r.created_at DESC
     LIMIT 3
"""

# Les alertes que l'agent a réellement levées, avec SON action recommandée —
# à ne pas confondre avec les seuils recalculés à la volée sur stock_levels.
_Q_ALERTS = """
    SELECT a.alert_type, a.severity, a.message, a.recommended_action,
           COALESCE(p.nom, a.sku::text) AS nom, a.triggered_at
      FROM inventory.alerts a
      LEFT JOIN sales.produits p ON p.sku = a.sku
     WHERE a.store_id = %s
       AND a.status NOT IN ('resolved', 'closed')
     ORDER BY CASE a.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                              WHEN 'medium' THEN 2 ELSE 3 END,
              a.triggered_at DESC
     LIMIT 5
"""

_Q_FORECAST = """
    SELECT DISTINCT ON (f.sku)
           COALESCE(p.nom, f.sku::text) AS nom, f.sku,
           f.demand_24h, f.confidence_low, f.confidence_high, f.model_version
      FROM inventory.demand_forecast f
      LEFT JOIN sales.produits p ON p.sku = f.sku
     WHERE f.store_id = %s
       AND f.forecast_date >= CURRENT_DATE
     ORDER BY f.sku, f.created_at DESC
"""

_Q_PULSE = """
    SELECT ca_today, nb_transactions, nb_ruptures, nb_critiques, pulse_at
      FROM monitoring.realtime_store_pulse
     WHERE store_id = %s
     ORDER BY pulse_at DESC
     LIMIT 1
"""

# Un agent dont le dernier run date de 3 h, ou a échoué, rend son output suspect.
_Q_HEALTH = """
    SELECT DISTINCT ON (agent_name)
           agent_name, status, completed_at,
           EXTRACT(EPOCH FROM (NOW() - completed_at)) / 60 AS minutes_ago
      FROM inventory.agent_runs
     WHERE store_id = %s AND completed_at IS NOT NULL
     ORDER BY agent_name, completed_at DESC
"""


def _fetch(store_id: str) -> dict:
    out = {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in EMPTY.items()}

    conn = psycopg2.connect(**_DB_CFG, connect_timeout=8)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute(_Q_ANALYST, (store_id,))
            if (row := cur.fetchone()):
                out["analyst"] = {
                    "resume":       (row["analyst_summary"] or "")[:400],
                    "cause_racine": row["cause_racine"] or "",
                    "forecast_eod": float(row["forecast_eod"] or 0),
                    "urgency":      row["urgency_level"] or "",
                    "gap_pct":      float(row["gap_pct"] or 0),
                    "nb_actions":   int(row["nb_actions"] or 0),
                    "age_min":      _age_minutes(row["created_at"]),
                }

            cur.execute(_Q_CONTEXT, (store_id,))
            if (row := cur.fetchone()):
                out["context"] = {
                    "uplift_pct":      float(row["demand_uplift_pct"] or 0),
                    "signal_dominant": row["dominant_signal"] or "",
                    "interpretation":  (row["interpretation"] or "")[:280],
                    "meteo":           float(row["weather_impact"] or 0),
                    "promo":           float(row["promo_impact"] or 0),
                    "evenement":       float(row["event_impact"] or 0),
                    "ferie":           float(row["holiday_impact"] or 0),
                    "confiance":       float(row["confidence"] or 0),
                    "age_min":         _age_minutes(row["created_at"]),
                }

            cur.execute(_Q_DECISION, (store_id,))
            if (row := cur.fetchone()):
                out["decision"] = {
                    "en_attente": int(row["en_attente"] or 0),
                    "escalades":  int(row["escalades"] or 0),
                    "urgentes":   int(row["urgentes"] or 0),
                    "age_min":    _age_minutes(row["derniere"]),
                }
            cur.execute(_Q_ESCALATIONS, (store_id,))
            out["decision"]["details_escalades"] = [
                {"nom": r["nom"], "motif": (r["escalation_reason"] or "")[:90],
                 "qty": int(r["order_qty"] or 0), "confiance": float(r["confidence"] or 0)}
                for r in cur.fetchall()
            ]

            cur.execute(_Q_ALERTS, (store_id,))
            out["alerts"] = [
                {"nom": r["nom"], "type": r["alert_type"], "severite": r["severity"],
                 "message": (r["message"] or "")[:120],
                 "action_recommandee": (r["recommended_action"] or "")[:120]}
                for r in cur.fetchall()
            ]

            cur.execute(_Q_FORECAST, (store_id,))
            rows = sorted(cur.fetchall(), key=lambda r: float(r["demand_24h"] or 0), reverse=True)
            out["forecast"] = [
                {"nom": r["nom"], "demande_24h": round(float(r["demand_24h"] or 0), 1),
                 "min": round(float(r["confidence_low"] or 0), 1),
                 "max": round(float(r["confidence_high"] or 0), 1),
                 "modele": r["model_version"] or ""}
                for r in rows[:4]
            ]

            cur.execute(_Q_PULSE, (store_id,))
            if (row := cur.fetchone()):
                out["pulse"] = {
                    "ca_today":    float(row["ca_today"] or 0),
                    "nb_tx":       int(row["nb_transactions"] or 0),
                    "nb_ruptures": int(row["nb_ruptures"] or 0),
                    "nb_critiques": int(row["nb_critiques"] or 0),
                    "age_min":     _age_minutes(row["pulse_at"]),
                }

            cur.execute(_Q_HEALTH, (store_id,))
            out["agents_health"] = [
                {"agent": r["agent_name"], "statut": r["status"],
                 "age_min": round(float(r["minutes_ago"] or 0))}
                for r in cur.fetchall()
            ]
        finally:
            cur.close()
    finally:
        conn.close()

    return out


def _age_minutes(ts) -> int:
    if not ts:
        return -1
    try:
        return max(0, round((time.time() - ts.timestamp()) / 60))
    except Exception:
        return -1


def get_agent_outputs(store_id: str) -> dict:
    """Sorties de tous les agents pour la boutique. Cache 60 s. Ne lève jamais."""
    with _lock:
        hit = _cache.get(store_id)
        if hit and time.time() - hit[1] < _TTL_SECONDS:
            return hit[0]

    try:
        data = _fetch(store_id)
    except Exception as e:
        logger.warning("[COACH AGENTS] lecture échouée: %.100s", str(e))
        return {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in EMPTY.items()}

    with _lock:
        _cache[store_id] = (data, time.time())
    return data


# ══════════════════════════════════════════════════════════════════════════════
# RENDU PROMPT
# ══════════════════════════════════════════════════════════════════════════════

_STALE_AFTER_MIN = 90


def format_agent_block(outputs: dict, max_chars: int = 1400) -> str:
    """
    Bloc « ce que les agents disent maintenant », injecté dans le prompt système.

    Chaque section porte son âge : un forecast de 3 h reste utile, une alerte de
    3 h ne l'est plus. Le coach doit pouvoir pondérer, donc il doit voir l'âge.
    """
    if not outputs:
        return ""

    lines: list[str] = []

    analyst = outputs.get("analyst") or {}
    if analyst.get("resume"):
        age = analyst.get("age_min", -1)
        lines.append(
            f"AGENT ANALYSTE (il y a {age} min) : {analyst['resume']}\n"
            f"  Cause racine : {analyst.get('cause_racine') or 'non identifiée'} "
            f"| Prévision fin de journée : {analyst.get('forecast_eod', 0):,.0f} TND"
        )

    ctx = outputs.get("context") or {}
    if ctx.get("signal_dominant"):
        lines.append(
            f"AGENT CONTEXTE (il y a {ctx.get('age_min', -1)} min) : signal dominant "
            f"« {ctx['signal_dominant']} », demande ajustée de {ctx.get('uplift_pct', 0):+.1f}% "
            f"(confiance {ctx.get('confiance', 0):.0%})\n"
            f"  {ctx.get('interpretation', '')}"
        )

    alerts = outputs.get("alerts") or []
    if alerts:
        rendered = "\n".join(
            f"  - [{a['severite'].upper()}] {a['nom']} : {a['message']}"
            + (f" → action agent : {a['action_recommandee']}" if a["action_recommandee"] else "")
            for a in alerts
        )
        lines.append(f"ALERTES STOCK OUVERTES ({len(alerts)}) :\n{rendered}")

    decision = outputs.get("decision") or {}
    if decision.get("en_attente") or decision.get("escalades"):
        head = (f"AGENT DÉCISION : {decision.get('en_attente', 0)} recommandation(s) en attente, "
                f"{decision.get('escalades', 0)} escalade(s) humaine(s)")
        details = "\n".join(
            f"  - {d['nom']} : {d['motif']} (qté {d['qty']}, confiance {d['confiance']:.0%})"
            for d in decision.get("details_escalades", [])
        )
        lines.append(head + ("\n" + details if details else ""))

    forecast = outputs.get("forecast") or []
    if forecast:
        rendered = "\n".join(
            f"  - {f['nom']} : {f['demande_24h']} unités attendues "
            f"(fourchette {f['min']}–{f['max']})"
            for f in forecast
        )
        lines.append(f"PRÉVISION DE DEMANDE 24 H :\n{rendered}")

    pulse = outputs.get("pulse") or {}
    if pulse and pulse.get("age_min", 999) < _STALE_AFTER_MIN:
        lines.append(
            f"PULSE TEMPS RÉEL (il y a {pulse['age_min']} min) : "
            f"{pulse['ca_today']:,.0f} TND | {pulse['nb_tx']} transactions | "
            f"{pulse['nb_ruptures']} rupture(s), {pulse['nb_critiques']} stock(s) critique(s)"
        )

    # Un agent silencieux depuis longtemps : le coach doit le dire plutôt que de
    # présenter des données périmées comme actuelles.
    stale = [a["agent"] for a in outputs.get("agents_health", [])
             if a.get("age_min", 0) > _STALE_AFTER_MIN or a.get("statut") == "failed"]
    if stale:
        lines.append(f"⚠ Agents sans run récent : {', '.join(stale)} — signale-le si on t'interroge dessus.")

    block = "\n\n".join(lines)
    return block[:max_chars]
