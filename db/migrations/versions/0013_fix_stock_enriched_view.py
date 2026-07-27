"""Fix sales.vw_stock_enriched: real gamme label + usable margin.

Deux défauts rendaient la vue inexploitable pour toute logique commerciale :

  1. ``p.famille AS gamme_libelle`` exposait le code famille brut ('20', '47')
     sous le nom d'une colonne censée porter le libellé. sales.produits a une
     vraie colonne gamme_libelle (TERMINAL, FORFAIT, ACCESSOIRE_PREMIUM,
     SERVICE, RECHARGE…) — c'est elle que les consommateurs attendent, et sans
     elle aucune segmentation par gamme n'est possible.

  2. ``COALESCE(p.marge_pct, 0)`` écrasait à 0 les 3 559 SKU qui n'ont qu'une
     marge calculée (marge_pct_calc) : 78% du catalogue remontait à marge nulle,
     donc tout tri « par marge » triait des zéros.

Le code famille reste exposé sous son propre nom (famille_code) pour ne rien
perdre. Renommer une colonne impose DROP + CREATE : CREATE OR REPLACE VIEW
n'autorise que l'ajout de colonnes en fin de liste. Seul le Stratège lit
sales.vw_stock_enriched (sku / product_name / stock_dispo / stock_risk) —
inventory.vw_stock_enriched, consommée par le Coach et le RAG, est une vue
distincte et n'est pas touchée.

Revision ID: 0013
Revises: 0012
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DROP VIEW IF EXISTS sales.vw_stock_enriched;
        CREATE VIEW sales.vw_stock_enriched AS
        SELECT sl.store_id,
               sl.sku,
               COALESCE(p.nom, sl.sku::text)                        AS product_name,
               p.categorie,
               p.famille                                            AS famille_code,
               COALESCE(p.gamme_libelle, 'AUTRE')                   AS gamme_libelle,
               COALESCE(p.prix_ttc, 0::numeric)                     AS prix_ttc,
               COALESCE(p.marge_pct, p.marge_pct_calc, 0::numeric)  AS marge_pct,
               p.flag_terminal,
               p.flag_forfait,
               p.flag_sim,
               p.flag_recharge,
               p.actif,
               COALESCE(sl.quantity_available, sl.quantity, 0)      AS stock_dispo,
               COALESCE(sl.quantity_reserved, 0)                    AS stock_in_transit,
               sl.last_updated,
               CASE
                   WHEN COALESCE(sl.quantity_available, sl.quantity, 0) = 0  THEN 'rupture'
                   WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 3 THEN 'critical'
                   WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 10 THEN 'warning'
                   ELSE 'ok'
               END                                                  AS stock_risk
        FROM inventory.stock_levels sl
        LEFT JOIN sales.produits p ON p.sku = sl.sku;
    """)


def downgrade() -> None:
    op.execute("""
        DROP VIEW IF EXISTS sales.vw_stock_enriched;
        CREATE VIEW sales.vw_stock_enriched AS
        SELECT sl.store_id,
               sl.sku,
               COALESCE(p.nom, sl.sku::text)                   AS product_name,
               p.categorie,
               p.famille                                       AS gamme_libelle,
               COALESCE(p.prix_ttc, 0::numeric)                AS prix_ttc,
               COALESCE(p.marge_pct, 0::numeric)               AS marge_pct,
               p.flag_terminal,
               p.flag_forfait,
               COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_dispo,
               COALESCE(sl.quantity_reserved, 0)               AS stock_in_transit,
               sl.last_updated,
               CASE
                   WHEN COALESCE(sl.quantity_available, sl.quantity, 0) = 0  THEN 'rupture'
                   WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 3 THEN 'critical'
                   WHEN COALESCE(sl.quantity_available, sl.quantity, 0) <= 10 THEN 'warning'
                   ELSE 'ok'
               END                                             AS stock_risk
        FROM inventory.stock_levels sl
        LEFT JOIN sales.produits p ON p.sku = sl.sku;
    """)
