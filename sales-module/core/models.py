from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class UrgencyLevel(str, Enum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"


class RiskLevel(str, Enum):
    CRITICAL = "critical"
    HIGH     = "high"
    MEDIUM   = "medium"
    OK       = "ok"


class CardStatus(str, Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# ── Stores ───────────────────────────────────────────────
class Store(BaseModel):
    id:         str
    store_code: str
    name:       str
    city:       Optional[str]
    capacity:   int = 20
    active:     bool = True


class StoreMetrics(BaseModel):
    store_id:       str
    ca_today:       float
    ca_target:      float
    attainment_pct: float
    visitors_h:     int
    agents_live:    int
    context:        dict
    updated_at:     datetime


# ── Advisors ─────────────────────────────────────────────
class Advisor(BaseModel):
    id:           str
    store_id:     str
    name:         str
    role:         str
    avatar_color: str
    active:       bool = True


class AdvisorPerformance(BaseModel):
    advisor_id:    str
    ca_realized:   float
    ca_target:     float
    performance:   float
    prevision_eod: float
    coach_score:   float
    status:        str


# ── Coaching ─────────────────────────────────────────────
class CoachingCard(BaseModel):
    id:            str
    advisor_id:    str
    cycle_id:      str
    priority:      UrgencyLevel
    gap:           float
    context:       str
    advice:        str
    confidence:    float
    status:        CardStatus = CardStatus.PENDING
    created_at:    datetime


class CoachingCardUpdate(BaseModel):
    action:     str   # approve | reject
    manager_id: str


# ── Forecast ─────────────────────────────────────────────
class ForecastEOD(BaseModel):
    store_id:   str
    eod:        float
    target:     float
    gap:        float
    ci_80_low:  float
    ci_80_high: float
    mape:       float
    model:      str = "TimesFM-v1.2"


class ForecastHourly(BaseModel):
    hours: list[dict]   # {hour, forecast, ci_low, ci_high}


# ── Inventory ────────────────────────────────────────────
class InventoryItem(BaseModel):
    sku:               str
    name:              str
    category:          str
    stock:             int
    stock_min:         int
    stock_max:         int
    demand_forecast_24h: int
    coverage_ratio:    float
    risk_level:        RiskLevel
    risk_score:        float
    recommendation:    str
    confidence:        float
    last_updated:      str
    trend:             str


class InventoryAlert(BaseModel):
    id:       str
    sku:      str
    type:     str      # stockout | redistribution | overstock
    urgency:  str
    message:  str
    action:   str
    time:     str


class InventoryReco(BaseModel):
    id:            str
    sku:           str
    store_id:      str
    qty_recommend: int
    action:        str   # order | transfer
    from_store:    Optional[str]
    nlg_text:      str
    confidence:    float
    validated:     Optional[bool]


# ── Cycle ────────────────────────────────────────────────
class CycleTrigger(BaseModel):
    store_id:    str
    trigger:     str = "manual"   # cron | event | manual
    event_type:  Optional[str]


class CycleStatus(BaseModel):
    cycle_id:      str
    status:        str
    current_step:  str
    completed:     list[str]
    duration_ms:   int
    errors:        list[str]