# Diagramme de la Base de Données — `ooredoo_sales`

> Rétro-ingénierie effectuée directement sur PostgreSQL via `pg_constraint`
> (source de vérité des FK — jamais `information_schema`).
> **51 tables** réparties en **7 schémas**, **49 clés étrangères** déclarées.
> Diagrammes au format **Mermaid** (rendu GitHub/VS Code, export PNG/SVG via <https://mermaid.live>).

Note de lecture : Mermaid n'accepte pas le `.` dans les noms d'entités — le
préfixe de schéma est rendu par `schema__table` (ex. `sales__boutiques` = `sales.boutiques`).

---

## 1. Vue macroscopique — les 7 schémas

| Schéma | Rôle | Tables | Volumétrie notable |
|--------|------|-------:|--------------------|
| `sales` | Référentiel & transactionnel ventes | 7 | `transactions` ≈ 1,93 M lignes |
| `inventory` | Stocks, alertes, recommandations agents | 11 | `stock_history` ≈ 1,12 M lignes |
| `supply` | Approvisionnement (PO Kanban, fournisseurs) | 7 | `stock_movements` ≈ 157 k |
| `market` | Contexte marché (événements, concurrents, MNP) | 5 | `events` : 38 festivals/concerts |
| `coaching` | Historique des cycles de coaching | 1 | 84 événements |
| `customer` | Voix du client (NPS/CSAT, segments) | 2 | — |
| `public` | Auth, HITL, observabilité agents, KPI agrégés | 18 | `agent_kpi_daily` ≈ 114 k |

**Tables pivots** : `sales.boutiques` (référencée par **14 FK**) et
`sales.produits` (référencée par **16 FK**) sont les hubs du modèle — tout le
système converge vers le couple (`store_id`, `sku`).

---

## 2. Vue globale des relations (49 FK)

```mermaid
erDiagram
    %% ================= HUBS =================
    sales__boutiques ||--o{ sales__agents : "emploie"
    sales__boutiques ||--o{ sales__objectifs : "a pour cible"
    sales__boutiques ||--o{ sales__transactions : "réalise"
    sales__boutiques ||--o{ sales__transactions_rt : "réalise (temps réel)"
    sales__produits  ||--o{ sales__transactions : "vendu dans"
    sales__produits  ||--o{ sales__transactions_rt : "vendu dans"
    sales__agents    ||--o{ sales__transactions : "effectue"
    sales__agents    ||--o{ sales__transactions_rt : "effectue"

    %% ================= INVENTORY =================
    sales__boutiques ||--o{ inventory__stock_levels : "stocke"
    sales__produits  ||--o{ inventory__stock_levels : "niveau de"
    sales__produits  ||--|| inventory__product_master : "paramètres supply"
    sales__boutiques ||--o{ inventory__alerts : "alerte sur"
    sales__produits  ||--o{ inventory__alerts : "concerne"
    inventory__agent_runs ||--o{ inventory__alerts : "génère"
    sales__boutiques ||--o{ inventory__recommendations : "pour"
    sales__produits  ||--o{ inventory__recommendations : "concerne"
    inventory__agent_runs ||--o{ inventory__recommendations : "produit"
    inventory__alerts ||--o{ inventory__recommendations : "déclenche"
    sales__boutiques ||--o{ inventory__context_adjustments : "ajusté pour"
    sales__produits  ||--o{ inventory__context_adjustments : "ajusté pour"
    sales__boutiques ||--o{ inventory__demand_forecast : "prévu pour"
    sales__produits  ||--o{ inventory__demand_forecast : "prévu pour"
    sales__boutiques ||--o{ inventory__sales_history : "historique"
    sales__produits  ||--o{ inventory__sales_history : "historique"
    sales__boutiques ||--o{ inventory__stock_history : "historique"
    sales__produits  ||--o{ inventory__stock_history : "historique"
    sales__produits  ||--o{ inventory__promotions : "promu"
    sales__produits  ||--o{ inventory__events : "impacté par"
    sales__boutiques ||--o{ inventory__events : "impacté par"

    %% ================= SUPPLY =================
    sales__produits  ||--o{ supply__purchase_orders : "commandé"
    sales__boutiques ||--o{ supply__purchase_orders : "livré à"
    supply__suppliers ||--o{ supply__purchase_orders : "fournit"
    inventory__recommendations ||--o{ supply__purchase_orders : "à l'origine de"
    supply__suppliers ||--o{ supply__supplier_products : "catalogue"
    sales__produits  ||--o{ supply__supplier_products : "sourcé"
    sales__produits  ||--o{ supply__reorder_params : "paramétré"
    sales__boutiques ||--o{ supply__reorder_params : "paramétré"
    sales__produits  ||--o{ supply__stock_movements : "mouvementé"
    sales__boutiques ||--o{ supply__stock_movements : "mouvementé"
    supply__purchase_orders ||--o{ supply__serial_numbers : "réceptionne"
    sales__produits  ||--o{ supply__transfers : "transféré"
    sales__boutiques ||--o{ supply__transfers : "source"
    sales__boutiques ||--o{ supply__transfers : "destination"

    %% ================= COACHING / CUSTOMER / MARKET =================
    sales__agents    ||--o{ coaching__coaching_events : "coaché"
    sales__boutiques ||--o{ coaching__coaching_events : "contexte"
    sales__agents    ||--o{ customer__nps_csat : "évalué"
    sales__boutiques ||--o{ customer__nps_csat : "évalué"
    market__competitors ||--o{ market__competitor_pricing : "pratique"
```

---

## 3. Schéma `sales` — Référentiel & transactionnel (détaillé)

```mermaid
erDiagram
    sales__boutiques {
        text store_id PK
        text store_name
        text ville
        text region
        varchar wilaya
        varchar canal
        varchar type_boutique
        numeric latitude
        numeric longitude
        int capacite_conseillers
        bool is_officielle
        int rang_ca_region
    }
    sales__produits {
        int sku PK
        text nom
        text categorie
        text famille
        varchar marque
        numeric prix_ht
        numeric prix_ttc
        numeric marge_pct
        bool flag_terminal
        bool flag_forfait
        bool flag_sim
        int lead_time_days
        int moq
        numeric holding_cost_pct
        varchar lifecycle_stage
        date date_eol
    }
    sales__agents {
        int agent_id PK
        text agent_name
        text store_id FK
        text role
        text performance_level
        numeric quota_mensuel_ca
        int quota_activations
        varchar specialisation
        numeric coach_score
        int anciennete_mois
    }
    sales__transactions {
        bigint id PK
        timestamp transaction_date
        date date_only
        int heure
        text store_id FK
        int agent_id FK
        int sku FK
        int quantity
        numeric lig_ttc
        numeric marge
        text payment_method
    }
    sales__transactions_rt {
        uuid sale_id PK
        timestamp date_vente
        int heure
        text store_id FK
        int agent_id FK
        int cod_prod FK
        numeric lig_ttc
        int qte_produit
    }
    sales__objectifs {
        int id PK
        text store_id FK
        int agent_id
        date date_objectif
        numeric objectif_ca
        int objectif_transactions
        numeric objectif_panier_moyen
    }
    sales__coaching_scripts {
        int id PK
        varchar store_id
        varchar categorie
        text situation
        text action
        varchar produit_cible
        text argument_vente
        int heure_min
        int heure_max
    }

    sales__boutiques ||--o{ sales__agents : "agents_store_id_fkey"
    sales__boutiques ||--o{ sales__objectifs : "objectifs_store_id_fkey"
    sales__boutiques ||--o{ sales__transactions : "transactions_store_id_fkey"
    sales__agents    ||--o{ sales__transactions : "transactions_agent_id_fkey"
    sales__produits  ||--o{ sales__transactions : "transactions_sku_fkey"
    sales__boutiques ||--o{ sales__transactions_rt : "fk_rt_store"
    sales__agents    ||--o{ sales__transactions_rt : "fk_rt_agent"
    sales__produits  ||--o{ sales__transactions_rt : "fk_rt_cod_prod"
```

`sales.coaching_scripts` (corpus RAG, 2 040 scripts) n'a volontairement pas de
FK : le `store_id` peut être générique (`ALL`) pour les scripts applicables à
tout le réseau.

---

## 4. Schéma `inventory` — Chaîne causale des agents stock

La chaîne causale est traçable de bout en bout :
**`agent_runs` → `alerts` → `recommendations` → `supply.purchase_orders`**.

```mermaid
erDiagram
    inventory__agent_runs {
        int id PK
        text cycle_id
        text agent_name
        text store_id
        text batch_id
        timestamp started_at
        float duration_ms
        text status
        jsonb input_summary
        jsonb output_summary
        int alerts_generated
        int recommendations_generated
    }
    inventory__alerts {
        int id PK
        text store_id FK
        int sku FK
        text alert_type
        text severity
        text status
        text recommended_action
        int agent_run_id FK
        timestamp triggered_at
        timestamp resolved_at
    }
    inventory__recommendations {
        uuid id PK
        int sku FK
        text store_id FK
        text action
        int order_qty
        text urgency
        numeric confidence
        text recommendation_text
        bool escalate_to_human
        text status
        text decided_by
        int agent_run_id FK
        int alert_id FK
        numeric order_cost
        numeric holding_cost
    }
    inventory__stock_levels {
        bigint id PK
        text store_id FK
        int sku FK
        int quantity
        int quantity_reserved
        int quantity_available
        float remaining_days_of_stock
        timestamp last_updated
    }
    inventory__context_adjustments {
        int id PK
        int sku FK
        text store_id FK
        numeric demand_uplift_pct
        numeric weather_impact
        numeric promo_impact
        numeric event_impact
        text dominant_signal
        jsonb signals
        numeric confidence
        date valid_from
        date valid_to
        int agent_run_id
    }
    inventory__demand_forecast {
        int id PK
        int sku FK
        text store_id FK
        date forecast_date
        numeric demand_24h
        numeric confidence_low
        numeric confidence_high
        text model_version
    }
    inventory__sales_history {
        bigint id PK
        date record_date
        text store_id FK
        int sku FK
        int quantity_sold
        numeric revenue
        bool is_promo
        text event_name
        text season
        numeric uplift_factor
        bool is_weekend
    }
    inventory__stock_history {
        bigint id PK
        date record_date
        text store_id FK
        int sku FK
        int stock_level
        bool is_stockout
    }
    inventory__product_master {
        int sku PK
        int lead_time_days
        int moq
        numeric holding_cost_pct
        numeric order_cost
        text lifecycle_stage
    }
    inventory__promotions {
        bigint id PK
        text promo_id
        date start_date
        date end_date
        int sku FK
        numeric discount_pct
        text promo_type
        text scope
    }
    inventory__events {
        int id PK
        text event_name
        text event_type
        date start_date
        date end_date
        int sku FK
        text store_id FK
        numeric estimated_uplift_pct
        text affected_categories
    }
    inventory__business_objectives {
        int id PK
        text objective_type
        text label
        bool is_active
        int priority
    }

    inventory__agent_runs ||--o{ inventory__alerts : "fk_alerts_agent_run"
    inventory__agent_runs ||--o{ inventory__recommendations : "fk_recommendations_agent_run"
    inventory__alerts ||--o{ inventory__recommendations : "recommendations_alert_id_fkey"
```

Toutes les tables `inventory.*` portant (`store_id`, `sku`) référencent
`sales.boutiques` et `sales.produits` (cf. vue globale §2) — FK omises ici pour
la lisibilité. `business_objectives` est une table de configuration sans FK
(objectif actif : minimiser ruptures / coûts / équilibré).

---

## 5. Schéma `supply` — Approvisionnement & Kanban PO

Cœur de la boucle fermée : la recommandation du DecisionAgent devient un bon de
commande `SUGGERE` sur le Kanban, approuvé par l'humain (HITL), suivi jusqu'à `RECU`.

```mermaid
erDiagram
    supply__suppliers {
        varchar supplier_id PK
        varchar nom
        varchar type_fournisseur
        jsonb categories
        jsonb marques
        int delai_livraison_moy
        numeric taux_fiabilite
        int commande_min
        numeric score_global
        bool actif
    }
    supply__supplier_products {
        int id PK
        varchar supplier_id FK
        int sku FK
        int lead_time_days
        int moq
        numeric unit_cost
        bool is_preferred
        bool actif
    }
    supply__purchase_orders {
        uuid po_id PK
        int sku FK
        varchar supplier_id FK
        varchar store_id FK
        uuid recommendation_id FK
        int quantite_commandee
        int quantite_recue
        numeric montant_total_ht
        varchar statut "SUGGERE|BROUILLON|SOUMIS|CONFIRME|EXPEDIE|RECU|ANNULE"
        varchar priorite
        varchar source "AGENT|HUMAN"
        varchar urgency
        numeric confidence
        timestamp date_soumission
        timestamp date_confirmation
        bool confirmed_auto
        date date_livraison_prevue
        date date_livraison_reelle
        int ecart_livraison_jours
        bool livraison_conforme
    }
    supply__serial_numbers {
        uuid id PK
        varchar num_serie
        varchar type_serie "IMEI|ICCID"
        int sku FK
        varchar store_id
        uuid po_id FK
        varchar statut
        date date_vente
        varchar num_client
    }
    supply__reorder_params {
        int sku PK "PK composite"
        varchar store_id PK "PK composite"
        numeric demande_moy_jour
        numeric demande_std_jour
        numeric lead_time_moy
        int stock_securite
        int point_commande
        int eoq
        numeric niveau_service
    }
    supply__stock_movements {
        uuid mouvement_id PK
        int sku FK
        varchar store_id FK
        varchar type_mouvement "RECEPTION|VENTE|TRANSFERT|AJUSTEMENT"
        int quantite
        int stock_avant
        int stock_apres
        varchar reference_id
        varchar reference_type
        timestamp date_mouvement
    }
    supply__transfers {
        uuid transfer_id PK
        int sku FK
        varchar store_source FK
        varchar store_dest FK
        int quantite
        varchar statut
        varchar priorite
        varchar motif
        timestamp date_demande
        timestamp date_reception
    }
    inventory__recommendations {
        uuid id PK
    }

    supply__suppliers ||--o{ supply__supplier_products : "catalogue"
    supply__suppliers ||--o{ supply__purchase_orders : "fournit"
    inventory__recommendations ||--o{ supply__purchase_orders : "SET NULL"
    supply__purchase_orders ||--o{ supply__serial_numbers : "réceptionne"
```

---

## 6. Schémas `market`, `coaching`, `customer` — Contexte & feedback

```mermaid
erDiagram
    market__events {
        uuid event_id PK
        varchar event_name
        varchar event_type "festival|concert|religieux|sport"
        date start_date
        date end_date
        varchar scope
        jsonb region_ids
        jsonb categories_impactees
        numeric uplift_terminal
        numeric uplift_forfait
        numeric uplift_recharge
        varchar intensite
    }
    market__competitors {
        varchar concurrent_id PK
        varchar nom
        numeric part_marche_pct
        bigint nb_abonnes
        varchar positionnement
        jsonb points_forts
        jsonb points_faibles
    }
    market__competitor_pricing {
        int id PK
        varchar concurrent_id FK
        varchar categorie
        numeric donnees_go
        numeric prix_ttc
        int engagement_mois
        date date_releve
    }
    market__mnp_flows {
        uuid mnp_id PK
        varchar direction "IN|OUT"
        varchar operateur_origine
        varchar operateur_destination
        date mois
        int volume
        varchar raison_principale
        varchar wilaya
    }
    market__seasonal_patterns {
        int id PK
        varchar categorie
        int mois
        int jour_semaine
        int heure_debut
        int heure_fin
        numeric facteur_demande
        varchar confidence
    }
    coaching__coaching_events {
        uuid id PK
        int advisor_id FK
        varchar store_id FK
        varchar cycle_id
        varchar urgency_level
        numeric gap_pct
        numeric forecast_eod
        text advice_text
        varchar produit_a_pousser
        text strategie
        text cause_racine
        bool rag_used
        jsonb script_ids
    }
    customer__nps_csat {
        int id PK
        varchar store_id FK
        int agent_id FK
        date feedback_date
        varchar type_enquete "NPS|CSAT"
        numeric score
        text verbatim
        varchar categorie_motif
        bool resolu
    }
    customer__segments {
        varchar segment_id PK
        varchar libelle
        numeric arpu_moyen_tnd
        numeric churn_rate_base
        varchar canal_prefere
        jsonb products_preferes
        numeric poids_marche_pct
    }
    sales__agents {
        int agent_id PK
    }
    sales__boutiques {
        text store_id PK
    }

    market__competitors ||--o{ market__competitor_pricing : "pratique"
    sales__agents ||--o{ coaching__coaching_events : "coaché"
    sales__boutiques ||--o{ coaching__coaching_events : "contexte"
    sales__agents ||--o{ customer__nps_csat : "évalué"
    sales__boutiques ||--o{ customer__nps_csat : "évalué"
```

---

## 7. Schéma `public` — Auth, HITL, observabilité & KPI (relations logiques)

Ces 18 tables n'ont **aucune FK déclarée** (choix de conception : tables
d'observabilité à fort débit d'écriture + agrégats recalculables — pas de
contrainte pour ne pas bloquer les pipelines). Les relations sont **logiques**,
portées par les clés naturelles `store_id`, `agent_id`, `cycle_id`, `user_id` :

