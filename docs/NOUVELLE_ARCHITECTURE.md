# Nouvelle Architecture Multi-Agents LangGraph — Moteur Retail Ooredoo

> **Solution** : un système multi-agents unifié pour le coaching de vente temps réel et
> l'optimisation des stocks, construit sur **un graphe LangGraph unique** avec subgraphs par
> domaine, état hiérarchique, checkpointing PostgreSQL, Human-in-the-Loop bloquant par
> `interrupt()`, et streaming natif.
>
> **Date** : 2026-07-17

---

## 1. Principe directeur

> **Un superviseur unique, des subgraphs natifs par domaine, un état hiérarchique,
> un checkpointer PostgreSQL, un HITL par `interrupt()`.**
> Le chat conseiller, les cycles événementiels (AlertBus) et les appels API sont trois
> *entrées* du même graphe — il n'existe qu'un seul chemin d'exécution, contrôlé,
> tracé et streamé de bout en bout.

Cinq propriétés en découlent :

1. **Une seule vérité d'orchestration** — une sémantique unique des verdicts guardrail, une
   seule boucle de réécriture, une seule porte humaine.
2. **Ownership structurel de l'état** — chaque agent ne peut écrire que dans son canal ; les
   conflits d'écriture entre branches parallèles sont impossibles par construction.
3. **Persistance native** — chaque cycle est un `thread_id` checkpointé : reprise après crash,
   pause HITL, audit et time-travel sans code ad hoc.
4. **Parallélisme homogène** — tout le parallélisme (branches de domaine, SKUs) passe par les
   primitives LangGraph (`Send()`), donc est visible du checkpointing, du streaming et des traces.
5. **LLM hors chemin critique** — les chiffres (prévisions, métriques stock, règles guardrail)
   restent 100 % déterministes ; le LLM formule, arbitre et enrichit, il ne calcule jamais.

---

## 2. Vue d'ensemble du graphe

```
                              ┌────────────────────────────────────────────────┐
 /chat · /stream ──┐          │        RETAIL SUPERVISOR (graphe unique)       │
 AlertBus Redis  ──┼─ entry ─▶│  checkpointer = AsyncPostgresSaver             │
 /supervisor/run ──┘          │  store        = AsyncPostgresStore (mémoire LT)│
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

**Sémantique unique des verdicts** (valable pour tout le système) :

| Verdict | Comportement |
|---|---|
| `APPROVE` | diffusion directe |
| `REWRITE` | retour au coach avec `guardrail_feedback` injecté dans le prompt, **borné à 2** ; borne atteinte → ESCALATE |
| `ESCALATE` | `interrupt()` — le graphe se met en pause, l'humain décide, le graphe reprend avec la décision |
| `BLOCK` | réponse de repli sûre, **toujours diffusée** — jamais d'absence de réponse |

---

## 3. L'état : `RetailState` hiérarchique

Un seul module d'état pour tout le système. L'état est organisé en **canaux par domaine** :
chaque subgraph écrit exclusivement dans son canal, les reducers fusionnent les écritures des
branches parallèles, et le state privé interne des subgraphs ne remonte jamais au parent.

```python
# app/core/graph/state.py
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
    rewrite_count: int         # borne structurelle de la boucle REWRITE
    hitl_decision: dict        # décision humaine réinjectée par Command(resume=...)
    safe_output: str

class Obs(TypedDict, total=False):
    agents_invoked: Annotated[list, operator.add]
    metrics: Annotated[dict, merge_domain]
    errors: Annotated[list, operator.add]

class RetailState(TypedDict, total=False):
    meta:      CycleMeta
    sales:     Annotated[SalesOutput,     merge_domain]   # écrit UNIQUEMENT par subgraph sales
    inventory: Annotated[InventoryOutput, merge_domain]   # écrit UNIQUEMENT par subgraph inventory
    knowledge: Annotated[KnowledgeOutput, merge_domain]   # écrit par le nœud RAG
    context:   Annotated[ContextOutput,   merge_domain]   # écrit par le nœud Sentinel
    decision:  CoachOutput                                # écrit par le coach
    control:   ControlState                               # écrit par guardrail / hitl_gate
    obs:       Annotated[Obs, merge_domain]               # écrit par tous (reducers)
