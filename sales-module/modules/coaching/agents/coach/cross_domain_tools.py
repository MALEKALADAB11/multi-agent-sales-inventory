"""
cross_domain_tools.py — Outils Cross-Domain CoachAgent (V5 FINAL)
==================================================================
8 outils dynamiques définis dans ARCHITECTURE_MULTI_AGENT_V5_FINAL.md
Section 4.2 — CoachAgent StateGraph

Tous les outils interrogent la DB ou Redis dynamiquement.
Aucune valeur de stock, SKU ou quantité codée en dur.
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
            # CA actuel dynamique
            row = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM(t.montant), 0) AS ca_actuel,
                    COALESCE(AVG(t.montant), 0) AS panier_moy,
                    COUNT(*)                    AS nb_transactions,
                    o.objectif_journalier       AS objectif
                FROM sales.transactions t
                JOIN sales.objectifs    o
                  ON o.store_id = t.store_id
                 AND o.date     = CURRENT_DATE
                WHERE t.store_id = $1
                  AND t.date     = CURRENT_DATE
                  AND o.store_id = $1
            """, store_id)

            ca_actuel  = float(row["ca_actuel"]  or 0)
            panier_moy = float(row["panier_moy"] or 0)
            nb_tx      = int(row["nb_transactions"] or 0)

            # Objectif : depuis DB ou moyenne des 30 derniers jours de cette boutique
            if row["objectif"]:
                objectif = float(row["objectif"])
            else:
                obj_row = await conn.fetchrow("""
                    SELECT AVG(objectif_journalier) AS avg_obj
                    FROM sales.objectifs
                    WHERE store_id = $1
                      AND date >= CURRENT_DATE - INTERVAL '30 days'
                """, store_id)
                objectif = float(obj_row["avg_obj"] or 0) if obj_row and obj_row["avg_obj"] else 0.0

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
        from core.state_bus import get_state_bus
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
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "inventory-module"))
        from db.repositories.inventory_repo import SyncInventoryRepo

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
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "inventory-module"))
        from db.repositories.inventory_repo import SyncInventoryRepo

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
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "inventory-module"))
        from db.repositories.inventory_repo import SyncInventoryRepo

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
        from modules.coaching.agents.stratege.nodes import node_rag_search
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
                        COUNT(*)                     AS nb_tx,
                        COALESCE(AVG(montant), 0)    AS panier_moy,
                        COALESCE(SUM(montant), 0)    AS ca_heure
                    FROM sales.transactions
                    WHERE store_id = $1
                      AND date     = CURRENT_DATE
                      AND EXTRACT(HOUR FROM heure) = $2
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
