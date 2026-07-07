"""
bootstrap.py — Initialisation de l'orchestrateur Coach-Stratège.
Appelé une fois au démarrage dans lifespan() de main.py.
"""

import logging
from typing import Optional
from .coach_stratege_orchestrator import CoachStrategeOrchestrator

logger = logging.getLogger(__name__)

_orchestrator: Optional[CoachStrategeOrchestrator] = None


async def initialize_coach_stratege_orchestrator(
    timeout:     float = 30.0,
    max_retries: int   = 3,
    ttl_cache:   int   = 1800,
) -> CoachStrategeOrchestrator:
    """
    Compile le Stratège et crée l'orchestrateur.
    Appeler dans lifespan() avant yield.
    """
    global _orchestrator

    logger.info("[BOOTSTRAP] Initialisation orchestrateur Coach-Stratège...")

    logger.info("[BOOTSTRAP] Compilation agent Stratège...")
    from app.sales.coaching.agents.stratege.agent import compile_stratege_agent
    stratege = compile_stratege_agent()
    logger.info("[BOOTSTRAP] ✓ Agent Stratège compilé")

    logger.info(
        "[BOOTSTRAP] Création orchestrateur (timeout=%ss, retries=%d, cache=%dmin)...",
        timeout, max_retries, ttl_cache // 60,
    )
    _orchestrator = CoachStrategeOrchestrator(
        stratege_agent = stratege,
        timeout        = timeout,
        max_retries    = max_retries,
        ttl_cache      = ttl_cache,
    )
    logger.info("[BOOTSTRAP] ✓ Orchestrateur initialisé et accessible")
    logger.info("[BOOTSTRAP] Cache stats: %s", _orchestrator.get_stats())
    logger.info("[BOOTSTRAP] ✓ Orchestrateur Coach-Stratège prêt")

    return _orchestrator


def get_orchestrator() -> CoachStrategeOrchestrator:
    """
    Retourne l'orchestrateur initialisé.
    Lève RuntimeError si initialize_coach_stratege_orchestrator() n'a pas été appelé.
    """
    if _orchestrator is None:
        raise RuntimeError(
            "Orchestrateur non initialisé. "
            "Appeler initialize_coach_stratege_orchestrator() dans lifespan()."
        )
    return _orchestrator
