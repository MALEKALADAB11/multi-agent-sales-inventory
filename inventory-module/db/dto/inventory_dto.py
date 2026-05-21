"""
inventory_dto.py
Data Transfer Objects for the inventory module.

One dataclass per entity. These are what your API layer (routes.py)
and agents return — never raw DB dicts.

Usage:
    from db.dto.inventory_dto import ProductDTO, AlertDTO, StockLevelDTO

    product = ProductDTO.from_db(row)
    alert   = AlertDTO.from_db(row)
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


# ── Products ──────────────────────────────────────────────────────────────────

@dataclass
class ProductDTO:
    sku: str
    product_name: str
    category: Optional[str]
    unit_cost: Optional[float]
    unit_price: Optional[float]
    lead_time_days: Optional[int]
    lead_time_std: Optional[float]
    moq: Optional[int]
    holding_cost_pct: Optional[float]
    order_cost: Optional[float]
    lifecycle_stage: Optional[str]

    @classmethod
    def from_db(cls, row: dict) -> "ProductDTO":
        return cls(
            sku              = row["sku"],
            product_name     = row["product_name"],
            category         = row.get("category"),
            unit_cost        = float(row["unit_cost"])        if row.get("unit_cost")        else None,
            unit_price       = float(row["unit_price"])       if row.get("unit_price")       else None,
            lead_time_days   = row.get("lead_time_days"),
            lead_time_std    = float(row["lead_time_std"])    if row.get("lead_time_std")    else None,
            moq              = row.get("moq"),
            holding_cost_pct = float(row["holding_cost_pct"]) if row.get("holding_cost_pct") else None,
            order_cost       = float(row["order_cost"])       if row.get("order_cost")       else None,
            lifecycle_stage  = row.get("lifecycle_stage"),
        )

    def to_dict(self) -> dict:
        return {
            "sku":              self.sku,
            "product_name":     self.product_name,
            "category":         self.category,
            "unit_cost":        self.unit_cost,
            "unit_price":       self.unit_price,
            "lead_time_days":   self.lead_time_days,
            "lead_time_std":    self.lead_time_std,
            "moq":              self.moq,
            "holding_cost_pct": self.holding_cost_pct,
            "order_cost":       self.order_cost,
            "lifecycle_stage":  self.lifecycle_stage,
        }


# ── Stores ────────────────────────────────────────────────────────────────────

@dataclass
class StoreDTO:
    store_id: str
    store_name: str
    region: Optional[str]
    active: bool

    @classmethod
    def from_db(cls, row: dict) -> "StoreDTO":
        return cls(
            store_id   = row["store_id"],
            store_name = row["store_name"],
            region     = row.get("region"),
            active     = row.get("active", True),
        )

    def to_dict(self) -> dict:
        return {
            "store_id":   self.store_id,
            "store_name": self.store_name,
            "region":     self.region,
            "active":     self.active,
        }


# ── Stock levels ──────────────────────────────────────────────────────────────

@dataclass
class StockLevelDTO:
    sku: str
    store_id: str
    product_name: Optional[str]       # joined from products — may be None
    stock_current: int
    stock_in_transit: int
    stock_min: Optional[int]
    stock_max: Optional[int]
    remaining_days_of_stock: Optional[float]
    last_updated: Optional[datetime]

    @property
    def is_low(self) -> bool:
        """True if current stock is at or below the minimum threshold."""
        if self.stock_min is None:
            return False
        return self.stock_current <= self.stock_min

    @property
    def is_overstock(self) -> bool:
        """True if current stock exceeds the maximum threshold."""
        if self.stock_max is None:
            return False
        return self.stock_current > self.stock_max

    @classmethod
    def from_db(cls, row: dict) -> "StockLevelDTO":
        return cls(
            sku                     = row["sku"],
            store_id                = row["store_id"],
            product_name            = row.get("product_name"),
            stock_current           = row["stock_current"],
            stock_in_transit        = row.get("stock_in_transit", 0),
            stock_min               = row.get("stock_min"),
            stock_max               = row.get("stock_max"),
            remaining_days_of_stock = float(row["remaining_days_of_stock"]) if row.get("remaining_days_of_stock") else None,
            last_updated            = row.get("last_updated"),
        )

    def to_dict(self) -> dict:
        return {
            "sku":                     self.sku,
            "store_id":                self.store_id,
            "product_name":            self.product_name,
            "stock_current":           self.stock_current,
            "stock_in_transit":        self.stock_in_transit,
            "stock_min":               self.stock_min,
            "stock_max":               self.stock_max,
            "remaining_days_of_stock": self.remaining_days_of_stock,
            "is_low":                  self.is_low,
            "is_overstock":            self.is_overstock,
            "last_updated":            self.last_updated.isoformat() if self.last_updated else None,
        }


# ── Demand forecast ───────────────────────────────────────────────────────────

@dataclass
class ForecastDTO:
    sku: str
    store_id: str
    product_name: Optional[str]
    forecast_date: date
    demand_24h: float
    confidence_low: Optional[float]
    confidence_high: Optional[float]
    model_version: Optional[str]

    @classmethod
    def from_db(cls, row: dict) -> "ForecastDTO":
        return cls(
            sku             = row["sku"],
            store_id        = row["store_id"],
            product_name    = row.get("product_name"),
            forecast_date   = row["forecast_date"],
            demand_24h      = float(row["demand_24h"]),
            confidence_low  = float(row["confidence_low"])  if row.get("confidence_low")  else None,
            confidence_high = float(row["confidence_high"]) if row.get("confidence_high") else None,
            model_version   = row.get("model_version"),
        )

    def to_dict(self) -> dict:
        return {
            "sku":             self.sku,
            "store_id":        self.store_id,
            "product_name":    self.product_name,
            "forecast_date":   self.forecast_date.isoformat(),
            "demand_24h":      self.demand_24h,
            "confidence_low":  self.confidence_low,
            "confidence_high": self.confidence_high,
            "model_version":   self.model_version,
        }


# ── Alerts ────────────────────────────────────────────────────────────────────

@dataclass
class AlertDTO:
    id: str
    sku: str
    store_id: str
    product_name: Optional[str]
    alert_type: str
    severity: str
    recommended_action: Optional[str]
    status: str
    triggered_at: datetime
    estimated_stockout_date: Optional[date]
    was_accurate: Optional[bool]

    @classmethod
    def from_db(cls, row: dict) -> "AlertDTO":
        return cls(
            id                      = str(row["id"]),
            sku                     = row["sku"],
            store_id                = row["store_id"],
            product_name            = row.get("product_name"),
            alert_type              = row["alert_type"],
            severity                = row["severity"],
            recommended_action      = row.get("recommended_action"),
            status                  = row["status"],
            triggered_at            = row["triggered_at"],
            estimated_stockout_date = row.get("estimated_stockout_date"),
            was_accurate            = row.get("was_accurate"),
        )

    def to_dict(self) -> dict:
        return {
            "id":                      self.id,
            "sku":                     self.sku,
            "store_id":                self.store_id,
            "product_name":            self.product_name,
            "alert_type":              self.alert_type,
            "severity":                self.severity,
            "recommended_action":      self.recommended_action,
            "status":                  self.status,
            "triggered_at":            self.triggered_at.isoformat(),
            "estimated_stockout_date": self.estimated_stockout_date.isoformat() if self.estimated_stockout_date else None,
            "was_accurate":            self.was_accurate,
        }


# ── Recommendations ───────────────────────────────────────────────────────────

@dataclass
class RecommendationDTO:
    id: str
    sku: str
    store_id: str
    product_name: Optional[str]
    recommendation_type: str
    recommendation_text: Optional[str]
    suggested_quantity: Optional[int]
    moq: Optional[int]               # joined from products
    confidence: Optional[float]
    status: str
    decided_by: Optional[str]
    decided_at: Optional[datetime]
    created_at: datetime

    @classmethod
    def from_db(cls, row: dict) -> "RecommendationDTO":
        return cls(
            id                  = str(row["id"]),
            sku                 = row["sku"],
            store_id            = row["store_id"],
            product_name        = row.get("product_name"),
            recommendation_type = row["recommendation_type"],
            recommendation_text = row.get("recommendation_text"),
            suggested_quantity  = row.get("suggested_quantity"),
            moq                 = row.get("moq"),
            confidence          = float(row["confidence"]) if row.get("confidence") else None,
            status              = row["status"],
            decided_by          = row.get("decided_by"),
            decided_at          = row.get("decided_at"),
            created_at          = row["created_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id":                  self.id,
            "sku":                 self.sku,
            "store_id":            self.store_id,
            "product_name":        self.product_name,
            "recommendation_type": self.recommendation_type,
            "recommendation_text": self.recommendation_text,
            "suggested_quantity":  self.suggested_quantity,
            "moq":                 self.moq,
            "confidence":          self.confidence,
            "status":              self.status,
            "decided_by":          self.decided_by,
            "decided_at":          self.decided_at.isoformat() if self.decided_at else None,
            "created_at":          self.created_at.isoformat(),
        }


# ── Promotions ────────────────────────────────────────────────────────────────

@dataclass
class PromotionDTO:
    promo_id: str
    promo_name: str
    promo_type: Optional[str]
    start_date: date
    end_date: date
    sku: Optional[str]
    category: Optional[str]
    discount_pct: Optional[float]
    scope: Optional[str]
    is_active: bool

    @classmethod
    def from_db(cls, row: dict) -> "PromotionDTO":
        return cls(
            promo_id     = row["promo_id"],
            promo_name   = row["promo_name"],
            promo_type   = row.get("promo_type"),
            start_date   = row["start_date"],
            end_date     = row["end_date"],
            sku          = row.get("sku"),
            category     = row.get("category"),
            discount_pct = float(row["discount_pct"]) if row.get("discount_pct") else None,
            scope        = row.get("scope"),
            is_active    = row.get("is_active", False),
        )

    def to_dict(self) -> dict:
        return {
            "promo_id":    self.promo_id,
            "promo_name":  self.promo_name,
            "promo_type":  self.promo_type,
            "start_date":  self.start_date.isoformat(),
            "end_date":    self.end_date.isoformat(),
            "sku":         self.sku,
            "category":    self.category,
            "discount_pct": self.discount_pct,
            "scope":       self.scope,
            "is_active":   self.is_active,
        }


# ── Agent runs ────────────────────────────────────────────────────────────────

@dataclass
class AgentRunDTO:
    id: str
    agent_name: str
    store_id: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]
    status: str
    error_message: Optional[str]
    items_processed: int
    alerts_generated: int
    recommendations_generated: int

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @classmethod
    def from_db(cls, row: dict) -> "AgentRunDTO":
        return cls(
            id                        = str(row["id"]),
            agent_name                = row["agent_name"],
            store_id                  = row.get("store_id"),
            started_at                = row["started_at"],
            completed_at              = row.get("completed_at"),
            status                    = row["status"],
            error_message             = row.get("error_message"),
            items_processed           = row.get("items_processed", 0),
            alerts_generated          = row.get("alerts_generated", 0),
            recommendations_generated = row.get("recommendations_generated", 0),
        )

    def to_dict(self) -> dict:
        return {
            "id":                        self.id,
            "agent_name":                self.agent_name,
            "store_id":                  self.store_id,
            "started_at":                self.started_at.isoformat(),
            "completed_at":              self.completed_at.isoformat() if self.completed_at else None,
            "status":                    self.status,
            "error_message":             self.error_message,
            "duration_seconds":          self.duration_seconds,
            "items_processed":           self.items_processed,
            "alerts_generated":          self.alerts_generated,
            "recommendations_generated": self.recommendations_generated,
        }
