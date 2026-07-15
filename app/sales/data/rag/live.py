"""
rag/live.py — Hydratation des documents avec les valeurs faisant foi (PostgreSQL).

Séparation des rôles :

    Milvus    → QUELS documents sont pertinents (recherche)
    Postgres  → QUELLES sont les valeurs (vérité)

Le payload indexé dans Milvus est un instantané pris au moment de l'ingestion.
Un prix révisé, une promo lancée, un stock écoulé dans l'heure : le vecteur ne
bouge pas, la ligne Postgres si. Servir le payload au LLM, c'est lui faire citer
comme un fait une valeur qui peut dater de plusieurs jours.

On relit donc les champs volatils (prix, marge, stock, promo) en une requête pour
tous les SKU retenus, juste avant de construire le prompt. Le coût est d'un aller
Postgres sur 3 à 6 lignes ; le bénéfice est qu'aucun chiffre affiché ne peut être
périmé. Les champs stables (nom, gamme, catégorie) restent ceux de l'index.
"""

import logging

from psycopg2.extras import RealDictCursor

from app.core.db import get_conn
from app.sales.data.rag.documents import RetrievedDocument
from app.sales.data.rag.settings import DOMAIN_PRODUCT

logger = logging.getLogger(__name__)

_SQL_LIVE_PRODUCTS = """
    SELECT p.sku,
           p.prix_ttc,
           COALESCE(p.marge_pct, p.marge_pct_calc) AS marge_pct,
           v.quantity_available,
           v.stock_status,
           pr.discount_pct,
           pr.promo_name
      FROM sales.produits p
      LEFT JOIN inventory.vw_stock_enriched v
             ON v.sku = p.sku AND v.store_id = %(store)s
      LEFT JOIN inventory.vw_active_promotions pr
             ON pr.sku = p.sku
     WHERE p.sku::text = ANY(%(skus)s)
"""


def hydrate_products(docs: list[RetrievedDocument], store_id: str) -> list[RetrievedDocument]:
    """
    Remplace prix/marge/stock/promo des fiches produit par les valeurs Postgres.

    Ne lève jamais : si la base est injoignable, les documents gardent leur payload
    indexé et `payload["live"]` reste False — le formatage peut alors signaler au
    coach que les chiffres ne sont pas confirmés.
    """
    products = [d for d in docs if d.domain == DOMAIN_PRODUCT and d.sku]
    if not products:
        return docs

    skus = [str(d.sku) for d in products]
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(_SQL_LIVE_PRODUCTS, {"store": store_id, "skus": skus})
                rows = {str(r["sku"]): r for r in cur.fetchall()}
    except Exception as e:
        logger.warning("[RAG LIVE] hydratation impossible: %.100s", str(e))
        return docs

    for doc in products:
        row = rows.get(str(doc.sku))
        if row is None:
            # Le SKU a disparu du catalogue depuis l'indexation : ne pas le servir
            # comme un produit vendable.
            doc.payload["live"] = False
            doc.payload["retire_du_catalogue"] = True
            continue

        promo_active = row["discount_pct"] is not None
        doc.payload.update({
            "prix_ttc":     float(row["prix_ttc"] or 0),
            "marge_pct":    float(row["marge_pct"]) if row["marge_pct"] is not None else None,
            "stock_dispo":  int(row["quantity_available"]) if row["quantity_available"] is not None else None,
            "stock_status": row["stock_status"] or "unknown",
            "promo_active": promo_active,
            "discount_pct": float(row["discount_pct"]) if promo_active else None,
            "promo_nom":    row["promo_name"] if promo_active else None,
            "live":         True,
        })

    logger.debug("[RAG LIVE] %d fiches produit rafraîchies", len(products))
    return docs
