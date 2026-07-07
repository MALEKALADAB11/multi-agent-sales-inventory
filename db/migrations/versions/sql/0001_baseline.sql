--
-- PostgreSQL database dump
--


-- Dumped from database version 17.10
-- Dumped by pg_dump version 17.10

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: coaching; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA coaching;



--
-- Name: customer; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA customer;



--
-- Name: inventory; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA inventory;



--
-- Name: market; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA market;



--
-- Name: monitoring; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA monitoring;



--
-- Name: sales; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA sales;



--
-- Name: supply; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA supply;



--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: sync_stock_on_sale(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.sync_stock_on_sale() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
                DECLARE v_daily_avg FLOAT;
                BEGIN
                    SELECT COALESCE(AVG(daily_qty), 0) INTO v_daily_avg
                    FROM (SELECT date_only, SUM(qte_produit)::float AS daily_qty
                          FROM sales.transactions_rt
                          WHERE cod_prod=NEW.cod_prod AND store_id=NEW.store_id
                            AND date_only >= CURRENT_DATE - INTERVAL '30 days'
                          GROUP BY date_only) sub;
                    UPDATE inventory.stock_levels
                    SET quantity=GREATEST(0,quantity-NEW.qte_produit),
                        last_sold=NEW.date_vente::date, updated_at=NOW(), last_updated=NOW(),
                        remaining_days_of_stock=CASE WHEN v_daily_avg>0
                            THEN GREATEST(0,(GREATEST(0,quantity-NEW.qte_produit)-quantity_reserved)::float/v_daily_avg)
                            ELSE 999.0 END
                    WHERE sku=NEW.cod_prod AND store_id=NEW.store_id;
                    RETURN NEW;
                END; $$;



--
-- Name: compute_gap(numeric, numeric); Type: FUNCTION; Schema: sales; Owner: postgres
--

CREATE FUNCTION sales.compute_gap(p_ca numeric, p_objectif numeric) RETURNS numeric
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT CASE 
        WHEN p_objectif = 0 THEN 0
        ELSE ((p_objectif - p_ca) / p_objectif * 100)
    END;
$$;



--
-- Name: get_ca_jour(text, date); Type: FUNCTION; Schema: sales; Owner: postgres
--

CREATE FUNCTION sales.get_ca_jour(p_store_id text, p_date date) RETURNS numeric
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT COALESCE(SUM(lig_ttc), 0)
    FROM sales.transactions
    WHERE store_id = p_store_id 
    AND date_only = p_date;
$$;



--
-- Name: get_objectif_jour(text, date); Type: FUNCTION; Schema: sales; Owner: postgres
--

CREATE FUNCTION sales.get_objectif_jour(p_store_id text, p_date date) RETURNS numeric
    LANGUAGE sql STABLE
    AS $$
    SELECT COALESCE(objectif_ca, 0)
    FROM sales.objectifs
    WHERE store_id = p_store_id 
    AND date_objectif = p_date
    LIMIT 1;
$$;



SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: coaching_events; Type: TABLE; Schema: coaching; Owner: postgres
--

CREATE TABLE coaching.coaching_events (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    advisor_id integer NOT NULL,
    store_id character varying(50) NOT NULL,
    cycle_id character varying(100),
    urgency_level character varying(10) NOT NULL,
    urgency_score numeric(5,2) DEFAULT 0,
    gap_pct numeric(7,2),
    gap_amount numeric(12,2),
    forecast_eod numeric(12,2),
    advice_text text,
    produit_a_pousser character varying(200),
    produit_a_eviter character varying(200),
    strategie text,
    cause_racine text,
    rag_used boolean DEFAULT false,
    nb_rag_scripts integer DEFAULT 0,
    script_ids jsonb DEFAULT '[]'::jsonb,
    context_hash character varying(64),
    weather_label character varying(100),
    weather_temp_c numeric(4,1),
    weather_effect numeric(5,2),
    event_name character varying(200),
    event_proximity_km numeric(6,2),
    guardrail_status character varying(20) DEFAULT 'APPROVE'::character varying,
    guardrail_rule character varying(100),
    feedback_score integer,
    was_effective boolean,
    ca_after_coaching numeric(12,2),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT coaching_events_feedback_score_check CHECK (((feedback_score >= 1) AND (feedback_score <= 5))),
    CONSTRAINT coaching_events_guardrail_status_check CHECK (((guardrail_status)::text = ANY ((ARRAY['APPROVE'::character varying, 'BLOCK'::character varying, 'REWRITE'::character varying])::text[]))),
    CONSTRAINT coaching_events_urgency_level_check CHECK (((urgency_level)::text = ANY ((ARRAY['HIGH'::character varying, 'MEDIUM'::character varying, 'LOW'::character varying])::text[])))
);



--
-- Name: escalation_seq; Type: SEQUENCE; Schema: coaching; Owner: postgres
--

CREATE SEQUENCE coaching.escalation_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: nps_csat; Type: TABLE; Schema: customer; Owner: postgres
--

CREATE TABLE customer.nps_csat (
    id integer NOT NULL,
    store_id character varying(50),
    agent_id integer,
    feedback_date date NOT NULL,
    type_enquete character varying(10) NOT NULL,
    score numeric(5,2),
    verbatim text,
    categorie_motif character varying(100),
    canal character varying(20) DEFAULT 'POST_VENTE'::character varying,
    resolu boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT nps_csat_type_enquete_check CHECK (((type_enquete)::text = ANY ((ARRAY['NPS'::character varying, 'CSAT'::character varying, 'CES'::character varying])::text[])))
);



--
-- Name: nps_csat_id_seq; Type: SEQUENCE; Schema: customer; Owner: postgres
--

CREATE SEQUENCE customer.nps_csat_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: nps_csat_id_seq; Type: SEQUENCE OWNED BY; Schema: customer; Owner: postgres
--

ALTER SEQUENCE customer.nps_csat_id_seq OWNED BY customer.nps_csat.id;


--
-- Name: segments; Type: TABLE; Schema: customer; Owner: postgres
--

CREATE TABLE customer.segments (
    segment_id character varying(20) NOT NULL,
    libelle character varying(100) NOT NULL,
    description text,
    arpu_moyen_tnd numeric(10,2),
    arpu_std_tnd numeric(10,2),
    churn_rate_base numeric(6,4),
    duree_vie_mois integer,
    canal_prefere character varying(20),
    products_preferes jsonb,
    nb_clients_estime integer,
    poids_marche_pct numeric(6,3),
    actif boolean DEFAULT true
);



--
-- Name: agent_runs; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.agent_runs (
    id integer NOT NULL,
    cycle_id text,
    agent_name text,
    store_id text,
    sku text,
    started_at timestamp without time zone DEFAULT now(),
    completed_at timestamp without time zone,
    duration_ms double precision,
    status text DEFAULT 'running'::text,
    input_summary jsonb,
    output_summary jsonb,
    error_message text,
    created_at timestamp without time zone DEFAULT now(),
    items_processed integer DEFAULT 0,
    items_succeeded integer DEFAULT 0,
    items_failed integer DEFAULT 0,
    batch_id text,
    alerts_generated integer DEFAULT 0,
    recommendations_generated integer DEFAULT 0
);



--
-- Name: agent_runs_id_seq; Type: SEQUENCE; Schema: inventory; Owner: postgres
--

CREATE SEQUENCE inventory.agent_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: agent_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: inventory; Owner: postgres
--

ALTER SEQUENCE inventory.agent_runs_id_seq OWNED BY inventory.agent_runs.id;


--
-- Name: alerts; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.alerts (
    id integer NOT NULL,
    store_id text,
    sku integer,
    alert_type text DEFAULT 'stockout_risk'::text,
    severity text DEFAULT 'medium'::text,
    message text,
    status text DEFAULT 'pending'::text,
    created_at timestamp without time zone DEFAULT now(),
    resolved_at timestamp without time zone,
    triggered_at timestamp without time zone DEFAULT now(),
    recommended_action text,
    agent_run_id text,
    CONSTRAINT alerts_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'acknowledged'::text, 'validated'::text, 'rejected'::text, 'dismissed'::text, 'resolved'::text])))
);



--
-- Name: alerts_id_seq; Type: SEQUENCE; Schema: inventory; Owner: postgres
--

CREATE SEQUENCE inventory.alerts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: inventory; Owner: postgres
--

ALTER SEQUENCE inventory.alerts_id_seq OWNED BY inventory.alerts.id;


--
-- Name: business_objectives; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.business_objectives (
    id integer NOT NULL,
    objective_type text DEFAULT 'balanced'::text NOT NULL,
    label text,
    description text,
    is_active boolean DEFAULT false,
    priority integer DEFAULT 1,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: business_objectives_id_seq; Type: SEQUENCE; Schema: inventory; Owner: postgres
--

CREATE SEQUENCE inventory.business_objectives_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: business_objectives_id_seq; Type: SEQUENCE OWNED BY; Schema: inventory; Owner: postgres
--

ALTER SEQUENCE inventory.business_objectives_id_seq OWNED BY inventory.business_objectives.id;


--
-- Name: context_adjustments; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.context_adjustments (
    id integer NOT NULL,
    sku integer NOT NULL,
    store_id text NOT NULL,
    demand_uplift_pct numeric(8,2) DEFAULT 0,
    adjustment_source text,
    weather_impact numeric(5,2),
    promo_impact numeric(5,2),
    event_impact numeric(5,2),
    created_at timestamp without time zone DEFAULT now(),
    valid_from date DEFAULT CURRENT_DATE,
    valid_to date DEFAULT (CURRENT_DATE + 7),
    confidence numeric(5,3) DEFAULT 0.5,
    dominant_signal text,
    signals jsonb,
    holiday_impact numeric(5,2) DEFAULT 0,
    category text,
    store_name text,
    interpretation text,
    agent_run_id integer
);



--
-- Name: context_adjustments_id_seq; Type: SEQUENCE; Schema: inventory; Owner: postgres
--

CREATE SEQUENCE inventory.context_adjustments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: context_adjustments_id_seq; Type: SEQUENCE OWNED BY; Schema: inventory; Owner: postgres
--

ALTER SEQUENCE inventory.context_adjustments_id_seq OWNED BY inventory.context_adjustments.id;


--
-- Name: demand_forecast; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.demand_forecast (
    id integer NOT NULL,
    sku text NOT NULL,
    store_id text NOT NULL,
    forecast_date date NOT NULL,
    demand_24h numeric(10,2),
    confidence_low numeric(10,2),
    confidence_high numeric(10,2),
    model_version text DEFAULT 'timesfm-v1'::text,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: demand_forecast_id_seq; Type: SEQUENCE; Schema: inventory; Owner: postgres
--

CREATE SEQUENCE inventory.demand_forecast_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: demand_forecast_id_seq; Type: SEQUENCE OWNED BY; Schema: inventory; Owner: postgres
--

ALTER SEQUENCE inventory.demand_forecast_id_seq OWNED BY inventory.demand_forecast.id;


--
-- Name: events; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.events (
    id integer NOT NULL,
    event_name text,
    event_type text,
    start_date date,
    end_date date,
    sku integer,
    store_id text,
    impact_pct numeric(5,2) DEFAULT 0,
    created_at timestamp without time zone DEFAULT now(),
    affected_categories text,
    estimated_uplift numeric(5,2) DEFAULT 0,
    estimated_uplift_pct numeric(5,2) DEFAULT 0,
    scope text
);



--
-- Name: events_id_seq; Type: SEQUENCE; Schema: inventory; Owner: postgres
--

CREATE SEQUENCE inventory.events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: events_id_seq; Type: SEQUENCE OWNED BY; Schema: inventory; Owner: postgres
--

ALTER SEQUENCE inventory.events_id_seq OWNED BY inventory.events.id;


--
-- Name: product_master; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.product_master (
    sku integer NOT NULL,
    product_name text,
    category text,
    unit_cost numeric(10,2),
    unit_price numeric(10,2),
    lead_time_days integer,
    lead_time_std integer,
    moq integer DEFAULT 1,
    holding_cost_pct numeric(5,2),
    order_cost numeric(10,2),
    lifecycle_stage text,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);



--
-- Name: produits; Type: TABLE; Schema: sales; Owner: postgres
--

CREATE TABLE sales.produits (
    sku integer NOT NULL,
    nom text NOT NULL,
    categorie text,
    famille text,
    prix_ht numeric(10,2),
    prix_ttc numeric(10,2),
    marge_pct numeric(5,2),
    stock_initial integer DEFAULT 0,
    actif boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now(),
    marque character varying(100),
    modele character varying(200),
    gamme_libelle character varying(100),
    famille_libelle character varying(100),
    pa_ht numeric(12,4),
    marge_pct_calc numeric(6,2),
    flag_4g boolean DEFAULT false,
    flag_5g boolean DEFAULT false,
    flag_terminal boolean DEFAULT false,
    flag_forfait boolean DEFAULT false,
    flag_sim boolean DEFAULT false,
    flag_recharge boolean DEFAULT false,
    serialisable boolean DEFAULT false,
    stockable boolean DEFAULT true,
    lead_time_days integer DEFAULT 14,
    lead_time_std integer DEFAULT 3,
    moq integer DEFAULT 1,
    holding_cost_pct numeric(8,6) DEFAULT 0.2,
    order_cost numeric(12,4) DEFAULT 50,
    date_lancement date,
    date_eol date,
    lifecycle_stage character varying(20) DEFAULT 'mature'::character varying,
    stockage_gb integer,
    ram_gb integer,
    couleur character varying(50)
);



--
-- Name: products; Type: VIEW; Schema: inventory; Owner: postgres
--

CREATE VIEW inventory.products AS
 SELECT p.sku,
    p.nom AS product_name,
    p.categorie AS category,
    p.famille AS family,
    p.prix_ht AS unit_cost,
    p.prix_ttc AS unit_price,
    p.marge_pct,
    p.actif AS active,
    COALESCE(pm.lead_time_days, 10) AS lead_time_days,
    COALESCE(pm.lead_time_std, 3) AS lead_time_std,
    COALESCE(pm.moq, 1) AS moq,
    COALESCE(pm.holding_cost_pct, 0.2) AS holding_cost_pct,
    COALESCE(pm.order_cost, (50)::numeric) AS order_cost,
    pm.lifecycle_stage
   FROM (sales.produits p
     LEFT JOIN inventory.product_master pm ON ((pm.sku = p.sku)));



--
-- Name: promotions; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.promotions (
    id bigint NOT NULL,
    promo_id text NOT NULL,
    promo_name text,
    start_date date NOT NULL,
    end_date date NOT NULL,
    sku integer,
    product_name text,
    category text,
    discount_pct numeric(5,2) DEFAULT 0,
    promo_type text,
    scope text,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: promotions_id_seq; Type: SEQUENCE; Schema: inventory; Owner: postgres
--

CREATE SEQUENCE inventory.promotions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: promotions_id_seq; Type: SEQUENCE OWNED BY; Schema: inventory; Owner: postgres
--

ALTER SEQUENCE inventory.promotions_id_seq OWNED BY inventory.promotions.id;


--
-- Name: recommendations; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.recommendations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sku integer NOT NULL,
    store_id text NOT NULL,
    recommendation_type text,
    action text,
    order_qty integer,
    urgency text,
    confidence numeric(5,3),
    recommendation_text text,
    trade_offs text,
    escalate_to_human boolean DEFAULT false,
    escalation_reason text,
    status text DEFAULT 'pending'::text,
    decided_by text,
    decided_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    agent_run_id text,
    order_cost numeric(10,2),
    holding_cost numeric(10,2),
    suggested_quantity integer,
    CONSTRAINT reco_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text, 'executed'::text, 'cancelled'::text])))
);



