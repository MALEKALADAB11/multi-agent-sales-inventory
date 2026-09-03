"""
Detailed visualization of inventory agent graphs with node descriptions
and complete orchestration workflow.
"""

from app.inventory.agents.analysis.agent import create_analysis_agent
from app.inventory.agents.context.agent import create_context_agent  
from app.inventory.agents.decision.agent import InventoryDecisionAgent

def print_agent_overview():
    """Print overview of the complete inventory agent system."""
    print("=" * 70)
    print("COMPLETE INVENTORY AGENT SYSTEM OVERVIEW")
    print("=" * 70)
    print()
    print("ORCHESTRATION WORKFLOW:")
    print("-" * 70)
    print("1. Analysis Agent and Context Agent run in PARALLEL")
    print("2. Decision Agent takes outputs from BOTH agents")
    print("3. Decision Agent produces final recommendation")
    print()
    print("DETAILED FLOW:")
    print("-" * 70)
    print("Orchestrator.start_batch()")
    print("  ├── AnalysisAgent.run(sku) [Parallel]")
    print("  │   ├── fetch_node:     Get stock, forecast, product data")
    print("  │   ├── compute_node:   Calculate metrics, risk assessment")
    print("  │   └── reason_node:    LLM evaluation of conflicts")
    print("  │")
    print("  ├── ContextAgent.run(sku) [Parallel]")
    print("  │   ├── fetch_signals_node: Get promotions, weather, events, history")
    print("  │   └── interpret_node:      LLM synthesizes demand_uplift_pct")
    print("  │")
    print("  └── DecisionAgent.run(sku, analysis_report, context_report)")
    print("      ├── constraints_check_node: Check hard constraints (MOQ, budget, etc.)")
    print("      └── decide_node:            LLM makes final recommendation")
    print()
    print("=" * 70)
    print()

def visualize_analysis_agent_detailed():
    """Visualize Analysis Agent with detailed node descriptions."""
    print("=" * 70)
    print("ANALYSIS AGENT - DETAILED STRUCTURE")
    print("=" * 70)
    print()
    print("GRAPH STRUCTURE:")
    print("-" * 70)
    
    agent = create_analysis_agent(use_llm=False)
    graph = agent.graph
    graph_structure = graph.get_graph()
    
    print(graph_structure.draw_mermaid())
    
    print()
    print("NODE DESCRIPTIONS:")
    print("-" * 70)
    print("fetch_node:")
    print("  - Retrieves current stock levels from database")
    print("  - Gets product information and constraints")
    print("  - Fetches sales history and forecasts")
    print("  - Uses preloaded data if available (batch optimization)")
    print()
    print("compute_node:")
    print("  - Calculates inventory metrics (days of stock, turnover)")
    print("  - Computes risk assessment (stockout risk, overstock risk)")
    print("  - Applies business objectives (balanced, aggressive, conservative)")
    print("  - Rule-based logic, no LLM calls")
    print()
    print("reason_node:")
    print("  - LLM evaluates cross-dimensional conflicts")
    print("  - Checks for inconsistencies in risk assessment")
    print("  - Provides reasoning source (llm vs rule_based)")
    print("  - Can be disabled for fast rule-based mode")
    print()
    print("STATE MANAGEMENT:")
    print("-" * 70)
    print("AgentState includes:")
    print("  - messages: LangChain message sequence")
    print("  - sku, store_id, business_objective")
    print("  - fetch_data: Raw data from fetch_node")
    print("  - computed_metrics: Results from compute_node")
    print("  - preloaded_stock, preloaded_product: Batch optimization data")
    print()

