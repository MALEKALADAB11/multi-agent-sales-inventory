import asyncio
from typing import List, Dict, Any
from datetime import datetime
from schemas import MCPRequest, MCPResponse, Transaction, InventoryLevel

class MCPClient:
    """Mock MCP Client for dynamic data pulls."""
    
    def __init__(self):
        self.mock_data = {
            "sales_transactions": [
                Transaction(id="TXN001", amount=150.5, timestamp=datetime.now(), product_id="PROD123"),
                Transaction(id="TXN002", amount=89.99, timestamp=datetime.now(), product_id="PROD456"),
            ],
            "inventory": [
                InventoryLevel(product_id="PROD123", stock=5, threshold=10),
                InventoryLevel(product_id="PROD456", stock=50),
            ]
        }
    
    async def pull(self, request: MCPRequest) -> MCPResponse:
        await asyncio.sleep(0.1)
        
        if request.action == "pull_transactions" and request.module == "sales":
            return MCPResponse(success=True, data=self.mock_data["sales_transactions"])
        
        elif request.action == "pull_inventory" and request.module == "inventory":
            snapshot = self.mock_data["inventory"].copy()
            for item in snapshot:
                item.is_rupture = item.stock < item.threshold
            return MCPResponse(success=True, data=snapshot)
        
        return MCPResponse(success=False, error="Unsupported request")