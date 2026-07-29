# Architecture Multi-Agents — Système Ooredoo (Sales + Inventory)

> Document **centré sur la partie multi-agents** : rôle de chaque agent, pattern, graphe interne, outils, entrées/sorties, et surtout **comment les agents communiquent et s'orchestrent**.
> Dérivé uniquement du code source. Diagramme associé : [`ARCHITECTURE_MULTI_AGENT.drawio`](./ARCHITECTURE_MULTI_AGENT.drawio).

---

## 1. Le roster d'agents

Le système compte **8 agents LangGraph** répartis en 2 domaines, plus un **méta-orchestrateur** qui les coordonne.

| # | Agent | Domaine | Pattern | Rôle | LLM ? |
|---|---|---|---|---|---|
| 1 | **Analyste** | Sales | Pipeline déterministe | Prévision CA/EOD, gap, urgence | ❌ (0 LLM, < 1 s) |
| 2 | **Stratège** | Sales | ReAct + **Reflexion** (self-critique) | Cause racine + actions commerciales | ✅ |
| 3 | **Coach** | Sales | Persona + RAG + scoring | Conseil conversationnel au vendeur | ✅ |
| 4 | **Guardrail** | Sales | Règles (7 checks) | Valide/route la reco du Coach | ❌ (déterministe) |
| 5 | **Analysis** | Inventory | fetch→compute→reason | Métriques stock (EOQ, reorder, risque) | ✅ (évaluateur) |
| 6 | **Context** | Inventory | fetch→interpret | Uplift de demande (events/promos) | ✅ |
| 7 | **Decision** | Inventory | constraints→decide | ORDER/HOLD/MONITOR/EXPEDITE | ✅ |
| 8 | **Coach cross-domaine** | Fusion | scoring 6 critères | Fusionne Sales×Inventory→reco produit | ❌ (formule) |
| — | **SupervisorAgent** | Méta | LangGraph fan-out/fan-in | Orchestre les 4 branches + coach + guardrail + HITL | — |

**Principe commun** : chaque agent est un `StateGraph` LangGraph **compilé une seule fois par process** (singleton) car un graphe compilé est *stateless* — l'état circule via `invoke()`, rien n'est stocké sur l'objet graphe.

---

## 2. La communication inter-agents : le `RetailState` partagé

Tous les agents lisent/écrivent **un unique `TypedDict` partagé** (`app/sales/core/retail_state.py`). C'est le **canal de communication central** — il n'y a pas d'appels directs agent→agent, tout passe par l'état.

### Règle d'or : chaque node ne retourne que SON delta

```python
# ✅ correct — le node retourne uniquement ce qu'il produit
return {"urgency_level": "HIGH", "gap_pct": 32.0}

# ❌ interdit — retourner l'état complet duplique les canaux à reducer
return {**state, ...}
```

### Trois canaux à *reducer* (écrits par plusieurs branches en parallèle)

```python
agents_invoked : Annotated[List[str], operator.add]   # concaténation
metrics        : Annotated[Dict, _merge_dict]          # fusion de dicts
errors         : Annotated[List[str], operator.add]
```

Sans ces reducers, LangGraph lève `InvalidUpdateError` (« can receive only one value per step ») car les 4 branches parallèles écrivent dans le même superstep.

### Découpage des champs par « propriétaire »

Pour éviter les conflits d'écriture parallèle, chaque branche a une **whitelist de champs** qu'elle seule écrit :

| Propriétaire | Champs |
|---|---|
| **sales_branch** | `_SALES_BRANCH_KEYS` : gap_pct, urgency_*, analyst_summary, strategie_*, focus_produits, hourly_gaps, ts_analysis… |
| **knowledge_branch** | `rag_context`, `retrieved_scripts` |
| **context_branch** | `external_context`, `context_report` |
| **inventory_branch** | `inventory_decisions`, `critical_stock_alerts` |
| **coach_agent** | `coach_recommendation`, `scored_products` |
| **guardrail_agent** | `guardrail_status`, `guardrail_issues`, `guardrail_feedback`, `requires_human_validation` |

> `sales_branch` prend une **whitelist explicite** (jamais `**result`) car le résultat complet du CycleOrchestrator contient `external_context` — écrit par `context_branch` dans le même superstep → conflit.

---

## 3. Le SupervisorAgent — orchestration du multi-agents

`app/sales/orchestration/supervisor_agent.py` — c'est le graphe qui **fait travailler tous les agents ensemble**.

