"""
supply_repo.py
All database access for the `supply` schema (purchase orders lifecycle).
Kept separate from inventory_repo.py — distinct bounded context, own schema.

Usage:
    from app.inventory.repositories.supply_repo import SyncPurchaseOrderRepo, PurchaseOrderTransitionError
"""
import os
import logging
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Status transition whitelist — server-side authority (client also mirrors this
# for immediate UI feedback, but this is what actually gets enforced).
# ─────────────────────────────────────────────────────────────────────────────
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "SUGGERE":      {"BROUILLON", "ANNULE"},
    "BROUILLON":    {"SOUMIS", "ANNULE"},
    "SOUMIS":       {"CONFIRME", "ANNULE", "LITIGE"},
    "CONFIRME":     {"EXPEDIE", "ANNULE", "LITIGE"},
    "EXPEDIE":      {"RECU_PARTIEL", "RECU", "LITIGE"},
    "RECU_PARTIEL": {"RECU", "LITIGE"},
    "RECU":         set(),
    "ANNULE":       set(),
    "LITIGE":       {"CONFIRME", "EXPEDIE", "ANNULE"},
}

_TERMINAL_RECEIVED = {"RECU", "RECU_PARTIEL"}


class PurchaseOrderTransitionError(Exception):
    """Raised when a requested statut transition is not in ALLOWED_TRANSITIONS."""
    def __init__(self, current: str, requested: str):
        self.current = current
        self.requested = requested
        allowed = sorted(ALLOWED_TRANSITIONS.get(current, set()))
        super().__init__(
            f"Cannot move purchase order from {current} to {requested}. "
            f"Allowed next statuses: {allowed or '(none — terminal state)'}"
        )


