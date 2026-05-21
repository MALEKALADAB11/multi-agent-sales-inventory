"""
monitoring.py — Agent Monitoring Endpoints
FastAPI endpoints pour la page Monitoring (BI mode PRO)
"""

from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta
from typing import List, Dict, Any
import random

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])

# ══════════════════════════════════════════════════════════════
# MOCK DATA SIMULATION (Replace with real LangGraph traces later)
# ══════════════════════════════════════════════════════════════

AGENT_IDS = [
    "APP08", "APP06", "APP02", "APP05", "APP07",
    "APP03", "APP04", "APP09", "APP10", "APP11"
]

def _get_agent_executions(last_n_minutes: int = 60) -> List[Dict[str, Any]]:
    """
    Simule les exécutions d'agents sur les N dernières minutes.
    Plus tard: lire depuis LangGraph checkpointer ou logs PostgreSQL.
    """
    executions = []
    now = datetime.now()
    
    for i in range(50):  # 50 exécutions sur la dernière heure
        agent_id = random.choice(AGENT_IDS)
        timestamp = now - timedelta(minutes=random.randint(0, last_n_minutes))
        
        # Simulate realistic metrics
        success = random.random() > 0.05  # 95% success rate
        latency = random.uniform(0.1, 3.5)
        tokens = random.randint(500, 2000)
        cost = tokens * 0.00002  # $0.02 per 1K tokens (GPT-4 pricing)
        
        executions.append({
            "agent_id": agent_id,
            "timestamp": timestamp.isoformat(),
            "status": "success" if success else "failed",
            "latency": round(latency, 2),
            "tokens_used": tokens,
            "cost_usd": round(cost, 4),
        })
    
    return executions


# ══════════════════════════════════════════════════════════════
# STEP 1: KPI BAR ENDPOINT
# ══════════════════════════════════════════════════════════════

@router.get("/kpis")
async def get_monitoring_kpis():
    """
    Retourne les KPIs globaux pour la barre supérieure.
    
    KPIs calculés:
    - HEALTHY: agents avec success_rate > 95% ET latency < 5s
    - RUNNING: agents actuellement en exécution
    - FAILED: nombre d'échecs dans la dernière heure
    - AVG_LATENCY: latence moyenne tous agents confondus
    - COST_TODAY: coût total API depuis minuit (USD)
    """
    
    executions = _get_agent_executions(last_n_minutes=60)
    
    # 1. Calculate per-agent metrics
    agent_stats: Dict[str, Dict] = {}
    for exec in executions:
        aid = exec["agent_id"]
        if aid not in agent_stats:
            agent_stats[aid] = {
                "total": 0,
                "success": 0,
                "latencies": [],
                "last_status": None,
            }
        
        agent_stats[aid]["total"] += 1
        if exec["status"] == "success":
            agent_stats[aid]["success"] += 1
        agent_stats[aid]["latencies"].append(exec["latency"])
        agent_stats[aid]["last_status"] = exec["status"]
    
    # 2. HEALTHY count (success_rate > 95% AND avg_latency < 5s)
    healthy_count = 0
    for aid, stats in agent_stats.items():
        success_rate = stats["success"] / stats["total"]
        avg_latency = sum(stats["latencies"]) / len(stats["latencies"])
        
        if success_rate > 0.95 and avg_latency < 5.0:
            healthy_count += 1
    
    # 3. RUNNING count (agents with status = "running" in last 2 min)
    recent_executions = [e for e in executions 
                         if (datetime.now() - datetime.fromisoformat(e["timestamp"])).seconds < 120]
    running_count = len(set(e["agent_id"] for e in recent_executions 
                            if e["status"] == "running"))
    
    # Simulate some running agents if none found
    if running_count == 0:
        running_count = random.randint(1, 3)
    
    # 4. FAILED count (last hour)
    failed_count = sum(1 for e in executions if e["status"] == "failed")
    
    # 5. AVG LATENCY (all agents)
    all_latencies = [e["latency"] for e in executions]
    avg_latency = round(sum(all_latencies) / len(all_latencies), 1) if all_latencies else 0.0
    
    # 6. COST TODAY (since midnight)
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_executions = [e for e in executions 
                        if datetime.fromisoformat(e["timestamp"]) >= today_start]
    cost_today = sum(e["cost_usd"] for e in today_executions)
    
    return {
        "healthy": healthy_count,
        "running": running_count,
        "failed": failed_count,
        "avg_latency": avg_latency,
        "cost_today_usd": round(cost_today, 2),
        "cost_today_tnd": round(cost_today * 3.1, 2),  # USD to TND conversion
        "timestamp": datetime.now().isoformat(),
    }