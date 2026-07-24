# Workflow de l'Agent Analyste — de la vente au gap

**Domaine Sales — Ooredoo Tunisie** · branche `refactor/monolith-v2`

> Documents liés : [PREVISION_CA_MODELE_GLOBAL.md](PREVISION_CA_MODELE_GLOBAL.md)
> (le modèle en détail) · [ARCHITECTURE_AGENTS_DETAILLEE.md](ARCHITECTURE_AGENTS_DETAILLEE.md) §3.1.

---

## 1. La chaîne, étape par étape

```
Ventes RT  +  Historique CA
   sales.transactions_rt        sales.vw_ca_par_boutique
   (jour courant, par heure)    (656 jours × 151 boutiques)
        │
        ▼
   build_feature_frame()                    features.py
   lags · lags jour-de-semaine · moyennes mobiles
   dynamique · calendrier · Fourier · identité boutique
        │
        ▼
   Dataset normalisé
   cible = y / médiane mobile 28 j     →  comparable d'une boutique à l'autre
        │
        ▼
   Entraînement XGBoost sur le ratio        global_model.py
   objectif aligné sur le WAPE · poids = échelle
   + 2 boosters quantiles pour l'IC 80 %
        │
        ▼   ═══ hors ligne ═══════════════════════════════
        ▼   ═══ en ligne ═════════════════════════════════
        ▼
   build_inference_row()                    features.py
   une ligne, mêmes colonnes qu'à l'entraînement
        │
        ▼
   Prédiction du ratio
        │
        ▼
   ratio × scale  ×  calibration            → CA en TND
        │
        ▼
   Fusion avec le déroulé intraday          ts_engine.analyze_store()
   eod = w·(CA réalisé / part écoulée) + (1−w)·modèle,  w = part écoulée
        │
        ├──▶  Prévision horaire temps réel
        │     reste à réaliser réparti sur les heures restantes
        │     au prorata du profil du jour de semaine
        │
        ▼
   Comparaison à l'objectif                 sales.objectifs
   gap_eod · gap_pct · couverture · atteinte
        │
        ▼
   Détection du gap et qualification
   urgence composite · faisabilité · heures en retard · tendance
        │
        ▼
   route_to = "strategie"  →  Agent Stratège
```

## 2. Le point de bascule : `scale` et calibration

Deux multiplications séparent le ratio prédit du chiffre affiché.

**`scale`** est la médiane mobile sur 28 jours des observations disponibles à la
date de prévision. Elle ramène chaque boutique à son propre régime : le modèle
n'apprend pas « 900 TND » mais « 1,3 fois le régime récent ». Sans cela, les
boutiques à 60 TND/jour disparaîtraient derrière celles à 3 100 TND.

**La calibration** est un facteur unique mesuré sur la fenêtre de validation,
qui annule le biais agrégé. Elle existe parce que l'objectif d'entraînement
optimise le WAPE en prédisant la *médiane* conditionnelle : sur des ventes à
distribution asymétrique, la médiane est sous la moyenne, et le modèle
sous-prévoit structurellement. Or `forecast_eod` alimente le gap et donc
l'urgence — une prévision basse gonfle l'urgence affichée au vendeur le matin,
quand le déroulé intraday pèse encore peu.

## 3. La fusion avec le déroulé intraday

Le modèle prédit un total journalier. Mais à 15 h, le CA déjà encaissé en dit
plus long que n'importe quel modèle. D'où la pondération continue :

```
part_écoulée = Σ share[h] pour h ≤ heure courante
déroulé      = CA réalisé / part_écoulée
w            = min(0,9 ; part_écoulée)
EOD          = w · déroulé + (1 − w) · modèle       puis plancher au CA réalisé
```

À 9 h on croit le modèle, à 18 h on croit le réalisé, et la bascule est
progressive — pas un `if`. Le plafond à 0,9 empêche une matinée exceptionnelle
d'extrapoler toute la journée.

L'intervalle se resserre au fil de la journée :
`demi-largeur = EOD × WAPE × (1 − 0,5 · part_écoulée) × 1,282`.

## 4. La prévision horaire temps réel

Le reste à réaliser (`EOD − CA encaissé`) est réparti sur les heures restantes
au prorata du profil intraday du jour de semaine, **normalisé par la part
restante**. Cette normalisation garantit par construction :

```
CA réalisé  +  Σ prévisions horaires  =  EOD annoncé
```

Sans elle, la courbe affichée au vendeur ne convergerait pas vers le chiffre
annoncé — une incohérence immédiatement visible sur le dashboard.

