"""quality_service.py — Juge LLM branché en direct sur les recommandations.
============================================================================
Ferme le gap « le juge n'existe qu'en batch hors-ligne (evals/) » : ce service
note les recommandations réelles au fil de l'eau et persiste le résultat dans
public.recommendation_scores (migration 0014). Il couvre les DEUX domaines :

  - inventory : recommendation_text du DecisionAgent (inventory.recommendations),
                grille juge inventaire (evals.judge.judge_inventory_answer).
  - sales     : strategie_summary du Stratège (public.hitl_reviews),
                grille juge coach (evals.judge.judge_answer).

Le juge est toujours appelé HORS du cycle agent (endpoint / BackgroundTasks) —
jamais dans le chemin critique de décision, donc zéro latence ajoutée aux cycles.
Idempotent grâce à UNIQUE(domain, ref_id, judge_model).

Contexte d'ancrage — `context_level` (migration 0016)
-----------------------------------------------------
`ancrage` se note en retrouvant chaque chiffre du texte dans les données que
l'agent avait ; ce que le juge reçoit comme CONTEXTE décide donc de ce que ce
critère mesure réellement. Deux régimes, marqués sur chaque score :

  'full'    — inventaire depuis 0016 : `context_snapshot` contient les trois
              rapports amont (baseline / context / adjusted), figés avec la
              ligne, au format exact de evals/run_inventory_recommendations.
              L'ancrage est pleinement vérifié et le score est comparable à
              celui du banc hors-ligne.
  'partial' — recommandations antérieures à 0016 (pas de snapshot) et tout le
              domaine vente : hitl_reviews ne conserve pas le contexte du
              Stratège (météo, stock, promos, RAG), seulement son résumé. Les
              autres critères restent pleinement évaluables sur le texte ;
              `ancrage` est indicatif.

Les deux ne se moyennent jamais ensemble : `get_judge_summary` publie
`by_criterion` sur les seuls scores 'full' et signale le reste dans
`context_levels`. Une moyenne mélangée n'est pas imprécise, elle est
ininterprétable — c'est ce qui faisait lire des « hallucinations » sur des
chiffres réels que le juge n'avait simplement pas sous les yeux.

Pattern DB : psycopg2, une connexion par appel (comme feedback_service). Toute
erreur DB/LLM est avalée et rend un résultat vide — jamais bloquant.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# Grilles de critères (miroir de evals.judge).
INVENTORY_CRITERIA = ["clarte", "coherence", "completude", "actionabilite", "richesse", "ancrage"]
SALES_CRITERIA = ["pertinence", "ancrage", "actionnabilite", "langue", "securite"]

CRITERIA_BY_DOMAIN = {"inventory": INVENTORY_CRITERIA, "sales": SALES_CRITERIA}


def _conn():
    from app.core.config import config
    return psycopg2.connect(
        host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
        user=config.DB_USER, password=config.DB_PASSWORD,
    )


def _insert_score(conn, *, domain: str, ref_id: str, store_id: str,
                  criteria_set: str, judgment, context_level: str) -> bool:
    """Persiste un Judgment. Retourne True si écrit."""
    scores = judgment.scores or {}
    mean = round(sum(scores.values()) / len(scores), 2) if scores else 0.0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.recommendation_scores
                    (domain, ref_id, store_id, mean_score, scores, criteria_set,
                     hallucination, verdict, judge_model, context_level)
                VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
                ON CONFLICT (domain, ref_id, judge_model) DO NOTHING
            """, (
                domain, str(ref_id), store_id, mean,
                json.dumps(scores, ensure_ascii=False), criteria_set,
                judgment.hallucination, judgment.verdict, judgment.judge_model,
                context_level,
            ))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.warning("[Quality] insert score(%s/%s): %s", domain, ref_id, e)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Scoring — inventaire
# ═══════════════════════════════════════════════════════════════════════════

def _build_inventory_scenario(rec: Dict[str, Any]) -> str:
    parts = [
        f"Produit SKU {rec.get('sku')} — boutique {rec.get('store_id')}.",
        f"Action décidée par l'agent : {rec.get('action') or rec.get('recommendation_type') or '—'}.",
        f"Niveau d'urgence : {rec.get('urgency') or '—'}.",
    ]
    qty = rec.get("order_qty") or rec.get("suggested_quantity")
    if qty is not None:
        parts.append(f"Quantité suggérée : {qty} unités.")
    if rec.get("confidence") is not None:
        parts.append(f"Confiance de l'agent : {float(rec['confidence']):.2f}.")
    return " ".join(parts)


