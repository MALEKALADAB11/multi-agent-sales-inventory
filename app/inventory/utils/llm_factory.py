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
  from app.inventory.utils.llm_factory import get_llm
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
    from app.inventory.config.settings import settings

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
    # MISTRAL — API directe La Plateforme (compatible OpenAI, quota indépendant
    # d'OpenRouter — pas de dépendance langchain-mistralai nécessaire)
    # ══════════════════════════════════════════════════════════════════════════
    if provider == "mistral":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai requis: pip install langchain-openai")

        resolved_key = api_key or settings.mistral_api_key
        if not resolved_key:
            raise ValueError(
                "MISTRAL_API_KEY manquante dans .env\n"
                "  → Crée un compte sur https://console.mistral.ai et ajoute :\n"
                "    MISTRAL_API_KEY=..."
            )

        if model:
            resolved_model = model
        elif role == "fast":
            resolved_model = settings.mistral_model_fast
        elif role == "smart":
            resolved_model = settings.mistral_model_smart
        elif role == "guardian":
            resolved_model = settings.mistral_model_guardian
        else:
            resolved_model = settings.mistral_model_smart

        logger.info("[LLMFactory] Mistral model=%s", resolved_model)

        return ChatOpenAI(
            api_key=resolved_key,
            model=resolved_model,
            base_url=settings.mistral_base_url,
            temperature=temperature,
            **kwargs,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # GROQ — inference ultra-rapide avec rotation des clés et fallback Ollama
    # ══════════════════════════════════════════════════════════════════════════
    elif provider == "groq":
        keys = [api_key] if api_key else list(settings.groq_api_keys)
        if not keys:
            raise ValueError(
                "Aucune clé Groq dans .env — définir GROQ_API_KEY "
                "ou GROQ_API_KEYS=clef1,clef2,... pour la rotation"
            )

        return _build_groq_multikey(
            keys=keys,
            model=model or settings.groq_model,
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
            "Valeurs valides: mistral, openrouter, groq, ollama, openai, anthropic"
        )


# ── Groq multi-clés ───────────────────────────────────────────────────────────
# Rotation de clés API : quand une clé est épuisée (429/quota) ou invalide,
# on bascule sur la suivante de GROQ_API_KEYS. Classe construite paresseusement
# pour ne pas exiger langchain-groq quand un autre provider est utilisé.

_groq_multikey_cls = None


def _rotatable_groq_error(exc: Exception) -> bool:
    """Erreur qui justifie d'essayer la clé Groq suivante (quota/limite/clé)."""
    status = getattr(exc, "status_code", None)
    if status in (401, 403, 413, 429, 498, 499):
        return True
    msg = str(exc).lower()
    return any(t in msg for t in (
        "rate limit", "rate_limit", "quota", "invalid api key",
        "over capacity", "insufficient", "too many requests",
    ))


def _get_groq_multikey_cls():
    global _groq_multikey_cls
    if _groq_multikey_cls is not None:
        return _groq_multikey_cls

    try:
        from langchain_groq import ChatGroq
    except ImportError:
        raise ImportError("langchain-groq requis: pip install langchain-groq")
    from pydantic import PrivateAttr

    class ChatGroqMultiKey(ChatGroq):
        """ChatGroq avec bascule automatique sur les clés de secours.

        Les instances de secours (une par clé supplémentaire) sont stockées
        dans `_fallbacks` ; toute erreur de quota/clé sur la primaire déclenche
        l'essai de la suivante, dans l'ordre. En streaming, la bascule n'a lieu
        que si aucun token n'est encore parti (pas de texte dupliqué).
        """

        _fallbacks: list = PrivateAttr(default_factory=list)

        def _try_next_sync(self, method: str, exc: Exception, *args, **kwargs):
            if not self._fallbacks or not _rotatable_groq_error(exc):
                raise exc
            last = exc
            for i, alt in enumerate(self._fallbacks, start=2):
                logger.warning(
                    "[LLMFactory] Groq: %.80s → bascule sur la clé #%d", str(last), i
                )
                try:
                    return getattr(alt, method)(*args, **kwargs)
                except Exception as e:
                    if not _rotatable_groq_error(e):
                        raise
                    last = e
            raise last

        async def _try_next_async(self, method: str, exc: Exception, *args, **kwargs):
            if not self._fallbacks or not _rotatable_groq_error(exc):
                raise exc
            last = exc
            for i, alt in enumerate(self._fallbacks, start=2):
                logger.warning(
                    "[LLMFactory] Groq: %.80s → bascule sur la clé #%d", str(last), i
                )
                try:
                    return await getattr(alt, method)(*args, **kwargs)
                except Exception as e:
                    if not _rotatable_groq_error(e):
                        raise
                    last = e
            raise last

        def _generate(self, *args, **kwargs):
            try:
                return super()._generate(*args, **kwargs)
            except Exception as e:
                return self._try_next_sync("_generate", e, *args, **kwargs)

        async def _agenerate(self, *args, **kwargs):
            try:
                return await super()._agenerate(*args, **kwargs)
            except Exception as e:
                return await self._try_next_async("_agenerate", e, *args, **kwargs)

        def _stream(self, *args, **kwargs):
            yielded = False
            try:
                for chunk in super()._stream(*args, **kwargs):
                    yielded = True
                    yield chunk
                return
            except Exception as e:
                if yielded or not self._fallbacks or not _rotatable_groq_error(e):
                    raise
                last = e
            for i, alt in enumerate(self._fallbacks, start=2):
                logger.warning(
                    "[LLMFactory] Groq stream: %.80s → bascule sur la clé #%d", str(last), i
                )
                try:
                    for chunk in alt._stream(*args, **kwargs):
                        yielded = True
                        yield chunk
                    return
                except Exception as e:
                    if yielded or not _rotatable_groq_error(e):
                        raise
                    last = e
            raise last

        async def _astream(self, *args, **kwargs):
            yielded = False
            try:
                async for chunk in super()._astream(*args, **kwargs):
                    yielded = True
                    yield chunk
                return
            except Exception as e:
                if yielded or not self._fallbacks or not _rotatable_groq_error(e):
                    raise
                last = e
            for i, alt in enumerate(self._fallbacks, start=2):
                logger.warning(
                    "[LLMFactory] Groq stream: %.80s → bascule sur la clé #%d", str(last), i
                )
                try:
                    async for chunk in alt._astream(*args, **kwargs):
                        yielded = True
                        yield chunk
                    return
                except Exception as e:
                    if yielded or not _rotatable_groq_error(e):
                        raise
                    last = e
            raise last

    _groq_multikey_cls = ChatGroqMultiKey
    return ChatGroqMultiKey


def _build_groq_multikey(keys: list, model: str, temperature: float, **kwargs) -> BaseChatModel:
    """Construit un ChatGroq dont les clés supplémentaires servent de secours."""
    cls = _get_groq_multikey_cls()
    from langchain_groq import ChatGroq

    primary = cls(api_key=keys[0], model_name=model, temperature=temperature, **kwargs)
    primary._fallbacks = [
        ChatGroq(api_key=k, model_name=model, temperature=temperature, **kwargs)
        for k in keys[1:]
    ]
    if len(keys) > 1:
        logger.info("[LLMFactory] Groq multi-clés: %d clés en rotation (model=%s)", len(keys), model)
    return primary


# ── Helpers tiérés — utilisés directement par les agents ─────────────────────
# Providers avec sélection de modèle par rôle (fast/smart/guardian)
_TIERED_PROVIDERS = ("openrouter", "mistral")


def get_fast_llm(**kwargs) -> BaseChatModel:
    """LLM FAST — pour analyse/contexte/signals (rapide, économique)."""
    from app.inventory.config.settings import settings
    if settings.llm_provider in _TIERED_PROVIDERS:
        return get_llm(provider=settings.llm_provider, role="fast", **kwargs)
    return get_llm(**kwargs)


def get_smart_llm(**kwargs) -> BaseChatModel:
    """LLM SMART — pour décision/coach/synthèse (précis, raisonnement fort)."""
    from app.inventory.config.settings import settings
    if settings.llm_provider in _TIERED_PROVIDERS:
        return get_llm(provider=settings.llm_provider, role="smart", **kwargs)
    return get_llm(**kwargs)


def get_guardian_llm(**kwargs) -> BaseChatModel:
    """LLM GUARDIAN — pour guardrail/critique/validation (fiable, structuré)."""
    from app.inventory.config.settings import settings
    if settings.llm_provider in _TIERED_PROVIDERS:
        return get_llm(provider=settings.llm_provider, role="guardian", **kwargs)
    return get_llm(**kwargs)


# ── Backward compat ───────────────────────────────────────────────────────────

def create_llm(api_key: Optional[str] = None, **kwargs) -> BaseChatModel:
    """Deprecated: utiliser get_llm() directement."""
    logger.warning("create_llm() deprecated — utiliser get_llm()")
    return get_llm(api_key=api_key, **kwargs)
