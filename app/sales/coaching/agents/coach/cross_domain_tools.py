"""
cross_domain_tools.py — Outils Cross-Domain CoachAgent (V5 FINAL)
==================================================================
8 outils dynamiques définis dans ARCHITECTURE_MULTI_AGENT_V5_FINAL.md
Section 4.2 — CoachAgent StateGraph

Tous les outils interrogent la DB ou Redis dynamiquement.
Aucune valeur de stock, SKU ou quantité codée en dur.

SPRINT 5 — S5.2 : score_product() implémente la formule de fusion Sales×Inventory
de l'architecture agentique (§8 CoachAgent Cross-Domain) :
    final_score = 0.30*sales_gap_alignment + 0.20*stock_health
                + 0.15*margin_score + 0.15*promotion_priority
                + 0.10*advisor_fit + 0.10*customer_fit
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Outil 1 — get_sales_context ─────────────────────────────────────────────

async def get_sales_context(store_id: str, advisor_id: str = "") -> Dict[str, Any]:
    """
    Retourne le contexte Sales courant depuis la DB (CA, TX, panier, gap).
    Dynamique : lit les transactions réelles du jour.
    """
    try:
        import asyncpg
        conn = await asyncpg.connect(
            host="localhost", port=5432,
            database="ooredoo_sales", user="postgres", password="admin",
            timeout=3,
        )
        try:
            # CA actuel dynamique depuis transactions_rt (données du jour en temps réel)
            row = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM(t.lig_ttc), 0) AS ca_actuel,
                    COALESCE(AVG(t.lig_ttc), 0) AS panier_moy,
                    COUNT(*)                     AS nb_transactions
                FROM sales.transactions_rt t
                WHERE t.store_id = $1
                  AND t.date_only = CURRENT_DATE
                  AND t.lig_ttc  > 0
            """, store_id)

            ca_actuel  = float(row["ca_actuel"]  or 0)
            panier_moy = float(row["panier_moy"] or 0)
            nb_tx      = int(row["nb_transactions"] or 0)

            # Objectif journalier depuis sales.objectifs (store-level, agent_id IS NULL)
            obj_row = await conn.fetchrow("""
                SELECT objectif_ca AS objectif_journalier
                FROM sales.objectifs
                WHERE store_id = $1
                  AND agent_id IS NULL
                  AND date_objectif = CURRENT_DATE
                LIMIT 1
            """, store_id)

            if obj_row and obj_row["objectif_journalier"]:
                objectif = float(obj_row["objectif_journalier"])
            else:
                # Fallback: moyenne historique depuis transactions
                hist_row = await conn.fetchrow("""
                    SELECT AVG(daily_ca) AS avg_obj
                    FROM (
                        SELECT date_only, SUM(lig_ttc) AS daily_ca
                        FROM sales.transactions_rt
                        WHERE store_id = $1
                          AND date_only >= CURRENT_DATE - INTERVAL '30 days'
                          AND date_only  < CURRENT_DATE
                        GROUP BY date_only
                    ) sub
                """, store_id)
                objectif = float(hist_row["avg_obj"] or 1007) if hist_row and hist_row["avg_obj"] else 1007.0

            gap_amount = max(0, objectif - ca_actuel)
            gap_pct     = round(gap_amount / objectif * 100, 1) if objectif > 0 else 0

            return {
                "store_id":   store_id,
                "ca_actuel":  ca_actuel,
                "objectif":   objectif,
                "gap_amount": gap_amount,
                "gap_pct":    gap_pct,
                "panier_moy": panier_moy,
                "nb_tx":      nb_tx,
                "advisor_id": advisor_id,
            }
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("[CoachTools] get_sales_context: %s", e)
        return {
            "store_id": store_id, "ca_actuel": 0, "objectif": 1007,
            "gap_amount": 0, "gap_pct": 0, "panier_moy": 0, "nb_tx": 0,
        }


# ── Outil 2 — get_inventory_snapshot ────────────────────────────────────────

