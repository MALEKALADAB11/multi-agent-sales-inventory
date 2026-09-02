"""
langfuse_observer.py — Observabilite Totale v3.0
=================================================
Compatible Langfuse server v2.95 + SDK langfuse==2.60.0

Trace TOUT le systeme multi-agent :
  1. Cycles complets (Analyste -> Stratege -> Coach) avec spans hierarchiques
  2. Chaque noeud LangGraph individuellement (entree/sortie/duree)
  3. Chaque appel LLM (OpenRouter) — prompt/response/tokens/latence/modele
  4. Chaque recherche RAG (query/scores/scripts/pertinence)
  5. Chaque interaction Coach Chat (message/reponse/domain/mode/confidence)
  6. Requetes PostgreSQL (duree/nb resultats)
  7. Scores de qualite automatiques (cycle, coach, RAG)
  8. Metriques business (gap comble, CA, urgence)

Usage:
  from app.core.langfuse import get_langfuse, LangfuseTracer
  tracer = LangfuseTracer(store_id=DEFAULT_STORE_ID)

  # Trace un cycle complet
  with tracer.cycle(cycle_id, trigger="cron") as cycle:
      with cycle.agent("analyste") as agent:
          with agent.node("receive_pos") as node:
              node.set_output({"ca": 500, "tx": 12})
          with agent.llm_call("llama3.2", system, user) as llm:
              llm.set_response(response, tokens=150, latency_ms=3200)

  # Trace le coach chat
  tracer.trace_coach_chat(message, reply, domain, mode, confidence, ...)
"""

import os
import time
import uuid
import logging
import socket
from datetime import datetime
from typing import Optional, Any
from contextlib import contextmanager
from app.core.config import DEFAULT_STORE_ID

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON LANGFUSE
# ══════════════════════════════════════════════════════════════════════════════

_lf = None
_lf_checked = False


def _host_reachable(host: str, timeout: float = 1.0) -> bool:
    """
    Sonde TCP avant d'instancier le SDK.

    Sans elle, un Langfuse éteint coûtait ~60 s par requête du coach : le SDK
    retente 3 fois avec backoff exponentiel, et ces retries s'exécutent sur le
    chemin de réponse. L'observabilité ne doit jamais peser sur le service
    qu'elle observe.
    """
    from urllib.parse import urlparse
    parsed = urlparse(host)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except Exception:
        return False


def _api_ready(host: str, timeout: float = 2.0) -> bool:
    """Sonde applicative : le port ouvert ne dit rien de l'état du serveur.

    Vécu : `/api/public/health` répondait 200 pendant que TOUT endpoint touchant
    la base rendait 500 (conteneur Langfuse debout, base cassée). Le SDK partait
    alors en boucle de retries et déversait une trace de pile par batch dans
    logs/errors.log. On interroge donc un endpoint authentifié qui, lui, lit
    vraiment la base.
    """
    import base64
    import httpx
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-pfe-local")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-pfe-local")
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/public/projects",
                      headers={"Authorization": f"Basic {auth}"}, timeout=timeout)
        return r.status_code < 400
    except Exception:
        return False


class _NoTracebackFilter(logging.Filter):
    """Garde le message d'erreur du SDK, jette la pile.

    Une panne d'observabilité produit une erreur par batch — avec `exc_info`,
    c'est 25 lignes à chaque fois et un errors.log illisible pour les vraies
    erreurs applicatives.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.exc_info = None
        record.exc_text = None
        return True


def _quiet_sdk_logging() -> None:
    """Le SDK Langfuse et son backoff ne parlent qu'en cas de problème réel."""
    for name in ("langfuse", "langfuse.task_manager", "langfuse.request", "backoff"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.ERROR)
        if not any(isinstance(f, _NoTracebackFilter) for f in lg.filters):
            lg.addFilter(_NoTracebackFilter())


