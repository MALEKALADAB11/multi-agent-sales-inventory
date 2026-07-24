"""Journal comptable complet : chaque vente RT écrit un mouvement VENTE.

Le trigger trg_sync_stock_on_sale (baseline 0001) décrémentait
inventory.stock_levels mais n'écrivait rien dans supply.stock_movements —
le journal n'était alimenté en direct que par les réceptions de BC
(RECEPTION_BC, supply_repo.update_status). Chaque vente insérée dans
sales.transactions_rt trace désormais aussi son mouvement VENTE avec
stock avant/après et le sale_id en référence croisée.

Convention existante respectée : quantite négative pour une sortie,
reference_type='VENTE' (cf. lignes seed du journal).

Nom de fonction qualifié `public.` (fix 2026-07-24) : 0001_baseline.sql est un
export pg_dump, il commence par set_config('search_path','',false) — le `false`
rend le reset valable pour toute la session, pas seulement la transaction.
Alembic enchaîne toutes les révisions sur UNE connexion, donc à partir de 0002
le search_path est vide et un CREATE FUNCTION non qualifié échoue en
InvalidSchemaName ("no schema has been selected to create in"). Invisible tant
qu'on applique les révisions une par une (nouvelle connexion à chaque appel),
fatal sur un `alembic upgrade head` depuis une base neuve.

Revision ID: 0012
Revises: 0011
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION public.sync_stock_on_sale() RETURNS trigger AS $fn$
        DECLARE
            v_daily_avg FLOAT;
            v_avant     INT;
            v_apres     INT;
        BEGIN
            -- transactions_rt est volontairement permissive : sans produit,
            -- rien à synchroniser.
            IF NEW.cod_prod IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT COALESCE(AVG(daily_qty), 0) INTO v_daily_avg
            FROM (SELECT date_only, SUM(qte_produit)::float AS daily_qty
                  FROM sales.transactions_rt
                  WHERE cod_prod=NEW.cod_prod AND store_id=NEW.store_id
                    AND date_only >= CURRENT_DATE - INTERVAL '30 days'
                  GROUP BY date_only) sub;

            -- FOR UPDATE : verrouille la ligne pour que stock_avant/apres
            -- restent cohérents si deux ventes du même SKU s'entrelacent.
            SELECT quantity INTO v_avant
            FROM inventory.stock_levels
            WHERE sku=NEW.cod_prod AND store_id=NEW.store_id
            FOR UPDATE;

            UPDATE inventory.stock_levels
            SET quantity=GREATEST(0,quantity-NEW.qte_produit),
                last_sold=NEW.date_vente::date, updated_at=NOW(), last_updated=NOW(),
                remaining_days_of_stock=CASE WHEN v_daily_avg>0
                    THEN GREATEST(0,(GREATEST(0,quantity-NEW.qte_produit)-quantity_reserved)::float/v_daily_avg)
                    ELSE 999.0 END
            WHERE sku=NEW.cod_prod AND store_id=NEW.store_id
            RETURNING quantity INTO v_apres;

            IF v_avant IS NOT NULL THEN
                INSERT INTO supply.stock_movements
                    (sku, store_id, type_mouvement, quantite,
                     stock_avant, stock_apres, reference_id, reference_type,
                     agent_id, date_mouvement)
                VALUES
                    (NEW.cod_prod, NEW.store_id, 'VENTE', -NEW.qte_produit,
                     v_avant, v_apres, NEW.sale_id::text, 'VENTE',
                     NEW.agent_id, NEW.date_vente);
            END IF;

            RETURN NEW;
        END;
        $fn$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    # Restaure le corps 0001 : décrément seul, sans journalisation.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.sync_stock_on_sale() RETURNS trigger AS $fn$
        DECLARE v_daily_avg FLOAT;
        BEGIN
            SELECT COALESCE(AVG(daily_qty), 0) INTO v_daily_avg
            FROM (SELECT date_only, SUM(qte_produit)::float AS daily_qty
                  FROM sales.transactions_rt
                  WHERE cod_prod=NEW.cod_prod AND store_id=NEW.store_id
                    AND date_only >= CURRENT_DATE - INTERVAL '30 days'
                  GROUP BY date_only) sub;
            UPDATE inventory.stock_levels
            SET quantity=GREATEST(0,quantity-NEW.qte_produit),
                last_sold=NEW.date_vente::date, updated_at=NOW(), last_updated=NOW(),
                remaining_days_of_stock=CASE WHEN v_daily_avg>0
                    THEN GREATEST(0,(GREATEST(0,quantity-NEW.qte_produit)-quantity_reserved)::float/v_daily_avg)
                    ELSE 999.0 END
            WHERE sku=NEW.cod_prod AND store_id=NEW.store_id;
            RETURN NEW;
        END;
        $fn$ LANGUAGE plpgsql;
    """)
