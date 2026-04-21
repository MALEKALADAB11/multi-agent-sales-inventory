"""
Graphe Workflow APP2 Analyzer - Sans dépendances LangGraph
Visualisation ASCII du flux
"""

from typing import Dict, Any
from datetime import datetime
from .agent import App2Analyzer

def visualize_graph_ascii():
    """Affiche le graphe en ASCII art"""
    
    graph_ascii = """
╔════════════════════════════════════════════════════════════════════════════════╗
║                       🎯 APP2 ANALYZER WORKFLOW GRAPH                         ║
╚════════════════════════════════════════════════════════════════════════════════╝

                                  [START]
                                     │
                    ┌────────────────▼────────────────┐
                    │                                 │
                    │   📥 NODE 1: RECEIVE POS        │
                    │   • Valide transaction          │
                    │   • Accumule montant            │
                    │   • Total reçu                  │
                    │                                 │
                    └────────────────┬────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │                                 │
                    │   📊 NODE 2: CALCULATE GAP      │
                    │   • Écart vs objectif           │
                    │   • Progression temporelle      │
                    │   • Variance                    │
                    │   • Transactions count          │
                    │                                 │
                    └────────────────┬────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │                                 │
                    │   🔮 NODE 3: PREDICT EOD        │
                    │   • Série temporelle ARIMA      │
                    │   • Confiance: 65%-85%          │
                    │   • Intervalles de confiance    │
                    │   • Montant restant prévu       │
                    │                                 │
                    └────────────────┬────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │                                 │
                    │   🚨 NODE 4: DETECT URGENCY     │
                    │   • HAUTE / MOYENNE / FAIBLE    │
                    │   • Score: 0-100                │
                    │   • Actions recommandées        │
                    │   • Taux d'atteinte             │
                    │                                 │
                    └────────────────┬────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │                                 │
                    │   📈 NODE 5: GENERATE REPORT    │
                    │   • Résumé complet              │
                    │   • Export JSON                 │
                    │   • Recommandations             │
                    │   • Historique                  │
                    │                                 │
                    └────────────────┬────────────────┘
                                     │
                                  [END]

════════════════════════════════════════════════════════════════════════════════

STATE FLOW:
   
   Transaction POS
        ↓
   Gap Analysis ─→ Forecast ─→ Urgency Detection ─→ Report
        ↓              ↓             ↓                  ↓
   current_total   prediction    urgency_level    final_analysis
   transactions    confidence    actions          summary
   avg_value       model         score            json_export

════════════════════════════════════════════════════════════════════════════════

NODES DESCRIPTION:

  1️⃣  RECEIVE POS (Réception)
      • Input: Transaction JSON
      • Output: Validation + accumulation
      • Process: Ajout au buffer du jour

  2️⃣  CALCULATE GAP (Écart Objectif)
      • Input: Transactions cumulées + Cible
      • Output: Gap analysis
      • Process: Comparaison vs objectif

  3️⃣  PREDICT EOD (Prévision)
      • Input: Historique transactions
      • Output: Prévision fin de jour
      • Model: ARIMA(1,0,1)

  4️⃣  DETECT URGENCY (Urgence)
      • Input: Gap + Prévision
      • Output: Niveau d'urgence + Actions
      • Process: Scoring + recommandations

  5️⃣  GENERATE REPORT (Rapport)
      • Input: Tous les analyses
      • Output: Rapport complet
      • Format: JSON + Résumé texte

════════════════════════════════════════════════════════════════════════════════
    """
    
    return graph_ascii


class App2AnalyzerWorkflow:
    """Workflow d'analyse APP2 sans LangGraph"""
    
    def __init__(self, analyzer: App2Analyzer, daily_target: float):
        self.analyzer = analyzer
        self.daily_target = daily_target
        self.execution_log = []
    
    def execute_workflow(self, transaction: Dict[str, Any]) -> Dict[str, Any]:
        """Exécute le workflow complet pour une transaction"""
        
        # Node 1: Receive POS
        print("\n📥 [NODE 1] Receiving POS Transaction...")
        pos_result = self.analyzer.receive_pos_stream(transaction)
        print(f"   ✓ Transaction {pos_result.get('transaction_id')} reçue: {pos_result.get('amount')}€")
        
        # Node 2: Calculate Gap
        print("\n📊 [NODE 2] Calculating Objective Gap...")
        gap_analysis = self.analyzer.calculate_objective_gap()
        print(f"   ✓ Écart: {gap_analysis.get('gap', 0):.2f}€ ({gap_analysis.get('gap_percentage', 0):.1f}%)")
        
        # Node 3: Predict EOD
        print("\n🔮 [NODE 3] Predicting EOD with Time Series Model...")
        forecast = self.analyzer.predict_eod_timeseries()
        print(f"   ✓ Prévision: {forecast.get('predicted_eod', 0):.2f}€")
        print(f"   ✓ Modèle: {forecast.get('model_used')}")
        print(f"   ✓ Confiance: {forecast.get('confidence_percentage')}")
        
        # Node 4: Detect Urgency
        print("\n🚨 [NODE 4] Detecting Urgency Level...")
        urgency = self.analyzer.detect_urgency()
        print(f"   ✓ Niveau: {urgency.get('urgency_level')}")
        print(f"   ✓ Score: {urgency.get('urgency_percentage')}")
        
        # Node 5: Generate Report
        print("\n📈 [NODE 5] Generating Final Report...")
        final_analysis = self.analyzer.full_analysis(self.daily_target)
        print(f"   ✓ Rapport généré")
        
        # Log execution
        execution_result = {
            "timestamp": datetime.now().isoformat(),
            "transaction_id": transaction.get("id"),
            "pos_result": pos_result,
            "gap_analysis": gap_analysis,
            "forecast": forecast,
            "urgency": urgency,
            "final_analysis": final_analysis
        }
        self.execution_log.append(execution_result)
        
        return execution_result
    
    def get_execution_summary(self) -> Dict[str, Any]:
        """Retourne un résumé de l'exécution"""
        
        if not self.execution_log:
            return {"status": "No executions yet"}
        
        latest = self.execution_log[-1]
        
        return {
            "total_executions": len(self.execution_log),
            "latest_execution": latest["timestamp"],
            "current_state": {
                "total_amount": latest["gap_analysis"].get("current_total"),
                "target": latest["gap_analysis"].get("daily_target"),
                "gap": latest["gap_analysis"].get("gap"),
                "gap_percentage": latest["gap_analysis"].get("gap_percentage"),
            },
            "forecast": {
                "predicted_eod": latest["forecast"].get("predicted_eod"),
                "confidence": latest["forecast"].get("confidence_percentage"),
                "model": latest["forecast"].get("model_used"),
            },
            "urgency": {
                "level": latest["urgency"].get("urgency_level"),
                "score": latest["urgency"].get("urgency_percentage"),
                "action_required": latest["urgency"].get("action_required"),
            }
        }