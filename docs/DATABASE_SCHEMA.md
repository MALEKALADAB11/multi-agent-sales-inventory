# Schema de base de donnees - Ooredoo Sales & Inventory AI

**Base : 2026-07-06 (introspection) + refonte 2026-07-07 (migrations Alembic 0001-0007).**

> ⚠️ **Depuis le 2026-07-07, l'arbre Alembic unique `db/migrations/` (racine du
> repo) est LA source de vérité du schéma.** La révision `0001` est une baseline
> générée par introspection de la base vivante ; toute évolution passe par une
> nouvelle révision (`alembic upgrade head`). L'application ne crée plus aucune
> table au runtime (`app/core/schema_check.py` vérifie la révision au boot).
> Les corps de tables ci-dessous datent de l'introspection du 2026-07-06 —
> voir la section [Refonte 2026-07-07](#refonte-2026-07-07-migrations-0002-0007)
> pour les changements apportés depuis (nouvelle table `supply.supplier_products`,
> colonnes/types/FK modifiés).

**Perimetre (après refonte)** : 8 schemas applicatifs (dont `monitoring`, 4 vues),
51 tables, **16 vues** (voir [Vues](#vues)), 48 cles etrangeres declarees.

## Sommaire

- [Schema `coaching`](#schema-coaching) (1 tables)
- [Schema `customer`](#schema-customer) (2 tables)
- [Schema `inventory`](#schema-inventory) (12 tables)
- [Schema `market`](#schema-market) (5 tables)
- [Schema `public`](#schema-public) (17 tables)
- [Schema `sales`](#schema-sales) (7 tables)
- [Schema `supply`](#schema-supply) (6 tables)
- [Carte globale des relations (cles etrangeres)](#carte-globale-des-relations-cles-etrangeres)
- [Historique du nettoyage du 2026-07-06](#historique-du-nettoyage-du-2026-07-06)
- [Vues (16)](#vues)
- [Refonte 2026-07-07 (migrations 0002-0007)](#refonte-2026-07-07-migrations-0002-0007)

---

## Schema `coaching`

Coaching commercial : evenements de coaching actifs (coaching_events). Les tables agent_memory/coaching_recommendations/escalations/hitl_requests ont ete retirees le 2026-07-06 (0 ligne, 0 reference code, superseees par public.agent_memory et public.hitl_reviews).

### `coaching.coaching_events`

*Lignes (COUNT exact, 2026-07-06) : 105*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | UUID | non | `uuid_generate_v4()` | PK |
| `advisor_id` | INT4 | non | `` |  |
| `store_id` | VARCHAR(50) | non | `` |  |
| `cycle_id` | VARCHAR(100) | oui | `` |  |
| `urgency_level` | VARCHAR(10) | non | `` |  |
| `urgency_score` | NUMERIC(5,2) | oui | `0` |  |
| `gap_pct` | NUMERIC(7,2) | oui | `` |  |
| `gap_amount` | NUMERIC(12,2) | oui | `` |  |
| `forecast_eod` | NUMERIC(12,2) | oui | `` |  |
| `advice_text` | TEXT | oui | `` |  |
| `produit_a_pousser` | VARCHAR(200) | oui | `` |  |
| `produit_a_eviter` | VARCHAR(200) | oui | `` |  |
| `strategie` | TEXT | oui | `` |  |
| `cause_racine` | TEXT | oui | `` |  |
| `rag_used` | BOOL | oui | `false` |  |
| `nb_rag_scripts` | INT4 | oui | `0` |  |
| `script_ids` | JSONB | oui | `'[]'::jsonb` |  |
| `context_hash` | VARCHAR(64) | oui | `` |  |
| `weather_label` | VARCHAR(100) | oui | `` |  |
| `weather_temp_c` | NUMERIC(4,1) | oui | `` |  |
| `weather_effect` | NUMERIC(5,2) | oui | `` |  |
| `event_name` | VARCHAR(200) | oui | `` |  |
| `event_proximity_km` | NUMERIC(6,2) | oui | `` |  |
| `guardrail_status` | VARCHAR(20) | oui | `'APPROVE'::character varying` |  |
| `guardrail_rule` | VARCHAR(100) | oui | `` |  |
| `feedback_score` | INT4 | oui | `` |  |
| `was_effective` | BOOL | oui | `` |  |
| `ca_after_coaching` | NUMERIC(12,2) | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes CHECK** :
- `coaching_events_feedback_score_check` : CHECK (((feedback_score >= 1) AND (feedback_score <= 5)))
- `coaching_events_guardrail_status_check` : CHECK (((guardrail_status)::text = ANY ((ARRAY['APPROVE'::character varying, 'BLOCK'::character varying, 'REWRITE'::character varying])::text[])))
- `coaching_events_urgency_level_check` : CHECK (((urgency_level)::text = ANY ((ARRAY['HIGH'::character varying, 'MEDIUM'::character varying, 'LOW'::character varying])::text[])))

**Index (hors PK)** :
- `idx_ce_advisor`
- `idx_ce_cycle`
- `idx_ce_rag`
- `idx_ce_store`
- `idx_ce_urgency`


---

## Schema `customer`

Donnees client : NPS/CSAT, segmentation. churn_signals retiree le 2026-07-06 (jamais implementee).

### `customer.nps_csat`

*Lignes (COUNT exact, 2026-07-06) : 342*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('customer.nps_csat_id_seq'::regclass)` | PK |
| `store_id` | VARCHAR(50) | oui | `` |  |
| `agent_id` | INT4 | oui | `` |  |
| `feedback_date` | DATE | non | `` |  |
| `type_enquete` | VARCHAR(10) | non | `` |  |
| `score` | NUMERIC(5,2) | oui | `` |  |
| `verbatim` | TEXT | oui | `` |  |
| `categorie_motif` | VARCHAR(100) | oui | `` |  |
| `canal` | VARCHAR(20) | oui | `'POST_VENTE'::character varying` |  |
| `resolu` | BOOL | oui | `false` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes CHECK** :
- `nps_csat_type_enquete_check` : CHECK (((type_enquete)::text = ANY ((ARRAY['NPS'::character varying, 'CSAT'::character varying, 'CES'::character varying])::text[])))

**Index (hors PK)** :
- `idx_nps_store_date`


### `customer.segments`

*Lignes (COUNT exact, 2026-07-06) : 8*

**Cle primaire** : `segment_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `segment_id` | VARCHAR(20) | non | `` | PK |
| `libelle` | VARCHAR(100) | non | `` |  |
| `description` | TEXT | oui | `` |  |
| `arpu_moyen_tnd` | NUMERIC(10,2) | oui | `` |  |
| `arpu_std_tnd` | NUMERIC(10,2) | oui | `` |  |
| `churn_rate_base` | NUMERIC(6,4) | oui | `` |  |
| `duree_vie_mois` | INT4 | oui | `` |  |
| `canal_prefere` | VARCHAR(20) | oui | `` |  |
| `products_preferes` | JSONB | oui | `` |  |
| `nb_clients_estime` | INT4 | oui | `` |  |
| `poids_marche_pct` | NUMERIC(6,3) | oui | `` |  |
| `actif` | BOOL | oui | `true` |  |


---

## Schema `inventory`

Coeur stock & decisions : produits, niveaux de stock, alertes, recommandations, runs d'agents.

### `inventory.agent_runs`

*Lignes (COUNT exact, 2026-07-06) : 63 309*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('inventory.agent_runs_id_seq'::regclass)` | PK |
| `cycle_id` | TEXT | oui | `` |  |
| `agent_name` | TEXT | oui | `` |  |
| `store_id` | TEXT | oui | `` |  |
| `sku` | TEXT | oui | `` |  |
| `started_at` | TIMESTAMP | oui | `now()` |  |
| `completed_at` | TIMESTAMP | oui | `` |  |
| `duration_ms` | FLOAT8 | oui | `` |  |
| `status` | TEXT | oui | `'running'::text` |  |
| `input_summary` | JSONB | oui | `` |  |
| `output_summary` | JSONB | oui | `` |  |
| `error_message` | TEXT | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `items_processed` | INT4 | oui | `0` |  |
| `items_succeeded` | INT4 | oui | `0` |  |
| `items_failed` | INT4 | oui | `0` |  |
| `batch_id` | TEXT | oui | `` |  |
| `alerts_generated` | INT4 | oui | `0` |  |
| `recommendations_generated` | INT4 | oui | `0` |  |


### `inventory.alerts`

*Lignes (COUNT exact, 2026-07-06) : 144*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('inventory.alerts_id_seq'::regclass)` | PK |
| `store_id` | TEXT | oui | `` | FK -> `sales.boutiques.store_id` |
| `sku` | INT4 | oui | `` | FK -> `sales.produits.sku` |
| `alert_type` | TEXT | oui | `'stockout_risk'::text` |  |
| `severity` | TEXT | oui | `'medium'::text` |  |
| `message` | TEXT | oui | `` |  |
| `status` | TEXT | oui | `'pending'::text` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `resolved_at` | TIMESTAMP | oui | `` |  |
| `triggered_at` | TIMESTAMP | oui | `now()` |  |
| `recommended_action` | TEXT | oui | `` |  |
| `agent_run_id` | TEXT | oui | `` |  |

**Contraintes CHECK** :
- `alerts_status_check` : CHECK ((status = ANY (ARRAY['pending'::text, 'acknowledged'::text, 'validated'::text, 'rejected'::text, 'dismissed'::text, 'resolved'::text])))

**References vers d'autres tables** :
- `store_id` -> `sales.boutiques.store_id`
- `sku` -> `sales.produits.sku`

**Index (hors PK)** :
- `idx_alerts_status`
- `idx_alerts_store`


### `inventory.business_objectives`

*Lignes (COUNT exact, 2026-07-06) : 6*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('inventory.business_objectives_id_seq'::regclass)` | PK |
| `objective_type` | TEXT | non | `'balanced'::text` |  |
| `label` | TEXT | oui | `` |  |
| `description` | TEXT | oui | `` |  |
| `is_active` | BOOL | oui | `false` |  |
| `priority` | INT4 | oui | `1` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |


### `inventory.context_adjustments`

*Lignes (COUNT exact, 2026-07-06) : 1 907*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('inventory.context_adjustments_id_seq'::regclass)` | PK |
| `sku` | INT4 | non | `` | FK -> `sales.produits.sku` |
| `store_id` | TEXT | non | `` | FK -> `sales.boutiques.store_id` |
| `demand_uplift_pct` | NUMERIC(8,2) | oui | `0` |  |
| `adjustment_source` | TEXT | oui | `` |  |
| `weather_impact` | NUMERIC(5,2) | oui | `` |  |
| `promo_impact` | NUMERIC(5,2) | oui | `` |  |
| `event_impact` | NUMERIC(5,2) | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `valid_from` | DATE | oui | `CURRENT_DATE` |  |
| `valid_to` | DATE | oui | `(CURRENT_DATE + 7)` |  |
| `confidence` | NUMERIC(5,3) | oui | `0.5` |  |
| `dominant_signal` | TEXT | oui | `` |  |
| `signals` | JSONB | oui | `` |  |
| `holiday_impact` | NUMERIC(5,2) | oui | `0` |  |
| `category` | TEXT | oui | `` |  |
| `store_name` | TEXT | oui | `` |  |
| `interpretation` | TEXT | oui | `` |  |
| `agent_run_id` | INT4 | oui | `` |  |

**Contraintes UNIQUE** :
- `ctx_adj_unique` sur (sku, store_id, valid_from)

**References vers d'autres tables** :
- `sku` -> `sales.produits.sku`
- `store_id` -> `sales.boutiques.store_id`

**Index (hors PK)** :
- `ctx_adj_unique`
- `idx_ctx_adj_sku_store`


### `inventory.demand_forecast`

*Lignes (COUNT exact, 2026-07-06, après seed) : 840*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('inventory.demand_forecast_id_seq'::regclass)` | PK |
| `sku` | TEXT | non | `` |  |
| `store_id` | TEXT | non | `` |  |
| `forecast_date` | DATE | non | `` |  |
| `demand_24h` | NUMERIC(10,2) | oui | `` |  |
| `confidence_low` | NUMERIC(10,2) | oui | `` |  |
| `confidence_high` | NUMERIC(10,2) | oui | `` |  |
| `model_version` | TEXT | oui | `'timesfm-v1'::text` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes UNIQUE** :
- `demand_forecast_sku_store_id_forecast_date_key` sur (sku, store_id, forecast_date)

**Index (hors PK)** :
- `demand_forecast_sku_store_id_forecast_date_key`


### `inventory.events`

*Lignes (COUNT exact, 2026-07-06, après seed) : 24*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('inventory.events_id_seq'::regclass)` | PK |
| `event_name` | TEXT | oui | `` |  |
| `event_type` | TEXT | oui | `` |  |
| `start_date` | DATE | oui | `` |  |
| `end_date` | DATE | oui | `` |  |
| `sku` | INT4 | oui | `` |  |
| `store_id` | TEXT | oui | `` |  |
| `impact_pct` | NUMERIC(5,2) | oui | `0` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `affected_categories` | TEXT | oui | `` |  |
| `estimated_uplift` | NUMERIC(5,2) | oui | `0` |  |
| `estimated_uplift_pct` | NUMERIC(5,2) | oui | `0` |  |
| `scope` | TEXT | oui | `` |  |

**Index (hors PK)** :
- `idx_events_dates`


### `inventory.product_master`

*Lignes (COUNT exact, 2026-07-06) : 4 178*

**Cle primaire** : `sku`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `sku` | INT4 | non | `` | PK; FK -> `sales.produits.sku` |
| `product_name` | TEXT | oui | `` |  |
| `category` | TEXT | oui | `` |  |
| `unit_cost` | NUMERIC(10,2) | oui | `` |  |
| `unit_price` | NUMERIC(10,2) | oui | `` |  |
| `lead_time_days` | INT4 | oui | `` |  |
| `lead_time_std` | INT4 | oui | `` |  |
| `moq` | INT4 | oui | `1` |  |
| `holding_cost_pct` | NUMERIC(5,2) | oui | `` |  |
| `order_cost` | NUMERIC(10,2) | oui | `` |  |
| `lifecycle_stage` | TEXT | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `updated_at` | TIMESTAMP | oui | `now()` |  |

**References vers d'autres tables** :
- `sku` -> `sales.produits.sku`

**Index (hors PK)** :
- `idx_product_master_category`
- `idx_product_master_lifecycle`


### `inventory.promotions`

*Lignes (COUNT exact, 2026-07-06) : 27*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT8 | non | `nextval('inventory.promotions_id_seq'::regclass)` | PK |
| `promo_id` | TEXT | non | `` |  |
| `promo_name` | TEXT | oui | `` |  |
| `start_date` | DATE | non | `` |  |
| `end_date` | DATE | non | `` |  |
| `sku` | INT4 | oui | `` |  |
| `product_name` | TEXT | oui | `` |  |
| `category` | TEXT | oui | `` |  |
| `discount_pct` | NUMERIC(5,2) | oui | `0` |  |
| `promo_type` | TEXT | oui | `` |  |
| `scope` | TEXT | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes UNIQUE** :
- `promotions_promo_id_key` sur (promo_id)

**Index (hors PK)** :
- `idx_promotions_dates`
- `idx_promotions_sku`
- `promotions_promo_id_key`


### `inventory.recommendations`

*Lignes (COUNT exact, 2026-07-06) : 511*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | UUID | non | `gen_random_uuid()` | PK |
| `sku` | INT4 | non | `` | FK -> `sales.produits.sku` |
| `store_id` | TEXT | non | `` | FK -> `sales.boutiques.store_id` |
| `recommendation_type` | TEXT | oui | `` |  |
| `action` | TEXT | oui | `` |  |
| `order_qty` | INT4 | oui | `` |  |
| `urgency` | TEXT | oui | `` |  |
| `confidence` | NUMERIC(5,3) | oui | `` |  |
| `recommendation_text` | TEXT | oui | `` |  |
| `trade_offs` | TEXT | oui | `` |  |
| `escalate_to_human` | BOOL | oui | `false` |  |
| `escalation_reason` | TEXT | oui | `` |  |
| `status` | TEXT | oui | `'pending'::text` |  |
| `decided_by` | TEXT | oui | `` |  |
| `decided_at` | TIMESTAMP | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `agent_run_id` | TEXT | oui | `` |  |
| `order_cost` | NUMERIC(10,2) | oui | `` |  |
| `holding_cost` | NUMERIC(10,2) | oui | `` |  |
| `suggested_quantity` | INT4 | oui | `` |  |

**Contraintes CHECK** :
- `reco_status_check` : CHECK ((status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text, 'executed'::text, 'cancelled'::text])))

**References vers d'autres tables** :
- `sku` -> `sales.produits.sku`
- `store_id` -> `sales.boutiques.store_id`

**Referencee par** :
- `supply.purchase_orders.recommendation_id`

**Index (hors PK)** :
- `idx_rec_sku_store`
- `idx_rec_status`
- `idx_recommendations_active`
- `idx_recommendations_store_status`
- `uq_reco_pending_sku_store`


### `inventory.sales_history`

*Lignes (COUNT exact, 2026-07-06) : 693 954*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT8 | non | `nextval('inventory.sales_history_id_seq'::regclass)` | PK |
| `record_date` | DATE | non | `` |  |
| `store_id` | TEXT | non | `` |  |
| `store_name` | TEXT | oui | `` |  |
| `region` | TEXT | oui | `` |  |
| `sku` | INT4 | non | `` |  |
| `product_name` | TEXT | oui | `` |  |
| `category` | TEXT | oui | `` |  |
| `quantity_sold` | INT4 | oui | `0` |  |
| `revenue` | NUMERIC(12,2) | oui | `0` |  |
| `unit_price` | NUMERIC(10,2) | oui | `` |  |
| `is_promo` | BOOL | oui | `false` |  |
| `event_name` | TEXT | oui | `` |  |
| `event_type` | TEXT | oui | `` |  |
| `season` | TEXT | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `promo_type` | VARCHAR(50) | oui | `` |  |
| `day_of_week` | INT2 | oui | `` |  |
| `week_of_year` | INT2 | oui | `` |  |
| `month_num` | INT2 | oui | `` |  |
| `year_num` | INT2 | oui | `` |  |
| `is_weekend` | BOOL | oui | `false` |  |
| `is_event_day` | BOOL | oui | `false` |  |
| `event_intensite` | VARCHAR(10) | oui | `` |  |
| `uplift_factor` | NUMERIC(6,4) | oui | `1.0` |  |

**Index (hors PK)** :
- `idx_sh_date_cat`
- `idx_sh_event`
- `idx_sh_store_sku_date`
- `idx_slh_date_store`
- `idx_slh_sku`


### `inventory.stock_history`

*Lignes (COUNT exact, 2026-07-06) : 844 987*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT8 | non | `nextval('inventory.stock_history_id_seq'::regclass)` | PK |
| `record_date` | DATE | non | `` |  |
| `store_id` | TEXT | non | `` |  |
| `store_name` | TEXT | oui | `` |  |
| `region` | TEXT | oui | `` |  |
| `sku` | INT4 | non | `` |  |
| `product_name` | TEXT | oui | `` |  |
| `category` | TEXT | oui | `` |  |
| `stock_level` | INT4 | oui | `0` |  |
| `is_stockout` | BOOL | oui | `false` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Index (hors PK)** :
- `idx_sh_date_store`
- `idx_sh_sku`


### `inventory.stock_levels`

*Lignes (COUNT exact, 2026-07-06) : 46 244*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT8 | non | `nextval('inventory.stock_levels_id_seq'::regclass)` | PK |
| `store_id` | TEXT | non | `` | FK -> `sales.boutiques.store_id` |
| `sku` | INT4 | non | `` | FK -> `sales.produits.sku` |
| `quantity` | INT4 | oui | `` |  |
| `quantity_reserved` | INT4 | oui | `0` |  |
| `quantity_available` | INT4 | oui | `` | GENEREE ((quantity - quantity_reserved)) |
| `last_received` | DATE | oui | `` |  |
| `last_sold` | DATE | oui | `` |  |
| `updated_at` | TIMESTAMP | oui | `now()` |  |
| `remaining_days_of_stock` | FLOAT8 | oui | `` |  |
| `last_updated` | TIMESTAMP | oui | `now()` |  |

**Contraintes UNIQUE** :
- `stock_levels_sku_store` sur (sku, store_id)

**References vers d'autres tables** :
- `store_id` -> `sales.boutiques.store_id`
- `sku` -> `sales.produits.sku`

**Index (hors PK)** :
- `idx_sl_store_sku`
- `idx_stock_levels_store_qty`
- `idx_stock_low_qty`
- `stock_levels_sku_store`


---

## Schema `market`

Intelligence marche : evenements concurrentiels, pricing, flux MNP, patterns saisonniers.

### `market.competitor_pricing`

*Lignes (COUNT exact, 2026-07-06) : 100*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('market.competitor_pricing_id_seq'::regclass)` | PK |
| `concurrent_id` | VARCHAR(20) | non | `` | FK -> `market.competitors.concurrent_id` |
| `categorie` | VARCHAR(50) | non | `` |  |
| `produit_type` | VARCHAR(200) | non | `` |  |
| `donnees_go` | NUMERIC(8,1) | oui | `` |  |
| `minutes_voix` | INT4 | oui | `` |  |
| `sms_count` | INT4 | oui | `` |  |
| `prix_ht` | NUMERIC(10,2) | oui | `` |  |
| `prix_ttc` | NUMERIC(10,2) | oui | `` |  |
| `engagement_mois` | INT4 | oui | `0` |  |
| `date_releve` | DATE | non | `` |  |
| `source` | VARCHAR(30) | oui | `'WEB'::character varying` |  |
| `actif` | BOOL | oui | `true` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**References vers d'autres tables** :
- `concurrent_id` -> `market.competitors.concurrent_id`

**Index (hors PK)** :
- `idx_competitor_pricing_cat`
- `idx_competitor_pricing_date`


### `market.competitors`

*Lignes (COUNT exact, 2026-07-06) : 3*

**Cle primaire** : `concurrent_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `concurrent_id` | VARCHAR(20) | non | `` | PK |
| `nom` | VARCHAR(100) | non | `` |  |
| `code_operateur` | VARCHAR(10) | oui | `` |  |
| `pays` | VARCHAR(50) | oui | `'Tunisia'::character varying` |  |
| `part_marche_pct` | NUMERIC(6,3) | oui | `` |  |
| `nb_abonnes` | INT8 | oui | `` |  |
| `positionnement` | VARCHAR(20) | oui | `'MID'::character varying` |  |
| `points_forts` | JSONB | oui | `` |  |
| `points_faibles` | JSONB | oui | `` |  |
| `date_entree_marche` | DATE | oui | `` |  |
| `actif` | BOOL | oui | `true` |  |
| `updated_at` | TIMESTAMP | oui | `now()` |  |

**Referencee par** :
- `market.competitor_pricing.concurrent_id`


### `market.events`

*Lignes (COUNT exact, 2026-07-06) : 165*

**Cle primaire** : `event_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `event_id` | UUID | non | `uuid_generate_v4()` | PK |
| `event_name` | VARCHAR(200) | non | `` |  |
| `event_type` | VARCHAR(50) | non | `` |  |
| `sous_type` | VARCHAR(100) | oui | `` |  |
| `start_date` | DATE | non | `` |  |
| `end_date` | DATE | non | `` |  |
| `annee` | INT4 | oui | `` | GENEREE ((EXTRACT(year FROM start_date))::integer) |
| `scope` | VARCHAR(20) | oui | `'NATIONAL'::character varying` |  |
| `region_ids` | JSONB | oui | `` |  |
| `categories_impactees` | JSONB | oui | `` |  |
| `uplift_terminal` | NUMERIC(6,2) | oui | `0` |  |
| `uplift_forfait` | NUMERIC(6,2) | oui | `0` |  |
| `uplift_sim` | NUMERIC(6,2) | oui | `0` |  |
| `uplift_recharge` | NUMERIC(6,2) | oui | `0` |  |
| `uplift_accessoire` | NUMERIC(6,2) | oui | `0` |  |
| `intensite` | VARCHAR(10) | oui | `'MEDIUM'::character varying` |  |
| `source_donnee` | VARCHAR(50) | oui | `'HISTORIQUE'::character varying` |  |
| `note_strategie` | TEXT | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes CHECK** :
- `events_intensite_check` : CHECK (((intensite)::text = ANY ((ARRAY['LOW'::character varying, 'MEDIUM'::character varying, 'HIGH'::character varying, 'EXTREME'::character varying])::text[])))
- `events_event_type_check` : CHECK (((event_type)::text = ANY ((ARRAY['RELIGIEUX'::character varying, 'SCOLAIRE'::character varying, 'SPORTIF'::character varying, 'COMMERCIAL'::character varying, 'NATIONAL'::character varying, 'CONCURRENTIEL'::character varying, 'METEO'::character varying, 'RESEAU'::character varying])::text[])))

**Index (hors PK)** :
- `idx_market_events_dates`
- `idx_market_events_type`


### `market.mnp_flows`

*Lignes (COUNT exact, 2026-07-06) : 110*

**Cle primaire** : `mnp_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `mnp_id` | UUID | non | `uuid_generate_v4()` | PK |
| `direction` | VARCHAR(10) | non | `` |  |
| `operateur_origine` | VARCHAR(20) | oui | `` |  |
| `operateur_destination` | VARCHAR(20) | oui | `` |  |
| `mois` | DATE | non | `` |  |
| `volume` | INT4 | non | `0` |  |
| `categorie_client` | VARCHAR(20) | oui | `'RESI'::character varying` |  |
| `raison_principale` | VARCHAR(100) | oui | `` |  |
| `wilaya` | VARCHAR(100) | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes CHECK** :
- `mnp_flows_direction_check` : CHECK (((direction)::text = ANY ((ARRAY['PORT_IN'::character varying, 'PORT_OUT'::character varying])::text[])))

**Index (hors PK)** :
- `idx_mnp_mois`


### `market.seasonal_patterns`

*Lignes (COUNT exact, 2026-07-06) : 42*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('market.seasonal_patterns_id_seq'::regclass)` | PK |
| `categorie` | VARCHAR(50) | non | `` |  |
| `mois` | INT4 | non | `` |  |
| `semaine_mois` | INT4 | oui | `` |  |
| `jour_semaine` | INT4 | oui | `` |  |
| `heure_debut` | INT4 | oui | `` |  |
| `heure_fin` | INT4 | oui | `` |  |
| `facteur_demande` | NUMERIC(6,4) | non | `1.0` |  |
| `facteur_std` | NUMERIC(6,4) | oui | `0.1` |  |
| `nb_annees_data` | INT4 | oui | `2` |  |
| `confidence` | VARCHAR(10) | oui | `'MEDIUM'::character varying` |  |
| `notes` | VARCHAR(200) | oui | `` |  |
| `updated_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes CHECK** :
- `seasonal_patterns_heure_debut_check` : CHECK (((heure_debut >= 0) AND (heure_debut <= 23)))
- `seasonal_patterns_heure_fin_check` : CHECK (((heure_fin >= 0) AND (heure_fin <= 23)))
- `seasonal_patterns_jour_semaine_check` : CHECK (((jour_semaine >= 0) AND (jour_semaine <= 6)))
- `seasonal_patterns_mois_check` : CHECK (((mois >= 1) AND (mois <= 12)))
- `seasonal_patterns_confidence_check` : CHECK (((confidence)::text = ANY ((ARRAY['LOW'::character varying, 'MEDIUM'::character varying, 'HIGH'::character varying, 'VERY_HIGH'::character varying])::text[])))
- `seasonal_patterns_semaine_mois_check` : CHECK (((semaine_mois >= 1) AND (semaine_mois <= 5)))

**Index (hors PK)** :
- `idx_seasonal_unique`


---

## Schema `public`

Schema par defaut Postgres : logging agent transverse actif (agent_cycles, agent_logs, agent_memory, agent_kpi_daily...), auth, HITL (hitl_reviews), alembic_version.

### `public.agent_cycles`

*Lignes (COUNT exact, 2026-07-06) : 1 147*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('agent_cycles_id_seq'::regclass)` | PK |
| `cycle_id` | VARCHAR(50) | oui | `` |  |
| `store_id` | VARCHAR(10) | oui | `'I63'::character varying` |  |
| `triggered_by` | VARCHAR(20) | oui | `` |  |
| `urgency_level` | VARCHAR(10) | oui | `` |  |
| `urgency_score` | FLOAT8 | oui | `` |  |
| `gap_pct` | FLOAT8 | oui | `` |  |
| `gap_amount` | FLOAT8 | oui | `` |  |
| `ca_today` | FLOAT8 | oui | `` |  |
| `ca_target` | FLOAT8 | oui | `` |  |
| `forecast_eod` | FLOAT8 | oui | `` |  |
| `analyst_summary` | TEXT | oui | `` |  |
| `strategie` | TEXT | oui | `` |  |
| `nb_actions` | INT4 | oui | `0` |  |
| `cause_racine` | TEXT | oui | `` |  |
| `rag_used` | BOOL | oui | `false` |  |
| `nb_rag_scripts` | INT4 | oui | `0` |  |
| `weather_label` | VARCHAR(50) | oui | `` |  |
| `weather_effect` | FLOAT8 | oui | `` |  |
| `total_ms` | FLOAT8 | oui | `` |  |
| `nodes_executed` | INT4 | oui | `0` |  |
| `errors_count` | INT4 | oui | `0` |  |
| `status` | VARCHAR(20) | oui | `'completed'::character varying` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes UNIQUE** :
- `agent_cycles_cycle_id_key` sur (cycle_id)

**Index (hors PK)** :
- `agent_cycles_cycle_id_key`
- `idx_cycles_created`
- `idx_cycles_store`
- `idx_cycles_urgency`


### `public.agent_errors`

*Lignes (COUNT exact, 2026-07-06) : 19*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('agent_errors_id_seq'::regclass)` | PK |
| `cycle_id` | VARCHAR(50) | oui | `` |  |
| `store_id` | VARCHAR(10) | oui | `'I63'::character varying` |  |
| `agent_name` | VARCHAR(30) | oui | `` |  |
| `node_name` | VARCHAR(50) | oui | `` |  |
| `error_type` | VARCHAR(50) | oui | `` |  |
| `error_msg` | TEXT | oui | `` |  |
| `traceback_txt` | TEXT | oui | `` |  |
| `context` | JSONB | oui | `` |  |
| `resolved` | BOOL | oui | `false` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Index (hors PK)** :
- `idx_errors_created`
- `idx_errors_cycle`
- `idx_errors_resolved`
- `idx_errors_type`


### `public.agent_kpi_daily`

*Lignes (COUNT exact, 2026-07-06) : 114 211*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT8 | non | `nextval('agent_kpi_daily_id_seq'::regclass)` | PK |
| `agent_id` | INT4 | non | `` |  |
| `store_id` | VARCHAR(50) | non | `` |  |
| `kpi_date` | DATE | non | `` |  |
| `ca_realise` | NUMERIC(14,2) | oui | `0` |  |
| `ca_cible` | NUMERIC(14,2) | oui | `` |  |
| `gap_ca_pct` | NUMERIC(7,2) | oui | `` |  |
| `nb_transactions` | INT4 | oui | `0` |  |
| `nb_clients_uniques` | INT4 | oui | `0` |  |
| `panier_moyen` | NUMERIC(10,2) | oui | `` |  |
| `nb_forfaits` | INT4 | oui | `0` |  |
| `nb_terminaux` | INT4 | oui | `0` |  |
| `nb_sim_activations` | INT4 | oui | `0` |  |
| `nb_recharges` | INT4 | oui | `0` |  |
| `nb_accessoires` | INT4 | oui | `0` |  |
| `nb_evouchers` | INT4 | oui | `0` |  |
| `nb_postpaye` | INT4 | oui | `0` |  |
| `nb_postpaye_cible` | INT4 | oui | `` |  |
| `gap_postpaye_pct` | NUMERIC(7,2) | oui | `` |  |
| `taux_upsell_accessoire` | NUMERIC(6,4) | oui | `` |  |
| `taux_upsell_assurance` | NUMERIC(6,4) | oui | `` |  |
| `taux_conversion_recharge` | NUMERIC(6,4) | oui | `` |  |
| `ca_terminaux` | NUMERIC(12,2) | oui | `0` |  |
| `ca_forfaits` | NUMERIC(12,2) | oui | `0` |  |
| `ca_sim` | NUMERIC(12,2) | oui | `0` |  |
| `ca_recharges` | NUMERIC(12,2) | oui | `0` |  |
| `ca_accessoires` | NUMERIC(12,2) | oui | `0` |  |
| `rang_boutique` | INT4 | oui | `` |  |
| `rang_region` | INT4 | oui | `` |  |
| `rang_national` | INT4 | oui | `` |  |
| `nb_reclamations` | INT4 | oui | `0` |  |
| `nps_score` | NUMERIC(5,2) | oui | `` |  |
| `coach_score` | NUMERIC(5,2) | oui | `` |  |
| `urgency_level` | VARCHAR(10) | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes UNIQUE** :
- `agent_kpi_daily_agent_id_kpi_date_key` sur (agent_id, kpi_date)

**Contraintes CHECK** :
- `agent_kpi_daily_urgency_level_check` : CHECK (((urgency_level)::text = ANY ((ARRAY['CRITIQUE'::character varying, 'ELEVE'::character varying, 'MODERE'::character varying, 'OK'::character varying])::text[])))

**Index (hors PK)** :
- `agent_kpi_daily_agent_id_kpi_date_key`
- `idx_agent_kpi_agent_date`
- `idx_agent_kpi_gap`
- `idx_agent_kpi_store_date`


### `public.agent_logs`

*Lignes (COUNT exact, 2026-07-06) : 11 900*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('agent_logs_id_seq'::regclass)` | PK |
| `cycle_id` | VARCHAR(50) | oui | `` |  |
| `store_id` | VARCHAR(10) | oui | `'I63'::character varying` |  |
| `agent_name` | VARCHAR(30) | oui | `` |  |
| `node_name` | VARCHAR(50) | oui | `` |  |
| `status` | VARCHAR(20) | oui | `` |  |
| `input_state` | JSONB | oui | `` |  |
| `output_state` | JSONB | oui | `` |  |
| `duration_ms` | FLOAT8 | oui | `` |  |
| `error_msg` | TEXT | oui | `` |  |
| `metadata` | JSONB | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Index (hors PK)** :
- `idx_logs_agent`
- `idx_logs_created`
- `idx_logs_cycle`
- `idx_logs_status`


### `public.agent_memory`

*Lignes (COUNT exact, 2026-07-06) : 939*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('agent_memory_id_seq'::regclass)` | PK |
| `agent_name` | VARCHAR(50) | non | `` |  |
| `store_id` | VARCHAR(50) | non | `` |  |
| `cycle_id` | VARCHAR(100) | oui | `` |  |
| `memory_type` | VARCHAR(50) | non | `` |  |
| `memory_data` | JSONB | non | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Index (hors PK)** :
- `idx_agent_memory_agent_store`


### `public.agent_sessions`

*Lignes (COUNT exact, 2026-07-06) : 2*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | VARCHAR(50) | non | `` | PK |
| `store_id` | VARCHAR(20) | non | `` |  |
| `agent_type` | VARCHAR(30) | oui | `` |  |
| `started_at` | TIMESTAMPTZ | oui | `now()` |  |
| `last_activity` | TIMESTAMPTZ | oui | `now()` |  |
| `status` | VARCHAR(20) | oui | `` |  |
| `memory_state` | JSON | oui | `` |  |
| `external_context` | JSON | oui | `` |  |

**Index (hors PK)** :
- `idx_session_store_time`


### `public.alembic_version`

*Lignes (COUNT exact, 2026-07-06) : 1*

**Cle primaire** : `version_num`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `version_num` | VARCHAR(32) | non | `` | PK |

**Index (hors PK)** :
- `alembic_version_pkc`


### `public.app_sessions`

*Lignes (COUNT exact, 2026-07-06) : 59*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('app_sessions_id_seq'::regclass)` | PK |
| `token` | VARCHAR(64) | non | `` |  |
| `user_id` | VARCHAR(30) | non | `` |  |
| `expires_at` | TIMESTAMP | non | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `last_used` | TIMESTAMP | oui | `now()` |  |
| `ip_address` | VARCHAR(45) | oui | `` |  |

**Contraintes UNIQUE** :
- `app_sessions_token_key` sur (token)

**Index (hors PK)** :
- `app_sessions_token_key`
- `idx_as_exp`
- `idx_as_token`
- `idx_as_user`


### `public.app_users`

*Lignes (COUNT exact, 2026-07-06) : 7*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('app_users_id_seq'::regclass)` | PK |
| `user_id` | VARCHAR(30) | non | `` |  |
| `username` | VARCHAR(50) | non | `` |  |
| `password_hash` | VARCHAR(64) | non | `` |  |
| `full_name` | VARCHAR(100) | oui | `` |  |
| `role` | VARCHAR(20) | non | `` |  |
| `store_id` | VARCHAR(20) | oui | `` |  |
| `store_name` | VARCHAR(100) | oui | `` |  |
| `initials` | VARCHAR(5) | oui | `` |  |
| `color` | VARCHAR(10) | oui | `` |  |
| `advisor_id` | VARCHAR(20) | oui | `` |  |
| `actif` | BOOL | oui | `true` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `last_login` | TIMESTAMP | oui | `` |  |

**Contraintes UNIQUE** :
- `app_users_user_id_key` sur (user_id)
- `app_users_username_key` sur (username)

**Index (hors PK)** :
- `app_users_user_id_key`
- `app_users_username_key`
- `idx_au_store`
- `idx_au_username`


### `public.coach_interactions`

*Lignes (COUNT exact, 2026-07-06) : 255*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('coach_interactions_id_seq'::regclass)` | PK |
| `advisor_name` | VARCHAR(100) | oui | `` |  |
| `store_id` | VARCHAR(20) | oui | `'I63'::character varying` |  |
| `message` | TEXT | oui | `` |  |
| `response` | TEXT | oui | `` |  |
| `gap_pct` | FLOAT8 | oui | `` |  |
| `urgency` | VARCHAR(10) | oui | `` |  |
| `rag_used` | BOOL | oui | `false` |  |
| `nb_rag_scripts` | INT4 | oui | `0` |  |
| `conseil_type` | VARCHAR(30) | oui | `'general'::character varying` |  |
| `confidence` | FLOAT8 | oui | `0.0` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Index (hors PK)** :
- `idx_ci_advisor`
- `idx_ci_created`
- `idx_coach_interactions_advisor_day`


### `public.hitl_reviews`

*Lignes (COUNT exact, 2026-07-06) : 9*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | UUID | non | `gen_random_uuid()` | PK |
| `store_id` | TEXT | non | `` |  |
| `cycle_id` | TEXT | non | `` |  |
| `urgency_level` | TEXT | non | `` |  |
| `gap_pct` | FLOAT8 | non | `` |  |
| `critique_score` | FLOAT8 | non | `` |  |
| `critique_feedback` | TEXT | non | `` |  |
| `strategie_summary` | TEXT | non | `` |  |
| `actions` | JSONB | non | `'[]'::jsonb` |  |
| `source` | TEXT | non | `'sales'::text` |  |
| `status` | TEXT | non | `'pending'::text` |  |
| `approver_name` | TEXT | oui | `` |  |
| `approver_note` | TEXT | oui | `` |  |
| `reviewed_at` | TIMESTAMPTZ | oui | `` |  |
| `created_at` | TIMESTAMPTZ | non | `now()` |  |

**Index (hors PK)** :
- `idx_hitl_reviews_pending`


### `public.rag_feedback`

*Lignes (COUNT exact, 2026-07-06, après seed) : 150*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('rag_feedback_id_seq'::regclass)` | PK |
| `cycle_id` | VARCHAR(50) | oui | `` |  |
| `store_id` | VARCHAR(10) | oui | `'I63'::character varying` |  |
| `agent_name` | VARCHAR(30) | oui | `'stratege'::character varying` |  |
| `query` | TEXT | oui | `` |  |
| `nb_results` | INT4 | oui | `` |  |
| `top_category` | VARCHAR(100) | oui | `` |  |
| `top_score` | FLOAT8 | oui | `` |  |
| `action_used` | TEXT | oui | `` |  |
| `was_useful` | BOOL | oui | `` |  |
| `context` | JSONB | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Index (hors PK)** :
- `idx_rag_agent`
- `idx_rag_created`
- `idx_rag_cycle`


### `public.rag_feedback_metrics`

*Lignes (COUNT exact, 2026-07-06, après seed) : 150*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('rag_feedback_metrics_id_seq'::regclass)` | PK |
| `cycle_id` | VARCHAR | oui | `` |  |
| `store_id` | VARCHAR(20) | oui | `` |  |
| `query` | TEXT | oui | `` |  |
| `nb_results` | INT4 | oui | `` |  |
| `top_category` | VARCHAR(50) | oui | `` |  |
| `top_score` | FLOAT8 | oui | `` |  |
| `action_used` | TEXT | oui | `` |  |
| `was_useful` | BOOL | oui | `` |  |
| `context` | JSON | oui | `` |  |
| `created_at` | TIMESTAMPTZ | oui | `now()` |  |

**Index (hors PK)** :
- `idx_rag_fb_cycle`


### `public.rag_queries`

*Lignes (COUNT exact, 2026-07-06, après seed) : 150*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('rag_queries_id_seq'::regclass)` | PK |
| `cycle_id` | VARCHAR | oui | `` |  |
| `session_id` | VARCHAR(50) | oui | `` |  |
| `query_text` | TEXT | oui | `` |  |
| `nb_results` | INT4 | oui | `` |  |
| `top_result_score` | FLOAT8 | oui | `` |  |
| `top_result_category` | VARCHAR(50) | oui | `` |  |
| `action_selected` | TEXT | oui | `` |  |
| `was_useful` | BOOL | oui | `` |  |
| `created_at` | TIMESTAMPTZ | oui | `now()` |  |

**Index (hors PK)** :
- `idx_rag_session`


### `public.store_kpi_daily`

*Lignes (COUNT exact, 2026-07-06) : 32 431*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT8 | non | `nextval('store_kpi_daily_id_seq'::regclass)` | PK |
| `store_id` | VARCHAR(50) | non | `` |  |
| `kpi_date` | DATE | non | `` |  |
| `ca_realise` | NUMERIC(14,2) | oui | `0` |  |
| `ca_cible` | NUMERIC(14,2) | oui | `` |  |
| `gap_ca_pct` | NUMERIC(7,2) | oui | `` |  |
| `ca_cumul_mois` | NUMERIC(16,2) | oui | `` |  |
| `ca_objectif_mois` | NUMERIC(16,2) | oui | `` |  |
| `nb_transactions` | INT4 | oui | `0` |  |
| `nb_clients` | INT4 | oui | `0` |  |
| `taux_conversion` | NUMERIC(6,4) | oui | `` |  |
| `footfall_estime` | INT4 | oui | `` |  |
| `nb_forfaits` | INT4 | oui | `0` |  |
| `nb_terminaux` | INT4 | oui | `0` |  |
| `nb_sim_activations` | INT4 | oui | `0` |  |
| `nb_recharges` | INT4 | oui | `0` |  |
| `ca_terminaux` | NUMERIC(14,2) | oui | `0` |  |
| `ca_forfaits` | NUMERIC(14,2) | oui | `0` |  |
| `nb_postpaye` | INT4 | oui | `0` |  |
| `nb_postpaye_cible` | INT4 | oui | `` |  |
| `gap_postpaye_pct` | NUMERIC(7,2) | oui | `` |  |
| `nps_score` | NUMERIC(5,2) | oui | `` |  |
| `csat_score` | NUMERIC(5,2) | oui | `` |  |
| `nb_reclamations` | INT4 | oui | `0` |  |
| `nb_ruptures_sku` | INT4 | oui | `0` |  |
| `taux_service_stock` | NUMERIC(6,4) | oui | `1.0` |  |
| `rang_region` | INT4 | oui | `` |  |
| `rang_national` | INT4 | oui | `` |  |
| `nb_agents_actifs` | INT4 | oui | `` |  |
| `ca_par_agent` | NUMERIC(12,2) | oui | `` |  |
| `panier_moyen_store` | NUMERIC(10,2) | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes UNIQUE** :
- `store_kpi_daily_store_id_kpi_date_key` sur (store_id, kpi_date)

**Index (hors PK)** :
- `idx_store_kpi_date`
- `idx_store_kpi_gap`
- `store_kpi_daily_store_id_kpi_date_key`


### `public.telco_targets_monthly`

*Lignes (COUNT exact, 2026-07-06) : 6 917*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT8 | non | `nextval('telco_targets_monthly_id_seq'::regclass)` | PK |
| `store_id` | VARCHAR(50) | non | `` |  |
| `agent_id` | INT4 | oui | `` |  |
| `mois` | INT4 | non | `` |  |
| `annee` | INT4 | non | `` |  |
| `niveau` | VARCHAR(10) | oui | `'AGENT'::character varying` |  |
| `ca_cible_mensuel` | NUMERIC(14,2) | oui | `` |  |
| `ca_cible_s1` | NUMERIC(12,2) | oui | `` |  |
| `ca_cible_s2` | NUMERIC(12,2) | oui | `` |  |
| `ca_cible_s3` | NUMERIC(12,2) | oui | `` |  |
| `ca_cible_s4` | NUMERIC(12,2) | oui | `` |  |
| `activations_totales` | INT4 | oui | `0` |  |
| `activations_postpaye` | INT4 | oui | `0` |  |
| `activations_prepaye` | INT4 | oui | `0` |  |
| `ventes_terminaux` | INT4 | oui | `0` |  |
| `upgrades_data` | INT4 | oui | `0` |  |
| `conversions_recharge_forfait` | INT4 | oui | `0` |  |
| `renouvellements_contrat` | INT4 | oui | `0` |  |
| `nps_cible` | NUMERIC(5,2) | oui | `` |  |
| `taux_reclamation_max` | NUMERIC(6,4) | oui | `` |  |
| `evenements_mois` | JSONB | oui | `` |  |
| `facteur_saisonnier` | NUMERIC(6,4) | oui | `1.0` |  |
| `ajustement_raison` | TEXT | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes CHECK** :
- `telco_targets_monthly_mois_check` : CHECK (((mois >= 1) AND (mois <= 12)))
- `telco_targets_monthly_niveau_check` : CHECK (((niveau)::text = ANY ((ARRAY['AGENT'::character varying, 'BOUTIQUE'::character varying, 'REGION'::character varying])::text[])))

**Index (hors PK)** :
- `idx_targets_agent_month`
- `idx_targets_store_month`


### `public.weekly_kpi_summary`

*Lignes (COUNT exact, 2026-07-06) : 67 028*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT8 | non | `nextval('weekly_kpi_summary_id_seq'::regclass)` | PK |
| `store_id` | VARCHAR(50) | non | `` |  |
| `agent_id` | INT4 | oui | `` |  |
| `annee_semaine` | VARCHAR(8) | non | `` |  |
| `semaine_debut` | DATE | non | `` |  |
| `semaine_fin` | DATE | non | `` |  |
| `niveau` | VARCHAR(10) | oui | `'AGENT'::character varying` |  |
| `ca_semaine` | NUMERIC(14,2) | oui | `0` |  |
| `ca_cible_semaine` | NUMERIC(14,2) | oui | `` |  |
| `gap_semaine_pct` | NUMERIC(7,2) | oui | `` |  |
| `nb_transactions` | INT4 | oui | `0` |  |
| `nb_postpaye` | INT4 | oui | `0` |  |
| `nb_terminaux` | INT4 | oui | `0` |  |
| `nb_forfaits` | INT4 | oui | `0` |  |
| `panier_moyen` | NUMERIC(10,2) | oui | `` |  |
| `top_produit` | VARCHAR(200) | oui | `` |  |
| `top_categorie` | VARCHAR(50) | oui | `` |  |
| `nb_jours_actifs` | INT4 | oui | `6` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes CHECK** :
- `weekly_kpi_summary_niveau_check` : CHECK (((niveau)::text = ANY ((ARRAY['AGENT'::character varying, 'BOUTIQUE'::character varying])::text[])))


---

## Schema `sales`

Ventes : historique, transactions temps reel, catalogue produits cote vente, objectifs.

### `sales.agents`

*Lignes (COUNT exact, 2026-07-06) : 699*

**Cle primaire** : `agent_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `agent_id` | INT4 | non | `` | PK |
| `agent_name` | TEXT | non | `` |  |
| `store_id` | TEXT | non | `` | FK -> `sales.boutiques.store_id` |
| `role` | TEXT | oui | `` |  |
| `phone` | TEXT | oui | `` |  |
| `email` | TEXT | oui | `` |  |
| `performance_level` | TEXT | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `date_embauche` | DATE | oui | `` |  |
| `date_depart` | DATE | oui | `` |  |
| `niveau_certification` | INT4 | oui | `1` |  |
| `quota_mensuel_ca` | NUMERIC(12,2) | oui | `` |  |
| `quota_activations` | INT4 | oui | `60` |  |
| `quota_postpaye` | INT4 | oui | `10` |  |
| `specialisation` | VARCHAR(50) | oui | `` |  |
| `avatar_color` | VARCHAR(7) | oui | `` |  |
| `coach_score` | NUMERIC(5,2) | oui | `0.0` |  |
| `anciennete_mois` | INT4 | oui | `12` |  |

**References vers d'autres tables** :
- `store_id` -> `sales.boutiques.store_id`

**Referencee par** :
- `sales.transactions.agent_id`
- `sales.transactions_rt.agent_id`

**Index (hors PK)** :
- `idx_agents_performance`
- `idx_agents_store`


### `sales.boutiques`

*Lignes (COUNT exact, 2026-07-06) : 201*

**Cle primaire** : `store_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `store_id` | TEXT | non | `` | PK |
| `store_name` | TEXT | non | `` |  |
| `address` | TEXT | oui | `` |  |
| `ville` | TEXT | oui | `` |  |
| `region` | TEXT | oui | `` |  |
| `manager_name` | TEXT | oui | `` |  |
| `phone` | TEXT | oui | `` |  |
| `active` | BOOL | oui | `true` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `type_boutique` | VARCHAR(5) | oui | `` |  |
| `canal` | VARCHAR(20) | oui | `'PHYSIQUE'::character varying` |  |
| `wilaya` | VARCHAR(100) | oui | `` |  |
| `zone_commerciale` | VARCHAR(100) | oui | `` |  |
| `latitude` | NUMERIC(10,7) | oui | `` |  |
| `longitude` | NUMERIC(10,7) | oui | `` |  |
| `capacite_conseillers` | INT4 | oui | `4` |  |
| `date_ouverture` | DATE | oui | `` |  |
| `is_officielle` | BOOL | oui | `false` |  |
| `rang_ca_region` | INT4 | oui | `` |  |
| `email_store` | VARCHAR(200) | oui | `` |  |

**Referencee par** :
- `inventory.alerts.store_id`
- `inventory.context_adjustments.store_id`
- `inventory.recommendations.store_id`
- `inventory.stock_levels.store_id`
- `sales.agents.store_id`
- `sales.objectifs.store_id`
- `sales.transactions.store_id`
- `sales.transactions_rt.store_id`
- `supply.purchase_orders.store_id`
- `supply.reorder_params.store_id`
- `supply.stock_movements.store_id`

**Index (hors PK)** :
- `idx_boutiques_active`
- `idx_boutiques_ville`


### `sales.coaching_scripts`

*Lignes (COUNT exact, 2026-07-06) : 1 141*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('sales.coaching_scripts_id_seq'::regclass)` | PK |
| `store_id` | VARCHAR(10) | oui | `'I63'::character varying` |  |
| `categorie` | VARCHAR(50) | oui | `` |  |
| `situation` | TEXT | oui | `` |  |
| `action` | TEXT | oui | `` |  |
| `produit_cible` | VARCHAR(100) | oui | `` |  |
| `argument_vente` | TEXT | oui | `` |  |
| `impact_observe` | VARCHAR(100) | oui | `` |  |
| `heure_min` | INT4 | oui | `` |  |
| `heure_max` | INT4 | oui | `` |  |
| `jour_semaine` | INT4 | oui | `` |  |
| `source` | VARCHAR(100) | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Index (hors PK)** :
- `idx_cs_cat`
- `idx_cs_created`
- `idx_cs_store`


### `sales.objectifs`

*Lignes (COUNT exact, 2026-07-06) : 23 517*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT4 | non | `nextval('sales.objectifs_id_seq'::regclass)` | PK |
| `store_id` | TEXT | non | `` | FK -> `sales.boutiques.store_id` |
| `agent_id` | INT4 | oui | `` |  |
| `date_objectif` | DATE | non | `` |  |
| `objectif_ca` | NUMERIC(10,2) | oui | `` |  |
| `objectif_transactions` | INT4 | oui | `` |  |
| `objectif_panier_moyen` | NUMERIC(10,2) | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**References vers d'autres tables** :
- `store_id` -> `sales.boutiques.store_id`

**Index (hors PK)** :
- `idx_objectifs_agent`
- `idx_objectifs_store_date`


### `sales.produits`

*Lignes (COUNT exact, 2026-07-06) : 4 593*

**Cle primaire** : `sku`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `sku` | INT4 | non | `` | PK |
| `nom` | TEXT | non | `` |  |
| `categorie` | TEXT | oui | `` |  |
| `famille` | TEXT | oui | `` |  |
| `prix_ht` | NUMERIC(10,2) | oui | `` |  |
| `prix_ttc` | NUMERIC(10,2) | oui | `` |  |
| `marge_pct` | NUMERIC(5,2) | oui | `` |  |
| `stock_initial` | INT4 | oui | `0` |  |
| `actif` | BOOL | oui | `true` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `marque` | VARCHAR(100) | oui | `` |  |
| `modele` | VARCHAR(200) | oui | `` |  |
| `gamme_libelle` | VARCHAR(100) | oui | `` |  |
| `famille_libelle` | VARCHAR(100) | oui | `` |  |
| `pa_ht` | NUMERIC(12,4) | oui | `` |  |
| `marge_pct_calc` | NUMERIC(6,2) | oui | `` |  |
| `flag_4g` | BOOL | oui | `false` |  |
| `flag_5g` | BOOL | oui | `false` |  |
| `flag_terminal` | BOOL | oui | `false` |  |
| `flag_forfait` | BOOL | oui | `false` |  |
| `flag_sim` | BOOL | oui | `false` |  |
| `flag_recharge` | BOOL | oui | `false` |  |
| `serialisable` | BOOL | oui | `false` |  |
| `stockable` | BOOL | oui | `true` |  |
| `lead_time_days` | INT4 | oui | `14` |  |
| `lead_time_std` | INT4 | oui | `3` |  |
| `moq` | INT4 | oui | `1` |  |
| `holding_cost_pct` | NUMERIC(8,6) | oui | `0.2` |  |
| `order_cost` | NUMERIC(12,4) | oui | `50` |  |
| `date_lancement` | DATE | oui | `` |  |
| `date_eol` | DATE | oui | `` |  |
| `lifecycle_stage` | VARCHAR(20) | oui | `'mature'::character varying` |  |
| `stockage_gb` | INT4 | oui | `` |  |
| `ram_gb` | INT4 | oui | `` |  |
| `couleur` | VARCHAR(50) | oui | `` |  |

**Referencee par** :
- `inventory.alerts.sku`
- `inventory.context_adjustments.sku`
- `inventory.product_master.sku`
- `inventory.recommendations.sku`
- `inventory.stock_levels.sku`
- `sales.transactions.sku`
- `sales.transactions_rt.cod_prod`
- `supply.purchase_orders.sku`
- `supply.reorder_params.sku`
- `supply.stock_movements.sku`

**Index (hors PK)** :
- `idx_produits_actif`
- `idx_produits_categorie`
- `idx_produits_famille`


### `sales.transactions`

*Lignes (COUNT exact, 2026-07-06) : 1 929 823*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | INT8 | non | `nextval('sales.transactions_id_seq'::regclass)` | PK |
| `transaction_date` | TIMESTAMP | non | `` |  |
| `date_only` | DATE | non | `` |  |
| `heure` | INT4 | non | `` |  |
| `store_id` | TEXT | non | `` | FK -> `sales.boutiques.store_id` |
| `agent_id` | INT4 | non | `` | FK -> `sales.agents.agent_id` |
| `sku` | INT4 | non | `` | FK -> `sales.produits.sku` |
| `quantity` | INT4 | oui | `1` |  |
| `prix_unitaire` | NUMERIC(10,2) | oui | `` |  |
| `lig_ht` | NUMERIC(10,2) | oui | `` |  |
| `lig_ttc` | NUMERIC(10,2) | oui | `` |  |
| `marge` | NUMERIC(10,2) | oui | `` |  |
| `payment_method` | TEXT | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**References vers d'autres tables** :
- `agent_id` -> `sales.agents.agent_id`
- `store_id` -> `sales.boutiques.store_id`
- `sku` -> `sales.produits.sku`

**Index (hors PK)** :
- `idx_transactions_agent`
- `idx_transactions_composite`
- `idx_transactions_date`
- `idx_transactions_heure`
- `idx_transactions_sku`
- `idx_transactions_store`
- `idx_transactions_store_date`
- `idx_transactions_store_heure`


### `sales.transactions_rt`

*Lignes (COUNT exact, 2026-07-06) : 8 238*

**Cle primaire** : `sale_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `sale_id` | UUID | non | `` | PK |
| `date_vente` | TIMESTAMP | non | `` |  |
| `date_only` | DATE | non | `` |  |
| `heure` | INT4 | non | `` |  |
| `store_id` | TEXT | non | `` | FK -> `sales.boutiques.store_id` |
| `agent_id` | INT4 | oui | `` | FK -> `sales.agents.agent_id` |
| `cod_prod` | INT4 | oui | `` | FK -> `sales.produits.sku` |
| `des_produit` | TEXT | oui | `` |  |
| `lig_ttc` | NUMERIC(10,2) | oui | `` |  |
| `lig_ht` | NUMERIC(10,2) | oui | `` |  |
| `lig_tva` | NUMERIC(10,2) | oui | `` |  |
| `qte_produit` | INT4 | oui | `1` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**References vers d'autres tables** :
- `agent_id` -> `sales.agents.agent_id`
- `cod_prod` -> `sales.produits.sku`
- `store_id` -> `sales.boutiques.store_id`

**Index (hors PK)** :
- `idx_transactions_rt_agent`
- `idx_transactions_rt_composite`
- `idx_transactions_rt_date`
- `idx_transactions_rt_store`
- `idx_transactions_rt_store_date`
- `idx_transactions_rt_store_time`


---

## Schema `supply`

Chaine d'approvisionnement : fournisseurs, bons de commande (Kanban achats), mouvements de stock, transferts, numeros de serie, previsions, parametres de reappro.

### `supply.purchase_orders`

*Lignes (COUNT exact, 2026-07-06) : 2*

**Cle primaire** : `po_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `po_id` | UUID | non | `uuid_generate_v4()` | PK |
| `sku` | INT4 | non | `` | FK -> `sales.produits.sku` |
| `supplier_id` | VARCHAR(30) | oui | `` | FK -> `supply.suppliers.supplier_id` |
| `store_id` | VARCHAR(50) | oui | `` | FK -> `sales.boutiques.store_id` |
| `quantite_commandee` | INT4 | non | `` |  |
| `quantite_recue` | INT4 | oui | `0` |  |
| `prix_unitaire_ht` | NUMERIC(12,4) | oui | `` |  |
| `montant_total_ht` | NUMERIC(14,4) | oui | `` |  |
| `devise` | VARCHAR(5) | oui | `'TND'::character varying` |  |
| `statut` | VARCHAR(20) | oui | `'BROUILLON'::character varying` |  |
| `priorite` | VARCHAR(10) | oui | `'NORMAL'::character varying` |  |
| `date_commande` | TIMESTAMP | oui | `now()` |  |
| `date_livraison_prevue` | DATE | oui | `` |  |
| `date_livraison_reelle` | DATE | oui | `` |  |
| `delai_reel_jours` | INT4 | oui | `` |  |
| `livraison_conforme` | BOOL | oui | `` |  |
| `reference_externe` | VARCHAR(100) | oui | `` |  |
| `notes` | TEXT | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `updated_at` | TIMESTAMP | oui | `now()` |  |
| `recommendation_id` | UUID | oui | `` | FK -> `inventory.recommendations.id` |
| `source` | VARCHAR(10) | non | `'MANUEL'::character varying` |  |
| `urgency` | VARCHAR(20) | oui | `` |  |
| `confidence` | NUMERIC(5,4) | oui | `` |  |
| `agent_decision_id` | UUID | oui | `` |  |

**Contraintes CHECK** :
- `purchase_orders_statut_check` : CHECK (((statut)::text = ANY ((ARRAY['SUGGERE'::character varying, 'BROUILLON'::character varying, 'SOUMIS'::character varying, 'CONFIRME'::character varying, 'EXPEDIE'::character varying, 'RECU_PARTIEL'::character varying, 'RECU'::character varying, 'ANNULE'::character varying, 'LITIGE'::character varying])::text[])))
- `purchase_orders_source_check` : CHECK (((source)::text = ANY ((ARRAY['AGENT'::character varying, 'MANUEL'::character varying])::text[])))

**References vers d'autres tables** :
- `store_id` -> `sales.boutiques.store_id`
- `recommendation_id` -> `inventory.recommendations.id`
- `sku` -> `sales.produits.sku`
- `supplier_id` -> `supply.suppliers.supplier_id`

**Referencee par** :
- `supply.serial_numbers.po_id`

**Index (hors PK)** :
- `idx_po_recommendation`
- `idx_po_sku`
- `idx_po_store`


### `supply.reorder_params`

*Lignes (COUNT exact, 2026-07-06) : 945*

**Cle primaire** : `sku`, `store_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `sku` | INT4 | non | `` | PK; FK -> `sales.produits.sku` |
| `store_id` | VARCHAR(50) | non | `` | PK; FK -> `sales.boutiques.store_id` |
| `demande_moy_jour` | NUMERIC(10,4) | oui | `` |  |
| `demande_std_jour` | NUMERIC(10,4) | oui | `` |  |
| `lead_time_moy` | NUMERIC(8,2) | oui | `` |  |
| `lead_time_std` | NUMERIC(8,2) | oui | `` |  |
| `stock_securite` | INT4 | oui | `` |  |
| `point_commande` | INT4 | oui | `` |  |
| `eoq` | INT4 | oui | `` |  |
| `niveau_service` | NUMERIC(5,4) | oui | `0.95` |  |
| `jours_stock_cible` | INT4 | oui | `30` |  |
| `derniere_maj` | TIMESTAMP | oui | `now()` |  |

**References vers d'autres tables** :
- `sku` -> `sales.produits.sku`
- `store_id` -> `sales.boutiques.store_id`


### `supply.serial_numbers`

*Lignes (COUNT exact, 2026-07-06, après seed) : 177*

**Cle primaire** : `id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `id` | UUID | non | `uuid_generate_v4()` | PK |
| `num_serie` | VARCHAR(50) | non | `` |  |
| `type_serie` | VARCHAR(10) | non | `` |  |
| `sku` | INT4 | oui | `` |  |
| `store_id` | VARCHAR(50) | oui | `` |  |
| `po_id` | UUID | oui | `` | FK -> `supply.purchase_orders.po_id` |
| `statut` | VARCHAR(20) | oui | `'EN_STOCK'::character varying` |  |
| `date_reception` | DATE | oui | `` |  |
| `date_vente` | DATE | oui | `` |  |
| `sale_id` | VARCHAR(100) | oui | `` |  |
| `num_client` | VARCHAR(30) | oui | `` |  |
| `notes` | TEXT | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes UNIQUE** :
- `serial_numbers_num_serie_key` sur (num_serie)

**Contraintes CHECK** :
- `serial_numbers_statut_check` : CHECK (((statut)::text = ANY ((ARRAY['EN_STOCK'::character varying, 'VENDU'::character varying, 'RESERVE'::character varying, 'DEFECTUEUX'::character varying, 'RETOURNE'::character varying, 'VOLE'::character varying, 'EN_TRANSIT'::character varying])::text[])))
- `serial_numbers_type_serie_check` : CHECK (((type_serie)::text = ANY ((ARRAY['IMEI'::character varying, 'ICCID'::character varying, 'ESIM'::character varying, 'EAN'::character varying])::text[])))

**References vers d'autres tables** :
- `po_id` -> `supply.purchase_orders.po_id`

**Index (hors PK)** :
- `idx_serial_sku_store`
- `serial_numbers_num_serie_key`


### `supply.stock_movements`

*Lignes (COUNT exact, 2026-07-06) : 156 669*

**Cle primaire** : `mouvement_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `mouvement_id` | UUID | non | `uuid_generate_v4()` | PK |
| `sku` | INT4 | non | `` | FK -> `sales.produits.sku` |
| `store_id` | VARCHAR(50) | non | `` | FK -> `sales.boutiques.store_id` |
| `type_mouvement` | VARCHAR(30) | non | `` |  |
| `quantite` | INT4 | non | `` |  |
| `stock_avant` | INT4 | oui | `` |  |
| `stock_apres` | INT4 | oui | `` |  |
| `reference_id` | VARCHAR(100) | oui | `` |  |
| `reference_type` | VARCHAR(30) | oui | `` |  |
| `agent_id` | INT4 | oui | `` |  |
| `date_mouvement` | TIMESTAMP | oui | `now()` |  |
| `notes` | TEXT | oui | `` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |

**Contraintes CHECK** :
- `stock_movements_type_mouvement_check` : CHECK (((type_mouvement)::text = ANY ((ARRAY['RECEPTION_BC'::character varying, 'VENTE'::character varying, 'RETOUR_CLIENT'::character varying, 'RETOUR_FOURNISSEUR'::character varying, 'TRANSFERT_ENTRANT'::character varying, 'TRANSFERT_SORTANT'::character varying, 'AJUSTEMENT_INVENTAIRE'::character varying, 'CASSE_PERTE'::character varying, 'INVENTAIRE_GAIN'::character varying, 'INVENTAIRE_PERTE'::character varying, 'RESERVATION'::character varying, 'LIBERATION_RESERVATION'::character varying])::text[])))

**References vers d'autres tables** :
- `sku` -> `sales.produits.sku`
- `store_id` -> `sales.boutiques.store_id`

**Index (hors PK)** :
- `idx_mvt_sku_store`
- `idx_mvt_store_date`


### `supply.suppliers`

*Lignes (COUNT exact, 2026-07-06) : 10*

**Cle primaire** : `supplier_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `supplier_id` | VARCHAR(30) | non | `` | PK |
| `nom` | VARCHAR(200) | non | `` |  |
| `pays_origine` | VARCHAR(50) | oui | `` |  |
| `type_fournisseur` | VARCHAR(30) | non | `` |  |
| `categories` | JSONB | oui | `` |  |
| `marques` | JSONB | oui | `` |  |
| `delai_livraison_moy` | INT4 | oui | `14` |  |
| `delai_livraison_std` | INT4 | oui | `3` |  |
| `taux_fiabilite` | NUMERIC(5,4) | oui | `0.90` |  |
| `commande_min` | INT4 | oui | `1` |  |
| `commande_multiple` | INT4 | oui | `1` |  |
| `devise` | VARCHAR(5) | oui | `'TND'::character varying` |  |
| `conditions_paiement` | VARCHAR(100) | oui | `` |  |
| `contact_nom` | VARCHAR(100) | oui | `` |  |
| `contact_email` | VARCHAR(200) | oui | `` |  |
| `actif` | BOOL | oui | `true` |  |
| `score_global` | NUMERIC(5,2) | oui | `0.0` |  |
| `created_at` | TIMESTAMP | oui | `now()` |  |
| `updated_at` | TIMESTAMP | oui | `now()` |  |

**Referencee par** :
- `supply.purchase_orders.supplier_id`


### `supply.transfers`

*Lignes (COUNT exact, 2026-07-06, après seed) : 34*

**Cle primaire** : `transfer_id`

| Colonne | Type | Nullable | Defaut | Remarque |
|---|---|---|---|---|
| `transfer_id` | UUID | non | `uuid_generate_v4()` | PK |
| `sku` | INT4 | non | `` |  |
| `store_source` | VARCHAR(50) | non | `` |  |
| `store_dest` | VARCHAR(50) | non | `` |  |
| `quantite` | INT4 | non | `` |  |
| `statut` | VARCHAR(20) | oui | `'DEMANDE'::character varying` |  |
| `priorite` | VARCHAR(10) | oui | `'NORMAL'::character varying` |  |
| `motif` | VARCHAR(100) | oui | `` |  |
| `date_demande` | TIMESTAMP | oui | `now()` |  |
| `date_approbation` | TIMESTAMP | oui | `` |  |
| `date_expedition` | TIMESTAMP | oui | `` |  |
| `date_reception` | TIMESTAMP | oui | `` |  |
| `notes` | TEXT | oui | `` |  |

**Contraintes CHECK** :
- `transfers_statut_check` : CHECK (((statut)::text = ANY ((ARRAY['DEMANDE'::character varying, 'APPROUVE'::character varying, 'EXPEDIE'::character varying, 'RECU'::character varying, 'REJETE'::character varying, 'ANNULE'::character varying, 'EN_LITIGE'::character varying])::text[])))


---

## Carte globale des relations (cles etrangeres)

| Table source | Colonne | -> | Table cible | Colonne |
|---|---|---|---|---|
| `inventory.alerts` | `store_id` | -> | `sales.boutiques` | `store_id` |
| `inventory.alerts` | `sku` | -> | `sales.produits` | `sku` |
| `inventory.context_adjustments` | `sku` | -> | `sales.produits` | `sku` |
| `inventory.context_adjustments` | `store_id` | -> | `sales.boutiques` | `store_id` |
| `inventory.product_master` | `sku` | -> | `sales.produits` | `sku` |
| `inventory.recommendations` | `sku` | -> | `sales.produits` | `sku` |
| `inventory.recommendations` | `store_id` | -> | `sales.boutiques` | `store_id` |
| `inventory.stock_levels` | `store_id` | -> | `sales.boutiques` | `store_id` |
| `inventory.stock_levels` | `sku` | -> | `sales.produits` | `sku` |
| `market.competitor_pricing` | `concurrent_id` | -> | `market.competitors` | `concurrent_id` |
| `sales.agents` | `store_id` | -> | `sales.boutiques` | `store_id` |
| `sales.objectifs` | `store_id` | -> | `sales.boutiques` | `store_id` |
| `sales.transactions` | `agent_id` | -> | `sales.agents` | `agent_id` |
| `sales.transactions` | `store_id` | -> | `sales.boutiques` | `store_id` |
| `sales.transactions` | `sku` | -> | `sales.produits` | `sku` |
| `sales.transactions_rt` | `agent_id` | -> | `sales.agents` | `agent_id` |
| `sales.transactions_rt` | `cod_prod` | -> | `sales.produits` | `sku` |
| `sales.transactions_rt` | `store_id` | -> | `sales.boutiques` | `store_id` |
| `supply.purchase_orders` | `store_id` | -> | `sales.boutiques` | `store_id` |
| `supply.purchase_orders` | `recommendation_id` | -> | `inventory.recommendations` | `id` |
| `supply.purchase_orders` | `sku` | -> | `sales.produits` | `sku` |
| `supply.purchase_orders` | `supplier_id` | -> | `supply.suppliers` | `supplier_id` |
| `supply.reorder_params` | `sku` | -> | `sales.produits` | `sku` |
| `supply.reorder_params` | `store_id` | -> | `sales.boutiques` | `store_id` |
| `supply.serial_numbers` | `po_id` | -> | `supply.purchase_orders` | `po_id` |
| `supply.stock_movements` | `sku` | -> | `sales.produits` | `sku` |
| `supply.stock_movements` | `store_id` | -> | `sales.boutiques` | `store_id` |

## Historique du nettoyage du 2026-07-06

**Supprime** (0 ligne, 0 reference code confirmee par audit, verifie sans dependance de vue avant suppression) :
- Schema `agent` entier (8 tables) + schemas `context` et `forecasting` (1 table chacun, vides)
- `public.actions_executed`, `agent_context`, `agent_memory_long`, `agent_memory_short`, `action_feedback`, `recommendations`
- `monitoring.coaching_scripts`, `monitoring.cycle_logs_legacy` (+ vue `monitoring.vw_cycle_summary` qui en dependait, elle-meme jamais interrogee)
- `coaching.hitl_requests`, `coaching.escalations`, `coaching.agent_memory`, `coaching.coaching_recommendations`
- `customer.churn_signals`, `market.network_events`
- `sales.daily_objectives_category`, `inventory.recommendations_computed`, `inventory.stock_snapshot_enriched`

**Conserve malgre 0 ligne au moment du nettoyage** (code actif qui lit/ecrit dedans, juste jamais declenche dans cet environnement) : `inventory.demand_forecast`, `inventory.events`, `public.rag_feedback`/`rag_queries`/`rag_feedback_metrics`, `supply.serial_numbers`/`transfers`. **Seedees le 2026-07-06** avec des donnees synthetiques referencant des entites reelles (skus/stores/cycles/purchase_orders existants) pour rendre le systeme demontrable — voir memoire projet `db_cleanup_2026-07-06` pour le detail de la generation.

**Cles etrangeres ajoutees** (0 ligne orpheline verifiee avant ajout) : `inventory.context_adjustments` (sku, store_id), `supply.reorder_params` (sku, store_id), `sales.transactions_rt` (cod_prod, store_id, agent_id). Les relations sku/store_id sur `inventory.alerts`, `inventory.product_master`, `inventory.recommendations`, `inventory.stock_levels`, `supply.purchase_orders`, `supply.stock_movements` etaient deja enforcees sous d'autres noms de contrainte au moment de l'execution (ajoutees entre l'audit initial et l'execution — la base est partagee/vivante).

---

## Vues

16 vues (absentes de l'introspection 2026-07-06 qui ne listait que les tables).

⚠️ **`inventory.products` et `inventory.stores` sont des VUES** (respectivement
sur `inventory.product_master` et `sales.boutiques`) — jamais d'INSERT/UPDATE
dessus ; les repositories les lisent comme des tables mais écrivent dans les
tables sous-jacentes.

| Schema | Vue | Rôle |
|---|---|---|
| inventory | `products` | alias lecture de `product_master` (compat repos) |
| inventory | `stores` | alias lecture de `sales.boutiques` |
| inventory | `stock_levels_v` | stock enrichi lecture |
| inventory | `vw_active_promotions` | promotions actives |
| inventory | `vw_stock_enriched` | stock + attributs produit |
| monitoring | `category_gap_live` | gap CA par catégorie temps réel |
| monitoring | `coaching_interactions` | alias `public.coach_interactions` |
| monitoring | `cycle_logs` | alias `public.agent_cycles` |
| monitoring | `realtime_store_pulse` | pouls boutique temps réel |
| public | `produits` | alias `sales.produits` |
| sales | `transactions_history` | historique unifié |
| sales | `vw_ca_par_boutique` | CA agrégé par boutique |
| sales | `vw_performance_agent` | performance par vendeur |
| sales | `vw_stock_enriched` | stock côté ventes |
| sales | `vw_top_products` | top produits |
| sales | `vw_ventes_par_agent` | ventes par vendeur |

---

## Refonte 2026-07-07 (migrations 0002-0007)

Changements appliqués via l'arbre Alembic unique `db/migrations/` (chaque
révision est documentée dans son fichier) :

| Rev | Contenu |
|---|---|
| `0001` | Baseline complète par introspection (8 schémas, 50 tables, 16 vues, fonctions/triggers, uuid-ossp). La base vivante a été *stampée*, pas rejouée ; `alembic upgrade head` sur une base vide reconstruit tout. |
| `0002` | `DROP DEFAULT 'I63'` sur `store_id` de agent_logs, agent_cycles, agent_errors, rag_feedback, coach_interactions, sales.coaching_scripts |
| `0003` | Réparation de 8 357 `sales.transactions_rt.des_produit` contenant le SKU brut (+ fix du simulateur à la source) ; CHECK `ck_po_statut` (9 statuts) sur `supply.purchase_orders` |
| `0004` | **Nouvelle table `supply.supplier_products`** — catalogue de sourcing fournisseur↔SKU (lead_time_days, moq, unit_cost, is_preferred unique par SKU). Seed : 1 370 lignes, 1 040 SKUs stockables actifs couverts |
| `0005` | Chaîne causale : `inventory.alerts.agent_run_id` et `inventory.recommendations.agent_run_id` TEXT→INTEGER + FK vers `agent_runs(id)` ; **nouvelle colonne `recommendations.alert_id`** FK→alerts ; index `stock_movements(reference_type, reference_id)` |
| `0006` | 16 FK ajoutées (demand_forecast, sales_history, stock_history, promotions, events, transfers, coaching_events, nps_csat) après purge de 115 lignes orphelines synthétiques ; `demand_forecast.sku` TEXT→INTEGER |
| `0007` | `COMMENT ON` sur schémas et tables `public.*` (appartenance AUTH/MONITORING/RAG/HITL/KPI) |

Total après refonte : **51 tables, 48 FK**. Les 5 anciennes sources de DDL
(2 arbres Alembic morts, SQL data/telco, CREATE TABLE runtime, shared_module)
ont été supprimées.

Pour régénérer les corps de tables de ce document : introspection directe
(`pg_catalog`/`pg_constraint`, jamais `information_schema` pour les FK).
