"""
Seed du catalogue de sourcing supply.supplier_products.

Source : UNIQUEMENT Postgres (politique zéro-CSV) — croisement de
supply.suppliers (categories/marques jsonb) × sales.produits (marque, flags,
pa_ht, moq, lead_time). Idempotent : ON CONFLICT DO NOTHING + recalcul
is_preferred.

Règles de matching (catégorie d'appro dérivée des flags produit) :
  flag_sim → SIM ; flag_recharge → RECHARGE ; flag_terminal → TERMINAL ;
  sinon ACCESSOIRE. Produits stockables uniquement.

Rang de préférence par SKU :
  1. CONSTRUCTEUR dont marques contient la marque du produit
  2. DISTRIBUTEUR_NATIONAL dont marques contient la marque
  3. Fournisseur spécialisé de la catégorie (ex. GEMALTO_SIM pour SIM)
  9. OOREDOO_CENTRAL (marques=ALL) — fallback universel
is_preferred = meilleur rang (égalité départagée par délai moyen croissant).

Usage : python db/seeds/seed_supplier_products.py
"""

import os
import sys

import psycopg2
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv()

_SQL = """
WITH produits_stockables AS (
    SELECT sku, marque, pa_ht, moq, lead_time_days,
           CASE
               WHEN flag_sim THEN 'SIM'
               WHEN flag_recharge THEN 'RECHARGE'
               WHEN flag_terminal THEN 'TERMINAL'
               ELSE 'ACCESSOIRE'
           END AS supply_cat
    FROM sales.produits
    WHERE stockable AND actif
),
matches AS (
    SELECT s.supplier_id,
           p.sku,
           s.delai_livraison_moy AS lead_time_days,
           GREATEST(COALESCE(p.moq, 1), COALESCE(s.commande_min, 1)) AS moq,
           p.pa_ht AS unit_cost,
           CASE
               WHEN s.type_fournisseur = 'CONSTRUCTEUR'
                    AND s.marques ? p.marque THEN 1
               WHEN s.type_fournisseur = 'DISTRIBUTEUR_NATIONAL'
                    AND s.marques ? p.marque THEN 2
               WHEN s.type_fournisseur NOT IN ('OPERATEUR_INTERNE')
                    AND s.marques ? 'ALL' THEN 3
               WHEN s.type_fournisseur = 'SIM_MANUFACTURER'
                    AND p.supply_cat = 'SIM' THEN 1
               WHEN s.type_fournisseur = 'OPERATEUR_INTERNE' THEN 9
           END AS rang
    FROM supply.suppliers s
    JOIN produits_stockables p
      ON s.actif
     AND s.categories ? p.supply_cat
     AND (s.marques ? COALESCE(p.marque, '')
          OR s.marques ? 'ALL'
          OR (s.type_fournisseur = 'SIM_MANUFACTURER' AND p.supply_cat = 'SIM'))
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY sku
               ORDER BY rang ASC, lead_time_days ASC, supplier_id
           ) AS rn
    FROM matches
    WHERE rang IS NOT NULL
)
INSERT INTO supply.supplier_products
    (supplier_id, sku, lead_time_days, moq, unit_cost, is_preferred)
SELECT supplier_id, sku, lead_time_days, moq, unit_cost, (rn = 1)
FROM ranked
ON CONFLICT (supplier_id, sku) DO NOTHING
"""


def main() -> None:
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ooredoo_sales"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(_SQL)
            inserted = cur.rowcount
            cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE is_preferred) "
                        "FROM supply.supplier_products")
            total, preferred = cur.fetchone()
            cur.execute("SELECT COUNT(DISTINCT sku) FROM supply.supplier_products")
            skus = cur.fetchone()[0]
        conn.commit()
        print(f"supplier_products : +{inserted} lignes | total {total} "
              f"| {skus} SKUs couverts | {preferred} préférés")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
