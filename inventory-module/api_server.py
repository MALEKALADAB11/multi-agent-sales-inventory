"""
API Server Entry Point
======================
Run with:
    uvicorn api_server:app --reload --port 8000

Or add to your existing server if you already have one running.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router as inventory_router

app = FastAPI(
    title="Inventory Agent API",
    description="HTTP interface for the InventoryAnalysisAgent pipeline",
    version="0.1.0",
)

# CORS — allow the Angular dev server and production origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",    # Angular dev server
        "http://localhost:4300",    # alternate dev port
        # Add your production origin here, e.g. "https://dashboard.yourcompany.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inventory_router, prefix="/api/inventory")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "inventory-agent-api"}
