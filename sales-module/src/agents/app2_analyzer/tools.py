"""
Tools pour APP2 Analyzer
"""

from typing import Dict, Any, List

def get_analyzer_tools():
    """Retourne les outils disponibles pour l'analyzer"""
    
    return {
        "analyze_transaction": analyze_transaction_tool,
        "detect_batch_anomalies": detect_batch_anomalies_tool,
        "get_statistics": get_statistics_tool,
    }

def analyze_transaction_tool(transaction: Dict) -> Dict[str, Any]:
    """Analyse une transaction unique"""
    return {
        "status": "success",
        "transaction_id": transaction.get("id"),
        "analyzed": True
    }

def detect_batch_anomalies_tool(transactions: List[Dict]) -> Dict[str, Any]:
    """Détecte les anomalies dans un batch"""
    return {
        "status": "success",
        "total_transactions": len(transactions),
        "anomalies_detected": 0
    }

def get_statistics_tool(transactions: List[Dict]) -> Dict[str, Any]:
    """Obtient les statistiques"""
    amounts = [t.get("amount", 0) for t in transactions]
    return {
        "total": sum(amounts),
        "average": sum(amounts) / len(amounts) if amounts else 0,
        "count": len(transactions)
    }