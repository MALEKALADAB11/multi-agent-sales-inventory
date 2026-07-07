"""
Decision Agent Tools
=====================
Data access for the decision agent.

Two responsibilities:
  1. get_active_objective()          — reads inventory.business_objectives
  2. action_to_recommendation_type() — maps ORDER/EXPEDITE → 'reorder',
                                       HOLD/MONITOR → None (no DB row written)

build_recommendation_text() is intentionally NOT here. The recommendation
text is produced by the agent itself (LLM or rule-based fallback in nodes.py)
as part of the decision output. It is situation-specific every time.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from app.inventory.repositories.inventory_repo import SyncInventoryRepo
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False


# ── Confidence string → float (NUMERIC(5,4) column) ──────────────────────────
_CONFIDENCE_FLOAT = {
    "high":   0.9,
    "medium": 0.6,
    "low":    0.3,
}

# ── Action → DB recommendation_type ──────────────────────────────────────────
_ACTION_TO_RECO_TYPE = {
    "ORDER":    "reorder",
    "EXPEDITE": "reorder",   # urgency is captured inside recommendation_text
    "HOLD":     None,        # no DB row — HOLD is not a recommendation
    "MONITOR":  None,        # no DB row — MONITOR is not a recommendation
}


def get_active_objective() -> Optional[dict]:
    """
    Read the current active business objective from DB.
    Returns the full row dict, or None if DB unavailable / no active row.
    """
    if not _DB_AVAILABLE:
        return None
    try:
        return SyncInventoryRepo.get_active_objective()
    except Exception as e:
        logger.warning("[Decision/tools] get_active_objective failed: %s", e)
        return None


def action_to_recommendation_type(action: str) -> Optional[str]:
    """
    Map internal action string to inventory.recommendations.recommendation_type.
    Returns None for HOLD / MONITOR — those produce no DB row.
    """
    return _ACTION_TO_RECO_TYPE.get(action.upper())


def confidence_to_float(confidence: str) -> float:
    """
    Convert 'high' / 'medium' / 'low' to the NUMERIC(5,4) the DB column expects.
    Defaults to 0.3 (low) for unrecognised strings.
    """
    return _CONFIDENCE_FLOAT.get(str(confidence).lower(), 0.3)
