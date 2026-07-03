"""
Supply API Routes — Purchase Order Kanban board
================================================
Bounded context separate from src/api/routes.py (inventory): drives the
supply.purchase_orders lifecycle (BROUILLON -> SOUMIS -> CONFIRME -> EXPEDIE
-> RECU_PARTIEL/RECU, side states ANNULE/LITIGE).

Mounted standalone in main.py — own prefix, own WS registry, no coupling to
the inventory router's cache/WS machinery.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from db.repositories.supply_repo import (
    SyncPurchaseOrderRepo,
    PurchaseOrderTransitionError,
    ALLOWED_TRANSITIONS,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/supply", tags=["supply"])


def _decimal_safe(obj: Any) -> Any:
    import decimal
    if isinstance(obj, dict):
        return {k: _decimal_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_safe(v) for v in obj]
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    return obj


async def _require_store_access(request: Request, store_id: str) -> None:
    """Mirrors the coach_chat.py RBAC call-site pattern (auth_router.validate_store_access)."""
    from auth_router import validate_store_access

    bearer = request.headers.get("Authorization", "")
    token = bearer[7:] if bearer.startswith("Bearer ") else None
    await validate_store_access(token, store_id)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket registry — dedicated to PO board traffic, decoupled from the
# inventory router's _active_ws_connections (avoids mixing stock_delta noise).
# ─────────────────────────────────────────────────────────────────────────────

_po_ws_connections: Dict[str, List[WebSocket]] = {}


async def _broadcast_po_status(
    store_id: str, po_id: str, sku: Any, old_statut: str, new_statut: str,
) -> None:
    message = {
        "type":       "po_status_changed",
        "store_id":   store_id,
        "po_id":      po_id,
        "sku":        sku,
        "old_statut": old_statut,
        "new_statut": new_statut,
        "timestamp":  time.time(),
    }
    connections = _po_ws_connections.get(store_id, [])
    dead = []
    for ws in connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.remove(ws)


@router.websocket("/ws/{store_id}")
async def po_board_ws(websocket: WebSocket, store_id: str) -> None:
    await websocket.accept()
    _po_ws_connections.setdefault(store_id, []).append(websocket)
    logger.info("[supply/ws] connected store=%s (total=%d)", store_id, len(_po_ws_connections[store_id]))
    try:
        while True:
            # Keep-alive: client pings every 15s (purchase-board-socket.service.ts).
            # We don't need the payload, just detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        conns = _po_ws_connections.get(store_id, [])
        if websocket in conns:
            conns.remove(websocket)
        logger.info("[supply/ws] disconnected store=%s (remaining=%d)", store_id, len(conns))


# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────

class CreatePurchaseOrderRequest(BaseModel):
    recommendation_id: str
    supplier_id: Optional[str] = None
    priorite: str = Field(default="NORMAL")


class UpdatePurchaseOrderStatusRequest(BaseModel):
    statut: str


@router.get("/purchase-orders/{store_id}")
async def list_purchase_orders(
    request: Request,
    store_id: str,
    statut: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    await _require_store_access(request, store_id)
    try:
        orders = SyncPurchaseOrderRepo.list_purchase_orders(store_id, statut)
        return _decimal_safe({
            "store_id":         store_id,
            "purchase_orders":  orders,
            "count":            len(orders),
            "filter":           statut or "all",
        })
    except Exception as exc:
        logger.error("Failed to list purchase orders for %s: %s", store_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/purchase-orders/detail/{po_id}")
async def get_purchase_order(request: Request, po_id: str) -> Dict[str, Any]:
    po = SyncPurchaseOrderRepo.get_purchase_order_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail=f"Purchase order '{po_id}' not found.")
    await _require_store_access(request, po["store_id"])
    return _decimal_safe(po)


@router.post("/purchase-orders")
async def create_purchase_order(
    request: Request,
    req: CreatePurchaseOrderRequest,
) -> Dict[str, Any]:
    store_id = SyncPurchaseOrderRepo.get_recommendation_store_id(req.recommendation_id)
    if not store_id:
        raise HTTPException(
            status_code=404,
            detail=f"Recommendation '{req.recommendation_id}' not found.",
        )
    await _require_store_access(request, store_id)

    try:
        po = SyncPurchaseOrderRepo.create_from_recommendation(
            recommendation_id=req.recommendation_id,
            supplier_id=req.supplier_id,
            priorite=req.priorite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if not po:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Could not create purchase order from recommendation "
                f"'{req.recommendation_id}' — it must exist, reference a known "
                f"product, be in 'approved' status, and have a usable quantity."
            ),
        )

    logger.info("Purchase order %s created (BROUILLON) from recommendation %s",
                po["po_id"], req.recommendation_id)
    return _decimal_safe(po)


@router.patch("/purchase-orders/{po_id}")
async def update_purchase_order_status(
    request: Request,
    po_id: str,
    req: UpdatePurchaseOrderStatusRequest,
) -> Dict[str, Any]:
    valid_statuses = set(ALLOWED_TRANSITIONS.keys())
    if req.statut not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid statut '{req.statut}'. Must be one of: {sorted(valid_statuses)}",
        )

    existing = SyncPurchaseOrderRepo.get_purchase_order_by_id(po_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Purchase order '{po_id}' not found.")
    await _require_store_access(request, existing["store_id"])

    old_statut = existing["statut"]

    try:
        updated = SyncPurchaseOrderRepo.update_status(po_id, req.statut)
    except PurchaseOrderTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not updated:
        raise HTTPException(status_code=404, detail=f"Purchase order '{po_id}' not found.")

    await _broadcast_po_status(
        store_id=updated["store_id"],
        po_id=po_id,
        sku=updated["sku"],
        old_statut=old_statut,
        new_statut=req.statut,
    )

    logger.info("Purchase order %s: %s -> %s", po_id, old_statut, req.statut)
    return {
        "po_id":   po_id,
        "statut":  req.statut,
        "updated": True,
    }
