"""
LLM Factory — OpenRouter + providers locaux.
=============================================

Fournisseurs supportés :
  openrouter   ← RECOMMANDÉ — accès unifié Claude/Gemini/Llama via une seule API
  groq         — inference ultra-rapide (Llama 3.3 70B)
  ollama       — modèles locaux (pas d'API key)
  openai       — GPT-4o
  anthropic    — Claude direct

Sélection tiérée OpenRouter (role= param) :
  "fast"     → gemini-flash-1.5         — analyse, contexte, signals
  "smart"    → claude-3.5-sonnet        — décision, coach, synthèse
  "guardian" → llama-3.1-70b-instruct  — guardrail, critique, validation

Usage :
  from src.utils.llm_factory import get_llm
  llm = get_llm()                              # provider par défaut (.env)
  llm = get_llm(provider="openrouter", role="smart")  # Claude pour décision
  llm = get_llm(provider="openrouter", role="fast")   # Gemini Flash pour analyse
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

# Rôles possibles pour OpenRouter
LLMRole = Literal["fast", "smart", "guardian"]


def get_llm(
    provider:    Optional[str]     = None,
    temperature: Optional[float]   = None,
    model:       Optional[str]     = None,
    api_key:     Optional[str]     = None,
    role:        Optional[LLMRole] = None,
    **kwargs,
) -> BaseChatModel:
    """
    Factory LLM centralisée.

    Args:
        provider:    "openrouter" | "groq" | "ollama" | "openai" | "anthropic"
                     Si None → lit LLM_PROVIDER depuis .env
        temperature: 0.0-1.0. Si None → lit LLM_TEMPERATURE depuis .env
        model:       Nom du modèle. Si None → défaut du provider
        api_key:     Clé API. Si None → lit depuis .env
        role:        "fast" | "smart" | "guardian"
                     Sélectionne le modèle tiéré OpenRouter.
                     Ignoré pour les autres providers.
        **kwargs:    Args supplémentaires passés au constructeur

    Returns:
        BaseChatModel configuré

    Raises:
        ValueError:  Provider non supporté ou clé manquante
        ImportError: Package provider non installé
    """
    from config.settings import settings

    provider    = provider    or settings.llm_provider
    temperature = temperature if temperature is not None else settings.llm_temperature

    logger.info("[LLMFactory] provider=%s role=%s temperature=%s", provider, role, temperature)

    # ══════════════════════════════════════════════════════════════════════════
    # OPENROUTER — recommandé (accès unifié Claude/Gemini/Llama)
    # ══════════════════════════════════════════════════════════════════════════
    if provider == "openrouter":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai requis: pip install langchain-openai")

        resolved_key = api_key or settings.openrouter_api_key
        if not resolved_key:
            raise ValueError(
                "OPENROUTER_API_KEY manquante dans .env\n"
                "  → Crée un compte sur https://openrouter.ai et ajoute :\n"
                "    OPENROUTER_API_KEY=sk-or-v1-..."
            )

        # Sélection du modèle par rôle
        if model:
            resolved_model = model
        elif role == "fast":
            resolved_model = settings.openrouter_model_fast
        elif role == "smart":
            resolved_model = settings.openrouter_model_smart
        elif role == "guardian":
            resolved_model = settings.openrouter_model_guardian
        else:
            # Défaut : smart si pas de rôle précisé
            resolved_model = settings.openrouter_model_smart

        logger.info("[LLMFactory] OpenRouter model=%s", resolved_model)

        return ChatOpenAI(
            api_key=resolved_key,
            model=resolved_model,
            base_url=settings.openrouter_base_url,
            temperature=temperature,
            default_headers={
                "HTTP-Referer": settings.openrouter_site_url,
                "X-Title":      settings.openrouter_app_name,
            },
            **kwargs,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # GROQ — inference ultra-rapide
    # ══════════════════════════════════════════════════════════════════════════
    elif provider == "groq":
        try:
            from langchain_groq import ChatGroq
        except ImportError:
            raise ImportError("langchain-groq requis: pip install langchain-groq")

        resolved_key = api_key or settings.groq_api_key
        if not resolved_key:
            raise ValueError("GROQ_API_KEY manquante dans .env")

        return ChatGroq(
            api_key=resolved_key,
            model_name=model or settings.groq_model,
            temperature=temperature,
            **kwargs,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # OLLAMA — modèles locaux (développement)
    # ══════════════════════════════════════════════════════════════════════════
    elif provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            raise ImportError("langchain-ollama requis: pip install langchain-ollama")

        base_url = kwargs.pop("base_url", settings.ollama_base_url)
        return ChatOllama(
            model=model or settings.ollama_model,
            base_url=base_url,
            temperature=temperature,
            **kwargs,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # OPENAI
    # ══════════════════════════════════════════════════════════════════════════
    elif provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai requis: pip install langchain-openai")

        resolved_key = api_key or settings.openai_api_key
        if not resolved_key:
            raise ValueError("OPENAI_API_KEY manquante dans .env")

        base_url = kwargs.pop("base_url", settings.openai_base_url)
        return ChatOpenAI(
            api_key=resolved_key,
            model=model or settings.openai_model,
            base_url=base_url,
            temperature=temperature,
            **kwargs,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # ANTHROPIC
    # ══════════════════════════════════════════════════════════════════════════
    elif provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError("langchain-anthropic requis: pip install langchain-anthropic")

        resolved_key = api_key or settings.anthropic_api_key
        if not resolved_key:
            raise ValueError("ANTHROPIC_API_KEY manquante dans .env")

        return ChatAnthropic(
            api_key=resolved_key,
            model=model or settings.anthropic_model,
            temperature=temperature,
            **kwargs,
        )

    else:
        raise ValueError(
            f"Provider '{provider}' non supporté. "
            "Valeurs valides: openrouter, groq, ollama, openai, anthropic"
        )


# ── Helpers tiérés — utilisés directement par les agents ─────────────────────

def get_fast_llm(**kwargs) -> BaseChatModel:
    """LLM FAST — pour analyse/contexte/signals (rapide, économique)."""
    from config.settings import settings
    if settings.llm_provider == "openrouter":
        return get_llm(provider="openrouter", role="fast", **kwargs)
    return get_llm(**kwargs)


def get_smart_llm(**kwargs) -> BaseChatModel:
    """LLM SMART — pour décision/coach/synthèse (précis, raisonnement fort)."""
    from config.settings import settings
    if settings.llm_provider == "openrouter":
        return get_llm(provider="openrouter", role="smart", **kwargs)
    return get_llm(**kwargs)


def get_guardian_llm(**kwargs) -> BaseChatModel:
    """LLM GUARDIAN — pour guardrail/critique/validation (fiable, structuré)."""
    from config.settings import settings
    if settings.llm_provider == "openrouter":
        return get_llm(provider="openrouter", role="guardian", **kwargs)
    return get_llm(**kwargs)


# ── Backward compat ───────────────────────────────────────────────────────────

def create_llm(api_key: Optional[str] = None, **kwargs) -> BaseChatModel:
    """Deprecated: utiliser get_llm() directement."""
    logger.warning("create_llm() deprecated — utiliser get_llm()")
    return get_llm(api_key=api_key, **kwargs)