def _build_inventory_context(rec: Dict[str, Any]) -> tuple[str, str]:
    """Retourne (contexte pour le juge, context_level).

    Snapshot présent → on rend exactement la forme produite par
    `build_context_string()` du banc hors-ligne (une ligne par rapport), pour
    que les deux notes d'ancrage mesurent la même chose. Absent (lignes d'avant
    la migration 0016) → repli sur les colonnes scalaires, marqué 'partial'.
    """
    snap = rec.get("context_snapshot")
    if isinstance(snap, str):          # au cas où la colonne remonte en texte
        try:
            snap = json.loads(snap)
        except (ValueError, TypeError):
            snap = None

    if isinstance(snap, dict) and any(
        snap.get(k) for k in ("baseline_report", "context_report", "adjusted_metrics")
    ):
        parts = [
            f"sku: {snap.get('sku') or rec.get('sku')}  |  "
            f"store_id: {snap.get('store_id') or rec.get('store_id')}",
            "baseline_report: "  + json.dumps(snap.get("baseline_report", {}),  ensure_ascii=False, default=str),
            "context_report: "   + json.dumps(snap.get("context_report", {}),   ensure_ascii=False, default=str),
            "adjusted_metrics: " + json.dumps(snap.get("adjusted_metrics", {}), ensure_ascii=False, default=str),
            f"business_objective: {snap.get('business_objective') or 'standard'}",
        ]
        return "\n".join(parts), "full"

    ref = {
        "action": rec.get("action") or rec.get("recommendation_type"),
        "urgency": rec.get("urgency"),
        "order_qty": rec.get("order_qty") or rec.get("suggested_quantity"),
        "confidence": float(rec["confidence"]) if rec.get("confidence") is not None else None,
        "order_cost": float(rec["order_cost"]) if rec.get("order_cost") is not None else None,
        "holding_cost": float(rec["holding_cost"]) if rec.get("holding_cost") is not None else None,
    }
    return json.dumps({k: v for k, v in ref.items() if v is not None}, ensure_ascii=False), "partial"


def score_inventory_recommendations(conn, store_id: Optional[str], limit: int) -> Dict[str, int]:
    res = {"scored": 0, "errors": 0, "full_context": 0, "partial_context": 0}
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        store_clause = "AND r.store_id = %(sid)s" if store_id else ""
        cur.execute(f"""
            SELECT r.id, r.sku, r.store_id, r.action, r.recommendation_type,
                   r.urgency, r.order_qty, r.suggested_quantity, r.confidence,
                   r.order_cost, r.holding_cost, r.recommendation_text,
                   r.context_snapshot
            FROM inventory.recommendations r
            LEFT JOIN public.recommendation_scores s
                   ON s.domain = 'inventory' AND s.ref_id = r.id::text
            WHERE r.recommendation_text IS NOT NULL
              AND length(trim(r.recommendation_text)) > 0
              AND s.id IS NULL
              {store_clause}
            ORDER BY r.created_at DESC
            LIMIT %(limit)s
        """, {"sid": store_id, "limit": limit})
        pending = cur.fetchall()

    if not pending:
        return res

    from evals.judge import judge_inventory_answer
    for rec in pending:
        context, level = _build_inventory_context(rec)
        try:
            j = judge_inventory_answer(
                _build_inventory_scenario(rec),
                rec["recommendation_text"],
                context=context,
                expected_behaviors=[
                    "recommandation claire et immédiatement actionnable pour un gérant de boutique",
                    "justifie POURQUOI cette action, pas seulement QUOI faire",
                ],
                max_retries=1,
            )
        except Exception as e:
            logger.warning("[Quality] judge inventaire(%s): %s", rec["id"], e)
            res["errors"] += 1
            continue
        if j.ok and j.scores and _insert_score(
            conn, domain="inventory", ref_id=rec["id"], store_id=rec["store_id"],
            criteria_set="inventory", judgment=j, context_level=level,
        ):
            res["scored"] += 1
            res["full_context" if level == "full" else "partial_context"] += 1
        else:
            res["errors"] += 1
    return res


# ═══════════════════════════════════════════════════════════════════════════
# Scoring — vente (stratégies Stratège via hitl_reviews)
# ═══════════════════════════════════════════════════════════════════════════

def _build_sales_question(row: Dict[str, Any]) -> str:
    gap = row.get("gap_pct")
    gap_txt = f"{float(gap):.0f}%" if gap is not None else "—"
    return (
        f"Situation boutique {row.get('store_id')} : niveau d'urgence "
        f"{row.get('urgency_level') or '—'}, écart vs objectif journalier {gap_txt}. "
        "Quelle stratégie de vente recommandes-tu aux conseillers ?"
    )


def _build_sales_context(row: Dict[str, Any]) -> str:
    ctx = {"urgency_level": row.get("urgency_level"), "gap_pct": row.get("gap_pct")}
    actions = row.get("actions")
    if actions:
        ctx["actions"] = actions
    if row.get("critique_feedback"):
        ctx["critique_feedback"] = row["critique_feedback"]
    return json.dumps(ctx, ensure_ascii=False, default=str)