def get_langfuse():
    """Retourne l'instance Langfuse singleton, ou None si indisponible."""
    global _lf, _lf_checked
    if _lf is not None or _lf_checked:
        return _lf

    _lf_checked = True   # une seule tentative par process : pas de sonde par requête
    host = os.getenv("LANGFUSE_HOST", "http://localhost:3001")

    if os.getenv("LANGFUSE_ENABLED", "true").lower() != "true":
        logger.info("Langfuse desactive (LANGFUSE_ENABLED=false)")
        return None

    if not _host_reachable(host):
        logger.warning("Langfuse injoignable (%s) — tracing desactive pour ce process", host)
        return None

    if not _api_ready(host):
        logger.warning("Langfuse debout mais API en erreur (%s) — tracing desactive "
                       "pour ce process (verifier `docker compose ps langfuse`)", host)
        return None

    _quiet_sdk_logging()
    try:
        from langfuse import Langfuse
        _lf = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-pfe-local"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-pfe-local"),
            host=host,
            # L'upload tourne dans les threads du task_manager (jamais dans
            # l'event loop) : un timeout court n'y protège rien et faisait
            # échouer l'ingestion des gros batchs (ReadTimeout après 3 retries).
            # 2 threads + batchs de 50 : draine la file plus vite que le rythme
            # de production des spans (sinon « analytics-python queue is full »
            # et traces perdues). Le volume inventory est aussi échantillonné
            # côté producteur (LANGFUSE_INVENTORY_SAMPLE, défaut 0.1).
            timeout=60,  # Increased from 30 to avoid upload timeouts
            threads=2,
            flush_at=50,
        )
        logger.info("Langfuse v2 connecte -> %s", host)
        return _lf
    except Exception as e:
        logger.warning(f"Langfuse indisponible: {e}")
        return None


def _ts():
    return datetime.now().strftime("%Y%m%d")


def _uid(prefix=""):
    return f"{prefix}{uuid.uuid4().hex[:8]}"


# ══════════════════════════════════════════════════════════════════════════════
# TRACER PRINCIPAL — API fluide avec context managers
# ══════════════════════════════════════════════════════════════════════════════