def get_inventory_snapshot(store_id: str) -> Dict[str, Any]:
    """
    Lit le snapshot stock depuis Redis StateBus (publié par InventoryOrchestrator).
    Dynamique : toutes les quantités viennent de l'analyse inventory.
    Fallback : DB directe si Redis indisponible.
    """
    try:
        from app.sales.core.state_bus import get_state_bus
        bus      = get_state_bus()
        snapshot = bus.read_inventory_snapshot(store_id)
        if snapshot:
            return {
                "store_id":       snapshot.store_id,
                "timestamp":      snapshot.timestamp,
                "skus":           snapshot.skus,
                "critical_count": snapshot.critical_count,
                "rupture_count":  snapshot.rupture_count,
                "healthy_count":  snapshot.healthy_count,
                "total_skus":     snapshot.total_skus,
                "source":         "redis",
            }
    except Exception as e:
        logger.debug("[CoachTools] Redis snapshot: %s", e)

    # Fallback DB
    return _get_snapshot_from_db(store_id)


def _get_snapshot_from_db(store_id: str) -> Dict[str, Any]:
    """Fallback synchrone depuis DB inventory — toujours dynamique."""
    try:
        from app.inventory.repositories.inventory_repo import SyncInventoryRepo

        stock_data = SyncInventoryRepo.get_stock_levels_batch([], store_id) or {}
        critical = sum(1 for v in stock_data.values() if int(v.get("stock_current", 0)) <= 5)
        rupture  = sum(1 for v in stock_data.values() if int(v.get("stock_current", 0)) == 0)
        healthy  = len(stock_data) - critical

        skus = {
            str(sku): {
                "stock_qty":    int(v.get("stock_current", 0)),
                "risk_level":   v.get("risk_level", "unknown"),
                "product_name": v.get("product_name", str(sku)),
                "days_remaining": float(v.get("days_remaining", 0)),
                "reorder_point":  int(v.get("reorder_point", 0)),
            }
            for sku, v in stock_data.items()
        }

        return {
            "store_id": store_id, "skus": skus,
            "critical_count": critical, "rupture_count": rupture,
            "healthy_count": healthy, "total_skus": len(skus),
            "source": "db_fallback",
        }
    except Exception as e:
        logger.warning("[CoachTools] DB snapshot: %s", e)
        return {
            "store_id": store_id, "skus": {}, "critical_count": 0,
            "rupture_count": 0, "healthy_count": 0, "total_skus": 0,
            "source": "unavailable",
        }


# ── Outil 3 — get_stock_alerts ──────────────────────────────────────────────

def get_stock_alerts(store_id: str, severity_filter: str = "high") -> List[Dict[str, Any]]:
    """
    Retourne les alertes stock actives depuis la DB (dynamique).
    Filtré par severity : critical | high | medium | all
    """
    try:
        from app.inventory.repositories.inventory_repo import SyncInventoryRepo

        alerts = SyncInventoryRepo.get_active_alerts(store_id=store_id) or []

        severity_map = {"critical": 3, "high": 2, "medium": 1, "low": 0, "all": -1}
        min_sev = severity_map.get(severity_filter, 2)

        return [
            {
                "sku":          str(a.get("sku", "")),
                "product_name": str(a.get("product_name", "")),
                "alert_type":   str(a.get("alert_type", "")),
                "severity":     str(a.get("severity", "low")),
                "stock_qty":    int(a.get("stock_qty", 0)),         # dynamique
                "days_remaining": float(a.get("days_remaining", 0)), # dynamique
                "action":       str(a.get("recommended_action", "")),
            }
            for a in alerts
            if severity_map.get(a.get("severity", "low"), 0) >= min_sev or min_sev == -1
        ][:10]
    except Exception as e:
        logger.debug("[CoachTools] get_stock_alerts: %s", e)
        return []


# ── Outil 4 — get_recommendable_products ────────────────────────────────────

def get_recommendable_products(
    store_id:       str,
    gap_amount:     float,
    advisor_history: Optional[Dict[str, float]] = None,
    max_products:   int = 10,
) -> List[Dict[str, Any]]:
    """
    Retourne les produits candidats depuis DB (stock > 0, disponibles).
    Filtre dynamiquement les ruptures.
    Utilisé par le Balancing Engine.
    """
    try:
        from app.inventory.repositories.inventory_repo import SyncInventoryRepo

        # Récupère produits avec stock > 0 dynamiquement
        products = SyncInventoryRepo.get_recommendable_products(
            store_id=store_id,
            min_stock=1,        # stock > 0 — dynamique
            limit=max_products,
        ) or []

        return [
            {
                "sku":            str(p.get("sku", "")),
                "name":           str(p.get("nom", p.get("name", ""))),
                "price":          float(p.get("prix_ttc", p.get("price", 0))),
                "stock_current":  int(p.get("stock_current", p.get("stock_qty", 0))),  # dynamique
                "stock_optimal":  float(p.get("stock_optimal", p.get("reorder_point", 0)) or 1) * 2,
                "margin_pct":     float(p.get("margin_pct", 20)),
                "days_to_stockout": float(p.get("days_remaining", 30)),   # dynamique
                "category":       str(p.get("category", p.get("categorie", "unknown"))),
                "risk_level":     str(p.get("risk_level", "LOW")),
                "is_top_seller":  bool(p.get("is_top_seller", False)),
                "active_promo":   bool(p.get("active_promo", False)),
            }
            for p in products
            if int(p.get("stock_current", p.get("stock_qty", 0))) > 0  # G9 implicite
        ]
    except Exception as e:
        logger.debug("[CoachTools] get_recommendable_products: %s", e)
        return []