def score_sales_recommendations(conn, store_id: Optional[str], limit: int) -> Dict[str, int]:
    # Toujours 'partial' : hitl_reviews conserve le résumé du Stratège, pas le
    # contexte qui l'a produit (météo, stock, promos, docs RAG). Tant que ce
    # contexte n'est pas figé comme l'est `context_snapshot` côté inventaire,
    # `ancrage` reste indicatif sur ce domaine — et doit le dire.
    res = {"scored": 0, "errors": 0, "full_context": 0, "partial_context": 0}
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        store_clause = "AND h.store_id = %(sid)s" if store_id else ""
        cur.execute(f"""
            SELECT h.id, h.store_id, h.urgency_level, h.gap_pct,
                   h.critique_feedback, h.strategie_summary, h.actions
            FROM public.hitl_reviews h
            LEFT JOIN public.recommendation_scores s
                   ON s.domain = 'sales' AND s.ref_id = h.id::text
            WHERE h.strategie_summary IS NOT NULL
              AND length(trim(h.strategie_summary)) > 0
              AND s.id IS NULL
              {store_clause}
            ORDER BY h.created_at DESC
            LIMIT %(limit)s
        """, {"sid": store_id, "limit": limit})
        pending = cur.fetchall()

    if not pending:
        return res

    from evals.judge import judge_answer
    for row in pending:
        try:
            j = judge_answer(
                _build_sales_question(row),
                row["strategie_summary"],
                context=_build_sales_context(row),
                expected_behaviors=[
                    "stratégie de vente concrète, priorisée et actionnable pour les conseillers",
                    "cohérente avec le niveau d'urgence et l'écart objectif",
                ],
                max_retries=1,
            )
        except Exception as e:
            logger.warning("[Quality] judge vente(%s): %s", row["id"], e)
            res["errors"] += 1
            continue
        if j.ok and j.scores and _insert_score(
            conn, domain="sales", ref_id=row["id"], store_id=row["store_id"],
            criteria_set="coach", judgment=j, context_level="partial",
        ):
            res["scored"] += 1
            res["partial_context"] += 1
        else:
            res["errors"] += 1
    return res


# ═══════════════════════════════════════════════════════════════════════════
# Entrée publique — score les deux domaines
# ═══════════════════════════════════════════════════════════════════════════

