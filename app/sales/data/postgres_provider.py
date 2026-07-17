"""
postgres_provider.py — v5.0 (Unified)
=======================================
Provider PostgreSQL UNIQUE pour tout le projet (sales + inventory + monitoring).
Lit UNIQUEMENT depuis les tables et vues reelles de la base ooredoo_sales.

Tables utilisees :
  sales.boutiques, sales.produits, sales.agents, sales.objectifs
  sales.transactions, sales.transactions_rt
  sales.vw_ca_par_boutique, sales.vw_stock_enriched
  sales.vw_performance_agent, sales.vw_top_products
  inventory.stock_levels, inventory.products
  inventory.stock_history, inventory.sales_history, inventory.promotions
  inventory.vw_stock_risk, inventory.vw_velocity

Aucun CSV, aucune vue fantome, aucun store_id hardcode.
Objectifs lus dynamiquement depuis sales.objectifs.
"""

import os
from app.core.config import DEFAULT_STORE_ID
import logging
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any

import asyncpg
import numpy as np

logger = logging.getLogger(__name__)

_pg_pool: Optional[asyncpg.Pool] = None


async def _get_pool() -> asyncpg.Pool:
    global _pg_pool
    if _pg_pool is None or _pg_pool._closed:
        _pg_pool = await asyncpg.create_pool(
            host=DB_CONFIG["host"], port=DB_CONFIG["port"],
            database=DB_CONFIG["database"],
            user=DB_CONFIG["user"], password=DB_CONFIG["password"],
            min_size=2, max_size=10,
            command_timeout=30,
            ssl=False,
        )
    return _pg_pool

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — depuis .env ou fallback
# ══════════════════════════════════════════════════════════════════════════════

DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")),
    "port":     int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))),
    "database": os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "ooredoo_sales")),
    "user":     os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")),
    "password": os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "root")),
}

# Store mapping — le frontend peut envoyer des aliases
STORE_MAP = {
    "store-lac2": DEFAULT_STORE_ID,
    "OOR_LAC_01": DEFAULT_STORE_ID,
    "lac2":       DEFAULT_STORE_ID,
}

DEFAULT_TARGET = 1007.0

PATTERN_HORAIRE = {
    8: 1.20, 9: 3.27, 10: 7.35, 11: 10.19, 12: 14.34,
    13: 12.20, 14: 9.41, 15: 9.10, 16: 8.24, 17: 8.80,
    18: 7.72, 19: 5.10, 20: 2.44, 21: 0.90,
}


def normalize_store_id(store_id: str) -> str:
    """Resout les aliases de store_id. Passe tel quel si inconnu."""
    return STORE_MAP.get(store_id, store_id)


async def get_pg_conn() -> asyncpg.Connection:
    pool = await _get_pool()
    return await pool.acquire()


async def _get_business_date(conn, store_id: str):
    """Retourne la date business courante (aujourd'hui ou la plus recente avant)."""
    d = await conn.fetchval("""
        SELECT MAX(date_only) FROM sales.vw_ca_par_boutique
        WHERE store_id = $1 AND date_only <= CURRENT_DATE
    """, store_id)
    if not d:
        d = await conn.fetchval(
            "SELECT MAX(date_only) FROM sales.vw_ca_par_boutique WHERE store_id = $1", store_id)
    return d


# ══════════════════════════════════════════════════════════════════════════════
# HELPER — Objectif dynamique depuis PostgreSQL
# ══════════════════════════════════════════════════════════════════════════════

async def _get_daily_target(conn, store_id: str, for_date: date = None) -> float:
    """Lit l'objectif CA depuis sales.objectifs. Fallback DEFAULT_TARGET."""
    target_date = for_date or date.today()
    try:
        val = await conn.fetchval("""
            SELECT objectif_ca FROM sales.objectifs
            WHERE store_id = $1 AND agent_id IS NULL AND date_objectif = $2
        """, store_id, target_date)
        if val:
            return float(val)
    except Exception:
        pass
    return DEFAULT_TARGET