```
                     ┌─────────────── FAN-OUT (parallèle) ───────────────┐
                     │                                                   │
START → initialize ──┼─→ sales_branch      (Analyste → Stratège)         │
                     │─→ knowledge_branch  (RAG Milvus)                  ├─→ merge_outputs
                     │─→ context_branch    (météo/fériés/events)         │
                     └─→ inventory_branch  (Analysis∥Context→Decision)   │
                                                                         │
   merge_outputs → coach_agent → guardrail_agent ──┐                    (fan-in)
                                                    │ route selon statut
        ┌───────────────────────────────────────────┼──────────────────┐
        ▼ approve         ▼ rewrite (1 boucle)      ▼ escalate          ▼ block
   notify_frontend    coach_agent (retour)     human_validation    safe_fallback
        │                                            │ (HITL)            │
        └──────────────────┬─────────────────────────┴───────────────────┘
                           ▼
                      save_memory → END
```

**Ce que fait chaque node du Superviseur :**

- `initialize_state` — génère `cycle_id` (retourne juste ce delta)
- `sales_branch` — **réutilise le `CycleOrchestrator` singleton** (main.py) → Analyste puis Stratège séquentiels
- `knowledge_branch` — appelle `node_rag_search` du Stratège → scripts Milvus
- `context_branch` — appelle `fetch_full_context` → météo Open-Meteo, fériés, events
- `inventory_branch` — appelle `analyze_store(fast=True)` → décisions stock déterministes
- `merge_outputs` — point de fan-in (fusion des 4 deltas dans `RetailState`)
- `coach_agent` — **scoring cross-domaine** (voir §5) → `coach_recommendation` + `scored_products`
- `guardrail_agent` — **7 règles** (voir §6) → statut routant
- `notify_frontend` / `human_validation` / `safe_fallback` — sorties selon statut
- `save_memory` — persiste dans `coach_interactions` (nourrit le RAG futur)

