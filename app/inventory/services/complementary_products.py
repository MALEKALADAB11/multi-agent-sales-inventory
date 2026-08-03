"""
complementary_products.py
==========================
Service for finding complementary/cross-sell products using hybrid approach:
1. Data-driven rules from product_associations table (computed from sales_history)
2. Business rules based on domain knowledge (gamme relationships)

Usage:
    from app.inventory.services.complementary_products import ComplementaryProductsService
    service = ComplementaryProductsService(pool)
    
    # Get complementary products for a specific SKU
    complements = await service.find_complementary(sku=12345, store_id="S01", limit=5)
    
    # Get complementary products for a gamme (fallback)
    complements = await service.find_by_gamme(gamme="TERMINAL", limit=5)
"""
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ComplementaryProduct:
    """Represents a complementary product recommendation."""
    sku: Optional[int]
    product_name: str
    gamme: str
    confidence: float
    lift: float
    source: str  # "data" | "business_rule"
    reason: str


class ComplementaryProductsService:
    """
    Hybrid service for complementary product recommendations.
    Combines data-driven associations with business rules.
    """
    
    # Business rules based on domain knowledge
    # These complement data-driven rules for logical relationships
    # that may not appear in sales data (different timing, channels, etc.)
    BUSINESS_RULES = {
        "TERMINAL": ["ACCESSOIRE", "SIM_KIT"],
        "SIM_KIT": ["RECHARGE", "TERMINAL"],
        "FORFAIT": ["TERMINAL", "SIM_KIT"],
        "ACCESSOIRE": ["TERMINAL"],
        "RECHARGE": ["SIM_KIT"],
    }
    
    def __init__(self, pool):
        self.pool = pool
    
    async def find_complementary(
        self,
        sku: int,
        store_id: Optional[str] = None,
        limit: int = 5,
        min_confidence: float = 0.1
    ) -> List[ComplementaryProduct]:
        """
        Find complementary products for a specific SKU using hybrid approach.
        
        Combines BOTH data-driven rules AND business rules (not fallback).
        Data rules get higher confidence, business rules provide logical complements.
        
        Args:
            sku: Product SKU to find complements for
            store_id: Optional store ID for store-specific rules
            limit: Max number of complements to return
            min_confidence: Minimum confidence threshold for data rules
            
        Returns:
            List of ComplementaryProduct objects
        """
        results = []
        
        # Get product gamme first
        async with self.pool.acquire() as conn:
            product_row = await conn.fetchrow("""
                SELECT sku, nom, gamme_libelle
                FROM sales.produits
                WHERE sku = $1
            """, sku)
            
            if not product_row:
                logger.warning(f"Product SKU {sku} not found")
                return []
            
            gamme = product_row["gamme_libelle"]
            product_name = product_row["nom"]
        
        # 1. Get product-level data rules (store-specific first, then global)
        data_results = await self._get_data_rules(sku, store_id, min_confidence, limit)
        results.extend(data_results)
        
        # 2. Get gamme-level data rules
        gamme_results = await self._get_gamme_data_rules(gamme, store_id, min_confidence, limit)
        results.extend(gamme_results)
        
        # 3. Add business rules (logical complements that may not appear in data)
        business_results = self._get_business_rules(gamme, limit)
        results.extend(business_results)
        
        # Deduplicate by gamme/sku and sort by confidence (data rules first)
        seen_gammes = set()
        seen_skus = set()
        final_results = []
        
        for r in results:
            # Skip if already seen
            if r.sku and r.sku in seen_skus:
                continue
            if r.gamme and r.gamme in seen_gammes:
                continue
            
            # Track seen
            if r.sku:
                seen_skus.add(r.sku)
            if r.gamme:
                seen_gammes.add(r.gamme)
            
            final_results.append(r)
        
        # Sort: data rules first (higher confidence), then business rules
        final_results.sort(key=lambda x: (x.source != "data", -x.confidence))
        
        return final_results[:limit]
    
    async def _get_data_rules(
        self,
        sku: int,
        store_id: Optional[str],
        min_confidence: float,
        limit: int
    ) -> List[ComplementaryProduct]:
        """Get data-driven rules for specific SKU."""
        results = []
        
        async with self.pool.acquire() as conn:
            # Try store-specific rules first
            if store_id:
                rows = await conn.fetch("""
                    SELECT pa.sku2, p.nom as product_name, pa.gamme2, pa.confidence, pa.lift
                    FROM inventory.product_associations pa
                    JOIN sales.produits p ON pa.sku2 = p.sku
                    WHERE pa.sku1 = $1 AND pa.store_id = $2 AND pa.confidence >= $3
                    ORDER BY pa.confidence DESC, pa.lift DESC
                    LIMIT $4
                """, sku, store_id, min_confidence, limit)
                
                for row in rows:
                    results.append(ComplementaryProduct(
                        sku=row["sku2"],
                        product_name=row["product_name"],
                        gamme=row["gamme2"],
                        confidence=float(row["confidence"]),
                        lift=float(row["lift"]),
                        source="data",
                        reason=f"Store-specific: sold together {row['confidence']:.0%} of the time"
                    ))
            
            # If no store-specific results, try global rules
            if not results:
                rows = await conn.fetch("""
                    SELECT pa.sku2, p.nom as product_name, pa.gamme2, pa.confidence, pa.lift
                    FROM inventory.product_associations pa
                    JOIN sales.produits p ON pa.sku2 = p.sku
                    WHERE pa.sku1 = $1 AND pa.store_id IS NULL AND pa.confidence >= $2
                    ORDER BY pa.confidence DESC, pa.lift DESC
                    LIMIT $3
                """, sku, min_confidence, limit)
                
                for row in rows:
                    results.append(ComplementaryProduct(
                        sku=row["sku2"],
                        product_name=row["product_name"],
                        gamme=row["gamme2"],
                        confidence=float(row["confidence"]),
                        lift=float(row["lift"]),
                        source="data",
                        reason=f"Global: sold together {row['confidence']:.0%} of the time"
                    ))
        
        return results
    
    async def _get_gamme_data_rules(
        self,
        gamme: str,
        store_id: Optional[str],
        min_confidence: float,
        limit: int
    ) -> List[ComplementaryProduct]:
        """Get gamme-level data rules (fallback when product-level is sparse)."""
        results = []
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT pa.gamme2, pa.confidence, pa.lift
                FROM inventory.product_associations pa
                WHERE pa.gamme1 = $1 AND pa.sku1 IS NULL AND pa.confidence >= $2
                ORDER BY pa.confidence DESC, pa.lift DESC
                LIMIT $3
            """, gamme, min_confidence, limit)
            
            for row in rows:
                # Get a representative product from this gamme
                product_row = await conn.fetchrow("""
                    SELECT sku, nom
                    FROM sales.produits
                    WHERE gamme_libelle = $1 AND actif = true
                    LIMIT 1
                """, row["gamme2"])
                
                if product_row:
                    results.append(ComplementaryProduct(
                        sku=product_row["sku"],
                        product_name=product_row["nom"],
                        gamme=row["gamme2"],
                        confidence=float(row["confidence"]),
                        lift=float(row["lift"]),
                        source="data",
                        reason=f"Gamme-level: {row['gamme2']} sells with {gamme} {row['confidence']:.0%} of the time"
                    ))
        
        return results
    
    def _get_business_rules(self, gamme: str, limit: int) -> List[ComplementaryProduct]:
        """Get business rules as fallback."""
        results = []
        
        complementary_gammes = self.BUSINESS_RULES.get(gamme, [])
        
        for comp_gamme in complementary_gammes[:limit]:
            results.append(ComplementaryProduct(
                sku=None,  # Gamme-level, no specific SKU
                product_name=f"Products in {comp_gamme}",
                gamme=comp_gamme,
                confidence=0.5,  # Default confidence for business rules
                lift=1.0,
                source="business_rule",
                reason=f"Business rule: {comp_gamme} logically complements {gamme}"
            ))
        
        return results
    
    async def find_by_gamme(
        self,
        gamme: str,
        store_id: Optional[str] = None,
        limit: int = 5
    ) -> List[ComplementaryProduct]:
        """
        Find complementary products by gamme (useful for category-level recommendations).
        """
        results = []
        
        # Try data rules first
        data_results = await self._get_gamme_data_rules(gamme, store_id, 0.1, limit)
        results.extend(data_results)
        
        # Fallback to business rules
        if len(results) < limit:
            business_results = self._get_business_rules(gamme, limit - len(results))
            results.extend(business_results)
        
        return results[:limit]
