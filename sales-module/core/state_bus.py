"""
state_bus.py — Shared State Bus (Redis Streams) + UnifiedCycleState
====================================================================
Patterns appliqués :
  • Shared State Bus   : Redis Streams comme bus d'événements cross-agents
  • Fan-Out / Fan-In   : inventory_snapshot publié → lu par SalesCycle
  • Unified State      : UnifiedCycleState étend SalesAgentState + inventory + critique + HITL

Redis Streams utilisés :
  cycle_state:{store_id}        — état complet du cycle (Analyst → Stratège → Coach)
  events:{store_id}             — événements temps réel (alertes, décisions)
  inventory_snapshot:{store_id} — snapshot stock publié par InventoryOrchestrator
  feedback:{store_id}           — retours CA réels pour Feedback Collector
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# UnifiedCycleState — extension de SalesAgentState avec champs v5
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class InventorySnapshot:
    """Snapshot stock temps réel publié par InventoryOrchestrator."""
    store_id:       str
    timestamp:      str
    skus:           Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # {sku: {stock_qty, risk_level, product_name, reorder_point, days_remaining}}
    critical_count: int   = 0
    rupture_count:  int   = 0
    healthy_count:  int   = 0
    total_skus:     int   = 0
    source:         str   = "inventory_orchestrator"


@dataclass
class CritiqueResult:
    """Résultat du Critique Agent (5 checks constitutionnels)."""
    passed:              bool
    score:               float       # 0.0 → 1.0 (seuil minimum : 0.8)
    checks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # {check_name: {passed, score, reason}}
    feedback:            str   = ""
    revision_required:   bool  = False
    timestamp:           str   = ""


@dataclass
class HumanGateResult:
    """Résultat de la validation humaine (Human-in-the-Loop)."""
    required:    bool  = False
    approved:    bool  = False
    approver:    str   = "auto"
    timeout_hit: bool  = False
    reason:      str   = ""
    timestamp:   str   = ""


@dataclass
class UnifiedCycleState:
    """
    État étendu pour le cycle v5.
    Utilisé comme overlay sur SalesAgentState — ne remplace pas TypedDict,
    sert à transporter les champs v5 sérialisés dans state["v5_extension"].
    """
    cycle_id:   str
    store_id:   str
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # ── Inventory cross-reference (Phase 2) ──────────────────────────────────
    inventory_snapshot:      Optional[InventorySnapshot] = None
    stock_filtered_products: List[str]                   = field(default_factory=list)
    # liste des SKUs avec stock > 0 disponibles pour recommendation

    # ── Critique Agent (Phase 3) ─────────────────────────────────────────────
    critique_result: Optional[CritiqueResult] = None
    critique_cycles: int                      = 0  # nb révisions itératives

    # ── Human-in-the-Loop Gate (Phase 4) ────────────────────────────────────
    hitl_result: Optional[HumanGateResult] = None

    # ── Procedural Memory — règles apprises (Phase 5) ───────────────────────
    procedural_rules: List[Dict[str, Any]] = field(default_factory=list)
    # [{"rule": "...", "confidence": 0.87, "source": "feedback"}]

    # ── Supervisor routing (Phase 4) ─────────────────────────────────────────
    supervisor_routing: Dict[str, Any] = field(default_factory=dict)
    # {"next_agent": "critique", "reason": "gap > 70%", "circuit_state": {...}}

    # ── Circuit Breakers snapshot ────────────────────────────────────────────
    circuit_states: Dict[str, str] = field(default_factory=dict)
    # {"analyst": "CLOSED", "stratege": "CLOSED", ...}

    # ── Feedback (Phase 5) ───────────────────────────────────────────────────
    feedback_score: Optional[float] = None
    ca_reel:        Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Convertir les dataclasses imbriquées en dict (asdict le fait déjà)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UnifiedCycleState":
        inv = data.pop("inventory_snapshot", None)
        cr  = data.pop("critique_result", None)
        hr  = data.pop("hitl_result", None)

        obj = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

        if inv and isinstance(inv, dict):
            obj.inventory_snapshot = InventorySnapshot(**inv)
        if cr and isinstance(cr, dict):
            obj.critique_result = CritiqueResult(**cr)
        if hr and isinstance(hr, dict):
            obj.hitl_result = HumanGateResult(**hr)

        return obj


# ═══════════════════════════════════════════════════════════════════════════════
# StateBus — Redis Streams
# ═══════════════════════════════════════════════════════════════════════════════

class StateBus:
    """
    Bus d'état partagé via Redis Streams.

    Streams :
      cycle_state:{store_id}        TTL 4h  — état cycle LangGraph
      events:{store_id}             TTL 24h — événements temps réel
      inventory_snapshot:{store_id} TTL 10min — stock snapshot
      feedback:{store_id}           TTL 30j  — retours CA

    Pattern utilisé : publish() écrit dans le stream, subscribe() lit les N derniers.
    Les agents lisent inventory_snapshot au début du cycle via read_inventory_snapshot().
    """

    STREAM_CYCLE       = "cycle_state:{store_id}"
    STREAM_EVENTS      = "events:{store_id}"
    STREAM_INV         = "inventory_snapshot:{store_id}"
    STREAM_FEEDBACK    = "feedback:{store_id}"

    TTL_CYCLE_S        = 4 * 3600      # 4h
    TTL_EVENTS_S       = 24 * 3600     # 24h
    TTL_INV_S          = 10 * 60       # 10 min
    TTL_FEEDBACK_S     = 30 * 24 * 3600  # 30 jours

    MAX_LEN_CYCLE      = 50
    MAX_LEN_EVENTS     = 200
    MAX_LEN_INV        = 20
    MAX_LEN_FEEDBACK   = 500

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self._url    = redis_url
        self._client = None

    # ── Connection ──────────────────────────────────────────────────────────

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
                logger.info("[StateBus] Redis connecté : %s", self._url)
            except Exception as e:
                logger.warning("[StateBus] Redis indisponible : %s — mode dégradé", e)
                self._client = None
        return self._client

    def _stream(self, template: str, store_id: str) -> str:
        return template.format(store_id=store_id)

    # ── Publish cycle state ──────────────────────────────────────────────────

    def publish_cycle_state(
        self,
        state: Dict[str, Any],
        store_id: str,
        phase: str = "unknown",
    ) -> bool:
        """Publie l'état du cycle courant dans Redis Streams."""
        client = self._get_client()
        if client is None:
            return False
        try:
            stream = self._stream(self.STREAM_CYCLE, store_id)
            payload = {
                "phase":      phase,
                "cycle_id":   state.get("cycle_id", ""),
                "store_id":   store_id,
                "timestamp":  datetime.utcnow().isoformat(),
                "urgency":    state.get("urgency_level", "LOW"),
                "gap_pct":    str(state.get("gap_objectif", 0.0)),
                "data":       json.dumps(self._slim_state(state), ensure_ascii=False)[:8000],
            }
            client.xadd(stream, payload, maxlen=self.MAX_LEN_CYCLE)
            client.expire(stream, self.TTL_CYCLE_S)
            return True
        except Exception as e:
            logger.debug("[StateBus] publish_cycle_state: %s", e)
            return False

    # ── Publish inventory snapshot ───────────────────────────────────────────

    def publish_inventory_snapshot(
        self,
        snapshot: InventorySnapshot,
    ) -> bool:
        """Publié par InventoryOrchestrator après le batch analysis."""
        client = self._get_client()
        if client is None:
            return False
        try:
            stream = self._stream(self.STREAM_INV, snapshot.store_id)
            payload = {
                "store_id":      snapshot.store_id,
                "timestamp":     snapshot.timestamp,
                "critical_count":str(snapshot.critical_count),
                "rupture_count": str(snapshot.rupture_count),
                "healthy_count": str(snapshot.healthy_count),
                "total_skus":    str(snapshot.total_skus),
                "data":          json.dumps(snapshot.skus, ensure_ascii=False)[:16000],
            }
            client.xadd(stream, payload, maxlen=self.MAX_LEN_INV)
            client.expire(stream, self.TTL_INV_S)
            logger.info(
                "[StateBus] inventory_snapshot publié : %s | %d SKUs (%d critical, %d rupture)",
                snapshot.store_id, snapshot.total_skus,
                snapshot.critical_count, snapshot.rupture_count,
            )
            return True
        except Exception as e:
            logger.debug("[StateBus] publish_inventory_snapshot: %s", e)
            return False

    # ── Read inventory snapshot ──────────────────────────────────────────────

    def read_inventory_snapshot(self, store_id: str) -> Optional[InventorySnapshot]:
        """
        Lit le dernier snapshot stock depuis Redis.
        Retourne None si indisponible (mode dégradé : Stratège travaille sans stock info).
        """
        client = self._get_client()
        if client is None:
            return None
        try:
            stream  = self._stream(self.STREAM_INV, store_id)
            entries = client.xrevrange(stream, count=1)
            if not entries:
                return None

            _, data = entries[0]
            skus = json.loads(data.get("data", "{}"))
            return InventorySnapshot(
                store_id       = data.get("store_id", store_id),
                timestamp      = data.get("timestamp", ""),
                skus           = skus,
                critical_count = int(data.get("critical_count", 0)),
                rupture_count  = int(data.get("rupture_count", 0)),
                healthy_count  = int(data.get("healthy_count", 0)),
                total_skus     = int(data.get("total_skus", 0)),
            )
        except Exception as e:
            logger.debug("[StateBus] read_inventory_snapshot: %s", e)
            return None

    # ── Publish event ────────────────────────────────────────────────────────

    def publish_event(
        self,
        store_id: str,
        event_type: str,
        data: Dict[str, Any],
        level: str = "info",
    ) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            stream = self._stream(self.STREAM_EVENTS, store_id)
            payload = {
                "type":      event_type,
                "level":     level,
                "store_id":  store_id,
                "timestamp": datetime.utcnow().isoformat(),
                "data":      json.dumps(data, ensure_ascii=False)[:4000],
            }
            client.xadd(stream, payload, maxlen=self.MAX_LEN_EVENTS)
            client.expire(stream, self.TTL_EVENTS_S)
            return True
        except Exception as e:
            logger.debug("[StateBus] publish_event: %s", e)
            return False

    # ── HITL (Human-in-the-Loop) gate ───────────────────────────────────────

    def request_human_approval(
        self,
        store_id: str,
        cycle_id: str,
        reason: str,
        strategie_actions: List[Dict[str, Any]],
        timeout_s: int = 120,
    ) -> HumanGateResult:
        """
        Publie une demande d'approbation humaine et attend la réponse.
        Timeout auto-approve après timeout_s secondes.
        """
        client = self._get_client()
        if client is None:
            return HumanGateResult(
                required=True, approved=True,
                approver="auto-timeout", reason="Redis indisponible",
                timestamp=datetime.utcnow().isoformat(),
            )

        approval_key = f"hitl:approval:{store_id}:{cycle_id}"
        request_key  = f"hitl:request:{store_id}:{cycle_id}"

        try:
            client.setex(request_key, timeout_s, json.dumps({
                "store_id":    store_id,
                "cycle_id":    cycle_id,
                "reason":      reason,
                "actions":     strategie_actions[:3],
                "expires_at":  time.time() + timeout_s,
            }, ensure_ascii=False))

            self.publish_event(store_id, "hitl_request", {
                "cycle_id": cycle_id,
                "reason":   reason,
                "timeout":  timeout_s,
            }, level="warning")

            logger.info("[StateBus] HITL demandé : %s/%s — timeout %ds", store_id, cycle_id, timeout_s)

            # Polling léger avec timeout
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                val = client.get(approval_key)
                if val:
                    decision = json.loads(val)
                    approved = decision.get("approved", False)
                    logger.info("[StateBus] HITL réponse reçue : approved=%s", approved)
                    return HumanGateResult(
                        required=True, approved=approved,
                        approver=decision.get("approver", "human"),
                        reason=decision.get("reason", ""),
                        timestamp=datetime.utcnow().isoformat(),
                    )
                time.sleep(2)

            # Timeout → auto-approve
            logger.warning("[StateBus] HITL timeout %ds → auto-approve", timeout_s)
            return HumanGateResult(
                required=True, approved=True,
                approver="auto-timeout", timeout_hit=True,
                reason=f"Timeout {timeout_s}s dépassé",
                timestamp=datetime.utcnow().isoformat(),
            )

        except Exception as e:
            logger.warning("[StateBus] HITL erreur : %s", e)
            return HumanGateResult(
                required=True, approved=True,
                approver="auto-error", reason=str(e),
                timestamp=datetime.utcnow().isoformat(),
            )

    # ── Feedback stream ──────────────────────────────────────────────────────

    def publish_feedback(
        self,
        store_id: str,
        cycle_id: str,
        ca_reel: float,
        ca_predit: float,
        conseil_applied: bool,
    ) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            stream = self._stream(self.STREAM_FEEDBACK, store_id)
            payload = {
                "cycle_id":       cycle_id,
                "store_id":       store_id,
                "ca_reel":        str(ca_reel),
                "ca_predit":      str(ca_predit),
                "conseil_applied":str(conseil_applied),
                "timestamp":      datetime.utcnow().isoformat(),
                "delta_pct":      str(round((ca_reel - ca_predit) / max(ca_predit, 1) * 100, 2)),
            }
            client.xadd(stream, payload, maxlen=self.MAX_LEN_FEEDBACK)
            client.expire(stream, self.TTL_FEEDBACK_S)
            return True
        except Exception as e:
            logger.debug("[StateBus] publish_feedback: %s", e)
            return False

    def read_recent_feedback(
        self,
        store_id: str,
        count: int = 30,
    ) -> List[Dict[str, Any]]:
        client = self._get_client()
        if client is None:
            return []
        try:
            stream  = self._stream(self.STREAM_FEEDBACK, store_id)
            entries = client.xrevrange(stream, count=count)
            results = []
            for _, data in entries:
                results.append({
                    "cycle_id":       data.get("cycle_id"),
                    "ca_reel":        float(data.get("ca_reel", 0)),
                    "ca_predit":      float(data.get("ca_predit", 0)),
                    "delta_pct":      float(data.get("delta_pct", 0)),
                    "conseil_applied":data.get("conseil_applied") == "True",
                    "timestamp":      data.get("timestamp"),
                })
            return results
        except Exception as e:
            logger.debug("[StateBus] read_recent_feedback: %s", e)
            return []

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _slim_state(state: Dict[str, Any]) -> Dict[str, Any]:
        """Sérialisation légère de l'état (champs clés seulement)."""
        return {
            "cycle_id":        state.get("cycle_id"),
            "store_id":        state.get("store_id"),
            "urgency_level":   state.get("urgency_level"),
            "gap_objectif":    state.get("gap_objectif"),
            "gap_amount":      state.get("gap_amount"),
            "strategie":       state.get("strategie"),
            "focus_produits":  state.get("focus_produits", [])[:5],
            "conseil_final":   (state.get("conseil_final") or "")[:300],
            "v5_extension":    state.get("v5_extension"),
        }

    def health_check(self) -> Dict[str, Any]:
        client = self._get_client()
        if client is None:
            return {"status": "unavailable", "url": self._url}
        try:
            client.ping()
            return {"status": "ok", "url": self._url}
        except Exception as e:
            return {"status": "error", "error": str(e)}


# ── Singleton ────────────────────────────────────────────────────────────────

_state_bus: Optional[StateBus] = None


def get_state_bus(redis_url: str = "redis://localhost:6379") -> StateBus:
    """Retourne le singleton StateBus."""
    global _state_bus
    if _state_bus is None:
        from core.config import get_config
        try:
            cfg = get_config()
            url = cfg.redis_url
        except Exception:
            url = redis_url
        _state_bus = StateBus(url)
    return _state_bus
