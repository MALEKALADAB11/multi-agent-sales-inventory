# Architecture Cible — Système Multi-Agents LangGraph Unifié

> **Objet** : critique de l'architecture multi-agents actuelle (vérifiée dans le code, branche
> `refactor/monolith-v2`) et proposition d'une architecture cible plus solide et plus cohérente,
> exploitant les primitives LangGraph au bon endroit (subgraphs, `Send()`, `Command`,
> `interrupt()`, checkpointer, store, streaming natif).
>
> **Date** : 2026-07-17
> **Sources code auditées** : `app/sales/orchestration/supervisor_agent.py`,
> `app/inventory/core/supervisor.py`, `app/sales/core/retail_state.py`,
> `app/inventory/core/state.py`, `app/sales/orchestration/graph.py`,
> `app/sales/coaching/agents/` (analyst, stratege, coach, guardrail),
> `app/inventory/agents/` (analysis, context, decision), `app/inventory/services/orchestrator.py`,
> `app/api/supervisor.py`, `app/sales/core/alert_bus.py`.

---

## Partie A — Critique de l'architecture actuelle

### A.1 Deux superviseurs concurrents qui font la même chose différemment ⚠️ structurel

C'est le problème principal. `app/sales/orchestration/supervisor_agent.py` (Sales) et
`app/inventory/core/supervisor.py` (Inventory) implémentent **le même pattern** — fan-out
parallèle → fusion → coach → guardrail → routage conditionnel — avec **deux codes différents,
deux états différents** (`app/sales/core/retail_state.py` *et* `app/inventory/core/state.py`,
tous deux nommés `RetailState`) et **des sémantiques de routage contradictoires** :

| Aspect | Superviseur Sales | Superviseur Inventory |
|---|---|---|
| Fan-out | arêtes statiques (`add_edge` ×4) | `Send()` dynamique |
| Verdict BLOCK | → `safe_fallback` → diffusion quand même | → `END` sec, aucune sortie |
| Verdict REWRITE | 1 re-passe, **sans compteur** | ≤ 2 cycles, compteur `guardrail_cycles` |
| HITL | `submit_hitl_review` puis le graphe continue | nœud `human_review` → output |
| Nœuds | codés en dur dans le module | injectés en callables (testable) |

Deux réponses différentes à la même question (« que fait un BLOCK ? ») dans le même système :
c'est une incohérence de conception, pas un détail d'implémentation.

### A.2 Le HITL n'est pas un vrai Human-in-the-Loop ⚠️ critique

Dans `node_human_validation` (supervisor_agent.py:431-450), le nœud soumet la review via
`submit_hitl_review(...)` puis **le graphe continue immédiatement** vers `notify_frontend` →
`save_memory` → END. La décision humaine arrive *après* la fin du graphe et **n'est jamais
réinjectée dans le cycle qui l'a demandée**. LangGraph fournit une primitive exactement pour ce
cas — `interrupt()` + checkpointer + `Command(resume=...)` — et elle n'est pas utilisée.

Le « Human-in-the-loop systématique » revendiqué est en réalité un *Human-après-la-boucle* :
l'humain valide en aval, le graphe a déjà terminé.

### A.3 Aucun checkpointer ⚠️ critique

Tous les graphes sont compilés avec `workflow.compile()` **sans checkpointer**. Conséquences :

- pas de persistance d'état entre invocations, pas de `thread_id` ;
- pas de reprise après un crash au milieu d'un cycle (les branches déjà exécutées sont perdues) ;
- pas de `get_state_history()` → pas de time-travel pour le debug, pas d'audit d'état natif ;
- le HITL bloquant (`interrupt()`) est mécaniquement impossible (cf. A.2).

C'est la fonctionnalité LangGraph la plus importante du point de vue production, et elle est absente.

### A.4 La boucle REWRITE est cassée côté Sales ⚠️ bug latent

Deux défauts combinés dans le routage `route_after_guardrail` (supervisor_agent.py:541-550) :