```

**Règles d'état** :

1. Chaque nœud retourne uniquement son **delta** dans son canal — jamais l'état complet.
2. Les canaux de domaine ont un reducer (`merge_domain`) : les branches parallèles fusionnent
   sans écrasement, structurellement.
3. Les subgraphs déclarent des schémas **`input`/`output` distincts** : leur state interne
   (prompts intermédiaires, JSON partiels, messages ReAct) reste privé.
4. Toute donnée destinée au frontend est lue depuis un canal nommé — pas de champ « flottant ».

---

## 4. Le nœud `supervisor` : routeur d'intention

Premier nœud du graphe. Il classifie l'intention (déterministe — mots-clés + heuristiques,
LLM en repli pour les cas ambigus) et construit un **plan dynamique** : seules les branches
utiles sont activées.

```python
async def supervisor(state: RetailState) -> Command:
    intent = classify_intent(state["meta"])
    branches = {
        "greeting":  [],                             # réponse directe, zéro branche
        "off_topic": [],
        "stock":     [Send("inventory", state)],
        "script":    [Send("knowledge", state)],
        "produit":   [Send("sales", state), Send("inventory", state), Send("knowledge", state)],
        "objectif":  [Send("sales", state), Send("context", state)],
    }.get(intent, [Send("sales", state), Send("inventory", state),
                   Send("knowledge", state), Send("context", state)])   # cycle complet par défaut
    if not branches:
        return Command(goto="deliver",
                       update={"decision": {"message_advisor": direct_reply(state)},
                               "meta": {**state["meta"], "intent": intent}})
    return Command(goto=branches, update={"meta": {**state["meta"], "intent": intent}})
```

- **Entrées** : `meta` (message, déclencheur, magasin, conseiller).
- **Sorties** : `meta.intent` + le plan (`Command(goto=[Send(...)])`).
- Un cycle déclenché par l'AlertBus (`stock_event`, `sales_event`) suit le plan complet ;
  une salutation reçoit une réponse immédiate sans aucun appel de branche ni LLM.
- Le cache Redis des réponses récentes est consulté ici : hit → `deliver` direct.

---

## 5. Les branches de collecte (fan-out parallèle)

Les quatre branches s'exécutent en parallèle dans le même superstep LangGraph. Chacune est
isolée : un échec écrit `obs.errors` et laisse son canal vide — les autres branches et la
suite du graphe continuent.

### 5.1 Subgraph `sales` — Analyste → Stratège

Monté comme subgraph compilé (`add_node("sales", build_sales_subgraph(services))`).

```
receive → ts_analysis → memory_compare → strategy_context → generate_strategy → self_critique → emit
```

**Agent Analyste** (nœuds `ts_analysis`, `memory_compare`) :
- *Rôle* : diagnostic chiffré et prévisions — entièrement déterministe.
- *Fonctionnement* : Holt-Winters saisonnier (période 7) sélectionné par grid-search + backtest
  WAPE sur 28 jours ; projection fin de journée par profil horaire du même jour de semaine ;
  anomalies par z-score horaire ; comparaison aux cycles précédents lus dans le **store**
  (`("store", store_id, "cycles")`). Replis : régression linéaire → agrégation SQL.
- *Entrées* : `meta.store_id`, heure courante, historique de ventes (PostgreSQL).
- *Sorties* (canal `sales`) : `gap_pct`, `forecast_eod`, `attainment`, `urgency_level`,
  `hourly_gaps`, `next_hours_forecast`, `trend_signal`, `feasibility`, `analyst_summary`.

**Agent Stratège** (nœuds `strategy_context`, `generate_strategy`, `self_critique`) :
- *Rôle* : plan d'action commercial priorisé, cross-domaine.
- *Fonctionnement* : consomme le diagnostic Analyste + les canaux `context` et `inventory`
  (déjà fusionnés si disponibles, sinon lecture directe des services) ; génération LLM
  (cascade Mistral → OpenRouter) avec prompt cross-domaine ; **auto-critique Reflexion**
  (relecture, estimation d'impact, réordonnancement) ; repli par règles métier si le LLM
  échoue — jamais de sortie vide.
- *Entrées* : sorties Analyste, stock critique, événements datés (en cours / à venir), offres.
- *Sorties* (canal `sales`) : `strategie`, `strategie_actions`, `focus_produits`,
  `cause_racine`, `critique_score`, `context_heatmap`.

### 5.2 Subgraph `inventory` — map-reduce natif par SKU

Le parallélisme par SKU utilise `Send()` (plus aucun `ThreadPoolExecutor`) : il est donc
checkpointé, streamé et tracé comme le reste du graphe.

```python
class InvState(TypedDict, total=False):
    meta: CycleMeta
    skus: list
    per_sku: Annotated[list, operator.add]                # reducer = collecte map-reduce
    inventory: Annotated[InventoryOutput, merge_domain]   # sortie remontée au parent

