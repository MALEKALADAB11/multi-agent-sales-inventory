"""
circuit_breaker.py — CircuitBreaker par agent (Pattern v5)
============================================================
3 états : CLOSED (normal) → OPEN (en panne) → HALF_OPEN (test)
Threshold : 3 échecs consécutifs → OPEN
Timeout   : 60s avant passage HALF_OPEN
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int   = 3     # nb échecs consécutifs → OPEN
    success_threshold: int   = 1     # nb succès en HALF_OPEN → CLOSED
    timeout_seconds:   float = 60.0  # durée avant HALF_OPEN
    half_open_max:     int   = 1     # max appels simultanés en HALF_OPEN


@dataclass
class CircuitBreakerStats:
    consecutive_failures:  int   = 0
    consecutive_successes: int   = 0
    total_calls:           int   = 0
    total_failures:        int   = 0
    total_successes:       int   = 0
    last_failure_time:     float = 0.0
    last_success_time:     float = 0.0
    open_since:            float = 0.0


class CircuitBreakerOpen(Exception):
    """Levée quand le circuit est ouvert et que le fallback doit s'appliquer."""
    def __init__(self, agent_name: str, retry_in: float):
        self.agent_name = agent_name
        self.retry_in   = retry_in
        super().__init__(f"Circuit OPEN pour {agent_name} — retry dans {retry_in:.1f}s")


class CircuitBreaker:
    """
    Implémentation thread-safe du pattern Circuit Breaker pour les agents LangGraph.

    Usage :
        cb = CircuitBreaker("analyst")

        try:
            result = await cb.call(analyst_agent.run, state)
        except CircuitBreakerOpen:
            result = analyst_fallback(state)
    """

    def __init__(self, agent_name: str, config: Optional[CircuitBreakerConfig] = None):
        self.agent_name = agent_name
        self.config     = config or CircuitBreakerConfig()
        self._state     = CircuitState.CLOSED
        self._stats     = CircuitBreakerStats()
        self._lock      = asyncio.Lock()
        self._half_open_calls = 0

    # ── État public ─────────────────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def stats(self) -> CircuitBreakerStats:
        return self._stats

    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN

    def is_closed(self) -> bool:
        return self._state == CircuitState.CLOSED

    # ── Appel principal ─────────────────────────────────────────────────────

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Exécute func(*args, **kwargs) en appliquant la logique circuit breaker.
        Lève CircuitBreakerOpen si le circuit est OPEN et le timeout non expiré.
        """
        async with self._lock:
            await self._maybe_transition()

            if self._state == CircuitState.OPEN:
                retry_in = self._time_until_half_open()
                logger.warning(
                    "[CB:%s] Circuit OPEN — appel bloqué (retry dans %.1fs)",
                    self.agent_name, retry_in,
                )
                raise CircuitBreakerOpen(self.agent_name, retry_in)

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max:
                    raise CircuitBreakerOpen(self.agent_name, 0.0)
                self._half_open_calls += 1

        self._stats.total_calls += 1

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result

        except CircuitBreakerOpen:
            raise

        except Exception as exc:
            await self._on_failure(exc)
            raise

    # ── Transitions ─────────────────────────────────────────────────────────

    async def _maybe_transition(self) -> None:
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._stats.open_since
            if elapsed >= self.config.timeout_seconds:
                logger.info(
                    "[CB:%s] Timeout expiré (%.1fs) → HALF_OPEN",
                    self.agent_name, elapsed,
                )
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self._stats.consecutive_successes = 0

    async def _on_success(self) -> None:
        async with self._lock:
            self._stats.total_successes          += 1
            self._stats.consecutive_failures      = 0
            self._stats.consecutive_successes    += 1
            self._stats.last_success_time         = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls -= 1
                if self._stats.consecutive_successes >= self.config.success_threshold:
                    logger.info("[CB:%s] HALF_OPEN → CLOSED (succès)", self.agent_name)
                    self._state = CircuitState.CLOSED

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._stats.total_failures           += 1
            self._stats.consecutive_successes     = 0
            self._stats.consecutive_failures     += 1
            self._stats.last_failure_time         = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls -= 1
                logger.warning(
                    "[CB:%s] Échec en HALF_OPEN → retour OPEN",
                    self.agent_name,
                )
                self._open_circuit()

            elif (self._state == CircuitState.CLOSED and
                  self._stats.consecutive_failures >= self.config.failure_threshold):
                logger.error(
                    "[CB:%s] %d échecs consécutifs → OPEN | dernier: %s",
                    self.agent_name,
                    self._stats.consecutive_failures,
                    str(exc)[:120],
                )
                self._open_circuit()

    def _open_circuit(self) -> None:
        self._state                = CircuitState.OPEN
        self._stats.open_since     = time.monotonic()
        self._half_open_calls      = 0

    # ── Utilitaires ─────────────────────────────────────────────────────────

    def _time_until_half_open(self) -> float:
        elapsed = time.monotonic() - self._stats.open_since
        return max(0.0, self.config.timeout_seconds - elapsed)

    def reset(self) -> None:
        """Réinitialisation manuelle (tests, admin)."""
        self._state   = CircuitState.CLOSED
        self._stats   = CircuitBreakerStats()
        self._half_open_calls = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent":               self.agent_name,
            "state":               self._state.value,
            "consecutive_failures":self._stats.consecutive_failures,
            "total_calls":         self._stats.total_calls, 
            "total_failures":      self._stats.total_failures,
            "total_successes":     self._stats.total_successes,
            "time_until_retry":    self._time_until_half_open() if self.is_open() else 0.0,
        }


# ── Registry global ──────────────────────────────────────────────────────────

_registry: Dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    agent_name: str,
    config: Optional[CircuitBreakerConfig] = None,
) -> CircuitBreaker:
    """
    Retourne le CircuitBreaker singleton pour cet agent.
    Crée avec config par défaut si inexistant.
    """
    if agent_name not in _registry:
        _registry[agent_name] = CircuitBreaker(agent_name, config)
        logger.info("[CB] Nouveau CircuitBreaker enregistré : %s", agent_name)
    return _registry[agent_name]


def get_all_circuit_states() -> Dict[str, Dict[str, Any]]:
    """Snapshot de l'état de tous les circuit breakers (pour monitoring)."""
    return {name: cb.to_dict() for name, cb in _registry.items()}
