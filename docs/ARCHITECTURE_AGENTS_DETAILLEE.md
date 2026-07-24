# Architecture détaillée des agents et de leur orchestration

> Ce document décrit, agent par agent, l'**architecture interne** de chaque agent (son graphe
> LangGraph, ses nœuds, son état, ses outils, ses replis), puis explique **comment les agents sont
> reliés entre eux et orchestrés** à trois niveaux : les graphes superviseurs, les orchestrateurs
> de service et les bus d'événements.
>
> Complément de `docs/ARCHITECTURE_COMPLETE.md` (vue d'ensemble de la solution).
> Date : 2026-07-17 — branche `refactor/monolith-v2`.

---

## Partie I — Architecture interne de chaque agent

Chaque agent du système est lui-même un **graphe LangGraph autonome** : une machine à états dont
les nœuds sont des fonctions Python et dont l'état circule de nœud en nœud. Un agent n'est donc
pas « un prompt » : c'est un pipeline structuré dans lequel l'appel au LLM n'est qu'un nœud parmi
d'autres, entouré de nœuds déterministes de collecte, de calcul, de validation et de persistance.

---

### 1. Agent Analyste (Sales)

*Fichiers : `app/sales/coaching/agents/analyst/agent.py` (graphe), `ts_engine.py` (moteur de
calcul), `ts_node.py` (nœud d'analyse), `prompts.py` (résumé LLM optionnel).*

**Mission** : produire le diagnostic chiffré des ventes du magasin — où en est le chiffre
d'affaires, comment finira la journée, quelles heures sont anormales.

**Architecture interne — graphe séquentiel de 7 nœuds :**

```
receive_pos → validate_data → load_memory → ts_analyst
    → compare_with_memory → build_strategy_query → save_memory → END
```

Le déroulement est le suivant :

1. **`receive_pos`** — réception des données du point de vente : chiffre d'affaires réalisé,
   objectif du jour, transactions récentes.
2. **`validate_data`** — validation des données d'entrée : cohérence des montants, présence des
   champs obligatoires. Un agent qui calcule sur des données invalides produit des chiffres faux
   avec assurance ; ce nœud coupe le problème à la racine.
3. **`load_memory`** — chargement de la mémoire de l'agent : les analyses des cycles précédents,
   pour permettre la comparaison temporelle.
4. **`ts_analyst`** — le cœur de l'agent (`node_ts_analysis`). Il appelle le moteur de séries
   temporelles `ts_engine.analyze_store()` qui : charge 120 jours d'historique ; ajuste un modèle
   Holt-Winters saisonnier (période 7 jours) sélectionné par recherche de paramètres + backtest
   WAPE sur 28 jours ; projette la fin de journée à partir du réalisé partiel et du profil horaire
   du même jour de semaine ; détecte les heures anormales (déviation + z-score) ; qualifie la
   tendance. Tout est déterministe — le LLM n'intervient qu'en option pour reformuler le résumé
   (`_maybe_enrich_summary`), avec repli linéaire (`_linear_fallback`) si le fit échoue.
5. **`compare_with_memory`** — comparaison avec les analyses passées : la situation s'améliore-t-elle
   ou se dégrade-t-elle par rapport au dernier cycle ?
6. **`build_strategy_query`** — construction de la « question » posée au Stratège : l'Analyste
   formule le problème (« gap de −12 % à 15 h, produit X en chute ») que le Stratège devra résoudre.
   C'est le **point de couplage explicite Analyste → Stratège**.
7. **`save_memory`** — persistance de l'analyse pour le cycle suivant.

**État** : `SalesAgentState` (TypedDict partagé par les agents sales).
**Place du LLM** : aucune, sur le chemin critique. Un résumé rédigé peut être activé via
`ANALYST_LLM_SUMMARY=1` (timeout 8 s) ; il reformule les chiffres sans jamais les recalculer, et
le résumé statistique reprend la main au moindre incident.

---

### 2. Agent Stratège (Sales)

*Fichiers : `app/sales/coaching/agents/stratege/agent.py` (graphe v3), `nodes.py`,
`events_scraper.py`.*

**Mission** : transformer le diagnostic en **plan d'action commercial priorisé**, en intégrant le
contexte marché réel (événements datés, offres, météo) et l'état des stocks.

**Architecture interne — graphe séquentiel de 6 nœuds avec auto-critique (pattern Reflexion) :**

```
fetch_context → rag_search → analyze_context → generate_strategy
    → build_output → self_critique → END
```

1. **`fetch_context`** — collecte : CA/objectif, météo, jours fériés, **événements datés**
   (scraper + base locale, séparés en « en cours » et « à venir »), offres actives, et **stock
   critique** (produits sous 5 unités) via les outils cross-domaine — c'est ce qui empêche le
   Stratège de recommander de pousser un produit en quasi-rupture.
2. **`rag_search`** — recherche Milvus de scripts de vente similaires à la situation courante.
3. **`analyze_context`** — analyse de cause racine : calcul du gap, du niveau d'urgence, des
   heures restantes, pondération des facteurs contextuels, génération d'alertes déterministes.
4. **`generate_strategy`** — l'appel LLM (Mistral primaire, OpenRouter secours) : le prompt
   combine diagnostic, facteurs, scripts RAG et stock, et demande des actions concrètes
   structurées en JSON.
5. **`build_output`** — formatage pour le frontend : StrateActions, carte de chaleur d'urgence,
   signaux de contexte.
6. **`self_critique`** — **auto-évaluation (Reflexion)** : le LLM relit ses propres actions, en
   évalue la cohérence et la complétude, estime l'impact de chacune et les réordonne ou en élimine.
   C'est une seconde passe de qualité intégrée au graphe, pas un post-traitement externe.

**Replis** : JSON partiel récupéré par `_extract_from_partial_json` ; en dernier recours,
`_make_fallback_strategy` construit une stratégie par règles métier sans LLM.
**État** : `SalesAgentState`. Le graphe est compilé une seule fois et mis en cache
(`get_stratege_agent`, singleton).

---

### 3. Agent Coach (Sales)

*Fichiers : `app/sales/coaching/agents/coach/agent.py` (graphe), `coach_chat.py` (endpoints chat),
`node_invoke_stratege.py`, `cross_domain_tools.py`.*

**Mission** : dialoguer avec le conseiller de vente et produire des conseils actionnables, sourcés
et contrôlés. Le Coach existe sous **deux formes** : un graphe LangGraph (utilisé dans les cycles
orchestrés) et un pipeline de chat optimisé pour la latence (endpoints `/chat` et `/stream`).

**Forme 1 — graphe LangGraph de 6 nœuds :**

```
load_context → rag_search → load_advisor_history → invoke_stratege_for_coach
    → generate_conseil → save_conseil → END
```

1. **`load_context`** — chargement du contexte magasin (ventes, stock, situation du jour).
2. **`rag_search`** — récupération des scripts de vente pertinents.
3. **`load_advisor_history`** — historique et profil du conseiller (personnalisation).
4. **`invoke_stratege_for_coach`** — **invocation de l'Agent Stratège comme sous-agent** : le
   Coach ne refait pas l'analyse stratégique, il appelle le Stratège via le
   `CoachStrategeOrchestrator` (voir Partie II) et intègre ses actions dans son propre contexte.
   C'est la relation d'orchestration la plus importante du module Sales : **le Stratège est un
   service du Coach**.
5. **`generate_conseil`** — génération LLM du conseil final.
6. **`save_conseil`** — persistance.

**Forme 2 — pipeline de chat (`coach_chat.py`)** : pour le chat interactif, la latence prime sur
la généralité du graphe. Le pipeline enchaîne : garde d'entrée (rate limiting, classification
d'intention, réponses immédiates pour salutations/hors-sujet, cache Redis) → collecte parallèle
(point de vente, ventes, inventaire, historique, profil conseiller, RAG) → intelligence produit
(produits hors stock exclus, substituts, scoring `scored_products`) → injection Stratège
serveur-side avec timeout borné → cascade LLM (Mistral → OpenRouter → Groq → Ollama) avec
validation de la réponse et repli par règles selon l'intention → Guardrail → diffusion
(SSE/WebSocket) et persistance.

**Outils cross-domaine** (`cross_domain_tools.py`) : c'est par ce module que le Coach franchit la
frontière du module Inventory — alertes de stock, statut des produits, urgences — sans dépendre
directement de son implémentation interne.

---

### 4. Agent Guardrail (transverse)

*Fichier : `app/sales/coaching/agents/guardrail/guardrail_agent.py`.*

**Mission** : dernier rempart avant l'utilisateur. Vérifie chaque réponse candidate.

**Architecture interne — pipeline de règles, sans graphe et sans LLM.** Le Guardrail est
volontairement l'agent le plus simple du système : une fonction pure `evaluate_guardrails()` qui
exécute sept contrôles indépendants et déterministes :

| Règle | Contrôle | Seuil (surchargeable par env) |
|---|---|---|
| G1 `stock_available` | Ne jamais recommander un produit à stock 0 | — |
| G2 `stockout_imminent` | Bloquer la promotion d'un produit en rupture imminente | couverture < 3 jours |
| G3 `rag_source` | La réponse doit être sourcée par le RAG | — |
| G4 `business_rules` | Règles commerciales Ooredoo | — |
| G5 `network_eligibility` | Éligibilité réseau de l'offre | — |
| G6 `confidence` | Confiance minimale de la recommandation | ≥ 0,65 |
| G7 `budget` | Plafond budgétaire des actions | ≤ 100 000 DT |

Chaque règle retourne une violation éventuelle ; `_compute_status` retient la sévérité maximale
et produit le verdict `APPROVE` / `REWRITE` / `REJECT`. Deux adaptateurs intègrent l'agent aux
graphes : `guardrail_node` (nœud LangGraph) et `route_guardrail` (fonction d'arête conditionnelle
qui transforme le verdict en route du graphe). Chaque évaluation émet un événement diffusé en
WebSocket (badge du chat, panel de monitoring).

**Pourquoi sans LLM ?** Un contrôleur doit être plus fiable que ce qu'il contrôle : des règles
déterministes sont rapides (aucune latence ajoutée), prédictibles (pas de faux verdict aléatoire)
et testables exhaustivement (34+ tests, 100 % au banc d'évaluation).

---

### 5. Agent Analysis (Inventory)

*Fichiers : `app/inventory/agents/analysis/agent.py`, `nodes.py`.*

**Mission** : diagnostic de santé du stock par produit — couverture, vitesse, risque.

**Architecture interne — graphe de 3 nœuds « fetch → compute → reason » :**

```
fetch → compute → reason → END
```

1. **`fetch`** — chargement des stocks et ventes récentes depuis PostgreSQL (repository).
2. **`compute`** — calculs déterministes : jours de couverture, vitesse d'écoulement, point de
   commande, classification du risque par SKU (CRITICAL / HIGH / OK / OVERSTOCK).
3. **`reason`** — nœud fabriqué par `create_reason_node(llm, use_llm)` : l'enrichissement LLM est
   **injectable et débrayable**. Si `use_llm` est faux ou que l'appel échoue, le repli
   `_apply_rule_based_fallback` applique les seuils métier. Le LLM interprète, il ne calcule pas.

**Particularité d'implémentation** : le graphe est **compilé une seule fois au niveau de la
classe** (`_compiled_graphs`, indexé par `use_llm`) et partagé par toutes les instances — décision
prise après avoir constaté que chaque worker recréait ses agents, ce qui coûtait cher en batch.
La méthode `run()` exécute le graphe pour un SKU et `_persist_result` sauvegarde le rapport.

---

### 6. Agent Context (Inventory)

*Fichiers : `app/inventory/agents/context/agent.py`, `nodes.py`.*

**Mission** : quantifier ce qui va faire bouger la demande — événements, promotions, saisonnalité,
tendances — sous forme de facteurs exploitables par la décision.

**Architecture interne — graphe de 2 nœuds :**

```
fetch_signals → interpret → END
```

1. **`fetch_signals`** — collecte des signaux : prévisions de demande (moteurs
   Holt-Winters/TimesFM), événements datés, offres et promotions actives, tendances Ooredoo,
   chaîne causale fournisseur (`supplier_products`).
2. **`interpret`** — nœud fabriqué par `create_interpret_node(llm, use_llm)` : transforme les
   signaux en **facteurs de demande pondérés par produit** (ex. festival à venir → facteur
   multiplicatif à la hausse sur les recharges). LLM optionnel, même principe de repli que
   l'Agent Analysis.

Même optimisation de compilation unique au niveau classe, même persistance du résultat.

---

### 7. Agent Decision (Inventory)

*Fichiers : `app/inventory/agents/decision/agent.py`, `nodes.py`.*

**Mission** : décider l'action d'approvisionnement par produit et créer les suggestions de
commande.

**Architecture interne — graphe de 2 nœuds avec court-circuit :**

```
constraints_check ──(décision déjà imposée ?)──► END
        │ sinon
        ▼
      decide → END
```

1. **`constraints_check`** — vérification des contraintes **avant** tout raisonnement : si une
   contrainte dure impose déjà la décision (blocage fournisseur, budget épuisé, commande déjà en
   cours), le nœud pose directement la décision et **l'arête conditionnelle court-circuite le
   LLM** (`lambda s: END if s.get("decision") else "decide"`). On ne paie ni la latence ni le
   risque d'un appel LLM quand la règle suffit.
2. **`decide`** — décision par produit (commander / accélérer / maintenir / surveiller) avec
   quantité et justification, en combinant règles métier et arbitrage LLM selon l'objectif du
   magasin (disponibilité vs coût).

**Post-traitement hors graphe** : `_compute_adjusted_metrics` applique les facteurs de demande du
Context aux métriques de stock (c'est là que « festival dans 5 jours » devient « +20 % sur la
quantité ») ; `_persist_recommendation` sauvegarde ; `_suggest_purchase_order` crée le **PO au
statut SUGGÉRÉ** sur le Kanban, poussé en temps réel par `po_ws_bus`. L'agent ne franchit jamais
la porte HITL lui-même.

---

## Partie II — Comment les agents s'orchestrent

L'orchestration se joue à **trois niveaux** : les graphes superviseurs (qui composent les agents
en un flux contrôlé), les orchestrateurs de service (qui gèrent les instances, le parallélisme et
les caches), et les bus d'événements (qui déclenchent et diffusent). S'y ajoutent les contrats
transverses : l'état partagé et les portes de contrôle.

### 8. Niveau 1 — Les graphes superviseurs

#### 8.1 Le superviseur Sales (`app/sales/orchestration/supervisor_agent.py`)

C'est le graphe maître du module Sales, 12 nœuds. Sa topologie exprime la stratégie
d'orchestration :

```
                    ┌── sales_branch (Analyste) ──────┐
                    ├── knowledge_branch (RAG) ────────┤
initialize_state ──►├── context_branch (Stratège) ─────├──► merge_outputs
                    └── inventory_branch (stocks) ─────┘         │
                                                                 ▼
                                                           coach_agent
                                                                 │
                                                          guardrail_agent
                                             ┌───────────────────┼───────────────────┐
                                        conforme            sensible              bloqué
                                             │                   │                   │
                                             │            human_validation     safe_fallback
                                             └───────────────────┼───────────────────┘
                                                                 ▼
                                                         notify_frontend → save_memory → END
```

Trois décisions d'orchestration structurent ce graphe :

- **Fan-out / fan-in parallèle.** Les quatre branches de collecte partent simultanément du nœud
  d'initialisation et convergent vers `merge_outputs`. La latence du cycle vaut celle de la
  branche la plus lente, pas la somme. Chaque branche encapsule un agent : l'orchestration ne
  connaît que l'interface (l'état), pas l'implémentation interne des agents.
- **Pipeline après fusion.** Une fois l'état fusionné, le flux redevient séquentiel :
  Coach → Guardrail. La synthèse a besoin de tout ; le contrôle a besoin de la synthèse. Il n'y a
  rien à paralléliser ici.
- **Routage conditionnel de sortie.** `route_after_guardrail` lit le verdict et choisit l'une des
  trois issues. Le graphe garantit structurellement qu'**aucun chemin ne mène au frontend sans
  passer par le Guardrail** — ce n'est pas une convention de code, c'est la topologie du graphe.

#### 8.2 Le superviseur Inventory (`app/inventory/core/supervisor.py`)

Symétrique du précédent, avec une différence technique : le dispatch parallèle utilise la
primitive LangGraph **`Send`** (`_route_parallel_branches` retourne une liste de `Send`), ce qui
permet de choisir dynamiquement quelles branches lancer selon l'état — plutôt que des arêtes
statiques. Le flux : `supervisor` (entrée) → branches parallèles `inventory_branch` /
`sales_branch` / `knowledge_branch` → `coach_fusion` → `guardrail` → routage conditionnel vers
`output` ou `human_review` (HITL) → `output` → END. Le graphe est construit par
`build_supervisor_graph()` et mis en cache par `get_or_build_supervisor()`.

#### 8.3 La composition d'agents en sous-graphes

Les agents étant eux-mêmes des graphes, l'orchestration est **fractale** : le superviseur invoque
la branche `context_branch`, qui exécute le graphe du Stratège (6 nœuds), dont le nœud
`rag_search` appelle le retriever, etc. Chaque niveau ne connaît que l'interface du niveau
inférieur (état en entrée, delta en sortie). C'est ce qui permet de tester chaque agent isolément
et de le remplacer sans toucher au superviseur.

### 9. Niveau 2 — Les orchestrateurs de service

Deux orchestrateurs Python complètent les graphes : ils gèrent ce que LangGraph ne gère pas — les
instances, les pools de workers, les caches et les timeouts.

#### 9.1 `InventoryOrchestrator` (`app/inventory/services/orchestrator.py`)

C'est lui qui exécute le pipeline Analysis → Context → Decision en production :

- **Instances partagées.** Les trois agents sont créés **une seule fois** à l'initialisation
  (`_analysis_agent`, `_context_agent`, `_decision_agent`) et réutilisés — l'ancienne version
  créait trois agents par worker, ce qui a été corrigé.
- **Parallélisme intra-SKU.** Dans `_run_pipeline`, Analysis et Context tournent **en parallèle**
  (leurs entrées sont indépendantes : l'un lit le stock, l'autre lit le marché), puis leurs deux
  sorties alimentent Decision. Cela divise environ par deux le temps par SKU.
- **Parallélisme inter-SKU.** `analyze_batch` traite les produits d'un magasin par pool de
  workers, chaque worker déroulant le pipeline complet pour son SKU, puis agrège les compteurs de
  risque (CRITICAL / HIGH).
- **Dispatch événementiel.** Les décisions CRITICAL/EXPEDITE sont publiées **de façon asynchrone
  sur le bus d'alertes Redis** : la publication ne bloque pas le pipeline, et les consommateurs
  (WebSocket, Kanban, cycles sales) réagissent de leur côté.

La relation entre les trois agents inventory est donc un **DAG de données** :

```
        ┌─► Analysis (stock) ──┐
trigger ─┤                     ├─► Decision ─► PO SUGGÉRÉ ─► HITL ─► Kanban
        └─► Context (marché) ──┘
```

#### 9.2 `CoachStrategeOrchestrator` (`app/sales/coaching/orchestrator/coach_stratege_orchestrator.py`)

C'est le médiateur de la relation **Coach → Stratège**, la plus sensible en latence puisqu'elle
est sur le chemin du chat :

- **`StrategyCache`** : cache mémoire (TTL 30 minutes, 50 entrées max) indexé par magasin et par
  tranche de gap — deux questions posées dans la même situation réutilisent la même stratégie au
  lieu de relancer le graphe du Stratège.
- **`invoke()`** : exécute le graphe du Stratège avec un **timeout borné**. Si le Stratège dépasse
  le délai, l'orchestrateur sert une entrée de cache même périmée (`get_stale`), sinon un repli
  construit depuis la base (`_fetch_fallback_from_db`), sinon des actions génériques calibrées
  sur le gap et l'urgence (`_generic_actions`). Le Coach reçoit **toujours** une `StrategieOutput`,
  jamais une exception.
- **Préchauffage** : le Stratège est invoqué en arrière-plan au démarrage pour que le premier
  message de chat ne paie pas le coût du premier cycle.

Ce composant illustre le principe général : **la relation entre deux agents n'est jamais un appel
direct**, c'est toujours une médiation (cache, timeout, repli) qui protège l'agent appelant des
défaillances de l'agent appelé.

### 10. Niveau 3 — Les bus d'événements et déclencheurs

- **AlertBus (Redis pub/sub, `app/sales/core/alert_bus.py` et
  `app/inventory/services/redis_alert_bus.py`)** : c'est le lien **asynchrone** entre les modules.
  Un événement (pic de ventes détecté par le simulateur ou le flux réel, décision CRITICAL du
  module Inventory, seuil de stock) est publié sur le bus ; les abonnés — déclencheurs de cycles
  superviseur (`alert_trigger.py`, `trigger.py`), diffuseurs WebSocket — réagissent sans couplage
  direct avec l'émetteur. C'est ce bus qui rend le système **événementiel** : les agents ne
  tournent pas en boucle, ils sont réveillés par les événements.
- **`po_ws_bus`** : bus WebSocket dédié au Kanban — chaque création ou mouvement de commande est
  poussé aux clients connectés en direct.
- **StateBus / state_merger** : diffusion des mises à jour de `RetailState` vers les widgets du
  dashboard.

### 11. Les contrats transverses qui rendent l'orchestration possible

#### 11.1 L'état partagé : `RetailState` et `SalesAgentState`

Deux niveaux d'état coexistent :

- **`SalesAgentState`** (`app/sales/core/state.py`) : l'état de travail **interne** aux graphes
  d'agents sales (Analyste, Stratège, Coach) — données du magasin, analyse, scripts, stratégie.
- **`RetailState`** (`app/sales/core/retail_state.py`) : l'état **unifié inter-agents** utilisé
  par les superviseurs. Sa propriété décisive : les champs susceptibles d'être écrits par
  plusieurs branches parallèles dans le même superstep LangGraph sont déclarés avec des
  **reducers** (`Annotated[List, operator.add]` pour les listes cumulatives comme
  `agents_invoked` et `errors`, fusion de dictionnaires pour `metrics`). Sans ces reducers,
  LangGraph refuse — ou écrase — les écritures concurrentes ; c'était la cause du bug historique
  des « widgets stratège/analyste vides ». La règle d'or qui en découle : **un nœud retourne
  uniquement son delta, jamais l'état complet**.

#### 11.2 Les outils cross-domaine : la frontière Sales ↔ Inventory

Les deux modules ne s'importent pas mutuellement leurs internes. Ils communiquent par trois voies
contrôlées : les **outils cross-domaine** du Coach (lecture des alertes et statuts de stock), le
**serveur MCP** (7 outils d'inventaire formalisés, y compris la manipulation du Kanban avec porte
HITL préservée), et l'**AlertBus** (événements asynchrones). Cette frontière étroite est ce qui
permet de faire évoluer un module sans casser l'autre.

#### 11.3 Les portes de contrôle : Guardrail et HITL

Deux mécanismes s'appliquent à *tous* les chemins d'orchestration :

- Le **Guardrail** est un nœud obligatoire des deux graphes superviseurs et du pipeline de chat —
  topologiquement, aucune sortie utilisateur ne le contourne.
- La porte **HITL** intercepte les actions à impact : réponses jugées sensibles par le Guardrail
  (nœud `human_validation` / `human_review`) et toutes les commandes d'approvisionnement (statut
  SUGGÉRÉ sur le Kanban, transitions d'état contrôlées par `app/api/hitl.py` et les endpoints
  d'approbation ; `po_auto_confirm` ne couvre que les transitions explicitement autorisées).

#### 11.4 L'observabilité comme fil conducteur

Chaque nœud de chaque graphe émet ses spans Langfuse (via `tracer.py` côté sales et
`langfuse_inventory.py` côté inventory) avec un identifiant de cycle commun : une exécution
complète — du déclencheur AlertBus jusqu'au badge guardrail — se lit comme une trace unique,
agent par agent, nœud par nœud, avec latences et coûts LLM.

---

## 12. Synthèse : qui parle à qui

| Relation | Mécanisme | Nature |
|---|---|---|
| Superviseur Sales → Analyste / Stratège / RAG / stocks | branches parallèles du graphe (fan-out) | synchrone, intra-cycle |
| Analyste → Stratège | `build_strategy_query` (l'Analyste formule le problème) | passage d'état |
| Coach → Stratège | `CoachStrategeOrchestrator` (cache TTL 30 min, timeout, replis) | synchrone médié |
| Coach → module Inventory | outils cross-domaine (alertes, statuts stock) | synchrone, lecture seule |
| Coach / Stratège / Decision → utilisateur | Guardrail (nœud obligatoire) puis HITL si sensible | contrôle en sortie |
| Analysis + Context → Decision | `InventoryOrchestrator._run_pipeline` (les deux en parallèle, puis décision) | DAG de données |
| Decision → Kanban / humain | `_suggest_purchase_order` → `po_ws_bus` → porte HITL | asynchrone + validation humaine |
| Inventory → Sales (et inverse) | AlertBus Redis (pub/sub) | asynchrone, événementiel |
| Tous les agents → dashboards | WebSocket store/advisor + StateBus | push temps réel |
| Tous les nœuds → observabilité | traces Langfuse par cycle | transversal |

En résumé : les **graphes superviseurs** composent les agents et garantissent structurellement le
contrôle (Guardrail, HITL) ; les **orchestrateurs de service** gèrent instances, parallélisme,
caches et replis pour tenir la latence ; les **bus d'événements** découplent les modules et
rendent le système réactif ; et les **contrats d'état** (deltas + reducers) permettent au
parallélisme de fonctionner sans conflit.