def fan_out_skus(state: InvState):
    return [Send("analyze_sku", {"meta": state["meta"], "sku": s}) for s in state["skus"]]

async def analyze_sku(item) -> dict:
    analysis = await analysis_agent(item)                 # fetch → compute → reason
    context  = await context_agent(item)                  # uplift demande (absent → 0)
    decision = await decision_agent(analysis, context)    # constraints_check → decide
    return {"per_sku": [decision]}

def build_inventory_subgraph(services):
    g = StateGraph(InvState, input=RetailState, output=RetailState)
    g.add_node("load_skus", load_skus_node)               # couche service (jamais une route API)
    g.add_node("analyze_sku", analyze_sku)
    g.add_node("reduce", reduce_decisions)
    g.add_edge(START, "load_skus")
    g.add_conditional_edges("load_skus", fan_out_skus, ["analyze_sku"])
    g.add_edge("analyze_sku", "reduce")
    g.add_edge("reduce", END)
    return g.compile()
```

**Agent Analysis** — `fetch → compute → reason` :
- *Rôle* : santé du stock par SKU. `compute` est déterministe : couverture en jours, vitesse
  d'écoulement, point de commande, EOQ, classement de risque. `reason` = enrichissement LLM
  optionnel avec repli par règles.
- *Entrées* : sku, store_id, stocks, ventes récentes, seuils. *Sorties* : `analysis_report`.

**Agent Context** — `fetch_signals → interpret` :
- *Rôle* : anticiper la demande. Collecte prévisions (Holt-Winters/TimesFM), événements datés,
  promotions, tendances, chaîne fournisseur (`supplier_products`) ; transforme chaque signal en
  **facteur de demande pondéré** (`demand_uplift_pct`) par produit.
- *Entrées* : prévisions, événements, offres. *Sorties* : `context_report` (uplift).
- *Dégradation* : rapport absent → uplift = 0, jamais une erreur.

**Agent Decision** — `constraints_check → decide` :
- *Rôle* : décision d'approvisionnement explicable par SKU.
- *Fonctionnement* : `constraints_check` d'abord (contrainte dure → décision directe,
  **court-circuit du LLM**) ; sinon `decide` combine métriques ajustées par l'uplift + arbitrage
  LLM (tier smart) selon l'objectif métier (disponibilité vs coût).
- *Sorties* : `{action: ORDER|HOLD|MONITOR|EXPEDITE, order_qty, rationale, supplier}` ; si
  `ORDER` → **PO au statut « SUGGÉRÉ »** sur le Kanban (WS temps réel). L'agent ne commande
  jamais : l'approbation humaine est obligatoire, la réception (`REÇU`) met à jour le stock
  et ferme la boucle.

Le nœud `reduce` agrège dans le canal `inventory` : `decisions`, `critical_alerts`
(risque critical/high), `suggested_pos`.

### 5.3 Nœud `knowledge` — RAG unique et caché

- **Un seul retrieval par cycle** : recherche sémantique Milvus (200+ scripts de vente
  vectorisés), repli corpus local si Milvus indisponible.
- `cache_policy=CachePolicy(ttl=300)` : les cycles rapprochés sur le même magasin réutilisent
  le résultat.
- *Sorties* (canal `knowledge`) : `scripts`, `query`, `source`. Le Stratège et le Coach
  **consomment ce canal** — aucun ne refait sa propre recherche.

### 5.4 Nœud `context` — Sentinel

- Collecte météo, **événements datés séparés en cours / à venir** (scraper + base locale),
  offres actives, signaux QoS. `CachePolicy(ttl=600)`.
- *Sorties* (canal `context`) : `events_current`, `events_upcoming`, `active_offers`,
  `weather`, `signals`.

---

## 6. Le subgraph `coach` — synthèse cross-domaine

Point de convergence (fan-in par reducers). Le coach est le seul agent qui parle à l'utilisateur.

```
assemble_context → product_intelligence → generate → emit
```

- **`assemble_context`** : lit les quatre canaux (`sales`, `inventory`, `knowledge`, `context`)
  + le profil conseiller depuis le **store** (`("advisor", advisor_id)`) + **`control.guardrail_feedback`
  si `rewrite_count > 0`** — la re-passe produit réellement une réponse différente.
- **`product_intelligence`** (déterministe) : exclusion des produits en rupture (canal
  `inventory.snapshot`), substituts pour les ruptures demandées, **scoring pondéré à 6
  critères** (ventes, stock, contexte, marge, profil conseiller, urgence) → `scored_products`.
- **`generate`** : cascade LLM (Mistral → OpenRouter → Groq → Ollama), validation de la
  réponse, repli par règles selon l'intention si aucun fournisseur ne répond. En mode chat,
  les tokens sont streamés (`stream_mode="messages"`).
- *Sorties* (canal `decision`) : `recommendation` (`{priority, product_to_push,
  product_to_avoid, message_for_advisor, business_justification, confidence}`),
  `scored_products`, `message_advisor`.

**Relation cross-domaine matérialisée** : `product_to_avoid` provient de
`inventory.critical_alerts` — on ne recommande jamais un produit en risque de rupture.

---

## 7. Le nœud `guardrail` — contrôle fail-closed

Les **7 règles déterministes** (sans LLM) sont conservées :

| Règle | Contrôle | Sévérité |
|---|---|---|
| G1 | produit recommandé à stock zéro | BLOCK |
| G2 | rupture imminente (< 3 jours, configurable) | REWRITE |
| G3 | pas de source RAG + confiance < 0,7 | REWRITE |
| G4 | motif d'offre non autorisée | BLOCK |
| G5 | 5G/Fibre sans vérification d'éligibilité | REWRITE |
| G6 | confiance < 0,65 | ESCALATE |
| G7 | coût de commande > plafond budget | ESCALATE |

```python
async def guardrail(state: RetailState) -> Command:
    try:
        verdict, issues, feedback = run_seven_rules(state)
    except Exception as e:
        # FAIL-CLOSED : une panne du contrôleur ESCALADE, elle n'approuve jamais
        verdict, issues, feedback = "ESCALATE", [{"rule": "G0", "error": str(e)}], ""
    count = state.get("control", {}).get("rewrite_count", 0)
    update = {"control": {"guardrail_verdict": verdict, "guardrail_issues": issues,
                          "guardrail_feedback": feedback, "rewrite_count": count}}
    if verdict == "REWRITE" and count < MAX_REWRITES:          # MAX_REWRITES = 2
        update["control"]["rewrite_count"] = count + 1
        return Command(goto="coach", update=update)
    if verdict == "REWRITE":
        verdict = "ESCALATE"                                    # borne atteinte → humain
    if verdict == "ESCALATE":
        return Command(goto="hitl_gate", update=update)
    if verdict == "BLOCK":
        return Command(goto="safe_output", update=update)
    return Command(goto="deliver", update=update)
