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

# ── Clients Redis par event loop ───────────────────────────────────────────────
# Un client asyncio Redis est lié à SA boucle : le partager entre la boucle
# FastAPI et les boucles temporaires de dispatch_alerts_sync provoquait des
# publications perdues (« Connection closed by server ») et un spam
# « RuntimeError: Event loop is closed » quand le GC détruisait les connexions
# d'une boucle fermée. WeakKeyDictionary : les entrées des boucles mortes
# disparaissent d'elles-mêmes.

import weakref

_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]" = weakref.WeakKeyDictionary()
_failed:  "weakref.WeakSet[asyncio.AbstractEventLoop]" = weakref.WeakSet()


async def _get_redis():
    """Client Redis async de la boucle courante (lazy-init, un par loop)."""
    loop = asyncio.get_running_loop()

    client = _clients.get(loop)
    if client is not None:
        return client
    if loop in _failed:
        return None

    try:
        import redis.asyncio as aioredis  # type: ignore

        host     = os.getenv("REDIS_HOST", "localhost")
        port     = int(os.getenv("REDIS_PORT", "6379"))
        db       = int(os.getenv("REDIS_DB", "0"))
        password = os.getenv("REDIS_PASSWORD") or None

        client = aioredis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await client.ping()
        _clients[loop] = client
        logger.info("[AlertBus] Redis connecté — %s:%d/db%d", host, port, db)
        return client

    except ImportError:
        logger.warning("[AlertBus] redis.asyncio non installé — alertes désactivées")
    except Exception as exc:
        logger.warning("[AlertBus] Redis indisponible (%s) — alertes non publiées", exc)

    _failed.add(loop)
    return None


def _reset_client() -> None:
    """Réinitialise les clients (tests unitaires)."""
    _clients.clear()
    _failed.clear()


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
    # Exécuté depuis les workers du pipeline (threads sans event loop).
    # Chaque dispatch crée SA boucle ; _get_redis fournit un client propre à
    # cette boucle (jamais partagé avec la boucle FastAPI), fermé ici AVANT
    # la fin de la boucle — sinon le GC détruit la connexion après coup et
    # spamme « RuntimeError: Event loop is closed ». L'attente est bornée :
    # le pipeline ne dépend jamais de Redis.
    import threading

    def _run() -> None:
        async def _dispatch_and_close() -> None:
            try:
                await dispatch_inventory_alerts(decisions, store_id)
            finally:
                client = _clients.pop(asyncio.get_running_loop(), None)
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:
                        pass
        try:
            asyncio.run(_dispatch_and_close())
        except Exception as exc:
            logger.warning("[AlertBus] dispatch_alerts_sync: %s", exc)

    t = threading.Thread(target=_run, daemon=True, name="alertbus-dispatch")
    t.start()
    t.join(timeout=15)
    if t.is_alive():
        logger.warning("[AlertBus] dispatch >15s — le pipeline continue sans attendre")