Chaque heure porte : `expected_ca`, `cumulative_ca`, `share_pct`, et `std_ca`
(dispersion historique de cette heure — le vendeur doit voir qu'une heure creuse
est prévisible et une heure de pic ne l'est pas). Le champ `target_hit_hour`
indique l'heure à laquelle la trajectoire franchit l'objectif, ou `None`.

## 5. Quand l'Analyste tourne-t-il ?

**Sur nouvelle vente, en deux étages** — `orchestration/sale_trigger.py`.

Faire tourner un cycle agent complet à chaque encaissement est intenable : une
boutique active encaisse plusieurs ventes par minute, et un cycle complet
mobilise le Stratège et le Coach, donc des appels LLM de plusieurs secondes.
À l'inverse, un simple cron de 15 minutes laisse le vendeur devant un chiffre
périmé juste après avoir encaissé.

| Étage | Quoi | Quand | Coût |
|---|---|---|---|
| **1 — recalcul analytique** | `analyze_store()` seul : CA, EOD, prévision horaire, gap, urgence → poussé en WebSocket | ~20 s après la dernière vente (debounce), plafond 60 s, battement 15 min | < 1 s, **zéro LLM** |
| **2 — cycle agent complet** | Stratège + Coach + Guardrail | seulement sur changement matériel | plusieurs secondes, appels LLM |

**L'étage 2 ne part que si** l'urgence monte d'un cran, la faisabilité se
dégrade, le gap bouge d'au moins `gap_delta_pts` points, ou le battement est
échu. Une amélioration ne déclenche rien : on ne réécrit pas un conseil parce
que la situation s'arrange. Un garde-fou (`min_full_cycle_s`, 3 min par défaut)
prime sur toutes ces règles, pour qu'une urgence oscillant autour d'un seuil ne
provoque pas de rafale d'appels LLM.

### Coalescence

Dix ventes en dix secondes déclenchent **un** recalcul, pas dix. La fenêtre de
debounce redémarre à chaque vente, avec un plafond dur pour qu'un flux continu
ne repousse pas indéfiniment le recalcul.

`notify_sale()` est **purement synchrone** — un compteur incrémenté, aucune I/O.
Le chemin d'encaissement n'attend donc rien, ce qui permet de le brancher sans
risque sur le callback POS (`main.py`, `_on_sale`).

### Réglages

| Variable | Défaut | Rôle |
|---|---|---|
| `SALE_TRIGGER_DEBOUNCE_S` | 20 | silence requis après la dernière vente |
| `SALE_TRIGGER_MAX_WAIT_S` | 60 | plafond dur sur un flux continu |
| `SALE_TRIGGER_HEARTBEAT_S` | 900 | battement en l'absence de vente |
| `SALE_TRIGGER_MIN_FULL_S` | 180 | intervalle minimal entre deux cycles complets |
| `SALE_TRIGGER_GAP_DELTA` | 5 | points de gap justifiant un cycle complet |

### Les autres déclencheurs, inchangés

`CronTrigger` (15 min) et `AlertCycleTrigger` (alerte stock critique via Redis
Pub/Sub) restent en place. Le déclencheur sur vente les complète : il couvre la
réactivité au flux de caisse, qu'aucun des deux ne captait.

## 6. Sortie de l'Analyste

Champs écrits dans `SalesAgentState` — [ts_node.py](../app/sales/coaching/agents/analyst/ts_node.py) :

```
urgency_level · urgency_score · gap_objectif · gap_amount
forecast_eod · forecast_mape · coverage · attainment
trend_signal · hourly_gaps · next_hours_forecast · target_hit_hour
feasibility · analyst_summary · route_to = "strategie"
```

Le LLM n'intervient nulle part dans cette chaîne. Il peut, en option
(`ANALYST_LLM_SUMMARY=1`), reformuler `analyst_summary` en deux phrases — sans
jamais recalculer un chiffre, avec un timeout de 8 s et le résumé statistique en
repli.

## 7. Index des fichiers

| Étape | Fichier |
|---|---|
| Panel et historique | [data.py](../app/sales/forecasting/data.py) |
| `build_feature_frame` · `build_inference_row` | [features.py](../app/sales/forecasting/features.py) |
| Entraînement, calibration, persistance | [global_model.py](../app/sales/forecasting/global_model.py) |
| Backtest rolling-origin | [backtest.py](../app/sales/forecasting/backtest.py) |
| Fusion intraday, horaire, gap, urgence | [ts_engine.py](../app/sales/coaching/agents/analyst/ts_engine.py) |
| Node LangGraph | [ts_node.py](../app/sales/coaching/agents/analyst/ts_node.py) |
| Déclenchement sur vente | [sale_trigger.py](../app/sales/orchestration/sale_trigger.py) |
| Câblage POS → déclencheur → WebSocket | [main.py](../app/main.py) |
| Entraînement / comparaison | [train_sales_forecast.py](../scripts/train_sales_forecast.py) · [tune_sales_forecast.py](../scripts/tune_sales_forecast.py) |
