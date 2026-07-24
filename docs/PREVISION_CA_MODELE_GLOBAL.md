# Prévision du CA journalier — modèle global entraîné

**Agent Analyste (domaine Sales) — Ooredoo Tunisie**
Branche `refactor/monolith-v2`

> Complète [ARCHITECTURE_AGENTS_DETAILLEE.md](ARCHITECTURE_AGENTS_DETAILLEE.md) §3.1.
> Le rapport de performance chiffré est régénéré à chaque entraînement dans
> [`evals/results/SALES_FORECAST_REPORT.md`](../evals/results/SALES_FORECAST_REPORT.md).

---

## 1. Le problème

Jusqu'ici, l'Analyste ajustait un modèle statistique **par boutique, à chaque
appel** : AutoETS ou Holt-Winters saisonnier sur les 120 derniers jours de la
boutique concernée. Rien n'était appris, rien n'était persisté — chaque
prévision repartait de zéro sur une seule série.

Cette approche a deux limites structurelles :

1. **Elle n'apprend rien du parc.** Le fait qu'un vendredi vaille 1,5 fois un
   lundi est vrai dans les 154 boutiques, mais chaque série doit le
   redécouvrir seule, avec 120 points de bruit.
2. **Elle plafonne sur les séries courtes ou bruitées.** Une boutique à 60 TND
   par jour avec un écart-type de 59 TND ne contient pas assez de signal pour
   qu'un lissage exponentiel isolé fasse mieux qu'une moyenne.

## 2. Le principe retenu — un modèle global sur cible normalisée

Un **seul** modèle de gradient boosting est entraîné sur les 154 boutiques
simultanément. Le point délicat est l'hétérogénéité des échelles : les boutiques
vont de ~60 TND/jour à ~3 100 TND/jour, soit un facteur 50. Entraîner sur le CA
brut concentrerait toute la capacité du modèle sur les grosses boutiques.

La cible apprise est donc un **ratio sans dimension** :

```
scale_t = médiane mobile 28 jours des observations disponibles à la date t
cible   = y_t / scale_t
prévision = ratio_prédit × scale_t
```

Le modèle apprend « ce jour vaut 1,3 fois le régime récent de sa boutique » —
une grandeur directement comparable d'une boutique à l'autre. Les features de
niveau (lags, moyennes mobiles) sont normalisées par ce même `scale`.

Les poids d'entraînement valent `scale`, ce qui réaligne l'optimisation sur le
WAPE exprimé en TND : sans cela, une erreur de 0,1 sur une boutique à 60 TND
pèserait autant qu'une erreur de 0,1 sur une boutique à 3 000 TND.

## 3. Features — 41 colonnes

| Famille | Colonnes | Intention |
|---|---|---|
| Horizon | `horizon` | un seul modèle sert h=1 (aujourd'hui) et h=2 (demain) |
| Lags récents | `lag_1` … `lag_7` | dynamique de court terme |
| Lags jour de semaine | `dow_lag_1` … `dow_lag_4`, `dow_mean_4`, `dow_std_4` | les 4 derniers mêmes jours de semaine — le signal le plus fort du retail |
| Moyennes mobiles | `roll_mean_{7,14,28,56}`, `roll_std_{7,28}`, `roll_min_7`, `roll_max_7` | niveau et dispersion récents |
| Dynamique | `trend_7_28`, `trend_14_56`, `momentum_1_7` | la boutique accélère-t-elle ? |
| Calendrier | `dow`, `day_of_month`, `month`, `week_of_year`, `is_weekend`, `is_month_start/end` | indexation temporelle |
| Fourier | 4 termes hebdomadaires + 4 annuels | saisonnalité continue, sans marche d'escalier |
| Régime | `log_scale`, `maturity_days` | taille de la boutique, ancienneté de la série |
| Identité | `store_cat` (catégoriel natif XGBoost) | profil propre à chaque boutique |

**`store_cat` porte l'essentiel du gain.** Il permet au modèle de séparer
« l'effet boutique » de « l'effet régime de CA » : deux boutiques au même CA
moyen n'ont pas le même profil hebdomadaire, et un modèle qui ne voit que
`log_scale` les confond.

### Ce que le modèle ne voit délibérément pas

**Aucun signal de contextualisation** — ni événements, ni offres, ni jours
fériés, ni météo. Ces signaux appartiennent à l'**agent Stratège**, qui les
charge via `stratege/tools.fetch_full_context()` (Open-Meteo, Nager.Date,
`market.events`, scraper ooredoo.tn) et les exploite au moment de bâtir la
recommandation commerciale.

Cette frontière est un choix d'architecture, pas un oubli : l'Analyste répond à
« où en est le chiffre », le Stratège à « pourquoi et quoi faire ». Mélanger les
deux rendrait le diagnostic dépendant de la disponibilité d'API externes, et
brouillerait la lecture des responsabilités.

## 4. Modèle

XGBoost, objectif **`reg:absoluteerror`**. L'erreur absolue sur le ratio est,
après remise à l'échelle, exactement le WAPE publié : la métrique optimisée est
celle qu'on rapporte. Un objectif quadratique tirerait systématiquement les
prévisions vers le haut sur les journées exceptionnelles.

L'intervalle de confiance à 80 % vient de **deux boosters quantiles** (10 % et
90 %) entraînés sur la même matrice. Il est donc *conditionné aux features* —
plus large un samedi de soldes que sur un mardi calme — là où une bande dérivée
du WAPE global aurait la même largeur partout.

