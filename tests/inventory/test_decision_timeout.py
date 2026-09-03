"""
test_decision_timeout.py
=========================
Régression : « [Orchestrator] decision_agent failed SKU=… : » (message vide)
puis « decision_agent timeout after 120s ».

Cause racine : aucun plafond sur une requête LLM. Le SDK OpenAI attend 600 s
par défaut, le client ollama n'attend rien — un modèle gratuit en file
d'attente chez OpenRouter dépassait donc à lui seul le budget du SKU, et
l'erreur remontée (FutureTimeoutError) a un str() vide.

Trois garde-fous vérifiés ici :
  1. chaque client LLM porte un timeout par requête ;
  2. un dépassement fait basculer sur le provider suivant de la chaîne ;
  3. le budget du SKU couvre la chaîne entière, et un SKU expiré est annulé
     au lieu de continuer à tourner sur la boucle de fond partagée.
"""
import asyncio
import time

import pytest

from app.inventory.config.settings import settings
from app.inventory.utils.llm_factory import _rotatable_llm_error, get_llm


# ── 1. Plafond par requête, quel que soit le provider ────────────────────────

@pytest.mark.parametrize("provider", ["openrouter", "mistral", "groq"])
def test_http_clients_carry_request_timeout(provider):
    """Sans ce plafond, le SDK OpenAI attend 600 s — 5x le budget du SKU."""
    try:
        llm = get_llm(provider=provider)
    except (ValueError, ImportError) as exc:
        pytest.skip(f"provider {provider} non configuré: {exc}")
    assert llm.request_timeout == settings.llm_request_timeout_s


def test_ollama_timeout_goes_through_client_kwargs():
    """ChatOllama (0.2.x) n'a pas de champ `timeout` : il passe par httpx."""
    try:
        llm = get_llm(provider="ollama")
    except ImportError as exc:
        pytest.skip(f"langchain-ollama absent: {exc}")
    assert llm.client_kwargs.get("timeout") == settings.llm_request_timeout_s


def test_explicit_timeout_is_not_overridden():
    try:
        llm = get_llm(provider="openrouter", timeout=5)
    except (ValueError, ImportError) as exc:
        pytest.skip(f"openrouter non configuré: {exc}")
    assert llm.request_timeout == 5


# ── 2. Un timeout doit faire basculer, pas échouer ───────────────────────────

def test_timeout_error_triggers_provider_fallback():
    """openai.APITimeoutError dit « Request timed out. » : le mot « timeout »
    n'y figure pas, la détection par texte seule le laissait passer pour une
    erreur définitive et tuait le nœud decide au lieu d'essayer Groq."""
    openai = pytest.importorskip("openai")
    httpx  = pytest.importorskip("httpx")

    exc = openai.APITimeoutError(request=httpx.Request("POST", "http://x"))
    assert "timeout" not in str(exc).lower()   # le piège d'origine
    assert _rotatable_llm_error(exc)
    assert _rotatable_llm_error(httpx.ReadTimeout("timed out"))
    # Une erreur applicative reste définitive — pas de bascule inutile.
    assert not _rotatable_llm_error(ValueError("JSON invalide"))


# ── 3. Budget du SKU et annulation ───────────────────────────────────────────

def test_decision_budget_covers_the_whole_fallback_chain():
    """Un budget inférieur à la chaîne re-crée le bug : le SKU expire alors
    que la chaîne de secours travaille encore."""
    from app.inventory.services import orchestrator

    minimum = settings.llm_request_timeout_s * orchestrator._LLM_CHAIN_LENGTH
    assert orchestrator._DECISION_TIMEOUT_S > minimum


def test_await_sync_cancels_the_coroutine_on_timeout():
    """`.result(timeout)` rend la main sans arrêter la coroutine : sans cancel,
    le SKU expiré continuait à tourner sur la boucle de fond partagée."""
    from concurrent.futures import TimeoutError as FutureTimeoutError

    from app.inventory.services.orchestrator import _await_sync

    cancelled = asyncio.Event()

    async def never_finishes():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(FutureTimeoutError):
        _await_sync(never_finishes(), timeout=0.2)

    # L'annulation est déposée sur la boucle de fond — on lui laisse un tour.
    for _ in range(50):
        if cancelled.is_set():
            break
        time.sleep(0.02)
    assert cancelled.is_set(), "la coroutine expirée tourne toujours"
