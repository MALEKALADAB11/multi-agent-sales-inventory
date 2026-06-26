"""
Lance le serveur FastAPI WebSocket.
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.websocket_endpoint:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
