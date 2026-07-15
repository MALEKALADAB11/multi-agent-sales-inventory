"""
rag/embeddings.py — Embeddings Ollama (nomic-embed-text) avec cache et batch.

Deux détails qui font la différence en qualité/coût :
  1. Préfixes de tâche. nomic-embed-text est entraîné en asymétrique : une requête
     doit être préfixée "search_query: " et un document "search_document: ".
     Sans ça on perd du recall sur les requêtes courtes.
  2. Cache disque. Ré-indexer 4 500 produits coûte ~4 min d'Ollama ; avec le cache,
     seuls les documents dont le texte a changé sont réembeddés.

L'appel batch (/api/embed) est utilisé en priorité, avec repli sur l'endpoint
unitaire historique (/api/embeddings) pour les vieux Ollama.
"""

import hashlib
import json
import logging
import os
import threading
import time
from typing import Iterable, Optional

import requests

from app.sales.data.rag.settings import (
    CACHE_DIR,
    EMBED_BATCH,
    EMBED_DIM,
    EMBED_DOC_PREFIX,
    EMBED_MAX_CHARS,
    EMBED_MODEL,
    EMBED_QUERY_PREFIX,
    EMBED_TIMEOUT,
    OLLAMA_URL,
)

logger = logging.getLogger(__name__)

_mem_cache: dict[str, list[float]] = {}
_mem_lock = threading.Lock()
_batch_endpoint_ok: Optional[bool] = None

# Disjoncteur : quand Ollama est éteint, chaque requête paie sa connexion refusée
# puis retente en unitaire, et le fan-out multiplie le tout. On coupe court après
# un premier échec — une panne doit se voir tout de suite, pas se subir lentement.
_breaker_open_until = 0.0
BREAKER_COOLDOWN = float(os.getenv("RAG_BREAKER_COOLDOWN", "30"))


def _breaker_is_open() -> bool:
    return time.monotonic() < _breaker_open_until


def _trip_breaker() -> None:
    global _breaker_open_until
    _breaker_open_until = time.monotonic() + BREAKER_COOLDOWN


def _key(text: str) -> str:
    return hashlib.sha1(f"{EMBED_MODEL}:{text}".encode("utf-8")).hexdigest()


def _cache_path(key: str):
    # Deux niveaux de sous-répertoires : évite 10 000 fichiers dans un seul dossier.
    d = CACHE_DIR / key[:2] / key[2:4]
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def _cache_get(text: str) -> Optional[list[float]]:
    key = _key(text)
    with _mem_lock:
        hit = _mem_cache.get(key)
    if hit is not None:
        return hit
    path = _cache_path(key)
    if path.exists():
        try:
            vec = json.loads(path.read_text())
            with _mem_lock:
                _mem_cache[key] = vec
            return vec
        except Exception:
            path.unlink(missing_ok=True)
    return None


def _cache_set(text: str, vec: list[float]) -> None:
    key = _key(text)
    with _mem_lock:
        _mem_cache[key] = vec
    try:
        _cache_path(key).write_text(json.dumps(vec))
    except Exception as e:  # cache best-effort : jamais bloquant
        logger.debug("[RAG EMBED] écriture cache échouée: %.60s", str(e))


def _fit_dim(vec: list[float]) -> list[float]:
    if len(vec) < EMBED_DIM:
        return vec + [0.0] * (EMBED_DIM - len(vec))
    return vec[:EMBED_DIM]


def _post_batch(inputs: list[str]) -> Optional[list[list[float]]]:
    """Ollama >= 0.2 : /api/embed accepte une liste et renvoie 'embeddings'."""
    global _batch_endpoint_ok
    if _batch_endpoint_ok is False:
        return None
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": inputs},
            timeout=EMBED_TIMEOUT,
        )
        if resp.status_code == 404:
            _batch_endpoint_ok = False
            return None
        resp.raise_for_status()
        embs = resp.json().get("embeddings")
        if not embs or len(embs) != len(inputs):
            return None
        _batch_endpoint_ok = True
        return [_fit_dim(e) for e in embs]
    except requests.Timeout:
        logger.warning("[RAG EMBED] timeout batch (%d textes, %.0fs)", len(inputs), EMBED_TIMEOUT)
        return None
    except Exception as e:
        logger.warning("[RAG EMBED] batch échoué: %.80s", str(e))
        return None


def _post_single(text: str) -> Optional[list[float]]:
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=EMBED_TIMEOUT,
        )
        resp.raise_for_status()
        emb = resp.json().get("embedding")
        return _fit_dim(emb) if emb else None
    except Exception as e:
        logger.warning("[RAG EMBED] unitaire échoué: %.80s", str(e))
        return None


def _embed_raw(texts: list[str]) -> list[Optional[list[float]]]:
    """Embedde des textes déjà préfixés, en s'appuyant sur le cache."""
    out: list[Optional[list[float]]] = [None] * len(texts)
    todo: list[int] = []

    for i, t in enumerate(texts):
        cached = _cache_get(t)
        if cached is not None:
            out[i] = cached
        else:
            todo.append(i)

    # Le cache reste servi même disjoncteur ouvert : seuls les appels réseau sont
    # coupés.
    if todo and _breaker_is_open():
        return out

    for start in range(0, len(todo), EMBED_BATCH):
        chunk_idx = todo[start:start + EMBED_BATCH]
        chunk = [texts[i] for i in chunk_idx]

        vecs = _post_batch(chunk)
        if vecs is None:
            vecs = [_post_single(t) for t in chunk]

        if all(v is None for v in vecs):
            _trip_breaker()
            logger.warning("[RAG EMBED] Ollama injoignable — embeddings coupés %.0fs",
                           BREAKER_COOLDOWN)
            return out

        for i, vec in zip(chunk_idx, vecs):
            if vec is not None:
                out[i] = vec
                _cache_set(texts[i], vec)

    return out


def embed_query(text: str) -> Optional[list[float]]:
    """Embedding d'une requête utilisateur. None si Ollama est injoignable."""
    if not text or not text.strip():
        return None
    prefixed = EMBED_QUERY_PREFIX + text.strip()[:EMBED_MAX_CHARS]
    return _embed_raw([prefixed])[0]


def embed_documents(texts: Iterable[str]) -> list[Optional[list[float]]]:
    """Embeddings d'un lot de documents (indexation). Conserve l'ordre d'entrée."""
    prefixed = [EMBED_DOC_PREFIX + (t or "")[:EMBED_MAX_CHARS] for t in texts]
    return _embed_raw(prefixed)


def health() -> dict:
    """Sonde utilisée par /coach/health et le script d'ingestion."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        present = any(m.split(":")[0] == EMBED_MODEL.split(":")[0] for m in models)
        return {"available": present, "model": EMBED_MODEL, "models": models}
    except Exception as e:
        return {"available": False, "model": EMBED_MODEL, "error": str(e)[:120]}