class LangfuseTracer:
    """Traceur central pour tout le systeme multi-agent."""

    def __init__(self, store_id: str = DEFAULT_STORE_ID):
        self.store_id = store_id
        self.lf = get_langfuse()

    @property
    def enabled(self):
        return self.lf is not None

    # ── Cycle complet (Analyste -> Stratege -> Coach) ─────────────────────

    @contextmanager
    def cycle(self, cycle_id: str, trigger: str = "cron"):
        """Trace un cycle orchestrateur complet."""
        ctx = _CycleTrace(self.lf, cycle_id, self.store_id, trigger)
        ctx.start()
        try:
            yield ctx
        finally:
            ctx.finish()

    # ── Agent individuel ──────────────────────────────────────────────────

    @contextmanager
    def agent(self, trace_id: str, agent_name: str):
        """Trace un agent (analyste, stratege, coach) comme span."""
        ctx = _AgentTrace(self.lf, trace_id, agent_name, self.store_id)
        ctx.start()
        try:
            yield ctx
        finally:
            ctx.finish()

    # ── Fonctions directes (backward compatible) ──────────────────────────

    def trace_llm_call(self, trace_id: str, agent_name: str, model: str,
                       system_prompt: str, user_prompt: str, response: str,
                       latency_ms: float, tokens_in: int = 0, tokens_out: int = 0,
                       metadata: dict = None):
        """Trace un appel LLM individuel."""
        if not self.lf:
            return
        try:
            gen_id = _uid(f"{agent_name}-llm-")
            self.lf.generation(
                id=gen_id,
                trace_id=trace_id,
                name=f"{agent_name}-llm",
                model=model,
                input=[
                    {"role": "system", "content": system_prompt[:800]},
                    {"role": "user", "content": user_prompt[:800]},
                ],
                output=response[:1000],
                usage={
                    "input": tokens_in or len(system_prompt.split()) + len(user_prompt.split()),
                    "output": tokens_out or len(response.split()),
                },
                metadata={
                    "latency_ms": round(latency_ms),
                    "agent": agent_name,
                    "store_id": self.store_id,
                    **(metadata or {}),
                },
            )
            # Pas de flush() ici : le SDK a un worker d'arriere-plan. Forcer
            # l'upload a chaque trace ajoutait un aller-retour reseau bloquant
            # sur le chemin de reponse du coach.
        except Exception as e:
            logger.debug(f"[LANGFUSE] trace_llm_call: {e}")

    def trace_rag_search(self, trace_id: str, agent_name: str,
                         query: str, nb_results: int, top_score: float,
                         scripts: list, latency_ms: float,
                         is_relevant: bool = False, min_score: float = 0.0):
        """Trace une recherche RAG Milvus."""
        if not self.lf:
            return
        try:
            self.lf.span(
                id=_uid("rag-"),
                trace_id=trace_id,
                name=f"{agent_name}-rag-search",
                input={"query": query[:300], "min_score": min_score},
                output={
                    "nb_results": nb_results,
                    "top_score": round(top_score, 3),
                    "is_relevant": is_relevant,
                    "scripts": [{"categorie": s.get("categorie", ""),
                                 "score": s.get("score", 0),
                                 "produit": s.get("produit", "")}
                                for s in scripts[:3]],
                },
                metadata={
                    "latency_ms": round(latency_ms),
                    "collection": "coaching_scripts",
                    "embed_model": "nomic-embed-text",
                    "agent": agent_name,
                },
            )
            # Pas de flush() ici : le SDK a un worker d'arriere-plan. Forcer
            # l'upload a chaque trace ajoutait un aller-retour reseau bloquant
            # sur le chemin de reponse du coach.
        except Exception as e:
            logger.debug(f"[LANGFUSE] trace_rag_search: {e}")

    def trace_node(self, trace_id: str, agent_name: str, node_name: str,
                   input_data: dict, output_data: dict, latency_ms: float,
                   level: str = "DEFAULT", metadata: dict = None):
        """Trace un noeud LangGraph individuel."""
        if not self.lf:
            return
        try:
            self.lf.span(
                id=_uid(f"{node_name}-"),
                trace_id=trace_id,
                name=f"{agent_name}/{node_name}",
                input=_truncate_dict(input_data, 500),
                output=_truncate_dict(output_data, 500),
                level=level,
                metadata={
                    "latency_ms": round(latency_ms),
                    "agent": agent_name,
                    "node": node_name,
                    "store_id": self.store_id,
                    **(metadata or {}),
                },
            )
        except Exception as e:
            logger.debug(f"[LANGFUSE] trace_node: {e}")

    def trace_sql(self, trace_id: str, query_name: str,
                  sql: str, latency_ms: float, nb_rows: int = 0,
                  error: str = None):
        """Trace une requete PostgreSQL."""
        if not self.lf:
            return
        try:
            self.lf.span(
                id=_uid("sql-"),
                trace_id=trace_id,
                name=f"sql/{query_name}",
                input={"sql": sql[:200]},
                output={"nb_rows": nb_rows, "error": error[:100] if error else None},
                level="ERROR" if error else "DEFAULT",
                metadata={"latency_ms": round(latency_ms)},
            )
        except Exception as e:
            logger.debug(f"[LANGFUSE] trace_sql: {e}")

    def score(self, trace_id: str, name: str, value: float, comment: str = ""):
        """Enregistre un score de qualite."""
        if not self.lf:
            return
        try:
            self.lf.score(trace_id=trace_id, name=name,
                          value=round(value, 3), comment=comment[:200])
            # Pas de flush() ici : le SDK a un worker d'arriere-plan. Forcer
            # l'upload a chaque trace ajoutait un aller-retour reseau bloquant
            # sur le chemin de reponse du coach.
        except Exception as e:
            logger.debug(f"[LANGFUSE] score: {e}")

    def flush(self):
        if self.lf:
            try:
                self.lf.flush()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXT MANAGERS — Traces hierarchiques
# ══════════════════════════════════════════════════════════════════════════════

