# Architecture V6 — Données flux, boucle de feedback, tendances & évaluation

> Complète `ARCHITECTURE_AGENTS.md`. Livré le 2026-07-08 : fermeture des 4 gaps
> identifiés entre les missions PFE et l'implémentation.

## 0. Audit "vrais agents" — vérifié

Tous les agents sont de **vrais graphes LangGraph compilés** (`StateGraph` + nodes + edges),
pas de simples fonctions :

| Agent | Pattern | Fichier |
|---|---|---|
| Analyste (sales) | **Moteur déterministe** — `ts_engine.analyze_store()` : prévision EOD (AutoETS → Holt-Winters saisonnier), gap horaire, urgence composite, backtest WAPE. Zéro LLM sur le chemin critique | `app/sales/coaching/agents/analyst/` (`ts_engine.py`, `ts_node.py`) |
| Stratège (sales) | **Reflexion** — fetch_context (météo/fériés/événements/scraping ooredoo.tn) → RAG Milvus → analyze → generate → self_critique | `app/sales/coaching/agents/stratege/` |
| Coach (sales) | Graphe + chat RAG SSE, alertes temps réel | `app/sales/coaching/agents/coach/` |
| Guardrail | Graphe de validation des sorties | `app/sales/coaching/agents/guardrail/` |
| Analysis / Context / Decision (inventory) | Graphes avec fallback règles + self-critique décision | `app/inventory/agents/*/agent.py` |
| Supervisor | Orchestration cross-domain `RetailState` | `app/sales/orchestration/supervisor_agent.py` |

Routage conditionnel : `route_after_analyst` (urgence/gap) et `route_after_stratege`
dans `app/sales/orchestration/graph.py` — pipeline Analyste → Stratège → Coach.

## 1. Déclenchement événementiel (données flux)

**Avant** : cycles uniquement cron 15 min sur UN magasin codé en dur + endpoint manuel.
**Maintenant** :

```
Vente RT (simulateur POS)          DecisionAgent inventory
        │ stock ≤ seuil                    │ risk=CRITICAL
        ▼                                  ▼
   AlertBus Redis  ──  publish alerts:store:{id}:stock
        │ psubscribe alerts:store:*:stock
        ▼
   AlertCycleTrigger (app/sales/orchestration/alert_trigger.py)
        │ priorité URGENT/HIGH + debounce 180 s/magasin
        ▼
   orchestrator.run_cycle(store_id, triggered_by="alert:stock")   ← t < 1 s
```

- `CronTrigger` est désormais **multi-magasins** : `COACHING_STORE_IDS=I63,S01`
  (liste explicite) ou top-N par CA 7 j (`COACHING_MAX_STORES`, défaut 3).
  Les 196 boutiques restent couvertes par l'événementiel (pattern `*`).
- Anti-boucle : le listener n'écoute **que** `:stock` — les alertes `:sales`
  (publiées par l'orchestrateur en fin de cycle si urgence CRITICAL/HIGH) et
  `:cross` sont produites par les cycles eux-mêmes.
- Débounce configurable : `ALERT_CYCLE_DEBOUNCE_S` (défaut 180).

## 2. Boucle de feedback humain → agents

**Table** : `public.agent_feedback` (migration Alembic **0008**).

Trois signaux agrégés par `app/core/feedback_service.py` :
1. **Incitations** — le conseiller déclare `followed`/`ignored` (`POST /api/v1/feedback`) ;
2. **HITL** — approbations/rejets manager (`public.hitl_reviews`, avec notes) ;
3. **PO Kanban** — devenir des PO suggérés (`supply.purchase_orders` :
   SUGGERE→BROUILLON = accepté, →ANNULE = refusé).

`get_learning_context_sync()` produit un bloc texte compact injecté dans :
- le prompt du **DecisionAgent** inventory (`decision/nodes.py`, chemin LLM) —
  avec l'historique feedback du SKU concerné ;
- le prompt du **Stratège** sales (`stratege/nodes.py`, node generate_strategy).

Exemple de contexte injecté (données réelles) :
```
- Sur 14j, le manager a approuvé 0% des stratégies escaladées (0 oui / 2 non).
- 50% des bons de commande suggérés ont été acceptés (1 accepté, 1 annulé).
- Raisons de rejet récentes : action trop complexe
```
Jamais bloquant : toute erreur DB ⇒ pas d'injection, le cycle continue.

## 3. Tendances marché côté inventory

Le scraping ooredoo.tn (promotions/internet/mobile, Playwright) appartient au
Stratège sales et alimente un cache JSON. `app/core/trends_provider.py` expose
ce cache aux agents inventory **sans re-scraper** (tolérance 7 j de staleness).

Le **ContextAgent** inventory a maintenant **6 signaux** parallèles :
historique, promotions DB, météo, fériés, événements DB, **market_offers**.
- Fallback règles : +3 %/offre active (max +9 %), poids surchargeable via
  `SIGNAL_WEIGHTS["market_offer_uplift_pct"]` ;
- Chemin LLM : bloc "MARKET TRENDS" ajouté au prompt interpret.

## 4. KPIs d'évaluation + choix du modèle de prévision

**`GET /api/v1/kpis?store_id&days=30`** — adoption (feedback/HITL/PO), santé
stock (ruptures, critiques, taux), ventes vs objectifs (`sales.objectifs`).

**`GET /api/v1/kpis/forecast-benchmark?sku&holdout_days=14`** — backtest réel :
train sur l'historique, prédiction du holdout, **WAPE/sMAPE/biais** par moteur,
classement dédupliqué par implémentation réellement utilisée.

Résultat mesuré (top vendeur 8811001, agrégat national, holdout 14 j, train 351 j) :

| Moteur | WAPE | Biais | Verdict |
|---|---|---|---|
| numpy Holt (fallback maison) | **4,4 %** | +0,9 % | ✅ baseline très solide |
| Prophet 1.3 | 4,5 % | −1,3 % | ❌ aucun gain, ~100× plus lent/SKU |
| TimesFM 200M | indisponible | — | torch DLL cassée sur ce poste (WinError 1114) |
| StatsForecast / Chronos | non installés | — | fallback numpy |

**Décision** : conserver la chaîne actuelle `TimesFM (si dispo) → StatsForecast →
numpy Holt`. Prophet n'apporte rien sur nos séries et son coût de fit par SKU est
prohibitif pour des cycles de 143 SKUs. Le benchmark est rejouable à tout moment
pour re-valider ce choix sur d'autres SKUs/magasins.

**Frontend** : `evaluation-kpi.service.ts` (Angular, signals) — fetch KPIs,
benchmark, et envoi du feedback conseiller.

## Variables d'environnement ajoutées

| Var | Défaut | Rôle |
|---|---|---|
| `COACHING_STORE_IDS` | — | Liste explicite des magasins du cron proactif |
| `COACHING_MAX_STORES` | 3 | Top-N par CA si pas de liste explicite |
| `ALERT_CYCLE_DEBOUNCE_S` | 180 | Anti-tempête du déclenchement événementiel |
| `DISABLE_CHRONOS` | — | `1` = ne pas importer torch (postes où la DLL est cassée) |

## Vérification

- Migration `0008` appliquée (`alembic upgrade head` OK).
- Smoke réels : feedback record/stats/learning-context OK, offres Ooredoo
  visibles côté inventory, KPIs bornés (31 j), benchmark Prophet vs Holt exécuté.
- Suite pytest complète : 100 % verte (avec `DISABLE_CHRONOS=1` sur ce poste).
