"""
ragas_provider.py — Branche RAGAS sur les modèles du projet (pas OpenAI par défaut).

RAGAS suppose OpenAI de base. Ici on lui donne :
  • LLM juge  : le même provider que le coach (Mistral > Groq > OpenRouter),
                via les wrappers LangChain — clés lues dans `.env`.
  • Embeddings : Ollama bge-m3, exactement le modèle du RAG du projet
                (voir app/sales/data/rag/settings.py), pour que la mesure de
                pertinence RAGAS parle le même espace vectoriel que le retriever.

Les deux briques sont optionnelles et dégradent proprement : sans clé LLM, aucune
métrique RAGAS n'est calculable ; sans Ollama joignable, seules les métriques qui
n'ont pas besoin d'embeddings sont calculées (faithfulness, context precision LLM).
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from evals.common import Provider, load_providers

# Ordre de préférence du juge — identique à la rotation du coach.
_JUDGE_ORDER = ("mistral", "groq", "openrouter")


@dataclass
class RagasBackends:
    llm:            object | None          # LangchainLLMWrapper | None
    embeddings:    object | None          # LangchainEmbeddingsWrapper | None
    judge_label:   str = ""               # "mistral/mistral-large-latest"
    embed_label:   str = ""               # "ollama/bge-m3"
    llm_error:     str = ""
    embed_error:   str = ""

    @property
    def can_run(self) -> bool:
        return self.llm is not None


def _pick_provider() -> tuple[Provider, str] | None:
    """Premier provider disponible dans l'ordre de préférence, avec son modèle fort."""
    providers = load_providers()
    for name in _JUDGE_ORDER:
        p = providers.get(name)
        if p and p.available:
            return p, p.models[0]
    return None


def _build_llm(provider: Provider, model: str):
    """Wrappe un chat model LangChain pour RAGAS. Groq a un wrapper natif ;
    Mistral et OpenRouter passent par ChatOpenAI (endpoints OpenAI-compatibles)."""
    from ragas.llms import LangchainLLMWrapper

    if provider.name == "groq":
        from langchain_groq import ChatGroq
        lc = ChatGroq(model=model, api_key=provider.api_key, temperature=0.0)
    else:
        # Mistral et OpenRouter exposent /v1/chat/completions façon OpenAI.
        from langchain_openai import ChatOpenAI
        lc = ChatOpenAI(model=model, api_key=provider.api_key,
                        base_url=provider.base_url, temperature=0.0)
    return LangchainLLMWrapper(lc)


def _ollama_reachable(base_url: str, timeout: float = 1.5) -> bool:
    parsed = urlparse(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except Exception:
        return False


def _build_embeddings():
    """Embeddings RAGAS = ceux du RAG du projet (Ollama bge-m3). None si injoignable."""
    from app.sales.data.rag.settings import EMBED_MODEL, OLLAMA_URL

    if not _ollama_reachable(OLLAMA_URL):
        return None, "", f"Ollama injoignable ({OLLAMA_URL})"
    try:
        from langchain_ollama import OllamaEmbeddings
        from ragas.embeddings import LangchainEmbeddingsWrapper
        emb = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_URL)
        return LangchainEmbeddingsWrapper(emb), f"ollama/{EMBED_MODEL}", ""
    except Exception as e:
        return None, "", f"{type(e).__name__}: {e}"


def build_backends() -> RagasBackends:
    """Construit LLM + embeddings pour RAGAS depuis la config du projet."""
    picked = _pick_provider()
    if not picked:
        return RagasBackends(
            llm=None, embeddings=None,
            llm_error="aucun provider LLM disponible (MISTRAL/GROQ/OPENROUTER_API_KEY manquantes)",
        )
    provider, model = picked
    try:
        llm = _build_llm(provider, model)
        llm_label = f"{provider.name}/{model}"
        llm_error = ""
    except Exception as e:
        llm, llm_label, llm_error = None, "", f"{type(e).__name__}: {e}"

    emb, emb_label, emb_error = _build_embeddings()
    return RagasBackends(
        llm=llm, embeddings=emb,
        judge_label=llm_label, embed_label=emb_label,
        llm_error=llm_error, embed_error=emb_error,
    )