--
-- Name: sales_history; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.sales_history (
    id bigint NOT NULL,
    record_date date NOT NULL,
    store_id text NOT NULL,
    store_name text,
    region text,
    sku integer NOT NULL,
    product_name text,
    category text,
    quantity_sold integer DEFAULT 0,
    revenue numeric(12,2) DEFAULT 0,
    unit_price numeric(10,2),
    is_promo boolean DEFAULT false,
    event_name text,
    event_type text,
    season text,
    created_at timestamp without time zone DEFAULT now(),
    promo_type character varying(50),
    day_of_week smallint,
    week_of_year smallint,
    month_num smallint,
    year_num smallint,
    is_weekend boolean DEFAULT false,
    is_event_day boolean DEFAULT false,
    event_intensite character varying(10),
    uplift_factor numeric(6,4) DEFAULT 1.0
);



--
-- Name: sales_history_id_seq; Type: SEQUENCE; Schema: inventory; Owner: postgres
--

CREATE SEQUENCE inventory.sales_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: sales_history_id_seq; Type: SEQUENCE OWNED BY; Schema: inventory; Owner: postgres
--

ALTER SEQUENCE inventory.sales_history_id_seq OWNED BY inventory.sales_history.id;


--
-- Name: stock_history; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.stock_history (
    id bigint NOT NULL,
    record_date date NOT NULL,
    store_id text NOT NULL,
    store_name text,
    region text,
    sku integer NOT NULL,
    product_name text,
    category text,
    stock_level integer DEFAULT 0,
    is_stockout boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: stock_history_id_seq; Type: SEQUENCE; Schema: inventory; Owner: postgres
--

CREATE SEQUENCE inventory.stock_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: stock_history_id_seq; Type: SEQUENCE OWNED BY; Schema: inventory; Owner: postgres
--

ALTER SEQUENCE inventory.stock_history_id_seq OWNED BY inventory.stock_history.id;


--
-- Name: stock_levels; Type: TABLE; Schema: inventory; Owner: postgres
--

CREATE TABLE inventory.stock_levels (
    id bigint NOT NULL,
    store_id text NOT NULL,
    sku integer NOT NULL,
    quantity integer,
    quantity_reserved integer DEFAULT 0,
    quantity_available integer GENERATED ALWAYS AS ((quantity - quantity_reserved)) STORED,
    last_received date,
    last_sold date,
    updated_at timestamp without time zone DEFAULT now(),
    remaining_days_of_stock double precision,
    last_updated timestamp without time zone DEFAULT now()
);



--
-- Name: stock_levels_id_seq; Type: SEQUENCE; Schema: inventory; Owner: postgres
--

CREATE SEQUENCE inventory.stock_levels_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: stock_levels_id_seq; Type: SEQUENCE OWNED BY; Schema: inventory; Owner: postgres
--

ALTER SEQUENCE inventory.stock_levels_id_seq OWNED BY inventory.stock_levels.id;


--
-- Name: stock_levels_v; Type: VIEW; Schema: inventory; Owner: postgres
--

CREATE VIEW inventory.stock_levels_v AS
 SELECT id,
    store_id,
    sku,
    quantity AS stock_current,
    quantity,
    COALESCE(quantity_reserved, 0) AS quantity_reserved,
    COALESCE((quantity - COALESCE(quantity_reserved, 0)), 0) AS available,
    last_received,
    last_sold,
    updated_at
   FROM inventory.stock_levels;



--
-- Name: boutiques; Type: TABLE; Schema: sales; Owner: postgres
--

CREATE TABLE sales.boutiques (
    store_id text NOT NULL,
    store_name text NOT NULL,
    address text,
    ville text,
    region text,
    manager_name text,
    phone text,
    active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now(),
    type_boutique character varying(5),
    canal character varying(20) DEFAULT 'PHYSIQUE'::character varying,
    wilaya character varying(100),
    zone_commerciale character varying(100),
    latitude numeric(10,7),
    longitude numeric(10,7),
    capacite_conseillers integer DEFAULT 4,
    date_ouverture date,
    is_officielle boolean DEFAULT false,
    rang_ca_region integer,
    email_store character varying(200)
);



--
-- Name: stores; Type: VIEW; Schema: inventory; Owner: postgres
--

CREATE VIEW inventory.stores AS
 SELECT store_id,
    store_name,
    ville AS city,
    region,
    manager_name,
    active
   FROM sales.boutiques;



--
-- Name: vw_active_promotions; Type: VIEW; Schema: inventory; Owner: postgres
--

CREATE VIEW inventory.vw_active_promotions AS
 SELECT promo_id,
    promo_name,
    sku,
    product_name,
    discount_pct,
    start_date,
    end_date
   FROM inventory.promotions
  WHERE (end_date >= CURRENT_DATE);



--
-- Name: vw_stock_enriched; Type: VIEW; Schema: inventory; Owner: postgres
--

CREATE VIEW inventory.vw_stock_enriched AS
 SELECT sl.store_id,
    sl.sku,
    p.nom,
    p.categorie,
    sl.quantity,
    sl.quantity_available,
        CASE
            WHEN (sl.quantity_available < 5) THEN 'RUPTURE'::text
            WHEN (sl.quantity_available < 10) THEN 'BAS'::text
            ELSE 'OK'::text
        END AS stock_status,
    p.prix_ttc,
    sl.last_sold,
    sl.updated_at
   FROM (inventory.stock_levels sl
     JOIN sales.produits p ON ((sl.sku = p.sku)));



--
-- Name: competitor_pricing; Type: TABLE; Schema: market; Owner: postgres
--

CREATE TABLE market.competitor_pricing (
    id integer NOT NULL,
    concurrent_id character varying(20) NOT NULL,
    categorie character varying(50) NOT NULL,
    produit_type character varying(200) NOT NULL,
    donnees_go numeric(8,1),
    minutes_voix integer,
    sms_count integer,
    prix_ht numeric(10,2),
    prix_ttc numeric(10,2),
    engagement_mois integer DEFAULT 0,
    date_releve date NOT NULL,
    source character varying(30) DEFAULT 'WEB'::character varying,
    actif boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: competitor_pricing_id_seq; Type: SEQUENCE; Schema: market; Owner: postgres
--

CREATE SEQUENCE market.competitor_pricing_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: competitor_pricing_id_seq; Type: SEQUENCE OWNED BY; Schema: market; Owner: postgres
--

ALTER SEQUENCE market.competitor_pricing_id_seq OWNED BY market.competitor_pricing.id;


--
-- Name: competitors; Type: TABLE; Schema: market; Owner: postgres
--

CREATE TABLE market.competitors (
    concurrent_id character varying(20) NOT NULL,
    nom character varying(100) NOT NULL,
    code_operateur character varying(10),
    pays character varying(50) DEFAULT 'Tunisia'::character varying,
    part_marche_pct numeric(6,3),
    nb_abonnes bigint,
    positionnement character varying(20) DEFAULT 'MID'::character varying,
    points_forts jsonb,
    points_faibles jsonb,
    date_entree_marche date,
    actif boolean DEFAULT true,
    updated_at timestamp without time zone DEFAULT now()
);



--
-- Name: events; Type: TABLE; Schema: market; Owner: postgres
--

CREATE TABLE market.events (
    event_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    event_name character varying(200) NOT NULL,
    event_type character varying(50) NOT NULL,
    sous_type character varying(100),
    start_date date NOT NULL,
    end_date date NOT NULL,
    annee integer GENERATED ALWAYS AS ((EXTRACT(year FROM start_date))::integer) STORED,
    scope character varying(20) DEFAULT 'NATIONAL'::character varying,
    region_ids jsonb,
    categories_impactees jsonb,
    uplift_terminal numeric(6,2) DEFAULT 0,
    uplift_forfait numeric(6,2) DEFAULT 0,
    uplift_sim numeric(6,2) DEFAULT 0,
    uplift_recharge numeric(6,2) DEFAULT 0,
    uplift_accessoire numeric(6,2) DEFAULT 0,
    intensite character varying(10) DEFAULT 'MEDIUM'::character varying,
    source_donnee character varying(50) DEFAULT 'HISTORIQUE'::character varying,
    note_strategie text,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT events_event_type_check CHECK (((event_type)::text = ANY ((ARRAY['RELIGIEUX'::character varying, 'SCOLAIRE'::character varying, 'SPORTIF'::character varying, 'COMMERCIAL'::character varying, 'NATIONAL'::character varying, 'CONCURRENTIEL'::character varying, 'METEO'::character varying, 'RESEAU'::character varying])::text[]))),
    CONSTRAINT events_intensite_check CHECK (((intensite)::text = ANY ((ARRAY['LOW'::character varying, 'MEDIUM'::character varying, 'HIGH'::character varying, 'EXTREME'::character varying])::text[])))
);



--
-- Name: mnp_flows; Type: TABLE; Schema: market; Owner: postgres
--

CREATE TABLE market.mnp_flows (
    mnp_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    direction character varying(10) NOT NULL,
    operateur_origine character varying(20),
    operateur_destination character varying(20),
    mois date NOT NULL,
    volume integer DEFAULT 0 NOT NULL,
    categorie_client character varying(20) DEFAULT 'RESI'::character varying,
    raison_principale character varying(100),
    wilaya character varying(100),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT mnp_flows_direction_check CHECK (((direction)::text = ANY ((ARRAY['PORT_IN'::character varying, 'PORT_OUT'::character varying])::text[])))
);



--
-- Name: seasonal_patterns; Type: TABLE; Schema: market; Owner: postgres
--

CREATE TABLE market.seasonal_patterns (
    id integer NOT NULL,
    categorie character varying(50) NOT NULL,
    mois integer NOT NULL,
    semaine_mois integer,
    jour_semaine integer,
    heure_debut integer,
    heure_fin integer,
    facteur_demande numeric(6,4) DEFAULT 1.0 NOT NULL,
    facteur_std numeric(6,4) DEFAULT 0.1,
    nb_annees_data integer DEFAULT 2,
    confidence character varying(10) DEFAULT 'MEDIUM'::character varying,
    notes character varying(200),
    updated_at timestamp without time zone DEFAULT now(),
    CONSTRAINT seasonal_patterns_confidence_check CHECK (((confidence)::text = ANY ((ARRAY['LOW'::character varying, 'MEDIUM'::character varying, 'HIGH'::character varying, 'VERY_HIGH'::character varying])::text[]))),
    CONSTRAINT seasonal_patterns_heure_debut_check CHECK (((heure_debut >= 0) AND (heure_debut <= 23))),
    CONSTRAINT seasonal_patterns_heure_fin_check CHECK (((heure_fin >= 0) AND (heure_fin <= 23))),
    CONSTRAINT seasonal_patterns_jour_semaine_check CHECK (((jour_semaine >= 0) AND (jour_semaine <= 6))),
    CONSTRAINT seasonal_patterns_mois_check CHECK (((mois >= 1) AND (mois <= 12))),
    CONSTRAINT seasonal_patterns_semaine_mois_check CHECK (((semaine_mois >= 1) AND (semaine_mois <= 5)))
);



--
-- Name: seasonal_patterns_id_seq; Type: SEQUENCE; Schema: market; Owner: postgres
--

CREATE SEQUENCE market.seasonal_patterns_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: seasonal_patterns_id_seq; Type: SEQUENCE OWNED BY; Schema: market; Owner: postgres
--

ALTER SEQUENCE market.seasonal_patterns_id_seq OWNED BY market.seasonal_patterns.id;


--
-- Name: transactions_rt; Type: TABLE; Schema: sales; Owner: postgres
--

CREATE TABLE sales.transactions_rt (
    sale_id uuid NOT NULL,
    date_vente timestamp without time zone NOT NULL,
    date_only date NOT NULL,
    heure integer NOT NULL,
    store_id text NOT NULL,
    agent_id integer,
    cod_prod integer,
    des_produit text,
    lig_ttc numeric(10,2),
    lig_ht numeric(10,2),
    lig_tva numeric(10,2),
    qte_produit integer DEFAULT 1,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: TABLE transactions_rt; Type: COMMENT; Schema: sales; Owner: postgres
--

COMMENT ON TABLE sales.transactions_rt IS 'Transactions temps rÃ©el gÃ©nÃ©rÃ©es par le simulateur. Sans FK â€” aucune contrainte bloquante. fetch_pos_data fait UNION ALL avec sales.transactions (historique).';


--
-- Name: category_gap_live; Type: VIEW; Schema: monitoring; Owner: postgres
--

CREATE VIEW monitoring.category_gap_live AS
 SELECT t.store_id,
        CASE
            WHEN (p.flag_terminal = true) THEN 'terminal'::text
            WHEN (p.flag_forfait = true) THEN 'forfait'::text
            WHEN (p.categorie = '70'::text) THEN 'accessoire'::text
            ELSE 'autre'::text
        END AS product_category,
    sum(t.lig_ttc) AS revenue_today
   FROM (sales.transactions_rt t
     JOIN sales.produits p ON ((p.sku = t.cod_prod)))
  WHERE (t.date_only = CURRENT_DATE)
  GROUP BY t.store_id,
        CASE
            WHEN (p.flag_terminal = true) THEN 'terminal'::text
            WHEN (p.flag_forfait = true) THEN 'forfait'::text
            WHEN (p.categorie = '70'::text) THEN 'accessoire'::text
            ELSE 'autre'::text
        END;



--
-- Name: VIEW category_gap_live; Type: COMMENT; Schema: monitoring; Owner: postgres
--

COMMENT ON VIEW monitoring.category_gap_live IS 'Gap en temps rÃ©el par catÃ©gorie produit vs objectifs journaliers. ConsommÃ© par CoachAgent pour identifier quelle catÃ©gorie prioriser dans le pitch.';


--
-- Name: coaching_interactions; Type: VIEW; Schema: monitoring; Owner: postgres
--

CREATE VIEW monitoring.coaching_interactions AS
 SELECT id,
    advisor_id,
    store_id,
    cycle_id,
    urgency_level,
    gap_pct,
    advice_text,
    produit_a_pousser,
    rag_used,
    nb_rag_scripts,
    guardrail_status,
    was_effective,
    feedback_score,
    created_at
   FROM coaching.coaching_events ce
  ORDER BY created_at DESC;



--
-- Name: agent_cycles; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.agent_cycles (
    id integer NOT NULL,
    cycle_id character varying(50),
    store_id character varying(10) DEFAULT 'I63'::character varying,
    triggered_by character varying(20),
    urgency_level character varying(10),
    urgency_score double precision,
    gap_pct double precision,
    gap_amount double precision,
    ca_today double precision,
    ca_target double precision,
    forecast_eod double precision,
    analyst_summary text,
    strategie text,
    nb_actions integer DEFAULT 0,
    cause_racine text,
    rag_used boolean DEFAULT false,
    nb_rag_scripts integer DEFAULT 0,
    weather_label character varying(50),
    weather_effect double precision,
    total_ms double precision,
    nodes_executed integer DEFAULT 0,
    errors_count integer DEFAULT 0,
    status character varying(20) DEFAULT 'completed'::character varying,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: cycle_logs; Type: VIEW; Schema: monitoring; Owner: postgres
--

CREATE VIEW monitoring.cycle_logs AS
 SELECT cycle_id,
    store_id,
    triggered_by,
    urgency_level,
    urgency_score,
    gap_pct,
    gap_amount,
    ca_today,
    ca_target,
    forecast_eod,
    analyst_summary,
    strategie,
    nb_actions,
    cause_racine,
    rag_used,
    nb_rag_scripts,
    weather_label,
    weather_effect,
    total_ms,
    nodes_executed,
    errors_count,
    status,
    created_at
   FROM public.agent_cycles
  ORDER BY created_at DESC;



--
-- Name: realtime_store_pulse; Type: VIEW; Schema: monitoring; Owner: postgres
--

CREATE VIEW monitoring.realtime_store_pulse AS
 SELECT b.store_id,
    b.store_name,
    b.region,
    COALESCE(tx.ca_today, (0)::numeric) AS ca_today,
    COALESCE(tx.nb_tx, (0)::bigint) AS nb_transactions,
    COALESCE(st.ruptures, (0)::bigint) AS nb_ruptures,
    COALESCE(st.critiques, (0)::bigint) AS nb_critiques,
    now() AS pulse_at
   FROM ((sales.boutiques b
     LEFT JOIN ( SELECT transactions_rt.store_id,
            sum(transactions_rt.lig_ttc) AS ca_today,
            count(*) AS nb_tx
           FROM sales.transactions_rt
          WHERE (transactions_rt.date_only = CURRENT_DATE)
          GROUP BY transactions_rt.store_id) tx ON ((tx.store_id = b.store_id)))
     LEFT JOIN ( SELECT stock_levels.store_id,
            sum(
                CASE
                    WHEN (COALESCE(stock_levels.quantity_available, stock_levels.quantity, 0) <= 0) THEN 1
                    ELSE 0
                END) AS ruptures,
            sum(
                CASE
                    WHEN ((COALESCE(stock_levels.quantity_available, stock_levels.quantity, 0) >= 1) AND (COALESCE(stock_levels.quantity_available, stock_levels.quantity, 0) <= 5)) THEN 1
                    ELSE 0
                END) AS critiques
           FROM inventory.stock_levels
          GROUP BY stock_levels.store_id) st ON ((st.store_id = b.store_id)));



--
-- Name: VIEW realtime_store_pulse; Type: COMMENT; Schema: monitoring; Owner: postgres
--

COMMENT ON VIEW monitoring.realtime_store_pulse IS 'Vue temps rÃ©el consolidÃ©e par boutique. AgrÃ¨ge CA/objectif/contexte/stock en 1 requÃªte. UtilisÃ©e par le SupervisorAgent et le dashboard Angular pour le KPI board.';


--
-- Name: agent_cycles_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.agent_cycles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: agent_cycles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.agent_cycles_id_seq OWNED BY public.agent_cycles.id;


--
-- Name: agent_errors; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.agent_errors (
    id integer NOT NULL,
    cycle_id character varying(50),
    store_id character varying(10) DEFAULT 'I63'::character varying,
    agent_name character varying(30),
    node_name character varying(50),
    error_type character varying(50),
    error_msg text,
    traceback_txt text,
    context jsonb,
    resolved boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: agent_errors_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.agent_errors_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: agent_errors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.agent_errors_id_seq OWNED BY public.agent_errors.id;


--
-- Name: agent_kpi_daily; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.agent_kpi_daily (
    id bigint NOT NULL,
    agent_id integer NOT NULL,
    store_id character varying(50) NOT NULL,
    kpi_date date NOT NULL,
    ca_realise numeric(14,2) DEFAULT 0,
    ca_cible numeric(14,2),
    gap_ca_pct numeric(7,2),
    nb_transactions integer DEFAULT 0,
    nb_clients_uniques integer DEFAULT 0,
    panier_moyen numeric(10,2),
    nb_forfaits integer DEFAULT 0,
    nb_terminaux integer DEFAULT 0,
    nb_sim_activations integer DEFAULT 0,
    nb_recharges integer DEFAULT 0,
    nb_accessoires integer DEFAULT 0,
    nb_evouchers integer DEFAULT 0,
    nb_postpaye integer DEFAULT 0,
    nb_postpaye_cible integer,
    gap_postpaye_pct numeric(7,2),
    taux_upsell_accessoire numeric(6,4),
    taux_upsell_assurance numeric(6,4),
    taux_conversion_recharge numeric(6,4),
    ca_terminaux numeric(12,2) DEFAULT 0,
    ca_forfaits numeric(12,2) DEFAULT 0,
    ca_sim numeric(12,2) DEFAULT 0,
    ca_recharges numeric(12,2) DEFAULT 0,
    ca_accessoires numeric(12,2) DEFAULT 0,
    rang_boutique integer,
    rang_region integer,
    rang_national integer,
    nb_reclamations integer DEFAULT 0,
    nps_score numeric(5,2),
    coach_score numeric(5,2),
    urgency_level character varying(10),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT agent_kpi_daily_urgency_level_check CHECK (((urgency_level)::text = ANY ((ARRAY['CRITIQUE'::character varying, 'ELEVE'::character varying, 'MODERE'::character varying, 'OK'::character varying])::text[])))
);



--
-- Name: agent_kpi_daily_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.agent_kpi_daily_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: agent_kpi_daily_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.agent_kpi_daily_id_seq OWNED BY public.agent_kpi_daily.id;


--
-- Name: agent_logs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.agent_logs (
    id integer NOT NULL,
    cycle_id character varying(50),
    store_id character varying(10) DEFAULT 'I63'::character varying,
    agent_name character varying(30),
    node_name character varying(50),
    status character varying(20),
    input_state jsonb,
    output_state jsonb,
    duration_ms double precision,
    error_msg text,
    metadata jsonb,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: agent_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.agent_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: agent_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.agent_logs_id_seq OWNED BY public.agent_logs.id;


--
-- Name: agent_memory; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.agent_memory (
    id integer NOT NULL,
    agent_name character varying(50) NOT NULL,
    store_id character varying(50) NOT NULL,
    cycle_id character varying(100),
    memory_type character varying(50) NOT NULL,
    memory_data jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: agent_memory_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.agent_memory_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: agent_memory_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.agent_memory_id_seq OWNED BY public.agent_memory.id;


--
-- Name: agent_sessions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.agent_sessions (
    id character varying(50) NOT NULL,
    store_id character varying(20) NOT NULL,
    agent_type character varying(30),
    started_at timestamp with time zone DEFAULT now(),
    last_activity timestamp with time zone DEFAULT now(),
    status character varying(20),
    memory_state json,
    external_context json
);







--
-- Name: app_sessions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.app_sessions (
    id integer NOT NULL,
    token character varying(64) NOT NULL,
    user_id character varying(30) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    last_used timestamp without time zone DEFAULT now(),
    ip_address character varying(45)
);



--
-- Name: app_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.app_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: app_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.app_sessions_id_seq OWNED BY public.app_sessions.id;


--
-- Name: app_users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.app_users (
    id integer NOT NULL,
    user_id character varying(30) NOT NULL,
    username character varying(50) NOT NULL,
    password_hash character varying(64) NOT NULL,
    full_name character varying(100),
    role character varying(20) NOT NULL,
    store_id character varying(20),
    store_name character varying(100),
    initials character varying(5),
    color character varying(10),
    advisor_id character varying(20),
    actif boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now(),
    last_login timestamp without time zone
);



--
-- Name: app_users_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.app_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: app_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.app_users_id_seq OWNED BY public.app_users.id;


--
-- Name: coach_interactions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.coach_interactions (
    id integer NOT NULL,
    advisor_name character varying(100),
    store_id character varying(20) DEFAULT 'I63'::character varying,
    message text,
    response text,
    gap_pct double precision,
    urgency character varying(10),
    rag_used boolean DEFAULT false,
    nb_rag_scripts integer DEFAULT 0,
    conseil_type character varying(30) DEFAULT 'general'::character varying,
    confidence double precision DEFAULT 0.0,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: coach_interactions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.coach_interactions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: coach_interactions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.coach_interactions_id_seq OWNED BY public.coach_interactions.id;


--
-- Name: hitl_reviews; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.hitl_reviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    store_id text NOT NULL,
    cycle_id text NOT NULL,
    urgency_level text NOT NULL,
    gap_pct double precision NOT NULL,
    critique_score double precision NOT NULL,
    critique_feedback text NOT NULL,
    strategie_summary text NOT NULL,
    actions jsonb DEFAULT '[]'::jsonb NOT NULL,
    source text DEFAULT 'sales'::text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    approver_name text,
    approver_note text,
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);



--
-- Name: produits; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.produits AS
 SELECT sku,
    nom,
    categorie,
    famille,
    prix_ht,
    prix_ttc,
    marge_pct,
    stock_initial,
    actif,
    created_at
   FROM sales.produits;



--
-- Name: rag_feedback; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.rag_feedback (
    id integer NOT NULL,
    cycle_id character varying(50),
    store_id character varying(10) DEFAULT 'I63'::character varying,
    agent_name character varying(30) DEFAULT 'stratege'::character varying,
    query text,
    nb_results integer,
    top_category character varying(100),
    top_score double precision,
    action_used text,
    was_useful boolean,
    context jsonb,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: rag_feedback_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.rag_feedback_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: rag_feedback_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.rag_feedback_id_seq OWNED BY public.rag_feedback.id;


--
-- Name: rag_feedback_metrics; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.rag_feedback_metrics (
    id integer NOT NULL,
    cycle_id character varying,
    store_id character varying(20),
    query text,
    nb_results integer,
    top_category character varying(50),
    top_score double precision,
    action_used text,
    was_useful boolean,
    context json,
    created_at timestamp with time zone DEFAULT now()
);



--
-- Name: rag_feedback_metrics_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.rag_feedback_metrics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: rag_feedback_metrics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.rag_feedback_metrics_id_seq OWNED BY public.rag_feedback_metrics.id;


--
-- Name: rag_queries; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.rag_queries (
    id integer NOT NULL,
    cycle_id character varying,
    session_id character varying(50),
    query_text text,
    nb_results integer,
    top_result_score double precision,
    top_result_category character varying(50),
    action_selected text,
    was_useful boolean,
    created_at timestamp with time zone DEFAULT now()
);



--
-- Name: rag_queries_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.rag_queries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: rag_queries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.rag_queries_id_seq OWNED BY public.rag_queries.id;


--
-- Name: store_kpi_daily; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.store_kpi_daily (
    id bigint NOT NULL,
    store_id character varying(50) NOT NULL,
    kpi_date date NOT NULL,
    ca_realise numeric(14,2) DEFAULT 0,
    ca_cible numeric(14,2),
    gap_ca_pct numeric(7,2),
    ca_cumul_mois numeric(16,2),
    ca_objectif_mois numeric(16,2),
    nb_transactions integer DEFAULT 0,
    nb_clients integer DEFAULT 0,
    taux_conversion numeric(6,4),
    footfall_estime integer,
    nb_forfaits integer DEFAULT 0,
    nb_terminaux integer DEFAULT 0,
    nb_sim_activations integer DEFAULT 0,
    nb_recharges integer DEFAULT 0,
    ca_terminaux numeric(14,2) DEFAULT 0,
    ca_forfaits numeric(14,2) DEFAULT 0,
    nb_postpaye integer DEFAULT 0,
    nb_postpaye_cible integer,
    gap_postpaye_pct numeric(7,2),
    nps_score numeric(5,2),
    csat_score numeric(5,2),
    nb_reclamations integer DEFAULT 0,
    nb_ruptures_sku integer DEFAULT 0,
    taux_service_stock numeric(6,4) DEFAULT 1.0,
    rang_region integer,
    rang_national integer,
    nb_agents_actifs integer,
    ca_par_agent numeric(12,2),
    panier_moyen_store numeric(10,2),
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: store_kpi_daily_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.store_kpi_daily_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: store_kpi_daily_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.store_kpi_daily_id_seq OWNED BY public.store_kpi_daily.id;


--
-- Name: telco_targets_monthly; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.telco_targets_monthly (
    id bigint NOT NULL,
    store_id character varying(50) NOT NULL,
    agent_id integer,
    mois integer NOT NULL,
    annee integer NOT NULL,
    niveau character varying(10) DEFAULT 'AGENT'::character varying,
    ca_cible_mensuel numeric(14,2),
    ca_cible_s1 numeric(12,2),
    ca_cible_s2 numeric(12,2),
    ca_cible_s3 numeric(12,2),
    ca_cible_s4 numeric(12,2),
    activations_totales integer DEFAULT 0,
    activations_postpaye integer DEFAULT 0,
    activations_prepaye integer DEFAULT 0,
    ventes_terminaux integer DEFAULT 0,
    upgrades_data integer DEFAULT 0,
    conversions_recharge_forfait integer DEFAULT 0,
    renouvellements_contrat integer DEFAULT 0,
    nps_cible numeric(5,2),
    taux_reclamation_max numeric(6,4),
    evenements_mois jsonb,
    facteur_saisonnier numeric(6,4) DEFAULT 1.0,
    ajustement_raison text,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT telco_targets_monthly_mois_check CHECK (((mois >= 1) AND (mois <= 12))),
    CONSTRAINT telco_targets_monthly_niveau_check CHECK (((niveau)::text = ANY ((ARRAY['AGENT'::character varying, 'BOUTIQUE'::character varying, 'REGION'::character varying])::text[])))
);



--
-- Name: telco_targets_monthly_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.telco_targets_monthly_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: telco_targets_monthly_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.telco_targets_monthly_id_seq OWNED BY public.telco_targets_monthly.id;


--
-- Name: weekly_kpi_summary; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.weekly_kpi_summary (
    id bigint NOT NULL,
    store_id character varying(50) NOT NULL,
    agent_id integer,
    annee_semaine character varying(8) NOT NULL,
    semaine_debut date NOT NULL,
    semaine_fin date NOT NULL,
    niveau character varying(10) DEFAULT 'AGENT'::character varying,
    ca_semaine numeric(14,2) DEFAULT 0,
    ca_cible_semaine numeric(14,2),
    gap_semaine_pct numeric(7,2),
    nb_transactions integer DEFAULT 0,
    nb_postpaye integer DEFAULT 0,
    nb_terminaux integer DEFAULT 0,
    nb_forfaits integer DEFAULT 0,
    panier_moyen numeric(10,2),
    top_produit character varying(200),
    top_categorie character varying(50),
    nb_jours_actifs integer DEFAULT 6,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT weekly_kpi_summary_niveau_check CHECK (((niveau)::text = ANY ((ARRAY['AGENT'::character varying, 'BOUTIQUE'::character varying])::text[])))
);



--
-- Name: weekly_kpi_summary_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.weekly_kpi_summary_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: weekly_kpi_summary_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.weekly_kpi_summary_id_seq OWNED BY public.weekly_kpi_summary.id;


--
-- Name: agents; Type: TABLE; Schema: sales; Owner: postgres
--

CREATE TABLE sales.agents (
    agent_id integer NOT NULL,
    agent_name text NOT NULL,
    store_id text NOT NULL,
    role text,
    phone text,
    email text,
    performance_level text,
    created_at timestamp without time zone DEFAULT now(),
    date_embauche date,
    date_depart date,
    niveau_certification integer DEFAULT 1,
    quota_mensuel_ca numeric(12,2),
    quota_activations integer DEFAULT 60,
    quota_postpaye integer DEFAULT 10,
    specialisation character varying(50),
    avatar_color character varying(7),
    coach_score numeric(5,2) DEFAULT 0.0,
    anciennete_mois integer DEFAULT 12
);



--
-- Name: coaching_scripts; Type: TABLE; Schema: sales; Owner: postgres
--

CREATE TABLE sales.coaching_scripts (
    id integer NOT NULL,
    store_id character varying(10) DEFAULT 'I63'::character varying,
    categorie character varying(50),
    situation text,
    action text,
    produit_cible character varying(100),
    argument_vente text,
    impact_observe character varying(100),
    heure_min integer,
    heure_max integer,
    jour_semaine integer,
    source character varying(100),
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: coaching_scripts_id_seq; Type: SEQUENCE; Schema: sales; Owner: postgres
--

CREATE SEQUENCE sales.coaching_scripts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: coaching_scripts_id_seq; Type: SEQUENCE OWNED BY; Schema: sales; Owner: postgres
--

ALTER SEQUENCE sales.coaching_scripts_id_seq OWNED BY sales.coaching_scripts.id;


--
-- Name: objectifs; Type: TABLE; Schema: sales; Owner: postgres
--

CREATE TABLE sales.objectifs (
    id integer NOT NULL,
    store_id text NOT NULL,
    agent_id integer,
    date_objectif date NOT NULL,
    objectif_ca numeric(10,2),
    objectif_transactions integer,
    objectif_panier_moyen numeric(10,2),
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: objectifs_id_seq; Type: SEQUENCE; Schema: sales; Owner: postgres
--

CREATE SEQUENCE sales.objectifs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: objectifs_id_seq; Type: SEQUENCE OWNED BY; Schema: sales; Owner: postgres
--

ALTER SEQUENCE sales.objectifs_id_seq OWNED BY sales.objectifs.id;


--
-- Name: transactions; Type: TABLE; Schema: sales; Owner: postgres
--

CREATE TABLE sales.transactions (
    id bigint NOT NULL,
    transaction_date timestamp without time zone NOT NULL,
    date_only date NOT NULL,
    heure integer NOT NULL,
    store_id text NOT NULL,
    agent_id integer NOT NULL,
    sku integer NOT NULL,
    quantity integer DEFAULT 1,
    prix_unitaire numeric(10,2),
    lig_ht numeric(10,2),
    lig_ttc numeric(10,2),
    marge numeric(10,2),
    payment_method text,
    created_at timestamp without time zone DEFAULT now()
);



--
-- Name: transactions_history; Type: VIEW; Schema: sales; Owner: postgres
--

CREATE VIEW sales.transactions_history AS
 SELECT (transactions.id)::text AS id,
    transactions.transaction_date,
    transactions.date_only,
    transactions.heure,
    transactions.store_id,
    transactions.agent_id,
    transactions.sku AS cod_prod,
    NULL::text AS des_produit,
    transactions.quantity AS qte_produit,
    transactions.prix_unitaire,
    transactions.lig_ht,
    transactions.lig_ttc,
    transactions.marge,
    transactions.payment_method,
    transactions.created_at,
    'historical'::text AS source
   FROM sales.transactions
UNION ALL
 SELECT (transactions_rt.sale_id)::text AS id,
    transactions_rt.date_vente AS transaction_date,
    transactions_rt.date_only,
    transactions_rt.heure,
    transactions_rt.store_id,
    transactions_rt.agent_id,
    transactions_rt.cod_prod,
    transactions_rt.des_produit,
    transactions_rt.qte_produit,
    NULL::numeric(10,2) AS prix_unitaire,
    transactions_rt.lig_ht,
    transactions_rt.lig_ttc,
    NULL::numeric(10,2) AS marge,
    NULL::text AS payment_method,
    transactions_rt.created_at,
    'realtime'::text AS source
   FROM sales.transactions_rt;



--
-- Name: VIEW transactions_history; Type: COMMENT; Schema: sales; Owner: postgres
--

COMMENT ON VIEW sales.transactions_history IS 'Vue unifiÃ©e transactions historiques + temps rÃ©el (UNION ALL). Lecture seule.';


--
-- Name: transactions_id_seq; Type: SEQUENCE; Schema: sales; Owner: postgres
--

CREATE SEQUENCE sales.transactions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;



--
-- Name: transactions_id_seq; Type: SEQUENCE OWNED BY; Schema: sales; Owner: postgres
--

ALTER SEQUENCE sales.transactions_id_seq OWNED BY sales.transactions.id;


--
-- Name: vw_ca_par_boutique; Type: VIEW; Schema: sales; Owner: postgres
--

CREATE VIEW sales.vw_ca_par_boutique AS
 SELECT store_id,
    date_only,
    sum(lig_ttc) AS ca_total,
    count(*) AS nb_transactions,
    avg(lig_ttc) AS avg_ticket
   FROM sales.transactions
  WHERE (lig_ttc > (0)::numeric)
  GROUP BY store_id, date_only;



--
-- Name: vw_performance_agent; Type: VIEW; Schema: sales; Owner: postgres
--

CREATE VIEW sales.vw_performance_agent AS
 SELECT t.store_id,
    t.agent_id,
    a.agent_name,
    a.performance_level,
    sum(t.lig_ttc) AS ca_30j,
    count(DISTINCT t.date_only) AS jours_actifs,
    count(*) AS nb_transactions,
    round((sum(t.lig_ttc) / (NULLIF(count(DISTINCT t.date_only), 0))::numeric), 2) AS ca_par_jour
   FROM (sales.transactions t
     LEFT JOIN sales.agents a ON ((a.agent_id = t.agent_id)))
  WHERE ((t.date_only >= (CURRENT_DATE - '30 days'::interval)) AND (t.lig_ttc > (0)::numeric))
  GROUP BY t.store_id, t.agent_id, a.agent_name, a.performance_level;



--
-- Name: vw_stock_enriched; Type: VIEW; Schema: sales; Owner: postgres
--

CREATE VIEW sales.vw_stock_enriched AS
 SELECT sl.store_id,
    sl.sku,
    COALESCE(p.nom, (sl.sku)::text) AS product_name,
    p.categorie,
    p.famille AS gamme_libelle,
    COALESCE(p.prix_ttc, (0)::numeric) AS prix_ttc,
    COALESCE(p.marge_pct, (0)::numeric) AS marge_pct,
    p.flag_terminal,
    p.flag_forfait,
    COALESCE(sl.quantity_available, sl.quantity, 0) AS stock_dispo,
    COALESCE(sl.quantity_reserved, 0) AS stock_in_transit,
    sl.last_updated,
        CASE
            WHEN (COALESCE(sl.quantity_available, sl.quantity, 0) = 0) THEN 'rupture'::text
            WHEN (COALESCE(sl.quantity_available, sl.quantity, 0) <= 3) THEN 'critical'::text
            WHEN (COALESCE(sl.quantity_available, sl.quantity, 0) <= 10) THEN 'warning'::text
            ELSE 'ok'::text
        END AS stock_risk
   FROM (inventory.stock_levels sl
     LEFT JOIN sales.produits p ON ((p.sku = sl.sku)));



--
-- Name: vw_top_products; Type: VIEW; Schema: sales; Owner: postgres
--

CREATE VIEW sales.vw_top_products AS
 SELECT t.store_id,
    t.sku,
    p.nom AS product_name,
    p.categorie,
    p.prix_ttc,
    sum(t.lig_ttc) AS ca_30j,
    sum(t.quantity) AS qty_30j,
    count(DISTINCT t.date_only) AS nb_jours_vendus
   FROM (sales.transactions t
     LEFT JOIN sales.produits p ON ((p.sku = t.sku)))
  WHERE ((t.date_only >= (CURRENT_DATE - '30 days'::interval)) AND (t.lig_ttc > (0)::numeric))
  GROUP BY t.store_id, t.sku, p.nom, p.categorie, p.prix_ttc;



--
-- Name: vw_ventes_par_agent; Type: VIEW; Schema: sales; Owner: postgres
--

CREATE VIEW sales.vw_ventes_par_agent AS
 SELECT agent_id,
    store_id,
    date(transaction_date) AS date_vente,
    sum(lig_ttc) AS ca,
    count(*) AS nb_transactions,
    count(DISTINCT sku) AS nb_produits,
    avg(lig_ttc) AS ticket_moyen
   FROM sales.transactions
  GROUP BY agent_id, store_id, (date(transaction_date));



--
-- Name: purchase_orders; Type: TABLE; Schema: supply; Owner: postgres
--

CREATE TABLE supply.purchase_orders (
    po_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    sku integer NOT NULL,
    supplier_id character varying(30),
    store_id character varying(50),
    quantite_commandee integer NOT NULL,
    quantite_recue integer DEFAULT 0,
    prix_unitaire_ht numeric(12,4),
    montant_total_ht numeric(14,4),
    devise character varying(5) DEFAULT 'TND'::character varying,
    statut character varying(20) DEFAULT 'BROUILLON'::character varying,
    priorite character varying(10) DEFAULT 'NORMAL'::character varying,
    date_commande timestamp without time zone DEFAULT now(),
    date_livraison_prevue date,
    date_livraison_reelle date,
    delai_reel_jours integer,
    livraison_conforme boolean,
    reference_externe character varying(100),
    notes text,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    recommendation_id uuid,
    source character varying(10) DEFAULT 'MANUEL'::character varying NOT NULL,
    urgency character varying(20),
    confidence numeric(5,4),
    agent_decision_id uuid,
    CONSTRAINT purchase_orders_source_check CHECK (((source)::text = ANY ((ARRAY['AGENT'::character varying, 'MANUEL'::character varying])::text[]))),
    CONSTRAINT purchase_orders_statut_check CHECK (((statut)::text = ANY ((ARRAY['SUGGERE'::character varying, 'BROUILLON'::character varying, 'SOUMIS'::character varying, 'CONFIRME'::character varying, 'EXPEDIE'::character varying, 'RECU_PARTIEL'::character varying, 'RECU'::character varying, 'ANNULE'::character varying, 'LITIGE'::character varying])::text[])))
);



--
-- Name: reorder_params; Type: TABLE; Schema: supply; Owner: postgres
--

CREATE TABLE supply.reorder_params (
    sku integer NOT NULL,
    store_id character varying(50) NOT NULL,
    demande_moy_jour numeric(10,4),
    demande_std_jour numeric(10,4),
    lead_time_moy numeric(8,2),
    lead_time_std numeric(8,2),
    stock_securite integer,
    point_commande integer,
    eoq integer,
    niveau_service numeric(5,4) DEFAULT 0.95,
    jours_stock_cible integer DEFAULT 30,
    derniere_maj timestamp without time zone DEFAULT now()
);



--
-- Name: serial_numbers; Type: TABLE; Schema: supply; Owner: postgres
--

CREATE TABLE supply.serial_numbers (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    num_serie character varying(50) NOT NULL,
    type_serie character varying(10) NOT NULL,
    sku integer,
    store_id character varying(50),
    po_id uuid,
    statut character varying(20) DEFAULT 'EN_STOCK'::character varying,
    date_reception date,
    date_vente date,
    sale_id character varying(100),
    num_client character varying(30),
    notes text,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT serial_numbers_statut_check CHECK (((statut)::text = ANY ((ARRAY['EN_STOCK'::character varying, 'VENDU'::character varying, 'RESERVE'::character varying, 'DEFECTUEUX'::character varying, 'RETOURNE'::character varying, 'VOLE'::character varying, 'EN_TRANSIT'::character varying])::text[]))),
    CONSTRAINT serial_numbers_type_serie_check CHECK (((type_serie)::text = ANY ((ARRAY['IMEI'::character varying, 'ICCID'::character varying, 'ESIM'::character varying, 'EAN'::character varying])::text[])))
);



--
-- Name: stock_movements; Type: TABLE; Schema: supply; Owner: postgres
--

CREATE TABLE supply.stock_movements (
    mouvement_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    sku integer NOT NULL,
    store_id character varying(50) NOT NULL,
    type_mouvement character varying(30) NOT NULL,
    quantite integer NOT NULL,
    stock_avant integer,
    stock_apres integer,
    reference_id character varying(100),
    reference_type character varying(30),
    agent_id integer,
    date_mouvement timestamp without time zone DEFAULT now(),
    notes text,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT stock_movements_type_mouvement_check CHECK (((type_mouvement)::text = ANY ((ARRAY['RECEPTION_BC'::character varying, 'VENTE'::character varying, 'RETOUR_CLIENT'::character varying, 'RETOUR_FOURNISSEUR'::character varying, 'TRANSFERT_ENTRANT'::character varying, 'TRANSFERT_SORTANT'::character varying, 'AJUSTEMENT_INVENTAIRE'::character varying, 'CASSE_PERTE'::character varying, 'INVENTAIRE_GAIN'::character varying, 'INVENTAIRE_PERTE'::character varying, 'RESERVATION'::character varying, 'LIBERATION_RESERVATION'::character varying])::text[])))
);



--
-- Name: suppliers; Type: TABLE; Schema: supply; Owner: postgres
--

CREATE TABLE supply.suppliers (
    supplier_id character varying(30) NOT NULL,
    nom character varying(200) NOT NULL,
    pays_origine character varying(50),
    type_fournisseur character varying(30) NOT NULL,
    categories jsonb,
    marques jsonb,
    delai_livraison_moy integer DEFAULT 14,
    delai_livraison_std integer DEFAULT 3,
    taux_fiabilite numeric(5,4) DEFAULT 0.90,
    commande_min integer DEFAULT 1,
    commande_multiple integer DEFAULT 1,
    devise character varying(5) DEFAULT 'TND'::character varying,
    conditions_paiement character varying(100),
    contact_nom character varying(100),
    contact_email character varying(200),
    actif boolean DEFAULT true,
    score_global numeric(5,2) DEFAULT 0.0,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);



--
-- Name: transfers; Type: TABLE; Schema: supply; Owner: postgres
--

CREATE TABLE supply.transfers (
    transfer_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    sku integer NOT NULL,
    store_source character varying(50) NOT NULL,
    store_dest character varying(50) NOT NULL,
    quantite integer NOT NULL,
    statut character varying(20) DEFAULT 'DEMANDE'::character varying,
    priorite character varying(10) DEFAULT 'NORMAL'::character varying,
    motif character varying(100),
    date_demande timestamp without time zone DEFAULT now(),
    date_approbation timestamp without time zone,
    date_expedition timestamp without time zone,
    date_reception timestamp without time zone,
    notes text,
    CONSTRAINT transfers_statut_check CHECK (((statut)::text = ANY ((ARRAY['DEMANDE'::character varying, 'APPROUVE'::character varying, 'EXPEDIE'::character varying, 'RECU'::character varying, 'REJETE'::character varying, 'ANNULE'::character varying, 'EN_LITIGE'::character varying])::text[])))
);



--
-- Name: nps_csat id; Type: DEFAULT; Schema: customer; Owner: postgres
--

ALTER TABLE ONLY customer.nps_csat ALTER COLUMN id SET DEFAULT nextval('customer.nps_csat_id_seq'::regclass);


--
-- Name: agent_runs id; Type: DEFAULT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.agent_runs ALTER COLUMN id SET DEFAULT nextval('inventory.agent_runs_id_seq'::regclass);


--
-- Name: alerts id; Type: DEFAULT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.alerts ALTER COLUMN id SET DEFAULT nextval('inventory.alerts_id_seq'::regclass);


--
-- Name: business_objectives id; Type: DEFAULT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.business_objectives ALTER COLUMN id SET DEFAULT nextval('inventory.business_objectives_id_seq'::regclass);


--
-- Name: context_adjustments id; Type: DEFAULT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.context_adjustments ALTER COLUMN id SET DEFAULT nextval('inventory.context_adjustments_id_seq'::regclass);


--
-- Name: demand_forecast id; Type: DEFAULT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.demand_forecast ALTER COLUMN id SET DEFAULT nextval('inventory.demand_forecast_id_seq'::regclass);


--
-- Name: events id; Type: DEFAULT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.events ALTER COLUMN id SET DEFAULT nextval('inventory.events_id_seq'::regclass);


--
-- Name: promotions id; Type: DEFAULT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.promotions ALTER COLUMN id SET DEFAULT nextval('inventory.promotions_id_seq'::regclass);


--
-- Name: sales_history id; Type: DEFAULT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.sales_history ALTER COLUMN id SET DEFAULT nextval('inventory.sales_history_id_seq'::regclass);


--
-- Name: stock_history id; Type: DEFAULT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.stock_history ALTER COLUMN id SET DEFAULT nextval('inventory.stock_history_id_seq'::regclass);


--
-- Name: stock_levels id; Type: DEFAULT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.stock_levels ALTER COLUMN id SET DEFAULT nextval('inventory.stock_levels_id_seq'::regclass);


--
-- Name: competitor_pricing id; Type: DEFAULT; Schema: market; Owner: postgres
--

ALTER TABLE ONLY market.competitor_pricing ALTER COLUMN id SET DEFAULT nextval('market.competitor_pricing_id_seq'::regclass);


--
-- Name: seasonal_patterns id; Type: DEFAULT; Schema: market; Owner: postgres
--

ALTER TABLE ONLY market.seasonal_patterns ALTER COLUMN id SET DEFAULT nextval('market.seasonal_patterns_id_seq'::regclass);


--
-- Name: agent_cycles id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_cycles ALTER COLUMN id SET DEFAULT nextval('public.agent_cycles_id_seq'::regclass);


--
-- Name: agent_errors id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_errors ALTER COLUMN id SET DEFAULT nextval('public.agent_errors_id_seq'::regclass);


--
-- Name: agent_kpi_daily id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_kpi_daily ALTER COLUMN id SET DEFAULT nextval('public.agent_kpi_daily_id_seq'::regclass);


--
-- Name: agent_logs id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_logs ALTER COLUMN id SET DEFAULT nextval('public.agent_logs_id_seq'::regclass);


--
-- Name: agent_memory id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_memory ALTER COLUMN id SET DEFAULT nextval('public.agent_memory_id_seq'::regclass);


--
-- Name: app_sessions id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_sessions ALTER COLUMN id SET DEFAULT nextval('public.app_sessions_id_seq'::regclass);


--
-- Name: app_users id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_users ALTER COLUMN id SET DEFAULT nextval('public.app_users_id_seq'::regclass);


--
-- Name: coach_interactions id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.coach_interactions ALTER COLUMN id SET DEFAULT nextval('public.coach_interactions_id_seq'::regclass);


--
-- Name: rag_feedback id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rag_feedback ALTER COLUMN id SET DEFAULT nextval('public.rag_feedback_id_seq'::regclass);


--
-- Name: rag_feedback_metrics id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rag_feedback_metrics ALTER COLUMN id SET DEFAULT nextval('public.rag_feedback_metrics_id_seq'::regclass);


--
-- Name: rag_queries id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rag_queries ALTER COLUMN id SET DEFAULT nextval('public.rag_queries_id_seq'::regclass);


--
-- Name: store_kpi_daily id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.store_kpi_daily ALTER COLUMN id SET DEFAULT nextval('public.store_kpi_daily_id_seq'::regclass);


--
-- Name: telco_targets_monthly id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.telco_targets_monthly ALTER COLUMN id SET DEFAULT nextval('public.telco_targets_monthly_id_seq'::regclass);


--
-- Name: weekly_kpi_summary id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.weekly_kpi_summary ALTER COLUMN id SET DEFAULT nextval('public.weekly_kpi_summary_id_seq'::regclass);


--
-- Name: coaching_scripts id; Type: DEFAULT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.coaching_scripts ALTER COLUMN id SET DEFAULT nextval('sales.coaching_scripts_id_seq'::regclass);


--
-- Name: objectifs id; Type: DEFAULT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.objectifs ALTER COLUMN id SET DEFAULT nextval('sales.objectifs_id_seq'::regclass);


--
-- Name: transactions id; Type: DEFAULT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.transactions ALTER COLUMN id SET DEFAULT nextval('sales.transactions_id_seq'::regclass);


--
-- Name: coaching_events coaching_events_pkey; Type: CONSTRAINT; Schema: coaching; Owner: postgres
--

ALTER TABLE ONLY coaching.coaching_events
    ADD CONSTRAINT coaching_events_pkey PRIMARY KEY (id);


--
-- Name: nps_csat nps_csat_pkey; Type: CONSTRAINT; Schema: customer; Owner: postgres
--

ALTER TABLE ONLY customer.nps_csat
    ADD CONSTRAINT nps_csat_pkey PRIMARY KEY (id);


--
-- Name: segments segments_pkey; Type: CONSTRAINT; Schema: customer; Owner: postgres
--

ALTER TABLE ONLY customer.segments
    ADD CONSTRAINT segments_pkey PRIMARY KEY (segment_id);


--
-- Name: agent_runs agent_runs_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (id);


--
-- Name: alerts alerts_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (id);


--
-- Name: business_objectives business_objectives_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.business_objectives
    ADD CONSTRAINT business_objectives_pkey PRIMARY KEY (id);


--
-- Name: context_adjustments context_adjustments_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.context_adjustments
    ADD CONSTRAINT context_adjustments_pkey PRIMARY KEY (id);


--
-- Name: context_adjustments ctx_adj_unique; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.context_adjustments
    ADD CONSTRAINT ctx_adj_unique UNIQUE (sku, store_id, valid_from);


--
-- Name: demand_forecast demand_forecast_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.demand_forecast
    ADD CONSTRAINT demand_forecast_pkey PRIMARY KEY (id);


--
-- Name: demand_forecast demand_forecast_sku_store_id_forecast_date_key; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.demand_forecast
    ADD CONSTRAINT demand_forecast_sku_store_id_forecast_date_key UNIQUE (sku, store_id, forecast_date);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: product_master product_master_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.product_master
    ADD CONSTRAINT product_master_pkey PRIMARY KEY (sku);


--
-- Name: promotions promotions_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.promotions
    ADD CONSTRAINT promotions_pkey PRIMARY KEY (id);


--
-- Name: promotions promotions_promo_id_key; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.promotions
    ADD CONSTRAINT promotions_promo_id_key UNIQUE (promo_id);


--
-- Name: recommendations recommendations_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.recommendations
    ADD CONSTRAINT recommendations_pkey PRIMARY KEY (id);


--
-- Name: sales_history sales_history_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.sales_history
    ADD CONSTRAINT sales_history_pkey PRIMARY KEY (id);


--
-- Name: stock_history stock_history_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.stock_history
    ADD CONSTRAINT stock_history_pkey PRIMARY KEY (id);


--
-- Name: stock_levels stock_levels_pkey; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.stock_levels
    ADD CONSTRAINT stock_levels_pkey PRIMARY KEY (id);


--
-- Name: stock_levels stock_levels_sku_store; Type: CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.stock_levels
    ADD CONSTRAINT stock_levels_sku_store UNIQUE (sku, store_id);


--
-- Name: competitor_pricing competitor_pricing_pkey; Type: CONSTRAINT; Schema: market; Owner: postgres
--

ALTER TABLE ONLY market.competitor_pricing
    ADD CONSTRAINT competitor_pricing_pkey PRIMARY KEY (id);


--
-- Name: competitors competitors_pkey; Type: CONSTRAINT; Schema: market; Owner: postgres
--

ALTER TABLE ONLY market.competitors
    ADD CONSTRAINT competitors_pkey PRIMARY KEY (concurrent_id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: market; Owner: postgres
--

ALTER TABLE ONLY market.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (event_id);


--
-- Name: mnp_flows mnp_flows_pkey; Type: CONSTRAINT; Schema: market; Owner: postgres
--

ALTER TABLE ONLY market.mnp_flows
    ADD CONSTRAINT mnp_flows_pkey PRIMARY KEY (mnp_id);


--
-- Name: seasonal_patterns seasonal_patterns_pkey; Type: CONSTRAINT; Schema: market; Owner: postgres
--

ALTER TABLE ONLY market.seasonal_patterns
    ADD CONSTRAINT seasonal_patterns_pkey PRIMARY KEY (id);


--
-- Name: agent_cycles agent_cycles_cycle_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_cycles
    ADD CONSTRAINT agent_cycles_cycle_id_key UNIQUE (cycle_id);


--
-- Name: agent_cycles agent_cycles_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_cycles
    ADD CONSTRAINT agent_cycles_pkey PRIMARY KEY (id);


--
-- Name: agent_errors agent_errors_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_errors
    ADD CONSTRAINT agent_errors_pkey PRIMARY KEY (id);


--
-- Name: agent_kpi_daily agent_kpi_daily_agent_id_kpi_date_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_kpi_daily
    ADD CONSTRAINT agent_kpi_daily_agent_id_kpi_date_key UNIQUE (agent_id, kpi_date);


--
-- Name: agent_kpi_daily agent_kpi_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_kpi_daily
    ADD CONSTRAINT agent_kpi_daily_pkey PRIMARY KEY (id);


--
-- Name: agent_logs agent_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_logs
    ADD CONSTRAINT agent_logs_pkey PRIMARY KEY (id);


--
-- Name: agent_memory agent_memory_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_memory
    ADD CONSTRAINT agent_memory_pkey PRIMARY KEY (id);


--
-- Name: agent_sessions agent_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_pkey PRIMARY KEY (id);





--
-- Name: app_sessions app_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_sessions
    ADD CONSTRAINT app_sessions_pkey PRIMARY KEY (id);


--
-- Name: app_sessions app_sessions_token_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_sessions
    ADD CONSTRAINT app_sessions_token_key UNIQUE (token);


--
-- Name: app_users app_users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_users
    ADD CONSTRAINT app_users_pkey PRIMARY KEY (id);


--
-- Name: app_users app_users_user_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_users
    ADD CONSTRAINT app_users_user_id_key UNIQUE (user_id);


--
-- Name: app_users app_users_username_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.app_users
    ADD CONSTRAINT app_users_username_key UNIQUE (username);


--
-- Name: coach_interactions coach_interactions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.coach_interactions
    ADD CONSTRAINT coach_interactions_pkey PRIMARY KEY (id);


--
-- Name: hitl_reviews hitl_reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.hitl_reviews
    ADD CONSTRAINT hitl_reviews_pkey PRIMARY KEY (id);


--
-- Name: rag_feedback_metrics rag_feedback_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rag_feedback_metrics
    ADD CONSTRAINT rag_feedback_metrics_pkey PRIMARY KEY (id);


--
-- Name: rag_feedback rag_feedback_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rag_feedback
    ADD CONSTRAINT rag_feedback_pkey PRIMARY KEY (id);


--
-- Name: rag_queries rag_queries_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rag_queries
    ADD CONSTRAINT rag_queries_pkey PRIMARY KEY (id);


--
-- Name: store_kpi_daily store_kpi_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.store_kpi_daily
    ADD CONSTRAINT store_kpi_daily_pkey PRIMARY KEY (id);


--
-- Name: store_kpi_daily store_kpi_daily_store_id_kpi_date_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.store_kpi_daily
    ADD CONSTRAINT store_kpi_daily_store_id_kpi_date_key UNIQUE (store_id, kpi_date);


--
-- Name: telco_targets_monthly telco_targets_monthly_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.telco_targets_monthly
    ADD CONSTRAINT telco_targets_monthly_pkey PRIMARY KEY (id);


--
-- Name: weekly_kpi_summary weekly_kpi_summary_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.weekly_kpi_summary
    ADD CONSTRAINT weekly_kpi_summary_pkey PRIMARY KEY (id);


--
-- Name: agents agents_pkey; Type: CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.agents
    ADD CONSTRAINT agents_pkey PRIMARY KEY (agent_id);


--
-- Name: boutiques boutiques_pkey; Type: CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.boutiques
    ADD CONSTRAINT boutiques_pkey PRIMARY KEY (store_id);


--
-- Name: coaching_scripts coaching_scripts_pkey; Type: CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.coaching_scripts
    ADD CONSTRAINT coaching_scripts_pkey PRIMARY KEY (id);


--
-- Name: objectifs objectifs_pkey; Type: CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.objectifs
    ADD CONSTRAINT objectifs_pkey PRIMARY KEY (id);


--
-- Name: produits produits_pkey; Type: CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.produits
    ADD CONSTRAINT produits_pkey PRIMARY KEY (sku);


--
-- Name: transactions transactions_pkey; Type: CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.transactions
    ADD CONSTRAINT transactions_pkey PRIMARY KEY (id);


--
-- Name: transactions_rt transactions_rt_pkey; Type: CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.transactions_rt
    ADD CONSTRAINT transactions_rt_pkey PRIMARY KEY (sale_id);


--
-- Name: purchase_orders purchase_orders_pkey; Type: CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.purchase_orders
    ADD CONSTRAINT purchase_orders_pkey PRIMARY KEY (po_id);


--
-- Name: reorder_params reorder_params_pkey; Type: CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.reorder_params
    ADD CONSTRAINT reorder_params_pkey PRIMARY KEY (sku, store_id);


--
-- Name: serial_numbers serial_numbers_num_serie_key; Type: CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.serial_numbers
    ADD CONSTRAINT serial_numbers_num_serie_key UNIQUE (num_serie);


--
-- Name: serial_numbers serial_numbers_pkey; Type: CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.serial_numbers
    ADD CONSTRAINT serial_numbers_pkey PRIMARY KEY (id);


--
-- Name: stock_movements stock_movements_pkey; Type: CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.stock_movements
    ADD CONSTRAINT stock_movements_pkey PRIMARY KEY (mouvement_id);


--
-- Name: suppliers suppliers_pkey; Type: CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.suppliers
    ADD CONSTRAINT suppliers_pkey PRIMARY KEY (supplier_id);


--
-- Name: transfers transfers_pkey; Type: CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.transfers
    ADD CONSTRAINT transfers_pkey PRIMARY KEY (transfer_id);


--
-- Name: idx_ce_advisor; Type: INDEX; Schema: coaching; Owner: postgres
--

CREATE INDEX idx_ce_advisor ON coaching.coaching_events USING btree (advisor_id, created_at DESC);


--
-- Name: idx_ce_cycle; Type: INDEX; Schema: coaching; Owner: postgres
--

CREATE INDEX idx_ce_cycle ON coaching.coaching_events USING btree (cycle_id);


--
-- Name: idx_ce_rag; Type: INDEX; Schema: coaching; Owner: postgres
--

CREATE INDEX idx_ce_rag ON coaching.coaching_events USING btree (rag_used, created_at DESC) WHERE (rag_used = true);


--
-- Name: idx_ce_store; Type: INDEX; Schema: coaching; Owner: postgres
--

CREATE INDEX idx_ce_store ON coaching.coaching_events USING btree (store_id, created_at DESC);


--
-- Name: idx_ce_urgency; Type: INDEX; Schema: coaching; Owner: postgres
--

CREATE INDEX idx_ce_urgency ON coaching.coaching_events USING btree (urgency_level, created_at DESC) WHERE ((urgency_level)::text = 'HIGH'::text);


--
-- Name: idx_nps_store_date; Type: INDEX; Schema: customer; Owner: postgres
--

CREATE INDEX idx_nps_store_date ON customer.nps_csat USING btree (store_id, feedback_date);


--
-- Name: idx_alerts_status; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_alerts_status ON inventory.alerts USING btree (status);


--
-- Name: idx_alerts_store; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_alerts_store ON inventory.alerts USING btree (store_id);


--
-- Name: idx_ctx_adj_sku_store; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_ctx_adj_sku_store ON inventory.context_adjustments USING btree (sku, store_id);


--
-- Name: idx_events_dates; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_events_dates ON inventory.events USING btree (start_date, end_date);


--
-- Name: idx_product_master_category; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_product_master_category ON inventory.product_master USING btree (category);


--
-- Name: idx_product_master_lifecycle; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_product_master_lifecycle ON inventory.product_master USING btree (lifecycle_stage);


--
-- Name: idx_promotions_dates; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_promotions_dates ON inventory.promotions USING btree (start_date, end_date);


--
-- Name: idx_promotions_sku; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_promotions_sku ON inventory.promotions USING btree (sku);


--
-- Name: idx_rec_sku_store; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_rec_sku_store ON inventory.recommendations USING btree (sku, store_id);


--
-- Name: idx_rec_status; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_rec_status ON inventory.recommendations USING btree (status);


--
-- Name: idx_recommendations_active; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_recommendations_active ON inventory.recommendations USING btree (store_id, created_at DESC) WHERE (status = ANY (ARRAY['pending'::text, 'approved'::text]));


--
-- Name: idx_recommendations_store_status; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_recommendations_store_status ON inventory.recommendations USING btree (store_id, status, created_at DESC);


--
-- Name: idx_sh_date_cat; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_sh_date_cat ON inventory.sales_history USING btree (record_date, category);


--
-- Name: idx_sh_date_store; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_sh_date_store ON inventory.stock_history USING btree (record_date, store_id);


--
-- Name: idx_sh_event; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_sh_event ON inventory.sales_history USING btree (event_type, record_date) WHERE (event_type IS NOT NULL);


--
-- Name: idx_sh_sku; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_sh_sku ON inventory.stock_history USING btree (sku);


--
-- Name: idx_sh_store_sku_date; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_sh_store_sku_date ON inventory.sales_history USING btree (store_id, sku, record_date DESC);


--
-- Name: idx_sl_store_sku; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE UNIQUE INDEX idx_sl_store_sku ON inventory.stock_levels USING btree (store_id, sku);


--
-- Name: idx_slh_date_store; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_slh_date_store ON inventory.sales_history USING btree (record_date, store_id);


--
-- Name: idx_slh_sku; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_slh_sku ON inventory.sales_history USING btree (sku);


--
-- Name: idx_stock_levels_store_qty; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_stock_levels_store_qty ON inventory.stock_levels USING btree (store_id, quantity_available);


--
-- Name: idx_stock_low_qty; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE INDEX idx_stock_low_qty ON inventory.stock_levels USING btree (store_id);


--
-- Name: uq_reco_pending_sku_store; Type: INDEX; Schema: inventory; Owner: postgres
--

CREATE UNIQUE INDEX uq_reco_pending_sku_store ON inventory.recommendations USING btree (sku, store_id) WHERE (status = 'pending'::text);


--
-- Name: idx_competitor_pricing_cat; Type: INDEX; Schema: market; Owner: postgres
--

CREATE INDEX idx_competitor_pricing_cat ON market.competitor_pricing USING btree (categorie, concurrent_id);


--
-- Name: idx_competitor_pricing_date; Type: INDEX; Schema: market; Owner: postgres
--

CREATE INDEX idx_competitor_pricing_date ON market.competitor_pricing USING btree (date_releve);


--
-- Name: idx_market_events_dates; Type: INDEX; Schema: market; Owner: postgres
--

CREATE INDEX idx_market_events_dates ON market.events USING btree (start_date, end_date);


--
-- Name: idx_market_events_type; Type: INDEX; Schema: market; Owner: postgres
--

CREATE INDEX idx_market_events_type ON market.events USING btree (event_type);


--
-- Name: idx_mnp_mois; Type: INDEX; Schema: market; Owner: postgres
--

CREATE INDEX idx_mnp_mois ON market.mnp_flows USING btree (mois, direction);


--
-- Name: idx_seasonal_unique; Type: INDEX; Schema: market; Owner: postgres
--

CREATE UNIQUE INDEX idx_seasonal_unique ON market.seasonal_patterns USING btree (categorie, mois, COALESCE(jour_semaine, '-1'::integer));


--
-- Name: idx_agent_kpi_agent_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_agent_kpi_agent_date ON public.agent_kpi_daily USING btree (agent_id, kpi_date DESC);


--
-- Name: idx_agent_kpi_gap; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_agent_kpi_gap ON public.agent_kpi_daily USING btree (kpi_date, gap_ca_pct) WHERE (gap_ca_pct < ('-15'::integer)::numeric);


--
-- Name: idx_agent_kpi_store_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_agent_kpi_store_date ON public.agent_kpi_daily USING btree (store_id, kpi_date DESC);


--
-- Name: idx_agent_memory_agent_store; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_agent_memory_agent_store ON public.agent_memory USING btree (agent_name, store_id, created_at DESC);


--
-- Name: idx_as_exp; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_as_exp ON public.app_sessions USING btree (expires_at);


--
-- Name: idx_as_token; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_as_token ON public.app_sessions USING btree (token);


--
-- Name: idx_as_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_as_user ON public.app_sessions USING btree (user_id);


--
-- Name: idx_au_store; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_au_store ON public.app_users USING btree (store_id);


--
-- Name: idx_au_username; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_au_username ON public.app_users USING btree (username);


--
-- Name: idx_ci_advisor; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_ci_advisor ON public.coach_interactions USING btree (advisor_name);


--
-- Name: idx_ci_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_ci_created ON public.coach_interactions USING btree (created_at DESC);


--
-- Name: idx_coach_interactions_advisor_day; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_coach_interactions_advisor_day ON public.coach_interactions USING btree (store_id, advisor_name, created_at DESC);


--
-- Name: idx_cycles_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_cycles_created ON public.agent_cycles USING btree (created_at DESC);


--
-- Name: idx_cycles_store; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_cycles_store ON public.agent_cycles USING btree (store_id);


--
-- Name: idx_cycles_urgency; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_cycles_urgency ON public.agent_cycles USING btree (urgency_level);


--
-- Name: idx_errors_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_errors_created ON public.agent_errors USING btree (created_at DESC);


--
-- Name: idx_errors_cycle; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_errors_cycle ON public.agent_errors USING btree (cycle_id);


--
-- Name: idx_errors_resolved; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_errors_resolved ON public.agent_errors USING btree (resolved);


--
-- Name: idx_errors_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_errors_type ON public.agent_errors USING btree (error_type);


--
-- Name: idx_hitl_reviews_pending; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_hitl_reviews_pending ON public.hitl_reviews USING btree (store_id, status, created_at DESC) WHERE (status = 'pending'::text);


--
-- Name: idx_logs_agent; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_logs_agent ON public.agent_logs USING btree (agent_name);


--
-- Name: idx_logs_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_logs_created ON public.agent_logs USING btree (created_at DESC);


--
-- Name: idx_logs_cycle; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_logs_cycle ON public.agent_logs USING btree (cycle_id);


--
-- Name: idx_logs_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_logs_status ON public.agent_logs USING btree (status);


--
-- Name: idx_rag_agent; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_rag_agent ON public.rag_feedback USING btree (agent_name);


--
-- Name: idx_rag_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_rag_created ON public.rag_feedback USING btree (created_at DESC);


--
-- Name: idx_rag_cycle; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_rag_cycle ON public.rag_feedback USING btree (cycle_id);


--
-- Name: idx_rag_fb_cycle; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_rag_fb_cycle ON public.rag_feedback_metrics USING btree (cycle_id);


--
-- Name: idx_rag_session; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_rag_session ON public.rag_queries USING btree (session_id);


--
-- Name: idx_session_store_time; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_session_store_time ON public.agent_sessions USING btree (store_id, last_activity);


--
-- Name: idx_store_kpi_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_store_kpi_date ON public.store_kpi_daily USING btree (kpi_date DESC);


--
-- Name: idx_store_kpi_gap; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_store_kpi_gap ON public.store_kpi_daily USING btree (kpi_date, gap_ca_pct) WHERE (gap_ca_pct < ('-10'::integer)::numeric);


--
-- Name: idx_targets_agent_month; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_targets_agent_month ON public.telco_targets_monthly USING btree (agent_id, annee, mois) WHERE (agent_id IS NOT NULL);


--
-- Name: idx_targets_store_month; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_targets_store_month ON public.telco_targets_monthly USING btree (store_id, annee, mois);


--
-- Name: idx_agents_performance; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_agents_performance ON sales.agents USING btree (performance_level);


--
-- Name: idx_agents_store; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_agents_store ON sales.agents USING btree (store_id);


--
-- Name: idx_boutiques_active; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_boutiques_active ON sales.boutiques USING btree (active);


--
-- Name: idx_boutiques_ville; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_boutiques_ville ON sales.boutiques USING btree (ville);


--
-- Name: idx_cs_cat; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_cs_cat ON sales.coaching_scripts USING btree (categorie);


--
-- Name: idx_cs_created; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_cs_created ON sales.coaching_scripts USING btree (created_at DESC);


--
-- Name: idx_cs_store; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_cs_store ON sales.coaching_scripts USING btree (store_id);


--
-- Name: idx_objectifs_agent; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_objectifs_agent ON sales.objectifs USING btree (agent_id);


--
-- Name: idx_objectifs_store_date; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_objectifs_store_date ON sales.objectifs USING btree (store_id, date_objectif);


--
-- Name: idx_produits_actif; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_produits_actif ON sales.produits USING btree (actif);


--
-- Name: idx_produits_categorie; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_produits_categorie ON sales.produits USING btree (categorie);


--
-- Name: idx_produits_famille; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_produits_famille ON sales.produits USING btree (famille);


--
-- Name: idx_transactions_agent; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_agent ON sales.transactions USING btree (agent_id);


--
-- Name: idx_transactions_composite; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_composite ON sales.transactions USING btree (date_only, store_id, agent_id);


--
-- Name: idx_transactions_date; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_date ON sales.transactions USING btree (date_only);


--
-- Name: idx_transactions_heure; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_heure ON sales.transactions USING btree (heure);


--
-- Name: idx_transactions_rt_agent; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_rt_agent ON sales.transactions_rt USING btree (agent_id);


--
-- Name: idx_transactions_rt_composite; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_rt_composite ON sales.transactions_rt USING btree (date_only, store_id, agent_id);


--
-- Name: idx_transactions_rt_date; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_rt_date ON sales.transactions_rt USING btree (date_only);


--
-- Name: idx_transactions_rt_store; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_rt_store ON sales.transactions_rt USING btree (store_id);


--
-- Name: idx_transactions_rt_store_date; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_rt_store_date ON sales.transactions_rt USING btree (store_id, date_only);


--
-- Name: idx_transactions_rt_store_time; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_rt_store_time ON sales.transactions_rt USING btree (store_id, created_at DESC);


--
-- Name: idx_transactions_sku; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_sku ON sales.transactions USING btree (sku);


--
-- Name: idx_transactions_store; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_store ON sales.transactions USING btree (store_id);


--
-- Name: idx_transactions_store_date; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_store_date ON sales.transactions USING btree (store_id, date_only);


--
-- Name: idx_transactions_store_heure; Type: INDEX; Schema: sales; Owner: postgres
--

CREATE INDEX idx_transactions_store_heure ON sales.transactions USING btree (store_id, date_only, heure);


--
-- Name: idx_mvt_sku_store; Type: INDEX; Schema: supply; Owner: postgres
--

CREATE INDEX idx_mvt_sku_store ON supply.stock_movements USING btree (sku, store_id, date_mouvement DESC);


--
-- Name: idx_mvt_store_date; Type: INDEX; Schema: supply; Owner: postgres
--

CREATE INDEX idx_mvt_store_date ON supply.stock_movements USING btree (store_id, date_mouvement DESC);


--
-- Name: idx_po_recommendation; Type: INDEX; Schema: supply; Owner: postgres
--

CREATE INDEX idx_po_recommendation ON supply.purchase_orders USING btree (recommendation_id);


--
-- Name: idx_po_sku; Type: INDEX; Schema: supply; Owner: postgres
--

CREATE INDEX idx_po_sku ON supply.purchase_orders USING btree (sku, statut);


--
-- Name: idx_po_store; Type: INDEX; Schema: supply; Owner: postgres
--

CREATE INDEX idx_po_store ON supply.purchase_orders USING btree (store_id, statut);


--
-- Name: idx_serial_sku_store; Type: INDEX; Schema: supply; Owner: postgres
--

CREATE INDEX idx_serial_sku_store ON supply.serial_numbers USING btree (sku, store_id, statut);


--
-- Name: transactions_rt trg_sync_stock_on_sale; Type: TRIGGER; Schema: sales; Owner: postgres
--

CREATE TRIGGER trg_sync_stock_on_sale AFTER INSERT ON sales.transactions_rt FOR EACH ROW EXECUTE FUNCTION public.sync_stock_on_sale();


--
-- Name: alerts fk_alerts_sku; Type: FK CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.alerts
    ADD CONSTRAINT fk_alerts_sku FOREIGN KEY (sku) REFERENCES sales.produits(sku);


--
-- Name: alerts fk_alerts_store; Type: FK CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.alerts
    ADD CONSTRAINT fk_alerts_store FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);


--
-- Name: context_adjustments fk_context_adj_sku; Type: FK CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.context_adjustments
    ADD CONSTRAINT fk_context_adj_sku FOREIGN KEY (sku) REFERENCES sales.produits(sku);


--
-- Name: context_adjustments fk_context_adj_store; Type: FK CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.context_adjustments
    ADD CONSTRAINT fk_context_adj_store FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);


--
-- Name: recommendations fk_reco_sku; Type: FK CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.recommendations
    ADD CONSTRAINT fk_reco_sku FOREIGN KEY (sku) REFERENCES sales.produits(sku);


--
-- Name: recommendations fk_reco_store; Type: FK CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.recommendations
    ADD CONSTRAINT fk_reco_store FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);


--
-- Name: product_master product_master_sku_fkey; Type: FK CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.product_master
    ADD CONSTRAINT product_master_sku_fkey FOREIGN KEY (sku) REFERENCES sales.produits(sku);


--
-- Name: stock_levels stock_levels_sku_fkey; Type: FK CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.stock_levels
    ADD CONSTRAINT stock_levels_sku_fkey FOREIGN KEY (sku) REFERENCES sales.produits(sku);


--
-- Name: stock_levels stock_levels_store_id_fkey; Type: FK CONSTRAINT; Schema: inventory; Owner: postgres
--

ALTER TABLE ONLY inventory.stock_levels
    ADD CONSTRAINT stock_levels_store_id_fkey FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);


--
-- Name: competitor_pricing competitor_pricing_concurrent_id_fkey; Type: FK CONSTRAINT; Schema: market; Owner: postgres
--

ALTER TABLE ONLY market.competitor_pricing
    ADD CONSTRAINT competitor_pricing_concurrent_id_fkey FOREIGN KEY (concurrent_id) REFERENCES market.competitors(concurrent_id);


--
-- Name: agents agents_store_id_fkey; Type: FK CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.agents
    ADD CONSTRAINT agents_store_id_fkey FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);


--
-- Name: transactions_rt fk_rt_agent; Type: FK CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.transactions_rt
    ADD CONSTRAINT fk_rt_agent FOREIGN KEY (agent_id) REFERENCES sales.agents(agent_id);


--
-- Name: transactions_rt fk_rt_cod_prod; Type: FK CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.transactions_rt
    ADD CONSTRAINT fk_rt_cod_prod FOREIGN KEY (cod_prod) REFERENCES sales.produits(sku);


--
-- Name: transactions_rt fk_rt_store; Type: FK CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.transactions_rt
    ADD CONSTRAINT fk_rt_store FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);


--
-- Name: objectifs objectifs_store_id_fkey; Type: FK CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.objectifs
    ADD CONSTRAINT objectifs_store_id_fkey FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);


--
-- Name: transactions transactions_agent_id_fkey; Type: FK CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.transactions
    ADD CONSTRAINT transactions_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES sales.agents(agent_id);


--
-- Name: transactions transactions_sku_fkey; Type: FK CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.transactions
    ADD CONSTRAINT transactions_sku_fkey FOREIGN KEY (sku) REFERENCES sales.produits(sku);


--
-- Name: transactions transactions_store_id_fkey; Type: FK CONSTRAINT; Schema: sales; Owner: postgres
--

ALTER TABLE ONLY sales.transactions
    ADD CONSTRAINT transactions_store_id_fkey FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);


--
-- Name: purchase_orders fk_po_sku; Type: FK CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.purchase_orders
    ADD CONSTRAINT fk_po_sku FOREIGN KEY (sku) REFERENCES sales.produits(sku);


--
-- Name: purchase_orders fk_po_store; Type: FK CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.purchase_orders
    ADD CONSTRAINT fk_po_store FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);


--
-- Name: reorder_params fk_reorder_sku; Type: FK CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.reorder_params
    ADD CONSTRAINT fk_reorder_sku FOREIGN KEY (sku) REFERENCES sales.produits(sku);


--
-- Name: reorder_params fk_reorder_store; Type: FK CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.reorder_params
    ADD CONSTRAINT fk_reorder_store FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);


--
-- Name: stock_movements fk_sm_sku; Type: FK CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.stock_movements
    ADD CONSTRAINT fk_sm_sku FOREIGN KEY (sku) REFERENCES sales.produits(sku);


--
-- Name: stock_movements fk_sm_store; Type: FK CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.stock_movements
    ADD CONSTRAINT fk_sm_store FOREIGN KEY (store_id) REFERENCES sales.boutiques(store_id);


--
-- Name: purchase_orders purchase_orders_recommendation_id_fkey; Type: FK CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.purchase_orders
    ADD CONSTRAINT purchase_orders_recommendation_id_fkey FOREIGN KEY (recommendation_id) REFERENCES inventory.recommendations(id) ON DELETE SET NULL;


--
-- Name: purchase_orders purchase_orders_supplier_id_fkey; Type: FK CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.purchase_orders
    ADD CONSTRAINT purchase_orders_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES supply.suppliers(supplier_id);


--
-- Name: serial_numbers serial_numbers_po_id_fkey; Type: FK CONSTRAINT; Schema: supply; Owner: postgres
--

ALTER TABLE ONLY supply.serial_numbers
    ADD CONSTRAINT serial_numbers_po_id_fkey FOREIGN KEY (po_id) REFERENCES supply.purchase_orders(po_id);


--
-- PostgreSQL database dump complete
--

