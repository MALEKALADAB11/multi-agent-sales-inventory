# Prévision du CA journalier — rapport de backtest

Panel : **151 boutiques**, 97,927 jours-boutique, 2024-10-01 → 2026-07-30.

Protocole : rolling-origin à origine expansive, **6 plis × 28 jours**, prévision à un jour (h=1). Fenêtre de test : 2026-02-12 → 2026-07-30 (24,681 prévisions par modèle).

## Résultats agrégés

| Modèle | WAPE % | MAE TND | RMSE TND | MASE | Biais % |
|---|---:|---:|---:|---:|---:|
| `global_xgb` ⭐ | **33.38** | 167.46 | 290.22 | 0.55 | -0.04 |
| `mean_28` | **43.55** | 218.47 | 352.75 | 0.717 | 0.47 |
| `holt_winters_seasonal7` | **46.3** | 232.27 | 393.91 | 0.762 | 3.12 |
| `mean_7` | **46.49** | 233.22 | 389.12 | 0.766 | 2.76 |
| `seasonal_naive` | **60.73** | 304.63 | 620.46 | 1.0 | 2.12 |

> Le meilleur modèle est **`global_xgb`** à **33.38 % de WAPE**, soit **+27.9 %** d'erreur relative en moins que le moteur statistique de production (`holt_winters_seasonal7`, 46.3 %).

## Détail par pli

| Pli | Entraîné jusqu'au | Test | Lignes test | WAPE global_xgb | WAPE mean_28 | WAPE holt_winters_seasonal7 | WAPE mean_7 | WAPE seasonal_naive |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | 2026-02-11 | 2026-02-12 → 2026-03-11 | 4,164 | 22.14 | 39.7 | 42.86 | 43.87 | 57.94 |
| 2 | 2026-03-11 | 2026-03-12 → 2026-04-09 | 4,155 | 22.16 | 39.7 | 42.65 | 42.97 | 55.18 |
| 3 | 2026-04-09 | 2026-04-10 → 2026-05-07 | 4,102 | 38.74 | 44.81 | 47.65 | 47.2 | 61.1 |
| 4 | 2026-05-07 | 2026-05-08 → 2026-06-04 | 3,982 | 37.94 | 44.96 | 48.33 | 48.01 | 61.56 |
| 5 | 2026-06-04 | 2026-06-05 → 2026-07-02 | 4,132 | 34.97 | 43.3 | 46.6 | 47.6 | 62.92 |
| 6 | 2026-07-02 | 2026-07-03 → 2026-07-30 | 4,146 | 40.93 | 47.68 | 48.67 | 48.28 | 64.17 |

## Couverture par boutique

Le modèle global bat le moteur statistique sur **150 boutiques sur 151** (99 %).

Les cinq boutiques où il perd le plus — à surveiller, le repli statistique reste disponible pour elles :

| Boutique | CA moyen | WAPE global | WAPE Holt-Winters | Écart |
|---|---:|---:|---:|---:|
| `I14` | 1809.4 | 52.85 | 33.22 | +19.63 |
| `M10` | 3473.9 | 11.89 | 13.56 | -1.67 |
| `I60` | 710.6 | 24.98 | 28.09 | -3.11 |
| `S20` | 549.6 | 25.46 | 28.63 | -3.17 |
| `S47` | 1642.4 | 22.73 | 26.22 | -3.49 |

*Backtest exécuté en 906.3 s.*
