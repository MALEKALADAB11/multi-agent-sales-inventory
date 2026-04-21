"""
Test APP2 Analyzer avec Graphe Visuel
"""

from src.agents.app2_analyzer.agent import App2Analyzer
from src.agents.app2_analyzer.graph_visual import display_graph_ascii, save_graph_visualization
from datetime import datetime, timedelta

# Afficher le graphe ASCII
print(display_graph_ascii())

# Générer le graphe visuel
print("\n" + "="*80)
print("GÉNÉRATION DU GRAPHE VISUEL")
print("="*80)
save_graph_visualization()

# Configuration
config = {"model_type": "arima", "detection_threshold": 0.8}
analyzer = App2Analyzer(config)
DAILY_TARGET = 5000

# Transactions
transactions = [
    {"id": "TXN001", "amount": 150.50, "timestamp": (datetime.now() - timedelta(hours=2, minutes=45)).isoformat(), "product_id": "P1"},
    {"id": "TXN002", "amount": 245.00, "timestamp": (datetime.now() - timedelta(hours=2, minutes=30)).isoformat(), "product_id": "P2"},
    {"id": "TXN003", "amount": 89.99, "timestamp": (datetime.now() - timedelta(hours=2, minutes=15)).isoformat(), "product_id": "P3"},
    {"id": "TXN004", "amount": 567.50, "timestamp": (datetime.now() - timedelta(hours=1, minutes=45)).isoformat(), "product_id": "P4"},
    {"id": "TXN005", "amount": 320.00, "timestamp": (datetime.now() - timedelta(hours=1, minutes=30)).isoformat(), "product_id": "P5"},
]

print("\n" + "="*80)
print("EXÉCUTION DU WORKFLOW")
print("="*80)

# Exécuter workflow
for idx, tx in enumerate(transactions, 1):
    print(f"\n{'='*80}")
    print(f"ÉTAPE {idx}/{len(transactions)}")
    print(f"{'='*80}")
    
    print(f"\n📥 [NODE 1] receive_pos")
    pos_result = analyzer.receive_pos_stream(tx)
    print(f"   ✓ Transaction {pos_result.get('transaction_id')}: {pos_result.get('amount')}€")
    
    print(f"\n📊 [NODE 2] calculate_gap")
    gap = analyzer.calculate_objective_gap()
    print(f"   ✓ Écart: {gap.get('gap', 0):.2f}€ ({gap.get('gap_percentage', 0):.1f}%)")
    
    print(f"\n🔮 [NODE 3] predict_eod")
    forecast = analyzer.predict_eod_timeseries()
    print(f"   ✓ Prévision: {forecast.get('predicted_eod', 0):.2f}€")
    
    print(f"\n🚨 [NODE 4] detect_urgency")
    urgency = analyzer.detect_urgency()
    print(f"   ✓ Niveau: {urgency.get('urgency_level')} ({urgency.get('urgency_percentage')})")
    
    print(f"\n📈 [NODE 5] generate_report")
    final = analyzer.full_analysis(DAILY_TARGET)
    print(f"   ✓ Rapport généré")

print(f"\n{'='*80}")
print("✅ WORKFLOW TERMINÉ !")
print(f"{'='*80}\n")

# Rapport final
print("\n📊 RAPPORT FINAL:\n")
final_analysis = analyzer.full_analysis(DAILY_TARGET)
print(final_analysis["summary"])

print("\n✅ Les fichiers graphes sont générés:")
print("   • app2_workflow_graph.png")
print("   • app2_workflow_graph.svg")
print("   • app2_workflow_graph.pdf")