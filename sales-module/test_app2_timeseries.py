"""
Test APP2 Analyzer avec Série Temporelle
"""

from src.agents.app2_analyzer.agent import App2Analyzer
from datetime import datetime, timedelta
import json

config = {
    "model_type": "arima",
    "detection_threshold": 0.8
}

analyzer = App2Analyzer(config)
DAILY_TARGET = 5000

print("="*80)
print("TEST APP2 ANALYZER - SÉRIE TEMPORELLE")
print("="*80)
print()

# Simuler un flux de transactions
# Simuler un flux de transactions (plus de données)
transactions = [
    # Heure 9
    {"id": "TXN001", "amount": 150.50, "timestamp": (datetime.now() - timedelta(hours=2, minutes=45)).isoformat(), "product_id": "P1"},
    {"id": "TXN002", "amount": 245.00, "timestamp": (datetime.now() - timedelta(hours=2, minutes=30)).isoformat(), "product_id": "P2"},
    {"id": "TXN003", "amount": 89.99, "timestamp": (datetime.now() - timedelta(hours=2, minutes=15)).isoformat(), "product_id": "P3"},
    
    # Heure 10
    {"id": "TXN004", "amount": 567.50, "timestamp": (datetime.now() - timedelta(hours=1, minutes=45)).isoformat(), "product_id": "P4"},
    {"id": "TXN005", "amount": 320.00, "timestamp": (datetime.now() - timedelta(hours=1, minutes=30)).isoformat(), "product_id": "P5"},
    {"id": "TXN006", "amount": 210.75, "timestamp": (datetime.now() - timedelta(hours=1, minutes=15)).isoformat(), "product_id": "P6"},
    
    # Heure 11
    {"id": "TXN007", "amount": 445.25, "timestamp": (datetime.now() - timedelta(minutes=45)).isoformat(), "product_id": "P7"},
    {"id": "TXN008", "amount": 189.99, "timestamp": (datetime.now() - timedelta(minutes=30)).isoformat(), "product_id": "P8"},
    {"id": "TXN009", "amount": 678.50, "timestamp": (datetime.now() - timedelta(minutes=15)).isoformat(), "product_id": "P9"},
    
    # Heure actuelle
    {"id": "TXN010", "amount": 320.00, "timestamp": datetime.now().isoformat(), "product_id": "P10"},
]

print("1️⃣ REÇOIT FLUX POS")
print("-"*80)
for tx in transactions:
    result = analyzer.receive_pos_stream(tx)
    print(f"  ✓ {tx['id']}: {tx['amount']}€ | Total: {result['total_received']}€")
print()

print("2️⃣ CALCULE L'ÉCART OBJECTIF")
print("-"*80)
gap = analyzer.calculate_objective_gap()
print(f"  Montant actuel: {gap.get('current_total', 0):.2f}€")
print(f"  Objectif: {gap.get('daily_target', 0):.2f}€")
print(f"  Écart: {gap.get('gap', 0):.2f}€ ({gap.get('gap_percentage', 0):.1f}%)")
print()

print("3️⃣ PRÉVISION EOD (SÉRIE TEMPORELLE)")
print("-"*80)
forecast = analyzer.predict_eod_timeseries()
print(f"  Prévision: {forecast.get('predicted_eod', 0):.2f}€")
print(f"  Confiance: {forecast.get('confidence_level', 0):.0%}")
print(f"  Modèle: {forecast.get('model_used')}")
print()

print("4️⃣ DÉTECTION URGENCE")
print("-"*80)
urgency = analyzer.detect_urgency()
print(f"  Niveau: {urgency.get('urgency_level')}")
print(f"  Score: {urgency.get('urgency_score', 0):.1f}/100")
print(f"  Action requise: {'✓ OUI' if urgency.get('action_required') else '✗ NON'}")
print(f"  {urgency.get('recommendation')}")
print()

print("="*80)
print("📊 ANALYSE COMPLÈTE")
print("="*80)
full = analyzer.full_analysis(DAILY_TARGET)
print(json.dumps(full, indent=2, ensure_ascii=False, default=str))