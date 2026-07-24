# Projet Complet — Nouvelle Architecture Multi-Agents LangGraph
## Moteur Agentique Retail Ooredoo Tunisie : Coaching de Vente + Optimisation des Stocks

> **Contenu** : présentation complète du projet sous la nouvelle architecture unifiée —
> chaque agent détaillé (rôle, fonctionnement pas-à-pas, entrées/sorties, modèle utilisé,
> replis), l'orchestration LangGraph, les flux, et l'inventaire exhaustif des **modèles
> utilisés dans tout le projet** (LLM, embeddings, prévision, retrieval).
>
> **Date** : 2026-07-17 — les modèles listés sont ceux vérifiés dans le code
> (`llm_factory.py`, `settings.py`, `coach_chat.py`, `rag/`, `forecasting/`, `kpis.py`).

---

# PARTIE 1 — LE PROJET

## 1.1 Mission

Le système est un moteur agentique destiné au réseau de points de vente Ooredoo Tunisie.
Il répond à deux problématiques métier complémentaires :

1. **Coaching commercial temps réel** — un conseiller en boutique doit savoir à tout moment
   quels produits mettre en avant, avec quels arguments, et où il en est par rapport à son
   objectif de chiffre d'affaires.
2. **Optimisation des stocks** — le magasin doit anticiper les ruptures, éviter le surstock
   et déclencher les commandes de réapprovisionnement au bon moment et dans les bonnes
   quantités.

Ces deux problématiques sont traitées par **un même système multi-agents**, car elles
partagent les mêmes données (historique de ventes, stocks, contexte marché) et
s'enrichissent mutuellement : on ne recommande jamais un produit en rupture imminente, et
une prévision de pic de ventes déclenche une suggestion de commande. Ce couplage
**cross-domaine** justifie l'architecture multi-agents.

## 1.2 Pile technologique

| Couche | Technologie |
|---|---|
| Backend | Python / FastAPI — monolithe modulaire, package unique `app/` |
| Orchestration agents | **LangGraph** (graphe unique, subgraphs, checkpointer, store) |
| Frontend | Angular 21 (Signals) — dashboard, chat SSE, Kanban, panneau HITL, monitoring |
| Base de données | PostgreSQL (source de vérité, migrations Alembic 0001-0008, ~50 tables, 1,49 M lignes de ventes sur 4,5 ans) |
| Cache / bus | Redis (cache réponses & prévisions, AlertBus pub/sub, rate limiting) |
| Base vectorielle | Milvus (RAG — 200+ scripts de vente vectorisés), repli corpus local |
| LLM | Cascade multi-fournisseurs : **Mistral → OpenRouter → Groq → Ollama** (détail Partie 4) |
| Prévision | Holt-Winters (primaire) · TimesFM 2.5 · Chronos-Bolt · Prophet · repli SQL |
| Observabilité | Langfuse (traces par nœud + routage + coûts LLM), suite `evals/` |
| Auth | JWT + RBAC au niveau du point de vente, slowapi (rate limiting) |

## 1.3 Entrées et sorties du système

**Entrées** : requêtes conseiller (chat), flux de ventes temps réel (PostgreSQL),
historique de ventes (4,5 ans), état des stocks + catalogue fournisseurs
(`supplier_products`), signaux marché (événements datés festivals/concerts via scraper +
base locale, offres actives, météo), décisions humaines (HITL, Kanban, feedback), base de
connaissances (scripts de vente vectorisés).

**Sorties** : réponse de coaching streamée (SSE) avec badge guardrail, produits recommandés
scorés (`scored_products`), suggestions de commande (PO « SUGGÉRÉ » sur le Kanban), alertes
temps réel (WebSocket), KPIs et prévisions (dashboards), traces d'exécution (Langfuse).

---

# PARTIE 2 — LA NOUVELLE ARCHITECTURE

## 2.1 Principe directeur

> **Un superviseur unique, des subgraphs natifs par domaine, un état hiérarchique,
> un checkpointer PostgreSQL, un HITL bloquant par `interrupt()`.**
> Le chat conseiller, les cycles événementiels (AlertBus) et les appels API sont trois
> *entrées* du même graphe — un seul chemin d'exécution, contrôlé, tracé et streamé.

## 2.2 Vue d'ensemble du graphe

```
                              ┌────────────────────────────────────────────────┐
 /chat · /stream ──┐          │        RETAIL SUPERVISOR (graphe unique)       │
 AlertBus Redis  ──┼─ entry ─▶│  checkpointer = AsyncPostgresSaver             │
 /supervisor/run ──┘          │  store        = AsyncPostgresStore (mémoire LT)│
                              │  thread_id    = cycle_id                       │
                              └────────────────────────────────────────────────┘
        supervisor (routeur d'intention)
            │  Command(goto=[Send("sales"), Send("inventory"), Send("knowledge"), Send("context")])
            │  — plan dynamique : n'active QUE les branches utiles
            ▼
   ┌────────────┬──────────────┬──────────────┬─────────────┐
   │  sales     │  inventory   │  knowledge   │  context    │  ◀── subgraphs compilés :
   │ Analyste → │  Send() par  │  RAG unique  │  Sentinel   │      trace, streaming et
   │ Stratège   │  SKU         │  (caché 5min)│ (caché 10min)│     checkpoint traversants
   └─────┬──────┴──────┬───────┴──────┬───────┴──────┬──────┘
         └─────────────┴─ fan-in (reducers par canal) ┴──────┘
                              ▼
                        coach (subgraph)         ◀── lit guardrail_feedback en re-passe
                              ▼
                        guardrail (fail-closed, 7 règles, RetryPolicy)
                              ▼
                APPROVE ────────────────▶ deliver
                REWRITE (count < 2) ────▶ coach
                ESCALATE ───────────────▶ hitl_gate : interrupt() ⏸ … Command(resume=…)
                BLOCK ──────────────────▶ safe_output ─▶ deliver
                              ▼
                        deliver  →  persist  →  END
```

