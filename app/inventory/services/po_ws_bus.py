"""
po_ws_bus.py — In-process WebSocket registry for the Purchase Order Kanban.

Shared between the API layer (src/api/supply_routes.py, owns the WS lifecycle:
accept/register/unregister) and the agent layer (src/agents/decision/agent.py,
publishes po_suggested events) so the decision agent never has to import the
API layer directly.

Sync/async bridge (broadcast_po_suggested_sync) mirrors the pattern already
used by src/services/redis_alert_bus.py::dispatch_alerts_sync — the decision
agent runs synchronously (inside a ThreadPoolExecutor worker), while the WS
send calls are async.
"""
from __future__ import annotations

import asyncio
import datetime
import decimal
import logging
import time
import uuid
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# store_id -> list of connected WebSocket clients for the PO board.
connections: Dict[str, List[Any]] = {}


def _json_safe(obj: Any) -> Any:
    """
    Raw psycopg2 RealDictCursor rows carry Decimal/UUID/date objects that
    Starlette's WebSocket.send_json (plain stdlib json under the hood) can't
    serialize — unlike REST responses, there's no jsonable_encoder in the way.
    """
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


async def broadcast_po_status(
    store_id: str, po_id: str, sku: Any, old_statut: str, new_statut: str,
) -> None:
    await _broadcast(store_id, {
        "type":       "po_status_changed",
        "store_id":   store_id,
        "po_id":      po_id,
        "sku":        sku,
        "old_statut": old_statut,
        "new_statut": new_statut,
        "timestamp":  time.time(),
    })


async def broadcast_po_suggested(po: Dict[str, Any]) -> None:
    """Notifies the board that the agent just created a SUGGERE purchase order."""
    store_id = po.get("store_id")
    if not store_id:
        return
    await _broadcast(store_id, {
        "type":      "po_suggested",
        "store_id":  store_id,
        "po":        _json_safe(po),
        "timestamp": time.time(),
    })


def broadcast_po_suggested_sync(po: Dict[str, Any]) -> None:
    """
    Sync wrapper for callers running outside an async context (the decision
    agent's run() is plain sync code). Never raises — a failed broadcast must
    not break the decision pipeline; the PO row is already committed either way.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(broadcast_po_suggested(po))
        else:
            loop.run_until_complete(broadcast_po_suggested(po))
    except RuntimeError:
        asyncio.run(broadcast_po_suggested(po))
    except Exception as exc:
        logger.warning("[po_ws_bus] broadcast_po_suggested_sync failed: %s", exc)


async def _broadcast(store_id: str, message: Dict[str, Any]) -> None:
    conns = connections.get(store_id, [])
    dead = []
    for ws in conns:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        conns.remove(ws)