class _CycleTrace:
    """Trace d'un cycle orchestrateur complet."""

    def __init__(self, lf, cycle_id, store_id, trigger):
        self.lf = lf
        self.cycle_id = cycle_id
        self.store_id = store_id
        self.trigger = trigger
        self.trace = None
        self.t0 = 0
        self.metrics = {}

    def start(self):
        if not self.lf:
            return
        self.t0 = time.time()
        try:
            self.trace = self.lf.trace(
                id=self.cycle_id,
                name="coaching-cycle",
                user_id=self.store_id,
                session_id=f"session-{self.store_id}-{_ts()}",
                tags=["cycle", self.store_id, self.trigger],
                input={"trigger": self.trigger, "store_id": self.store_id,
                       "timestamp": datetime.now().isoformat()},
            )
        except Exception as e:
            logger.debug(f"[LANGFUSE] cycle start: {e}")

    def set_metrics(self, **kwargs):
        self.metrics.update(kwargs)

    @contextmanager
    def agent(self, agent_name: str):
        ctx = _AgentTrace(self.lf, self.cycle_id, agent_name, self.store_id)
        ctx.start()
        try:
            yield ctx
        finally:
            ctx.finish()

    def finish(self):
        if not self.lf:
            return
        duration = (time.time() - self.t0) * 1000
        try:
            if self.trace:
                self.trace.update(
                    output={**self.metrics, "total_ms": round(duration)},
                )
            # Score cycle
            gap = self.metrics.get("gap_pct", 0)
            urgency = self.metrics.get("urgency", "MEDIUM")
            nb_actions = self.metrics.get("nb_actions", 0)
            uw = {"CRITICAL": 1.0, "HIGH": 0.8, "MEDIUM": 0.5, "LOW": 0.2}.get(urgency, 0.5)
            aw = min(1.0, nb_actions / 3) if nb_actions else 0
            sw = max(0.0, 1.0 - duration / 60000)
            score_val = round(uw * 0.3 + aw * 0.4 + sw * 0.3, 3)
            self.lf.score(trace_id=self.cycle_id, name="cycle-quality",
                          value=score_val,
                          comment=f"urgency={urgency} gap={gap:.1f}% actions={nb_actions}")
            # Pas de flush() ici : le SDK a un worker d'arriere-plan. Forcer
            # l'upload a chaque trace ajoutait un aller-retour reseau bloquant
            # sur le chemin de reponse du coach.
        except Exception as e:
            logger.debug(f"[LANGFUSE] cycle finish: {e}")


class _AgentTrace:
    """Trace d'un agent individuel (analyste, stratege, coach)."""

    def __init__(self, lf, trace_id, agent_name, store_id):
        self.lf = lf
        self.trace_id = trace_id
        self.agent_name = agent_name
        self.store_id = store_id
        self.span = None
        self.t0 = 0
        self.output = {}

    def start(self):
        if not self.lf:
            return
        self.t0 = time.time()
        try:
            self.span = self.lf.span(
                id=_uid(f"{self.agent_name}-"),
                trace_id=self.trace_id,
                name=f"agent-{self.agent_name}",
                input={"agent": self.agent_name, "store_id": self.store_id},
            )
        except Exception as e:
            logger.debug(f"[LANGFUSE] agent start: {e}")

    def set_output(self, **kwargs):
        self.output.update(kwargs)

    @contextmanager
    def node(self, node_name: str):
        ctx = _NodeTrace(self.lf, self.trace_id, self.agent_name, node_name)
        ctx.start()
        try:
            yield ctx
        finally:
            ctx.finish()

    @contextmanager
    def llm_call(self, model: str, system: str = "", user: str = ""):
        ctx = _LLMTrace(self.lf, self.trace_id, self.agent_name, model, system, user)
        ctx.start()
        try:
            yield ctx
        finally:
            ctx.finish()

    @contextmanager
    def rag_search(self, query: str):
        ctx = _RAGTrace(self.lf, self.trace_id, self.agent_name, query)
        ctx.start()
        try:
            yield ctx
        finally:
            ctx.finish()

    def finish(self):
        if not self.lf or not self.span:
            return
        duration = (time.time() - self.t0) * 1000
        try:
            self.span.update(
                output={**self.output, "duration_ms": round(duration)},
            )
        except Exception as e:
            logger.debug(f"[LANGFUSE] agent finish: {e}")


