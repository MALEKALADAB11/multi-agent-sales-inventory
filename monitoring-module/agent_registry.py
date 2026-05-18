from typing import List
from schemas import AgentState, AgentStatus 
from datetime import datetime

# LA SOURCE UNIQUE DE VÉRITÉ
AGENT_DATA = {
    "APP06": {"name": "Orchestrator", "type": "Orchestration"},
    "APP01": {"name": "Sales Agent", "type": "Sales"},
    "APP08": {"name": "InventoryWatcher", "type": "Inventory"},
    "APP03": {"name": "ForecastEngine", "type": "Inventory"},
    "APP05": {"name": "GapDetector", "type": "Inventory"},
    "APP04": {"name": "Strategist", "type": "Strategy"},
    "APP07": {"name": "Coach Agent", "type": "Coaching"},
    "APP05-RAG": {"name": "Knowledge RAG", "type": "Support"},
    "APP-MEM": {"name": "Memory Agent", "type": "Support"},
    "APP09": {"name": "Action Agent", "type": "Action"}
}

def get_all_agents_configs():
    """Retourne la configuration brute pour le monitoring"""
    return AGENT_DATA