-- Migration 005: market intelligence + enrich existing tables
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS customer;
CREATE SCHEMA IF NOT EXISTS supply;

-- Enrich sales.boutiques
ALTER TABLE sales.boutiques
ADD COLUMN IF NOT EXISTS type_boutique  VARCHAR(5),
ADD COLUMN IF NOT EXISTS canal          VARCHAR(20) DEFAULT 'PHYSIQUE',
ADD COLUMN IF NOT EXISTS wilaya         VARCHAR(100),
ADD COLUMN IF NOT EXISTS zone_commerciale VARCHAR(100),
ADD COLUMN IF NOT EXISTS latitude       NUMERIC(10,7),
ADD COLUMN IF NOT EXISTS longitude      NUMERIC(10,7),
ADD COLUMN IF NOT EXISTS capacite_conseillers INTEGER DEFAULT 4,
ADD COLUMN IF NOT EXISTS date_ouverture DATE,
ADD COLUMN IF NOT EXISTS is_officielle  BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS rang_ca_region INTEGER,
ADD COLUMN IF NOT EXISTS email_store    VARCHAR(200);

-- Enrich sales.produits
ALTER TABLE sales.produits
ADD COLUMN IF NOT EXISTS marque         VARCHAR(100),
ADD COLUMN IF NOT EXISTS modele         VARCHAR(200),
ADD COLUMN IF NOT EXISTS gamme_libelle  VARCHAR(100),
ADD COLUMN IF NOT EXISTS famille_libelle VARCHAR(100),
ADD COLUMN IF NOT EXISTS pa_ht          NUMERIC(12,4),
ADD COLUMN IF NOT EXISTS marge_pct_calc NUMERIC(6,2),
ADD COLUMN IF NOT EXISTS flag_4g        BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS flag_5g        BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS flag_terminal  BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS flag_forfait   BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS flag_sim       BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS flag_recharge  BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS serialisable   BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS stockable      BOOLEAN DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS lead_time_days INTEGER DEFAULT 14,
ADD COLUMN IF NOT EXISTS lead_time_std  INTEGER DEFAULT 3,
ADD COLUMN IF NOT EXISTS moq            INTEGER DEFAULT 1,
ADD COLUMN IF NOT EXISTS holding_cost_pct NUMERIC(8,6) DEFAULT 0.2,
ADD COLUMN IF NOT EXISTS order_cost     NUMERIC(12,4) DEFAULT 50,
ADD COLUMN IF NOT EXISTS date_lancement DATE,
ADD COLUMN IF NOT EXISTS date_eol       DATE,
ADD COLUMN IF NOT EXISTS lifecycle_stage VARCHAR(20) DEFAULT 'mature',
ADD COLUMN IF NOT EXISTS stockage_gb    INTEGER,
ADD COLUMN IF NOT EXISTS ram_gb         INTEGER,
ADD COLUMN IF NOT EXISTS couleur        VARCHAR(50);

-- Enrich sales.agents
ALTER TABLE sales.agents
ADD COLUMN IF NOT EXISTS date_embauche      DATE,
ADD COLUMN IF NOT EXISTS date_depart        DATE,
ADD COLUMN IF NOT EXISTS niveau_certification INTEGER DEFAULT 1,
ADD COLUMN IF NOT EXISTS quota_mensuel_ca   NUMERIC(12,2),
ADD COLUMN IF NOT EXISTS quota_activations  INTEGER DEFAULT 60,
ADD COLUMN IF NOT EXISTS quota_postpaye     INTEGER DEFAULT 10,
ADD COLUMN IF NOT EXISTS specialisation     VARCHAR(50),
ADD COLUMN IF NOT EXISTS avatar_color       VARCHAR(7),
ADD COLUMN IF NOT EXISTS coach_score        NUMERIC(5,2) DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS anciennete_mois    INTEGER DEFAULT 12;

