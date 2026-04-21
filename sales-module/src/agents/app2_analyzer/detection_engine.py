"""
Detection Engine pour APP2 Analyzer
Détecte les anomalies et patterns POS
"""

from typing import List, Dict, Any
from datetime import datetime, timedelta

class DetectionEngine:
    """Moteur de détection des anomalies POS"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.threshold = config.get("detection_threshold", 0.8)
        
    def detect_anomalies(self, transactions: List[Dict]) -> List[str]:
        """Détecte les anomalies dans les transactions"""
        
        anomalies = []
        
        for transaction in transactions:
            # Vérifier chaque type d'anomalie
            if self._check_unusual_amount(transaction):
                anomalies.append("UNUSUAL_AMOUNT")
            
            if self._check_high_frequency(transaction):
                anomalies.append("HIGH_FREQUENCY")
            
            if self._check_off_hours(transaction):
                anomalies.append("OFF_HOURS")
            
            if self._check_unusual_product_mix(transaction):
                anomalies.append("UNUSUAL_PRODUCT_MIX")
            
            if self._check_payment_method_anomaly(transaction):
                anomalies.append("PAYMENT_METHOD_ANOMALY")
        
        return list(set(anomalies))  # Retirer les doublons
    
    def _check_unusual_amount(self, transaction: Dict) -> bool:
        """Vérifier les montants inhabituels"""
        amount = transaction.get("amount", 0)
        
        # Si le montant est > 5000, c'est suspect
        if amount > 5000:
            return True
        
        # Si le montant est < 0, c'est une erreur
        if amount < 0:
            return True
        
        return False
    
    def _check_high_frequency(self, transaction: Dict) -> bool:
        """Vérifier la fréquence de transactions"""
        # Exemple: si plusieurs transactions du même client en < 1 minute
        return False  # À implémenter avec historique
    
    def _check_off_hours(self, transaction: Dict) -> bool:
        """Vérifier si la transaction est en dehors des heures de service"""
        timestamp = transaction.get("timestamp")
        
        if isinstance(timestamp, str):
            try:
                dt = datetime.fromisoformat(timestamp)
            except:
                return False
        else:
            dt = timestamp
        
        hour = dt.hour
        
        # Transactions entre 23h et 6h sont suspectes
        if hour >= 23 or hour < 6:
            return True
        
        return False
    
    def _check_unusual_product_mix(self, transaction: Dict) -> bool:
        """Vérifier les combinaisons de produits inhabituelles"""
        products = transaction.get("products", [])
        
        # Exemple: beaucoup d'alcool + beaucoup de médicaments
        has_alcohol = any(p.get("category") == "alcohol" for p in products)
        has_medicine = any(p.get("category") == "medicine" for p in products)
        
        if has_alcohol and has_medicine:
            return True
        
        return False
    
    def _check_payment_method_anomaly(self, transaction: Dict) -> bool:
        """Vérifier les anomalies de paiement"""
        payment_method = transaction.get("payment_method", "")
        amount = transaction.get("amount", 0)
        
        # Montant élevé en espèces est suspect
        if payment_method == "cash" and amount > 3000:
            return True
        
        return False
    
    def get_statistics(self, transactions: List[Dict]) -> Dict[str, Any]:
        """Obtient les statistiques sur les transactions"""
        
        if not transactions:
            return {}
        
        amounts = [t.get("amount", 0) for t in transactions]
        
        return {
            "total_transactions": len(transactions),
            "total_amount": sum(amounts),
            "average_amount": sum(amounts) / len(amounts),
            "min_amount": min(amounts),
            "max_amount": max(amounts),
            "timestamp": datetime.now().isoformat()
        }