class _NodeTrace:
    """Trace d'un noeud LangGraph."""

    def __init__(self, lf, trace_id, agent_name, node_name):
        self.lf = lf
        self.trace_id = trace_id
        self.name = f"{agent_name}/{node_name}"
        self.node_name = node_name
        self.t0 = 0
        self.output = {}

    def start(self):
        self.t0 = time.time()

    def set_output(self, data: dict = None, **kwargs):
        if data:
            self.output.update(_truncate_dict(data, 300))
        self.output.update(kwargs)

    def set_error(self, error: str):
        self.output["error"] = str(error)[:200]

    def finish(self):
        if not self.lf:
            return
        duration = (time.time() - self.t0) * 1000
        try:
            self.lf.span(
                id=_uid(f"{self.node_name}-"),
                trace_id=self.trace_id,
                name=self.name,
                output={**self.output, "duration_ms": round(duration)},
                level="ERROR" if "error" in self.output else "DEFAULT",
                metadata={"latency_ms": round(duration)},
            )
        except Exception as e:
            logger.debug(f"[LANGFUSE] node finish: {e}")


class _LLMTrace:
    """Trace d'un appel LLM."""

    def __init__(self, lf, trace_id, agent_name, model, system, user):
        self.lf = lf
        self.trace_id = trace_id
        self.agent_name = agent_name
        self.model = model
        self.system = system
        self.user = user
        self.t0 = 0
        self.response = ""
        self.tokens_in = 0
        self.tokens_out = 0
        self.metadata = {}

    def start(self):
        self.t0 = time.time()

    def set_response(self, response: str, tokens_in: int = 0,
                     tokens_out: int = 0, **meta):
        self.response = response
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.metadata.update(meta)

    def finish(self):
        if not self.lf:
            return
        duration = (time.time() - self.t0) * 1000
        try:
            self.lf.generation(
                id=_uid(f"{self.agent_name}-llm-"),
                trace_id=self.trace_id,
                name=f"{self.agent_name}-llm",
                model=self.model,
                input=[
                    {"role": "system", "content": self.system[:800]},
                    {"role": "user", "content": self.user[:800]},
                ],
                output=self.response[:1000],
                usage={
                    "input": self.tokens_in or _estimate_tokens(self.system + self.user),
                    "output": self.tokens_out or _estimate_tokens(self.response),
                },
                metadata={
                    "latency_ms": round(duration),
                    "agent": self.agent_name,
                    **self.metadata,
                },
            )
        except Exception as e:
            logger.debug(f"[LANGFUSE] llm finish: {e}")