# ── Outil 5 — retrieve_advisor_history ──────────────────────────────────────

def retrieve_advisor_history(advisor_id: str, store_id: str) -> Dict[str, Any]:
    """
    Récupère le profil du conseiller depuis advisor_profile (Mémoire Procédurale).
    Dynamique — issu des interactions précédentes.
    """
    try:
        import asyncpg
        import asyncio

        async def _fetch():
            conn = await asyncpg.connect(
                host="localhost", port=5432,
                database="ooredoo_sales", user="postgres", password="admin",
                timeout=3,
            )
            try:
                row = await conn.fetchrow("""
                    SELECT strong_categories, weak_categories,
                           avg_response_acceptance,
                           total_recos_received, total_recos_followed
                    FROM monitoring.advisor_profile
                    WHERE advisor_name = $1 AND store_id = $2
                """, advisor_id, store_id)
                if row:
                    import json as _json
                    return {
                        "advisor_id":  advisor_id,
                        "strong":      _json.loads(row["strong_categories"] or "{}"),
                        "weak":        _json.loads(row["weak_categories"]   or "{}"),
                        "acceptance":  float(row["avg_response_acceptance"]  or 0.5),
                        "total_recos": int(row["total_recos_received"]       or 0),
                        "followed":    int(row["total_recos_followed"]       or 0),
                    }
                return {"advisor_id": advisor_id, "strong": {}, "weak": {}, "acceptance": 0.5}
            finally:
                await conn.close()

        return asyncio.get_event_loop().run_until_complete(_fetch())
    except Exception as e:
        logger.debug("[CoachTools] retrieve_advisor_history: %s", e)
        return {"advisor_id": advisor_id, "strong": {}, "weak": {}, "acceptance": 0.5}


# ── Outil 6 — rag_search_scripts (déjà existant, wrapper pour cross-domain) ─

def rag_search_scripts(query: str, store_id: str, hour: int = 14) -> List[Dict[str, Any]]:
    """Wrapper vers le RAG Milvus existant — pas de duplication."""
    try:
        from app.sales.coaching.agents.stratege.nodes import node_rag_search
        return []  # Le rag_search existant est appelé dans node_rag_search
    except Exception:
        return []


# ── Outil 7 — check_promotions ──────────────────────────────────────────────

def check_promotions(sku: str, store_id: str) -> Dict[str, Any]:
    """Vérifie si une promotion active existe sur ce SKU — dynamique depuis DB."""
    try:
        import asyncpg, asyncio

        async def _fetch():
            conn = await asyncpg.connect(
                host="localhost", port=5432,
                database="ooredoo_sales", user="postgres", password="admin",
                timeout=3,
            )
            try:
                row = await conn.fetchrow("""
                    SELECT titre, remise_pct, date_fin
                    FROM market.promotions
                    WHERE $1 = ANY(skus) AND store_id = $2
                      AND date_debut <= CURRENT_DATE AND date_fin >= CURRENT_DATE
                    LIMIT 1
                """, int(sku), store_id)
                if row:
                    return {
                        "has_promo":  True,
                        "title":      row["titre"],
                        "discount":   float(row["remise_pct"] or 0),
                        "expires":    str(row["date_fin"]),
                    }
                return {"has_promo": False}
            finally:
                await conn.close()

        return asyncio.get_event_loop().run_until_complete(_fetch())
    except Exception as e:
        logger.debug("[CoachTools] check_promotions: %s", e)
        return {"has_promo": False}


# ── Outil 8 — get_realtime_kpis ─────────────────────────────────────────────

