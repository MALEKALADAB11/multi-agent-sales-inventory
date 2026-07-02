# Architecture Base de Données — Ooredoo Tunisia Agentic Retail Platform

**Base de données :** `ooredoo_sales` (PostgreSQL 15+)  
**Version :** 2026-06-30 (Post-Migration 008)  
**Mise à jour :** Dynamique temps réel — agents multi-domaines

---

## Table des matières

1. [Vue d'ensemble des schémas](#1-vue-densemble-des-schémas)
2. [Diagramme des relations](#2-diagramme-des-relations)
3. [Schéma `sales` — Core POS & Référentiels](#3-schéma-sales--core-pos--référentiels)
4. [Schéma `inventory` — Stock & Prévisions](#4-schéma-inventory--stock--prévisions)
5. [Schéma `supply` — Supply Chain](#5-schéma-supply--supply-chain)
6. [Schéma `market` — Intelligence Marché](#6-schéma-market--intelligence-marché)
7. [Schéma `customer` — Clients & Feedback](#7-schéma-customer--clients--feedback)
8. [Schéma `coaching` — Agents Coaching](#8-schéma-coaching--agents-coaching)
9. [Schéma `context` — Contexte Temps Réel *(Nouveau)*](#9-schéma-context--contexte-temps-réel-nouveau)
10. [Schéma `forecasting` — Features Prévision *(Nouveau)*](#10-schéma-forecasting--features-prévision-nouveau)
11. [Tables KPI (schema public)](#11-tables-kpi-schema-public)
12. [Schéma `monitoring` — Vues Observabilité](#12-schéma-monitoring--vues-observabilité)
13. [Index critiques](#13-index-critiques)
14. [Flux temps réel par agent](#14-flux-temps-réel-par-agent)
15. [Règles de qualité des données](#15-règles-de-qualité-des-données)

---

## 1. Vue d'ensemble des schémas

| Schéma | Nb tables | Rôle | Alimentation |
|---|---|---|---|
| `sales` | 9 | POS temps réel, référentiels boutiques/produits/agents | Transactions live + ETL nuit |
| `inventory` | 7 | Stock, historique ventes, promotions, recommandations | Agents + calcul nuit |
| `supply` | 6 | Commandes, mouvements stock, fournisseurs, n° série | Agents + WMS |
| `market` | 6 | Événements, concurrence, saisonnalité, réseau | ETL + API externe |
| `customer` | 3 | Segments, churn, NPS/CSAT | Enquêtes + agents |
| `coaching` | 5 | Events coaching, escalations, HITL, mémoire agents | Agents coaching |
| `context` | 1 | Contexte météo/trafic/réseau par boutique/heure | API temps réel *(Mig 008)* |
| `forecasting` | 1 | Features lag/rolling/forecast précalculées | Job nuit *(Mig 008)* |
| `monitoring` | 5 vues | Observabilité multi-agents, dashboard | Vues sur tables live |
| `public` | 6 | KPI agents/boutiques, cycles agents, interactions | Calcul nuit + agents |

**Total : ~44 tables + vues**

---

## 2. Diagramme des relations

```
sales.boutiques ◄──────────────────────── clé étrangère store_id ─────────────────────────────►
       │
       ├── sales.transactions_rt  (POS temps réel)
       ├── sales.transactions      (historique)
       ├── sales.objectifs         (objectifs global store/agent)
       ├── sales.daily_objectives_category (objectifs par catégorie) ← [Mig 008]
       ├── context.context_hourly_store     (météo/trafic/réseau)    ← [Mig 008]
       ├── inventory.stock_levels
       ├── inventory.stock_snapshot_enriched                          ← [Mig 008]
       ├── inventory.recommendations_computed                         ← [Mig 008]
       ├── forecasting.forecast_features_daily                        ← [Mig 008]
       ├── coaching.coaching_events
       ├── coaching.coaching_recommendations                          ← [Mig 008]
       ├── coaching.escalations
       ├── coaching.hitl_requests
       ├── coaching.agent_memory
       ├── agent_kpi_daily
       ├── store_kpi_daily
       └── supply.transfers (store_source / store_dest)

sales.agents ◄────────────────────────── agent_id ────────────────────────────────────────────►
       │
       ├── sales.transactions_rt (agent_id)
       ├── sales.transactions    (agent_id)
       ├── sales.objectifs       (agent_id)
       ├── agent_kpi_daily       (agent_id)
       ├── coaching.coaching_events (advisor_id)
       ├── coaching.hitl_requests   (approver_id)
       └── customer.nps_csat        (agent_id)

sales.produits ◄──────────────────────── sku ─────────────────────────────────────────────────►
       │
       ├── sales.transactions (sku)
       ├── inventory.stock_levels (sku)
       ├── inventory.sales_history (sku)
       ├── supply.purchase_orders (sku)
       ├── supply.stock_movements (sku)
       ├── supply.serial_numbers  (sku)
       └── supply.reorder_params  (sku)
```

---

## 3. Schéma `sales` — Core POS & Référentiels

### 3.1 `sales.boutiques`
Table maître des 202 boutiques Ooredoo Tunisie.

| Colonne | Type | Description |
|---|---|---|
| `store_id` | VARCHAR(50) PK | Identifiant boutique (ex: `I63`, `M01`, `S47`) |
| `nom` | VARCHAR(200) | Nom commercial de la boutique |
| `type_boutique` | VARCHAR(5) | I=Indirect, M=Officielle, S=Sous-franchise, C=B2B, T=Online |
| `canal` | VARCHAR(20) | `PHYSIQUE`, `ONLINE`, `B2B` |
| `region` | VARCHAR(100) | Région commerciale (Grand Tunis, Sahel, Sud…) |
| `wilaya` | VARCHAR(100) | Wilaya administrative (Tunis, Sousse, Sfax…) |
| `zone_commerciale` | VARCHAR(100) | Zone précise (Tunisia Mall Lac2, Géant Tunis…) |
| `latitude` | NUMERIC(10,7) | Coordonnée GPS latitude |
| `longitude` | NUMERIC(10,7) | Coordonnée GPS longitude |
| `capacite_conseillers` | INTEGER | Nombre de postes conseillers |
| `date_ouverture` | DATE | Date d'ouverture |
| `is_officielle` | BOOLEAN | TRUE si boutique officielle Ooredoo (M-prefix) |
| `rang_ca_region` | INTEGER | Rang CA dans la région |
| `statut` | VARCHAR(20) | `ACTIVE`, `CLOSED`, `TEMP_CLOSED` |
| `email_store` | VARCHAR(200) | Email boutique |

**Index :** `store_id` (PK), `wilaya`, `type_boutique`

---

### 3.2 `sales.produits`
Catalogue complet des 4 212 produits Ooredoo enrichi.

| Colonne | Type | Description |
|---|---|---|
| `sku` | INTEGER PK | Code SKU produit |
| `nom` | VARCHAR(500) | Nom produit complet |
| `categorie` | VARCHAR(10) | Code catégorie (50=Terminal, 88=Forfait, 30=Recharge…) |
| `gamme_libelle` | VARCHAR(100) | TERMINAL, FORFAIT, SIM_KIT, RECHARGE, ACCESSOIRE… |
| `famille_libelle` | VARCHAR(100) | Famille produit |
| `marque` | VARCHAR(100) | Samsung, Apple, Xiaomi, Huawei, Ooredoo… |
| `modele` | VARCHAR(200) | Référence modèle |
| `prix_ht` | NUMERIC(12,4) | Prix hors taxe TND |
| `prix_ttc` | NUMERIC(12,4) | Prix TTC TND |
| `pa_ht` | NUMERIC(12,4) | Prix d'achat HT calculé |
| `marge_pct_calc` | NUMERIC(6,2) | Marge calculée en % |
| `flag_terminal` | BOOLEAN | Terminal smartphone/tablette |
| `flag_forfait` | BOOLEAN | Forfait postpayé/prépayé |
| `flag_sim` | BOOLEAN | SIM/eSIM |
| `flag_recharge` | BOOLEAN | Recharge crédit |
| `flag_5g` | BOOLEAN | Compatible 5G |
| `flag_4g` | BOOLEAN | Compatible 4G/Box |
| `serialisable` | BOOLEAN | Nécessite numéro de série (IMEI) |
| `stockable` | BOOLEAN | Gestion physique en stock |
| `lead_time_days` | INTEGER | Délai approvisionnement en jours |
| `lead_time_std` | INTEGER | Écart-type délai |
| `moq` | INTEGER | Quantité minimum de commande |
| `lifecycle_stage` | VARCHAR(20) | `mature`, `launch`, `declining`, `discontinued` |
| `actif` | BOOLEAN | Produit actif |

**Catégories codes :** 50=Terminal, 88=Forfait postpayé, 80=Box/Fixe, 20=SIM Kit, 40=SIM Service, 30=Recharge, 70=Accessoire, 32=Accessoire Premium, 99=Service, 90=Digital, 10=Business

---

### 3.3 `sales.agents`
Profils des 580+ conseillers de vente enrichis.

| Colonne | Type | Description |
|---|---|---|
| `agent_id` | INTEGER PK | Identifiant agent |
| `nom` | VARCHAR(200) | Nom conseiller |
| `store_id` | VARCHAR(50) FK | Boutique principale |
| `performance_level` | VARCHAR(20) | `senior`, `confirmed`, `junior` |
| `date_embauche` | DATE | Date d'embauche |
| `anciennete_mois` | INTEGER | Ancienneté calculée en mois |
| `niveau_certification` | INTEGER | Niveau 1-5 certification Ooredoo |
| `quota_mensuel_ca` | NUMERIC(12,2) | Quota CA mensuel TND |
| `quota_activations` | INTEGER | Quota activations mensuel |
| `quota_postpaye` | INTEGER | Quota forfaits postpayés |
| `coach_score` | NUMERIC(5,2) | Score coaching cumulé (0-100) |
| `specialisation` | VARCHAR(50) | `TERMINAL`, `FORFAIT`, `CORPORATE`, `DATA` |
| `avatar_color` | VARCHAR(7) | Couleur avatar UI (#hexcode) |
| `statut` | VARCHAR(20) | `ACTIVE`, `INACTIVE`, `CONGE` |

---

### 3.4 `sales.transactions`
Historique POS complet — 1.49M+ lignes sur 4.5 ans.

| Colonne | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Identifiant transaction |
| `store_id` | VARCHAR(50) FK | Boutique |
| `agent_id` | INTEGER FK | Conseiller |
| `sku` | INTEGER FK | Produit vendu |
| `date_only` | DATE | Date de la transaction |
| `heure` | INTEGER | Heure (0-23) |
| `transaction_date` | TIMESTAMP | Datetime complet |
| `quantity` | INTEGER | Quantité vendue |
| `lig_ttc` | NUMERIC(14,4) | Montant ligne TTC |
| `is_promo` | BOOLEAN | Vendu en promotion |
| `payment_type` | VARCHAR(20) | `CASH`, `CARD`, `VIREMENT` |

**Index :** `(store_id, date_only)`, `(store_id, date_only, heure)`, `(agent_id, date_only)`

---

### 3.5 `sales.transactions_rt`
Transactions temps réel du jour courant (flush quotidien).

| Colonne | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Identifiant |
| `store_id` | VARCHAR(50) | Boutique |
| `agent_id` | INTEGER | Conseiller |
| `advisor_name` | VARCHAR(200) | Nom conseiller (dénormalisé pour perf) |
| `sku` | INTEGER | Produit |
| `created_at` | TIMESTAMP | Timestamp exact de la transaction |
| `date_only` | DATE | Date (pour index) |
| `montant` | NUMERIC(14,4) | Montant TTC TND |
| `quantity` | INTEGER | Quantité |
| `payment_method` | VARCHAR(20) | Moyen de paiement |

**Index :** `(store_id, created_at DESC)`, `(store_id, date_only)`, `(store_id, advisor_name, created_at DESC)`

**Principe :** Cette table est la source de vérité pour le CA du jour. L'agent analyste (`fetch_live_pos`) la lit en premier. Elle est purgée chaque nuit et les données migrées vers `sales.transactions`.

---

### 3.6 `sales.objectifs`
Objectifs globaux journaliers par boutique et agent.

| Colonne | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Identifiant |
| `store_id` | VARCHAR(50) FK | Boutique |
| `agent_id` | INTEGER | Conseiller (NULL = objectif boutique global) |
| `date_objectif` | DATE | Date cible |
| `objectif_ca` | NUMERIC(14,2) | CA cible TND |
| `objectif_activations` | INTEGER | Nombre activations cible |
| `objectif_postpaye` | INTEGER | Forfaits postpayés cible |

**Relation avec `sales.daily_objectives_category` :** `sales.objectifs` contient le montant global par boutique/agent ; `sales.daily_objectives_category` ventile ce montant par catégorie produit pour le coaching granulaire.

---

### 3.7 `sales.daily_objectives_category` *(Nouveau — Migration 008)*
Objectifs journaliers décomposés par catégorie produit.

| Colonne | Type | Description |
|---|---|---|
| `objective_id` | VARCHAR(60) PK | Format : `OBJ-{store_id}-{YYYYMMDD}-{CAT}` |
| `store_id` | VARCHAR(50) FK | Boutique |
| `objective_date` | DATE | Date cible |
| `product_category` | VARCHAR(100) | Postpayé, Recharge, Forfait data, SIM / eSIM, Terminal, Accessoire |
| `target_revenue_ttc` | NUMERIC(14,2) | CA cible TND pour cette catégorie |
| `target_units` | INTEGER | Nombre unités cible |
| `priority_weight` | NUMERIC(4,2) | Poids prioritaire (1.0=normal, 1.15=prioritaire) |
| `source_flag` | VARCHAR(50) | `COMPUTED_FROM_MONTHLY_TARGET` |

**Consommé par :** Vue `monitoring.category_gap_live`, CoachAgent cross-domain scoring.

---

### 3.8 `sales.rag_scripts`
Scripts de vente pour le RAG (Retrieval Augmented Generation).

| Colonne | Type | Description |
|---|---|---|
| `script_id` | VARCHAR(20) PK | Identifiant script (ex: `S-042`) |
| `title` | VARCHAR(200) | Titre du script |
| `category` | VARCHAR(50) | Catégorie produit ciblée |
| `trigger_type` | VARCHAR(50) | `UPSELL`, `OBJECTION`, `OPENING`, `CLOSING` |
| `script_text` | TEXT | Texte complet du script |
| `embedding` | vector(768) | Embedding vectoriel (pgvector) |
| `usage_count` | INTEGER | Nombre d'utilisations |
| `avg_rating` | NUMERIC(3,2) | Note moyenne 1-5 |
| `created_at` | TIMESTAMP | Date création |

---

## 4. Schéma `inventory` — Stock & Prévisions

### 4.1 `inventory.stock_levels`
Niveaux de stock courants par boutique et SKU.

| Colonne | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Identifiant |
| `store_id` | VARCHAR(50) FK | Boutique |
| `sku` | INTEGER FK | Produit |
| `quantity_available` | INTEGER | Quantité disponible |
| `quantity_reserved` | INTEGER | Quantité réservée (commandes en attente) |
| `quantity_on_order` | INTEGER | Quantité en commande |
| `stock_current` | INTEGER | Stock physique actuel |
| `stock_min` | INTEGER | Seuil minimum d'alerte |
| `last_updated` | TIMESTAMP | Dernière mise à jour |
| `remaining_days_of_stock` | NUMERIC(10,2) | Jours de stock restants calculés |

**Index :** `(store_id, quantity_available ASC)` — pour les alertes de rupture

---

### 4.2 `inventory.sales_history`
Historique agrégé quotidien pour le time-series et le forecasting.

| Colonne | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Identifiant |
| `store_id` | VARCHAR(50) FK | Boutique |
| `sku` | INTEGER | Produit |
| `product_name` | TEXT | Nom produit (dénormalisé) |
| `category` | VARCHAR(50) | TERMINAL, FORFAIT, SIM, RECHARGE, ACCESSOIRE |
| `record_date` | DATE | Date de l'agrégat |
| `quantity_sold` | INTEGER | Quantité vendue ce jour |
| `revenue` | NUMERIC(14,2) | CA TND ce jour |
| `unit_price` | NUMERIC(12,4) | Prix moyen unitaire |
| `is_promo` | BOOLEAN | Jour de promotion |
| `promo_type` | VARCHAR(50) | Type promo active |
| `event_name` | VARCHAR(200) | Événement marché ce jour |
| `event_type` | VARCHAR(50) | RELIGIEUX, COMMERCIAL, RESEAU… |
| `season` | VARCHAR(20) | RAMADAN, ETE, RENTREE, SOLDES_HIVER… |
| `is_event_day` | BOOLEAN | Événement HIGH/EXTREME actif |
| `event_intensite` | VARCHAR(10) | LOW/MEDIUM/HIGH/EXTREME |
| `uplift_factor` | NUMERIC(6,4) | Facteur saisonnalité appliqué |
| `day_of_week` | SMALLINT | 0=Lundi, 6=Dimanche |
| `week_of_year` | SMALLINT | Semaine ISO |
| `month_num` | SMALLINT | Mois (1-12) |
| `year_num` | SMALLINT | Année |
| `is_weekend` | BOOLEAN | Samedi ou Dimanche |

**Index :** `(store_id, sku, record_date DESC)`, `(record_date, category)`, `(event_type, record_date)`

---

### 4.3 `inventory.promotions`
Promotions actives par SKU ou catégorie.

| Colonne | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Identifiant interne |
| `promo_id` | VARCHAR(50) UNIQUE | Code promo métier (ex: `SEED-2026-T01`) |
| `promo_name` | VARCHAR(300) | Libellé complet |
| `start_date` | DATE | Début validité |
| `end_date` | DATE | Fin validité |
| `sku` | INTEGER | SKU ciblé (NULL = toute la catégorie) |
| `product_name` | VARCHAR(300) | Description produit ciblé |
| `category` | VARCHAR(10) | Code catégorie ciblée |
| `discount_pct` | NUMERIC(6,2) | Remise en % |
| `promo_type` | VARCHAR(30) | `discount`, `bundle`, `flash`, `seasonal` |
| `scope` | VARCHAR(20) | `sku`, `category`, `store`, `all_stores` |

---

### 4.4 `inventory.recommendations`
Recommandations InventoryAgent — résultats des décisions temps réel.

| Colonne | Type | Description |
|---|---|---|
| `id` | UUID PK | Identifiant |
| `store_id` | VARCHAR(50) FK | Boutique |
| `sku` | INTEGER | Produit concerné |
| `recommendation_type` | VARCHAR(50) | `REORDER`, `TRANSFER`, `HOLD`, `LIQUIDATE` |
| `quantity` | INTEGER | Quantité recommandée |
| `reason` | TEXT | Justification textuelle agent |
| `urgency` | VARCHAR(10) | `HIGH`, `MEDIUM`, `LOW` |
| `status` | VARCHAR(20) | `pending`, `approved`, `rejected`, `executed` |
| `approved_by` | INTEGER | agent_id approbateur |
| `created_at` | TIMESTAMP | Création |
| `resolved_at` | TIMESTAMP | Résolution |

---

### 4.5 `inventory.stock_snapshot_enriched` *(Nouveau — Migration 008)*
Snapshot quotidien enrichi avec métriques supply chain pré-calculées.

| Colonne | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Identifiant |
| `snapshot_ts` | TIMESTAMP | Horodatage du snapshot |
| `store_id` | VARCHAR(50) FK | Boutique |
| `product_id` | VARCHAR(50) | Identifiant produit |
| `product_name` | TEXT | Nom produit |
| `product_category` | VARCHAR(100) | Catégorie |
| `stock_on_hand` | INTEGER | Stock physique |
| `reserved_qty` | INTEGER | Réservations en cours |
| `available_qty` | INTEGER | Calculé : `stock_on_hand - reserved_qty` (GENERATED) |
| `avg_daily_demand_30d` | NUMERIC(10,4) | Demande moyenne 30 derniers jours |
| `demand_last_7d` | NUMERIC(10,4) | Demande 7 derniers jours |
| `coverage_days` | NUMERIC(10,2) | Jours de couverture (999=hors stock / overstock) |
| `safety_stock` | INTEGER | Stock de sécurité calculé (formule Wilson) |
| `reorder_point` | INTEGER | Point de déclenchement de commande |
| `risk_level` | VARCHAR(20) | `CRITICAL`, `HIGH`, `MEDIUM`, `OK`, `OVERSTOCK` |
| `recommended_action` | VARCHAR(50) | `EXPEDITE_TRANSFER_OR_REPLENISH`, `REPLENISH`, `MONITOR`, `HOLD_OR_PROMOTE`, `LIQUIDATE` |
| `data_quality_flag` | VARCHAR(50) | `OK`, `NEGATIVE_STOCK_TO_VALIDATE`, `ZERO_DEMAND` |
| `source_flag` | VARCHAR(60) | `COMPUTED_FROM_STOCK_LEVELS` |

**Consommé par :** CoachAgent `cross_domain_tools.score_product()`, SupervisorAgent, vue `monitoring.realtime_store_pulse`.

---

### 4.6 `inventory.recommendations_computed` *(Nouveau — Migration 008)*
Recommandations IA pré-calculées avec TTL 24h.

| Colonne | Type | Description |
|---|---|---|
| `recommendation_id` | VARCHAR(30) PK | Format : `INV-{NNNNNN}` |
| `store_id` | VARCHAR(50) FK | Boutique cible |
| `product_id` | VARCHAR(50) | Produit concerné |
| `product_name` | TEXT | Nom produit |
| `risk_level` | VARCHAR(20) | Niveau de risque actuel |
| `recommended_action` | VARCHAR(50) | Action recommandée |
| `recommended_qty` | INTEGER | Quantité suggérée |
| `source_store_id` | VARCHAR(50) | Boutique source (pour transferts) |
| `reason` | TEXT | Justification (stock X, demande Y/jour, couverture Z jours) |
| `expected_impact_tnd` | NUMERIC(12,2) | Impact CA estimé TND |
| `requires_manager_approval` | BOOLEAN | Nécessite validation manager |
| `status` | VARCHAR(20) | `ACTIVE`, `EXECUTED`, `EXPIRED`, `CANCELLED`, `PENDING_APPROVAL` |
| `expires_at` | TIMESTAMP | Expiration automatique (NOW + 24h) |

---

## 5. Schéma `supply` — Supply Chain

### 5.1 `supply.suppliers`
Fournisseurs référencés.

| Colonne | Type | Description |
|---|---|---|
| `supplier_id` | VARCHAR(30) PK | Code fournisseur |
| `nom` | VARCHAR(200) | Nom fournisseur |
| `pays_origine` | VARCHAR(50) | Pays d'origine |
| `type_fournisseur` | VARCHAR(30) | `CONSTRUCTEUR`, `DISTRIBUTEUR`, `OPERATEUR` |
| `categories` | JSONB | Catégories produits fournies |
| `marques` | JSONB | Marques distribuées |
| `delai_livraison_moy` | INTEGER | Délai moyen en jours |
| `taux_fiabilite` | NUMERIC(5,4) | Taux de fiabilité (0-1) |
| `score_global` | NUMERIC(5,2) | Score partenaire (0-100) |

### 5.2 `supply.purchase_orders`
Bons de commande.

| Colonne | Type | Description |
|---|---|---|
| `po_id` | UUID PK | Identifiant commande |
| `sku` | INTEGER | Produit commandé |
| `supplier_id` | VARCHAR(30) FK | Fournisseur |
| `store_id` | VARCHAR(50) | Boutique destinataire |
| `quantite_commandee` | INTEGER | Quantité commandée |
| `quantite_recue` | INTEGER | Quantité reçue |
| `prix_unitaire_ht` | NUMERIC(12,4) | Prix unitaire HT |
| `statut` | VARCHAR(20) | `BROUILLON`, `SOUMIS`, `CONFIRME`, `EXPEDIE`, `RECU`, `ANNULE`, `LITIGE` |
| `priorite` | VARCHAR(10) | `URGENT`, `NORMAL`, `LOW` |
| `date_commande` | TIMESTAMP | Date création |
| `date_livraison_prevue` | DATE | Livraison prévue |
| `date_livraison_reelle` | DATE | Livraison effective |

### 5.3 `supply.stock_movements`
Journal de tous les mouvements de stock.

| Colonne | Type | Description |
|---|---|---|
| `mouvement_id` | UUID PK | Identifiant mouvement |
| `sku` | INTEGER | Produit |
| `store_id` | VARCHAR(50) | Boutique |
| `type_mouvement` | VARCHAR(30) | `RECEPTION_BC`, `VENTE`, `RETOUR_CLIENT`, `RETOUR_FOURNISSEUR`, `TRANSFERT_ENTRANT`, `TRANSFERT_SORTANT`, `AJUSTEMENT_INVENTAIRE`, `CASSE_PERTE` |
| `quantite` | INTEGER | Quantité (positive=entrée, négative=sortie) |
| `stock_avant` | INTEGER | Stock avant mouvement |
| `stock_apres` | INTEGER | Stock après mouvement |
| `reference_id` | VARCHAR(100) | Référence document source |
| `reference_type` | VARCHAR(30) | `VENTE`, `BC`, `TRANSFERT` |
| `agent_id` | INTEGER | Responsable mouvement |
| `date_mouvement` | TIMESTAMP | Date/heure mouvement |

### 5.4 `supply.transfers`
Transferts inter-boutiques.

| Colonne | Type | Description |
|---|---|---|
| `transfer_id` | UUID PK | Identifiant transfert |
| `sku` | INTEGER | Produit transféré |
| `store_source` | VARCHAR(50) | Boutique expéditrice |
| `store_dest` | VARCHAR(50) | Boutique destinataire |
| `quantite` | INTEGER | Quantité transférée |
| `statut` | VARCHAR(20) | `DEMANDE`, `APPROUVE`, `EXPEDIE`, `RECU`, `REJETE` |
| `priorite` | VARCHAR(10) | Priorité du transfert |
| `motif` | VARCHAR(100) | Motif (ex: `RUPTURE_CRITIQUE_I63`) |

### 5.5 `supply.serial_numbers`
Numéros de série IMEI/ICCID/eSIM.

| Colonne | Type | Description |
|---|---|---|
| `id` | UUID PK | Identifiant |
| `num_serie` | VARCHAR(50) UNIQUE | Numéro de série |
| `type_serie` | VARCHAR(10) | `IMEI`, `ICCID`, `ESIM`, `EAN` |
| `sku` | INTEGER | Produit associé |
| `store_id` | VARCHAR(50) | Boutique |
| `statut` | VARCHAR(20) | `EN_STOCK`, `VENDU`, `RESERVE`, `DEFECTUEUX`, `RETOURNE`, `VOLE`, `EN_TRANSIT` |
| `sale_id` | VARCHAR(100) | Référence vente associée |

### 5.6 `supply.reorder_params`
Paramètres de réapprovisionnement calculés (formule Wilson).

| Colonne | Type | Description |
|---|---|---|
| `sku` | INTEGER PK(1/2) | Produit |
| `store_id` | VARCHAR(50) PK(2/2) | Boutique |
| `demande_moy_jour` | NUMERIC(10,4) | Demande moyenne quotidienne (90 jours) |
| `demande_std_jour` | NUMERIC(10,4) | Écart-type demande |
| `lead_time_moy` | NUMERIC(8,2) | Lead time moyen fournisseur (jours) |
| `stock_securite` | INTEGER | Stock de sécurité = 1.65 × σ × √L |
| `point_commande` | INTEGER | Seuil déclencheur = d×L + stock_securite |
| `eoq` | INTEGER | EOQ = √(2×D×K / h×p) |
| `niveau_service` | NUMERIC(5,4) | Niveau de service cible (défaut 0.95) |
| `jours_stock_cible` | INTEGER | Couverture cible en jours (défaut 30) |
| `derniere_maj` | TIMESTAMP | Dernière mise à jour |

---

## 6. Schéma `market` — Intelligence Marché

### 6.1 `market.events`
Calendrier des événements impactant les ventes.

| Colonne | Type | Description |
|---|---|---|
| `event_id` | UUID PK | Identifiant |
| `event_name` | VARCHAR(200) | Nom de l'événement |
| `event_type` | VARCHAR(50) | `RELIGIEUX`, `SCOLAIRE`, `SPORTIF`, `COMMERCIAL`, `NATIONAL`, `CONCURRENTIEL`, `METEO`, `RESEAU` |
| `start_date` | DATE | Début |
| `end_date` | DATE | Fin |
| `scope` | VARCHAR(20) | `NATIONAL`, `REGIONAL`, `LOCAL` |
| `uplift_terminal` | NUMERIC(6,2) | Impact % sur ventes terminaux |
| `uplift_forfait` | NUMERIC(6,2) | Impact % sur forfaits |
| `uplift_recharge` | NUMERIC(6,2) | Impact % sur recharges |
| `intensite` | VARCHAR(10) | `LOW`, `MEDIUM`, `HIGH`, `EXTREME` |

### 6.2 `market.seasonal_patterns`
Patterns saisonniers par catégorie et période.

| Colonne | Type | Description |
|---|---|---|
| `id` | SERIAL PK | Identifiant |
| `categorie` | VARCHAR(50) | Catégorie produit |
| `mois` | INTEGER | Mois (1-12) |
| `jour_semaine` | INTEGER | Jour (0=Lun, 6=Dim) |
| `heure_debut` / `heure_fin` | INTEGER | Plage horaire |
| `facteur_demande` | NUMERIC(6,4) | Multiplicateur de demande (1.0=normal) |
| `confidence` | VARCHAR(10) | `LOW`, `MEDIUM`, `HIGH`, `VERY_HIGH` |

### 6.3 `market.competitors`
Concurrents télécom (Tunisie Telecom, Orange, Free).

| Colonne | Type | Description |
|---|---|---|
| `concurrent_id` | VARCHAR(20) PK | Code concurrent (TT, ORANGE, FREE_TN) |
| `nom` | VARCHAR(100) | Nom commercial |
| `part_marche_pct` | NUMERIC(6,3) | Part de marché % |
| `nb_abonnes` | BIGINT | Nombre abonnés |
| `positionnement` | VARCHAR(20) | `STATE`, `PREMIUM`, `LOW_COST`, `MID` |

### 6.4 `market.competitor_pricing`
Relevés tarifaires concurrents.

| Colonne | Type | Description |
|---|---|---|
| `id` | SERIAL PK | Identifiant |
| `concurrent_id` | VARCHAR(20) FK | Concurrent |
| `categorie` | VARCHAR(50) | Catégorie produit |
| `prix_ttc` | NUMERIC(10,2) | Prix TTC relevé |
| `date_releve` | DATE | Date du relevé |
| `source` | VARCHAR(30) | `WEB`, `BOUTIQUE`, `PRESSE` |

### 6.5 `market.mnp_flows`
Flux de portabilité Mobile Number Portability.

| Colonne | Type | Description |
|---|---|---|
| `mnp_id` | UUID PK | Identifiant |
| `direction` | VARCHAR(10) | `PORT_IN`, `PORT_OUT` |
| `mois` | DATE | Mois concerné |
| `volume` | INTEGER | Volume de portages |
| `raison_principale` | VARCHAR(100) | Motif principal (prix, couverture…) |

### 6.6 `market.network_events`
Incidents et maintenances réseau.

| Colonne | Type | Description |
|---|---|---|
| `event_id` | UUID PK | Identifiant |
| `wilaya` | VARCHAR(100) | Zone impactée |
| `event_type` | VARCHAR(30) | `PANNE`, `MAINTENANCE`, `UPGRADE_4G`, `UPGRADE_5G`, `CONGESTION` |
| `severite` | VARCHAR(10) | Sévérité |
| `start_time` / `end_time` | TIMESTAMP | Plage horaire |
| `clients_impactes` | INTEGER | Nombre clients affectés |
| `impact_ventes_pct` | NUMERIC(6,2) | Impact estimé sur ventes % |

---

## 7. Schéma `customer` — Clients & Feedback

### 7.1 `customer.segments`
8 segments clients Ooredoo Tunisie.

| Colonne | Type | Description |
|---|---|---|
| `segment_id` | VARCHAR(20) PK | `RESI_STANDARD`, `RESI_DATA`, `RESI_PREMIUM`, `CORPO_PME`, `CORPO_GRAND`, `SENIOR`, `ETUDIANT`, `ROAMING` |
| `libelle` | VARCHAR(100) | Libellé segment |
| `arpu_moyen_tnd` | NUMERIC(10,2) | ARPU mensuel moyen TND |
| `churn_rate_base` | NUMERIC(6,4) | Taux de churn de base |
| `canal_prefere` | VARCHAR(20) | `BOUTIQUE`, `DIGITAL`, `B2B` |
| `products_preferes` | JSONB | Produits privilégiés par segment |
| `nb_clients_estime` | INTEGER | Base clients estimée |

### 7.2 `customer.churn_signals`
Signaux de risque churn par boutique.

| Colonne | Type | Description |
|---|---|---|
| `id` | UUID PK | Identifiant |
| `store_id` | VARCHAR(50) | Boutique |
| `signal_date` | DATE | Date du signal |
| `nb_inactifs_30j` | INTEGER | Clients inactifs 30 jours |
| `nb_port_out` | INTEGER | Portabilités sortantes |
| `score_risque_churn` | NUMERIC(5,2) | Score risque 0-100 |
| `facteurs_risque` | JSONB | Facteurs détectés |
| `actions_retention` | JSONB | Actions proposées |

### 7.3 `customer.nps_csat`
Enquêtes satisfaction NPS/CSAT.

| Colonne | Type | Description |
|---|---|---|
| `id` | SERIAL PK | Identifiant |
| `store_id` | VARCHAR(50) | Boutique |
| `agent_id` | INTEGER FK | Conseiller évalué |
| `feedback_date` | DATE | Date feedback |
| `type_enquete` | VARCHAR(10) | `NPS`, `CSAT`, `CES` |
| `score` | NUMERIC(5,2) | Score (NPS : 0-10, CSAT : 1-5) |
| `verbatim` | TEXT | Commentaire client |
| `categorie_motif` | VARCHAR(100) | `CONSEIL_PRODUIT`, `TEMPS_ATTENTE`, `COMPETENCE_TECHNIQUE`… |
| `resolu` | BOOLEAN | Problème résolu |

---

## 8. Schéma `coaching` — Agents Coaching

### 8.1 `coaching.coaching_events`
Archive de toutes les recommandations coaching générées par les agents.

| Colonne | Type | Description |
|---|---|---|
| `id` | UUID PK | Identifiant |
| `advisor_id` | INTEGER FK | Conseiller coaché |
| `store_id` | VARCHAR(50) FK | Boutique |
| `cycle_id` | VARCHAR(100) | Identifiant cycle agent (`CYC-YYYYMMDD-HHMM-{store}`) |
| `urgency_level` | VARCHAR(10) | `HIGH`, `MEDIUM`, `LOW` |
| `urgency_score` | NUMERIC(5,2) | Score urgence 0-100 |
| `gap_pct` | NUMERIC(7,2) | Gap vs objectif % (négatif = sous objectif) |
| `gap_amount` | NUMERIC(12,2) | Montant manquant TND |
| `forecast_eod` | NUMERIC(12,2) | Prévision fin de journée TND |
| `advice_text` | TEXT | Texte du conseil coaching |
| `produit_a_pousser` | VARCHAR(200) | Produit recommandé à vendre |
| `produit_a_eviter` | VARCHAR(200) | Produit à éviter (rupture, marge faible) |
| `strategie` | TEXT | Stratégie préconisée |
| `cause_racine` | TEXT | Cause identifiée de la sous-performance |
| `rag_used` | BOOLEAN | RAG utilisé dans la génération |
| `nb_rag_scripts` | INTEGER | Nombre de scripts RAG mobilisés |
| `script_ids` | JSONB | IDs scripts utilisés |
| `weather_label` | VARCHAR(100) | Condition météo au moment du coaching |
| `weather_effect` | NUMERIC(5,2) | Impact météo estimé sur ventes % |
| `event_name` | VARCHAR(200) | Événement marché actif |
| `guardrail_status` | VARCHAR(20) | `APPROVE`, `BLOCK`, `REWRITE` |
| `guardrail_rule` | VARCHAR(100) | Règle guardrail déclenchée |
| `feedback_score` | INTEGER | Note conseiller 1-5 |
| `was_effective` | BOOLEAN | Coaching effectif (CA amélioré) |
| `ca_after_coaching` | NUMERIC(12,2) | CA réalisé post-coaching TND |

### 8.2 `coaching.escalations`
Escalades manager sur sous-performances persistantes (>90 min, gap>25%).

| Colonne | Type | Description |
|---|---|---|
| `id` | VARCHAR(20) PK | Format : `ESC-NNNN` |
| `store_id` | VARCHAR(50) | Boutique |
| `gap_pct` | NUMERIC(7,2) | Gap au moment de l'escalade |
| `gap_duration_min` | INTEGER | Durée gap en minutes |
| `root_causes` | JSONB | Causes identifiées (array de strings) |
| `actions` | JSONB | Plan d'actions (array d'objets `{type, detail, requires_approval}`) |
| `hitl_request_ids` | JSONB | IDs hitl_requests associés |
| `expected_impact_tnd` | NUMERIC(12,2) | Impact estimé résolution |
| `status` | VARCHAR(20) | `pending`, `approved`, `rejected`, `executed`, `cancelled` |

### 8.3 `coaching.hitl_requests`
Requêtes Human-In-The-Loop pour approbation manager.

| Colonne | Type | Description |
|---|---|---|
| `id` | UUID PK | Identifiant |
| `store_id` | VARCHAR(50) | Boutique |
| `escalation_id` | VARCHAR(20) FK | Escalade parente |
| `action_type` | VARCHAR(50) | `STOCK_TRANSFER`, `ADVISOR_REALLOCATION`, `PITCH_CHANGE`, `EMERGENCY_COACHING`, `GOAL_REVISION`, `INTER_STORE_SUPPORT` |
| `description` | TEXT | Description action demandée |
| `expected_impact_tnd` | NUMERIC(12,2) | Impact attendu |
| `auto_approved` | BOOLEAN | Approbation automatique OPA |
| `status` | VARCHAR(20) | `pending`, `approved`, `rejected`, `auto_approved` |
| `approver_name` | VARCHAR(100) | Nom du manager approbateur |
| `approver_comment` | TEXT | Commentaire approbateur |

### 8.4 `coaching.agent_memory`
Mémoire persistante inter-cycles des agents.

| Colonne | Type | Description |
|---|---|---|
| `id` | UUID PK | Identifiant |
| `agent_name` | VARCHAR(50) | `analyst`, `stratege`, `coach`, `supervisor`, `guardrail` |
| `store_id` | VARCHAR(50) | Boutique contexte |
| `advisor_id` | INTEGER | Conseiller contexte |
| `cycle_id` | VARCHAR(100) | Dernier cycle associé |
| `content` | JSONB | Mémoire structurée (gap_history, avg_gap_7d, advisor_style, scripts_efficaces…) |

### 8.5 `coaching.coaching_recommendations` *(Nouveau — Migration 008)*
Pool de recommandations coaching actives (RAG seed + fallback).

| Colonne | Type | Description |
|---|---|---|
| `recommendation_id` | VARCHAR(30) PK | Format : `COACH-{NNNNNN}` |
| `store_id` | VARCHAR(50) | Boutique cible |
| `advisor_id` / `advisor_name` | VARCHAR | Conseiller ciblé |
| `trigger_type` | VARCHAR(50) | `OBJECTIVE_GAP`, `UPSELL_OPPORTUNITY`, `STOCK_RISK`, `CHURN_SIGNAL`, `CROSS_SELL` |
| `severity` | VARCHAR(10) | `HIGH`, `MEDIUM`, `LOW` |
| `product_to_push` | VARCHAR(50) | SKU ou code produit à pousser |
| `product_to_avoid` | VARCHAR(50) | SKU ou code à éviter |
| `recommendation_text` | TEXT | Texte conseil personnalisé |
| `business_justification` | TEXT | Justification contextuelle (météo, trafic, événement) |
| `confidence` | NUMERIC(4,2) | Confidence 0-1 |
| `expected_impact_ttc` | NUMERIC(12,2) | Impact CA attendu TND |
| `status` | VARCHAR(20) | `ACTIVE`, `USED`, `EXPIRED`, `REJECTED` |

---

## 9. Schéma `context` — Contexte Temps Réel *(Nouveau)*

### 9.1 `context.context_hourly_store`
Contexte environnemental par boutique, par heure.

| Colonne | Type | Description |
|---|---|---|
| `context_id` | VARCHAR(60) PK | Format : `CTX-{store_id}-{YYYYMMDDhh}` |
| `store_id` | VARCHAR(50) FK | Boutique |
| `city` | VARCHAR(100) | Ville de la boutique |
| `context_hour` | TIMESTAMP | Heure exacte du contexte |
| `weather_condition` | VARCHAR(30) | `clear`, `cloudy`, `rain`, `storm`, `fog`, `hot`, `sand` |
| `rain_mm` | NUMERIC(6,2) | Précipitations en mm |
| `temperature_c` | NUMERIC(5,2) | Température en °C |
| `event_type` | VARCHAR(50) | Événement actif ce créneau (salary_day, eid, soldes…) |
| `event_strength` | NUMERIC(4,2) | Force de l'événement (0=aucun, 1=max) |
| `traffic_index` | NUMERIC(6,2) | Index trafic piéton 0-100 (50=normal) |
| `cell_load_pct` | NUMERIC(6,2) | Charge cellule réseau % |
| `outage_flag` | SMALLINT | 0=nominal, 1=incident réseau |
| `network_status` | VARCHAR(20) | `nominal`, `busy`, `degraded`, `outage` |
| `footfall_estimate` | INTEGER | Estimation visiteurs heure |
| `source_flag` | VARCHAR(60) | `REALTIME_API`, `SYNTHETIC_CONTEXT_CALIBRATED` |

**Alimentation temps réel :** Job Python toutes les heures depuis API météo + métriques réseau interne.

**Consommé par :**
- `react_tools.get_seasonal_context()` — enrichit le contexte analyste
- `cross_domain_tools.get_sales_context()` — contexte pour le scoring
- Vue `monitoring.realtime_store_pulse`

---

## 10. Schéma `forecasting` — Features Prévision *(Nouveau)*

### 10.1 `forecasting.forecast_features_daily`
Features pré-calculées pour le moteur de prévision EOD.

| Colonne | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Identifiant |
| `feature_date` | DATE | Date des features |
| `store_id` | VARCHAR(50) FK | Boutique |
| `product_category` | VARCHAR(100) | Catégorie produit |
| `actual_revenue_ttc` | NUMERIC(14,2) | CA réel de cette journée (rempli N+1) |
| `actual_units` | INTEGER | Unités réelles vendues |
| `lag_1d_revenue` | NUMERIC(14,2) | CA de J-1 |
| `lag_7d_revenue` | NUMERIC(14,2) | CA de J-7 (même jour semaine précédente) |
| `rolling_7d_revenue` | NUMERIC(14,2) | Moyenne CA 7 derniers jours |
| `rolling_30d_revenue` | NUMERIC(14,2) | Moyenne CA 30 derniers jours |
| `forecast_next_day_revenue` | NUMERIC(14,2) | Prévision J+1 |
| `forecast_confidence` | NUMERIC(4,2) | Confidence du forecast (0-1) |
| `gap_vs_objective_pct` | NUMERIC(7,2) | Gap historique vs objectif |
| `model_used` | VARCHAR(30) | `LINEAR`, `SEASONAL`, `TIMESFM`, `ENSEMBLE` |
| `source_flag` | VARCHAR(60) | `DERIVED_FEATURES` |

**Alimentation :** Job nuit (00h30) via `forecasting/timeseries_engine.py`.

**Consommé par :**
- `react_tools.compute_eod_forecast()` — lit les features précalculées pour accélérer le calcul
- TimesFM — features input pour le modèle Google TimesFM

---

## 11. Tables KPI (schema public)

### 11.1 `agent_kpi_daily`
KPIs consolidés par conseiller et par jour.

| Colonne | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Identifiant |
| `agent_id` | INTEGER | Conseiller |
| `store_id` | VARCHAR(50) | Boutique |
| `kpi_date` | DATE | Date |
| `ca_realise` | NUMERIC(14,2) | CA réalisé TND |
| `ca_cible` | NUMERIC(14,2) | CA cible TND |
| `gap_ca_pct` | NUMERIC(7,2) | Gap % vs objectif |
| `nb_transactions` | INTEGER | Nombre transactions |
| `panier_moyen` | NUMERIC(10,2) | Ticket moyen |
| `nb_forfaits` | INTEGER | Forfaits vendus |
| `nb_terminaux` | INTEGER | Terminaux vendus |
| `nb_sim_activations` | INTEGER | Activations SIM |
| `nb_recharges` | INTEGER | Recharges |
| `nb_postpaye` | INTEGER | Forfaits postpayés |
| `ca_terminaux` / `ca_forfaits` | NUMERIC(12,2) | CA par catégorie |
| `rang_boutique` | INTEGER | Rang dans la boutique |
| `rang_region` | INTEGER | Rang régional |
| `rang_national` | INTEGER | Rang national |
| `urgency_level` | VARCHAR(10) | `CRITIQUE`, `ELEVE`, `MODERE`, `OK` |
| `coach_score` | NUMERIC(5,2) | Score coaching du jour |

### 11.2 `store_kpi_daily`
KPIs consolidés par boutique et par jour.

| Colonne | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Identifiant |
| `store_id` | VARCHAR(50) | Boutique |
| `kpi_date` | DATE | Date |
| `ca_realise` | NUMERIC(14,2) | CA boutique TND |
| `ca_cible` | NUMERIC(14,2) | CA cible TND |
| `gap_ca_pct` | NUMERIC(7,2) | Gap % |
| `ca_cumul_mois` | NUMERIC(16,2) | CA cumulé mois en cours |
| `taux_conversion` | NUMERIC(6,4) | Taux de conversion |
| `footfall_estime` | INTEGER | Trafic piéton estimé |
| `nps_score` | NUMERIC(5,2) | NPS moyen du jour |
| `csat_score` | NUMERIC(5,2) | CSAT moyen du jour |
| `nb_ruptures_sku` | INTEGER | Nombre de références en rupture |
| `taux_service_stock` | NUMERIC(6,4) | Taux de service stock (0-1) |
| `rang_region` / `rang_national` | INTEGER | Classements |

### 11.3 `telco_targets_monthly`
Objectifs mensuels détaillés par boutique, agent et niveau.

| Colonne | Type | Description |
|---|---|---|
| `id` | BIGSERIAL PK | Identifiant |
| `store_id` | VARCHAR(50) | Boutique |
| `agent_id` | INTEGER | Conseiller (NULL=boutique) |
| `mois` | INTEGER | Mois (1-12) |
| `annee` | INTEGER | Année |
| `niveau` | VARCHAR(10) | `AGENT`, `BOUTIQUE`, `REGION` |
| `ca_cible_mensuel` | NUMERIC(14,2) | CA mensuel cible TND |
| `ca_cible_s1/s2/s3/s4` | NUMERIC(12,2) | Cibles par semaine |
| `activations_postpaye` | INTEGER | Activations postpayé cible |
| `facteur_saisonnier` | NUMERIC(6,4) | Coefficient saisonnalité |

### 11.4 `weekly_kpi_summary`
Synthèse hebdomadaire KPIs.

| Colonne | Type | Description |
|---|---|---|
| `annee_semaine` | VARCHAR(8) | Format `2026-W27` |
| `semaine_debut` / `semaine_fin` | DATE | Plage semaine |
| `ca_semaine` | NUMERIC(14,2) | CA semaine TND |
| `gap_semaine_pct` | NUMERIC(7,2) | Gap semaine % |
| `top_produit` | VARCHAR(200) | Meilleur produit semaine |
| `top_categorie` | VARCHAR(50) | Meilleure catégorie |

### 11.5 `public.agent_cycles`
Log d'exécution de chaque cycle agent.

| Colonne | Type | Description |
|---|---|---|
| `cycle_id` | VARCHAR(100) PK | Identifiant cycle |
| `store_id` | VARCHAR(50) | Boutique |
| `triggered_by` | VARCHAR(50) | Déclencheur (`CRON`, `ALERT`, `MANUAL`) |
| `urgency_level` | VARCHAR(10) | Niveau d'urgence calculé |
| `gap_pct` | NUMERIC(7,2) | Gap au déclenchement |
| `ca_today` | NUMERIC(12,2) | CA au moment du cycle |
| `ca_target` | NUMERIC(12,2) | Objectif du jour |
| `analyst_summary` | TEXT | Résumé agent analyste |
| `strategie` | TEXT | Stratégie choisie |
| `rag_used` | BOOLEAN | RAG utilisé |
| `total_ms` | INTEGER | Durée totale du cycle en ms |
| `nodes_executed` | JSONB | Nœuds StateGraph exécutés |
| `errors_count` | INTEGER | Nombre d'erreurs |
| `status` | VARCHAR(20) | `SUCCESS`, `ERROR`, `PARTIAL` |

### 11.6 `public.coach_interactions`
Journal des interactions coach (messages envoyés aux conseillers).

| Colonne | Type | Description |
|---|---|---|
| `id` | UUID PK | Identifiant |
| `store_id` | VARCHAR(50) | Boutique |
| `advisor_name` | VARCHAR(200) | Nom conseiller |
| `message` | TEXT | Message coaching envoyé |
| `channel` | VARCHAR(20) | `CHAT`, `SSE`, `PUSH`, `EMAIL` |
| `read_at` | TIMESTAMP | Date lecture |
| `feedback` | INTEGER | Feedback 1-5 |
| `created_at` | TIMESTAMP | Date envoi |

---

## 12. Schéma `monitoring` — Vues Observabilité

### 12.1 Vue `monitoring.cycle_logs`
Vue sur `public.agent_cycles` — expose les 50 derniers cycles.

**Colonnes clés :** `cycle_id`, `store_id`, `urgency_level`, `gap_pct`, `total_ms`, `status`, `created_at`

### 12.2 Vue `monitoring.coaching_interactions`
Vue sur `coaching.coaching_events` — interactions récentes.

**Colonnes clés :** `advisor_id`, `store_id`, `urgency_level`, `advice_text`, `guardrail_status`, `was_effective`

### 12.3 Vue `monitoring.realtime_store_pulse` *(Nouveau — Migration 008)*
**Agrège en 1 requête :** CA du jour, objectif, taux d'atteinte, contexte environnemental, alertes stock, recommandations actives.

**Sources :** `sales.transactions_rt` + `sales.objectifs` + `context.context_hourly_store` + `inventory.stock_snapshot_enriched` + `inventory.recommendations_computed`

**Consommée par :** Dashboard Angular KPI board, SupervisorAgent pour le monitoring multi-boutiques.

### 12.4 Vue `monitoring.category_gap_live` *(Nouveau — Migration 008)*
**Calcule en temps réel** le gap par catégorie produit vs objectif de la journée.

**Sources :** `sales.daily_objectives_category` + `sales.transactions_rt` + `sales.produits`

**Consommée par :** CoachAgent pour identifier la catégorie sous-performante dans le pitch.

### 12.5 Table `monitoring.advisor_profile`
Profil de performance conseiller (mise à jour à chaque cycle).

| Colonne | Type | Description |
|---|---|---|
| `store_id` | VARCHAR(50) | Boutique |
| `advisor_name` | VARCHAR(200) | Nom conseiller |
| `avg_ca_7d` | NUMERIC(12,2) | CA moyen 7 derniers jours |
| `avg_gap_7d` | NUMERIC(7,2) | Gap moyen 7j |
| `top_category` | VARCHAR(50) | Catégorie de force |
| `weak_category` | VARCHAR(50) | Catégorie de faiblesse |
| `best_hour` | INTEGER | Meilleure heure de vente |
| `conversion_rate` | NUMERIC(6,4) | Taux de conversion |
| `updated_at` | TIMESTAMP | Dernière mise à jour |

---

## 13. Index critiques

```sql
-- Performance queries temps réel
idx_transactions_rt_store_time  ON sales.transactions_rt(store_id, created_at DESC)
idx_transactions_rt_store_date  ON sales.transactions_rt(store_id, date_only)
idx_transactions_rt_advisor     ON sales.transactions_rt(store_id, advisor_name, created_at DESC)

-- Stock alerts (S3.1)
idx_stock_levels_store_qty      ON inventory.stock_levels(store_id, quantity_available ASC)
idx_sse_store_risk              ON inventory.stock_snapshot_enriched(store_id, risk_level, snapshot_ts DESC)
idx_sse_critical                ON inventory.stock_snapshot_enriched(snapshot_ts DESC)
                                WHERE risk_level IN ('CRITICAL','HIGH')

-- Forecasting
idx_ffd_store_date              ON forecasting.forecast_features_daily(store_id, feature_date DESC)
idx_ffd_gap                     ON forecasting.forecast_features_daily(feature_date, gap_vs_objective_pct)
                                WHERE gap_vs_objective_pct < -10

-- Coaching
idx_ce_urgency                  ON coaching.coaching_events(urgency_level, created_at DESC)
                                WHERE urgency_level = 'HIGH'
idx_hitl_pending                ON coaching.hitl_requests(status, created_at)
                                WHERE status = 'pending'

-- Context temps réel
idx_ctx_store_hour              ON context.context_hourly_store(store_id, context_hour DESC)
idx_ctx_network                 ON context.context_hourly_store(network_status, context_hour)
                                WHERE network_status != 'nominal'

-- KPI
idx_agent_kpi_gap               ON agent_kpi_daily(kpi_date, gap_ca_pct)
                                WHERE gap_ca_pct < -15
idx_store_kpi_gap               ON store_kpi_daily(kpi_date, gap_ca_pct)
                                WHERE gap_ca_pct < -10
```

---

## 14. Flux temps réel par agent

### AnalystAgent (`react_tools.py`)
```
sales.transactions_rt          → fetch_live_pos()          → CA courant, nb_tx, panier moyen
sales.objectifs                → fetch_live_pos()          → objectif du jour
context.context_hourly_store   → get_seasonal_context()    → météo, trafic, événement [NOUVEAU]
inventory.sales_history        → get_historical_comparison()→ J-7, J-14, J-28
market.seasonal_patterns       → get_seasonal_context()    → facteur saisonnalité
forecasting.forecast_features_daily → compute_eod_forecast()  → features lag/rolling [NOUVEAU]
```

### CoachAgent (`cross_domain_tools.py`)
```
sales.transactions_rt          → get_sales_context()       → CA live, gap
sales.daily_objectives_category→ category_gap_live VIEW    → gap par catégorie [NOUVEAU]
inventory.stock_snapshot_enriched → score_product()        → santé stock [NOUVEAU]
inventory.recommendations_computed → get_inventory_context()→ actions cross-domain [NOUVEAU]
coaching.coaching_recommendations → get_rag_scripts()      → pool recommandations [NOUVEAU]
customer.nps_csat              → get_customer_context()    → NPS/CSAT conseiller
coaching.agent_memory          → get/set advisor_memory()  → mémoire inter-cycles
```

### InventoryDecisionAgent
```
inventory.stock_levels         → baseline_report()         → stock courant
supply.reorder_params          → calculate_eoq()           → paramètres réappro
inventory.stock_snapshot_enriched → WRITE                  → résultat snapshot [NOUVEAU]
inventory.recommendations_computed → WRITE                 → recommandations cross-domain [NOUVEAU]
supply.purchase_orders         → create_po()               → bon de commande
supply.transfers               → request_transfer()        → transfert inter-boutiques
```

### SupervisorAgent
```
monitoring.realtime_store_pulse → monitor_all_stores()     → pulse temps réel [NOUVEAU]
monitoring.category_gap_live   → detect_critical_gaps()    → alertes catégorie [NOUVEAU]
coaching.escalations           → check_pending_escalations()→ escalades en attente
coaching.hitl_requests         → get_approval_queue()      → file d'approbation
public.agent_cycles            → WRITE cycle_log()         → trace exécution
```

### GuardrailAgent
```
coaching.coaching_events        → READ last_events()       → historique conseiller
coaching.coaching_recommendations → VALIDATE               → vérification recommandations
coaching.agent_memory           → READ guardrail_memory()  → mémoire règles
```

---

## 15. Règles de qualité des données

| Règle | Table | Action |
|---|---|---|
| `stock_on_hand < 0` | `inventory.stock_snapshot_enriched` | `data_quality_flag = 'NEGATIVE_STOCK_TO_VALIDATE'` |
| `avg_daily_demand_30d = 0 AND stock > 5` | `inventory.stock_snapshot_enriched` | `risk_level = 'OVERSTOCK'`, `data_quality_flag = 'ZERO_DEMAND'` |
| `montant > 3000 TND` | `sales.transactions` / `transactions_rt` | Conserver + flag `OUTLIER_AMOUNT_EXCLUDED_FOR_MODEL` |
| `montant = 0` | `sales.transactions` | Conserver comme `ZERO_AMOUNT_BUSINESS_LINE` |
| `source_flag = 'SYNTHETIC_*'` | Toutes | Filtrer dans les requêtes LLM production, autoriser en tests |
| `expires_at < NOW()` | `inventory.recommendations_computed` | `status` auto-mis à `EXPIRED` (job toutes les heures) |
| `context_hour > NOW() + 2h` | `context.context_hourly_store` | Rejeter (données futures interdites) |
| Devise unité | `sales.transactions_rt` | Toujours en **TND** (pas millimes). Convertir : `millimes / 1000 = TND` |

---

*Document généré le 2026-06-30 — Synchronisé avec les migrations 001-008.*  
*Prochaine mise à jour : à chaque migration ou évolution de schéma.*