**Parallèle vs séquentiel** — le choix est piloté par les **dépendances de données réelles** :
- Les 4 branches sont **parallèles** (indépendantes).
- **À l'intérieur** de sales_branch, Analyste→Stratège est **séquentiel** car le Stratège consomme la `rag_query` construite par l'Analyste.
- Analysis ∥ Context sont **parallèles** (aucun ne dépend de l'autre), puis Decision les fusionne.

---

## 4. Chaque agent en détail

### 4.1 Agent Analyste (Sales) — `analyst/`

**Graphe (7 nodes, déterministe) :**
```
receive_pos → validate_data → load_memory → ts_analyst
            → compare_with_memory → build_strategy_query → save_memory → END
```
- **Cœur = `ts_analyst`** (`ts_engine.py`) : moteur de séries temporelles pur (0 LLM, < 1 s)
  1. Holt-Winters saisonnier (période 7) + backtest rolling-origin → MAPE réel
  2. Profil intraday par jour de semaine
  3. Prévision EOD hybride (modèle + déroulé intraday pondéré)
  4. Gap horaire (attendu vs réel, z-score, OK/WATCH/ALERT)
  5. Prévision h+1..h+3
  6. Urgence composite + faisabilité (ACHIEVED→CLOSED)
- **`build_strategy_query`** construit la requête que le Stratège consommera → **dépendance qui impose le séquencement**
- **Écrit** : `forecast_eod`, `gap_pct`, `urgency_level`, `hourly_gaps`, `next_hours_forecast`, `analyst_summary`, `feasibility`, `trend_signal`

### 4.2 Agent Stratège (Sales) — `stratege/` — pattern Reflexion

**Graphe (6 nodes) :**
```
fetch_context → rag_search → analyze_context → generate_strategy → build_output → self_critique → END
```
- `fetch_context` — météo réelle, fériés, events/festivals (`market.events`), promos
- `rag_search` — Milvus → scripts de vente similaires
- `generate_strategy` — **LLM** → `strategie_actions` (priorité, produit cible, argument)
- **`self_critique`** — auto-évalue cohérence/complétude (pattern **Reflexion** : l'agent se relit) → `critique_score`, `critique_passed`
- **Écrit** : `strategie`, `strategie_actions`, `cause_racine`, `focus_produits`, `message_manager`, `context_heatmap`, `rag_used`, `nb_rag_scripts`

### 4.3 Agent Coach (Sales) — `coach_chat.py` — conversationnel RAG

Le plus riche (~2500 lignes). Pipeline pour chaque message conseiller :
```
message → _classify_intent → loaders contexte (∥) → RAG cross-domaine
        → Stratège câblé → substituts anti-rupture → LLM (retry 4 niveaux) → réponse
```
1. **Intent** : greeting / off_topic / recap / inventory / cross_domain / coaching / conversation
2. **Loaders parallèles** : POS, ventes détail, contexte inventaire (alertes, top-sellers, recos agent, PO en cours, décisions humaines), profil conseiller
3. **RAG unifié cross-domaine** : scripts `[S*]`, playbooks `[I*]`, fiches produit `[P*]`, décisions `[D*]` — Milvus + fallback lexical (jamais à vide)
4. **Stratège câblé serveur-side** (`_get_stratege_for_chat`) : orchestrateur résilient, cache 30 min, timeout borné, warm en tâche de fond
5. **Substituts anti-hallucination** : produit nommé en rupture → substituts réellement en stock (SQL gamme/quartile de prix + scoring agents)
6. **Retry LLM 4 niveaux** : Mistral → OpenRouter → OpenRouter stripped → Ollama → fallback intent

**Hiérarchie de sources** imposée par le prompt (la plus haute gagne) :
`agents vivants > SITUATION (chiffres) > fiches [P*] > scripts [S*]/playbooks [I*]/décisions [D*]`

### 4.4 Coach cross-domaine (node du Superviseur) — `cross_domain_tools.py`

Distinct du chat : c'est la **fusion Sales×Inventory** appelée dans `node_coach_agent`. Elle :
- `get_sales_context` (CA, gap réel), `get_recommendable_products` (stock > 0), `retrieve_advisor_history` (profil conseiller) — en parallèle
- puis `rank_products` → applique la **formule de scoring** (§5) → top-3
- construit `coach_recommendation` (product_to_push, product_to_avoid, message, confidence)

### 4.5 Agent Guardrail (Sales) — `guardrail_agent.py`

Valide **toute** reco du Coach avant le frontend. 7 règles (§6) → statut **APPROVE / REWRITE / ESCALATE / BLOCK**.

### 4.6 Chaîne Inventory (3 agents) — `InventoryOrchestrator` batch parallèle (8 workers)

```
Par SKU :   [Analysis ∥ Context]   →   Decision
```
- **Analysis** (`fetch→compute→reason`) : EOQ, point de commande, safety stock, jours de couverture, risque. LLM = **évaluateur de conflits cross-dimensionnels**, pas narrateur. → `analysis_report`
- **Context** (`fetch_signals→interpret`) : apprend les uplifts historiques d'events/promos → `demand_uplift_pct` (7 j). → `context_report`
- **Decision** (`constraints_check→decide`) : fusionne les deux (uplift appliqué aux métriques), produit l'action. Persiste dans `inventory.recommendations` **et auto-suggère un PO** (statut SUGGERE) poussé au Kanban WS. Route conditionnelle : si `constraints_check` bloque déjà → END, sinon → `decide`.

---

## 5. La formule de fusion cross-domaine (le cœur du scoring)

`score_product()` — combine 6 critères pondérés (`cross_domain_tools.py` §S5.2) :

```
final_score = 0.30 · sales_gap_alignment   (le prix comble-t-il le gap CA ?)
            + 0.20 · stock_health           (stock sain / risque rupture ?)
            + 0.15 · margin_score           (marge normalisée 0–50 %)
            + 0.15 · promotion_priority     (promo active > top-seller > normal)
            + 0.10 · advisor_fit            (perf historique du conseiller sur la catégorie)
            + 0.10 · customer_fit           (top-seller / premium convertit ?)
```

Chaque critère ∈ [0,1], chacun calculé dynamiquement depuis la DB (aucune valeur codée en dur). La sortie inclut `components` (score par critère → **explicabilité**) et `recommendation_reason` (justification lisible). `rank_products` trie et renvoie le top-N.

---

## 6. Les 7 garde-fous (Guardrail) et le routage

| Règle | Contrôle | Sévérité si violée |
|---|---|---|
| **G1** Stock | Ne jamais recommander un produit à stock 0 | **BLOCK** |
| **G2** Rupture | Éviter un produit en rupture imminente (< 3 j) sauf déstockage | REWRITE |
| **G3** RAG | Arguments commerciaux doivent venir d'une source fiable (si confiance basse + pas de RAG) | REWRITE |
| **G4** Business | Pas de remise/offre non autorisée (patterns interdits) | **BLOCK** |
| **G5** Network | Pas de 5G/Fibre sans vérif d'éligibilité | REWRITE |
| **G6** Confidence | Si confiance < 0.65 → relire ou escalader | ESCALATE |
| **G7** Budget | Commande stock > 100 000 DT → validation manager | ESCALATE |

Le statut final = **la sévérité maximale** parmi les règles violées (`_compute_status`). Le routage LangGraph après guardrail :
- **APPROVE** → `notify_frontend` (envoyé tel quel)
- **REWRITE** → retour à `coach_agent` avec feedback (max **1 boucle**)
- **ESCALATE** → `human_validation` (HITL, file de validation manager)
- **BLOCK** → `safe_fallback` (message sûr, l'original n'est jamais envoyé)

---

## 7. Boucles de raisonnement (Reflexion & rewrite)

Le système a **deux niveaux d'auto-correction** :
1. **Intra-agent (Stratège)** : `self_critique` note sa propre stratégie (`critique_score`) — pattern Reflexion.
2. **Inter-agent (Guardrail→Coach)** : si REWRITE, le Coach est relancé **une fois** avec le `guardrail_feedback` précis (ex. « propose une alternative, stock imminent »).

Le `critique_score` du Stratège alimente d'ailleurs le G6 (confidence) du Guardrail — les deux boucles sont chaînées.

---

## 8. Qui déclenche le multi-agents ? (3 voies)

| Déclencheur | Fréquence | Cible | Ce qui tourne |
|---|---|---|---|
| **CronTrigger** | 15 min | top-N boutiques | cycle agent complet |
| **SaleEventTrigger** | événement vente (debounce ~20 s) | boutique active | **Étage 1** : Analyste seul (déterministe) ; **Étage 2** : cycle complet *seulement si* urgence↑ / gap ±5 pts / faisabilité↓ (garde-fou coût 180 s) |
| **AlertCycleTrigger** | rupture stock (Redis) | boutique concernée | cycle complet immédiat (debounce 180 s, URGENT/HIGH) |

Le `SupervisorAgent` est aussi appelé directement depuis la boucle WS dashboard (`_run_agents`) et l'endpoint `/api/v1/supervisor/run`.

---

## 9. Résumé du flux de communication

```
Trigger ──► SupervisorGraph.ainvoke(RetailState)
                │
                ├─ 4 branches ∥ écrivent leurs deltas ──► RetailState (reducers)
                │      (agents Sales + Inventory + RAG + Context)
                │
                ├─ coach_agent lit tout le state ──► scoring 6 critères ──► coach_recommendation
                │
                ├─ guardrail lit coach_recommendation ──► 7 règles ──► statut
                │       │
                │       ├─ APPROVE ──► WS coach_recommendation (frontend)
                │       ├─ REWRITE ──► retour coach (1x)
                │       ├─ ESCALATE ─► HITL (manager)
                │       └─ BLOCK ────► safe_fallback
                │
                └─ save_memory ──► coach_interactions (mémoire RAG future)
```

Aucun agent n'appelle un autre agent directement : **tout transite par le `RetailState`**, ce qui rend chaque agent testable et remplaçable isolément, et permet le parallélisme contrôlé par reducers.

---

## Annexe — Fichiers clés du multi-agents

```
app/sales/orchestration/
├── supervisor_agent.py      # LE méta-graphe (fan-out 4 branches + coach + guardrail + HITL)
├── graph.py                 # CycleOrchestrator (Analyste→Stratège→Coach séquentiel)
├── trigger.py               # CronTrigger 15 min
├── sale_trigger.py          # SaleEventTrigger 2 étages
└── alert_trigger.py         # AlertCycleTrigger (Redis)

app/sales/core/
└── retail_state.py          # RetailState partagé + reducers

app/sales/coaching/agents/
├── analyst/  (agent.py, ts_engine.py, nodes.py)      # Analyste déterministe
├── stratege/ (agent.py, nodes.py)                    # Stratège Reflexion
├── coach/    (coach_chat.py, cross_domain_tools.py)  # Coach chat + scoring fusion
└── guardrail/(guardrail_agent.py)                    # 7 règles

app/inventory/
├── services/orchestrator.py # InventoryOrchestrator (batch 8 workers)
└── agents/  (analysis/, context/, decision/)         # chaîne 3 agents
```
