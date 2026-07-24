"""
Smoke test du contrat API — gate obligatoire entre chaque phase du refactoring.

Usage :
    python scripts/smoke_test.py                 # serveur déjà lancé sur :8000
    SMOKE_STORE=I75 python scripts/smoke_test.py # autre boutique

Vérifie :
  - endpoints REST du contrat frontend (D:\\frontend\\PFE\\src\\app\\core\\services)
  - login + endpoint authentifié
  - les 3 WebSockets (connexion + 2s d'écoute)
  - présence dans /openapi.json des routes lourdes (LLM) non appelées
  - sauvegarde un snapshot des paths OpenAPI pour diff entre phases

Exit code 0 = tout vert, sinon 1.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = os.getenv("SMOKE_BASE_URL", "http://localhost:8000")
WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")
STORE = os.getenv("SMOKE_STORE", "I14")
ADVISOR = os.getenv("SMOKE_ADVISOR", "adv-zi")
USERNAME = os.getenv("SMOKE_USER", "managermenzah")
PASSWORD = os.getenv("SMOKE_PASSWORD", "admin123")
SNAPSHOT = Path(__file__).parent / "openapi_snapshot.json"

FAILURES: list[str] = []
PASSED: list[str] = []


def check(label: str, ok: bool, detail: str = ""):
    if ok:
        PASSED.append(label)
        print(f"  OK   {label}")
    else:
        FAILURES.append(f"{label} — {detail}")
        print(f"  FAIL {label} — {detail}")


def rest_checks() -> str | None:
    """Endpoints REST. Retourne le token de session (ou None)."""
    token = None
    with httpx.Client(base_url=BASE, timeout=90) as c:

        def get(label, path, headers=None, expect=(200,), timeout=90):
            try:
                r = c.get(path, headers=headers or {}, timeout=timeout)
                check(label, r.status_code in expect,
                      f"HTTP {r.status_code}: {r.text[:120]}")
                return r
            except Exception as e:
                check(label, False, repr(e))
                return None

        get("GET /health", "/health")

        # Auth
        try:
            r = c.post("/api/auth/login",
                       json={"username": USERNAME, "password": PASSWORD})
            check("POST /api/auth/login", r.status_code == 200,
                  f"HTTP {r.status_code}: {r.text[:120]}")
            if r.status_code == 200:
                token = r.json()["token"]
        except Exception as e:
            check("POST /api/auth/login", False, repr(e))

        auth = {"Authorization": f"Bearer {token}"} if token else {}
        if token:
            get("GET /api/auth/me", "/api/auth/me", headers=auth)

        # Sales / stores
        get(f"GET /api/v1/stores/{STORE}/metrics",
            f"/api/v1/stores/{STORE}/metrics", headers=auth)
        get(f"GET /api/v1/stores/{STORE}/advisors",
            f"/api/v1/stores/{STORE}/advisors", headers=auth)
        get(f"GET /api/v1/forecast/eod/{STORE}",
            f"/api/v1/forecast/eod/{STORE}", headers=auth)

        # Coach (lecture seule — le chat POST est LLM, vérifié via openapi)
        get("GET /api/v1/coach/health", "/api/v1/coach/health", headers=auth)

        # Monitoring
        get("GET /api/monitoring/health", "/api/monitoring/health")
        get("GET /api/monitoring/cycles", "/api/monitoring/cycles")
        get("GET /api/monitoring/stats", "/api/monitoring/stats")

        # Inventory + supply (fast=true : orchestrateur rule-based, sans LLM —
        # le smoke ne doit pas dépendre d'Ollama ; cold start ~60s sur 143 SKUs)
        get(f"GET /api/inventory/store/{STORE}",
            f"/api/inventory/store/{STORE}?fast=true&page_size=10",
            headers=auth, timeout=240)
        get(f"GET /api/supply/purchase-orders/{STORE}",
            f"/api/supply/purchase-orders/{STORE}", headers=auth)

        # OpenAPI : routes lourdes présentes + snapshot pour diff inter-phases
        r = get("GET /openapi.json", "/openapi.json")
        if r is not None and r.status_code == 200:
            paths = sorted(r.json()["paths"].keys())
            for must in ["/api/v1/coach/chat", "/api/v1/cycle/trigger",
                         "/api/v1/stores/{store_id}/live-analysis",
                         "/api/v1/hitl/pending"]:
                check(f"openapi contient {must}", must in paths,
                      "route absente du schéma")
            if SNAPSHOT.exists():
                old = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
                missing = sorted(set(old) - set(paths))
                check("openapi : aucune route supprimée vs snapshot",
                      not missing, f"disparues: {missing[:10]}")
            else:
                SNAPSHOT.write_text(json.dumps(paths, indent=1),
                                    encoding="utf-8")
                print(f"  --   snapshot openapi initial écrit ({len(paths)} paths)")
    return token


async def ws_check(label: str, url: str):
    import websockets
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            try:
                await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                pass  # connexion ouverte sans message = OK
        check(label, True)
    except Exception as e:
        check(label, False, repr(e))


async def ws_checks():
    await ws_check(f"WS /ws/store/{STORE}", f"{WS_BASE}/ws/store/{STORE}")
    await ws_check(f"WS /ws/advisor/{ADVISOR}", f"{WS_BASE}/ws/advisor/{ADVISOR}")
    await ws_check(f"WS /api/inventory/ws/{STORE}",
                   f"{WS_BASE}/api/inventory/ws/{STORE}")


def main():
    print(f"Smoke test → {BASE} (store={STORE})")
    rest_checks()
    asyncio.run(ws_checks())
    print(f"\n{len(PASSED)} OK, {len(FAILURES)} FAIL")
    if FAILURES:
        for f in FAILURES:
            print(f"  ✗ {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