```

- `retry_policy=RetryPolicy(max_attempts=2)` sur le nœud : une erreur transitoire est
  retentée avant d'escalader.
- Le verdict est co-localisé avec le routage (`Command`) : une seule fonction décide et route.
- Chaque évaluation émet un événement WS (badge chat + panel monitoring), inchangé.

---

## 8. Le nœud `hitl_gate` — Human-in-the-Loop bloquant

Le graphe **se met réellement en pause** : l'état est checkpointé, la décision humaine reprend
l'exécution exactement où elle s'était arrêtée, et cette décision vit **dans l'état du cycle**.

```python
async def hitl_gate(state: RetailState) -> Command:
    decision = interrupt({                                   # ⏸ checkpoint + pause
        "cycle_id":  state["meta"]["cycle_id"],
        "strategie": state["sales"].get("strategie", ""),
        "actions":   state["sales"].get("strategie_actions", [])[:3],
        "issues":    state["control"]["guardrail_issues"],
        "reco":      state["decision"].get("recommendation", {}),
    })
    # … reprise ici : decision = payload envoyé par l'humain
    if decision.get("approved"):
        return Command(goto="deliver", update={"control": {"hitl_decision": decision}})
    return Command(goto="safe_output", update={"control": {"hitl_decision": decision}})