Le nombre d'arbres est fixé par early stopping sur les 28 derniers jours
d'entraînement.

## 5. Protocole d'évaluation

Rolling-origin à origine expansive : **6 plis de 28 jours**, soit 168 jours de
test. Chaque pli n'est prédit qu'avec des données strictement antérieures à son
début, et le modèle est **ré-entraîné à chaque frontière de pli**.

Tous les modèles produisent des prévisions à un jour (h=1) sur exactement les
mêmes couples (boutique, date) — c'est l'usage réel de l'Analyste, qui prédit le
CA du jour à partir de la veille.

| Modèle comparé | Rôle |
|---|---|
| `seasonal_naive` (y_{t-7}) | le seul baseline qui compte en retail hebdomadaire |
| `mean_7`, `mean_28` | moyennes mobiles |
| `holt_winters_seasonal7` | **le moteur de production** — la référence à battre |
| `global_xgb` | le modèle global |

Sur Holt-Winters, la suite `fitted` de la récursion *est* la prévision à un
jour : l'état à l'instant t n'utilise que y₀…y_{t-1}. Seuls les paramètres
(α, β, γ) sont estimés sur la partie entraînement du pli.

**Métriques** : WAPE (référence projet), MAE, RMSE, MASE (rapport à la naïve
saisonnière — sous 1, le modèle bat la naïve) et **biais**, car une prévision
systématiquement basse ronge la confiance du vendeur autant qu'une prévision
imprécise.

## 6. Intégration — cascade à quatre rangs

`ts_engine.forecast_daily_series()` essaie dans l'ordre :

```
1. global_xgb              modèle entraîné, si présent sur disque
2. statsforecast AutoETS   season_length = 7
3. holt_winters_seasonal7  grid-search 27 combinaisons (α, β, γ)
4. mean_fallback           < 21 jours d'historique
   linear_fallback         PostgreSQL injoignable
```

Le rang 1 retourne `None` — jamais une exception — dès que quoi que ce soit
manque : modèle absent, historique sous 35 jours, boutique inconnue du jeu
d'entraînement, échelle nulle. Les rangs suivants prennent alors le relais sans
que l'appelant ait à le savoir. **Sans fichier de modèle, le comportement du
système est strictement celui d'avant.**

## 7. Réentraînement

```bash
python scripts/train_sales_forecast.py                 # backtest + entraînement
python scripts/train_sales_forecast.py --backtest-only  # évaluation seule
```

Le modèle final n'est écrit **que s'il bat le moteur statistique en WAPE** sur
la fenêtre de test ; sinon le script sort en code 2 sans rien toucher. Un modèle
qui perd n'a rien à faire en production, et l'écrire malgré tout rendrait le
premier rang de la cascade silencieusement nuisible.

Artefacts produits :

```
app/sales/models/forecast/sales_daily_v1.ubj          booster principal
app/sales/models/forecast/sales_daily_v1.q0.1.ubj     borne basse IC 80 %
app/sales/models/forecast/sales_daily_v1.q0.9.ubj     borne haute IC 80 %
app/sales/models/forecast/sales_daily_v1.meta.json    features, catégories, métriques
evals/results/sales_forecast_backtest.json            rapport complet, par pli et par boutique
evals/results/SALES_FORECAST_REPORT.md                rapport lisible
```

## 8. Garde-fous en test

`app/sales/forecasting/tests/test_forecasting.py` — 13 tests, dont trois
propriétés qui comptent plus que les autres :

1. **Parité entraînement ↔ inférence.** La ligne de features produite à
   l'inférence doit être identique, colonne par colonne, à celle qui a servi à
   l'entraînement pour la même date. Une divergence ici laisserait le backtest
   flatteur pendant que la production dérive en silence.
2. **Absence de fuite.** Modifier la valeur cible d'une date ne doit déplacer
   aucune de ses features.
3. **Dégradation gracieuse.** Sans modèle sur disque, la cascade retombe sur le
   moteur statistique sans lever.

## 9. Index des fichiers

| Sujet | Fichier |
|---|---|
| Features et normalisation | [features.py](../app/sales/forecasting/features.py) |
| Modèle et persistance | [global_model.py](../app/sales/forecasting/global_model.py) |
| Harnais de backtest | [backtest.py](../app/sales/forecasting/backtest.py) |
| Chargement du panel | [data.py](../app/sales/forecasting/data.py) |
| Script d'entraînement | [train_sales_forecast.py](../scripts/train_sales_forecast.py) |
| Intégration cascade | [ts_engine.py](../app/sales/coaching/agents/analyst/ts_engine.py) |
| Tests | [test_forecasting.py](../app/sales/forecasting/tests/test_forecasting.py) |
