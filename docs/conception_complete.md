# Conception Complète du Projet — Multi-Agent Sales & Inventory

## Table des matières

1. [Vue d'ensemble du projet](#1-vue-densemble)
2. [Architecture technique](#2-architecture-technique)
3. [Base de données — 5 schémas](#3-base-de-données)
4. [Schéma SALES — Données commerciales](#4-schéma-sales)
5. [Schéma INVENTORY — Gestion de stock](#5-schéma-inventory)
6. [Schéma PUBLIC — Auth & Observabilité agents](#6-schéma-public)
7. [Schéma MONITORING — Cycles & Scripts](#7-schéma-monitoring)
8. [Schéma AGENT — Legacy (non actif)](#8-schéma-agent)
9. [Vues SQL](#9-vues-sql)
10. [Diagramme des relations](#10-diagramme-des-relations)
11. [Flux de données complet](#11-flux-de-données)
12. [Modules applicatifs](#12-modules-applicatifs)
13. [Services externes & infrastructure](#13-services-externes--infrastructure)

---

## 1. Vue d'ensemble

Système de coaching IA multi-agent pour Ooredoo Tunisie. Il analyse les données de vente temps réel de **201 boutiques** (focus store I63 — FR LAC2 Tunisia Mall), détecte les écarts d'objectif, génère des stratégies commerciales et répond aux questions des conseillers via un chatbot.

**Chiffres clés de la base de données :**

| Schéma | Tables actives | Volume principal |
|--------|---------------|-----------------|
| `sales` | 6 tables | 1 922 452 transactions historiques |
| `inventory` | 10 tables | 1 809 043 lignes historique ventes, 46 213 niveaux stock |
| `public` | ~12 tables | 155 mémoires agents, 1 959 logs |
| `monitoring` | 2 tables | Cycles & scripts coaching |
| `agent` | 8 tables | Legacy — non utilisé activement |

---

## 2. Architecture technique

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CLIENTS                                          │
│         Dashboard Angular          Coach Chat UI                         │
└──────────────────┬──────────────────────────┬───────────────────────────┘
                   │ WebSocket                 │ REST POST /api/v1/coach/chat
                   ▼                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    FastAPI — main.py (port 8000)                         │
│                                                                          │
│  ┌──────────────────┐   ┌──────────────────┐   ┌─────────────────────┐  │
│  │  SALES MODULE    │   │ INVENTORY MODULE │   │  AUTH / MONITORING  │  │
│  │  port 8001       │   │  port 8002       │   │  auth_router.py     │  │
│  │  /api/v1/...     │   │  /inventory/...  │   │  monitoring_router  │  │
│  └────────┬─────────┘   └────────┬─────────┘   └─────────────────────┘  │
└───────────┼──────────────────────┼─────────────────────────────────────┘
            │                      │
            ▼                      ▼
┌───────────────────────────────────────────────────────────────────────┐
│                    PostgreSQL — ooredoo_sales                          │
│   schemas: sales | inventory | public | monitoring | agent             │
└───────────────────────────────────────────────────────────────────────┘
            │                      │
            ▼                      ▼
┌──────────────────┐   ┌─────────────────────────────────────────────┐
│  Milvus (19530)  │   │  Ollama (11434)         Langfuse (3001)     │
│  coaching_scripts│   │  nomic-embed-text (768) Observabilité       │
│  (vectorDB RAG)  │   │  llama3.2:latest (LLM)  cycles agents       │
└──────────────────┘   └─────────────────────────────────────────────┘
```

**Connexions base de données :**

| Module | Driver | Pool | Usage |
|--------|--------|------|-------|
| `sales-module` (async) | `asyncpg` | max=10 | Lecture POS, forecast |
| `realtime_simulator.py` | `asyncpg` | max=5 | INSERT transactions RT |
| `inventory-module` | `psycopg2` | ThreadedConnectionPool(2,10) | Pipeline complet |
| `coach_chat.py` | `psycopg2` (run_in_executor) | connexion directe | Stock + ventes |
| `auth_router.py` | `psycopg2` | connexion directe | Auth sessions |

---

## 3. Base de données

**Nom :** `ooredoo_sales`  
**Host :** localhost:5432  
**User :** postgres

### Répartition des schémas

```
ooredoo_sales
├── sales         ← Données métier : boutiques, agents, produits, transactions, objectifs
├── inventory     ← Gestion de stock : niveaux, historiques, alertes, recommandations IA
├── public        ← Authentification app, logs agents, mémoire IA, interactions chat
├── monitoring    ← Cycles de traitement, scripts coaching générés par les agents
└── agent         ← Legacy — schéma alternatif non utilisé activement
```

---

## 4. Schéma SALES

> Données commerciales brutes et référentiels métier.

### 4.1 `sales.boutiques` — Référentiel boutiques

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `store_id` | text | **PK** | Code boutique (ex: "I63") |
| `store_name` | text | NOT NULL | Nom complet |
| `address` | text | | Adresse |
| `ville` | text | | Ville |
| `region` | text | | Région |
| `manager_name` | text | | Nom du manager |
| `phone` | text | | Téléphone |
| `active` | boolean | | Boutique active |
| `created_at` | timestamp | | Date création |

**Volume :** 201 boutiques actives  
**Index :** `idx_boutiques_active`, `idx_boutiques_ville`

---

### 4.2 `sales.agents` — Conseillers de vente

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `agent_id` | integer | **PK** | ID conseiller |
| `agent_name` | text | NOT NULL | Nom complet |
| `store_id` | text | **FK** → boutiques | Boutique d'affectation |
| `role` | text | | Rôle (conseiller, manager) |
| `phone` | text | | Téléphone |
| `email` | text | | Email |
| `performance_level` | text | | Niveau de performance |
| `created_at` | timestamp | | Date création |

**Volume :** 687 agents  
**FK :** `store_id` → `sales.boutiques.store_id`  
**Index :** `idx_agents_store`, `idx_agents_performance`

---

### 4.3 `sales.produits` — Catalogue produits (source de vérité SKU)

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `sku` | integer | **PK** | Code produit unique |
| `nom` | text | NOT NULL | Nom produit |
| `categorie` | text | | Catégorie (Smartphone, Forfait...) |
| `famille` | text | | Famille produit |
| `prix_ht` | numeric | | Prix HT en TND |
| `prix_ttc` | numeric | | Prix TTC en TND |
| `marge_pct` | numeric | | Taux de marge % |
| `stock_initial` | integer | | Stock initial livraison |
| `actif` | boolean | | Produit actif au catalogue |
| `created_at` | timestamp | | Date création |

**Volume :** 4 593 produits  
**Rôle central :** clé de jointure pour `transactions`, `stock_levels`, `product_master`, `promotions`  
**Index :** `idx_produits_categorie`, `idx_produits_famille`, `idx_produits_actif`

---

### 4.4 `sales.transactions` — Historique transactions (source principale)

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | bigint | **PK** | ID transaction |
| `transaction_date` | timestamp | NOT NULL | Date et heure précise |
| `date_only` | date | NOT NULL | Date seule (pour agréger) |
| `heure` | integer | NOT NULL | Heure (0-23) |
| `store_id` | text | **FK** → boutiques | Boutique |
| `agent_id` | integer | **FK** → agents | Conseiller |
| `sku` | integer | **FK** → produits | Produit vendu |
| `quantity` | integer | | Quantité |
| `prix_unitaire` | numeric | | Prix unitaire |
| `lig_ht` | numeric | | Montant HT ligne |
| `lig_ttc` | numeric | | Montant TTC ligne |
| `marge` | numeric | | Marge réalisée |
| `payment_method` | text | | Mode de paiement |
| `created_at` | timestamp | | Date insertion |

**Volume :** 1 922 452 lignes  
**FK :** `store_id` → boutiques, `agent_id` → agents, `sku` → produits  
**Index composites :** `idx_transactions_composite(date_only, store_id, agent_id)`, `idx_transactions_date`, `idx_transactions_store`, `idx_transactions_sku`, `idx_transactions_agent`, `idx_transactions_heure`

---

### 4.5 `sales.transactions_rt` — Transactions temps réel (simulateur)

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `sale_id` | uuid | **PK** | ID unique UUID |
| `date_vente` | timestamp | NOT NULL | Date et heure |
| `date_only` | date | NOT NULL | Date seule |
| `heure` | integer | NOT NULL | Heure |
| `store_id` | text | NOT NULL | Boutique |
| `agent_id` | integer | | Conseiller (peut être NULL) |
| `cod_prod` | integer | | SKU produit (INTEGER) |
| `des_produit` | text | | Nom produit |
| `lig_ttc` | numeric | | Montant TTC |
| `lig_ht` | numeric | | Montant HT |
| `lig_tva` | numeric | | TVA |
| `qte_produit` | integer | | Quantité |
| `created_at` | timestamp | | Date insertion |

**Volume :** 103 lignes (RT actif)  
**Rôle :** Reçoit les transactions injectées toutes les 15 secondes par `realtime_simulator.py` via asyncpg pool  
**Fusionné** avec `transactions` dans la vue `vw_ca_par_boutique` (UNION ALL)  
**Index :** `idx_transactions_rt_composite(date_only, store_id)`, `idx_transactions_rt_date`, `idx_transactions_rt_store`, `idx_transactions_rt_agent`

---

### 4.6 `sales.objectifs` — Objectifs journaliers

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID objectif |
| `store_id` | text | **FK** → boutiques | Boutique |
| `agent_id` | integer | | Conseiller (NULL = objectif boutique) |
| `date_objectif` | date | NOT NULL | Date de l'objectif |
| `objectif_ca` | numeric | | CA à atteindre en TND |
| `objectif_transactions` | integer | | Nb transactions cible |
| `objectif_panier_moyen` | numeric | | Panier moyen cible |
| `created_at` | timestamp | | Date création |

**Volume :** 21 105 lignes  
**FK :** `store_id` → boutiques  
**Index :** `idx_objectifs_store_date(store_id, date_objectif)`, `idx_objectifs_agent`

---

## 5. Schéma INVENTORY

> Gestion des stocks : niveaux courants, historiques, alertes IA, recommandations.

### 5.1 `inventory.stock_levels` — Niveaux de stock courants

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | bigint | **PK** | ID |
| `store_id` | text | **FK** → boutiques | Boutique |
| `sku` | integer | **FK** → produits | Produit |
| `quantity` | integer | | Quantité physique totale |
| `quantity_reserved` | integer | | Quantité réservée |
| `quantity_available` | integer | | Quantité disponible = qty - reserved |
| `last_received` | date | | Dernière livraison |
| `last_sold` | date | | Dernière vente |
| `updated_at` | timestamp | | Dernière mise à jour |
| `remaining_days_of_stock` | float | | Jours de stock restants |
| `last_updated` | timestamp | | Timestamp système |

**Volume :** 46 213 lignes (201 boutiques × ~230 SKUs actifs en moyenne)  
**Pour I63 :** 113 SKUs (3 ruptures, 57 critiques, 29 OK)  
**Contrainte UNIQUE :** `(sku, store_id)` — un seul niveau par produit par boutique  
**FK :** `sku` → `sales.produits.sku`, `store_id` → `sales.boutiques.store_id`  
**Index :** `idx_stock_store_sku(store_id, sku)`, `idx_stock_low_qty(store_id)`

---

### 5.2 `inventory.stock_history` — Historique niveaux de stock

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | bigint | **PK** | ID |
| `record_date` | date | NOT NULL | Date de la prise de snapshot |
| `store_id` | text | NOT NULL | Boutique |
| `store_name` | text | | Nom boutique (dénormalisé) |
| `region` | text | | Région |
| `sku` | integer | NOT NULL | Produit |
| `product_name` | text | | Nom produit (dénormalisé) |
| `category` | text | | Catégorie |
| `stock_level` | integer | | Niveau de stock ce jour |
| `is_stockout` | boolean | | Rupture = true |
| `created_at` | timestamp | | Date insertion |

**Volume :** 844 987 lignes  
**Usage :** Entraînement modèles de prévision (TimesFM), calcul vélocité historique  
**Index :** `idx_sh_date_store(record_date, store_id)`, `idx_sh_sku`

---

### 5.3 `inventory.sales_history` — Historique ventes pour l'inventaire

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | bigint | **PK** | ID |
| `record_date` | date | NOT NULL | Date de vente |
| `store_id` | text | NOT NULL | Boutique |
| `store_name` | text | | Nom boutique (dénormalisé) |
| `region` | text | | Région |
| `sku` | integer | NOT NULL | Produit |
| `product_name` | text | | Nom produit (dénormalisé) |
| `category` | text | | Catégorie |
| `quantity_sold` | integer | | Quantité vendue |
| `revenue` | numeric | | Revenu généré |
| `unit_price` | numeric | | Prix unitaire |
| `is_promo` | boolean | | Sous promotion |
| `event_name` | text | | Événement associé |
| `event_type` | text | | Type d'événement |
| `season` | text | | Saison (ete, hiver...) |
| `created_at` | timestamp | | Date insertion |

**Volume :** 1 809 043 lignes  
**Usage :** Source principale pour le pipeline d'inventaire (`pg_data_loader.py`), calcul de demande, prévision de réapprovisionnement  
**Index :** `idx_slh_date_store(record_date, store_id)`, `idx_slh_sku`

---

### 5.4 `inventory.product_master` — Paramètres logistiques produits

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `sku` | integer | **PK** + **FK** → produits | Produit |
| `product_name` | text | | Nom (dénormalisé) |
| `category` | text | | Catégorie |
| `unit_cost` | numeric | | Coût d'achat unitaire |
| `unit_price` | numeric | | Prix de vente |
| `lead_time_days` | integer | | Délai livraison en jours |
| `lead_time_std` | integer | | Écart-type délai livraison |
| `moq` | integer | | Quantité minimum de commande |
| `holding_cost_pct` | numeric | | Coût de possession % |
| `order_cost` | numeric | | Coût fixe par commande |
| `lifecycle_stage` | text | | Cycle de vie (launch, growth, mature, decline) |
| `created_at` | timestamp | | Date création |
| `updated_at` | timestamp | | Date mise à jour |

**Volume :** 4 178 lignes  
**FK :** `sku` → `sales.produits.sku`  
**Index :** `idx_product_master_category`, `idx_product_master_lifecycle`

---

### 5.5 `inventory.alerts` — Alertes générées par les agents IA

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID alerte |
| `store_id` | text | | Boutique |
| `sku` | integer | | Produit concerné |
| `alert_type` | text | | Type (stockout_risk, low_stock, overstock) |
| `severity` | text | | Sévérité (critical, warning, info) |
| `message` | text | | Message alerte |
| `status` | text | | Statut (open, resolved, acknowledged) |
| `created_at` | timestamp | | Date création |
| `resolved_at` | timestamp | | Date résolution |
| `triggered_at` | timestamp | | Date déclenchement |
| `recommended_action` | text | | Action recommandée |
| `agent_run_id` | text | | Référence cycle IA |

**Volume :** 143 alertes actives  
**Index :** `idx_alerts_store`, `idx_alerts_status`

---

### 5.6 `inventory.recommendations` — Recommandations de réapprovisionnement IA

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | uuid | **PK** | ID recommandation |
| `sku` | integer | NOT NULL | Produit |
| `store_id` | text | NOT NULL | Boutique |
| `recommendation_type` | text | | Type (reorder, adjust, hold) |
| `action` | text | | Action textuelle |
| `order_qty` | integer | | Quantité à commander |
| `urgency` | text | | Urgence (critical, high, medium, low) |
| `confidence` | numeric | | Score de confiance (0-1) |
| `recommendation_text` | text | | Texte explicatif |
| `trade_offs` | text | | Compromis identifiés |
| `escalate_to_human` | boolean | | Escalade vers humain requise |
| `escalation_reason` | text | | Raison escalade |
| `status` | text | | Statut (pending, approved, rejected, executed) |
| `decided_by` | text | | Qui a décidé |
| `decided_at` | timestamp | | Date décision |
| `created_at` | timestamp | | Date génération |
| `agent_run_id` | text | | Référence cycle IA |
| `order_cost` | numeric | | Coût de la commande |
| `holding_cost` | numeric | | Coût de possession |
| `suggested_quantity` | integer | | Quantité suggérée par EOQ |

**Volume :** 148 recommandations  
**Index :** `idx_rec_sku_store(sku, store_id)`, `idx_rec_status`

---

### 5.7 `inventory.context_adjustments` — Ajustements contextuels

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID |
| `sku` | integer | NOT NULL | Produit |
| `store_id` | text | NOT NULL | Boutique |
| `demand_uplift_pct` | numeric | | Boost de demande % |
| `adjustment_source` | text | | Source (weather, promo, event) |
| `weather_impact` | numeric | | Impact météo |
| `promo_impact` | numeric | | Impact promotion |
| `event_impact` | numeric | | Impact événement |
| `holiday_impact` | numeric | | Impact jour férié |
| `signals` | jsonb | | Tous les signaux en JSON |
| `dominant_signal` | text | | Signal dominant |
| `confidence` | numeric | | Confiance 0-1 |
| `interpretation` | text | | Interprétation textuelle |
| `valid_from` | date | | Début validité |
| `valid_to` | date | | Fin validité |
| `category` | text | | Catégorie produit |
| `created_at` | timestamp | | Date création |
| `agent_run_id` | integer | | Référence cycle IA |

**Volume :** 191 ajustements actifs  
**Contrainte UNIQUE :** `(sku, store_id, valid_from)` — un ajustement par produit/boutique/période  
**Index :** `idx_ctx_adj_sku_store(sku, store_id)`, `ctx_adj_unique`

---

### 5.8 `inventory.agent_runs` — Journal d'exécution des agents inventaire

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID run |
| `cycle_id` | text | | Identifiant cycle |
| `agent_name` | text | | Nom de l'agent (AnalysisAgent, ContextAgent, DecisionAgent) |
| `store_id` | text | | Boutique |
| `sku` | text | | SKU traité |
| `started_at` | timestamp | | Début |
| `completed_at` | timestamp | | Fin |
| `duration_ms` | float | | Durée en ms |
| `status` | text | | Statut (success, error) |
| `input_summary` | jsonb | | Résumé entrée |
| `output_summary` | jsonb | | Résumé sortie |
| `error_message` | text | | Message erreur |
| `items_processed` | integer | | Nombre items traités |
| `items_succeeded` | integer | | Succès |
| `items_failed` | integer | | Échecs |
| `alerts_generated` | integer | | Alertes générées |
| `recommendations_generated` | integer | | Recommandations générées |
| `batch_id` | text | | ID batch de traitement |

**Volume :** 3 097 runs  

---

### 5.9 `inventory.promotions` — Promotions actives

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | bigint | **PK** | ID |
| `promo_id` | text | UNIQUE | Code promo |
| `promo_name` | text | | Nom promotion |
| `start_date` | date | NOT NULL | Début |
| `end_date` | date | NOT NULL | Fin |
| `sku` | integer | | Produit concerné (NULL = tous) |
| `product_name` | text | | Nom produit |
| `category` | text | | Catégorie |
| `discount_pct` | numeric | | Remise % |
| `promo_type` | text | | Type (flash, seasonal, bundle) |
| `scope` | text | | Périmètre (national, regional, store) |

**Volume :** 15 promotions  
**Index :** `idx_promotions_dates(start_date, end_date)`, `idx_promotions_sku`

---

### 5.10 `inventory.business_objectives` — Objectifs métier des agents

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID |
| `objective_type` | text | NOT NULL | Type (minimize_stockout, optimize_cost...) |
| `label` | text | | Label affiché |
| `description` | text | | Description |
| `is_active` | boolean | | Actif |
| `priority` | integer | | Priorité |
| `created_at` | timestamp | | Date création |

**Volume :** 6 objectifs  
**Usage :** Paramétrer les agents `DecisionAgent` pour leurs arbitrages

---

## 6. Schéma PUBLIC

> Authentification utilisateurs, logs des agents sales, mémoire IA, interactions coach.

### 6.1 `public.app_users` — Utilisateurs de l'application

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID interne |
| `user_id` | varchar(30) | UNIQUE NOT NULL | Identifiant utilisateur |
| `username` | varchar(50) | UNIQUE NOT NULL | Login |
| `password_hash` | varchar(64) | NOT NULL | Hash SHA256 |
| `full_name` | varchar(100) | | Nom complet |
| `role` | varchar(20) | NOT NULL | Rôle (manager, conseiller, admin) |
| `store_id` | varchar(20) | | Boutique assignée |
| `store_name` | varchar(100) | | Nom boutique |
| `initials` | varchar(5) | | Initiales affichage |
| `color` | varchar(10) | | Couleur avatar |
| `advisor_id` | varchar(20) | | ID conseiller lié |
| `actif` | boolean | | Compte actif |
| `created_at` | timestamp | | Date création |
| `last_login` | timestamp | | Dernière connexion |

**Volume :** 7 utilisateurs  
**Index :** `idx_au_username`, `idx_au_store`

---

### 6.2 `public.app_sessions` — Sessions JWT

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID |
| `token` | varchar(64) | UNIQUE NOT NULL | Token de session |
| `user_id` | varchar(30) | NOT NULL | Référence utilisateur |
| `expires_at` | timestamp | NOT NULL | Expiration |
| `created_at` | timestamp | | Date création |
| `last_used` | timestamp | | Dernière utilisation |
| `ip_address` | varchar(45) | | IP client |

**Index :** `idx_as_token`, `idx_as_user`, `idx_as_exp`

---

### 6.3 `public.agent_cycles` — Cycles d'exécution agents sales

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID |
| `cycle_id` | varchar(50) | UNIQUE | Identifiant cycle (UUID court) |
| `store_id` | varchar(10) | | Boutique |
| `triggered_by` | varchar(20) | | Déclencheur (cron, websocket, manual) |
| `urgency_level` | varchar(10) | | LOW/MEDIUM/HIGH/CRITICAL |
| `urgency_score` | float | | Score 0-1 |
| `gap_pct` | float | | Écart objectif % |
| `gap_amount` | float | | Écart en TND |
| `ca_today` | float | | CA réalisé |
| `ca_target` | float | | Objectif CA |
| `forecast_eod` | float | | Prévision fin de journée |
| `analyst_summary` | text | | Résumé analyste en français |
| `strategie` | text | | Stratégie recommandée |
| `nb_actions` | integer | | Nombre d'actions générées |
| `cause_racine` | text | | Cause racine identifiée |
| `rag_used` | boolean | | RAG Milvus utilisé |
| `nb_rag_scripts` | integer | | Nombre scripts RAG |
| `weather_label` | varchar(50) | | Météo |
| `weather_effect` | float | | Effet météo sur demande |
| `total_ms` | float | | Durée totale cycle |
| `nodes_executed` | integer | | Nodes LangGraph exécutés |
| `errors_count` | integer | | Erreurs |
| `status` | varchar(20) | | success/error/partial |
| `created_at` | timestamp | | Date |

**Volume :** 83 cycles  
**Index :** `idx_cycles_store`, `idx_cycles_urgency`, `idx_cycles_created`

---

### 6.4 `public.agent_logs` — Logs détaillés par node

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID |
| `cycle_id` | varchar(50) | | Référence cycle |
| `store_id` | varchar(10) | | Boutique |
| `agent_name` | varchar(30) | | Nom agent (analyste, stratege, coach) |
| `node_name` | varchar(50) | | Nom du node LangGraph |
| `status` | varchar(20) | | success/error/warning |
| `input_state` | jsonb | | État LangGraph en entrée |
| `output_state` | jsonb | | État LangGraph en sortie |
| `duration_ms` | float | | Durée node en ms |
| `error_msg` | text | | Message erreur |
| `metadata` | jsonb | | Métadonnées supplémentaires |
| `created_at` | timestamp | | Date |

**Volume :** 1 959 logs  
**Index :** `idx_logs_cycle`, `idx_logs_agent`, `idx_logs_status`, `idx_logs_created`

---

### 6.5 `public.agent_memory` — Mémoire persistante agents sales

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID |
| `agent_name` | varchar(50) | NOT NULL | Nom agent |
| `store_id` | varchar(50) | NOT NULL | Boutique |
| `cycle_id` | varchar(100) | | Cycle de création |
| `memory_type` | varchar(50) | NOT NULL | Type (gap_trend, urgency_history, revenue_trend) |
| `memory_data` | jsonb | NOT NULL | Données en JSON |
| `created_at` | timestamp | | Date |

**Volume :** 155 entrées  
**Usage :** Chaque cycle agent_analyste lit la mémoire du cycle précédent pour calibrer le niveau d'urgence  
**Index :** `idx_agent_memory_agent_store(agent_name, store_id, memory_type)`

---

### 6.6 `public.coach_interactions` — Historique conversations coach chat

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID |
| `advisor_name` | varchar(100) | | Nom conseiller |
| `store_id` | varchar(20) | | Boutique |
| `message` | text | | Question posée |
| `response` | text | | Réponse du coach IA |
| `gap_pct` | float | | Gap au moment de la question |
| `urgency` | varchar(10) | | Niveau urgence |
| `rag_used` | boolean | | RAG utilisé |
| `nb_rag_scripts` | integer | | Nb scripts RAG |
| `conseil_type` | varchar(30) | | Type conseil (inventory/script, sales/coaching...) |
| `confidence` | float | | Score de confiance |
| `created_at` | timestamp | | Date |

**Volume :** 35 interactions  
**Index :** `idx_ci_advisor`, `idx_ci_created`

---

## 7. Schéma MONITORING

### 7.1 `monitoring.coaching_scripts` — Scripts coaching générés par les agents

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID |
| `cycle_id` | text | | Référence cycle |
| `store_id` | text | | Boutique |
| `urgency` | text | | Niveau urgence |
| `gap_pct` | float | | Gap au moment de génération |
| `action` | text | | Action recommandée |
| `produit` | text | | Produit cible |
| `argument` | text | | Argument de vente |
| `impact` | text | | Impact estimé |
| `created_at` | timestamp | | Date |

**Usage :** Alimenté par `agent_logger.py` après chaque cycle stratège. Ces scripts sont ensuite **vectorisés** (Ollama nomic-embed-text) et insérés dans **Milvus** `coaching_scripts` pour le RAG.

---

### 7.2 `monitoring.cycle_logs` — Synthèse cycles

| Colonne | Type | Contrainte | Description |
|---------|------|-----------|-------------|
| `id` | integer | **PK** | ID |
| `cycle_id` | text | UNIQUE | Identifiant cycle |
| `store_id` | text | | Boutique |
| `trigger` | text | | Déclencheur |
| `started_at` | timestamp | | Début |
| `completed_at` | timestamp | | Fin |
| `total_ms` | float | | Durée totale |
| `urgency_level` | text | | Niveau urgence |
| `gap_pct` | float | | Gap % |
| `nb_actions` | integer | | Actions générées |
| `rag_used` | boolean | | RAG utilisé |
| `nb_rag_scripts` | integer | | Scripts RAG |
| `errors_count` | integer | | Erreurs |
| `nodes_executed` | integer | | Nodes exécutés |
| `created_at` | timestamp | | Date |

---

## 8. Schéma AGENT

> Schéma alternatif legacy — duplique en partie public. Actuellement **non utilisé activement** par le code de production. Conservé pour compatibilité.

Tables : `sessions`, `agent_cycles`, `agent_logs`, `agent_context`, `agent_memory_long`, `agent_memory_short`, `recommendations`, `actions_executed`

Relations internes : tout est lié à `agent.sessions.id` via FK.

---

## 9. Vues SQL

### `sales.vw_ca_par_boutique` — CA agrégé par boutique et par jour

```sql
SELECT store_id, date_only,
       SUM(lig_ttc) AS ca_total,
       COUNT(*) AS nb_transactions,
       AVG(lig_ttc) AS avg_ticket
FROM (
    SELECT store_id, date_only, lig_ttc FROM sales.transactions
    UNION ALL
    SELECT store_id, date_only, lig_ttc FROM sales.transactions_rt
) combined
GROUP BY store_id, date_only
```
**Usage :** Lecture principale du CA par `coach_chat.py` et l'orchestrateur

---

### `sales.vw_ventes_par_agent` — Performances par conseiller

```sql
SELECT agent_id, store_id, DATE(transaction_date) AS date_vente,
       SUM(lig_ttc) AS ca, COUNT(*) AS nb_transactions,
       COUNT(DISTINCT sku) AS nb_produits, AVG(lig_ttc) AS ticket_moyen
FROM sales.transactions
GROUP BY agent_id, store_id, DATE(transaction_date)
```
**Usage :** Dashboard performances conseillers

---

### `inventory.vw_stock_enriched` — Stock enrichi avec produits

```sql
SELECT sl.store_id, sl.sku, p.nom, p.categorie,
       sl.quantity, sl.quantity_available,
       CASE WHEN sl.quantity_available < 5  THEN 'RUPTURE'
            WHEN sl.quantity_available < 10 THEN 'BAS'
            ELSE 'OK' END AS stock_status,
       p.prix_ttc, sl.last_sold
FROM inventory.stock_levels sl
JOIN sales.produits p ON sl.sku = p.sku
```
**Usage :** Page inventaire du dashboard

---

### `inventory.vw_active_promotions` — Promotions en cours

```sql
SELECT promo_id, promo_name, sku, product_name, discount_pct, start_date, end_date
FROM inventory.promotions
WHERE end_date >= CURRENT_DATE
```

---

### `monitoring.vw_cycle_summary` — Synthèse journalière des cycles

```sql
SELECT DATE(created_at) AS jour, store_id,
       COUNT(*) AS nb_cycles, AVG(total_ms)::integer AS avg_ms,
       AVG(gap_pct)::numeric(5,1) AS avg_gap,
       SUM(CASE WHEN urgency_level='CRITICAL' THEN 1 ELSE 0 END) AS critical_count,
       SUM(CASE WHEN rag_used THEN 1 ELSE 0 END) AS rag_count
FROM monitoring.cycle_logs
GROUP BY DATE(created_at), store_id
```

---

## 10. Diagramme des relations

```
sales.boutiques (store_id PK)
│
├──< sales.agents (store_id FK)
│       │
│       └──< sales.transactions (agent_id FK)
│
├──< sales.objectifs (store_id FK)
│
├──< sales.transactions (store_id FK)
│       │
│       └──> sales.produits (sku FK)
│
├──< sales.transactions_rt (store_id — sans FK formelle)
│
└──< inventory.stock_levels (store_id FK)
        │
        └──> sales.produits (sku FK)


sales.produits (sku PK)
│
├──< sales.transactions (sku FK)
├──< inventory.stock_levels (sku FK)
├──< inventory.product_master (sku FK)
└──< inventory.promotions (sku — sans FK formelle)


public.agent_cycles (cycle_id PK)
│
└──< public.agent_logs (cycle_id — référence textuelle)
└──< public.agent_memory (cycle_id — référence textuelle)


monitoring.coaching_scripts (id PK)
│
└── vectorisé → Milvus.coaching_scripts (RAG)


public.app_users (user_id PK)
│
└──< public.app_sessions (user_id — référence textuelle)


inventory.agent_runs (id PK)
│
├──< inventory.alerts (agent_run_id)
├──< inventory.recommendations (agent_run_id)
└──< inventory.context_adjustments (agent_run_id)
```

---

## 11. Flux de données

### Flux Sales (pipeline temps réel toutes les 15 min)

```
[PostgreSQL sales.transactions]
         │
         ▼
  Agent Analyste (LangGraph 11 nodes)
    → lit: vw_ca_par_boutique, objectifs, transactions
    → calcule: gap_pct, urgency_score, forecast EOD
    → écrit: public.agent_cycles, public.agent_logs, public.agent_memory
         │
         ▼ (si urgence >= MEDIUM)
  Agent Stratège (LangGraph 5 nodes)
    → lit: météo API, agent_memory, Milvus coaching_scripts
    → génère: strategie_actions (JSON), coaching_cards
    → écrit: monitoring.cycle_logs, monitoring.coaching_scripts
         │
         ├── [WebSocket] → Dashboard Angular (metrics_update)
         │
         └── [Milvus] agent_logger.py vectorise les scripts
                  → INSERT coaching_scripts (nomic-embed-text 768D)
```

---

### Flux Realtime (toutes les 15 secondes)

```
realtime_simulator.py
    → lit: sales.transactions (données historiques I63)
    → simule: 1 transaction aléatoire
    → écrit: sales.transactions_rt (asyncpg pool max=5)
         │
         └── visible immédiatement via vw_ca_par_boutique (UNION ALL)
```

---

### Flux Inventory (déclenché par pipeline API)

```
[PostgreSQL inventory.sales_history + stock_history]
         │
         ▼
  pg_data_loader.py (psycopg2 ThreadedConnectionPool)
    → load_sales_history() → DataFrame 1.8M lignes
    → load_stock_levels()  → DataFrame 46K lignes
         │ (cache TTL 5min dans _DataCache)
         ▼
  Pour chaque SKU (143 SKUs actifs I63) :
  
  AnalysisAgent (3 nodes)
    → calcule: ABC, vélocité, saisonnalité, jours de stock
    → écrit: inventory.agent_runs

  ContextAgent (3 nodes)
    → lit: météo, promotions, événements
    → écrit: inventory.context_adjustments

  DecisionAgent (3 nodes)
    → lit: alertes existantes, business_objectives
    → génère: alerts, recommendations
    → écrit: inventory.alerts, inventory.recommendations
         │
         └── [API] GET /inventory/{store_id} → résultat JSON
```

---

### Flux Coach Chat (par requête REST)

```
POST /api/v1/coach/chat
    {message, advisor_name, store_id, context}
         │
         ▼
  classify_intent()
    │
    ├── domain=inventory
    │     → psycopg2: inventory.stock_levels JOIN sales.produits
    │     → psycopg2: sales.transactions (top vendeurs 7j)
    │     → LLM OpenRouter → réponse stock
    │
    └── domain=sales
          → psycopg2: vw_ca_par_boutique (latest date)
          → optionnel: Milvus coaching_scripts (RAG)
          → LLM OpenRouter → réponse coach
          │
          └── écrit: public.coach_interactions
```

---

### Flux Auth

```
POST /auth/login {username, password}
    → public.app_users (vérif password_hash SHA256)
    → génère token → INSERT public.app_sessions
    → retourne {token, user_info, store_id}

GET /api/... avec header Authorization: Bearer {token}
    → public.app_sessions (vérifie token + expires_at)
    → retourne données si valide
```

---

## 12. Modules applicatifs

```
D:\backend\multi-agent-sales-inventory\
│
├── main.py                    ← Orchestrateur principal FastAPI, CronTrigger 15min,
│                                WebSocket broadcast, routes API sales
│
├── auth_router.py             ← Login/logout/me, JWT sessions, public.app_users
├── monitoring_router.py       ← Monitoring cycles, logs, alertes
├── agent_logger.py            ← Observabilité: écrit public.agent_logs, cycles,
│                                mémoire, coaching_scripts + insert Milvus
│
├── sales-module/
│   ├── main.py                ← FastAPI secondaire port 8001
│   ├── data/
│   │   ├── postgres_provider.py   ← asyncpg pool max=10, fetch POS/forecast
│   │   ├── realtime_simulator.py  ← asyncpg pool max=5, INSERT transactions_rt
│   │   ├── rag_retriever.py       ← Milvus singleton + recherche RAG
│   │   └── csv_realtime_provider.py
│   └── modules/coaching/agents/
│       ├── analyst/           ← Agent Analyste (11 nodes LangGraph)
│       │   ├── agent.py       ← Définition graph LangGraph
│       │   ├── nodes.py       ← 11 nodes: receive_pos → output
│       │   └── prompts.py     ← Few-shot prompt JSON
│       ├── stratege/          ← Agent Stratège (5 nodes LangGraph)
│       │   ├── nodes.py       ← fetch_context, rag_search, analyze, generate, build
│       │   ├── prompts.py     ← Constitutional AI G1-G8
│       │   └── tools.py       ← fetch_full_context (météo, fériés, promos)
│       └── coach/
│           ├── coach_chat.py  ← Endpoint POST /api/v1/coach/chat (4 modes)
│           └── tools.py       ← save_interaction → public.coach_interactions
│
├── inventory-module/
│   ├── main.py                ← FastAPI secondaire port 8002
│   ├── src/
│   │   ├── pg_data_loader.py  ← psycopg2 ThreadedConnectionPool, load_sales_history,
│   │   │                         load_stock_levels, load_product_master
│   │   ├── agents/
│   │   │   ├── analysis/      ← AnalysisAgent (ABC, vélocité, saisonnalité)
│   │   │   ├── context/       ← ContextAgent (météo, promos, événements)
│   │   │   └── decision/      ← DecisionAgent (alertes, recommandations EOQ)
│   │   ├── services/
│   │   │   └── orchestrator.py ← Pipeline complet par SKU
│   │   ├── tools/internal/
│   │   │   └── stock_tools.py ← _DataCache (TTL 5min), outils agents
│   │   └── forecasting/
│   │       └── timesfm_forecaster.py ← Prévision demande TimesFM/Prophet
│   └── db/
│       └── migrations/        ← Alembic migrations inventory schema
│
└── monitoring-module/
    ├── monitoring.py          ← Métriques agents, health checks
    └── agent_registry.py      ← Registre agents actifs
```

---

## 13. Services externes & infrastructure

### Docker Compose

| Service | Image | Port | Volume |
|---------|-------|------|--------|
| `standalone` (Milvus) | milvusdb/milvus:v2.4.9 | 19530 | volumes/milvus/ |
| `etcd` | bitnami/etcd:3.5.5 | — | volumes/etcd/ |
| `minio` (Milvus storage) | minio/minio:latest | 9001 | volumes/minio/ |
| `langfuse-server` | langfuse/langfuse:2 | 3001 | PostgreSQL dédié |

### Milvus — Collection `coaching_scripts`

| Champ | Type | Description |
|-------|------|-------------|
| `id` | INT64 (auto) | PK |
| `vector` | FLOAT_VECTOR[768] | Embedding nomic-embed-text |
| `pg_id` | INT64 | Référence monitoring.coaching_scripts.id |
| `categorie` | VARCHAR(100) | Catégorie produit |
| `situation` | VARCHAR(200) | Situation de vente |
| `action` | VARCHAR(200) | Action recommandée |
| `produit` | VARCHAR(100) | Produit cible |
| `argument` | VARCHAR(200) | Argument de vente |
| `impact` | VARCHAR(100) | Impact observé |
| `heure_min` | INT64 | Plage horaire début |
| `heure_max` | INT64 | Plage horaire fin |
| `jour_semaine` | INT64 | Jour semaine (-1=tous) |
| `store_id` | VARCHAR(20) | Boutique source |

**Index :** FLAT + COSINE  
**Alimentation :** `agent_logger.py` après chaque cycle stratège  
**Recherche :** Embedding query → top-6 scripts → bonus horaire/jour → top-3 injectés dans prompt LLM

### Ollama (localhost:11434)

| Modèle | Usage |
|--------|-------|
| `nomic-embed-text` | Embeddings RAG (768 dimensions) |
| `llama3.2:latest` | LLM coaching local (fallback) |

### OpenRouter (API externe)

| Modèle | Usage |
|--------|-------|
| `openai/gpt-oss-120b:free` | LLM principal coach chat |
| Fallback texte statique | Si clé absente ou timeout |

### Langfuse (localhost:3001)

Observabilité complète des cycles agents : traces par node, latences, scores RAG, historique urgences.
