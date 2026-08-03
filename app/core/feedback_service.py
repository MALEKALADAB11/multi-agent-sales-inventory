"""
feedback_service.py — Boucle de feedback humain → agents.
==========================================================
Trois sources de vérité :
  - public.agent_feedback   : incitations suivies/ignorées par les conseillers
  - public.hitl_reviews     : stratégies approuvées/rejetées par le manager
  - supply.purchase_orders  : devenir des PO suggérés (SUGGERE → BROUILLON/ANNULE)

`get_learning_context_sync()` agrège ces signaux en un bloc texte compact,
injecté dans les prompts du DecisionAgent (inventory) et du Stratège (sales)
pour que les agents tiennent compte des arbitrages humains passés.

Pattern DB : psycopg2, une connexion par appel (comme SyncPurchaseOrderRepo).
Toute erreur DB rend un résultat vide — jamais bloquant pour un cycle agent.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


def _conn():
    from app.core.config import config
    return psycopg2.connect(
        host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
        user=config.DB_USER, password=config.DB_PASSWORD,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Écriture
# ═══════════════════════════════════════════════════════════════════════════

def record_feedback(
    *,
    store_id: str,
    source: str,                 # 'incitation' | 'hitl' | 'po' | 'reco'
    decision: str,               # 'followed' | 'ignored' | 'approved' | 'rejected'
    ref_id: Optional[str] = None,
    sku: Optional[int] = None,
    action_type: Optional[str] = None,
    reason: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Insère un feedback. Retourne l'id ou None si erreur DB."""
    conn = None
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.agent_feedback
                    (store_id, source, ref_id, sku, action_type, decision, reason, payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                RETURNING id
                """,
                (store_id, source, ref_id, sku, action_type, decision, reason,
                 json.dumps(payload or {}, ensure_ascii=False)),
            )
            fb_id = cur.fetchone()[0]
        conn.commit()
        logger.info("[Feedback] enregistré id=%s store=%s source=%s decision=%s",
                    fb_id, store_id, source, decision)
        return fb_id
    except Exception as e:
        logger.warning("[Feedback] record_feedback: %s", e)
        return None
    finally:
        if conn:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Agrégats
# ═══════════════════════════════════════════════════════════════════════════

def get_feedback_stats(store_id: Optional[str] = None, days: int = 14) -> Dict[str, Any]:
    """
    Agrégats pour le tableau KPI et le contexte d'apprentissage :
      - incitations   : taux de suivi conseiller (followed / total)
      - hitl          : taux d'approbation manager
      - po            : devenir des PO suggérés par le DecisionAgent
      - recent_rejections : dernières raisons de rejet (pour les prompts)
    """
    stats: Dict[str, Any] = {
        "window_days": days,
        "incitations": {"followed": 0, "ignored": 0, "follow_rate": None},
        "hitl":        {"approved": 0, "rejected": 0, "pending": 0, "approval_rate": None},
        "po":          {"suggested": 0, "accepted": 0, "cancelled": 0, "adoption_rate": None},
        "recent_rejections": [],
        "top_rejection_reasons": [],
    }
    conn = None
    try:
        conn = _conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            store_clause = "AND store_id = %(sid)s" if store_id else ""
            params = {"sid": store_id, "days": days}

            # 1. Incitations conseiller
            cur.execute(f"""
                SELECT decision, COUNT(*) AS cnt
                FROM public.agent_feedback
                WHERE source = 'incitation'
                  AND created_at >= NOW() - (%(days)s || ' days')::interval
                  {store_clause}
                GROUP BY decision
            """, params)
            for r in cur.fetchall():
                if r["decision"] in ("followed", "ignored"):
                    stats["incitations"][r["decision"]] = int(r["cnt"])
            tot = stats["incitations"]["followed"] + stats["incitations"]["ignored"]
            if tot:
                stats["incitations"]["follow_rate"] = round(
                    stats["incitations"]["followed"] / tot * 100, 1)

            # 2. HITL manager
            cur.execute(f"""
                SELECT status, COUNT(*) AS cnt
                FROM public.hitl_reviews
                WHERE created_at >= NOW() - (%(days)s || ' days')::interval
                  {store_clause}
                GROUP BY status
            """, params)
            for r in cur.fetchall():
                if r["status"] in ("approved", "rejected", "pending"):
                    stats["hitl"][r["status"]] = int(r["cnt"])
            decided = stats["hitl"]["approved"] + stats["hitl"]["rejected"]
            if decided:
                stats["hitl"]["approval_rate"] = round(
                    stats["hitl"]["approved"] / decided * 100, 1)

            # 3. PO suggérés — SUGGERE encore ouvert, BROUILLON+ = accepté, ANNULE = refus
            cur.execute(f"""
                SELECT statut, COUNT(*) AS cnt
                FROM supply.purchase_orders
                WHERE recommendation_id IS NOT NULL
                  AND created_at >= NOW() - (%(days)s || ' days')::interval
                  {store_clause}
                GROUP BY statut
            """, params)
            po_counts = {r["statut"]: int(r["cnt"]) for r in cur.fetchall()}
            suggested = sum(po_counts.values())
            cancelled = po_counts.get("ANNULE", 0)
            still_open = po_counts.get("SUGGERE", 0)
            accepted  = suggested - cancelled - still_open
            stats["po"] = {
                "suggested": suggested,
                "accepted": accepted,
                "cancelled": cancelled,
                "adoption_rate": round(accepted / (accepted + cancelled) * 100, 1)
                                 if (accepted + cancelled) else None,
                "by_status": po_counts,
            }

            # 4. Raisons de rejet récentes (HITL + feedback explicite)
            cur.execute(f"""
                SELECT reason, created_at FROM (
                    SELECT approver_note AS reason, reviewed_at AS created_at, store_id
                    FROM public.hitl_reviews
                    WHERE status = 'rejected' AND approver_note IS NOT NULL
                    UNION ALL
                    SELECT reason, created_at, store_id
                    FROM public.agent_feedback
                    WHERE decision IN ('rejected','ignored') AND reason IS NOT NULL
                ) x
                WHERE created_at >= NOW() - (%(days)s || ' days')::interval
                  {store_clause}
                ORDER BY created_at DESC
                LIMIT 5
            """, params)
            stats["recent_rejections"] = [
                str(r["reason"])[:160] for r in cur.fetchall() if r["reason"]
            ]

            # 5. Top causes de rejet (groupées par raison, triées par fréquence) —
            #    alimente la section "Top causes de rejets" de la page Supervision.
            #    Regroupement sur le texte exact : les raisons sont saisies libres,
            #    donc seuls les doublons mot-pour-mot se cumulent.
            cur.execute(f"""
                SELECT reason, COUNT(*) AS cnt, MAX(created_at) AS last_seen
                FROM (
                    SELECT approver_note AS reason, reviewed_at AS created_at, store_id
                    FROM public.hitl_reviews
                    WHERE status = 'rejected' AND approver_note IS NOT NULL
                    UNION ALL
                    SELECT reason, created_at, store_id
                    FROM public.agent_feedback
                    WHERE decision IN ('rejected','ignored') AND reason IS NOT NULL
                ) x
                WHERE created_at >= NOW() - (%(days)s || ' days')::interval
                  {store_clause}
                GROUP BY reason
                ORDER BY cnt DESC, last_seen DESC
                LIMIT 5
            """, params)
            stats["top_rejection_reasons"] = [
                {"reason": str(r["reason"])[:160], "count": int(r["cnt"])}
                for r in cur.fetchall() if r["reason"]
            ]
    except Exception as e:
        logger.warning("[Feedback] get_feedback_stats: %s", e)
    finally:
        if conn:
            conn.close()
    return stats


# ═══════════════════════════════════════════════════════════════════════════
# Vue d'ensemble pour la page Supervision (fenêtre courante vs baseline)
# ═══════════════════════════════════════════════════════════════════════════

def _decided_totals(stats: Dict[str, Any]) -> tuple[int, int]:
    """Consolide les 3 boucles de feedback en (acceptés, rejetés) décidés."""
    inc = stats["incitations"]
    hitl = stats["hitl"]
    po = stats["po"]
    accepted = int(inc["followed"]) + int(hitl["approved"]) + int(po.get("accepted", 0))
    rejected = int(inc["ignored"]) + int(hitl["rejected"]) + int(po.get("cancelled", 0))
    return accepted, rejected


def get_feedback_overview(
    store_id: Optional[str] = None,
    window_days: int = 30,
    baseline_days: int = 90,
) -> Dict[str, Any]:
    """
    Cartes KPI de la page Supervision : taux d'acceptation et de rejet sur la
    fenêtre courante (30j par défaut), comparés au taux de rejet moyen sur la
    baseline 3 mois (90j). Consolide les 3 boucles humaines réelles
    (incitations coach, HITL stratèges, PO stock) — aucune donnée mockée.
    """
    cur = get_feedback_stats(store_id=store_id, days=window_days)
    base = get_feedback_stats(store_id=store_id, days=baseline_days)

    a_c, r_c = _decided_totals(cur)
    a_b, r_b = _decided_totals(base)
    dec_c = a_c + r_c
    dec_b = a_b + r_b

    accept_rate = round(a_c / dec_c * 100, 1) if dec_c else None
    reject_rate = round(r_c / dec_c * 100, 1) if dec_c else None
    baseline_reject_rate = round(r_b / dec_b * 100, 1) if dec_b else None
    reject_delta = (round(reject_rate - baseline_reject_rate, 1)
                    if (reject_rate is not None and baseline_reject_rate is not None) else None)

    return {
        "window_days": window_days,
        "baseline_days": baseline_days,
        "decided": dec_c,
        "accepted": a_c,
        "rejected": r_c,
        "accept_rate": accept_rate,
        "reject_rate": reject_rate,
        "baseline_reject_rate": baseline_reject_rate,
        "reject_delta": reject_delta,          # + = plus de rejets que la moyenne 3 mois
        "by_loop": {
            "incitations": cur["incitations"],
            "hitl": cur["hitl"],
            "po": cur["po"],
        },
        "recent_rejections": cur["recent_rejections"],
        "top_rejection_reasons": cur["top_rejection_reasons"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Contexte d'apprentissage pour les prompts agents
# ═══════════════════════════════════════════════════════════════════════════

def get_learning_context_sync(
    store_id: Optional[str] = None,
    sku: Optional[str] = None,
    days: int = 14,
) -> str:
    """
    Bloc texte compact (français) injecté dans les prompts DecisionAgent /
    Stratège. Chaîne vide si aucun signal — l'appelant n'injecte alors rien.
    """
    stats = get_feedback_stats(store_id=store_id, days=days)
    lines: list[str] = []

    hitl = stats["hitl"]
    if hitl["approval_rate"] is not None:
        lines.append(
            f"- Sur {days}j, le manager a approuvé {hitl['approval_rate']:.0f}% "
            f"des stratégies escaladées ({hitl['approved']} oui / {hitl['rejected']} non)."
        )

    po = stats["po"]
    if po.get("adoption_rate") is not None:
        lines.append(
            f"- {po['adoption_rate']:.0f}% des bons de commande suggérés ont été "
            f"acceptés ({po['accepted']} acceptés, {po['cancelled']} annulés). "
            + ("Sois plus conservateur sur les quantités." if po["adoption_rate"] < 50 else "")
        )

    inc = stats["incitations"]
    if inc["follow_rate"] is not None:
        lines.append(
            f"- Les conseillers ont suivi {inc['follow_rate']:.0f}% des incitations "
            f"({inc['followed']}/{inc['followed'] + inc['ignored']}). "
            + ("Propose des actions plus simples et immédiates." if inc["follow_rate"] < 50 else "")
        )

    if stats["recent_rejections"]:
        lines.append("- Raisons de rejet récentes : "
                     + " | ".join(stats["recent_rejections"][:3]))

    # SKU-specific : les 3 derniers feedbacks sur ce produit
    if sku is not None:
        conn = None
        try:
            sku_int = int(sku)
            conn = _conn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT decision, reason, created_at::date AS d
                    FROM public.agent_feedback
                    WHERE sku = %s
                    ORDER BY created_at DESC LIMIT 3
                """, (sku_int,))
                rows = cur.fetchall()
            if rows:
                hist = "; ".join(
                    f"{r['d']}: {r['decision']}" + (f" ({str(r['reason'])[:60]})" if r["reason"] else "")
                    for r in rows
                )
                lines.append(f"- Historique feedback sur ce SKU : {hist}")
        except (ValueError, TypeError):
            pass
        except Exception as e:
            logger.debug("[Feedback] sku history: %s", e)
        finally:
            if conn:
                conn.close()

    return "\n".join(lines)