**Sémantique unique des verdicts** (valable partout dans le système) :

| Verdict | Comportement |
|---|---|
| `APPROVE` | diffusion directe |
| `REWRITE` | retour au coach avec le feedback injecté dans le prompt — **borné à 2** ; borne atteinte → ESCALATE |
| `ESCALATE` | `interrupt()` : le graphe se met en pause, l'humain décide, le graphe reprend avec la décision |
| `BLOCK` | réponse de repli sûre **toujours diffusée** — jamais d'absence de réponse |

## 2.3 L'état partagé : `RetailState` hiérarchique

L'état est organisé en **canaux par domaine** : chaque agent ne peut écrire que dans son
canal (ownership structurel), les reducers fusionnent les écritures parallèles, et le state
interne des subgraphs (prompts intermédiaires, JSON partiels) ne remonte jamais au parent.

```python
class RetailState(TypedDict, total=False):
    meta:      CycleMeta                                  # cycle_id, store_id, advisor_id, trigger, intent
    sales:     Annotated[SalesOutput,     merge_domain]   # écrit UNIQUEMENT par le subgraph sales
    inventory: Annotated[InventoryOutput, merge_domain]   # écrit UNIQUEMENT par le subgraph inventory
    knowledge: Annotated[KnowledgeOutput, merge_domain]   # écrit par le nœud RAG
    context:   Annotated[ContextOutput,   merge_domain]   # écrit par le nœud Sentinel
    decision:  CoachOutput                                # écrit par le coach
    control:   ControlState                               # verdict, issues, rewrite_count, hitl_decision
    obs:       Annotated[Obs, merge_domain]               # agents_invoked, metrics, errors (reducers)
```

Règles : chaque nœud retourne **uniquement son delta** dans son canal ; une branche qui
échoue écrit `obs.errors` et laisse son canal vide — les autres continuent.

---

# PARTIE 3 — LES AGENTS EN DÉTAIL

Chaque agent est décrit avec : sa raison d'être, son fonctionnement pas-à-pas, ses
entrées/sorties, le **modèle qu'il utilise**, et ses mécanismes de repli.

## 3.1 Agent Supervisor (routeur d'intention) — le chef d'orchestre

**Position** : premier nœud du graphe. **Modèle** : aucun en chemin nominal (classification
déterministe par mots-clés/heuristiques) ; LLM tier FAST en repli pour les cas ambigus.

**Rôle.** Décider *quoi exécuter* pour chaque requête. Il ne raisonne pas sur le fond : il
classifie l'intention, construit le plan de branches, et gère les chemins rapides.

**Fonctionnement pas-à-pas.**
1. Normalise le message et vérifie le **cache Redis** (question déjà posée récemment →
   réponse immédiate, zéro branche, zéro LLM).
2. Classifie l'intention : `greeting` / `off_topic` / `stock` / `script` / `produit` /
   `objectif` / cycle complet (déclencheurs AlertBus et scheduled).
3. Construit le plan dynamique :

| Intention | Branches activées |
|---|---|
| `greeting`, `off_topic` | aucune — réponse directe → `deliver` |
| `stock` | inventory |
| `script` | knowledge |
| `produit` | sales + inventory + knowledge |
| `objectif` | sales + context |
| cycle événementiel / défaut | les 4 branches |

4. Retourne `Command(goto=[Send(...)], update={"meta": {..., "intent": intent}})`.

**Entrées** : `meta` (message, trigger_type, store_id, advisor_id).
**Sorties** : plan `Send()` + `meta.intent`.
**Repli** : intention indéterminée → cycle complet (jamais de blocage).

## 3.2 Agent Analyste — l'expert en séries temporelles (subgraph `sales`)

**Modèle** : **aucun LLM dans le chemin des chiffres** — Holt-Winters (statsmodels) ; LLM
tier FAST optionnel uniquement pour enrichir la formulation du résumé.

**Rôle.** Produire le diagnostic chiffré du magasin : où en est le CA par rapport à
l'objectif, comment va finir la journée, quelles heures sont anormales, quelle est la
dynamique.

**Fonctionnement pas-à-pas.**
1. **Chargement** : 120 jours de série journalière depuis PostgreSQL.
2. **Ajustement Holt-Winters saisonnier** (période 7 — la saisonnalité hebdomadaire domine
   dans le retail) : une recherche par grille teste plusieurs combinaisons de paramètres de
   lissage, et un **backtest sur les 28 derniers jours** (métrique WAPE) départage les
   candidats — la combinaison qui aurait le mieux prédit le passé récent gagne. Sur le banc
   de référence : **4,4 % d'erreur, meilleur que Prophet**.
3. **Projection fin de journée** : profil horaire moyen du même jour de semaine → part du CA
   normalement réalisée à l'heure courante (si à 14 h le magasin fait habituellement 55 % de
   sa journée, le réalisé partiel est extrapolé), avec intervalle de confiance.
4. **Détection d'anomalies** : chaque heure comparée au profil attendu (déviation, z-score)
   → ledger horaire `{hour, expected, actual, status}` ; signal de tendance
   (ACCELERATING / STABLE / DECELERATING).