def visualize_context_agent_detailed():
    """Visualize Context Agent with detailed node descriptions."""
    print("=" * 70)
    print("CONTEXT AGENT - DETAILED STRUCTURE")
    print("=" * 70)
    print()
    print("GRAPH STRUCTURE:")
    print("-" * 70)
    
    agent = create_context_agent(use_llm=False)
    graph = agent.graph
    graph_structure = graph.get_graph()
    
    print(graph_structure.draw_mermaid())
    
    print()
    print("NODE DESCRIPTIONS:")
    print("-" * 70)
    print("fetch_signals_node:")
    print("  - Gathers historical promotion performance")
    print("  - Gets current active promotions")
    print("  - Fetches weather data and forecasts")
    print("  - Retrieves holiday and event calendars")
    print("  - Analyzes historical patterns from sales history")
    print("  - Pure Python, no LLM calls")
    print()
    print("interpret_node:")
    print("  - LLM synthesizes all signals into demand_uplift_pct")
    print("  - Provides interpretation of dominant factors")
    print("  - Assigns confidence level (high/medium/low)")
    print("  - Identifies dominant_signal (promotions, weather, etc.)")
    print("  - Rule-based fallback if LLM disabled")
    print()
    print("STATE MANAGEMENT:")
    print("-" * 70)
    print("ContextAgentState includes:")
    print("  - messages: LangChain message sequence")
    print("  - sku, store_id")
    print("  - signals: Raw signals from fetch_signals_node")
    print("  - context_report: Final interpreted context")
    print()

def visualize_decision_agent_detailed():
    """Visualize Decision Agent with detailed node descriptions."""
    print("=" * 70)
    print("DECISION AGENT - DETAILED STRUCTURE")
    print("=" * 70)
    print()
    print("GRAPH STRUCTURE:")
    print("-" * 70)
    
    agent = InventoryDecisionAgent(use_llm=False)
    graph = agent.graph
    graph_structure = graph.get_graph()
    
    print(graph_structure.draw_mermaid())
    
    print()
    print("CONDITIONAL ROUTING:")
    print("-" * 70)
    print("constraints_check_node routes to:")
    print("  - END if decision already set (hard constraint violation)")
    print("  - decide node otherwise (normal flow)")
    print()
    print("NODE DESCRIPTIONS:")
    print("-" * 70)
    print("constraints_check_node:")
    print("  - Checks hard constraints (MOQ, budget limits, etc.)")
    print("  - Can immediately block decisions that violate constraints")
    print("  - Sets decision field if hard block detected")
    print("  - Rule-based, no LLM calls")
    print()
    print("decide_node:")
    print("  - LLM makes final recommendation (ORDER/HOLD/MONITOR/EXPEDITE)")
    print("  - Calculates order quantity with EOQ-based logic")
    print("  - Provides decision rationale and trade-offs")
    print("  - Assigns confidence level and urgency")
    print("  - Determines if human escalation is needed")
    print("  - Rule-based fallback if LLM disabled")
    print()
    print("STATE MANAGEMENT:")
    print("-" * 70)
    print("DecisionAgentState includes:")
    print("  - messages: LangChain message sequence")
    print("  - sku, store_id, business_objective")
    print("  - baseline_report: Output from Analysis Agent")
    print("  - context_report: Output from Context Agent")
    print("  - adjusted_metrics: Metrics with demand uplift applied")
    print("  - decision: Final decision from decide_node")
    print("  - constraints_violations: List of constraint violations")
    print()

def show_graph_statistics():
    """Show statistics about the compiled graphs."""
    print("=" * 70)
    print("GRAPH STATISTICS")
    print("=" * 70)
    print()
    
    agents = {
        "Analysis Agent": create_analysis_agent(use_llm=False),
        "Context Agent": create_context_agent(use_llm=False),
        "Decision Agent": InventoryDecisionAgent(use_llm=False)
    }
    
    for name, agent in agents.items():
        graph = agent.graph
        graph_structure = graph.get_graph()
        
        print(f"{name}:")
        print(f"  - Node count: {len(graph_structure.nodes)}")
        print(f"  - Entry point: {graph_structure.entry_point}")
        print(f"  - Has conditional edges: {len(graph_structure.branches) > 0}")
        print()

if __name__ == "__main__":
    try:
        print_agent_overview()
        visualize_analysis_agent_detailed()
        visualize_context_agent_detailed()
        visualize_decision_agent_detailed()
        show_graph_statistics()
        
        print("=" * 70)
        print("VISUALIZATION COMPLETE")
        print("=" * 70)
        print()
        print("To get ASCII art visualization, install grandalf:")
        print("  pip install grandalf")
        print()
        print("To visualize Mermaid diagrams:")
        print("  1. Copy the Mermaid code above")
        print("  2. Paste it into https://mermaid.live/")
        print("  3. See interactive flowchart diagrams")
        
    except Exception as e:
        print(f"Error during visualization: {e}")
        import traceback
        traceback.print_exc()
