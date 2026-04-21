"""
Graphe visuel LangGraph-style pour APP2 Analyzer
Génère un fichier PNG avec Graphviz
"""

from typing import Dict, Any
import os

def create_workflow_graph():
    """Crée le graphe visuel du workflow"""
    
    graph_code = """
digraph APP2_Analyzer_Workflow {
    rankdir=LR;
    node [shape=box, style=filled, fontname="Arial"];
    edge [fontname="Arial"];
    
    start [label="__start__", shape=ellipse, fillcolor="#90EE90"];
    
    receive_pos [label="receive_pos\\nReceive POS", fillcolor="#B0E0E6"];
    calculate_gap [label="calculate_gap\\nCalculate Gap", fillcolor="#FFE4B5"];
    predict_eod [label="predict_eod\\nPredict EOD", fillcolor="#DDA0DD"];
    detect_urgency [label="detect_urgency\\nDetect Urgency", fillcolor="#FFB6C1"];
    generate_report [label="generate_report\\nGenerate Report", fillcolor="#F0E68C"];
    
    end [label="__end__", shape=ellipse, fillcolor="#FFB6C6"];
    
    start -> receive_pos [label="transaction"];
    receive_pos -> calculate_gap [label="pos_result"];
    calculate_gap -> predict_eod [label="gap_analysis"];
    predict_eod -> detect_urgency [label="forecast"];
    detect_urgency -> generate_report [label="urgency"];
    generate_report -> end [label="final_analysis"];
}
"""
    
    return graph_code
    
    

def save_graph_visualization(output_path: str = "app2_workflow_graph"):
    """Sauvegarde le graphe en PNG"""
    
    try:
        import pydot
        
        graph_code = create_workflow_graph()
        
        # Parser le graphe
        graphs = pydot.graph_from_dot_data(graph_code)
        graph = graphs[0]
        
        # Sauvegarder en PNG
        graph.write_png(f"{output_path}.png")
        print(f"✅ Graphe sauvegardé: {output_path}.png")
        
        # Sauvegarder en SVG aussi
        graph.write_svg(f"{output_path}.svg")
        print(f"✅ Graphe sauvegardé: {output_path}.svg")
        
        # Sauvegarder en PDF
        graph.write_pdf(f"{output_path}.pdf")
        print(f"✅ Graphe sauvegardé: {output_path}.pdf")
        
        return True
        
    except ImportError:
        print("❌ pydot non installé")
        print("Installez: pip install pydot graphviz")
        return False
    except Exception as e:
        print(f"❌ Erreur: {e}")
        return False

def display_graph_ascii():
    """Affiche le graphe en ASCII"""
    
    ascii_graph = """
╔════════════════════════════════════════════════════════════════════════════════╗
║                    APP2 ANALYZER - WORKFLOW GRAPH                             ║
╚════════════════════════════════════════════════════════════════════════════════╝


          ┌──────────┐
          │ __start__│
          └────┬─────┘
               │ transaction
               │
        ┌──────▼──────────┐
        │   receive_pos   │
        │   📥 Receive   │
        │      POS       │
        └──────┬──────────┘
               │ pos_result
               │
        ┌──────▼──────────┐
        │ calculate_gap  │
        │   📊 Gap      │
        │    Analysis   │
        └──────┬──────────┘
               │ gap_analysis
               │
        ┌──────▼──────────┐
        │  predict_eod   │
        │   🔮 Predict   │
        │      EOD       │
        └──────┬──────────┘
               │ forecast
               │
        ┌──────▼──────────┐
        │ detect_urgency │
        │   🚨 Urgency   │
        │    Detection   │
        └──────┬──────────┘
               │ urgency
               │
        ┌──────▼──────────┐
        │generate_report │
        │   📈 Report    │
        │   Generation   │
        └──────┬──────────┘
               │ final_analysis
               │
          ┌────▼─────┐
          │  __end__ │
          └──────────┘

════════════════════════════════════════════════════════════════════════════════

FLUX DE DONNÉES:

  Transaction POS
       ↓
   [receive_pos] → Validation + Accumulation
       ↓
   [calculate_gap] → Écart vs Objectif
       ↓
   [predict_eod] → Prévision ARIMA
       ↓
   [detect_urgency] → Score + Actions
       ↓
   [generate_report] → Rapport Final

════════════════════════════════════════════════════════════════════════════════
    """
    
    return ascii_graph

if __name__ == "__main__":
    print(display_graph_ascii())
    print("\nGénération du graphe visuel...")
    save_graph_visualization()