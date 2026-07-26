"""Purge du flot de ventes simulees (transactions_rt) pour I63.

1. Restaure inventory.stock_levels au MAX(stock_avant) connu du journal
   des mouvements VENTE (etat le plus sain observe avant le drain).
2. Supprime les mouvements VENTE lies aux ventes RT.
3. Supprime toutes les lignes transactions_rt du store.
Le tout dans une transaction unique.
"""
import asyncio, asyncpg

STORE = 'I63'

async def main():
    c = await asyncpg.connect(host='localhost', database='ooredoo_sales',
                              user='postgres', password='root')
    async with c.transaction():
        restored = await c.execute("""
            UPDATE inventory.stock_levels sl
            SET quantity = m.max_avant, updated_at = NOW(), last_updated = NOW(),
                remaining_days_of_stock = 999.0
            FROM (
                SELECT sku, store_id, MAX(stock_avant) AS max_avant
                FROM supply.stock_movements
                WHERE store_id = $1 AND type_mouvement = 'VENTE'
                GROUP BY sku, store_id
            ) m
            WHERE sl.sku = m.sku AND sl.store_id = m.store_id
              AND sl.quantity < m.max_avant
        """, STORE)
        print('stock restaure :', restored)

        mv = await c.execute("""
            DELETE FROM supply.stock_movements
            WHERE store_id = $1 AND type_mouvement = 'VENTE'
              AND reference_id IN (SELECT sale_id::text FROM sales.transactions_rt
                                   WHERE store_id = $1)
        """, STORE)
        print('mouvements VENTE supprimes :', mv)

        tx = await c.execute(
            "DELETE FROM sales.transactions_rt WHERE store_id = $1", STORE)
        print('transactions_rt supprimees :', tx)

    row = await c.fetchrow("""
        SELECT COUNT(*) FILTER (WHERE quantity <= 0) AS ruptures,
               COUNT(*) AS total
        FROM inventory.stock_levels WHERE store_id = $1""", STORE)
    print('etat stock apres purge :', dict(row))

    ca = await c.fetchval("""
        SELECT ROUND(COALESCE(SUM(lig_ttc),0)) FROM sales.transactions
        WHERE store_id=$1 AND date_only=CURRENT_DATE""", STORE)
    print('CA du jour restant (batch) :', ca, 'DT')
    await c.close()

asyncio.run(main())
