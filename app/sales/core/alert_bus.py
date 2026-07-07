"""
alert_bus.py — Redis Pub/Sub Alert Bus (V5 FINAL)
==================================================
Implémente le système d'alertes temps réel défini dans
ARCHITECTURE_MULTI_AGENT_V5_FINAL.md — Section 5

Channels Redis Pub/Sub :
  alerts:store:{store_id}:stock   ← alertes inventaire (DecisionAgent)
  alerts:store:{store_id}:sales   ← alertes ventes (AnalystAgent)
  alerts:store:{store_id}:cross   ← combinaisons sales+stock (StrategistAgent)
  events:cycle:{cycle_id}         ← events orchestration

CoachAgent souscrit à tous les channels et évalue la priorité de chaque alerte.
Latence cible : t=0ms → t=300ms sur le frontend conseiller.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AlertType(str, Enum):
    STOCK    = "stock"
    SALES    = "sales"
    CROSS    = "cross"
    CYCLE    = "cycle"


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH     = "high"
    MEDIUM   = "medium"
    LOW      = "low"


def _channel_stock(store_id: str) -> str:
    return f"alerts:store:{store_id}:stock"

def _channel_sales(store_id: str) -> str:
    return f"alerts:store:{store_id}:sales"

def _channel_cross(store_id: str) -> str:
    return f"alerts:store:{store_id}:cross"

def _channel_cycle(cycle_id: str) -> str:
    return f"events:cycle:{cycle_id}"


# ═══════════════════════════════════════════════════════════════════════════════
# AlertBus — Publisher
# ═══════════════════════════════════════════════════════════════════════════════

class AlertBus:
    """
    Bus d'alertes Redis Pub/Sub.
    Les agents publient des alertes sur leurs channels respectifs.
    CoachAgent souscrit et réagit.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self._url    = redis_url
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import redis
                self._client = redis.from_url(
                    self._url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
                self._client.ping()
                logger.info("[AlertBus] Redis Pub/Sub connecté : %s", self._url)
            except Exception as e:
                logger.warning("[AlertBus] Redis indisponible : %s", e)
                self._client = None
        return self._client

    # ── Évaluation de priorité (Section 5.3 du doc) ─────────────────────────

    @staticmethod
    def evaluate_priority(alert: Dict[str, Any]) -> str:
        """
        Priorité = f(severity, business_impact, time_to_act, advisor_state)
        Reproduit exactement la logique de evaluate_priority() du document v5 FINAL.
        Tout est dynamique — aucun SKU ou montant codé en dur.
        """
        score = 0

        sev = alert.get("severity", "medium")
        if sev == "critical":   score += 40
        elif sev == "high":     score += 25
        elif sev == "medium":   score += 10

        if alert.get("is_top_seller"):             score += 20
        if alert.get("revenue_at_risk", 0) > 1000: score += 15

        hours_to_act = alert.get("hours_to_act", 24)
        if hours_to_act < 2:    score += 20
        elif hours_to_act < 8:  score += 10

        if alert.get("advisor_idle"):  score += 5

        if score >= 70:    return "URGENT"
        elif score >= 50:  return "HIGH"
        elif score >= 30:  return "MEDIUM"
        else:              return "LOW"

    # ── Publication ──────────────────────────────────────────────────────────

    def publish_stock_alert(
        self,
        store_id:       str,
        sku:            str,
        product_name:   str,
        stock_qty:      int,          # dynamique depuis DB
        risk_level:     str,
        days_to_stockout: float,      # dynamique depuis InventoryAnalysis
        revenue_at_risk:  float = 0.0,
        is_top_seller:    bool  = False,
        alternative_sku:  Optional[str] = None,
        cycle_id:         str  = "",
    ) -> bool:
        """
        Publié par DecisionAgent quand risk=CRITICAL ou action=EXPEDITE.
        Toutes les valeurs sont dynamiques — extraites de l'analyse inventory.
        """
        client = self._get_client()
        if client is None:
            return False

        hours_to_act = days_to_stockout * 24 if days_to_stockout else 24

        alert = {
            "type":             AlertType.STOCK.value,
            "severity":         "critical" if risk_level in ("CRITICAL", "critical") else "high",
            "store_id":         store_id,
            "sku":              str(sku),
            "product_name":     product_name,
            "stock_qty":        stock_qty,          # dynamique
            "risk_level":       risk_level,
            "days_to_stockout": days_to_stockout,   # dynamique
            "revenue_at_risk":  revenue_at_risk,
            "is_top_seller":    is_top_seller,
            "hours_to_act":     hours_to_act,
            "alternative_sku":  alternative_sku,
            "cycle_id":         cycle_id,
            "timestamp":        datetime.utcnow().isoformat(),
        }

        channel  = _channel_stock(store_id)
        priority = self.evaluate_priority(alert)
        alert["priority"] = priority

        try:
            n = client.publish(channel, json.dumps(alert, ensure_ascii=False))
            logger.info(
                "[AlertBus] STOCK alert publiée : %s | %s (stock=%d) | priority=%s | %d subscriber(s)",
                store_id, product_name, stock_qty, priority, n,
            )
            return True
        except Exception as e:
            logger.debug("[AlertBus] publish_stock_alert: %s", e)
            return False

    def publish_sales_alert(
        self,
        store_id:     str,
        urgency:      str,
        gap_amount:   float,        # dynamique depuis AnalystAgent
        gap_pct:      float,        # dynamique
        hours_remaining: float,
        cycle_id:     str  = "",
        advisor_idle: bool = False,
    ) -> bool:
        """
        Publié par AnalystAgent quand urgency=CRITICAL ou gap anormal.
        Les montants viennent du calcul dynamique du gap CA.
        """
        client = self._get_client()
        if client is None:
            return False

        alert = {
            "type":            AlertType.SALES.value,
            "severity":        "critical" if urgency == "CRITICAL" else "high" if urgency == "HIGH" else "medium",
            "store_id":        store_id,
            "urgency_level":   urgency,
            "gap_amount":      gap_amount,         # dynamique
            "gap_pct":         gap_pct,            # dynamique
            "hours_remaining": hours_remaining,    # dynamique
            "hours_to_act":    hours_remaining,
            "revenue_at_risk": gap_amount,
            "advisor_idle":    advisor_idle,
            "cycle_id":        cycle_id,
            "timestamp":       datetime.utcnow().isoformat(),
        }

        channel  = _channel_sales(store_id)
        priority = self.evaluate_priority(alert)
        alert["priority"] = priority

        try:
            n = client.publish(channel, json.dumps(alert, ensure_ascii=False))
            logger.info(
                "[AlertBus] SALES alert publiée : %s | gap=%.1f%% (%.0f TND) | priority=%s | %d sub(s)",
                store_id, gap_pct, gap_amount, priority, n,
            )
            return True
        except Exception as e:
            logger.debug("[AlertBus] publish_sales_alert: %s", e)
            return False

    def publish_cross_alert(
        self,
        store_id:     str,
        title:        str,
        message:      str,
        severity:     str = "medium",
        data:         Optional[Dict[str, Any]] = None,
        cycle_id:     str = "",
    ) -> bool:
        """
        Publié par StrategistAgent pour les opportunités météo/event croisées.
        Exemple : "Pluie → pousse accessoires résistants à l'eau"
        """
        client = self._get_client()
        if client is None:
            return False

        alert = {
            "type":      AlertType.CROSS.value,
            "severity":  severity,
            "store_id":  store_id,
            "title":     title,
            "message":   message,
            "data":      data or {},
            "cycle_id":  cycle_id,
            "timestamp": datetime.utcnow().isoformat(),
        }
        alert["priority"] = self.evaluate_priority(alert)

        try:
            n = client.publish(
                _channel_cross(store_id),
                json.dumps(alert, ensure_ascii=False),
            )
            logger.info("[AlertBus] CROSS alert publiée : %s | '%s' | %d sub(s)", store_id, title[:60], n)
            return True
        except Exception as e:
            logger.debug("[AlertBus] publish_cross_alert: %s", e)
            return False

    def publish_cycle_event(
        self,
        cycle_id: str,
        event:    str,
        data:     Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Événements d'orchestration : AnalysisComplete, StrategyReady, etc."""
        client = self._get_client()
        if client is None:
            return False

        payload = {
            "event":     event,
            "cycle_id":  cycle_id,
            "data":      data or {},
            "timestamp": datetime.utcnow().isoformat(),
        }

        try:
            client.publish(_channel_cycle(cycle_id), json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as e:
            logger.debug("[AlertBus] publish_cycle_event: %s", e)
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# AsyncAlertListener — Subscriber pour CoachAgent
# ═══════════════════════════════════════════════════════════════════════════════

class AsyncAlertListener:
    """
    Subscriber async utilisé par CoachAgent pour écouter les alertes Redis.

    Extrait du document v5 FINAL Section 5.1 :
      async def listen_alerts(store_id):
          async for msg in redis.subscribe(f"alerts:store:{store_id}:*"):
              priority = evaluate_priority(msg)
              context = await enrich_with_context(msg)
              if priority >= "HIGH":
                  await push_to_advisor(context)
              else:
                  await queue_for_batch(context)

    Implémentation via aioredis (async).
    """

    URGENT_PRIORITIES = {"URGENT", "HIGH"}

    def __init__(self, store_id: str, redis_url: str = "redis://localhost:6379"):
        self.store_id  = store_id
        self._url      = redis_url
        self._running  = False
        self._channels = [
            _channel_stock(store_id),
            _channel_sales(store_id),
            _channel_cross(store_id),
        ]

    async def listen(
        self,
        on_urgent:  Callable,
        on_normal:  Optional[Callable] = None,
        loop_sleep: float = 0.05,
    ) -> None:
        """
        Écoute les alertes en continu.
        Appelle on_urgent(alert) pour URGENT/HIGH, on_normal(alert) pour MEDIUM/LOW.
        """
        try:
            import aioredis
            redis = await aioredis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=2,
            )
        except ImportError:
            logger.warning("[AsyncAlertListener] aioredis non installé — utilisation de redis-py sync")
            await self._listen_sync(on_urgent, on_normal)
            return
        except Exception as e:
            logger.warning("[AsyncAlertListener] Redis indisponible : %s", e)
            return

        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(*self._channels)
            logger.info(
                "[AsyncAlertListener] Souscrit à %d channels pour store=%s",
                len(self._channels), self.store_id,
            )
            self._running = True

            async for message in pubsub.listen():
                if not self._running:
                    break
                if message["type"] != "message":
                    continue

                try:
                    alert    = json.loads(message["data"])
                    priority = AlertBus.evaluate_priority(alert)
                    alert["priority"] = priority

                    if priority in self.URGENT_PRIORITIES:
                        await on_urgent(alert)
                    elif on_normal:
                        await on_normal(alert)

                except json.JSONDecodeError:
                    logger.debug("[AsyncAlertListener] Message invalide ignoré")
                except Exception as exc:
                    logger.warning("[AsyncAlertListener] Erreur traitement alert : %s", exc)

                await asyncio.sleep(loop_sleep)

        finally:
            await pubsub.unsubscribe(*self._channels)
            await redis.close()

    async def _listen_sync(
        self,
        on_urgent: Callable,
        on_normal: Optional[Callable],
    ) -> None:
        """Fallback sync avec polling léger (redis-py)."""
        try:
            import redis as _redis
            client = _redis.from_url(self._url, decode_responses=True)
            pubsub = client.pubsub()
            pubsub.subscribe(*self._channels)
            self._running = True

            while self._running:
                msg = pubsub.get_message(timeout=0.05)
                if msg and msg["type"] == "message":
                    try:
                        alert    = json.loads(msg["data"])
                        priority = AlertBus.evaluate_priority(alert)
                        alert["priority"] = priority

                        coro = on_urgent(alert) if priority in self.URGENT_PRIORITIES else (on_normal(alert) if on_normal else None)
                        if coro:
                            await coro
                    except Exception as exc:
                        logger.warning("[AsyncAlertListener sync] %s", exc)

                await asyncio.sleep(0.05)

        except Exception as e:
            logger.warning("[AsyncAlertListener sync] Erreur : %s", e)

    def stop(self) -> None:
        self._running = False


# ── Singleton ────────────────────────────────────────────────────────────────

_alert_bus: Optional[AlertBus] = None


def get_alert_bus(redis_url: str = "redis://localhost:6379") -> AlertBus:
    global _alert_bus
    if _alert_bus is None:
        try:
            from app.sales.core.config import get_config
            url = get_config().redis_url
        except Exception:
            url = redis_url
        _alert_bus = AlertBus(url)
    return _alert_bus