```mermaid
erDiagram
    app_users {
        int id PK
        varchar user_id UK
        varchar username
        varchar password_hash
        varchar role "manager|advisor|admin"
        varchar store_id "logique vers sales.boutiques"
        varchar advisor_id
    }
    app_sessions {
        int id PK
        varchar token UK
        varchar user_id "logique vers app_users"
        timestamp expires_at
        varchar ip_address
    }
    agent_cycles {
        int id PK
        varchar cycle_id UK
        varchar store_id "logique"
        varchar triggered_by
        varchar urgency_level
        float gap_pct
        float forecast_eod
        text analyst_summary
        text strategie
        bool rag_used
    }
    agent_logs {
        int id PK
        varchar cycle_id "logique vers agent_cycles"
        varchar agent_name
        varchar node_name
        varchar status
        jsonb input_state
        jsonb output_state
        float duration_ms
    }
    agent_errors {
        int id PK
        varchar cycle_id "logique"
        varchar agent_name
        varchar error_type
        text traceback_txt
        bool resolved
    }
    hitl_reviews {
        uuid id PK
        text cycle_id "logique"
        text store_id "logique"
        float critique_score
        jsonb actions
        text status "pending|approved|rejected"
        text approver_name
        timestamptz reviewed_at
    }
    coach_interactions {
        int id PK
        varchar store_id "logique"
        varchar advisor_name
        text message
        text response
        float confidence
        bool rag_used
    }
    agent_feedback {
        int id PK
        varchar store_id "logique"
        varchar source
        varchar ref_id "reco/alerte/PO"
        int sku "logique vers sales.produits"
        varchar decision "accepted|rejected"
        jsonb payload
    }
    rag_feedback {
        int id PK
        varchar cycle_id "logique"
        text query
        float top_score
        bool was_useful
    }
    store_kpi_daily {
        bigint id PK
        varchar store_id "logique"
        date kpi_date
        numeric ca_realise
        numeric ca_cible
        numeric gap_ca_pct
        numeric taux_conversion
    }
    agent_kpi_daily {
        bigint id PK
        int agent_id "logique vers sales.agents"
        varchar store_id "logique"
        date kpi_date
        numeric ca_realise
        numeric gap_ca_pct
        int nb_postpaye
    }
    telco_targets_monthly {
        bigint id PK
        varchar store_id "logique"
        int agent_id "logique"
        int mois
        int annee
        numeric ca_cible_mensuel
        int activations_postpaye
    }

    app_users ||--o{ app_sessions : "user_id (logique)"
    agent_cycles ||--o{ agent_logs : "cycle_id (logique)"
    agent_cycles ||--o{ agent_errors : "cycle_id (logique)"
    agent_cycles ||--o{ hitl_reviews : "cycle_id (logique)"
    agent_cycles ||--o{ rag_feedback : "cycle_id (logique)"
    store_kpi_daily ||--o{ agent_kpi_daily : "store_id+date (logique)"
```