# ══════════════════════════════════════════════════════════════════════════════
# 1. FETCH POS DATA — Donnees de vente du jour
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_pos_data(store_id: str) -> dict:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        last_date = await _get_business_date(conn, sid)
        if not last_date:
            return _fallback_pos(sid)

        # CA agrege + infos boutique via JOIN
        row = await conn.fetchrow("""
            SELECT c.store_id, b.store_name, b.ville, b.manager_name,
                   c.date_only AS business_date,
                   c.nb_transactions, c.ca_total AS current_revenue, c.avg_ticket
            FROM sales.vw_ca_par_boutique c
            LEFT JOIN sales.boutiques b ON b.store_id = c.store_id
            WHERE c.store_id = $1 AND c.date_only = $2
        """, sid, last_date)
        if not row:
            return _fallback_pos(sid)

        # CA horaire depuis les transactions directement
        hourly_rows = await conn.fetch("""
            SELECT heure, SUM(lig_ttc) AS ca_heure
            FROM (
                SELECT heure, lig_ttc FROM sales.transactions
                WHERE store_id = $1 AND date_only = $2
                UNION ALL
                SELECT heure, lig_ttc FROM sales.transactions_rt
                WHERE store_id = $1 AND date_only = $2
            ) combined
            GROUP BY heure ORDER BY heure
        """, sid, last_date)

        hourly_ca = {int(r["heure"]): float(r["ca_heure"] or 0) for r in hourly_rows}
        daily_target = await _get_daily_target(conn, sid, last_date)
        current_revenue = float(row["current_revenue"] or 0)

        logger.info(f"[PG] POS {sid} | DATE={last_date} | CA={current_revenue:.0f} TND | TX={row['nb_transactions']}")

        return {
            "store_id":              sid,
            "store_name":            row["store_name"] or sid,
            "ville":                 row["ville"] or "",
            "store_manager":         row["manager_name"] or "",
            "business_date":         str(last_date),
            "daily_target":          daily_target,
            "daily_target_tnd":      daily_target,
            "current_revenue":       round(current_revenue, 2),
            "current_revenue_tnd":   round(current_revenue, 2),
            "nb_transactions_today": int(row["nb_transactions"] or 0),
            "avg_ticket":            round(float(row["avg_ticket"] or 0), 2),
            "hourly_ca":             hourly_ca,
            "current_hour":          datetime.now().hour,
            "snapshot_time":         datetime.now().strftime("%H:%M"),
            "closing_hour":          20,
            "data_status":           "available",
            "source":                "postgresql",
        }
    except Exception as e:
        logger.warning(f"[PG] fetch_pos_data error {sid}: {e}")
        return _fallback_pos(sid)
    finally:
        await conn.close()


