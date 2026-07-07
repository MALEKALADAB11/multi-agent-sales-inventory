"""
balancing_engine.py — Balancing Engine Sales × Inventory (V5 FINAL)
====================================================================
Implémente le score multi-critères défini dans ARCHITECTURE_MULTI_AGENT_V5_FINAL.md
Section 6 — Balancing Engine

Score = w1×gap_alignment + w2×stock_health + w3×margin + w4×stockout_urgency + w5×advisor_fit

TOUT est dynamique : les stocks, marges, vélocités viennent de la DB ou du snapshot Redis.
Aucune valeur codée en dur pour les produits, SKUs ou quantités.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Poids par défaut (Section 6.1 du doc) ───────────────────────────────────
WEIGHT_GAP_ALIGNMENT   = 0.30
WEIGHT_STOCK_HEALTH    = 0.20
WEIGHT_MARGIN          = 0.15
WEIGHT_URGENCY         = 0.20
WEIGHT_ADVISOR_FIT     = 0.15

# Seuils pour stock_health
STOCK_RUPTURE_RATIO    = 0.20   # stock/optimal < 0.2 → pénalité
STOCK_SURSTOCK_RATIO   = 2.0    # stock/optimal > 2.0 → bonus écoulement


@dataclass
class ProductCandidate:
    """
    Produit candidat à la recommandation.
    Toutes les valeurs sont injectées dynamiquement depuis la DB ou le snapshot.
    """
    sku:              str
    name:             str
    price:            float          # Prix TND (depuis DB produits)
    stock_current:    int            # Stock actuel dynamique
    stock_optimal:    float          # Stock optimal (EOQ/ROP calculé)
    margin_pct:       float          # Marge en % (depuis DB produits)
    days_to_stockout: float          # Jours avant rupture (calculé par InventoryAnalysis)
    category:         str
    risk_level:       str            # CRITICAL | HIGH | MEDIUM | LOW
    is_top_seller:    bool   = False
    active_promo:     bool   = False

    # Scores calculés (remplis par BalancingEngine)
    gap_score:        float  = 0.0
    stock_score:      float  = 0.0
    margin_score:     float  = 0.0
    urgency_score:    float  = 0.0
    fit_score:        float  = 0.0
    final_score:      float  = 0.0
    reasoning:        Dict[str, float] = field(default_factory=dict)


@dataclass
class BalancingContext:
    """
    Contexte du cycle courant injecté dans le moteur.
    Tous les champs viennent du state courant — rien codé en dur.
    """
    store_id:           str
    gap_amount:         float        # TND manquants pour atteindre l'objectif
    gap_pct:            float        # % gap vs objectif
    urgency_level:      str          # CRITICAL | HIGH | MEDIUM | LOW
    advisor_id:         str
    advisor_history:    Dict[str, float]  # {category: acceptance_rate}
    current_hour:       int
    hours_remaining:    float


class BalancingEngine:
    """
    Calcule le score pondéré multi-critères pour chaque produit candidat.
    Retourne les 3 meilleurs candidats triés par score décroissant.

    Critères (Section 6.1) :
      1. Gap Alignment       — Le prix du produit comble-t-il le gap ?
      2. Stock Health        — Est-il sain / urgent à écouler ?
      3. Margin              — Marge normalisée
      4. Stockout Urgency    — Urgence d'écouler avant rupture
      5. Advisor Fit         — Taux d'acceptation historique du conseiller
    """

    def __init__(
        self,
        w_gap:     float = WEIGHT_GAP_ALIGNMENT,
        w_stock:   float = WEIGHT_STOCK_HEALTH,
        w_margin:  float = WEIGHT_MARGIN,
        w_urgency: float = WEIGHT_URGENCY,
        w_fit:     float = WEIGHT_ADVISOR_FIT,
    ):
        total = w_gap + w_stock + w_margin + w_urgency + w_fit
        self.w_gap     = w_gap     / total
        self.w_stock   = w_stock   / total
        self.w_margin  = w_margin  / total
        self.w_urgency = w_urgency / total
        self.w_fit     = w_fit     / total

    def score(
        self,
        candidates: List[ProductCandidate],
        context:    BalancingContext,
        top_n:      int = 3,
    ) -> List[ProductCandidate]:
        """
        Score chaque candidat et retourne les top_n.
        Filtre d'abord les produits en rupture (stock=0).
        """
        # G9 implicite : exclure les produits avec stock=0
        available = [c for c in candidates if c.stock_current > 0]
        if not available:
            logger.warning("[BalancingEngine] Tous les candidats en rupture — fallback tous les candidats")
            available = candidates  # fallback si rien de disponible

        for product in available:
            product.gap_score     = self._gap_alignment(product, context)
            product.stock_score   = self._stock_health(product)
            product.margin_score  = self._margin_score(product)
            product.urgency_score = self._urgency_score(product)
            product.fit_score     = self._advisor_fit(product, context)

            product.final_score = (
                self.w_gap     * product.gap_score     +
                self.w_stock   * product.stock_score   +
                self.w_margin  * product.margin_score  +
                self.w_urgency * product.urgency_score +
                self.w_fit     * product.fit_score
            )

            product.reasoning = {
                "gap_alignment":    round(product.gap_score, 3),
                "stock_health":     round(product.stock_score, 3),
                "margin":           round(product.margin_score, 3),
                "stockout_urgency": round(product.urgency_score, 3),
                "advisor_fit":      round(product.fit_score, 3),
                "final":            round(product.final_score, 3),
            }

        ranked = sorted(available, key=lambda x: x.final_score, reverse=True)

        logger.info(
            "[BalancingEngine] Store=%s | Gap=%.0f TND | %d candidats scorés | top=%s (%.3f)",
            context.store_id,
            context.gap_amount,
            len(available),
            ranked[0].name if ranked else "N/A",
            ranked[0].final_score if ranked else 0,
        )

        return ranked[:top_n]

    # ── Critère 1 — Gap Alignment ────────────────────────────────────────────

    def _gap_alignment(self, p: ProductCandidate, ctx: BalancingContext) -> float:
        """
        Dans quelle mesure ce produit comble-t-il le gap CA ?
        Normalisé : score=1.0 si price >= gap_amount.
        """
        if ctx.gap_amount <= 0:
            return 0.5
        return min(p.price / ctx.gap_amount, 1.0)

    # ── Critère 2 — Stock Health ─────────────────────────────────────────────

    def _stock_health(self, p: ProductCandidate) -> float:
        """
        -0.5 si stock critique (rupture imminente — éviter de décevoir le client)
        +0.8 si surstock (bonus pour inciter à l'écouler)
        +0.5 sinon (stock sain)
        Tout dynamique depuis stock_current et stock_optimal.
        """
        if p.stock_optimal <= 0:
            return 0.5

        ratio = p.stock_current / p.stock_optimal

        if p.stock_current == 0:
            return -1.0   # Rupture totale → exclure (complété par le filtre initial)
        elif ratio < STOCK_RUPTURE_RATIO:
            return -0.5   # Stock très bas → pénalité (risque déception)
        elif ratio > STOCK_SURSTOCK_RATIO:
            return 0.8    # Surstock → bonus (écouler)
        else:
            return 0.5    # Stock sain

    # ── Critère 3 — Marge ───────────────────────────────────────────────────

    def _margin_score(self, p: ProductCandidate) -> float:
        """Normalise la marge sur 50% (référence haute Ooredoo)."""
        return min(p.margin_pct / 50.0, 1.0)

    # ── Critère 4 — Urgence d'écouler ───────────────────────────────────────

    def _urgency_score(self, p: ProductCandidate) -> float:
        """
        Urgence d'écouler basée sur days_to_stockout (calculé par InventoryAnalysis).
        < 3 jours → 1.0  (EXPEDITE — écouler immédiatement)
        < 7 jours → 0.8
        > 60 jours → 0.3  (pas urgent, mais bon stock → légèrement recommandable)
        Dynamique : vient de l'InventoryAnalysisAgent.
        """
        d = p.days_to_stockout
        if d < 3:    return 1.0
        elif d < 7:  return 0.8
        elif d < 14: return 0.6
        elif d < 30: return 0.4
        elif d < 60: return 0.3
        else:        return 0.7   # surstock : bon à vendre aussi

    # ── Critère 5 — Advisor Fit ──────────────────────────────────────────────

    def _advisor_fit(self, p: ProductCandidate, ctx: BalancingContext) -> float:
        """
        Taux d'acceptation historique du conseiller pour cette catégorie.
        Depuis advisor_profile (Mémoire Procédurale dynamique).
        Défaut 0.5 si inconnu.
        """
        return ctx.advisor_history.get(p.category, 0.5)


# ── Helpers — Construction dynamique des candidats ───────────────────────────

def build_candidates_from_snapshot(
    inventory_snapshot: Dict[str, Any],
    product_db_data: Optional[Dict[str, Any]] = None,
) -> List[ProductCandidate]:
    """
    Construit la liste des ProductCandidate depuis le snapshot Redis.
    Toutes les valeurs (stock, risk_level, days_remaining) viennent du snapshot —
    elles ont été calculées dynamiquement par InventoryAnalysisAgent.
    La marge et le prix viennent de product_db_data si fourni.

    Paramètres :
      inventory_snapshot : {"skus": {sku: {stock_qty, risk_level, product_name, days_remaining, ...}}}
      product_db_data    : {sku: {price, margin_pct, category, is_top_seller, ...}} depuis DB
    """
    candidates = []
    skus_data  = (inventory_snapshot or {}).get("skus", {})
    product_db = product_db_data or {}

    for sku, info in skus_data.items():
        stock_qty    = int(info.get("stock_qty", 0))
        risk_level   = str(info.get("risk_level", "unknown")).upper()
        product_name = str(info.get("product_name", sku))
        days_rem     = float(info.get("days_remaining", 30.0))
        reorder_pt   = float(info.get("reorder_point", 0))

        # Données DB (prix, marge, catégorie) — dynamiques
        db_info      = product_db.get(sku, product_db.get(str(sku), {}))
        price        = float(db_info.get("price", 0) or 0)
        margin_pct   = float(db_info.get("margin_pct", 20) or 20)
        category     = str(db_info.get("category", "unknown"))
        is_top       = bool(db_info.get("is_top_seller", False))
        active_promo = bool(db_info.get("active_promo", False))

        # Stock optimal = reorder_point * 2 (heuristique), depuis DB dynamiquement
        stock_optimal = max(reorder_pt * 2, stock_qty * 1.2, 1.0)

        candidates.append(ProductCandidate(
            sku            = str(sku),
            name           = product_name,
            price          = price,
            stock_current  = stock_qty,
            stock_optimal  = stock_optimal,
            margin_pct     = margin_pct,
            days_to_stockout = days_rem,
            category       = category,
            risk_level     = risk_level,
            is_top_seller  = is_top,
            active_promo   = active_promo,
        ))

    return candidates


def get_balancing_engine() -> BalancingEngine:
    """Factory — instance singleton."""
    return BalancingEngine()
