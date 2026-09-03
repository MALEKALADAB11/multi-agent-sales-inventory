"""
Script to visualize LangGraph agent graphs for the inventory system.
Run this to see the node structure of each inventory agent.
"""

from app.inventory.agents.analysis.agent import create_analysis_agent
from app.inventory.agents.context.agent import create_context_agent  
from app.inventory.agents.decision.agent import InventoryDecisionAgent

def visualize_analysis_agent():
    """Visualize the Analysis Agent graph structure."""
    print("=" * 60)
    print("INVENTORY ANALYSIS AGENT GRAPH")
    print("=" * 60)
    print("Nodes: fetch -> compute -> reason")
    print()
    
    agent = create_analysis_agent(use_llm=False)  # No LLM needed for visualization
    graph = agent.graph
    
    # Get the graph structure
    graph_structure = graph.get_graph()
    
    # Print ASCII representation (requires grandalf package)
    try:
        print("ASCII Graph Structure:")
        print(graph_structure.print_ascii())
    except ImportError:
        print("ASCII Graph Structure: (Install grandalf with: pip install grandalf)")
    
    # Print Mermaid format (for documentation/tools - no extra package needed)
    print("\nMermaid Format:")
    print(graph_structure.draw_mermaid())
    
    return graph_structure

def visualize_context_agent():
    """Visualize the Context Agent graph structure."""
    print("=" * 60)
    print("INVENTORY CONTEXT AGENT GRAPH")
    print("=" * 60)
    print("Nodes: fetch_signals -> interpret")
    print()
    
    agent = create_context_agent(use_llm=False)
    graph = agent.graph
    
    graph_structure = graph.get_graph()
    
    try:
        print("ASCII Graph Structure:")
        print(graph_structure.print_ascii())
    except ImportError:
        print("ASCII Graph Structure: (Install grandalf with: pip install grandalf)")
    
    print("\nMermaid Format:")
    print(graph_structure.draw_mermaid())
    
    return graph_structure

def visualize_decision_agent():
    """Visualize the Decision Agent graph structure."""
    print("=" * 60)
    print("INVENTORY DECISION AGENT GRAPH")
    print("=" * 60)
    print("Nodes: constraints_check -> decide (with conditional routing)")
    print()
    
    agent = InventoryDecisionAgent(use_llm=False)
    graph = agent.graph
    
    graph_structure = graph.get_graph()
    
    try:
        print("ASCII Graph Structure:")
        print(graph_structure.print_ascii())
    except ImportError:
        print("ASCII Graph Structure: (Install grandalf with: pip install grandalf)")
    
    print("\nMermaid Format:")
    print(graph_structure.draw_mermaid())
    
    return graph_structure

def print_node_details(graph_structure, agent_name):
    """Print detailed information about nodes in the graph."""
    print(f"\n{agent_name} - Node Details:")
    print("-" * 40)
    
    # Get all nodes
    nodes = graph_structure.nodes
    
    for node_id in nodes:
        print(f"Node: {node_id}")
        # You can add more details here if needed

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("INVENTORY AGENT GRAPH VISUALIZATION")
    print("=" * 60 + "\n")
    
    try:
        # Visualize each agent
        analysis_graph = visualize_analysis_agent()
        print("\n")
        
        context_graph = visualize_context_agent()
        print("\n")
        
        decision_graph = visualize_decision_agent()
        print("\n")
        
        print("=" * 60)
        print("VISUALIZATION COMPLETE")
        print("=" * 60)
        print("\nYou can use the Mermaid format in tools like:")
        print("- https://mermaid.live/ (online Mermaid editor)")
        print("- Markdown files that support Mermaid")
        print("- Documentation tools that support Mermaid diagrams")
        
    except Exception as e:
        print(f"Error during visualization: {e}")
        import traceback
        traceback.print_exc()