```

**API de reprise** :

```python
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
    result = await graph.ainvoke(Command(resume=decision.model_dump()), config)
    return {"status": "completed", "state": public_view(result)}
```

Le panneau HITL Angular lit le payload d'interrupt (poussé en WS) et appelle `/resolve` avec
`{approved, modified_qty, comment}`. La boucle de feedback devient une lecture de
`get_state_history()` : chaque décision humaine est un checkpoint horodaté du cycle concerné.

**Même mécanisme pour le Kanban** : l'approbation d'un PO « SUGGÉRÉ » est une transition
protégée — les outils (y compris MCP `move_purchase_order`) ne peuvent pas contourner la porte.

---

## 9. Sortie : `safe_output`, `deliver`, `persist`

- **`safe_output`** : construit la réponse de repli sûre (`control.safe_output`, confiance 0,
  priorité LOW). Un BLOCK produit **toujours** une réponse diffusée.
- **`deliver`** : diffusion temps réel — WS `/ws/store/{id}` (widgets analyste/stratège,
  recommandation, badge guardrail, `scored_products`), WS `/ws/advisor/{id}` (coaching
  personnalisé), bus WS Kanban (PO). En mode chat, les tokens sont déjà partis en SSE pendant
  `generate` ; `deliver` envoie l'événement de fin + métadonnées.
- **`persist`** : écrit le résumé de cycle et le feedback dans le **store**
  (namespaces ci-dessous) via le pool PostgreSQL partagé injecté — aucun pool créé par cycle.

---

## 10. Persistance : checkpointer et store

### 10.1 Cycle de vie (lifespan FastAPI)

```python
async with AsyncPostgresSaver.from_conn_string(DB_URL) as checkpointer, \
           AsyncPostgresStore.from_conn_string(DB_URL) as store:
    app.state.graph = build_graph(services).compile(
        checkpointer=checkpointer,     # threads, reprise, interrupt, time-travel
        store=store,                   # mémoire long-terme cross-thread
    )
    yield