Tables non représentées (même logique, faible enjeu pour le rapport) :
`agent_memory`, `agent_sessions`, `rag_queries`, `rag_feedback_metrics`,
`weekly_kpi_summary`, `alembic_version` (versionnement migrations).

---

## 8. Inventaire exhaustif des 49 clés étrangères

Règle de suppression : `NO ACTION` (défaut) sauf mention `SET NULL`
(utilisée sur les liens de traçabilité agents, pour ne jamais perdre l'historique).

| Table source | Colonne(s) | → Table cible | Règle DELETE |
|---|---|---|---|
| coaching.coaching_events | advisor_id | sales.agents | NO ACTION |
| coaching.coaching_events | store_id | sales.boutiques | NO ACTION |
| customer.nps_csat | agent_id | sales.agents | NO ACTION |
| customer.nps_csat | store_id | sales.boutiques | NO ACTION |
| inventory.alerts | agent_run_id | inventory.agent_runs | **SET NULL** |
| inventory.alerts | sku | sales.produits | NO ACTION |
| inventory.alerts | store_id | sales.boutiques | NO ACTION |
| inventory.context_adjustments | sku | sales.produits | NO ACTION |
| inventory.context_adjustments | store_id | sales.boutiques | NO ACTION |
| inventory.demand_forecast | sku | sales.produits | NO ACTION |
| inventory.demand_forecast | store_id | sales.boutiques | NO ACTION |
| inventory.events | sku | sales.produits | NO ACTION |
| inventory.events | store_id | sales.boutiques | NO ACTION |
| inventory.product_master | sku | sales.produits | NO ACTION |
| inventory.promotions | sku | sales.produits | NO ACTION |
| inventory.recommendations | sku | sales.produits | NO ACTION |
| inventory.recommendations | store_id | sales.boutiques | NO ACTION |
| inventory.recommendations | agent_run_id | inventory.agent_runs | **SET NULL** |
| inventory.recommendations | alert_id | inventory.alerts | **SET NULL** |
| inventory.sales_history | sku | sales.produits | NO ACTION |
| inventory.sales_history | store_id | sales.boutiques | NO ACTION |
| inventory.stock_history | sku | sales.produits | NO ACTION |
| inventory.stock_history | store_id | sales.boutiques | NO ACTION |
| inventory.stock_levels | sku | sales.produits | NO ACTION |
| inventory.stock_levels | store_id | sales.boutiques | NO ACTION |
| market.competitor_pricing | concurrent_id | market.competitors | NO ACTION |
| sales.agents | store_id | sales.boutiques | NO ACTION |
| sales.objectifs | store_id | sales.boutiques | NO ACTION |
| sales.transactions | agent_id | sales.agents | NO ACTION |
| sales.transactions | sku | sales.produits | NO ACTION |
| sales.transactions | store_id | sales.boutiques | NO ACTION |
| sales.transactions_rt | agent_id | sales.agents | NO ACTION |
| sales.transactions_rt | cod_prod | sales.produits | NO ACTION |
| sales.transactions_rt | store_id | sales.boutiques | NO ACTION |
| supply.purchase_orders | sku | sales.produits | NO ACTION |
| supply.purchase_orders | store_id | sales.boutiques | NO ACTION |
| supply.purchase_orders | recommendation_id | inventory.recommendations | **SET NULL** |
| supply.purchase_orders | supplier_id | supply.suppliers | NO ACTION |
| supply.reorder_params | sku | sales.produits | NO ACTION |
| supply.reorder_params | store_id | sales.boutiques | NO ACTION |
| supply.serial_numbers | po_id | supply.purchase_orders | NO ACTION |
| supply.stock_movements | sku | sales.produits | NO ACTION |
| supply.stock_movements | store_id | sales.boutiques | NO ACTION |
| supply.supplier_products | sku | sales.produits | NO ACTION |
| supply.supplier_products | supplier_id | supply.suppliers | NO ACTION |
| supply.transfers | sku | sales.produits | NO ACTION |
| supply.transfers | store_dest | sales.boutiques | NO ACTION |
| supply.transfers | store_source | sales.boutiques | NO ACTION |

---

## 9. Points de conception à valoriser dans le rapport

1. **Modèle en étoile double hub** : `sales.boutiques` et `sales.produits`
   centralisent l'intégrité référentielle de tout le système (30 des 49 FK).
2. **Chaîne causale agentique traçable** :
   `agent_runs → alerts → recommendations → purchase_orders` — chaque décision
   IA est reliée à son exécution d'agent, avec `SET NULL` pour préserver
   l'historique métier même après purge des runs techniques.
3. **Boucle fermée d'approvisionnement** : `purchase_orders.source`
   (AGENT/HUMAN) + `statut` Kanban (SUGGERE → … → RECU) + `confirmed_auto`
   matérialisent le HITL en base.
4. **Séparation FK déclarées / relations logiques** : les schémas métier
   (`sales`, `inventory`, `supply`…) sont contraints ; le schéma `public`
   (observabilité, KPI à fort débit) reste sans FK par choix de performance —
   à présenter comme un compromis assumé.
5. **Double PK composite** : `supply.reorder_params (sku, store_id)` — les
   paramètres de réapprovisionnement sont propres au couple produit × boutique.
