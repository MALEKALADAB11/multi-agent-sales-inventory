"""
seed_product_associations.py
============================
Computes product association rules from sales_history using temporal correlation.
Analyzes which products sell together on the same day at the same store.

Run: python db/seeds/seed_product_associations.py
Re-run safe: existing rows deleted first.
"""
import asyncio
import logging
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _seed_common import get_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def _pg_conn():
    """Open a direct psycopg2 connection for synchronous queries."""
    import os
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")),
        port=int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))),
        dbname=os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "ooredoo_sales")),
        user=os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "root")),
        connect_timeout=10,
    )


async def compute_product_associations(min_cooccurrence: int = 5):
    """
    Computes product associations from sales_history.
    
    For each product pair (sku1, sku2) that sells together on same day/store:
    - support: P(sku1 AND sku2) = co_occurrence / total_days
    - confidence: P(sku2 | sku1) = co_occurrence / days_sku1_sold
    - lift: confidence / P(sku2) = how much more likely than random
    """
    logger.info("Computing product associations from sales_history...")
    
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            # Get total number of (date, store) pairs with any sales
            cur.execute("""
                SELECT COUNT(DISTINCT (record_date, store_id))
                FROM inventory.sales_history
                WHERE quantity_sold > 0
            """)
            total_days = cur.fetchone()[0]
            logger.info(f"Total (date, store) pairs with sales: {total_days:,}")
            
            # Compute product-level associations
            cur.execute("""
                WITH daily_sales AS (
                    SELECT 
                        record_date,
                        store_id,
                        sku
                    FROM inventory.sales_history
                    WHERE quantity_sold > 0
                ),
                sku_daily_counts AS (
                    SELECT 
                        sku,
                        COUNT(DISTINCT (record_date, store_id)) as days_sold
                    FROM daily_sales
                    GROUP BY sku
                ),
                cooccurrence AS (
                    SELECT 
                        d1.sku as sku1,
                        d2.sku as sku2,
                        COUNT(DISTINCT (d1.record_date, d1.store_id)) as co_occurrence_days
                    FROM daily_sales d1
                    JOIN daily_sales d2 
                        ON d1.record_date = d2.record_date 
                        AND d1.store_id = d2.store_id
                        AND d1.sku < d2.sku
                    GROUP BY d1.sku, d2.sku
                    HAVING COUNT(DISTINCT (d1.record_date, d1.store_id)) >= %s
                )
                SELECT 
                    c.sku1,
                    c.sku2,
                    p1.gamme_libelle as gamme1,
                    p2.gamme_libelle as gamme2,
                    c.co_occurrence_days,
                    s1.days_sold as sku1_days,
                    s2.days_sold as sku2_days,
                    ROUND(c.co_occurrence_days::numeric / %s, 3) as support,
                    ROUND(c.co_occurrence_days::numeric / s1.days_sold, 3) as confidence,
                    ROUND(
                        (c.co_occurrence_days::numeric / s1.days_sold) / 
                        (s2.days_sold::numeric / %s), 3
                    ) as lift
                FROM cooccurrence c
                JOIN sku_daily_counts s1 ON c.sku1 = s1.sku
                JOIN sku_daily_counts s2 ON c.sku2 = s2.sku
                JOIN sales.produits p1 ON c.sku1 = p1.sku
                JOIN sales.produits p2 ON c.sku2 = p2.sku
                WHERE c.co_occurrence_days >= %s
                  AND s1.days_sold > 0
                  AND s2.days_sold > 0
                ORDER BY c.co_occurrence_days DESC
            """, (min_cooccurrence, total_days, total_days, min_cooccurrence))
            
            rows = cur.fetchall()
            logger.info(f"Found {len(rows):,} product associations with >= {min_cooccurrence} co-occurrences")
            
            return rows
    finally:
        conn.close()


