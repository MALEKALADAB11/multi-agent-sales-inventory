# Gaps de données — état après la refonte du 2026-07-07

Ce document liste ce qui **reste** à combler côté données après le chantier
« monolithe + migrations » (branche `refactor/monolith-v2`). Les gaps résolus
sont rappelés en bas pour mémoire.

## Priorité 1 — impacte les décisions agents

| # | Gap | Table(s) | Volumétrie actuelle | Correctif proposé |
|---|-----|----------|--------------------:|-------------------|
| 1 | Rosters conseillers en dur dans le code (fallback) | `app/sales/data/json_service.py` `_AGENTS`/`_STORE_NAMES`/`_OBJECTIFS_DEFAUT` | 3 boutiques hardcodées | Lire `sales.agents` + `sales.boutiques` + `sales.objectifs` (les données existent : 699 agents, 201 boutiques) ; supprimer les dicts |
| 2 | Contenu coaching quasi vide | `coaching.coaching_events` seule table peuplée (105 lignes) | 1 table / schéma | Générer des événements de coaching depuis les cycles réels (`public.agent_cycles`, 
lien advisor) ; sinon supprimer le schéma et pointer vers `public.coach_interactions` |
| 3 | `demand_forecast` seedé synthétiquement | `inventory.demand_forecast` | seed mince | Alimenter par le forecaster TimesFM en batch nocturne (cron) au lieu du seed |
| 4 | Multi-boutiques : volume temps réel sur 1 seule boutique | `sales.transactions_rt` | I63 seulement (simulateur) | Paramétrer le simulateur pour N boutiques (`DEFAULT_STORE_ID` + liste) ; désormais possible car le pass-through STORE_MAP est en place |

## Priorité 2 — robustesse / réalisme

| # | Gap | Table(s) | Volumétrie | Correctif proposé |
|---|-----|----------|-----------:|-------------------|
| 5 | Churn client jamais implémenté | (table supprimée au cleanup) | 0 | Ne recréer `customer.churn_signals` que si un agent la consomme ; définir d'abord le cas d'usage |
| 6 | `serial_numbers` / `transfers` seeds minces | `supply.serial_numbers`, `supply.transfers` | seed synthétique | Générer les numéros de série à la réception PO (hook dans `update_status` RECU) ; transfers alimentés par un futur flux inter-boutiques |
| 7 | Profondeur market limitée | `market.competitor_pricing` (100), `mnp_flows` (110) | statique | Rafraîchissement périodique (scraper prix concurrents existe : `stratege/scraper.py`) |
| 8 | KPI weekly/monthly non recalculés | `public.weekly_kpi_summary`, `telco_targets_monthly` | seed | Job de recalcul hebdo depuis `sales.transactions` |

## Priorité 3 — hygiène

| # | Gap | Détail |
|---|-----|--------|
| 9 | `sales.transactions_rt.cod_prod` mal nommé | C'est un `sku` (FK vers `sales.produits.sku` posée). Renommage = migration + code + vues dépendantes ; faible valeur, à faire à l'occasion |
| 10 | `inventory.product_master.category` = codes numériques | Catégories réelles dans `sales.produits.categorie`/flags ; unifier lors d'une future consolidation produit |
| 11 | Écart catalogues | `sales.produits` 4 593 SKUs vs `inventory.product_master` 4 178 (FK ok, mais 415 SKUs sans attributs supply) — compléter product_master ou dériver les attributs des flags produits |

## Résolus par ce chantier (2026-07-07)

- ✅ Référentiel fournisseur↔produit : `supply.supplier_products` (migration 0004,
  seed 1 370 lignes, 1 040/1 040 SKUs stockables actifs couverts, 1 préféré/SKU)
  — **câblé** dans les 2 chemins de création de PO (sélection préféré → lead time
  min, MOQ appliquée).
- ✅ Chaîne causale : `recommendations.alert_id` (rattachement auto à l'alerte
  pending du même sku/boutique), `agent_run_id` INT + FK sur alerts et
  recommendations, `purchase_orders.recommendation_id` (existait).
- ✅ Boucle réception PO : RECU/RECU_PARTIEL incrémente `inventory.stock_levels`
  + trace `supply.stock_movements` (type `RECEPTION_BC`, reference_type `BC`,
  reference_id = po_id).
- ✅ `des_produit` corrompu (SKU brut) : 8 357 lignes réparées (migration 0003)
  + source du bug corrigée dans le simulateur (résolution noms via `sales.produits`).
- ✅ 16 FK ajoutées (48 au total), orphelins purgés, types normalisés
  (`agent_run_id`, `demand_forecast.sku` TEXT→INT).
- ✅ Hardcode I63 éliminé (config `DEFAULT_STORE_ID`, pass-through multi-boutiques,
  prompts paramétrés).
- ✅ Zéro CSV : toutes les données vivent dans PostgreSQL ; seeds et scripts
  (embed RAG inclus) lisent la base.
