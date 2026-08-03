"""
http.py — Client httpx partagé.

Pourquoi ce module existe
-------------------------
Les appels LLM (stratège, coach) et les scrapers construisaient un
`httpx.AsyncClient` par tentative, dans un `async with`. Le constructeur est
synchrone et coûte ~700 ms sur cette machine : il monte un `SSLContext` neuf,
qui charge et parse le bundle CA de certifi (292 Ko) à chaque fois.

Ce coût tombait intégralement sur la boucle d'événements. Le stratège fait une
rotation de 4 modèles × 2 variantes de payload : jusqu'à 8 constructions par
appel, soit ~5,6 s de boucle gelée — pendant lesquelles aucune autre requête
HTTP ni frame WebSocket ne progresse. Mesuré au démarrage : 57 s de boucle
gelée au total, dont 35 s imputables à ces deux fonctions.

Un client réutilisé amortit le SSLContext et garde les connexions ouvertes
(keep-alive), ce qui supprime aussi le handshake TLS des appels suivants.

Usage
-----
    from app.core.http import get_http_client

    client = get_http_client()
    resp = await client.post(url, json=payload, timeout=45)

Le timeout se passe par requête, pas à la construction : un seul client sert
des appels aux budgets très différents (5 s pour la météo, 45 s pour un LLM).

Le client est indexé par boucle d'événements, comme le pool asyncpg de
`app.core.db` : un client httpx tient un pool de connexions lié à la boucle qui
l'a créé, et le réutiliser depuis une autre boucle (asyncio.run dans un thread
worker) casse le transport.
"""

from __future__ import annotations

import asyncio
import logging
import weakref

import httpx

logger = logging.getLogger(__name__)

# Timeout par défaut, volontairement large : chaque appelant passe le sien sur
# la requête. Sert uniquement de garde-fou si quelqu'un l'oublie.
DEFAULT_TIMEOUT = 30.0

_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = (
    weakref.WeakKeyDictionary()
)


def get_http_client() -> httpx.AsyncClient:
    """
    Client httpx partagé par la boucle d'événements courante.

    Ne le fermez pas et ne l'utilisez pas dans un `async with` : il est partagé.
    L'arrêt applicatif passe par close_http_client().
    """
    loop = asyncio.get_event_loop()
    client = _clients.get(loop)
    if client is not None and not client.is_closed:
        return client

    client = httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
        # Assez de connexions gardées ouvertes pour que les cycles agents
        # concurrents (un par magasin suivi) ne se rouvrent pas de TLS.
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
    )
    _clients[loop] = client
    logger.debug("[HTTP] client httpx partagé créé pour la boucle %r", loop)
    return client


async def close_http_client() -> None:
    """Ferme le client de la boucle courante (arrêt applicatif)."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    client = _clients.pop(loop, None)
    if client is not None and not client.is_closed:
        try:
            await client.aclose()
        except Exception as exc:  # pragma: no cover - best effort à l'arrêt
            logger.debug("[HTTP] fermeture client: %s", exc)