class _RAGTrace:
    """Trace d'une recherche RAG."""

    def __init__(self, lf, trace_id, agent_name, query):
        self.lf = lf
        self.trace_id = trace_id
        self.agent_name = agent_name
        self.query = query
        self.t0 = 0
        self.results = []
        self.is_relevant = False

    def start(self):
        self.t0 = time.time()

    def set_results(self, scripts: list, is_relevant: bool = False):
        self.results = scripts
        self.is_relevant = is_relevant

    def finish(self):
        if not self.lf:
            return
        duration = (time.time() - self.t0) * 1000
        try:
            top_score = self.results[0]["score"] if self.results else 0
            self.lf.span(
                id=_uid("rag-"),
                trace_id=self.trace_id,
                name=f"{self.agent_name}-rag",
                input={"query": self.query[:300]},
                output={
                    "nb_results": len(self.results),
                    "top_score": round(top_score, 3),
                    "is_relevant": self.is_relevant,
                    "categories": [s.get("categorie", "") for s in self.results[:3]],
                },
                metadata={
                    "latency_ms": round(duration),
                    "collection": "coaching_scripts",
                    "embed_model": "nomic-embed-text",
                },
            )
        except Exception as e:
            logger.debug(f"[LANGFUSE] rag finish: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# FONCTIONS DIRECTES — Backward compatible avec le code existant
# ══════════════════════════════════════════════════════════════════════════════

def trace_full_cycle(
    cycle_id: str, store_id: str, trigger: str,
    urgency_level: str, gap_pct: float, gap_amount: float,
    analyst_summary: str,
    nb_actions: int, rag_used: bool, nb_rag_scripts: int,
    analyste_ms: float, stratege_ms: float, coach_ms: float,
    total_ms: float,
    conseil_final: str = "", coach_score: float = 0.0,
):
    """Trace un cycle complet — compatible avec l'appel existant."""
    lf = get_langfuse()
    if not lf:
        return
    try:
        trace = lf.trace(
            id=cycle_id,
            name="coaching-cycle",
            user_id=store_id,
            session_id=f"session-{store_id}-{_ts()}",
            tags=["cycle", store_id, trigger],
            input={
                "trigger": trigger, "store_id": store_id,
                "urgency": urgency_level, "gap_pct": round(gap_pct, 1),
            },
            output={
                "nb_actions": nb_actions, "rag_used": rag_used,
                "conseil": conseil_final[:200] if conseil_final else "",
            },
        )

        # Span Analyste
        lf.span(
            id=_uid("analyste-"), trace_id=cycle_id,
            name="agent-analyste",
            input={"store_id": store_id},
            output={"urgency": urgency_level, "gap_pct": round(gap_pct, 1),
                     "summary": analyst_summary[:200]},
            metadata={"latency_ms": round(analyste_ms)},
        )

        # Span Stratege
        lf.span(
            id=_uid("stratege-"), trace_id=cycle_id,
            name="agent-stratege",
            input={"gap_pct": gap_pct, "urgency": urgency_level},
            output={"nb_actions": nb_actions, "rag_used": rag_used,
                     "nb_rag_scripts": nb_rag_scripts},
            metadata={"latency_ms": round(stratege_ms)},
        )

        # Span Coach
        if coach_ms > 0:
            lf.span(
                id=_uid("coach-"), trace_id=cycle_id,
                name="agent-coach",
                output={"conseil": conseil_final[:200], "score": coach_score},
                metadata={"latency_ms": round(coach_ms)},
            )

        # Score qualite
        uw = {"CRITICAL": 1.0, "HIGH": 0.8, "MEDIUM": 0.5, "LOW": 0.2}.get(urgency_level, 0.5)
        aw = min(1.0, nb_actions / 3) if nb_actions else 0
        sw = max(0.0, 1.0 - total_ms / 60000)
        score_val = round(uw * 0.3 + aw * 0.4 + sw * 0.3, 3)
        lf.score(trace_id=cycle_id, name="cycle-quality", value=score_val,
                 comment=f"urgency={urgency_level} gap={gap_pct:.1f}% actions={nb_actions}")
        lf.flush()
        logger.debug(f"[LANGFUSE] Cycle trace: {cycle_id} score={score_val}")
    except Exception as e:
        logger.warning(f"[LANGFUSE] trace_full_cycle: {e}")


def trace_coach_chat(
    store_id: str, advisor_name: str,
    message: str, response: str,
    question_type: str = "general",
    rag_used: bool = False, nb_rag_scripts: int = 0,
    confidence: float = 0.0, latency_ms: float = 0.0,
    gap_pct: float = 0.0, urgency: str = "MEDIUM",
    domain: str = "sales", mode: str = "conversation",
    model: str = "", rag_scores: list = None,
):
    """Trace une interaction Coach Chat complete."""
    lf = get_langfuse()
    if not lf:
        return
    try:
        trace_id = _uid(f"chat-{store_id}-")
        trace = lf.trace(
            id=trace_id,
            name="coach-chat",
            user_id=store_id,
            session_id=f"chat-{store_id}-{advisor_name.replace(' ', '_')}-{_ts()}",
            tags=["coach-chat", domain, mode, store_id],
            input={
                "message": message[:300],
                "advisor": advisor_name,
                "domain": domain,
                "mode": mode,
                "question_type": question_type,
            },
            output={
                "reply": response[:500],
                "confidence": confidence,
                "rag_used": rag_used,
                "nb_rag_scripts": nb_rag_scripts,
            },
            metadata={
                "latency_ms": round(latency_ms),
                "gap_pct": round(gap_pct, 1),
                "urgency": urgency,
                "model": model or "unknown",
            },
        )

        # Generation LLM si model connu
        if model and "fallback" not in model:
            lf.generation(
                id=_uid("chat-llm-"),
                trace_id=trace_id,
                name="coach-chat-llm",
                model=model,
                input=[{"role": "user", "content": message[:500]}],
                output=response[:800],
                usage={
                    "input": _estimate_tokens(message),
                    "output": _estimate_tokens(response),
                },
                metadata={"latency_ms": round(latency_ms), "domain": domain, "mode": mode},
            )

        # RAG span si utilise
        if rag_used and nb_rag_scripts > 0:
            lf.span(
                id=_uid("chat-rag-"),
                trace_id=trace_id,
                name="coach-chat-rag",
                input={"query_type": question_type},
                output={
                    "nb_scripts": nb_rag_scripts,
                    "scores": [s.get("score", 0) for s in (rag_scores or [])[:3]],
                },
                metadata={"collection": "coaching_scripts"},
            )

        # Scores
        lf.score(trace_id=trace_id, name="chat-confidence",
                 value=confidence,
                 comment=f"{domain}/{mode}/{question_type}")

        # Score reactivite
        speed_score = max(0.0, 1.0 - latency_ms / 30000)
        lf.score(trace_id=trace_id, name="chat-speed",
                 value=round(speed_score, 3),
                 comment=f"{latency_ms:.0f}ms")

        lf.flush()
    except Exception as e:
        logger.warning(f"[LANGFUSE] trace_coach_chat: {e}")


def trace_rag_search(
    agent_name: str, store_id: str,
    query: str, nb_results: int, top_score: float,
    latency_ms: float, is_relevant: bool = False,
    scripts: list = None,
):
    """Trace une recherche RAG — backward compatible."""
    lf = get_langfuse()
    if not lf:
        return
    try:
        trace_id = _uid(f"rag-{agent_name}-")
        lf.trace(
            id=trace_id,
            name=f"{agent_name}-rag",
            user_id=store_id,
            tags=[agent_name, "rag", store_id],
            input={"query": query[:300]},
            output={
                "nb_results": nb_results,
                "top_score": round(top_score, 3),
                "is_relevant": is_relevant,
                "scripts": [{"categorie": s.get("categorie", ""),
                             "score": s.get("score", 0)}
                            for s in (scripts or [])[:3]],
            },
            metadata={
                "latency_ms": round(latency_ms),
                "collection": "coaching_scripts",
                "embed_model": "nomic-embed-text",
            },
        )
        lf.flush()
    except Exception as e:
        logger.warning(f"[LANGFUSE] trace_rag_search: {e}")


def trace_llm_generation(
    trace_id: str, agent_name: str, model: str,
    system_prompt: str, user_prompt: str,
    response: str, latency_ms: float,
    tokens_in: int = 0, tokens_out: int = 0,
    metadata: dict = None,
):
    """Trace un appel LLM individuel — backward compatible."""
    lf = get_langfuse()
    if not lf:
        return
    try:
        lf.generation(
            id=_uid(f"{agent_name}-gen-"),
            trace_id=trace_id,
            name=f"{agent_name}-llm",
            model=model,
            input=[
                {"role": "system", "content": system_prompt[:800]},
                {"role": "user", "content": user_prompt[:800]},
            ],
            output=response[:1000],
            usage={
                "input": tokens_in or _estimate_tokens(system_prompt + user_prompt),
                "output": tokens_out or _estimate_tokens(response),
            },
            metadata={
                "latency_ms": round(latency_ms),
                "agent": agent_name,
                **(metadata or {}),
            },
        )
        lf.flush()
    except Exception as e:
        logger.debug(f"[LANGFUSE] trace_llm_generation: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _estimate_tokens(text: str) -> int:
    """Estimation rapide des tokens (1 token ~ 4 chars en francais)."""
    return max(1, len(text) // 4)


def _truncate_dict(d: dict, max_val_len: int = 300) -> dict:
    """Tronque les valeurs string d'un dict."""
    result = {}
    for k, v in d.items():
        if isinstance(v, str) and len(v) > max_val_len:
            result[k] = v[:max_val_len] + "..."
        elif isinstance(v, dict):
            result[k] = _truncate_dict(v, max_val_len)
        else:
            result[k] = v
    return result
