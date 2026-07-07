"""
Redis Alert Bus — dispatch asynchrone des alertes critiques stock.

Architecture :
  InventoryDecisionAgent → [CRITICAL/EXPEDITE/STOCKOUT] → publish("alerts:{store_id}")
  Abonnés : monitoring-module, frontend WebSocket bridge, SMS gateway

Canaux Redis :
  alerts:{store_id}         — Pub/Sub temps réel pour abonnés actifs
  alerts_history:{store_id} — Sorted set (score=timestamp) TTL 1h

Types d'alertes :
  CRITICAL_STOCK     — risk_level=CRITICAL (rupture imminente)
  EXPEDITE           — commande urgente requise
  STOCKOUT_IMMINENT  — days_of_stock < 3

Fallback : si Redis indisponible → log WARNING et pipeline continue (non-bloquant).

Env vars :
  REDIS_HOST (default: localhost)
  REDIS_PORT (default: 6379)
  REDIS_DB   (default: 0)
  REDIS_PASSWORD (optionnel)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Singleton Redis client ─────────────────────────────────────────────────────

_redis_client = None
_redis_available: bool = False


async def _get_redis():
    """Lazy-init Redis async client. Thread-safe via asyncio."""
    global _redis_client, _redis_available

    if _redis_client is not None:
        return _redis_client if _redis_available else None

    try:
        import redis.asyncio as aioredis  # type: ignore

        host     = os.getenv("REDIS_HOST", "localhost")
        port     = int(os.getenv("REDIS_PORT", "6379"))
        db       = int(os.getenv("REDIS_DB", "0"))
        password = os.getenv("REDIS_PASSWORD") or None

        _redis_client = aioredis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await _redis_client.ping()
        _redis_available = True
        logger.info("[AlertBus] Redis connecté — %s:%d/db%d", host, port, db)

    except ImportError:
        logger.warning("[AlertBus] redis.asyncio non installé — alertes désactivées")
        _redis_available = False
    except Exception as exc:
        logger.warning("[AlertBus] Redis indisponible (%s) — alertes non publiées", exc)
        _redis_available = False

    return _redis_client if _redis_available else None


def _reset_client() -> None:
    """Réinitialise le client (tests unitaires)."""
    global _redis_client, _redis_available
    _redis_client    = None
    _redis_available = False


# ── Publish ────────────────────────────────────────────────────────────────────

async def publish_alert(
    store_id:   str,
    sku:        str,
    alert_type: str,
    payload:    Dict[str, Any],
) -> bool:
    """
    Publie une alerte sur le canal Redis 'alerts:{store_id}'.

    Args:
        store_id:   identifiant du magasin (ex: "I63")
        sku:        SKU concerné
        alert_type: "CRITICAL_STOCK" | "EXPEDITE" | "STOCKOUT_IMMINENT"
        payload:    données additionnelles (action, risk_level, days_of_stock…)

    Returns:
        True si publiée avec succès, False sinon (non-bloquant).
    """
    r = await _get_redis()
    if r is None:
        return False

    message = json.dumps(
        {
            "alert_type": alert_type,
            "store_id":   store_id,
            "sku":        sku,
            "timestamp":  datetime.utcnow().isoformat(),
            **payload,
        },
        ensure_ascii=False,
        default=str,
    )

    try:
        channel      = f"alerts:{store_id}"
        n_subscribers = await r.publish(channel, message)

        # Historique dans sorted set (score = unix timestamp, TTL 1h)
        ts = datetime.utcnow().timestamp()
        await r.zadd(f"alerts_history:{store_id}", {message: ts})
        await r.expire(f"alerts_history:{store_id}", 3600)

        logger.info(
            "[AlertBus] Publié — channel=%s type=%s sku=%s subscribers=%d",
            channel, alert_type, sku, n_subscribers,
        )
        return True

    except Exception as exc:
        logger.warning("[AlertBus] publish échoué pour %s/%s: %s", store_id, sku, exc)
        return False


async def get_alert_history(store_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Retourne les dernières alertes depuis le sorted set Redis.

    Args:
        store_id: identifiant du magasin
        limit:    nombre max d'alertes (les plus récentes)

    Returns:
        Liste de dicts, ordonnée du plus récent au plus ancien.
    """
    r = await _get_redis()
    if r is None:
        return []
    try:
        raw_items = await r.zrevrange(
            f"alerts_history:{store_id}", 0, limit - 1
        )
        alerts = []
        for item in raw_items:
            try:
                alerts.append(json.loads(item))
            except json.JSONDecodeError:
                pass
        return alerts
    except Exception as exc:
        logger.warning("[AlertBus] get_alert_history failed: %s", exc)
        return []


# ── Dispatch depuis décisions inventory ───────────────────────────────────────

async def dispatch_inventory_alerts(
    decisions: List[Dict[str, Any]],
    store_id:  str,
) -> None:
    """
    Dispatche les alertes critiques depuis la liste de décisions inventory.

    Appelé après inventory_branch pour chaque SKU critique.
    Non-bloquant : les échecs Redis sont silencieusement ignorés.

    Args:
        decisions: liste de dicts décision (champs: sku, action, risk_level, …)
        store_id:  identifiant du magasin
    """
    tasks = []
    for dec in decisions:
        risk_level    = dec.get("risk_level", "")
        action        = dec.get("action", "")
        days_of_stock = dec.get("days_of_stock_remaining")
        sku           = dec.get("sku", "")

        should_alert = (
            risk_level == "CRITICAL"
            or action == "EXPEDITE"
            or (days_of_stock is not None and float(days_of_stock) < 3)
        )

        if not should_alert:
            continue

        if action == "EXPEDITE":
            alert_type = "EXPEDITE"
        elif days_of_stock is not None and float(days_of_stock) < 3:
            alert_type = "STOCKOUT_IMMINENT"
        else:
            alert_type = "CRITICAL_STOCK"

        tasks.append(
            publish_alert(
                store_id=store_id,
                sku=sku,
                alert_type=alert_type,
                payload={
                    "action":            action,
                    "risk_level":        risk_level,
                    "days_of_stock":     days_of_stock,
                    "order_qty":         dec.get("order_qty"),
                    "urgency":           dec.get("urgency"),
                    "escalate_to_human": dec.get("escalate_to_human"),
                    "confidence":        dec.get("confidence"),
                },
            )
        )

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        published = sum(1 for r in results if r is True)
        logger.info(
            "[AlertBus] Batch dispatch — store=%s: %d/%d alertes publiées",
            store_id, published, len(tasks),
        )


# ── Sync wrapper (pour orchestrateur synchrone) ───────────────────────────────

def dispatch_alerts_sync(
    decisions: List[Dict[str, Any]],
    store_id:  str,
) -> None:
    """
    Version synchrone de dispatch_inventory_alerts.

    Crée une boucle asyncio temporaire si nécessaire.
    Utilise l'event loop existant si disponible (ex: FastAPI).
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Dans FastAPI / contexte async existant
            asyncio.ensure_future(dispatch_inventory_alerts(decisions, store_id))
        else:
            loop.run_until_complete(dispatch_inventory_alerts(decisions, store_id))
    except RuntimeError:
        # Pas d'event loop — crée une temporaire
        asyncio.run(dispatch_inventory_alerts(decisions, store_id))
    except Exception as exc:
        logger.warning("[AlertBus] dispatch_alerts_sync failed: %s", exc)