5. **Comparaison mémoire** : lecture des cycles précédents dans le store
   (`("store", store_id, "cycles")`) pour qualifier l'évolution.
6. **Synthèse** : CA vs objectif, % d'écart, prévision EOD bornée, taux d'atteinte,
   faisabilité (ACHIEVED / ACHIEVABLE / CHALLENGING / VERY_HARD / CLOSED).
7. **Mode ReAct** (questions ad hoc) : le LLM dispose de 4 outils qu'il invoque en
   raisonnant — `detect_anomalies`, `ts_decomposition` (tendance/saisonnalité/résidu),
   `forecast_multi_horizon`, `product_velocity`.

**Entrées** : store_id, heure courante, historique de ventes.
**Sorties** (canal `sales`) : `gap_pct`, `gap_amount`, `forecast_eod` (+ intervalle),
`attainment`, `urgency_level/score`, `hourly_gaps`, `next_hours_forecast` (h+1..h+3),
`trend_signal`, `feasibility`, `analyst_summary`.
**Replis** : série trop courte ou ajustement échoué → régression linéaire → agrégation SQL
simple. Le WAPE de backtest est exposé comme indicateur de confiance.

## 3.3 Agent Stratège — le stratège cross-domaine (subgraph `sales`)

**Modèle** : tier **SMART** — Mistral `mistral-large-latest` en primaire, OpenRouter en
secours, Ollama local en dernier recours. Auto-critique par le même tier.

**Rôle.** Transformer les signaux bruts (ventes, stocks, marché) en **plan d'action
commercial priorisé** : que faire maintenant, dans quel ordre, avec quel impact attendu.