def score_recent_recommendations(
    store_id: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Note les recommandations non encore évaluées (inventaire + vente).
    Retourne un récap par domaine."""
    _empty = {"scored": 0, "errors": 0, "full_context": 0, "partial_context": 0}
    out: Dict[str, Any] = {
        "inventory": dict(_empty),
        "sales": dict(_empty),
        "total_scored": 0,
    }
    conn = None
    try:
        # Import tardif : evals dépend de httpx/dotenv — on n'alourdit pas le
        # démarrage de l'API et on reste robuste si le package est absent.
        try:
            import evals.judge  # noqa: F401
        except Exception as e:
            logger.warning("[Quality] juge indisponible: %s", e)
            return out

        conn = _conn()
        try:
            out["inventory"] = score_inventory_recommendations(conn, store_id, limit)
        except Exception as e:
            logger.warning("[Quality] scoring inventaire: %s", e)
        try:
            out["sales"] = score_sales_recommendations(conn, store_id, limit)
        except Exception as e:
            logger.warning("[Quality] scoring vente: %s", e)
        out["total_scored"] = out["inventory"]["scored"] + out["sales"]["scored"]
    except Exception as e:
        logger.warning("[Quality] score_recent_recommendations: %s", e)
    finally:
        if conn:
            conn.close()
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Agrégats pour la page Supervision
# ═══════════════════════════════════════════════════════════════════════════

def _domain_summary(cur, domain: str, days: int, store_id: Optional[str]) -> Dict[str, Any]:
    criteria = CRITERIA_BY_DOMAIN[domain]
    store_clause = "AND store_id = %(sid)s" if store_id else ""
    params = {"sid": store_id, "days": days, "dom": domain}

    summary: Dict[str, Any] = {
        "domain": domain,
        "n_scored": 0,
        "mean_score": None,
        "mean_pct": None,
        "by_criterion": {c: None for c in criteria},
        "hallucination_rate": None,
        "judge_model": None,
        "worst": [],
        # Répartition des scores par régime de contexte, et taille de la base
        # sur laquelle `ancrage` / `hallucination_rate` sont réellement calculés.
        "context_levels": {"full": 0, "partial": 0},
        "ancrage_basis": {"level": "full", "n": 0},
    }

    cur.execute(f"""
        SELECT COUNT(*) AS n, AVG(mean_score) AS avg_mean,
               MAX(judge_model) AS judge_model,
               COUNT(*) FILTER (WHERE context_level = 'full')    AS n_full,
               COUNT(*) FILTER (WHERE context_level IS DISTINCT FROM 'full') AS n_partial
        FROM public.recommendation_scores
        WHERE domain = %(dom)s
          AND created_at >= NOW() - (%(days)s || ' days')::interval
          {store_clause}
    """, params)
    row = cur.fetchone() or {}
    n = int(row.get("n") or 0)
    summary["n_scored"] = n
    summary["context_levels"] = {"full": int(row.get("n_full") or 0),
                                 "partial": int(row.get("n_partial") or 0)}
    if n and row.get("avg_mean") is not None:
        avg = float(row["avg_mean"])
        summary["mean_score"] = round(avg, 2)
        summary["mean_pct"] = round(avg / 5 * 100, 1)
        summary["judge_model"] = row.get("judge_model")

    # `ancrage` et le taux d'hallucination qui en découle ne veulent rien dire
    # sur un score 'partial' : le juge n'avait pas les données pour recouper.
    # Les inclure ne rendrait pas la moyenne « un peu pessimiste », il la rendrait
    # fausse dans une proportion inconnue. On les restreint à 'full' et on publie
    # la taille de cette base ; les autres critères portent sur le texte seul et
    # restent calculés sur la totalité des scores.
    cur.execute(f"""
        SELECT COUNT(*) AS n,
               AVG(CASE WHEN hallucination THEN 1 ELSE 0 END) AS hall_rate
        FROM public.recommendation_scores
        WHERE domain = %(dom)s AND context_level = 'full'
          AND created_at >= NOW() - (%(days)s || ' days')::interval
          {store_clause}
    """, params)
    fr = cur.fetchone() or {}
    n_full = int(fr.get("n") or 0)
    summary["ancrage_basis"]["n"] = n_full
    if n_full:
        summary["hallucination_rate"] = round(float(fr.get("hall_rate") or 0) * 100, 1)

    for c in criteria:
        level_clause = "AND context_level = 'full'" if c == "ancrage" else ""
        cur.execute(f"""
            SELECT AVG((scores->>%(crit)s)::numeric) AS v
            FROM public.recommendation_scores
            WHERE domain = %(dom)s AND scores ? %(crit)s
              AND created_at >= NOW() - (%(days)s || ' days')::interval
              {level_clause}
              {store_clause}
        """, {**params, "crit": c})
        v = cur.fetchone()
        summary["by_criterion"][c] = round(float(v["v"]), 2) if v and v["v"] is not None else None

    cur.execute(f"""
        SELECT mean_score, verdict, hallucination, scores
        FROM public.recommendation_scores
        WHERE domain = %(dom)s
          AND created_at >= NOW() - (%(days)s || ' days')::interval
          {store_clause}
        ORDER BY mean_score ASC NULLS LAST
        LIMIT 5
    """, params)
    summary["worst"] = [
        {
            "mean_score": float(w["mean_score"]) if w.get("mean_score") is not None else None,
            "hallucination": bool(w.get("hallucination")),
            "verdict": w.get("verdict"),
        }
        for w in cur.fetchall()
    ]
    return summary


def get_judge_summary(store_id: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
    """Résumé des scores juge par domaine (inventory + sales) + moyenne globale."""
    out: Dict[str, Any] = {
        "window_days": days,
        "inventory": None,
        "sales": None,
        "overall_mean": None,
        "overall_pct": None,
        "n_scored": 0,
        "context_levels": {"full": 0, "partial": 0},
    }
    conn = None
    try:
        conn = _conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            out["inventory"] = _domain_summary(cur, "inventory", days, store_id)
            out["sales"] = _domain_summary(cur, "sales", days, store_id)

            store_clause = "AND store_id = %(sid)s" if store_id else ""
            cur.execute(f"""
                SELECT COUNT(*) AS n, AVG(mean_score) AS avg_mean,
                       COUNT(*) FILTER (WHERE context_level = 'full') AS n_full,
                       COUNT(*) FILTER (WHERE context_level IS DISTINCT FROM 'full') AS n_partial
                FROM public.recommendation_scores
                WHERE created_at >= NOW() - (%(days)s || ' days')::interval
                  {store_clause}
            """, {"sid": store_id, "days": days})
            row = cur.fetchone() or {}
            n = int(row.get("n") or 0)
            out["n_scored"] = n
            out["context_levels"] = {"full": int(row.get("n_full") or 0),
                                     "partial": int(row.get("n_partial") or 0)}
            if n and row.get("avg_mean") is not None:
                avg = float(row["avg_mean"])
                out["overall_mean"] = round(avg, 2)
                out["overall_pct"] = round(avg / 5 * 100, 1)
    except Exception as e:
        logger.warning("[Quality] get_judge_summary: %s", e)
    finally:
        if conn:
            conn.close()
    return out