-- market.events
CREATE TABLE IF NOT EXISTS market.events (
    event_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_name       VARCHAR(200) NOT NULL,
    event_type       VARCHAR(50) NOT NULL
        CHECK (event_type IN ('RELIGIEUX','SCOLAIRE','SPORTIF','COMMERCIAL','NATIONAL','CONCURRENTIEL','METEO','RESEAU')),
    sous_type        VARCHAR(100),
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL,
    annee            INTEGER GENERATED ALWAYS AS (EXTRACT(YEAR FROM start_date)::INTEGER) STORED,
    scope            VARCHAR(20) DEFAULT 'NATIONAL',
    region_ids       JSONB,
    categories_impactees JSONB,
    uplift_terminal  NUMERIC(6,2) DEFAULT 0,
    uplift_forfait   NUMERIC(6,2) DEFAULT 0,
    uplift_sim       NUMERIC(6,2) DEFAULT 0,
    uplift_recharge  NUMERIC(6,2) DEFAULT 0,
    uplift_accessoire NUMERIC(6,2) DEFAULT 0,
    intensite        VARCHAR(10) DEFAULT 'MEDIUM'
        CHECK (intensite IN ('LOW','MEDIUM','HIGH','EXTREME')),
    source_donnee    VARCHAR(50) DEFAULT 'HISTORIQUE',
    note_strategie   TEXT,
    created_at       TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_market_events_dates ON market.events(start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_market_events_type ON market.events(event_type);

-- market.seasonal_patterns
CREATE TABLE IF NOT EXISTS market.seasonal_patterns (
    id               SERIAL PRIMARY KEY,
    categorie        VARCHAR(50) NOT NULL,
    mois             INTEGER NOT NULL CHECK (mois BETWEEN 1 AND 12),
    semaine_mois     INTEGER CHECK (semaine_mois BETWEEN 1 AND 5),
    jour_semaine     INTEGER CHECK (jour_semaine BETWEEN 0 AND 6),
    heure_debut      INTEGER CHECK (heure_debut BETWEEN 0 AND 23),
    heure_fin        INTEGER CHECK (heure_fin BETWEEN 0 AND 23),
    facteur_demande  NUMERIC(6,4) NOT NULL DEFAULT 1.0,
    facteur_std      NUMERIC(6,4) DEFAULT 0.1,
    nb_annees_data   INTEGER DEFAULT 2,
    confidence       VARCHAR(10) DEFAULT 'MEDIUM'
        CHECK (confidence IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
    notes            VARCHAR(200),
    updated_at       TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_seasonal_unique
    ON market.seasonal_patterns(categorie, mois, COALESCE(jour_semaine, -1));

-- market.competitors
CREATE TABLE IF NOT EXISTS market.competitors (
    concurrent_id    VARCHAR(20) PRIMARY KEY,
    nom              VARCHAR(100) NOT NULL,
    code_operateur   VARCHAR(10),
    pays             VARCHAR(50) DEFAULT 'Tunisia',
    part_marche_pct  NUMERIC(6,3),
    nb_abonnes       BIGINT,
    positionnement   VARCHAR(20) DEFAULT 'MID',
    points_forts     JSONB,
    points_faibles   JSONB,
    date_entree_marche DATE,
    actif            BOOLEAN DEFAULT TRUE,
    updated_at       TIMESTAMP DEFAULT NOW()
);

-- market.competitor_pricing
CREATE TABLE IF NOT EXISTS market.competitor_pricing (
    id               SERIAL PRIMARY KEY,
    concurrent_id    VARCHAR(20) NOT NULL REFERENCES market.competitors(concurrent_id),
    categorie        VARCHAR(50) NOT NULL,
    produit_type     VARCHAR(200) NOT NULL,
    donnees_go       NUMERIC(8,1),
    minutes_voix     INTEGER,
    sms_count        INTEGER,
    prix_ht          NUMERIC(10,2),
    prix_ttc         NUMERIC(10,2),
    engagement_mois  INTEGER DEFAULT 0,
    date_releve      DATE NOT NULL,
    source           VARCHAR(30) DEFAULT 'WEB',
    actif            BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_competitor_pricing_date ON market.competitor_pricing(date_releve);
CREATE INDEX IF NOT EXISTS idx_competitor_pricing_cat ON market.competitor_pricing(categorie, concurrent_id);

-- market.mnp_flows
CREATE TABLE IF NOT EXISTS market.mnp_flows (
    mnp_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    direction        VARCHAR(10) NOT NULL CHECK (direction IN ('PORT_IN','PORT_OUT')),
    operateur_origine  VARCHAR(20),
    operateur_destination VARCHAR(20),
    mois             DATE NOT NULL,
    volume           INTEGER NOT NULL DEFAULT 0,
    categorie_client VARCHAR(20) DEFAULT 'RESI',
    raison_principale VARCHAR(100),
    wilaya           VARCHAR(100),
    created_at       TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mnp_mois ON market.mnp_flows(mois, direction);

-- market.network_events
CREATE TABLE IF NOT EXISTS market.network_events (
    event_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wilaya           VARCHAR(100),
    store_ids        JSONB,
    event_type       VARCHAR(30) NOT NULL
        CHECK (event_type IN ('PANNE','MAINTENANCE','UPGRADE_4G','UPGRADE_5G','CONGESTION','COUPURE_FIBRE','DEGRADATION')),
    severite         VARCHAR(10) DEFAULT 'MEDIUM',
    start_time       TIMESTAMP NOT NULL,
    end_time         TIMESTAMP,
    clients_impactes INTEGER,
    impact_ventes_pct NUMERIC(6,2) DEFAULT 0,
    resolu           BOOLEAN DEFAULT FALSE,
    notes            TEXT,
    created_at       TIMESTAMP DEFAULT NOW()
);

-- customer.segments
CREATE TABLE IF NOT EXISTS customer.segments (
    segment_id       VARCHAR(20) PRIMARY KEY,
    libelle          VARCHAR(100) NOT NULL,
    description      TEXT,
    arpu_moyen_tnd   NUMERIC(10,2),
    arpu_std_tnd     NUMERIC(10,2),
    churn_rate_base  NUMERIC(6,4),
    duree_vie_mois   INTEGER,
    canal_prefere    VARCHAR(20),
    products_preferes JSONB,
    nb_clients_estime INTEGER,
    poids_marche_pct NUMERIC(6,3),
    actif            BOOLEAN DEFAULT TRUE
);

-- customer.churn_signals
CREATE TABLE IF NOT EXISTS customer.churn_signals (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id         VARCHAR(50),
    signal_date      DATE NOT NULL,
    nb_inactifs_30j  INTEGER DEFAULT 0,
    nb_inactifs_60j  INTEGER DEFAULT 0,
    nb_reclamations  INTEGER DEFAULT 0,
    nb_port_out      INTEGER DEFAULT 0,
    taux_renouvellement NUMERIC(6,4),
    score_risque_churn NUMERIC(5,2),
    facteurs_risque  JSONB,
    actions_retention JSONB,
    created_at       TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_churn_store_date ON customer.churn_signals(store_id, signal_date);

-- customer.nps_csat
CREATE TABLE IF NOT EXISTS customer.nps_csat (
    id               SERIAL PRIMARY KEY,
    store_id         VARCHAR(50),
    agent_id         INTEGER,
    feedback_date    DATE NOT NULL,
    type_enquete     VARCHAR(10) NOT NULL CHECK (type_enquete IN ('NPS','CSAT','CES')),
    score            NUMERIC(5,2),
    verbatim         TEXT,
    categorie_motif  VARCHAR(100),
    canal            VARCHAR(20) DEFAULT 'POST_VENTE',
    resolu           BOOLEAN DEFAULT FALSE,
    created_at       TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_nps_store_date ON customer.nps_csat(store_id, feedback_date);

-- supply.suppliers
CREATE TABLE IF NOT EXISTS supply.suppliers (
    supplier_id          VARCHAR(30) PRIMARY KEY,
    nom                  VARCHAR(200) NOT NULL,
    pays_origine         VARCHAR(50),
    type_fournisseur     VARCHAR(30) NOT NULL,
    categories           JSONB,
    marques              JSONB,
    delai_livraison_moy  INTEGER DEFAULT 14,
    delai_livraison_std  INTEGER DEFAULT 3,
    taux_fiabilite       NUMERIC(5,4) DEFAULT 0.90,
    commande_min         INTEGER DEFAULT 1,
    commande_multiple    INTEGER DEFAULT 1,
    devise               VARCHAR(5) DEFAULT 'TND',
    conditions_paiement  VARCHAR(100),
    contact_nom          VARCHAR(100),
    contact_email        VARCHAR(200),
    actif                BOOLEAN DEFAULT TRUE,
    score_global         NUMERIC(5,2) DEFAULT 0.0,
    created_at           TIMESTAMP DEFAULT NOW(),
    updated_at           TIMESTAMP DEFAULT NOW()
);

-- supply.purchase_orders
CREATE TABLE IF NOT EXISTS supply.purchase_orders (
    po_id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sku                  INTEGER NOT NULL,
    supplier_id          VARCHAR(30) REFERENCES supply.suppliers(supplier_id),
    store_id             VARCHAR(50),
    quantite_commandee   INTEGER NOT NULL,
    quantite_recue       INTEGER DEFAULT 0,
    prix_unitaire_ht     NUMERIC(12,4),
    montant_total_ht     NUMERIC(14,4),
    devise               VARCHAR(5) DEFAULT 'TND',
    statut               VARCHAR(20) DEFAULT 'BROUILLON'
        CHECK (statut IN ('BROUILLON','SOUMIS','CONFIRME','EXPEDIE','RECU_PARTIEL','RECU','ANNULE','LITIGE')),
    priorite             VARCHAR(10) DEFAULT 'NORMAL',
    date_commande        TIMESTAMP DEFAULT NOW(),
    date_livraison_prevue DATE,
    date_livraison_reelle DATE,
    delai_reel_jours     INTEGER,
    livraison_conforme   BOOLEAN,
    reference_externe    VARCHAR(100),
    notes                TEXT,
    created_at           TIMESTAMP DEFAULT NOW(),
    updated_at           TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_po_sku ON supply.purchase_orders(sku, statut);
CREATE INDEX IF NOT EXISTS idx_po_store ON supply.purchase_orders(store_id, statut);

-- supply.stock_movements
CREATE TABLE IF NOT EXISTS supply.stock_movements (
    mouvement_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sku              INTEGER NOT NULL,
    store_id         VARCHAR(50) NOT NULL,
    type_mouvement   VARCHAR(30) NOT NULL
        CHECK (type_mouvement IN ('RECEPTION_BC','VENTE','RETOUR_CLIENT','RETOUR_FOURNISSEUR',
               'TRANSFERT_ENTRANT','TRANSFERT_SORTANT','AJUSTEMENT_INVENTAIRE','CASSE_PERTE',
               'INVENTAIRE_GAIN','INVENTAIRE_PERTE','RESERVATION','LIBERATION_RESERVATION')),
    quantite         INTEGER NOT NULL,
    stock_avant      INTEGER,
    stock_apres      INTEGER,
    reference_id     VARCHAR(100),
    reference_type   VARCHAR(30),
    agent_id         INTEGER,
    date_mouvement   TIMESTAMP DEFAULT NOW(),
    notes            TEXT,
    created_at       TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mvt_sku_store ON supply.stock_movements(sku, store_id, date_mouvement DESC);
CREATE INDEX IF NOT EXISTS idx_mvt_store_date ON supply.stock_movements(store_id, date_mouvement DESC);

-- supply.transfers
CREATE TABLE IF NOT EXISTS supply.transfers (
    transfer_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sku              INTEGER NOT NULL,
    store_source     VARCHAR(50) NOT NULL,
    store_dest       VARCHAR(50) NOT NULL,
    quantite         INTEGER NOT NULL,
    statut           VARCHAR(20) DEFAULT 'DEMANDE'
        CHECK (statut IN ('DEMANDE','APPROUVE','EXPEDIE','RECU','REJETE','ANNULE','EN_LITIGE')),
    priorite         VARCHAR(10) DEFAULT 'NORMAL',
    motif            VARCHAR(100),
    date_demande     TIMESTAMP DEFAULT NOW(),
    date_approbation TIMESTAMP,
    date_expedition  TIMESTAMP,
    date_reception   TIMESTAMP,
    notes            TEXT
);

-- supply.serial_numbers
CREATE TABLE IF NOT EXISTS supply.serial_numbers (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    num_serie        VARCHAR(50) UNIQUE NOT NULL,
    type_serie       VARCHAR(10) NOT NULL CHECK (type_serie IN ('IMEI','ICCID','ESIM','EAN')),
    sku              INTEGER,
    store_id         VARCHAR(50),
    po_id            UUID REFERENCES supply.purchase_orders(po_id),
    statut           VARCHAR(20) DEFAULT 'EN_STOCK'
        CHECK (statut IN ('EN_STOCK','VENDU','RESERVE','DEFECTUEUX','RETOURNE','VOLE','EN_TRANSIT')),
    date_reception   DATE,
    date_vente       DATE,
    sale_id          VARCHAR(100),
    num_client       VARCHAR(30),
    notes            TEXT,
    created_at       TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_serial_sku_store ON supply.serial_numbers(sku, store_id, statut);

-- supply.reorder_params
CREATE TABLE IF NOT EXISTS supply.reorder_params (
    sku              INTEGER NOT NULL,
    store_id         VARCHAR(50) NOT NULL,
    demande_moy_jour NUMERIC(10,4),
    demande_std_jour NUMERIC(10,4),
    lead_time_moy    NUMERIC(8,2),
    lead_time_std    NUMERIC(8,2),
    stock_securite   INTEGER,
    point_commande   INTEGER,
    eoq              INTEGER,
    niveau_service   NUMERIC(5,4) DEFAULT 0.95,
    jours_stock_cible INTEGER DEFAULT 30,
    derniere_maj     TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (sku, store_id)
);

SELECT 'MIGRATION 005+006 APPLIED' AS status;
