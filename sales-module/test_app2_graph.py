"""
Test APP2 Analyzer avec Workflow Graph Visualization
"""

from src.agents.app2_analyzer.agent import App2Analyzer
from src.agents.app2_analyzer.graph import visualize_graph_ascii, App2AnalyzerWorkflow
from datetime import datetime, timedelta

# Afficher le graphe ASCII
print(visualize_graph_ascii())

# Configuration
config = {"model_type": "arima", "detection_threshold": 0.8}
analyzer = App2Analyzer(config)
DAILY_TARGET = 5000

# Transactions de test
transactions = [
    {"id": "TXN001", "amount": 150.50, "timestamp": (datetime.now() - timedelta(hours=2, minutes=45)).isoformat(), "product_id": "P1"},
    {"id": "TXN002", "amount": 245.00, "timestamp": (datetime.now() - timedelta(hours=2, minutes=30)).isoformat(), "product_id": "P2"},
    {"id": "TXN003", "amount": 89.99, "timestamp": (datetime.now() - timedelta(hours=2, minutes=15)).isoformat(), "product_id": "P3"},
    {"id": "TXN004", "amount": 567.50, "timestamp": (datetime.now() - timedelta(hours=1, minutes=45)).isoformat(), "product_id": "P4"},
    {"id": "TXN005", "amount": 320.00, "timestamp": (datetime.now() - timedelta(hours=1, minutes=30)).isoformat(), "product_id": "P5"},
]

print("\n" + "="*80)
print("EXÉCUTION DU WORKFLOW APP2 ANALYZER")
print("="*80)

# Créer le workflow
workflow = App2AnalyzerWorkflow(analyzer, DAILY_TARGET)

# Exécuter pour chaque transaction
for idx, tx in enumerate(transactions, 1):
    print(f"\n{'='*80}")
    print(f"EXÉCUTION {idx}/{len(transactions)}")
    print(f"{'='*80}")
    
    try:
        result = workflow.execute_workflow(tx)
        
        # Résumé
        print(f"\n{'─'*80}")
        print("RÉSUMÉ EXÉCUTION:")
        print(f"{'─'*80}")
        
        gap = result["gap_analysis"]
        forecast = result["forecast"]
        urgency = result["urgency"]
        
        print(f"  📊 Total: {gap.get('current_total', 0):.2f}€ / {gap.get('daily_target', 0):.2f}€")
        print(f"  🔮 Prévision EOD: {forecast.get('predicted_eod', 0):.2f}€")
        print(f"  🚨 Urgence: {urgency.get('urgency_level')} ({urgency.get('urgency_percentage')})")
    
    except Exception as e:
        print(f"  ❌ Erreur: {e}")
        import traceback
        traceback.print_exc()

print(f"\n{'='*80}")
print("✅ WORKFLOW COMPLÉTÉ !")
print(f"{'='*80}\n")

# Afficher résumé global
print("\n📊 RÉSUMÉ GLOBAL:\n")
summary = workflow.get_execution_summary()
print(f"  • Exécutions: {summary['total_executions']}")
print(f"  • Montant total: {summary['current_state']['total_amount']:.2f}€")
print(f"  • Prévision EOD: {summary['forecast']['predicted_eod']:.2f}€")
print(f"  • Urgence: {summary['urgency']['level']} ({summary['urgency']['score']})")