```

`services` est une dataclass injectée à la construction (pool PG, client Milvus, fabrique LLM,
bus WS, config) — aucun nœud n'importe le module d'entrée ni n'appelle une route API.

### 10.2 Checkpointer (`thread_id = cycle_id`)

| Capacité | Usage |
|---|---|
| Persistance par superstep | reprise après crash au milieu d'un cycle |
| `interrupt` / `resume` | HITL bloquant (§8) |
| `get_state_history()` | audit complet, time-travel debug, replay |
| Threads de chat | `thread_id = chat-{advisor_id}-{date}` → historique conversationnel natif |

### 10.3 Store (mémoire long terme, namespacée)

| Namespace | Contenu | Écrit par | Lu par |
|---|---|---|---|
| `("advisor", advisor_id)` | profil, historique coaching, préférences | persist | coach |
| `("store", store_id, "feedback")` | notes « utile / pas utile », décisions HITL | persist | supervisor, stratège |
| `("store", store_id, "cycles")` | résumés de cycles | persist | analyste (comparaison mémoire) |

---

## 11. Streaming natif — un seul chemin pour le chat

Le chat n'est pas un système parallèle : `/chat` et `/stream` invoquent **le même graphe**
avec `trigger_type="advisor_message"`, et le SSE utilise le streaming LangGraph :

```python
@router.post("/stream")
async def stream(body: ChatBody):
    config = {"configurable": {"thread_id": f"chat-{body.advisor_id}-{today()}"}}
    async def sse():
        async for mode, chunk in graph.astream(
            initial_state(body), config,
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":                  # tokens LLM du coach → SSE
                yield sse_token(chunk)
            elif mode == "updates":                 # deltas de nœuds → widgets WS
                await ws_bus.push(body.store_id, chunk)
        yield sse_done()
    return StreamingResponse(sse(), media_type="text/event-stream")
```

Un seul run exécute, contrôle (guardrail), trace et streame. Les chemins rapides (salutation,
hors-sujet, cache Redis) sont gérés par le routeur d'intention (§4) et répondent sans LLM.

---

## 12. Flux principaux

### 12.1 Cycle événementiel (autonome)

```
AlertBus Redis (stock_event / sales_event, seuil franchi)
  → graph.ainvoke({meta: {trigger_type, store_id}}, thread_id=cycle_id)
  → supervisor (plan complet) → 4 branches ∥ → coach → guardrail
  → APPROVE → deliver (widgets WS mis à jour sans action utilisateur) → persist
  → feedback humain éventuel (« utile / pas utile ») → store → cycles suivants
```

### 12.2 Chat conseiller

```
Message → supervisor (intention + cache) → branches utiles seulement
  → coach (tokens SSE au fil de la génération) → guardrail (badge WS)
  → deliver → persist (thread de chat checkpointé = historique natif)
```

### 12.3 Boucle d'approvisionnement fermée

```
inventory subgraph : Send() par SKU → Decision → PO « SUGGÉRÉ » (Kanban WS)
  → approbation humaine (porte HITL, infranchissable par les outils/MCP)
  → EN ATTENTE → EN LIVRAISON → REÇU → mise à jour stock
  → le stock corrigé alimente le cycle d'analyse suivant  (boucle fermée)
  → refus → décision persistée dans le store → amélioration continue
```

### 12.4 Cycle avec escalade humaine

```
guardrail: ESCALATE → hitl_gate: interrupt(payload) ⏸
  → payload poussé en WS → panneau HITL Angular
  → POST /hitl/{cycle_id}/resolve → Command(resume={approved, ...})
  → le graphe reprend au point exact de pause
  → approuvé → deliver | refusé → safe_output → deliver
```

---

## 13. Récapitulatif des agents

| Agent | Emplacement dans le graphe | Déterministe / LLM | Entrées | Sorties (canal) |
|---|---|---|---|---|
| **Supervisor (routeur)** | nœud d'entrée | déterministe (LLM en repli) | `meta` | plan `Send()`, `meta.intent` |
| **Analyste** | subgraph `sales` | 100 % déterministe (TS) | historique ventes, heure | `sales.*` (gap, forecast, anomalies) |
| **Stratège** | subgraph `sales` | LLM + auto-critique, repli règles | analyste, contexte, stock critique | `sales.*` (stratégie, actions) |
| **Analysis** | subgraph `inventory` (par SKU) | déterministe, LLM optionnel | stocks, ventes, seuils | `analysis_report` |
| **Context** | subgraph `inventory` (par SKU) | signaux + LLM optionnel | prévisions, événements, promos | `context_report` (uplift) |
| **Decision** | subgraph `inventory` (par SKU) | règles d'abord, LLM arbitre | analysis + context + contraintes | `inventory.*`, PO « SUGGÉRÉ » |
| **Knowledge (RAG)** | nœud caché (TTL 5 min) | retrieval | requête, magasin | `knowledge.scripts` |
| **Sentinel** | nœud caché (TTL 10 min) | collecte | store_id | `context.*` |
| **Coach** | subgraph après fan-in | scoring déterministe + LLM cascade | les 4 canaux + store + feedback guardrail | `decision.*` |
| **Guardrail** | nœud fail-closed | 100 % déterministe (7 règles) | `decision`, `inventory.snapshot` | `control.*` + routage |
| **Hitl_gate** | nœud `interrupt()` | humain | payload de contrôle | `control.hitl_decision` |

---

## 14. Notions LangGraph mobilisées

| Notion | Rôle dans l'architecture |
|---|---|
| `StateGraph` + canaux typés | contrat d'état unique, ownership structurel par domaine |
| Reducers (`Annotated[..., fn]`) | fusion sans écrasement des écritures parallèles |
| Subgraphs compilés (`add_node(name, graph)`) | Sales, Inventory, Coach — trace, stream et checkpoint traversants |
| Schémas `input`/`output` | state privé des subgraphs invisible du parent |
| `Send()` | fan-out dynamique : branches par intention, map-reduce par SKU |
| `Command(goto, update)` | routage + mise à jour d'état co-localisés (supervisor, guardrail, hitl) |
| `Command(resume)` / `interrupt()` | HITL bloquant réel avec reprise au point exact |
| Checkpointer (`AsyncPostgresSaver`) | persistance par `thread_id`, reprise, time-travel, audit |
| Store (`AsyncPostgresStore`) | mémoire long terme namespacée (profils, feedback, cycles) |
| `RetryPolicy` | robustesse par nœud (guardrail, appels externes) |
| `CachePolicy` | déduplication (RAG, contexte) — un retrieval par fenêtre |
| `astream(stream_mode=["messages","updates"])` | SSE tokens + updates widgets depuis le même run |
| `recursion_limit` | filet de sécurité global (en plus de `rewrite_count`) |

---

## 15. Invariants du système

1. **Aucune commande (PO) exécutée sans approbation humaine** — la porte HITL est
   infranchissable, y compris par les outils MCP.
2. **Les chiffres sont 100 % déterministes** — prévisions, métriques stock, scoring, règles
   guardrail ; le LLM formule et arbitre, il ne calcule jamais et ne bloque jamais le flux.
3. **Toute réponse visible passe par le guardrail fail-closed** — une panne du contrôleur
   escalade vers l'humain, elle n'approuve jamais.
4. **Un BLOCK produit toujours une réponse de repli diffusée** — jamais d'absence de réponse.
5. **Dégradation contrôlée partout** — Milvus → corpus local ; Holt-Winters → linéaire → SQL ;
   Mistral → OpenRouter → Groq → Ollama ; LLM → règles métier.
6. **Chaque cycle est un thread checkpointé** — reprise, audit et time-travel natifs.
7. **Une branche qui échoue n'arrête ni les autres ni le graphe** — erreur dans `obs.errors`,
   canal vide traité comme absent par les nœuds suivants.

---

*Documents liés : `docs/ARCHITECTURE_CIBLE.md` (critique de l'existant + plan de migration),
`docs/ARCHITECTURE_COMPLETE.md` (architecture actuelle),
`docs/architecture_multi_agents.svg|png` (diagramme de l'existant).*