async def compute_gamme_associations():
    """
    Computes gamme-level associations (higher level, more general).
    Useful fallback when product-level data is sparse.
    """
    logger.info("Computing gamme-level associations...")
    
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                WITH daily_gamme_sales AS (
                    SELECT 
                        record_date,
                        store_id,
                        gamme_libelle
                    FROM inventory.sales_history sh
                    JOIN sales.produits p ON sh.sku = p.sku
                    WHERE sh.quantity_sold > 0
                ),
                gamme_daily_counts AS (
                    SELECT 
                        gamme_libelle,
                        COUNT(DISTINCT (record_date, store_id)) as days_sold
                    FROM daily_gamme_sales
                    GROUP BY gamme_libelle
                ),
                cooccurrence AS (
                    SELECT 
                        d1.gamme_libelle as gamme1,
                        d2.gamme_libelle as gamme2,
                        COUNT(DISTINCT (d1.record_date, d1.store_id)) as co_occurrence_days
                    FROM daily_gamme_sales d1
                    JOIN daily_gamme_sales d2 
                        ON d1.record_date = d2.record_date 
                        AND d1.store_id = d2.store_id
                        AND d1.gamme_libelle < d2.gamme_libelle
                    GROUP BY d1.gamme_libelle, d2.gamme_libelle
                )
                SELECT 
                    c.gamme1,
                    c.gamme2,
                    c.co_occurrence_days,
                    g1.days_sold as gamme1_days,
                    g2.days_sold as gamme2_days,
                    ROUND(
                        c.co_occurrence_days::numeric / 
                        (SELECT COUNT(DISTINCT (record_date, store_id)) FROM daily_gamme_sales), 
                        3
                    ) as support,
                    ROUND(c.co_occurrence_days::numeric / g1.days_sold, 3) as confidence,
                    ROUND(
                        (c.co_occurrence_days::numeric / g1.days_sold) / 
                        (g2.days_sold::numeric / (SELECT COUNT(DISTINCT (record_date, store_id)) FROM daily_gamme_sales)), 
                        3
                    ) as lift
                FROM cooccurrence c
                JOIN gamme_daily_counts g1 ON c.gamme1 = g1.gamme_libelle
                JOIN gamme_daily_counts g2 ON c.gamme2 = g2.gamme_libelle
                WHERE c.co_occurrence_days >= 10
                ORDER BY c.co_occurrence_days DESC
            """)
            
            rows = cur.fetchall()
            logger.info(f"Found {len(rows):,} gamme-level associations")
            
            return rows
    finally:
        conn.close()


async def main():
    # Compute product-level associations
    product_rows = await compute_product_associations(min_cooccurrence=5)
    
    # Compute gamme-level associations
    gamme_rows = await compute_gamme_associations()
    
    # Insert into database
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Create table if it doesn't exist
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory.product_associations (
                id SERIAL PRIMARY KEY,
                sku1 integer,
                sku2 integer,
                gamme1 text NOT NULL,
                gamme2 text NOT NULL,
                co_occurrence_days integer NOT NULL,
                confidence numeric(10,6) NOT NULL,
                lift numeric(12,6),
                support numeric(10,6) NOT NULL,
                last_updated timestamp NOT NULL DEFAULT now(),
                store_id text,
                CONSTRAINT uq_product_assoc UNIQUE (sku1, sku2, store_id)
            );
        """)

        # Relax NOT NULL on sku1/sku2 in case the table already existed from
        # an earlier run, before gamme-level (sku1=sku2=NULL) rules existed.
        await conn.execute("ALTER TABLE inventory.product_associations ALTER COLUMN sku1 DROP NOT NULL")
        await conn.execute("ALTER TABLE inventory.product_associations ALTER COLUMN sku2 DROP NOT NULL")
        
        # Clear existing data
        await conn.execute("DELETE FROM inventory.product_associations")
        logger.info("Cleared existing product associations")
        
        # Insert product-level associations (store_id = NULL for global rules)
        # row layout: sku1, sku2, gamme1, gamme2, co_occurrence_days,
        #             sku1_days, sku2_days, support, confidence, lift
        for row in product_rows:
            await conn.execute("""
                INSERT INTO inventory.product_associations 
                (sku1, sku2, gamme1, gamme2, co_occurrence_days, confidence, lift, support, store_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NULL)
            """, row[0], row[1], row[2], row[3], row[4], row[8], row[9], row[7])
        
        logger.info(f"Inserted {len(product_rows):,} product-level associations")
        
        # Insert gamme-level associations as pseudo-rules (sku1 = sku2 = NULL)
        # row layout: gamme1, gamme2, co_occurrence_days, gamme1_days,
        #             gamme2_days, support, confidence, lift
        for row in gamme_rows:
            await conn.execute("""
                INSERT INTO inventory.product_associations 
                (sku1, sku2, gamme1, gamme2, co_occurrence_days, confidence, lift, support, store_id)
                VALUES (NULL, NULL, $1, $2, $3, $4, $5, $6, NULL)
            """, row[0], row[1], row[2], row[6], row[7], row[5])
        
        logger.info(f"Inserted {len(gamme_rows):,} gamme-level associations")
    
    await pool.close()
    logger.info("Done: product associations seeded successfully")


if __name__ == "__main__":
    asyncio.run(main())