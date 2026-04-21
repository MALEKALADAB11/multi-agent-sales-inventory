"""
Test complet APP2 Analyzer avec les 4 objectifs
"""

from sales_module.src.agents.app2_analyzer.agent import App2Analyzer
from datetime import datetime, timedelta
import json

# Configuration
config = {
    "model": "gpt-4",
    "temperature": 0.7,
    "detection_threshold": 0.8,
    "openai_api_key": "YOUR_API_KEY"  # À remplacer
}

# Créer l'agent
analyzer = App2Analyzer(config)

print("=" * 80)
print("TEST APP2 ANALYZER - ANALYSTE VENTES")
print("=" * 80)
print()

# Définir l'objectif du jour (en euros)
DAILY_TARGET = 5000
print(f"📊 Objectif du jour: {DAILY_TARGET}€")
print()

# Simuler un flux de transactions POS
transactions = [
    {"id": "TXN001", "amount": 150.50, "timestamp": datetime.now().isoformat(), "product_id": "PROD123"},
    {"id": "TXN002", "amount": 245.00, "timestamp": datetime.now().isoformat(), "product_id": "PROD456"},
    {"id": "TXN003", "amount": 89.99, "timestamp": datetime.now().isoformat(), "product_id": "PROD789"},
    {"id": "TXN004", "amount": 567.50, "timestamp": datetime.now().isoformat(), "product_id": "PROD101"},
    {"id": "TXN005", "amount": 320.00, "timestamp": datetime.now().isoformat(), "product_id": "PROD202"},
    {"id": "TXN006", "amount": 410.75, "timestamp": datetime.now().isoformat(), "product_id": "PROD303"},
    {"id": "TXN007", "amount": 190.25, "timestamp": datetime.now().isoformat(), "product_id": "PROD404"},
    {"id": "TXN008", "amount": 278.99, "timestamp": datetime.now().isoformat(), "product_id": "PROD505"},
]

print("🔄 1️⃣ RECOIT FLUX POS EN DIRECT")
print("-" * 80)
for transaction in transactions:
    result = analyzer.receive_pos_stream(transaction)
    print(f"  ✓ Transaction {transaction['id']}: {transaction['amount']}€")

print(f"  Total reçu: {len(analyzer.daily_transactions)} transactions")
print()

print("📈 2️⃣ CALCULE URGENCY_SCORE")
print("-" * 80)
urgency = analyzer.calculate_urgency_score()
print(f"  Urgency Score: {urgency.get('urgency_score', 0):.1f}/100")
print(f"  Montant actuel: {urgency.get('current_total', 0)}€")
print()

print("🎯 3️⃣ APPELLE TimeslPM -> PRÉVISION EOD")
print("-" * 80)
forecast = analyzer.call_timesl_pm()
print(f"  Prévision EOD: {forecast.get('forecast_amount', 0):.2f}€")
print(f"  Objectif: {forecast.get('target', 0)}€")
print(f"  % d'atteinte: {forecast.get('achievement_percentage', 0):.1f}%")
print(f"  Atteindra la cible: {'✓ OUI' if forecast.get('will_reach_target') else '✗ NON'}")
print()

print("⚠️  4️⃣ DÉTECTE ÉCART: HAUTE/FAIBLE")
print("-" * 80)
gap = analyzer.detect_gap()
print(f"  Écart: {gap.get('gap', 0):.2f}€")
print(f"  % d'écart: {gap.get('gap_percentage', 0):.1f}%")
print(f"  Niveau d'écart: {gap.get('gap_level', 'UNKNOWN')}")
print(f"  Action: {gap.get('action', 'N/A')}")
print()

print("🔧 AJUSTE LES OBJECTIFS EN TEMPS RÉEL")
print("-" * 80)
adjustment = analyzer.adjust_objectives_realtime()
print(f"  Objectif précédent: {adjustment.get('previous_target', 0)}€")
print(f"  Nouvel objectif: {adjustment.get('new_target', 0):.2f}€")
print(f"  Ajustement: {adjustment.get('adjustment_percentage', 0):.1f}%")
print(f"  Stratégie: {adjustment.get('strategy', 'UNKNOWN')}")
print(f"  📝 Recommandation: {adjustment.get('recommendation', 'N/A')}")
print()

print("=" * 80)
print("📊 ANALYSE COMPLÈTE")
print("=" * 80)
full_analysis = analyzer.full_analysis(DAILY_TARGET)
print(json.dumps(full_analysis, indent=2, ensure_ascii=False))