1. `rewrite → coach_agent`, mais `node_coach_agent` **ne lit jamais `guardrail_feedback`** :
   il ré-exécute le même `rank_products` déterministe sur les mêmes données → même
   recommandation → même verdict. La re-passe ne peut pas produire un résultat différent.
2. Contrairement au superviseur Inventory, **il n'y a pas de compteur** : si le verdict reste
   REWRITE, le cycle `coach_agent → guardrail → coach_agent` boucle jusqu'à la
   `recursion_limit` LangGraph. Le commentaire du code dit « one extra pass » mais rien ne le
   garantit.

### A.5 Guardrail fail-open ⚠️ sécurité

`node_guardrail` : `except Exception → {"guardrail_status": "APPROVE"}`. Si le contrôleur
plante, **tout passe sans contrôle**. Pour un composant dont le rôle est de bloquer les réponses
non conformes, le repli sûr devrait être ESCALATE (ou au minimum REWRITE), jamais APPROVE.

### A.6 Couplages cachés et inversion de couches

- `node_sales_branch` fait `from main import app` pour récupérer un singleton global
  (`app.state.orchestrator`) — dépendance d'un nœud de graphe vers le module d'entrée FastAPI :
  non testable isolément, import circulaire latent.
- `node_inventory_branch` appelle **un handler de route FastAPI** (`analyze_store`) comme
  fonction interne, avec un commentaire expliquant qu'il faut passer chaque kwarg explicitement
  sinon on reçoit les sentinelles `Query()`. La couche API est utilisée comme couche service —
  symptôme qu'il manque une vraie couche service partagée entre les deux modules.

### A.7 Les sous-graphes ne sont pas des subgraphs LangGraph

L'Analyste, le Stratège, le Coach et le `CycleOrchestrator` sont appelés comme **fonctions
Python wrappées dans un nœud** (ex. `sales_branch` → `orchestrator.run_cycle(...)`), pas montés
comme subgraphs compilés (`add_node("sales", compiled_subgraph)`). Conséquences :

- pas de trace unifiée parent/enfant — le tracing Langfuse est recousu à la main à chaque niveau ;
- pas de streaming des nœuds internes via `astream()` du graphe parent ;
- pas de checkpoint traversant (une reprise ne peut pas redémarrer au milieu d'un sous-graphe) ;
- trois niveaux de graphes (superviseur → CycleOrchestrator → agents) dont deux invisibles
  depuis le premier.

### A.8 État plat de ~60 champs avec ownership « par convention »

`RetailState` (sales) est un sac plat où la propriété des champs est protégée par des
**whitelists manuelles** : `_SALES_BRANCH_KEYS` (28 clés listées à la main dans
supervisor_agent.py:63-77), filtrage manuel des 6 clés guardrail, commentaires « ne surtout pas
retourner `**state` ». Le bug historique des widgets stratège/analyste vides venait exactement
de là. La protection est **disciplinaire, pas structurelle** : le prochain champ ajouté sans mise
à jour de la whitelist reproduira le bug.

### A.9 Deux modèles de parallélisme concurrents

- Superstep LangGraph (branches parallèles) côté superviseurs ;
- `ThreadPoolExecutor` imbriqués côté Inventory (`orchestrator.py` : pool externe multi-SKU +
  pool interne Analysis ∥ Context), **hors graphe** — invisibles au checkpointing, au streaming
  et aux traces.

Le fan-out par SKU est précisément le cas d'usage nominal de `Send()` (map-reduce natif).

### A.10 Redondances et chemins parallèles de vérité

- **RAG exécuté jusqu'à 3 fois par cycle** : `knowledge_branch` (réutilise `node_rag_search` du
  Stratège), le Stratège dans son propre sous-graphe (`rag_search`), et le Coach (`rag_search`).
- **`coach_chat.py` (~2 500 lignes) court-circuite le superviseur** : le chat a son propre
  pipeline complet (intention, cache, RAG, cascade LLM, guardrail, SSE artisanal) parallèle au
  graphe. Deux implémentations de « produire une réponse coach contrôlée », à maintenir en
  cohérence à la main.
