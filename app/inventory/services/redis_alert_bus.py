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
from concurrent.futures import TimeoutError as FutureTimeoutError
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


# ── Persistent background event loop ───────────────────────────────────────────
#
# One loop, one thread, started lazily on first use and kept alive for the
# life of the process. _get_redis() / publish_alert() / etc. always run on
# THIS loop's thread, so _redis_client is never touched by two loops at
# once — no more races. Callers on other threads (orchestrator workers)
# hand off work via asyncio.run_coroutine_threadsafe() instead of each
# spinning up their own loop.

import threading as _threading

_bg_loop:   Optional[asyncio.AbstractEventLoop] = None
_bg_thread: Optional[_threading.Thread] = None
_bg_lock = _threading.Lock()


def _ensure_background_loop() -> asyncio.AbstractEventLoop:
    """Démarre (une seule fois) le loop d'arrière-plan singleton et le retourne."""
    global _bg_loop, _bg_thread

    if _bg_loop is not None:
        return _bg_loop

    with _bg_lock:
        if _bg_loop is not None:
            return _bg_loop

        ready = _threading.Event()

        def _run_loop() -> None:
            global _bg_loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _bg_loop = loop
            ready.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        _bg_thread = _threading.Thread(
            target=_run_loop, daemon=True, name="alertbus-loop"
        )
        _bg_thread.start()

        if not ready.wait(timeout=5):
            logger.error("[AlertBus] background loop failed to start in time")

        return _bg_loop


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

    Exécutée depuis les workers du pipeline (threads sans event loop).
    Schedule le dispatch sur LE loop d'arrière-plan partagé et persistant
    (voir _ensure_background_loop) au lieu de créer sa propre boucle+thread
    à chaque appel.

    Historique : la version précédente créait un thread + une boucle asyncio
    ENTIÈREMENT NOUVELLE à chaque appel, et réinitialisait le client Redis
    global au début/à la fin de chacune. Avec 30-50 SKUs alert-worthy
    dispatchés quasi simultanément par les workers de l'orchestrateur, on
    avait autant de boucles concurrentes qui se marchaient dessus sur LE
    MÊME client global — d'où les erreurs "attached to a different loop",
    les connexions fermées après la fermeture de leur boucle propriétaire,
    et des dispatches qui restaient bloqués jusqu'au timeout de join(15s)
    à chaque fois. Un seul loop persistant élimine la course.

    Non-bloquant au-delà de 15s : le pipeline ne dépend jamais de Redis.
    """
    try:
        loop = _ensure_background_loop()
        future = asyncio.run_coroutine_threadsafe(
            dispatch_inventory_alerts(decisions, store_id), loop
        )
        future.result(timeout=15)
    except FutureTimeoutError:
        logger.warning("[AlertBus] dispatch >15s — le pipeline continue sans attendre")
    except Exception as exc:
        logger.warning("[AlertBus] dispatch_alerts_sync: %s", exc)