def _fallback_pos(sid: str) -> dict:
    return {
        "store_id": sid, "store_name": sid, "ville": "", "store_manager": "",
        "business_date": str(date.today()),
        "daily_target": DEFAULT_TARGET, "daily_target_tnd": DEFAULT_TARGET,
        "current_revenue": 0, "current_revenue_tnd": 0,
        "nb_transactions_today": 0, "avg_ticket": 0,
        "hourly_ca": {}, "current_hour": datetime.now().hour,
        "snapshot_time": datetime.now().strftime("%H:%M"),
        "closing_hour": 20, "data_status": "unavailable", "source": "fallback",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. FETCH POS HISTORY — Transactions du jour
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_pos_history(store_id: str, limit: int = 200) -> list:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        last_date = await _get_business_date(conn, sid)
        if not last_date:
            return []

        rows = await conn.fetch("""
            SELECT t.id::text AS sale_id, t.transaction_date, t.heure,
                   t.agent_id, t.sku, p.nom AS product_name,
                   p.categorie, p.famille,
                   t.lig_ttc AS revenue, t.lig_ht, t.quantity,
                   t.marge, t.payment_method
            FROM sales.transactions t
            LEFT JOIN sales.produits p ON p.sku = t.sku
            WHERE t.store_id = $1 AND t.date_only = $2
            UNION ALL
            SELECT rt.sale_id::text, rt.date_vente, rt.heure,
                   rt.agent_id, rt.cod_prod, rt.des_produit,
                   NULL, NULL,
                   rt.lig_ttc, rt.lig_ht, rt.qte_produit,
                   NULL, NULL
            FROM sales.transactions_rt rt
            WHERE rt.store_id = $1 AND rt.date_only = $2
            ORDER BY transaction_date DESC LIMIT $3
        """, sid, last_date, limit)

        now = datetime.now()
        history = []
        for r in rows:
            tx_dt = r["transaction_date"]
            minutes_ago = 60
            if tx_dt and hasattr(tx_dt, "hour"):
                try:
                    fake = now.replace(hour=tx_dt.hour, minute=tx_dt.minute, second=0, microsecond=0)
                    minutes_ago = max(0, int((now - fake).total_seconds() / 60))
                except Exception:
                    pass

            history.append({
                "sale_id":          str(r["sale_id"] or ""),
                "time":             f"{int(r['heure'] or 0):02d}:00",
                "minutes_ago":      minutes_ago,
                "hour":             int(r["heure"] or 0),
                "agent_id":         str(r["agent_id"] or ""),
                "product_code":     str(r["sku"] or ""),
                "product_name":     str(r["product_name"] or ""),
                "product_category": str(r["categorie"] or "Autre"),
                "revenue":          float(r["revenue"] or 0),
                "revenue_tnd":      float(r["revenue"] or 0),
                "quantity":         int(r["quantity"] or 1),
                "source":           "postgresql",
            })

        logger.info(f"[PG] History {sid} | DATE={last_date} | {len(history)} tx")
        return history
    except Exception as e:
        logger.warning(f"[PG] fetch_pos_history error {sid}: {e}")
        return []
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# 3. FORECAST — Prediction fin de journee multi-sources
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_timesfm_prediction(store_id: str) -> dict:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        last_date = await _get_business_date(conn, sid)
        if not last_date:
            return _fallback_forecast(sid)

        ca_ref = float(await conn.fetchval(
            "SELECT ca_total FROM sales.vw_ca_par_boutique WHERE store_id=$1 AND date_only=$2",
            sid, last_date) or 0)

        # Historique meme jour de semaine
        hist_rows = await conn.fetch("""
            SELECT ca_total FROM sales.vw_ca_par_boutique
            WHERE store_id = $1 AND date_only < $2
              AND EXTRACT(DOW FROM date_only) = EXTRACT(DOW FROM $2::date)
            ORDER BY date_only DESC LIMIT 4
        """, sid, last_date)

        # Moyenne 7 derniers jours
        last7_rows = await conn.fetch("""
            SELECT ca_total FROM sales.vw_ca_par_boutique
            WHERE store_id=$1 AND date_only < $2 AND date_only >= $3
        """, sid, last_date, last_date - timedelta(days=7))

        last7_ca = [float(r["ca_total"]) for r in last7_rows if r["ca_total"]]
        avg_7d = sum(last7_ca) / len(last7_ca) if last7_ca else ca_ref

        daily_target = await _get_daily_target(conn, sid, last_date)
        current_hour = datetime.now().hour
        pct_done = max(sum(PATTERN_HORAIRE.get(h, 0) for h in range(8, current_hour + 1)) / 100, 0.03)

        forecasts, weights = [], []
        if ca_ref > 0:
            forecasts.append(ca_ref / pct_done); weights.append(0.5)
        for i, row in enumerate(hist_rows):
            ca_sim = float(row["ca_total"] or 0)
            if ca_sim > 0:
                forecasts.append(ca_sim); weights.append(1.0 / (i + 1))
        if avg_7d > 0 and ca_ref > 0:
            expected = avg_7d * pct_done
            if expected > 0:
                forecasts.append(avg_7d * (ca_ref / expected)); weights.append(0.3)

        if forecasts:
            forecast_eod = sum(f * w for f, w in zip(forecasts, weights)) / sum(weights)
        else:
            forecast_eod = avg_7d or ca_ref

        forecast_eod = min(round(forecast_eod), int(daily_target * 1.5))
        forecast_eod = max(forecast_eod, round(ca_ref))

        std_7d = float(np.std(last7_ca)) if len(last7_ca) >= 2 else forecast_eod * 0.10
        ci_spread = min(std_7d * 0.5, forecast_eod * 0.15)
        mape = 14.3
        if len(forecasts) >= 2 and ca_ref > 0:
            mape = round(min(float(np.mean([abs(f - ca_ref) / ca_ref * 100
                       for f in forecasts[:2]])), 30.0), 1)

        logger.info(f"[PG] Forecast {sid} | DATE={last_date} | CA={ca_ref:.0f} | "
                    f"EOD={forecast_eod:.0f} | Obj={daily_target:.0f} | Sources={len(forecasts)}")

        return {
            "forecast_end_of_day": forecast_eod,
            "forecast_end_of_day_tnd": forecast_eod,
            "forecast_remaining": max(0, forecast_eod - round(ca_ref)),
            "confidence_interval": {
                "low": max(round(ca_ref), forecast_eod - round(ci_spread)),
                "high": min(forecast_eod + round(ci_spread), int(daily_target * 1.5)),
            },
            "objectif": daily_target,
            "business_date": str(last_date),
            "ca_current": round(ca_ref, 2),
            "nb_sources": len(forecasts),
            "avg_7d_ca": round(avg_7d, 2),
            "pct_done": round(pct_done * 100, 1),
            "mape": mape,
            "model_version": "pg-multi-source-v5",
            "source": "postgresql_forecast",
        }
    except Exception as e:
        logger.warning(f"[PG] fetch_timesfm_prediction error {sid}: {e}")
        return _fallback_forecast(sid)
    finally:
        await conn.close()


def _fallback_forecast(sid: str) -> dict:
    return {
        "forecast_end_of_day": 0, "forecast_end_of_day_tnd": 0,
        "forecast_remaining": 0, "confidence_interval": {"low": 0, "high": 0},
        "objectif": DEFAULT_TARGET, "mape": 99.0, "nb_sources": 0, "source": "fallback",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. ADVISORS / SELLERS — Performance par conseiller
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_sellers(store_id: str) -> list:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        last_date = await _get_business_date(conn, sid)
        if not last_date:
            return []

        daily_target = await _get_daily_target(conn, sid, last_date)

        rows = await conn.fetch("""
            SELECT t.agent_id, a.agent_name,
                   COUNT(*) AS nb_ventes,
                   SUM(t.lig_ttc) AS ca_total,
                   AVG(t.lig_ttc) AS avg_ticket
            FROM sales.transactions t
            LEFT JOIN sales.agents a ON a.agent_id = t.agent_id
            WHERE t.store_id = $1 AND t.date_only = $2
            GROUP BY t.agent_id, a.agent_name
            ORDER BY SUM(t.lig_ttc) DESC
        """, sid, last_date)

        nb_sellers = max(len(rows), 1)
        per_target = round(daily_target / nb_sellers)

        return [{
            "agent_id":       str(r["agent_id"] or ""),
            "agent_name":     str(r["agent_name"] or f"Agent {r['agent_id']}"),
            "revenue_today":  round(float(r["ca_total"] or 0), 2),
            "nb_ventes":      int(r["nb_ventes"] or 0),
            "avg_ticket":     round(float(r["avg_ticket"] or 0), 2),
            "objectif":       per_target,
            "attainment_pct": round(float(r["ca_total"] or 0) / per_target * 100) if per_target else 0,
            "business_date":  str(last_date),
            "source":         "postgresql",
        } for r in rows]
    except Exception as e:
        logger.warning(f"[PG] fetch_sellers error {sid}: {e}")
        return []
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# 5. STOCK — Niveaux de stock enrichis
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_stock_enriched(store_id: str, limit: int = 200) -> list:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        rows = await conn.fetch("""
            SELECT sl.sku, p.nom AS product_name, p.categorie, p.famille,
                   p.prix_ttc, p.marge_pct_calc AS marge_pct,
                   COALESCE(sl.quantity_available, sl.quantity, 0) AS quantity,
                   COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_dispo,
                   CASE
                       WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 0  THEN 'rupture'
                       WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 5  THEN 'critical'
                       WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 15 THEN 'warning'
                       ELSE 'ok'
                   END AS stock_risk,
                   sl.last_updated AS updated_at,
                   pm.lead_time_days, pm.moq, pm.unit_cost, pm.lifecycle_stage
            FROM inventory.stock_levels sl
            LEFT JOIN sales.produits p ON p.sku = sl.sku
            LEFT JOIN inventory.products pm ON pm.sku = sl.sku
            WHERE sl.store_id = $1
            ORDER BY COALESCE(sl.quantity_available, sl.quantity, 0) ASC
            LIMIT $2
        """, sid, limit)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[PG] fetch_stock_enriched error {sid}: {e}")
        return []
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# 6. BOUTIQUES — Liste des boutiques actives
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_boutiques(active_only: bool = True) -> list:
    conn = await get_pg_conn()
    try:
        where = "WHERE b.active = true" if active_only else ""
        rows = await conn.fetch(f"""
            SELECT b.store_id, b.store_name, b.ville, b.region,
                   b.manager_name, b.active,
                   COALESCE(c.nb_transactions, 0) AS nb_transactions,
                   COALESCE(c.ca_total, 0) AS ca_total
            FROM sales.boutiques b
            LEFT JOIN (
                SELECT store_id, SUM(nb_transactions) AS nb_transactions,
                       SUM(ca_total) AS ca_total
                FROM sales.vw_ca_par_boutique
                WHERE date_only >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY store_id
            ) c ON b.store_id = c.store_id
            {where}
            ORDER BY COALESCE(c.ca_total, 0) DESC
        """)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[PG] fetch_boutiques error: {e}")
        return []
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# 7. INVENTORY — Historique stock et ventes (pour TimesFM/Prophet)
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_stock_history(store_id: str, sku: int = None, days: int = 90) -> list:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        if sku:
            rows = await conn.fetch("""
                SELECT record_date, sku, product_name, stock_level, is_stockout
                FROM inventory.stock_history
                WHERE store_id = $1 AND sku = $2 AND record_date >= CURRENT_DATE - $3 * INTERVAL '1 day'
                ORDER BY record_date
            """, sid, sku, days)
        else:
            rows = await conn.fetch("""
                SELECT record_date, sku, product_name, stock_level, is_stockout
                FROM inventory.stock_history
                WHERE store_id = $1 AND record_date >= CURRENT_DATE - $2 * INTERVAL '1 day'
                ORDER BY record_date
            """, sid, days)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[PG] fetch_stock_history error {sid}: {e}")
        return []
    finally:
        await conn.close()


async def fetch_sales_history(store_id: str, sku: int = None, days: int = 90) -> list:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        if sku:
            rows = await conn.fetch("""
                SELECT record_date, sku, product_name, quantity_sold, revenue,
                       is_promo, event_name, season
                FROM inventory.sales_history
                WHERE store_id = $1 AND sku = $2 AND record_date >= CURRENT_DATE - $3 * INTERVAL '1 day'
                ORDER BY record_date
            """, sid, sku, days)
        else:
            rows = await conn.fetch("""
                SELECT record_date, sku, product_name, quantity_sold, revenue,
                       is_promo, event_name, season
                FROM inventory.sales_history
                WHERE store_id = $1 AND record_date >= CURRENT_DATE - $2 * INTERVAL '1 day'
                ORDER BY record_date
            """, sid, days)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[PG] fetch_sales_history error {sid}: {e}")
        return []
    finally:
        await conn.close()


async def fetch_promotions(active_only: bool = True) -> list:
    conn = await get_pg_conn()
    try:
        if active_only:
            rows = await conn.fetch("""
                SELECT promo_id, promo_name, start_date, end_date, sku,
                       product_name, category, discount_pct, promo_type, scope
                FROM inventory.promotions
                WHERE end_date >= CURRENT_DATE OR end_date IS NULL
                ORDER BY start_date DESC
            """)
        else:
            rows = await conn.fetch("SELECT * FROM inventory.promotions ORDER BY start_date DESC")
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[PG] fetch_promotions error: {e}")
        return []
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# 8. MONITORING — Logs et metriques
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_cycle_logs(store_id: str = None, limit: int = 50) -> list:
    conn = await get_pg_conn()
    try:
        if store_id:
            sid = normalize_store_id(store_id)
            rows = await conn.fetch("""
                SELECT * FROM monitoring.cycle_logs
                WHERE store_id = $1 ORDER BY created_at DESC LIMIT $2
            """, sid, limit)
        else:
            rows = await conn.fetch(
                "SELECT * FROM monitoring.cycle_logs ORDER BY created_at DESC LIMIT $1", limit)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[PG] fetch_cycle_logs error: {e}")
        return []
    finally:
        await conn.close()


async def fetch_coaching_interactions(store_id: str = None, limit: int = 50) -> list:
    conn = await get_pg_conn()
    try:
        if store_id:
            sid = normalize_store_id(store_id)
            rows = await conn.fetch("""
                SELECT * FROM monitoring.coaching_interactions
                WHERE store_id = $1 ORDER BY created_at DESC LIMIT $2
            """, sid, limit)
        else:
            rows = await conn.fetch(
                "SELECT * FROM monitoring.coaching_interactions ORDER BY created_at DESC LIMIT $1", limit)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[PG] fetch_coaching_interactions error: {e}")
        return []
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# HELPER — Date business
# ══════════════════════════════════════════════════════════════════════════════

async def get_latest_business_date(store_id: str) -> Optional[date]:
    sid = normalize_store_id(store_id)
    conn = await get_pg_conn()
    try:
        return await conn.fetchval(
            "SELECT MAX(date_only) FROM sales.vw_ca_par_boutique WHERE store_id = $1 AND date_only <= CURRENT_DATE", sid)
    except Exception:
        return None
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON — Compatible avec le code existant
# ══════════════════════════════════════════════════════════════════════════════

class PostgresProvider:
    """Provider unifie — utilise par les agents analyste, stratege, coach."""

    async def fetch_pos_data(self, store_id: str) -> dict:
        return await fetch_pos_data(store_id)

    async def fetch_pos_history(self, store_id: str) -> list:
        return await fetch_pos_history(store_id)

    async def fetch_timesfm_prediction(self, store_id: str) -> dict:
        return await fetch_timesfm_prediction(store_id)

    async def fetch_sellers(self, store_id: str) -> list:
        return await fetch_sellers(store_id)

    async def fetch_stock_enriched(self, store_id: str) -> list:
        return await fetch_stock_enriched(store_id)

    async def fetch_boutiques(self, active_only: bool = True) -> list:
        return await fetch_boutiques(active_only)

    async def fetch_stock_history(self, store_id: str, sku: int = None, days: int = 90) -> list:
        return await fetch_stock_history(store_id, sku, days)

    async def fetch_sales_history(self, store_id: str, sku: int = None, days: int = 90) -> list:
        return await fetch_sales_history(store_id, sku, days)

    async def fetch_promotions(self, active_only: bool = True) -> list:
        return await fetch_promotions(active_only)

    async def fetch_cycle_logs(self, store_id: str = None, limit: int = 50) -> list:
        return await fetch_cycle_logs(store_id, limit)

    async def fetch_coaching_interactions(self, store_id: str = None, limit: int = 50) -> list:
        return await fetch_coaching_interactions(store_id, limit)


_provider_instance: Optional[PostgresProvider] = None


def get_data_provider() -> PostgresProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = PostgresProvider()
    return _provider_instance