- `node_save_memory` **crée et ferme un pool asyncpg à chaque cycle** — coût par invocation.
- Le fan-out du superviseur Sales est **aveugle** : les 4 branches tournent toujours, même pour
  une salutation (le filtre d'intention existe, mais un étage trop bas, dans `coach_chat.py`).

### A.11 Ce qui est bon et doit être conservé ✅

1. **LLM hors chemin critique** : moteur TS déterministe (Holt-Winters + backtest WAPE),
   `compute` d'Analysis, `constraints_check` de Decision, 7 règles guardrail pures.
2. **Deltas + reducers** (`operator.add`, `_merge_dict`) — le principe est le bon, c'est sa
   granularité qui est à revoir (cf. B.2).
3. **Isolation des pannes par branche** (try/except → `errors`, les autres branches continuent).
4. **Cascade LLM avec replis** (Mistral → OpenRouter → Groq → Ollama ; règles métier en dernier
   recours) et dégradation contrôlée partout (Milvus → corpus local, HW → linéaire → SQL).
5. **PO « SUGGÉRÉ » avec porte humaine obligatoire** sur le Kanban.
6. **Graphes compilés en singleton** (stateless, compilés une fois par process).
7. **Observabilité** : spans Langfuse par nœud *et par décision de routage*.

---

## Partie B — Architecture cible

### B.1 Principe directeur

> **Un superviseur unique, des subgraphs natifs par domaine, un état hiérarchique,
> un checkpointer PostgreSQL, un HITL par `interrupt()`.**
> Le chat conseiller et les cycles événementiels deviennent deux *entrées* du même graphe,
> plus deux systèmes parallèles.

```
                              ┌────────────────────────────────────────────────┐
 /chat · /stream ──┐          │        RETAIL SUPERVISOR (graphe unique)       │
 AlertBus Redis  ──┼─ entry ─▶│  checkpointer = PostgresSaver                  │
 /supervisor/run ──┘          │  store        = PostgresStore (mémoire LT)     │
                              │  thread_id    = cycle_id                       │
                              └────────────────────────────────────────────────┘
        supervisor (nœud routeur — classification d'intention)
            │  Command(goto=[Send("sales"), Send("inventory"), Send("knowledge"), Send("context")])
            │  — plan dynamique : n'active QUE les branches utiles à l'intention
            ▼
   ┌────────────┬──────────────┬──────────────┬─────────────┐
   │  sales     │  inventory   │  knowledge   │  context    │  ◀── subgraphs COMPILÉS montés
   │ (subgraph) │  (subgraph)  │ (nœud+cache) │ (nœud+cache)│      via add_node → trace,
   │ Analyste → │  Send() par  │  RAG unique, │  Sentinel   │      streaming et checkpoint
   │ Stratège   │  SKU (map-   │  résultat    │  (événements│      traversants
   │            │  reduce)     │  partagé     │   météo...) │
   └─────┬──────┴──────┬───────┴──────┬───────┴──────┬──────┘
         └─────────────┴─── fan-in (reducers par canal de domaine) ──┘
                              ▼
                        coach (subgraph)         ◀── lit control.guardrail_feedback en re-passe
                              ▼
                        guardrail (fail-closed, RetryPolicy)
                              ▼
              Command selon verdict + control.rewrite_count :
                APPROVE ────────────────▶ deliver
                REWRITE (count < 2) ────▶ coach   (feedback injecté dans le prompt)
                ESCALATE ───────────────▶ hitl_gate : interrupt(payload)  ⏸ PAUSE
                                          … reprise : Command(resume=décision humaine)
                                          approuvé → deliver · refusé → safe_output
                BLOCK ──────────────────▶ safe_output ─▶ deliver
                              ▼
                        deliver  →  persist  →  END
```

Sémantique **unique** des verdicts (fin de la divergence A.1) : BLOCK produit toujours un
`safe_output` diffusé (jamais une absence de réponse), REWRITE est borné à 2 partout, ESCALATE
suspend réellement le graphe.

### B.2 L'état : hiérarchique, ownership structurel

Remplacer le sac plat de ~60 champs par un état à **canaux par domaine**, chacun avec son propre
schéma et son reducer. Une branche ne peut structurellement écrire que sa clé — les whitelists
manuelles disparaissent.

```python
# app/core/graph/state.py  (UNIQUE — remplace les deux RetailState actuels)
import operator
from typing import Annotated, TypedDict

def merge_domain(a: dict, b: dict) -> dict:
    """Reducer : fusion superficielle du canal d'un domaine (deltas only)."""
    return {**(a or {}), **(b or {})}

class CycleMeta(TypedDict, total=False):
    cycle_id: str; store_id: str; advisor_id: str
    trigger_type: str          # advisor_message | stock_event | sales_event | scheduled | manual
    intent: str                # produit | objectif | script | stock | greeting | off_topic
    user_message: str

class SalesOutput(TypedDict, total=False):
    gap_pct: float; forecast_eod: float; attainment: float
    urgency_level: str; hourly_gaps: list; next_hours_forecast: list
    trend_signal: str; feasibility: str; analyst_summary: str
    strategie: str; strategie_actions: list; focus_produits: list
    cause_racine: str; critique_score: float; context_heatmap: dict

class InventoryOutput(TypedDict, total=False):
    decisions: list            # par SKU : {sku, action, order_qty, rationale}
    critical_alerts: list; snapshot: dict; suggested_pos: list

class KnowledgeOutput(TypedDict, total=False):
    scripts: list; query: str; source: str          # milvus | local_corpus

class ContextOutput(TypedDict, total=False):
    events_current: list; events_upcoming: list
    active_offers: list; weather: dict; signals: list

class CoachOutput(TypedDict, total=False):
    recommendation: dict; scored_products: list
    message_advisor: str; confidence: float

class ControlState(TypedDict, total=False):
    guardrail_verdict: str     # APPROVE | REWRITE | ESCALATE | BLOCK
    guardrail_issues: list; guardrail_feedback: str
    rewrite_count: int         # ← borne structurelle de la boucle REWRITE
    hitl_decision: dict        # ← décision humaine réinjectée par Command(resume=...)
    safe_output: str

class Obs(TypedDict, total=False):
    agents_invoked: Annotated[list, operator.add]
    metrics: Annotated[dict, merge_domain]
    errors: Annotated[list, operator.add]

class RetailState(TypedDict, total=False):
    meta:      CycleMeta
    sales:     Annotated[SalesOutput,     merge_domain]   # écrit UNIQUEMENT par subgraph sales
    inventory: Annotated[InventoryOutput, merge_domain]
    knowledge: Annotated[KnowledgeOutput, merge_domain]
    context:   Annotated[ContextOutput,   merge_domain]
    decision:  CoachOutput                                # écrit par coach
    control:   ControlState                               # écrit par guardrail / hitl_gate
    obs:       Annotated[Obs, merge_domain]
```

Les subgraphs déclarent en plus des **schémas `input`/`output` distincts** : le state privé
interne (prompts intermédiaires du Stratège, JSON partiels, messages ReAct) ne remonte jamais
dans l'état global du parent.

### B.3 Le graphe superviseur cible — squelette de code

```python
# app/core/graph/supervisor.py
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, Send, interrupt, RetryPolicy, CachePolicy
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

MAX_REWRITES = 2

# ── 1. Routeur d'intention (remplace le fan-out aveugle) ─────────────────────
async def supervisor(state: RetailState) -> Command:
    intent = classify_intent(state["meta"])          # déterministe, remonté de coach_chat.py
    branches = {
        "greeting":  [],                             # réponse directe, zéro branche
        "stock":     [Send("inventory", state)],
        "produit":   [Send("sales", state), Send("inventory", state), Send("knowledge", state)],
        "objectif":  [Send("sales", state), Send("context", state)],
    }.get(intent, [Send("sales", state), Send("inventory", state),
                   Send("knowledge", state), Send("context", state)])
    if not branches:
        return Command(goto="deliver",
                       update={"decision": {"message_advisor": greeting_reply(state)},
                               "meta": {**state["meta"], "intent": intent}})
    return Command(goto=branches, update={"meta": {**state["meta"], "intent": intent}})

# ── 2. Guardrail fail-closed + routage borné ─────────────────────────────────
async def guardrail(state: RetailState) -> Command:
    try:
        verdict, issues, feedback = run_seven_rules(state)   # G1..G7, inchangées
    except Exception as e:
        # FAIL-CLOSED : une panne du contrôleur escalade, elle n'approuve jamais
        verdict, issues, feedback = "ESCALATE", [{"rule": "G0", "error": str(e)}], ""
    count = state.get("control", {}).get("rewrite_count", 0)
    update = {"control": {"guardrail_verdict": verdict, "guardrail_issues": issues,
                          "guardrail_feedback": feedback, "rewrite_count": count}}
    if verdict == "REWRITE" and count < MAX_REWRITES:
        update["control"]["rewrite_count"] = count + 1
        return Command(goto="coach", update=update)          # coach LIT le feedback
    if verdict == "REWRITE":                                 # borne atteinte → escalade
        verdict = "ESCALATE"
    if verdict == "ESCALATE":
        return Command(goto="hitl_gate", update=update)
    if verdict == "BLOCK":
        return Command(goto="safe_output", update=update)
    return Command(goto="deliver", update=update)

# ── 3. HITL réel : le graphe SE MET EN PAUSE ─────────────────────────────────
async def hitl_gate(state: RetailState) -> Command:
    decision = interrupt({                                   # ⏸ checkpoint + pause
        "cycle_id":  state["meta"]["cycle_id"],
        "strategie": state["sales"].get("strategie", ""),
        "actions":   state["sales"].get("strategie_actions", [])[:3],
        "issues":    state["control"]["guardrail_issues"],
        "reco":      state["decision"].get("recommendation", {}),
    })
    # … reprise ici quand l'API appelle Command(resume=...) — decision = payload humain
    if decision.get("approved"):
        return Command(goto="deliver",
                       update={"control": {"hitl_decision": decision}})
    return Command(goto="safe_output",
                   update={"control": {"hitl_decision": decision}})

# ── 4. Assemblage — subgraphs COMPILÉS montés comme nœuds ────────────────────
def build_graph(services) -> "CompiledStateGraph":
    g = StateGraph(RetailState)
    g.add_node("supervisor", supervisor)
    g.add_node("sales",     build_sales_subgraph(services))      # Analyste → Stratège
    g.add_node("inventory", build_inventory_subgraph(services))  # Send() par SKU (B.4)
    g.add_node("knowledge", rag_node,
               cache_policy=CachePolicy(ttl=300))                # RAG UNIQUE, caché 5 min
    g.add_node("context",   context_node, cache_policy=CachePolicy(ttl=600))
    g.add_node("coach",     build_coach_subgraph(services))
    g.add_node("guardrail", guardrail,
               retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("hitl_gate",   hitl_gate)
    g.add_node("safe_output", safe_output_node)
    g.add_node("deliver",     deliver_node)                      # WS + SSE
    g.add_node("persist",     persist_node)                      # pool PG partagé (injecté)

    g.add_edge(START, "supervisor")
    for b in ("sales", "inventory", "knowledge", "context"):
        g.add_edge(b, "coach")                                   # fan-in par reducers
    g.add_edge("coach", "guardrail")
    g.add_edge("safe_output", "deliver")
    g.add_edge("deliver", "persist")
    g.add_edge("persist", END)
    return g   # compilé dans lifespan avec checkpointer + store (voir B.6)
```

Points notables :

- **`Command(goto=..., update=...)`** remplace les `add_conditional_edges` éparpillées : le
  routage et la mise à jour d'état sont co-localisés dans le nœud qui décide.
- **Pas d'arête statique `supervisor → branches`** : le plan est dynamique (liste de `Send`).
- **`CachePolicy`** sur knowledge/context : fin du RAG ×3 par cycle — un seul retrieval,
  résultat partagé via le canal `knowledge` de l'état.
- **`RetryPolicy`** sur le guardrail : une erreur transitoire est retentée avant d'escalader.
- **`services`** est injecté (dataclass : pool PG, client Milvus, fabrique LLM, bus WS) — fin de
  `from main import app` et de l'appel de handlers FastAPI (A.6).

### B.4 Subgraph Inventory — map-reduce natif par SKU

Remplace les `ThreadPoolExecutor` imbriqués de `services/orchestrator.py` (A.9) :

```python
# app/inventory/graph/subgraph.py
class InvState(TypedDict, total=False):
    meta: CycleMeta
    skus: list                                            # SKUs à analyser
    per_sku: Annotated[list, operator.add]                # reducer = collecte map-reduce
    inventory: Annotated[InventoryOutput, merge_domain]   # sortie remontée au parent

def fan_out_skus(state: InvState):
    return [Send("analyze_sku", {"meta": state["meta"], "sku": s})
            for s in state["skus"]]                       # parallélisme NATIF LangGraph

async def analyze_sku(item) -> dict:
    analysis = await analysis_agent(item)                 # fetch → compute → reason
    context  = await context_agent(item)                  # uplift demande (optionnel → 0)
    decision = await decision_agent(analysis, context)    # constraints_check → decide
    return {"per_sku": [decision]}                        # delta collecté par le reducer

def reduce_decisions(state: InvState) -> dict:
    per_sku = state["per_sku"]
    return {"inventory": {
        "decisions":       [d for d in per_sku if d["action"] in ("ORDER", "EXPEDITE")],
        "critical_alerts": [d for d in per_sku if d["risk"] in ("critical", "high")],
        "suggested_pos":   [d["po"] for d in per_sku if d.get("po")],
    }}

def build_inventory_subgraph(services):
    g = StateGraph(InvState, input=RetailState, output=RetailState)
    g.add_node("load_skus", load_skus_node)               # remplace l'appel au route handler
    g.add_node("analyze_sku", analyze_sku)
    g.add_node("reduce", reduce_decisions)
    g.add_edge(START, "load_skus")
    g.add_conditional_edges("load_skus", fan_out_skus, ["analyze_sku"])
    g.add_edge("analyze_sku", "reduce")
    g.add_edge("reduce", END)
    return g.compile()
```

Gains : le parallélisme par SKU est checkpointé, streamé et tracé comme le reste ; la création
de PO « SUGGÉRÉ » reste dans `decision_agent` avec la porte humaine inchangée.

### B.5 HITL de bout en bout — API de reprise

```python
# app/api/supervisor.py (cible)
@router.post("/run")
async def run_cycle(body: RunBody):
    config = {"configurable": {"thread_id": body.cycle_id or new_cycle_id()}}
    result = await graph.ainvoke(initial_state(body), config)
    if "__interrupt__" in result:                # graphe en pause sur hitl_gate
        return {"status": "pending_hitl",
                "cycle_id": config["configurable"]["thread_id"],
                "payload": result["__interrupt__"][0].value}
    return {"status": "completed", "state": public_view(result)}

@router.post("/hitl/{cycle_id}/resolve")
async def resolve_hitl(cycle_id: str, decision: HitlDecision):
    config = {"configurable": {"thread_id": cycle_id}}
    # Le graphe REPREND exactement où il s'était arrêté, décision humaine DANS l'état
    result = await graph.ainvoke(Command(resume=decision.model_dump()), config)
    return {"status": "completed", "state": public_view(result)}
```

La boucle de feedback (migration 0008) devient une **lecture de `get_state_history()`** : chaque
décision humaine est un checkpoint horodaté du cycle qui l'a demandée — plus de table à
réconcilier à la main.

### B.6 Checkpointer, store et cycle de vie

```python
# app/main.py (lifespan, cible)
async with AsyncPostgresSaver.from_conn_string(DB_URL) as checkpointer, \
           AsyncPostgresStore.from_conn_string(DB_URL) as store:
    app.state.graph = build_graph(services).compile(
        checkpointer=checkpointer,     # threads, reprise, interrupt, time-travel
        store=store,                   # mémoire long-terme cross-thread
    )
    yield
```

Usage du **store** (mémoire long terme, namespacée) — remplace les tables ad hoc et le pool
asyncpg recréé à chaque cycle :

| Namespace | Contenu | Écrit par | Lu par |
|---|---|---|---|
| `("advisor", advisor_id)` | profil, historique de coaching, préférences | persist | coach |
| `("store", store_id, "feedback")` | notes « utile / pas utile », décisions HITL | persist | supervisor, stratège |
| `("store", store_id, "cycles")` | résumés de cycles (remplace `coach_interactions`) | persist | analyste (compare_with_memory) |

### B.7 Streaming natif — un seul chemin d'exécution pour le chat

`coach_chat.py` est absorbé : le chat devient une **entrée** du graphe unique
(`trigger_type="advisor_message"`), et le SSE utilise le streaming LangGraph :

```python
# app/api/chat.py (cible)
@router.post("/stream")
async def stream(body: ChatBody):
    config = {"configurable": {"thread_id": f"chat-{body.advisor_id}-{today()}"}}
    async def sse():
        async for mode, chunk in graph.astream(
            initial_state(body), config,
            stream_mode=["messages", "updates"],    # tokens LLM + updates de nœuds
        ):
            if mode == "messages":                  # tokens du coach → SSE
                yield sse_token(chunk)
            elif mode == "updates":                 # deltas de nœuds → widgets WS
                await ws_bus.push(body.store_id, chunk)
        yield sse_done()
    return StreamingResponse(sse(), media_type="text/event-stream")
```

Un seul pipeline exécute, contrôle (guardrail), trace et streame — le chat n'est plus un
système parallèle de 2 500 lignes. Les optimisations existantes (cache Redis des réponses,
réponse directe aux salutations) vivent dans le nœud `supervisor` (routage d'intention) et
restent effectives.

### B.8 Correspondance problème → solution → notion LangGraph

| # | Problème constaté (Partie A) | Solution cible | Primitive LangGraph |
|---|---|---|---|
| A.1 | 2 superviseurs divergents | 1 superviseur, sémantique unique des verdicts | `StateGraph` unique + subgraphs |
| A.2 | HITL fire-and-forget | pause réelle, reprise avec la décision dans l'état | `interrupt()` / `Command(resume=...)` |
| A.3 | aucun checkpointer | reprise, audit, time-travel, threads | `AsyncPostgresSaver`, `thread_id`, `get_state_history()` |
| A.4 | REWRITE sans effet ni borne | `control.rewrite_count` + feedback lu par le coach | `Command(goto, update)` |
| A.5 | guardrail fail-open | exception → ESCALATE (fail-closed) + retries | `RetryPolicy` par nœud |
| A.6 | import de `main`, appel de routes | couche service injectée à la construction | injection `services` / `RunnableConfig` |
| A.7 | sous-graphes = fonctions wrappées | subgraphs compilés montés dans le parent | `add_node(name, compiled_graph)` + schémas `input`/`output` |
| A.8 | état plat, ownership disciplinaire | état hiérarchique par canal de domaine | canaux typés + reducers par canal |
| A.9 | ThreadPools hors graphe | fan-out par SKU dans le graphe | `Send()` map-reduce + reducer `operator.add` |
| A.10a | RAG ×3 par cycle | nœud knowledge unique, résultat partagé et caché | `CachePolicy(ttl=...)` |
| A.10b | chat parallèle au superviseur | chat = entrée du même graphe, SSE natif | `astream(stream_mode=["messages","updates"])` |
| A.10c | fan-out aveugle (4 branches toujours) | plan dynamique selon l'intention | `Command(goto=[Send(...)])` |
| A.10d | pool asyncpg créé/fermé par cycle | pool partagé injecté + store pour la mémoire | `BaseStore` (`AsyncPostgresStore`) |

### B.9 Récapitulatif des notions LangGraph mobilisées

| Notion | Rôle dans l'architecture cible |
|---|---|
| `StateGraph` + canaux typés | contrat d'état unique, ownership structurel par domaine |
| Reducers (`Annotated[..., fn]`) | fusion sans écrasement des branches parallèles (conservé, généralisé) |
| Subgraphs compilés | Analyste/Stratège, Inventory, Coach — trace, stream et checkpoint traversants |
| Schémas `input`/`output` | state privé des subgraphs invisible du parent |
| `Send()` | fan-out dynamique : branches par intention, map-reduce par SKU |
| `Command(goto, update)` | routage + mise à jour co-localisés (superviseur, guardrail, hitl) |
| `interrupt()` / `Command(resume)` | HITL bloquant réel avec reprise |
| Checkpointer (`AsyncPostgresSaver`) | persistance par `thread_id`, reprise après crash, time-travel, audit |
| Store (`AsyncPostgresStore`) | mémoire long terme namespacée (profils, feedback, cycles) |
| `RetryPolicy` / `CachePolicy` | robustesse par nœud (guardrail) / déduplication (RAG, contexte) |
| `astream(stream_mode=...)` | SSE tokens + updates widgets depuis le même run |
| `recursion_limit` | filet de sécurité global (en plus de la borne explicite `rewrite_count`) |

---

## Partie C — Plan de migration (ordre de moindre risque)

Chaque étape est livrable indépendamment et laisse le système fonctionnel.

### Étape 1 — Quick wins sans refonte (corrige A.4, A.5, A.6, A.10d)
- Ajouter `rewrite_count` au state Sales + borne à 2 + injection de `guardrail_feedback` dans
  `node_coach_agent`.
- Guardrail fail-closed : `except → ESCALATE`.
- Pool PostgreSQL partagé (créé au lifespan) pour `save_memory`.
- Extraire une couche service : `app/inventory/services/analysis_service.py` appelée par la
  route *et* par `inventory_branch` — supprimer `from main import app` et l'appel du handler.

### Étape 2 — Checkpointer (corrige A.3)
- `AsyncPostgresSaver` sur les graphes existants, `thread_id = cycle_id`, sans changer la
  topologie. Gains immédiats : audit d'état, reprise, debug time-travel.

### Étape 3 — HITL réel (corrige A.2)
- Remplacer `submit_hitl_review` fire-and-forget par `interrupt()` dans un nœud `hitl_gate`.
- Adapter `/hitl/*/resolve` en `Command(resume=...)`. Le panneau Angular existant change peu :
  il lit le payload d'interrupt au lieu de la table `hitl_pending`.

### Étape 4 — État hiérarchique + subgraphs (corrige A.7, A.8)
- Introduire le `RetailState` hiérarchique (B.2) ; adapter les nœuds pour écrire dans leur canal.
- Convertir Analyste/Stratège/Coach en subgraphs compilés montés ; supprimer `CycleOrchestrator`
  (le graphe parent reprend son rôle) et les whitelists.

### Étape 5 — Fusion des superviseurs + inventory map-reduce (corrige A.1, A.9)
- Un seul graphe superviseur avec la sémantique unifiée des verdicts.
- Subgraph inventory en `Send()` par SKU (B.4) ; retirer les `ThreadPoolExecutor`.

### Étape 6 — Absorption du chat + streaming natif (corrige A.10b, A.10c)
- Le routeur d'intention remonte dans le nœud `supervisor` ; `/chat` et `/stream` invoquent le
  graphe unique avec `astream()` ; démanteler progressivement le pipeline propre de
  `coach_chat.py` (garder cache Redis et réponses directes dans le routeur).

### Invariants à préserver pendant toute la migration
1. Aucune commande (PO) exécutée sans approbation humaine.
2. Les chiffres (prévisions, métriques stock) restent 100 % déterministes, LLM hors chemin critique.
3. Toute réponse visible passe par le guardrail — désormais fail-closed.
4. Chaque dépendance garde son repli (Milvus → corpus, HW → linéaire → SQL, cascade LLM).
5. Traçabilité complète — renforcée par les checkpoints.

---

*Voir aussi : `docs/ARCHITECTURE_COMPLETE.md` (état actuel), `docs/architecture_multi_agents.svg|png`
(diagramme de l'existant). Références LangGraph : concepts `persistence`, `human-in-the-loop`,
`subgraphs`, `Send API`, `Command`, `streaming`, `memory store`.*
