import asyncio
import random
from datetime import datetime
from typing import List, Set, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Imports de tes fichiers locaux
from agent_registry import AGENT_DATA 
from schemas import (
    MonitoringData, AgentState, AgentStatus, 
    MCPRequest, MCPAlert
)
from mcp_client import MCPClient

app = FastAPI(title="Ooredoo Multi-Agent Monitor")

# Configuration CORS complète pour éviter les blocages Angular
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

manager = ConnectionManager()
mcp = MCPClient()

# --- 🛠 CORRECTIF DES ROUTES 404 (IMPORTANT) ---

@app.get("/")
async def root():
    return {"status": "online", "message": "Ooredoo Backend API"}

@app.get("/api/v1/stores/{store_id}/metrics")
async def get_metrics(store_id: str):
    return {
        "store_id": store_id,
        "revenue_daily": 4250.5,
        "active_agents": len(AGENT_DATA),
        "status": "online",
        "performance_score": 98.2
    }

@app.get("/api/v1/stores/{store_id}/advisors")
async def get_advisors(store_id: str):
    return [
        {"id": "APP04", "name": "Strategist", "type": "Strategy", "status": "active"},
        {"id": "APP07", "name": "Coach Agent", "type": "Coaching", "status": "active"},
        {"id": "APP05-RAG", "name": "Knowledge RAG", "type": "Support", "status": "active"}
    ]

@app.get("/api/v1/forecast/eod/{store_id}")
async def get_eod_forecast(store_id: str):
    return {"forecast_value": 5100, "confidence": 0.95, "unit": "TND"}

@app.get("/api/v1/forecast/hourly/{store_id}")
async def get_hourly_forecast(store_id: str):
    return {
        "store_id": store_id,
        "data": [random.randint(50, 300) for _ in range(24)],
        "labels": [f"{i}h" for i in range(24)]
    }

@app.post("/api/v1/stores/{store_id}/simulate")
async def simulate_pos(store_id: str):
    """Simule une vente POS pour tester le WebSocket"""
    return {
        "status": "success",
        "message": "Transaction simulée",
        "amount": random.randint(50, 500)
    }

# --- 🧠 LOGIQUE AGENTIQUE & WEBSOCKET ---

class OoredooSupervisionAgent:
    def __init__(self):
        self.agents_config = AGENT_DATA
    
    async def generate_realtime_metrics(self) -> MonitoringData:
        """Génère des métriques en temps réel pour le dashboard"""
        agents_states = []
        
        for agent_id, info in self.agents_config.items():
            # Ajoute du mouvement ici !
            random_latency = f"{random.uniform(0.1, 0.9):.2f}s"
            random_load = f"{random.randint(5, 60)}%"
            
            # ✅ FIXED: Added agent_type to match your schema
            agents_states.append(AgentState(
                id=agent_id,
                name=info["name"],
                agent_type=info.get("type", "Unknown"),  # Uses "type" from AGENT_DATA
                status=AgentStatus.RUNNING,
                last_activity=datetime.now(),
                metrics={"latency": random_latency, "load": random_load},
                health_score=random.randint(90, 100)
            ))
        
        return MonitoringData(
            timestamp=datetime.now(),
            agents=agents_states,
            system_health=random.uniform(0.85, 0.99),
            alerts=[]
        )

monitor = OoredooSupervisionAgent()

@app.websocket("/ws/store/{store_id}")
async def websocket_endpoint(websocket: WebSocket, store_id: str):
    await manager.connect(websocket)
    try:
        while True:
            # Envoie des métriques au format attendu par Angular
            metrics_update = {
                "type": "metrics_update",
                "ca_today": random.randint(3000, 5000),
                "ca_target": 8500,
                "attainment": random.randint(70, 95),
                "visitors_h": random.randint(15, 45),
                "niveau_urgence": random.choice(["HIGH", "MEDIUM", "LOW"]),
                "ecart_objectif": round(random.uniform(-100, 100), 1),
                "forecast_eod": random.randint(5000, 7000),
                "forecast_ci_low": random.randint(4500, 6000),
                "forecast_mape": round(random.uniform(5, 15), 2),
                "last_cycle_id": f"CYCLE-{random.randint(1000, 9999)}",
                "advisors": [
                    {
                        "id": "ADV001",
                        "name": "Ali Ben Salah",
                        "performance": random.randint(70, 100),
                        "status": "active"
                    },
                    {
                        "id": "ADV002",
                        "name": "Fatma Chakroun",
                        "performance": random.randint(70, 100),
                        "status": "active"
                    }
                ]
            }
            
            await websocket.send_json(metrics_update)
            await asyncio.sleep(2)  # Update every 2 seconds
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.websocket("/ws/advisor/{advisor_id}")
async def advisor_websocket(websocket: WebSocket, advisor_id: str):
    await manager.connect(websocket)
    try:
        while True:
            coach_update = {
                "type": "coach_update",
                "advisor_id": advisor_id,
                "coaching_message": f"Performance update for {advisor_id}",
                "score": random.randint(70, 100),
                "timestamp": datetime.now().isoformat()
            }
            await websocket.send_json(coach_update)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)