**Fonctionnement pas-à-pas.**
1. **Collecte du contexte** : CA/objectif, **stock critique** (produits < 5 unités — pour ne
   jamais recommander de pousser un produit en quasi-rupture), météo, **événements datés
   séparés en cours / à venir** (on n'agit pas pareil pendant un festival et 5 jours avant),
   offres actives. Lit les canaux `context` et `inventory` déjà remplis.
2. **Scripts pertinents** : lecture du canal `knowledge` (le RAG a déjà tourné — pas de
   retrieval en double).
3. **Analyse déterministe** : écart à l'objectif, niveau d'urgence, heures d'ouverture
   restantes, alertes par règles.
4. **Génération LLM** : prompt cross-domaine combinant ventes + inventaire → actions
   stratégiques structurées `{priorite, action, produit_cible, argument_vente, impact}`.
5. **Auto-critique (Reflexion)** : le LLM relit ses propres actions, estime l'impact de
   chacune, les réordonne ou en élimine → `critique_score`, `critique_passed`.
6. **Livrables frontend** : StrateActions, carte de chaleur d'urgence, signaux de contexte.
7. **Cause racine** : en cas d'écart important, analyse de cause racine.

**Entrées** : sorties Analyste, stock critique, événements, offres, météo, scripts RAG.
**Sorties** (canal `sales`) : `strategie`, `strategie_actions`, `focus_produits`,
`cause_racine`, `context_heatmap`, `real_time_alerts`, `critique_score`.
**Replis** : JSON tronqué → extraction partielle ; échec LLM → stratégie par règles métier.
**Le Stratège ne rend jamais une réponse vide.**

## 3.4 Nœud Knowledge (RAG) — la base de connaissances

**Modèle** : embeddings **`bge-m3`** via Ollama (voir Partie 4.2) ; pas de LLM.

**Rôle.** Fournir les scripts et arguments de vente Ooredoo pertinents pour la situation.
**Un seul retrieval par cycle**, résultat partagé par le Stratège et le Coach via le canal
`knowledge` (`CachePolicy(ttl=300)` — les cycles rapprochés réutilisent le résultat).

**Fonctionnement.** Recherche **hybride** dans Milvus : rappel dense (cosinus `bge-m3`,
comprend la paraphrase) + BM25 (précision lexicale), fusion et rerank. L'embedding de la
requête est calculé une seule fois et partagé. Si Milvus ou Ollama sont indisponibles :
**repli corpus local** (BM25 seul) — le service ne s'interrompt jamais.

**Entrées** : requête (message ou requête construite par l'intention), store_id.
**Sorties** (canal `knowledge`) : `scripts` (docs + scores), `query`, `source`
(`milvus` | `local_corpus`).

## 3.5 Nœud Context (Sentinel) — les signaux externes

**Modèle** : aucun (collecte pure). `CachePolicy(ttl=600)`.

**Rôle.** Collecter les signaux externes qui influencent la vente et la demande : météo,
**événements datés** (festivals, concerts — scraper + base locale, séparés en cours / à
venir), offres commerciales actives, signaux QoS réseau.

**Sorties** (canal `context`) : `events_current`, `events_upcoming`, `active_offers`,
`weather`, `signals`.

## 3.6 Agent Analysis — le diagnosticien des stocks (subgraph `inventory`, par SKU)

**Modèle** : calculs 100 % déterministes ; enrichissement LLM optionnel tier **FAST**
(`open-mistral-nemo` / équivalent), désactivable (`use_llm=False`).

**Rôle.** Établir l'état de santé du stock d'un SKU : couverture en jours, vitesse
d'écoulement, et classement de risque (rupture / insuffisant / sain / surstock).

**Fonctionnement pas-à-pas** (pipeline `fetch → compute → reason`) :
1. **`fetch`** : stocks et ventes récentes depuis PostgreSQL (repository d'inventaire).
2. **`compute`** (déterministe) : jours de couverture, vitesse d'écoulement, point de
   commande, EOQ, classement de risque par référence.
3. **`reason`** (optionnel) : le LLM interprète les métriques et affine la qualification ;
   s'il échoue ou est désactivé, un repli par règles applique les seuils métier — **les
   chiffres restent toujours fondés sur le calcul, jamais sur le modèle**.

**Entrées** : sku, store_id, niveaux de stock, ventes, seuils.
**Sorties** : `analysis_report` — métriques par produit + alertes rupture/surstock avec
niveau d'urgence.

## 3.7 Agent Context (Inventory) — le capteur de demande (subgraph `inventory`, par SKU)

**Modèle** : signaux déterministes ; pondération LLM optionnelle tier **FAST**.

**Rôle.** Anticiper la **demande** : identifier ce qui va faire vendre plus ou moins dans
les jours à venir, pour que la décision d'approvisionnement ne repose pas uniquement sur le
passé.

**Fonctionnement** (pipeline `fetch_signals → interpret`) :
1. **`fetch_signals`** : prévisions de demande (moteurs Holt-Winters/TimesFM/Chronos du
   module forecasting), événements datés, promotions actives, tendances Ooredoo, **chaîne
   causale fournisseur** (table `supplier_products` : contraintes d'approvisionnement).
2. **`interpret`** : chaque signal devient un **facteur de demande pondéré** par produit —
   ex. un festival à venir = facteur multiplicatif à la hausse sur recharges et box 4G.
   Le LLM affine la pondération (optionnel).

**Entrées** : prévisions, événements, promotions, saisonnalité, contraintes fournisseurs.
**Sorties** : `context_report` avec `demand_uplift_pct` par produit.
**Dégradation contractuelle** : rapport absent → uplift = 0 pour l'agent Decision, **jamais
une erreur**.

## 3.8 Agent Decision — le décideur d'approvisionnement (subgraph `inventory`, par SKU)

**Modèle** : règles d'abord ; arbitrage LLM tier **SMART** (`mistral-large-latest` /
OpenRouter smart) — décision critique, raisonnement fort requis.

**Rôle.** Convertir diagnostic + contexte en **décision d'approvisionnement explicable** :
commander, accélérer, maintenir ou surveiller — avec quantité, fournisseur, justification.

**Fonctionnement pas-à-pas** (pipeline `constraints_check → decide`) :
1. **`constraints_check` en premier** : si une contrainte dure bloque (budget, fournisseur
   indisponible, quantité min non atteinte), la décision est posée directement et **le nœud
   LLM est court-circuité** (arête conditionnelle → END).
2. **Ajustement des métriques** : point de commande et quantité recalculés avec l'uplift du
   Context — « festival dans 5 jours » devient concrètement « commander 20 % de plus ».
3. **`decide`** : choix de l'action par produit (règles métier + arbitrage LLM) selon
   l'objectif du magasin (privilégier la disponibilité vs maîtriser le coût) →
   `{action: ORDER | HOLD | MONITOR | EXPEDITE, order_qty, order_qty_rationale, supplier}`.
4. **Persistance** : recommandation motivée écrite dans `inventory.recommendations`.
5. **Suggestion de PO** : si `ORDER` → création d'un **ordre d'achat au statut « SUGGÉRÉ »**
   sur le Kanban, poussé en temps réel par le bus WebSocket dédié.

**L'agent ne commande jamais directement** : l'approbation humaine est obligatoire ; la
réception (`REÇU`) met à jour le stock et **ferme la boucle d'approvisionnement**.

**Entrées** : `analysis_report` (obligatoire — sans lui l'agent retourne une erreur),
`context_report` (optionnel), contraintes fournisseurs, objectif métier.
**Sorties** : décision explicable + PO « SUGGÉRÉ ».

**Parallélisme** : le subgraph inventory fan-out les SKUs par **`Send()`** (map-reduce natif
LangGraph — checkpointé, streamé, tracé), le nœud `reduce` agrège dans le canal `inventory` :
`decisions`, `critical_alerts`, `suggested_pos`.

## 3.9 Agent Coach — l'interlocuteur du conseiller (subgraph `coach`, après fan-in)

**Modèle** : **cascade complète** — Mistral (rotation `mistral-large-latest` →
`mistral-small-latest` → `open-mistral-nemo`) → OpenRouter
(`nvidia/nemotron-3-nano-30b-a3b:free`, fallback `nemotron-3-super-120b-a12b:free`) →
Groq (`openai/gpt-oss-120b`, fallback `llama-3.3-70b-versatile`) → Ollama local
(`qwen2.5:0.5b`). Scoring produits 100 % déterministe.

**Rôle.** Le seul agent qui parle à l'utilisateur : répondre en langage naturel avec des
recommandations concrètes, sourcées et adaptées au contexte immédiat du magasin.

**Fonctionnement pas-à-pas.**
1. **`assemble_context`** : lit les 4 canaux (`sales`, `inventory`, `knowledge`, `context`),
   le **profil conseiller** depuis le store (`("advisor", advisor_id)`) pour personnaliser
   ton et recommandations, l'historique de conversation du thread checkpointé, et — si
   `rewrite_count > 0` — **`control.guardrail_feedback`** : la re-passe produit réellement
   une réponse différente.
2. **`product_intelligence`** (déterministe) :
   - exclusion des produits en rupture (`inventory.snapshot`) — le Coach ne les propose jamais ;
   - **substituts** préparés pour les produits en rupture qui seraient demandés ;
   - **scoring pondéré à 6 critères** (ventes, disponibilité stock, contexte du moment,
     marge, profil conseiller, urgence) → `scored_products` (top 3, affichés en puces dans
     le chat et le dashboard) ;
   - `product_to_avoid` dérivé de `inventory.critical_alerts` — matérialisation du
     couplage Sales ↔ Inventory.
3. **`generate`** : assemblage du prompt final (situation du magasin, produits scorés,
   scripts RAG, stratégie du Stratège, profil conseiller) puis **cascade LLM** avec
   validation de la réponse à chaque étage ; en mode chat les tokens partent en SSE au fil
   de la génération (`stream_mode="messages"`).
4. **`emit`** : construit `recommendation` `{priority, product_to_push, product_to_avoid,
   message_for_advisor, business_justification, confidence}`.

**Entrées** : les 4 canaux + store + feedback guardrail + message utilisateur.
**Sorties** (canal `decision`) : `recommendation`, `scored_products`, `message_advisor`,
`confidence`.
**Replis** : aucun fournisseur ne répond validement → réponse par règles selon l'intention
détectée. Salutations et hors-sujet ne passent jamais ici (gérés par le Supervisor).

## 3.10 Agent Guardrail — le contrôleur de conformité (fail-closed)

**Modèle** : **aucun LLM** — 7 règles déterministes, rapides, prédictibles, testables
(34+ tests dédiés, 100 % au banc d'évaluation).

**Rôle.** Vérifier chaque réponse candidate avant affichage. Décide ET route
(`Command(goto, update)`).

**Les 7 règles et leur sévérité** :

| Règle | Contrôle | Sévérité |
|---|---|---|
| G1 | ne jamais recommander un produit à stock zéro | **BLOCK** |
| G2 | rupture imminente : couverture < 3 jours (`GUARDRAIL_STOCKOUT_DAYS`) | REWRITE |
| G3 | arguments sans source RAG + confiance < 0,7 | REWRITE |
| G4 | motif d'offre non autorisée (remises hors catalogue, gratuités) | **BLOCK** |
| G5 | 5G/Fibre proposée sans mention de vérification d'éligibilité réseau | REWRITE |
| G6 | confiance < 0,65 (`GUARDRAIL_CONFIDENCE_MIN`) | ESCALATE |
| G7 | coût de commande > 100 000 DT (`GUARDRAIL_BUDGET_CAP_DT`) | ESCALATE |

**Fonctionnement.** Chaque règle examine la recommandation et retourne une violation
éventuelle ; la **sévérité maximale** donne le verdict. Routage borné :
REWRITE avec `rewrite_count < 2` → coach (feedback injecté) ; borne atteinte → ESCALATE ;
ESCALATE → `hitl_gate` ; BLOCK → `safe_output`. **Fail-closed** : une exception du guardrail
lui-même → ESCALATE (jamais APPROVE) ; `RetryPolicy(max_attempts=2)` sur le nœud.
Chaque évaluation émet un événement WS (badge chat + panel monitoring). Tous les seuils sont
surchargeables par variables d'environnement.

**Entrées** : `decision.recommendation`, `inventory.snapshot`, `knowledge` (rag_used),
`sales.strategie_actions`, confiance.
**Sorties** (canal `control`) : `guardrail_verdict`, `guardrail_issues`,
`guardrail_feedback`, `rewrite_count` + le routage.

## 3.11 Nœud Hitl_gate — le Human-in-the-Loop bloquant

**Modèle** : l'humain.

**Fonctionnement.** `decision = interrupt(payload)` : le graphe est **checkpointé et mis en
pause** ; le payload (stratégie, top 3 actions, violations guardrail, recommandation) est
poussé en WS vers le panneau HITL Angular ; l'API `POST /hitl/{cycle_id}/resolve` reprend le
graphe avec `Command(resume={approved, modified_qty, comment})` **exactement au point de
pause**. Approuvé → `deliver` ; refusé → `safe_output`. La décision vit dans
`control.hitl_decision`, et l'historique des décisions est une lecture de
`get_state_history()`.

**Même principe pour le Kanban** : l'approbation d'un PO « SUGGÉRÉ » est une transition
protégée — aucun outil (y compris MCP `move_purchase_order`) ne peut contourner la porte.

## 3.12 Nœuds de sortie — `safe_output`, `deliver`, `persist`

- **`safe_output`** : réponse de repli sûre (confiance 0, priorité LOW). Un BLOCK produit
  **toujours** une réponse diffusée.
- **`deliver`** : diffusion temps réel — WS `/ws/store/{id}` (widgets analyste/stratège,
  recommandation, badge guardrail, scored_products), WS `/ws/advisor/{id}` (coaching
  personnalisé), bus WS Kanban (PO). En chat, les tokens sont déjà partis en SSE.
- **`persist`** : résumé de cycle + feedback dans le store (pool PostgreSQL partagé,
  injecté au lifespan — jamais de pool créé par cycle).

## 3.13 Tableau récapitulatif des agents

| Agent | Position | Modèle | Entrées principales | Sorties (canal) |
|---|---|---|---|---|
| Supervisor (routeur) | entrée du graphe | déterministe (LLM FAST en repli) | `meta` | plan `Send()`, `meta.intent` |
| Analyste | subgraph sales | Holt-Winters (aucun LLM critique) | historique ventes | `sales.*` (gap, forecast, anomalies) |
| Stratège | subgraph sales | SMART : mistral-large → OpenRouter → Ollama | analyste + contexte + stock critique | `sales.*` (stratégie, actions) |
| Knowledge (RAG) | nœud caché TTL 5 min | embeddings bge-m3 + BM25 | requête | `knowledge.scripts` |
| Sentinel | nœud caché TTL 10 min | aucun | store_id | `context.*` |
| Analysis | subgraph inventory (par SKU) | déterministe + FAST optionnel | stocks, ventes, seuils | `analysis_report` |
| Context (inv.) | subgraph inventory (par SKU) | signaux + FAST optionnel | prévisions, événements, promos | `context_report` (uplift) |
| Decision | subgraph inventory (par SKU) | règles + SMART | analysis + context + contraintes | `inventory.*`, PO « SUGGÉRÉ » |
| Coach | après fan-in | cascade Mistral→OpenRouter→Groq→Ollama | 4 canaux + store + feedback | `decision.*` |
| Guardrail | contrôle final | aucun (7 règles) | decision + inventory.snapshot | `control.*` + routage |
| Hitl_gate | `interrupt()` | humain | payload de contrôle | `control.hitl_decision` |

---

# PARTIE 4 — LES MODÈLES UTILISÉS DANS LE PROJET (inventaire complet)

> Tous les identifiants ci-dessous sont vérifiés dans le code :
> `app/inventory/utils/llm_factory.py`, `app/inventory/config/settings.py`,
> `app/core/config.py`, `app/sales/coaching/agents/coach/coach_chat.py`,
> `app/sales/data/rag/settings.py`, `app/inventory/forecasting/timeseries_engine.py`,
> `app/api/kpis.py`, `app/api/forecast.py`.

## 4.1 Modèles de langage (LLM)

### La fabrique LLM et les trois tiers de rôles

`llm_factory.py` abstrait les fournisseurs et affecte un modèle par **rôle** :

| Tier | Usage | Mistral (primaire) | OpenRouter (équivalent) |
|---|---|---|---|
| **FAST** | analyse, contexte, enrichissements (rapide + économique) | `open-mistral-nemo` | `OPENROUTER_MODEL_FAST` (déf. `openai/gpt-oss-120b:free`) |
| **SMART** | décision, coach, stratégie (raisonnement fort) | `mistral-large-latest` | `OPENROUTER_MODEL_SMART` |
| **GUARDIAN** | critique, validation structurée | `mistral-small-latest` | `OPENROUTER_MODEL_GUARDIAN` |

### La cascade de fournisseurs (ordre de repli)

| Ordre | Fournisseur | Modèles configurés | Rôle dans la cascade |
|---|---|---|---|
| 1 | **Mistral** (La Plateforme, API directe) | rotation : `mistral-large-latest` → `mistral-small-latest` → `open-mistral-nemo` | primaire — quota indépendant d'OpenRouter |
| 2 | **OpenRouter** | `nvidia/nemotron-3-nano-30b-a3b:free` ; fallback `nvidia/nemotron-3-super-120b-a12b:free` ; défaut usine `openai/gpt-oss-120b:free` | secours 1 (2 tentatives : prompt complet puis allégé) |
| 3 | **Groq** | `openai/gpt-oss-120b` ; fallback `llama-3.3-70b-versatile` — multi-clés en rotation | secours 2 (inférence ultra-rapide) |
| 4 | **Ollama** (local, sans clé API) | Analyste/Stratège : `llama3.2:latest` · Coach : `qwen2.5:0.5b` · legacy : `llama3.1:8b` | dernier recours local |
| — | **OpenAI** (optionnel, configuré) | `gpt-4o-mini` | disponible via la factory, non utilisé en nominal |
| — | **Anthropic** (optionnel, configuré) | `claude-3-5-sonnet-20241022` | disponible via la factory, non utilisé en nominal |

**Retry du Coach à 4 niveaux** (coach_chat.py) : Mistral (rotation 3 modèles) → OpenRouter
prompt complet → OpenRouter prompt allégé → Ollama local ; si tout échoue → réponse par
règles selon l'intention. Un benchmark comparatif des modèles existe dans `evals/`.

### Attribution modèle ↔ agent (nominal)

| Agent | Tier / modèle nominal |
|---|---|
| Supervisor (routeur) | déterministe ; FAST en repli d'ambiguïté |
| Analyste (résumé optionnel, mode ReAct) | FAST (`open-mistral-nemo` / Ollama `llama3.2`) |
| Stratège (génération + auto-critique) | SMART (`mistral-large-latest` → OpenRouter → Ollama `llama3.2`) |
| Analysis / Context inventory (enrichissement) | FAST, désactivable (`use_llm=False`) |
| Decision inventory (arbitrage) | SMART (OpenRouter smart, fallback provider configuré) |
| Coach (génération conversationnelle) | cascade complète 4 fournisseurs |
| Guardrail | **aucun LLM** (7 règles déterministes) |

## 4.2 Modèles d'embeddings (RAG)

| Modèle | Rôle | Détail |
|---|---|---|
| **`bge-m3`** (servi par Ollama, `RAG_EMBED_MODEL`) | embeddings **principal** du RAG (requêtes + documents) | choisi contre `nomic-embed-text` après test comparatif : nomic, entraîné surtout sur de l'anglais, classait mal le français commercial (test « coque iPhone 6 » : nomic −0,053, bge-m3 +0,296). Symétrique (pas de préfixes de tâche requis). Cache disque des embeddings (évite de ré-embedder ~4 500 produits à chaque ingestion). |
| **`nomic-embed-text`** (Ollama, `OLLAMA_EMBED_MODEL`) | embeddings **secondaires/legacy** | utilisés par les outils du Coach (`coach/tools.py`), le logger sémantique (`agent_logger.py`) et le seed initial des scripts RAG. Asymétrique (préfixes de tâche exigés). |

### Pipeline de retrieval hybride (Milvus)

```
requête ──▶ embed_query (bge-m3, calculé 1×, partagé)
        ├──▶ recherche DENSE (cosinus)  → comprend la paraphrase (« ça coûte cher » ≈ objection prix)
        └──▶ recherche BM25 (lexicale)  → précision sur les termes exacts
                    ▼
              fusion + rerank (score cosine + BM25, seuil calibré sur bge-m3)
                    ▼
              scripts retournés (canal knowledge)
```

Repli : Ollama injoignable → branche dense coupée, **BM25 assure seul** ; Milvus
indisponible → **corpus local**. Le RAG ne bloque jamais le flux.

## 4.3 Modèles de prévision (séries temporelles)

| Modèle | Type | Rôle | Détail |
|---|---|---|---|
| **Holt-Winters saisonnier** (statsmodels `ExponentialSmoothing`) | statistique | **PRIMAIRE** — prévisions Analyste + inventory | période 7 (saisonnalité hebdo), grid-search des paramètres de lissage départagée par **backtest WAPE 28 jours** ; **4,4 % d'erreur sur le banc, meilleur que Prophet** |
| **TimesFM 2.5 200M** (`google/timesfm-2.5-200m-pytorch`) | foundation model (PyTorch) | zero-shot, benchmark + prévisions | singleton préchargé au démarrage ; sur Windows `import torch` doit précéder `import timesfm` |
| **Chronos-Bolt-Small 71M** (Amazon) | foundation model | optionnel — SKUs à historique < 30 jours | désactivable par `DISABLE_CHRONOS=1` (DLL torch cassée sur le poste de dev) ; singleton lazy-load |
| **Prophet** (Meta) | statistique | repli EOD + benchmark | saisonnalité hebdo + annuelle, sans régresseurs ; utilisé si la prévision TimesFM retourne 0 |
| **StatsForecast AutoETS / Theta** | statistique | benchmark comparatif | banc `/forecast-benchmark` |
| **Baseline numpy / régression linéaire** | naïf | repli Analyste + référence benchmark | dernier étage avant le repli SQL |
| **Agrégation SQL** | — | repli ultime | jamais d'absence de prévision |

**Hiérarchie de repli en production** : Holt-Winters → régression linéaire → SQL (Analyste) ;
Chronos si historique court et disponible → Holt-Winters sinon (inventory). Le benchmark
(`/api/v1/kpis` — TimesFM vs StatsForecast vs Chronos vs Prophet vs numpy, métriques
MAPE/WAPE **mesurées sur nos données**) justifie le choix du primaire.

## 4.4 Récapitulatif global des modèles

```
LLM (génération)
├── Mistral La Plateforme : mistral-large-latest · mistral-small-latest · open-mistral-nemo
├── OpenRouter : nvidia/nemotron-3-nano-30b-a3b:free · nvidia/nemotron-3-super-120b-a12b:free · openai/gpt-oss-120b:free
├── Groq : openai/gpt-oss-120b · llama-3.3-70b-versatile   (multi-clés en rotation)
├── Ollama local : llama3.2:latest · qwen2.5:0.5b · llama3.1:8b
└── Configurés optionnels : gpt-4o-mini (OpenAI) · claude-3-5-sonnet-20241022 (Anthropic)

EMBEDDINGS (RAG)
├── bge-m3 (principal — via Ollama, multilingue, cache disque)
└── nomic-embed-text (secondaire/legacy — outils coach, logger, seed)

RETRIEVAL
└── Milvus (dense cosinus + BM25 + rerank) → repli corpus local

PRÉVISION (séries temporelles)
├── Holt-Winters saisonnier (primaire, backtest WAPE — 4,4 %)
├── TimesFM 2.5 200M (google/timesfm-2.5-200m-pytorch)
├── Chronos-Bolt-Small 71M (Amazon — optionnel, DISABLE_CHRONOS)
├── Prophet · StatsForecast AutoETS/Theta (benchmark + replis)
└── Régression linéaire → SQL (replis ultimes)
```

## 4.5 Variables d'environnement des modèles

| Variable | Rôle | Défaut code |
|---|---|---|
| `LLM_PROVIDER` | fournisseur par défaut de la factory | `ollama` |
| `MISTRAL_MODEL` / `_FAST` / `_SMART` / `_GUARDIAN` | tiers Mistral | `mistral-small-latest` / `open-mistral-nemo` / `mistral-large-latest` / small |
| `OPENROUTER_MODEL` / `_FALLBACK` / `_FAST` / `_SMART` / `_GUARDIAN` | modèles OpenRouter | `nvidia/nemotron-3-nano-30b-a3b:free` / `nemotron-3-super-120b-a12b:free` / `openai/gpt-oss-120b:free` |
| `GROQ_MODEL` / `GROQ_MODEL_FALLBACK` | modèles Groq | `openai/gpt-oss-120b` / `llama-3.3-70b-versatile` |
| `OLLAMA_MODEL_ANALYST` / `_STRATEGE` / `_COACH` | modèles locaux par agent | `llama3.2:latest` / `llama3.2:latest` / `qwen2.5:0.5b` |
| `RAG_EMBED_MODEL` | embeddings RAG | `bge-m3` |
| `OLLAMA_EMBED_MODEL` | embeddings secondaires | `nomic-embed-text` |
| `DISABLE_CHRONOS` | coupe Chronos | — |
| `GUARDRAIL_CONFIDENCE_MIN` / `_BUDGET_CAP_DT` / `_STOCKOUT_DAYS` | seuils guardrail | `0.65` / `100000` / `3.0` |

---

# PARTIE 5 — ORCHESTRATION, PERSISTANCE ET FLUX

## 5.1 Persistance LangGraph

```python
# lifespan FastAPI
async with AsyncPostgresSaver.from_conn_string(DB_URL) as checkpointer, \
           AsyncPostgresStore.from_conn_string(DB_URL) as store:
    app.state.graph = build_graph(services).compile(
        checkpointer=checkpointer,   # threads, reprise, interrupt, time-travel
        store=store,                 # mémoire long-terme cross-thread
    )
```

**Checkpointer** (`thread_id = cycle_id`) : reprise après crash au milieu d'un cycle, pause
HITL, `get_state_history()` pour l'audit et le time-travel, threads de chat
(`chat-{advisor_id}-{date}`) = historique conversationnel natif.

**Store** (mémoire long terme namespacée) :

| Namespace | Contenu | Écrit par | Lu par |
|---|---|---|---|
| `("advisor", advisor_id)` | profil, historique coaching | persist | coach |
| `("store", store_id, "feedback")` | notes utile/pas utile, décisions HITL | persist | supervisor, stratège |
| `("store", store_id, "cycles")` | résumés de cycles | persist | analyste |

**Services injectés** à la construction du graphe (pool PG, client Milvus, fabrique LLM,
bus WS, config) — aucun nœud n'importe le module d'entrée ni n'appelle une route API.

## 5.2 Streaming natif

```python
async for mode, chunk in graph.astream(state, config, stream_mode=["messages", "updates"]):
    if mode == "messages":  ...  # tokens LLM du coach → SSE
    if mode == "updates":   ...  # deltas de nœuds → widgets WebSocket
```

Un seul run exécute, contrôle, trace et streame — le chat n'est pas un pipeline parallèle.

## 5.3 Les quatre flux principaux

**Cycle événementiel (autonome)** : AlertBus Redis (pic/chute de ventes, seuil de stock) →
`graph.ainvoke` (plan complet) → 4 branches ∥ → coach → guardrail → deliver (widgets WS mis
à jour sans action utilisateur) → persist → feedback humain → store → cycles suivants.

**Chat conseiller** : message → supervisor (intention + cache) → branches utiles seulement →
coach (tokens SSE) → guardrail (badge WS) → deliver → persist (thread checkpointé).

**Boucle d'approvisionnement fermée** : inventory `Send()` par SKU → Decision → PO
« SUGGÉRÉ » (Kanban WS) → approbation humaine (porte infranchissable) → EN ATTENTE →
EN LIVRAISON → REÇU → mise à jour stock → le stock corrigé alimente le cycle suivant.

**Escalade humaine** : guardrail ESCALATE → `hitl_gate: interrupt()` ⏸ → payload WS →
panneau HITL Angular → `POST /hitl/{cycle_id}/resolve` → `Command(resume=...)` → reprise au
point exact → deliver ou safe_output.

## 5.4 Contrats API

| Endpoint | Méthode | Rôle |
|---|---|---|
| `/auth/login`, `/auth/me` | POST/GET | JWT, RBAC store-level |
| `/chat`, `/stream` | POST | entrée chat du graphe (SSE natif) |
| `/api/v1/supervisor/run`, `/async` | POST | cycle complet ; `pending_hitl` si interrupt |
| `/hitl/{cycle_id}/resolve` | POST | reprise `Command(resume=...)` |
| `/stores/{id}/metrics`, `/live-analysis`, `/product-mix` | GET | KPIs, analyse live |
| `/forecast/eod/{id}`, `/hourly/{id}`, `/forecast-benchmark` | GET | prévisions + benchmark moteurs |
| `/api/inventory/status/{id}`, `/alerts/{id}` | GET | stocks, alertes |
| `/purchase-orders/*` | POST/GET | Kanban PO (suggest/approve/reject) |
| `/feedback` | POST | note humaine → store |
| `/monitoring/*` | GET | santé agents, événements guardrail, coûts LLM |
| WS `/ws/store/{id}`, `/ws/advisor/{id}`, bus PO | — | temps réel frontend |

Le **serveur MCP maison** expose 7 outils inventory (`get_stock_status`,
`compute_inventory_metrics`, `get_forecast_summary`, `suggest_purchase_order`,
`get_purchase_order`, `list_purchase_orders`, `move_purchase_order`) — la porte HITL est
préservée : aucune transition nécessitant approbation ne peut être contournée par l'outil.

## 5.5 Les sept invariants du système

1. **Aucune commande (PO) exécutée sans approbation humaine** — porte HITL infranchissable,
   y compris via MCP.
2. **Les chiffres sont 100 % déterministes** — prévisions, métriques stock, scoring, règles
   guardrail ; le LLM formule et arbitre, il ne calcule jamais.
3. **Toute réponse visible passe par le guardrail fail-closed** — une panne du contrôleur
   escalade, elle n'approuve jamais.
4. **Un BLOCK produit toujours une réponse de repli diffusée.**
5. **Dégradation contrôlée partout** — Milvus → corpus local ; bge-m3 coupé → BM25 seul ;
   Holt-Winters → linéaire → SQL ; Mistral → OpenRouter → Groq → Ollama → règles.
6. **Chaque cycle est un thread checkpointé** — reprise, audit, time-travel natifs.
7. **Une branche qui échoue n'arrête ni les autres ni le graphe.**

---

*Documents liés : `docs/NOUVELLE_ARCHITECTURE.md` (architecture cible condensée),
`docs/ARCHITECTURE_CIBLE.md` (critique de l'existant + plan de migration),
`docs/ARCHITECTURE_COMPLETE.md` (architecture actuelle),
`docs/architecture_multi_agents.svg|png` (diagramme).*
