"""
common.py — Infrastructure partagée des évaluations.

Client LLM minimal (OpenAI-compatible) indépendant du code applicatif :
un harnais d'éval qui dépend du code qu'il évalue biaise le benchmark
(mêmes fallbacks, mêmes retries). Ici : un POST, un timeout, une mesure.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

# Console Windows en cp1252 : sans ça, le premier caractère non-ASCII plante le run.
import sys
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

EVALS_DIR    = Path(__file__).resolve().parent
DATASETS_DIR = EVALS_DIR / "datasets"
RESULTS_DIR  = EVALS_DIR / "results"


# ══════════════════════════════════════════════════════════════════════════════
# Providers (OpenAI-compatible chat/completions)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Provider:
    name:     str
    base_url: str
    api_key:  str
    models:   list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.models)


def _groq_keys() -> list[str]:
    keys = [os.getenv("GROQ_API_KEY") or ""] + (os.getenv("GROQ_API_KEYS") or "").split(",")
    return [k.strip() for k in keys if k.strip()]


def load_providers() -> dict[str, Provider]:
    """Providers configurés via .env — mêmes modèles que la rotation du coach."""
    groq = _groq_keys()
    return {
        "mistral": Provider(
            name="mistral",
            base_url="https://api.mistral.ai/v1",
            api_key=os.getenv("MISTRAL_API_KEY", ""),
            models=[
                os.getenv("MISTRAL_MODEL_SMART", "mistral-large-latest"),
                os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
            ],
        ),
        "groq": Provider(
            name="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key=groq[0] if groq else "",
            models=[
                os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
                os.getenv("GROQ_MODEL_FALLBACK", "llama-3.3-70b-versatile"),
            ],
        ),
        "openrouter": Provider(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
            models=[
                os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-nano-30b-a3b:free"),
                os.getenv("OPENROUTER_MODEL_FALLBACK", "nvidia/nemotron-3-super-120b-a12b:free"),
            ],
        ),
    }


@dataclass
class ChatResult:
    text:          str
    latency_ms:    float
    model:         str
    provider:      str
    prompt_tokens: int = 0
    output_tokens: int = 0
    error:         str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text.strip())


def chat(
    provider: Provider,
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 700,
    timeout: float = 60.0,
    response_json: bool = False,
) -> ChatResult:
    """Un appel chat/completions, latence bout-en-bout mesurée. Ne lève jamais."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}
    # nemotron : couper le raisonnement, sinon le budget part en chaîne de pensée
    if "nemotron" in model:
        payload["reasoning"] = {"enabled": False}

    t0 = time.perf_counter()
    try:
        resp = httpx.post(
            f"{provider.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {provider.api_key}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        latency = (time.perf_counter() - t0) * 1000
        if resp.status_code != 200:
            return ChatResult("", latency, model, provider.name,
                              error=f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        usage = data.get("usage") or {}
        return ChatResult(
            text=(data["choices"][0]["message"].get("content") or "").strip(),
            latency_ms=latency,
            model=model,
            provider=provider.name,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
        )
    except Exception as e:
        return ChatResult("", (time.perf_counter() - t0) * 1000, model,
                          provider.name, error=f"{type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# I/O datasets & résultats
# ══════════════════════════════════════════════════════════════════════════════

def load_dataset(name: str) -> list[dict]:
    path = DATASETS_DIR / name
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["cases"] if isinstance(data, dict) else data


def save_results(name: str, payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload.setdefault("run_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    path = RESULTS_DIR / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n  Résultats → {path}")
    return path


def extract_json(text: str) -> Optional[dict]:
    """Extrait le premier objet JSON d'une réponse LLM (tolère les fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None
