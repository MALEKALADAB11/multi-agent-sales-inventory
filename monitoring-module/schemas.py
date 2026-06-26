from pydantic import BaseModel
from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum

class AgentStatus(str, Enum):
    RUNNING = "running"
    IDLE = "idle"
    ERROR = "error"
    STOPPED = "stopped"

class AgentState(BaseModel):
    id: str
    name: str
    agent_type: str  # ✅ ADDED THIS LINE
    status: AgentStatus
    last_activity: datetime
    metrics: Dict[str, Any]
    health_score: int

class MonitoringData(BaseModel):
    timestamp: datetime
    agents: List[AgentState]
    system_health: float
    alerts: List[str]

class MCPRequest(BaseModel):
    action: str
    module: str
    params: Optional[Dict[str, Any]] = {}

class MCPResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None

class MCPAlert(BaseModel):
    level: str
    message: str
    timestamp: datetime

class Transaction(BaseModel):
    id: str
    amount: float
    timestamp: datetime
    product_id: str

class InventoryLevel(BaseModel):
    product_id: str
    stock: int
    threshold: int = 10
    is_rupture: bool = False