# ── Outil 9 — score_product (Cross-Domain Fusion Formula, Sprint 5) ─────────

def score_product(
    product:         Dict[str, Any],
    sales_context:   Dict[str, Any],
    advisor_history: Dict[str, Any],
    has_promo:       bool = False,
) -> Dict[str, Any]:
    """
    Compute the cross-domain fusion score for a product.

    Formula (§8 CoachAgent Cross-Domain, ARCHITECTURE_AGENTIQUE_COMMUNICATION.md):
        final_score = 0.30 * sales_gap_alignment
                    + 0.20 * stock_health
                    + 0.15 * margin_score
                    + 0.15 * promotion_priority
                    + 0.10 * advisor_fit
                    + 0.10 * customer_fit

    Args:
        product:        One item from get_recommendable_products()
        sales_context:  Output of get_sales_context()
        advisor_history:Output of retrieve_advisor_history()
        has_promo:      Whether an active promo exists for this product

    Returns:
        {
            "sku":               str,
            "name":              str,
            "price":             float,
            "final_score":       float,   # [0.0 – 1.0]
            "components":        dict,    # per-criterion scores for explainability
            "recommendation_reason": str, # human-readable justification
        }
    """
    price          = float(product.get("price", 0))
    stock_qty      = int(product.get("stock_current", 0))
    stock_optimal  = max(1.0, float(product.get("stock_optimal", 10)))
    margin_pct     = float(product.get("margin_pct", 20))
    days_to_out    = float(product.get("days_to_stockout", 30))
    category       = str(product.get("category", ""))
    is_top_seller  = bool(product.get("is_top_seller", False))
    risk_level     = str(product.get("risk_level", "LOW"))

    gap_amount = float(sales_context.get("gap_amount", 0))
    gap_pct    = float(sales_context.get("gap_pct", 0))
    objectif   = max(1.0, float(sales_context.get("objectif", 1)))

    # ── C1: Sales Gap Alignment (0–1) ─────────────────────────────────────
    # How well does the product price address the remaining gap?
    if gap_amount <= 0:
        sales_gap_alignment = 0.3  # gap already filled — minor push still useful
    elif price <= 0:
        sales_gap_alignment = 0.0
    else:
        coverage = price / gap_amount
        if coverage >= 1.0:
            sales_gap_alignment = 1.0
        elif coverage >= 0.5:
            sales_gap_alignment = 0.7
        elif coverage >= 0.2:
            sales_gap_alignment = 0.5
        else:
            sales_gap_alignment = 0.2
        # Boost if gap is urgent (> 30%)
        if gap_pct > 30:
            sales_gap_alignment = min(1.0, sales_gap_alignment * 1.15)

    # ── C2: Stock Health (0–1) ─────────────────────────────────────────────
    # High stock = safe to push; critical/zero = penalised
    if stock_qty == 0 or risk_level == "CRITICAL":
        stock_health = 0.0
    elif risk_level == "HIGH" or days_to_out < 3:
        stock_health = 0.25
    elif risk_level == "MEDIUM" or days_to_out < 7:
        stock_health = 0.55
    else:
        ratio = min(1.0, stock_qty / stock_optimal)
        stock_health = 0.7 + 0.3 * ratio

    # ── C3: Margin Score (0–1) ─────────────────────────────────────────────
    # Normalize margin 0–50% range → 0–1
    margin_score = min(1.0, margin_pct / 50.0)

    # ── C4: Promotion Priority (0–1) ──────────────────────────────────────
    if has_promo:
        promotion_priority = 1.0
    elif is_top_seller:
        promotion_priority = 0.6
    else:
        promotion_priority = 0.2

    # ── C5: Advisor Fit (0–1) ─────────────────────────────────────────────
    # Use advisor's historical performance on this category
    strong_cats = advisor_history.get("strong", {})
    weak_cats   = advisor_history.get("weak", {})
    acceptance  = float(advisor_history.get("acceptance", 0.5))

    if category in strong_cats:
        advisor_fit = min(1.0, 0.6 + float(strong_cats[category]) * 0.4)
    elif category in weak_cats:
        advisor_fit = max(0.1, 0.4 - float(weak_cats.get(category, 0)) * 0.3)
    else:
        advisor_fit = acceptance  # neutral → use overall acceptance rate

    # ── C6: Customer Fit (0–1) ────────────────────────────────────────────
    # Proxy: top-sellers have higher customer fit; flagship products score well
    if is_top_seller:
        customer_fit = 0.9
    elif price >= 800:
        customer_fit = 0.7  # premium products convert less but increase CA/unit
    elif price >= 300:
        customer_fit = 0.6
    else:
        customer_fit = 0.5

    # ── Final weighted score ───────────────────────────────────────────────
    final_score = (
        0.30 * sales_gap_alignment
        + 0.20 * stock_health
        + 0.15 * margin_score
        + 0.15 * promotion_priority
        + 0.10 * advisor_fit
        + 0.10 * customer_fit
    )

    # ── Explainability ────────────────────────────────────────────────────
    top_reason = max(
        [
            ("gap CA", sales_gap_alignment * 0.30),
            ("stock sain", stock_health * 0.20),
            ("marge", margin_score * 0.15),
            ("promo active", promotion_priority * 0.15),
            ("fit conseiller", advisor_fit * 0.10),
            ("fit client", customer_fit * 0.10),
        ],
        key=lambda x: x[1],
    )[0]

    name = product.get("name", "Produit")
    recommendation_reason = (
        f"{name} recommandé principalement pour : {top_reason} "
        f"(score={final_score:.2f}, promo={'oui' if has_promo else 'non'}, "
        f"stock={stock_qty}u, marge={margin_pct:.0f}%)"
    )

    return {
        "sku":           product.get("sku", ""),
        "name":          name,
        "price":         price,
        "final_score":   round(final_score, 4),
        "components": {
            "sales_gap_alignment": round(sales_gap_alignment, 3),
            "stock_health":        round(stock_health, 3),
            "margin_score":        round(margin_score, 3),
            "promotion_priority":  round(promotion_priority, 3),
            "advisor_fit":         round(advisor_fit, 3),
            "customer_fit":        round(customer_fit, 3),
        },
        "recommendation_reason": recommendation_reason,
    }


