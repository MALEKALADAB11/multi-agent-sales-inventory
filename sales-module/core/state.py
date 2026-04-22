from typing import TypedDict, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CycleMetrics:
    cycle_id:      str
    started_at:    datetime
    total_ms:      int  = 0
    agents_called: list = field(default_factory=list)
    errors:        list = field(default_factory=list)


class AgentState(TypedDict):
    # ── Identité cycle ──────────────────────────────
    store_id:      str
    cycle_id:      str
    triggered_by:  str

    # ── Data brute ──────────────────────────────────
    pos_data:      dict

    # ── APP02 écrit ces champs ──────────────────────
    ecart_objectif:  float
    niveau_urgence:  str
    forecast_eod:    float
    forecast_ci_low: float
    forecast_ci_high:float
    forecast_mape:   float

    # ── Champs suivants (APP05, APP07...) ───────────
    context_data:    dict
    strategie:       Optional[str]
    conseil_final:   Optional[str]

    # ── Meta ────────────────────────────────────────
    errors:  list
    metrics: dict


def initial_state(
    store_id:     str,
    cycle_id:     str,
    triggered_by: str
) -> AgentState:
    return AgentState(
        store_id       = store_id,
        cycle_id       = cycle_id,
        triggered_by   = triggered_by,
        pos_data       = {},
        ecart_objectif = 0.0,
        niveau_urgence = "LOW",
        forecast_eod   = 0.0,
        forecast_ci_low  = 0.0,
        forecast_ci_high = 0.0,
        forecast_mape    = 0.0,
        context_data   = {},
        strategie      = None,
        conseil_final  = None,
        errors         = [],
        metrics        = {}
    )