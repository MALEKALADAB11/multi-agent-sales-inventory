"""Shim de compatibilité — l'application vit dans app/main.py.

    uvicorn main:app --port 8000
"""

from app.main import app  # noqa: F401