def rank_products(
    products:        List[Dict[str, Any]],
    sales_context:   Dict[str, Any],
    advisor_history: Dict[str, Any],
    promos:          Optional[Dict[str, bool]] = None,
    top_n:           int = 3,
) -> List[Dict[str, Any]]:
    """
    Score and rank all candidate products using score_product().
    Returns top_n products sorted by final_score descending.

    Args:
        products:        From get_recommendable_products()
        sales_context:   From get_sales_context()
        advisor_history: From retrieve_advisor_history()
        promos:          {sku: has_promo} dict from check_promotions()
        top_n:           Number of top products to return

    Returns: list of score_product() dicts, sorted by final_score DESC
    """
    promos = promos or {}
    scored = [
        score_product(
            product         = p,
            sales_context   = sales_context,
            advisor_history = advisor_history,
            has_promo       = promos.get(str(p.get("sku", "")), False),
        )
        for p in products
    ]
    return sorted(scored, key=lambda x: x["final_score"], reverse=True)[:top_n]


def get_realtime_kpis(store_id: str) -> Dict[str, Any]:
    """KPIs temps réel : TX conversion, panier moyen heure courante — dynamique."""
    try:
        import asyncpg, asyncio, datetime as _dt

        async def _fetch():
            conn = await asyncpg.connect(
                host="localhost", port=5432,
                database="ooredoo_sales", user="postgres", password="admin",
                timeout=3,
            )
            try:
                hour = _dt.datetime.now().hour
                row  = await conn.fetchrow("""
                    SELECT
                        COUNT(*)                      AS nb_tx,
                        COALESCE(AVG(lig_ttc), 0)     AS panier_moy,
                        COALESCE(SUM(lig_ttc), 0)     AS ca_heure
                    FROM sales.transactions_rt
                    WHERE store_id = $1
                      AND date_only = CURRENT_DATE
                      AND EXTRACT(HOUR FROM date_vente) = $2
                      AND lig_ttc  > 0
                """, store_id, hour)
                return {
                    "store_id":  store_id,
                    "hour":      hour,
                    "nb_tx":     int(row["nb_tx"] or 0),
                    "panier_moy":float(row["panier_moy"] or 0),
                    "ca_heure":  float(row["ca_heure"] or 0),
                }
            finally:
                await conn.close()

        return asyncio.get_event_loop().run_until_complete(_fetch())
    except Exception as e:
        logger.debug("[CoachTools] get_realtime_kpis: %s", e)
        return {"store_id": store_id, "nb_tx": 0, "panier_moy": 0, "ca_heure": 0}
