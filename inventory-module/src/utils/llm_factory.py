"""
LLM Factory
===========
Centralized LLM provider initialization with support for multiple backends.

Usage:
    from src.utils.llm_factory import get_llm
    
    # Uses LLM_PROVIDER from .env
    llm = get_llm()
    
    # Override provider
    llm = get_llm(provider="groq")
    
    # Override specific settings
    llm = get_llm(provider="ollama", temperature=0.5)

Supported Providers:
    - groq: Fast inference via Groq Cloud
    - ollama: Local models via Ollama
    - openai: OpenAI API (GPT-4, etc.)
    - anthropic: Anthropic API (Claude, etc.)

Adding a New Provider:
    1. Add settings to Settings class in config/settings.py
    2. Add provider to Literal type hint in settings.llm_provider
    3. Add elif block in get_llm() function below
    4. Update .env.example with new provider's variables
"""

import logging
from typing import Optional, Any
from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


def get_llm(
    provider: Optional[str] = None,
    temperature: Optional[float] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    **kwargs
) -> BaseChatModel:
    """
    Factory function to create LLM instances based on provider.
    
    Args:
        provider: LLM provider ("groq", "ollama", "openai", "anthropic")
                 If None, reads from settings.llm_provider
        temperature: Model temperature (0.0-1.0). If None, uses settings default
        model: Model name. If None, uses provider's default from settings
        api_key: API key for the provider. If None, uses settings default
        **kwargs: Additional provider-specific arguments
    
    Returns:
        Configured LLM instance
    
    Raises:
        ValueError: If provider is unsupported or required credentials are missing
        ImportError: If required provider package is not installed
    
    Examples:
        >>> # Use default provider from .env
        >>> llm = get_llm()
        
        >>> # Override provider
        >>> llm = get_llm(provider="groq", temperature=0.0)
        
        >>> # Override model
        >>> llm = get_llm(provider="openai", model="gpt-4o")
    """
    from config.settings import settings
    
    # Use settings defaults if not overridden
    provider = provider or settings.llm_provider
    temperature = temperature if temperature is not None else settings.llm_temperature
    
    logger.info(f"Initializing LLM: provider={provider}, temperature={temperature}")
    
    # ══════════════════════════════════════════════════════════════════════════
    # GROQ
    # ══════════════════════════════════════════════════════════════════════════
    if provider == "groq":
        try:
            from langchain_groq import ChatGroq
        except ImportError:
            raise ImportError(
                "langchain-groq is not installed. "
                "Install it with: pip install langchain-groq"
            )
        
        resolved_api_key = api_key or settings.groq_api_key
        if not resolved_api_key:
            raise ValueError(
                "Groq API key not found. Set GROQ_API_KEY in .env or pass api_key parameter"
            )
        
        resolved_model = model or settings.groq_model
        
        return ChatGroq(
            api_key=resolved_api_key,
            model_name=resolved_model,
            temperature=temperature,
            **kwargs
        )
    
    # ══════════════════════════════════════════════════════════════════════════
    # OLLAMA
    # ══════════════════════════════════════════════════════════════════════════
    elif provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            raise ImportError(
                "langchain-ollama is not installed. "
                "Install it with: pip install langchain-ollama"
            )
        
        resolved_model = model or settings.ollama_model
        base_url = kwargs.pop("base_url", settings.ollama_base_url)
        
        return ChatOllama(
            model=resolved_model,
            base_url=base_url,
            temperature=temperature,
            **kwargs
        )
    
    # ══════════════════════════════════════════════════════════════════════════
    # OPENAI
    # ══════════════════════════════════════════════════════════════════════════
    elif provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError(
                "langchain-openai is not installed. "
                "Install it with: pip install langchain-openai"
            )
        
        resolved_api_key = api_key or settings.openai_api_key
        if not resolved_api_key:
            raise ValueError(
                "OpenAI API key not found. Set OPENAI_API_KEY in .env or pass api_key parameter"
            )
        
        resolved_model = model or settings.openai_model
        base_url = kwargs.pop("base_url", settings.openai_base_url)
        
        return ChatOpenAI(
            api_key=resolved_api_key,
            model=resolved_model,
            base_url=base_url,
            temperature=temperature,
            **kwargs
        )
    
    # ══════════════════════════════════════════════════════════════════════════
    # ANTHROPIC
    # ══════════════════════════════════════════════════════════════════════════
    elif provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError(
                "langchain-anthropic is not installed. "
                "Install it with: pip install langchain-anthropic"
            )
        
        resolved_api_key = api_key or settings.anthropic_api_key
        if not resolved_api_key:
            raise ValueError(
                "Anthropic API key not found. Set ANTHROPIC_API_KEY in .env or pass api_key parameter"
            )
        
        resolved_model = model or settings.anthropic_model
        
        return ChatAnthropic(
            api_key=resolved_api_key,
            model=resolved_model,
            temperature=temperature,
            **kwargs
        )
    
    # ══════════════════════════════════════════════════════════════════════════
    # UNSUPPORTED PROVIDER
    # ══════════════════════════════════════════════════════════════════════════
    else:
        raise ValueError(
            f"Unsupported LLM provider: {provider}. "
            f"Supported: groq, ollama, openai, anthropic"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Convenience aliases for backward compatibility
# ══════════════════════════════════════════════════════════════════════════════

def create_llm(api_key: Optional[str] = None, **kwargs) -> BaseChatModel:
    """
    Legacy function for backward compatibility.
    Defaults to Groq provider with api_key parameter.
    
    Deprecated: Use get_llm() instead for explicit provider selection.
    """
    logger.warning(
        "create_llm() is deprecated. Use get_llm(provider='groq', api_key=...) instead"
    )
    return get_llm(provider="groq", api_key=api_key, **kwargs)