class SyncPurchaseOrderRepo:
    """
    Synchronous DB access for supply.purchase_orders — mirrors the
    SyncInventoryRepo pattern (psycopg2, one connection per call, @staticmethod
    so callers never instantiate).
    """

    @staticmethod
    def _conn():
        from app.core.config import config
        return psycopg2.connect(
            host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
            user=config.DB_USER, password=config.DB_PASSWORD,
        )

    # ── Reads ────────────────────────────────────────────────────────────────

    @staticmethod
    def list_purchase_orders(store_id: str, statut: Optional[str] = None) -> list[dict]:
        """All POs for a store, optionally filtered by statut. Enriched with product_name."""
        conn = SyncPurchaseOrderRepo._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if statut and statut != "all":
                    cur.execute("""
                        SELECT po.*, p.product_name
                        FROM supply.purchase_orders po
                        LEFT JOIN inventory.products p ON p.sku = po.sku
                        WHERE po.store_id = %s AND po.statut = %s
                        ORDER BY po.date_commande DESC
                    """, (store_id, statut))
                else:
                    cur.execute("""
                        SELECT po.*, p.product_name
                        FROM supply.purchase_orders po
                        LEFT JOIN inventory.products p ON p.sku = po.sku
                        WHERE po.store_id = %s
                        ORDER BY po.date_commande DESC
                    """, (store_id,))
                return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("SyncPurchaseOrderRepo.list_purchase_orders(%s): %s", store_id, exc)
            return []
        finally:
            conn.close()

    @staticmethod
    def get_purchase_order_by_id(po_id: str) -> Optional[dict]:
        conn = SyncPurchaseOrderRepo._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT po.*, p.product_name
                    FROM supply.purchase_orders po
                    LEFT JOIN inventory.products p ON p.sku = po.sku
                    WHERE po.po_id = %s
                """, (po_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.warning("SyncPurchaseOrderRepo.get_purchase_order_by_id(%s): %s", po_id, exc)
            return None
        finally:
            conn.close()

    @staticmethod
    def get_purchase_order_by_recommendation(recommendation_id: str) -> Optional[dict]:
        """Used to enforce one-PO-per-recommendation (409 on duplicate creation)."""
        conn = SyncPurchaseOrderRepo._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM supply.purchase_orders WHERE recommendation_id = %s",
                    (recommendation_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.warning(
                "SyncPurchaseOrderRepo.get_purchase_order_by_recommendation(%s): %s",
                recommendation_id, exc,
            )
            return None
        finally:
            conn.close()

    @staticmethod
    def get_recommendation_store_id(recommendation_id: str) -> Optional[str]:
        """Lightweight lookup used by the route layer to run RBAC before creating a PO."""
        conn = SyncPurchaseOrderRepo._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT store_id FROM inventory.recommendations WHERE id = %s",
                    (recommendation_id,),
                )
                row = cur.fetchone()
                return str(row[0]) if row else None
        except Exception as exc:
            logger.warning(
                "SyncPurchaseOrderRepo.get_recommendation_store_id(%s): %s",
                recommendation_id, exc,
            )
            return None
        finally:
            conn.close()

    # ── Writes ───────────────────────────────────────────────────────────────

    @staticmethod
    def create_from_recommendation(
        recommendation_id: str,
        supplier_id: Optional[str] = None,
        priorite: str = "NORMAL",
    ) -> Optional[dict]:
        """
        Creates a BROUILLON purchase order from an approved recommendation.
        Joins inventory.recommendations + inventory.products in one query
        (same DB, cross-schema — no need to go through inventory_repo.py).

        Returns the created PO row (dict, with product_name), or None if the
        recommendation/product doesn't exist or isn't in 'approved' status.
        Raises ValueError if a PO already exists for this recommendation_id
        (caller / route layer maps this to 409).
        """
        conn = SyncPurchaseOrderRepo._conn()
        try:
            existing = SyncPurchaseOrderRepo.get_purchase_order_by_recommendation(recommendation_id)
            if existing:
                raise ValueError(f"Purchase order already exists for recommendation {recommendation_id}")

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        r.id            AS recommendation_id,
                        r.sku           AS sku,
                        r.store_id      AS store_id,
                        r.status        AS recommendation_status,
                        COALESCE(r.suggested_quantity, r.order_qty) AS quantity,
                        p.unit_cost     AS unit_cost,
                        p.lead_time_days AS lead_time_days,
                        p.product_name  AS product_name
                    FROM inventory.recommendations r
                    JOIN inventory.products p ON p.sku = r.sku
                    WHERE r.id = %s
                """, (recommendation_id,))
                row = cur.fetchone()

                if not row:
                    logger.warning(
                        "create_from_recommendation: recommendation/product not found for %s",
                        recommendation_id,
                    )
                    return None

                if row["recommendation_status"] != "approved":
                    logger.warning(
                        "create_from_recommendation: recommendation %s is '%s', not 'approved'",
                        recommendation_id, row["recommendation_status"],
                    )
                    return None

                qty = int(row["quantity"] or 0)
                if qty <= 0:
                    logger.warning(
                        "create_from_recommendation: no usable quantity for recommendation %s",
                        recommendation_id,
                    )
                    return None

                unit_cost   = row["unit_cost"] or 0
                total_cost  = qty * float(unit_cost)
                lead_days   = row["lead_time_days"] or 14

                cur.execute("""
                    INSERT INTO supply.purchase_orders
                        (sku, supplier_id, store_id, quantite_commandee,
                         prix_unitaire_ht, montant_total_ht, statut, priorite,
                         date_livraison_prevue, recommendation_id)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, 'BROUILLON', %s,
                         CURRENT_DATE + (%s || ' days')::interval, %s)
                    RETURNING *
                """, (
                    row["sku"], supplier_id, row["store_id"], qty,
                    unit_cost, total_cost, priorite,
                    lead_days, recommendation_id,
                ))
                created = dict(cur.fetchone())
                created["product_name"] = row["product_name"]

            conn.commit()
            return created

        except ValueError:
            raise
        except Exception as exc:
            conn.rollback()
            logger.warning("SyncPurchaseOrderRepo.create_from_recommendation(%s): %s", recommendation_id, exc)
            return None
        finally:
            conn.close()

    @staticmethod
    def create_suggestion_from_recommendation(
        recommendation_id: str,
        agent_run_id: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Auto-creates a SUGGERE purchase order straight from a *pending*
        recommendation — called by the decision agent right after it writes
        the recommendation, so a card appears on the Kanban the moment the
        agent decides, before any human has approved anything.

        Unlike create_from_recommendation, this does NOT require the
        recommendation to be 'approved' — SUGGERE is the pre-approval state.
        Approving/rejecting later goes through approve_suggestion /
        reject_suggestion, never through this method again (one-PO-per-
        recommendation is still enforced).

        Never raises — a failed suggestion must not break the decision
        pipeline; the recommendation row is already committed either way.
        """
        conn = SyncPurchaseOrderRepo._conn()
        try:
            existing = SyncPurchaseOrderRepo.get_purchase_order_by_recommendation(recommendation_id)
            if existing:
                return None

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        r.id            AS recommendation_id,
                        r.sku           AS sku,
                        r.store_id      AS store_id,
                        r.urgency       AS urgency,
                        r.confidence    AS confidence,
                        r.suggested_quantity AS quantity,
                        p.unit_cost     AS unit_cost,
                        p.lead_time_days AS lead_time_days,
                        p.product_name  AS product_name
                    FROM inventory.recommendations r
                    JOIN inventory.products p ON p.sku = r.sku
                    WHERE r.id = %s
                """, (recommendation_id,))
                row = cur.fetchone()

                if not row:
                    logger.warning(
                        "create_suggestion_from_recommendation: recommendation/product not found for %s",
                        recommendation_id,
                    )
                    return None

                qty = int(row["quantity"] or 0)
                if qty <= 0:
                    logger.warning(
                        "create_suggestion_from_recommendation: no usable quantity for recommendation %s",
                        recommendation_id,
                    )
                    return None

                unit_cost  = row["unit_cost"] or 0
                total_cost = qty * float(unit_cost)
                lead_days  = row["lead_time_days"] or 14

                cur.execute("""
                    INSERT INTO supply.purchase_orders
                        (sku, store_id, quantite_commandee,
                         prix_unitaire_ht, montant_total_ht, statut, source,
                         urgency, confidence, agent_decision_id,
                         date_livraison_prevue, recommendation_id)
                    VALUES
                        (%s, %s, %s, %s, %s, 'SUGGERE', 'AGENT',
                         %s, %s, %s,
                         CURRENT_DATE + (%s || ' days')::interval, %s)
                    RETURNING *
                """, (
                    row["sku"], row["store_id"], qty,
                    unit_cost, total_cost,
                    row["urgency"], row["confidence"], agent_run_id,
                    lead_days, recommendation_id,
                ))
                created = dict(cur.fetchone())
                created["product_name"] = row["product_name"]

            conn.commit()
            return created

        except Exception as exc:
            conn.rollback()
            logger.warning(
                "SyncPurchaseOrderRepo.create_suggestion_from_recommendation(%s): %s",
                recommendation_id, exc,
            )
            return None
        finally:
            conn.close()

    @staticmethod
    def approve_suggestion(po_id: str, decided_by: str) -> Optional[dict]:
        """
        Human approves an agent-suggested PO: recommendation -> approved,
        PO SUGGERE -> BROUILLON, both in one transaction. This is the single
        gate that lets a suggestion turn into real spend.
        """
        return SyncPurchaseOrderRepo._decide_suggestion(
            po_id, decided_by, recommendation_status="approved", new_statut="BROUILLON",
        )

    @staticmethod
    def reject_suggestion(po_id: str, decided_by: str) -> Optional[dict]:
        """Human rejects an agent-suggested PO: recommendation -> rejected, PO -> ANNULE."""
        return SyncPurchaseOrderRepo._decide_suggestion(
            po_id, decided_by, recommendation_status="rejected", new_statut="ANNULE",
        )

    @staticmethod
    def _decide_suggestion(
        po_id: str, decided_by: str, *, recommendation_status: str, new_statut: str,
    ) -> Optional[dict]:
        conn = SyncPurchaseOrderRepo._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT statut, recommendation_id FROM supply.purchase_orders WHERE po_id = %s",
                    (po_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None

                current = row["statut"]
                if current != "SUGGERE":
                    raise PurchaseOrderTransitionError(current, new_statut)

                if row["recommendation_id"]:
                    cur.execute("""
                        UPDATE inventory.recommendations
                        SET status = %s, decided_by = %s, decided_at = NOW()
                        WHERE id = %s
                    """, (recommendation_status, decided_by, row["recommendation_id"]))

                cur.execute("""
                    UPDATE supply.purchase_orders
                    SET statut = %s, updated_at = NOW()
                    WHERE po_id = %s AND statut = 'SUGGERE'
                    RETURNING *
                """, (new_statut, po_id))
                updated = cur.fetchone()

            conn.commit()
            return dict(updated) if updated else None

        except PurchaseOrderTransitionError:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            logger.warning("SyncPurchaseOrderRepo._decide_suggestion(%s, %s): %s", po_id, new_statut, exc)
            return None
        finally:
            conn.close()

    @staticmethod
    def update_status(po_id: str, new_statut: str, quantite_recue: Optional[int] = None) -> Optional[dict]:
        """
        Validates the transition against ALLOWED_TRANSITIONS before updating.
        Raises PurchaseOrderTransitionError if invalid.
        Returns the updated row (dict), or None if po_id not found.

        On transition into RECU/RECU_PARTIEL, also records the reception in
        supply.stock_movements and increments inventory.stock_levels — this
        closes the loop so the next agent cycle sees accurate stock instead
        of re-suggesting the same reorder forever. All in one transaction.
        """
        conn = SyncPurchaseOrderRepo._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM supply.purchase_orders WHERE po_id = %s", (po_id,)
                )
                row = cur.fetchone()
                if not row:
                    return None

                current = row["statut"]
                if new_statut not in ALLOWED_TRANSITIONS.get(current, set()):
                    raise PurchaseOrderTransitionError(current, new_statut)

                if new_statut in _TERMINAL_RECEIVED:
                    received_qty = quantite_recue if quantite_recue is not None else int(row["quantite_commandee"])

                    cur.execute("""
                        UPDATE supply.purchase_orders SET
                            statut                = %s,
                            quantite_recue        = quantite_recue + %s,
                            date_livraison_reelle = CURRENT_DATE,
                            delai_reel_jours      = CURRENT_DATE - date_commande::date,
                            updated_at            = NOW()
                        WHERE po_id = %s
                        RETURNING *
                    """, (new_statut, received_qty, po_id))
                    updated = cur.fetchone()

                    if received_qty > 0:
                        # inventory.stock_levels.sku is INTEGER, same as
                        # supply.purchase_orders.sku — no cast needed. Mirrors
                        # SyncInventoryRepo.upsert_stock_level_sync's column names
                        # (quantity, not stock_current — that's only the read-side
                        # alias used in SELECTs). quantity_available is a GENERATED
                        # column (quantity - quantity_reserved) — never written to.
                        cur.execute("""
                            UPDATE inventory.stock_levels SET
                                quantity      = quantity + %s,
                                last_received = CURRENT_DATE,
                                last_updated  = NOW()
                            WHERE sku = %s AND store_id = %s
                            RETURNING quantity
                        """, (received_qty, row["sku"], row["store_id"]))
                        stock_row = cur.fetchone()
                        stock_after = stock_row["quantity"] if stock_row else None

                        cur.execute("""
                            INSERT INTO supply.stock_movements
                                (sku, store_id, type_mouvement, quantite,
                                 stock_avant, stock_apres, reference_id, reference_type)
                            VALUES (%s, %s, 'RECEPTION_BC', %s, %s, %s, %s, 'BC')
                        """, (
                            row["sku"], row["store_id"], received_qty,
                            (stock_after - received_qty) if stock_after is not None else None,
                            stock_after, str(po_id),
                        ))
                else:
                    cur.execute("""
                        UPDATE supply.purchase_orders SET
                            statut     = %s,
                            updated_at = NOW()
                        WHERE po_id = %s
                        RETURNING *
                    """, (new_statut, po_id))
                    updated = cur.fetchone()

            conn.commit()
            return dict(updated) if updated else None

        except PurchaseOrderTransitionError:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            logger.warning("SyncPurchaseOrderRepo.update_status(%s, %s): %s", po_id, new_statut, exc)
            return None
        finally:
            conn.close()
