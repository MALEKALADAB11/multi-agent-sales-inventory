"""
sales_module_utils.py — Utilitaires partagés pour le backend PFE
=================================================================
Place ce fichier dans : PFE-Backend/ (même dossier que main.py)
"""
import asyncio
import logging
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

# DB config — identique à sales-module/data/postgres_provider.py
_DB = {
    "host": "localhost", "port": 5432,
    "database": "ooredoo_sales",
    "user": "postgres", "password": "admin",
}

async def _insert_rt_tx(
    store_id:     str,
    sku:          str,
    advisor_id:   str,
    product_name: str,
    amount:       float,
    units:        int = 1,
) -> None:
    """
    Insère une transaction en temps réel dans sales.transactions_rt.
    Table sans FK — aucune contrainte d'intégrité bloquante.
    fetch_pos_data fait ensuite UNION ALL avec les données historiques.
    """
    if amount <= 0:
        return  # ignorer les transactions sans montant

    try:
        import asyncpg
        now = datetime.now()
        conn = await asyncpg.connect(**_DB, timeout=3)
        try:
            await conn.execute("""
                INSERT INTO sales.transactions_rt
                    (sale_id, date_vente, date_only, heure,
                     store_id, agent_id, cod_prod, des_produit,
                     lig_ttc, lig_ht, lig_tva, qte_produit)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (sale_id) DO NOTHING
            """,
                str(uuid.uuid4()),          # sale_id
                now,                        # date_vente
                now.date(),                 # date_only ← CURRENT_DATE
                now.hour,                   # heure
                store_id,                   # store_id
                advisor_id,                 # agent_id
                sku,                        # cod_prod
                product_name,               # des_produit
                round(amount, 2),           # lig_ttc
                round(amount / 1.19, 2),    # lig_ht
                round(amount * 0.19 / 1.19, 2),  # lig_tva
                units,                      # qte_produit
            )
            logger.debug("[RT TX] Inséré %s@%s %.0f TND", sku, store_id, amount)
        finally:
            await conn.close()

    except Exception as e:
        logger.debug("[RT TX] Erreur insert: %s", str(e)[:80])
