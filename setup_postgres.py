"""
setup_postgres.py - Import complet Ooredoo vers PostgreSQL
python setup_postgres.py
"""

import os
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import logging
import numpy as np
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "dbname":   "ooredoo_sales",
    "user":     "postgres",
    "password": "admin",
}

DATA_DIR         = Path(__file__).parent / "sales-module" / "data"
BOUTIQUE_FILE    = DATA_DIR / "boutique_actif.xls"
PRODUCT_FILE     = DATA_DIR / "product.xls"
STOCK_FILE       = DATA_DIR / "stock_centre.xls"
TRANSACTION_FILE = DATA_DIR / "transaction_vente_test_100500_fast.csv"

FAMILLE_MAP = {
    11: "Recharge", 12: "Recharge", 13: "Recharge",
    20: "SIM Ligne", 21: "SIM Ligne",
    30: "Terminal",
    40: "Accessoire", 41: "Accessoire", 44: "Accessoire",
    47: "Accessoire", 48: "Accessoire",
    10: "Forfait Mobile", 15: "Forfait Mobile",
    52: "Forfait Data",
    60: "Box Fibre",
    70: "Services",
    80: "Postpaye", 90: "Postpaye", 92: "Postpaye",
}


def get_conn():
    os.environ['PGCLIENTENCODING'] = 'UTF8'
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_client_encoding('UTF8')
    return conn


def clean(v, maxlen=None):
    if v is None:
        return None
    try:
        if isinstance(v, float) and np.isnan(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, str):
        v = v.strip()
        if maxlen:
            v = v[:maxlen]
        if v == '' or v.lower() == 'nan':
            return None
    return v


def create_schema(conn):
    logger.info("Creation du schema...")
    with conn.cursor() as cur:
        cur.execute("""
        CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

        DROP TABLE IF EXISTS coaching_cards     CASCADE;
        DROP TABLE IF EXISTS ratios_historiques CASCADE;
        DROP TABLE IF EXISTS patterns_horaires  CASCADE;
        DROP TABLE IF EXISTS objectifs          CASCADE;
        DROP TABLE IF EXISTS transactions       CASCADE;
        DROP TABLE IF EXISTS stock              CASCADE;
        DROP TABLE IF EXISTS agents             CASCADE;
        DROP TABLE IF EXISTS produits           CASCADE;
        DROP TABLE IF EXISTS boutiques          CASCADE;

        CREATE TABLE boutiques (
            store_id      VARCHAR(10)  PRIMARY KEY,
            store_name    VARCHAR(200),
            adresse       VARCHAR(300),
            ville         VARCHAR(100),
            type_boutique VARCHAR(5),
            statut        VARCHAR(5)   DEFAULT 'A',
            created_at    TIMESTAMP    DEFAULT NOW()
        );

        CREATE TABLE produits (
            cod_prod    VARCHAR(20)  PRIMARY KEY,
            des_prod    VARCHAR(300),
            cod_famille INTEGER,
            categorie   VARCHAR(60),
            pv_ttc      NUMERIC(12,3),
            pa_ttc      NUMERIC(12,3),
            tx_marge    NUMERIC(10,3),
            tx_tva      NUMERIC(8,2),
            flag_5g     VARCHAR(5),
            flag_4g     VARCHAR(5),
            flag_3g     VARCHAR(5),
            actif       VARCHAR(5)   DEFAULT 'O',
            created_at  TIMESTAMP    DEFAULT NOW()
        );

        CREATE TABLE agents (
            agent_id      VARCHAR(10)  PRIMARY KEY,
            agent_name    VARCHAR(150),
            agent_surname VARCHAR(150),
            store_id      VARCHAR(10)  REFERENCES boutiques(store_id),
            actif         BOOLEAN      DEFAULT TRUE,
            created_at    TIMESTAMP    DEFAULT NOW()
        );

        CREATE TABLE stock (
            id         SERIAL      PRIMARY KEY,
            store_id   VARCHAR(10) REFERENCES boutiques(store_id),
            cod_prod   VARCHAR(20) REFERENCES produits(cod_prod),
            qte_stk    INTEGER     DEFAULT 0,
            qte_vte    INTEGER     DEFAULT 0,
            qte_res    INTEGER     DEFAULT 0,
            dat_maj    TIMESTAMP,
            updated_at TIMESTAMP   DEFAULT NOW(),
            UNIQUE(store_id, cod_prod)
        );

        CREATE TABLE transactions (
            sale_id         VARCHAR(40) PRIMARY KEY,
            date_vente      TIMESTAMP   NOT NULL,
            store_id        VARCHAR(10) REFERENCES boutiques(store_id),
            cod_prod        VARCHAR(20),
            des_produit     VARCHAR(300),
            agent_id        VARCHAR(10),
            qte_produit     INTEGER     DEFAULT 1,
            lig_ttc         NUMERIC(14,3),
            lig_ht          NUMERIC(14,3),
            lig_tva         NUMERIC(14,3),
            tx_tva          NUMERIC(8,2),
            forfait         VARCHAR(100),
            type_abonnement VARCHAR(30),
            type_client     VARCHAR(30),
            num_client      VARCHAR(50),
            num_serie       VARCHAR(80),
            date_only       DATE
                GENERATED ALWAYS AS (date_vente::DATE) STORED,
            heure           SMALLINT
                GENERATED ALWAYS AS (EXTRACT(HOUR FROM date_vente)::SMALLINT) STORED,
            jour_semaine    SMALLINT
                GENERATED ALWAYS AS (EXTRACT(DOW FROM date_vente)::SMALLINT) STORED,
            created_at      TIMESTAMP   DEFAULT NOW()
        );

        CREATE TABLE objectifs (
            id            SERIAL      PRIMARY KEY,
            store_id      VARCHAR(10) REFERENCES boutiques(store_id),
            agent_id      VARCHAR(10),
            date_objectif DATE        NOT NULL,
            objectif_ca   NUMERIC(14,3) NOT NULL,
            created_at    TIMESTAMP   DEFAULT NOW(),
            UNIQUE(store_id, agent_id, date_objectif)
        );

        CREATE TABLE ratios_historiques (
            id              SERIAL      PRIMARY KEY,
            store_id        VARCHAR(10) REFERENCES boutiques(store_id),
            jour_semaine    SMALLINT    NOT NULL,
            heure           SMALLINT    NOT NULL,
            ratio_eod       NUMERIC(10,4),
            ca_heure_moyen  NUMERIC(14,3),
            ca_eod_moyen    NUMERIC(14,3),
            nb_observations INTEGER,
            updated_at      TIMESTAMP   DEFAULT NOW(),
            UNIQUE(store_id, jour_semaine, heure)
        );

        CREATE TABLE patterns_horaires (
            id              SERIAL      PRIMARY KEY,
            store_id        VARCHAR(10) REFERENCES boutiques(store_id),
            jour_semaine    SMALLINT    NOT NULL,
            heure           SMALLINT    NOT NULL,
            poids_moyen     NUMERIC(10,6),
            ca_moyen        NUMERIC(14,3),
            nb_observations INTEGER,
            updated_at      TIMESTAMP   DEFAULT NOW(),
            UNIQUE(store_id, jour_semaine, heure)
        );

        CREATE TABLE coaching_cards (
            id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
            store_id        VARCHAR(10),
            agent_id        VARCHAR(10),
            cycle_id        VARCHAR(30),
            date_conseil    TIMESTAMP   DEFAULT NOW(),
            urgence         VARCHAR(10),
            gap_pct         NUMERIC(6,2),
            gap_amount      NUMERIC(14,3),
            forecast_eod    NUMERIC(14,3),
            analyst_summary TEXT,
            strategie       TEXT,
            actions         JSONB,
            cause_racine    TEXT,
            contexte        JSONB,
            statut          VARCHAR(20) DEFAULT 'PENDING',
            created_at      TIMESTAMP   DEFAULT NOW()
        );

        CREATE INDEX idx_tx_store_date  ON transactions(store_id, date_only);
        CREATE INDEX idx_tx_agent       ON transactions(agent_id);
        CREATE INDEX idx_tx_prod        ON transactions(cod_prod);
        CREATE INDEX idx_tx_heure       ON transactions(heure, jour_semaine);
        CREATE INDEX idx_stock_store    ON stock(store_id);
        CREATE INDEX idx_coaching_store ON coaching_cards(store_id, date_conseil);
        """)
    conn.commit()
    logger.info("  OK - Schema cree avec DROP + CREATE")


def import_boutiques(conn):
    logger.info("Import boutiques...")
    df = pd.read_excel(BOUTIQUE_FILE, engine='xlrd')
    rows = []
    for _, r in df.iterrows():
        rows.append((
            clean(r['CD_DIST'],                maxlen=10),
            clean(r.get('NOM_DIST'),           maxlen=200),
            clean(r.get('ADRESSE'),            maxlen=300),
            clean(r.get('VILLE'),              maxlen=100),
            clean(r.get('INTERNE_EXTERNE'),    maxlen=5),
            clean(r.get('CD_STATUT', 'A'),     maxlen=5),
        ))
    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO boutiques
                (store_id, store_name, adresse, ville, type_boutique, statut)
            VALUES %s
            ON CONFLICT (store_id) DO UPDATE SET
                store_name = EXCLUDED.store_name
        """, rows)
    conn.commit()
    logger.info(f"  OK - {len(rows)} boutiques")


def import_produits(conn):
    logger.info("Import produits...")
    df = pd.read_excel(PRODUCT_FILE, engine='xlrd')
    rows = []
    skipped = 0
    for _, r in df.iterrows():
        cod = clean(str(r['COD_PROD']), maxlen=20)
        if not cod:
            skipped += 1
            continue

        famille = None
        try:
            fam_raw = r.get('COD_FAM')
            if fam_raw is not None and not (isinstance(fam_raw, float) and np.isnan(fam_raw)):
                famille = int(fam_raw)
        except (TypeError, ValueError):
            pass

        categorie = FAMILLE_MAP.get(famille, "Autre")

        def safe_flag(val):
            v = clean(str(val), maxlen=5)
            return v if v and v != 'nan' else None

        rows.append((
            cod,
            clean(r.get('DES_PROD'),  maxlen=300),
            famille,
            categorie,
            clean(r.get('PV_TTC')),
            clean(r.get('PA_TTC')),
            clean(r.get('TX_MARGE')),
            clean(r.get('TX_TVA')),
            safe_flag(r.get('FLAG_5G', '')),
            safe_flag(r.get('FLAG_4G', '')),
            safe_flag(r.get('FLAG_3G', '')),
            safe_flag(r.get('ACTIF',   'O')) or 'O',
        ))

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO produits
                (cod_prod, des_prod, cod_famille, categorie,
                 pv_ttc, pa_ttc, tx_marge, tx_tva,
                 flag_5g, flag_4g, flag_3g, actif)
            VALUES %s
            ON CONFLICT (cod_prod) DO UPDATE SET
                des_prod  = EXCLUDED.des_prod,
                pv_ttc    = EXCLUDED.pv_ttc,
                categorie = EXCLUDED.categorie
        """, rows)
    conn.commit()
    logger.info(f"  OK - {len(rows)} produits | {skipped} ignores")


def import_agents(conn):
    logger.info("Import agents...")
    df = pd.read_csv(
        TRANSACTION_FILE,
        usecols=['AGENT_ID', 'AGENT_NAME', 'AGENT_SURNAME', 'CODE_CENTRE'],
        dtype=str
    )
    agents = (
        df.groupby('AGENT_ID')
        .agg(
            agent_name    = ('AGENT_NAME',    'first'),
            agent_surname = ('AGENT_SURNAME', 'first'),
            store_id      = ('CODE_CENTRE',   lambda x: x.mode()[0]),
        )
        .reset_index()
    )

    with conn.cursor() as cur:
        cur.execute("SELECT store_id FROM boutiques")
        valid_stores = {r[0] for r in cur.fetchall()}

    rows = []
    for _, r in agents.iterrows():
        raw_id   = str(r['AGENT_ID']).strip()
        agent_id = raw_id.zfill(4)[:10]
        store_id = str(r['store_id']).strip()
        if store_id not in valid_stores:
            store_id = None
        rows.append((
            agent_id,
            clean(r['agent_name'],    maxlen=150),
            clean(r['agent_surname'], maxlen=150),
            store_id,
            True,
        ))

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO agents (agent_id, agent_name, agent_surname, store_id, actif)
            VALUES %s
            ON CONFLICT (agent_id) DO UPDATE SET
                agent_name    = EXCLUDED.agent_name,
                agent_surname = EXCLUDED.agent_surname
        """, rows)
    conn.commit()
    logger.info(f"  OK - {len(rows)} agents")


def import_stock(conn):
    logger.info("Import stock...")
    df = pd.read_excel(STOCK_FILE, engine='xlrd')

    with conn.cursor() as cur:
        cur.execute("SELECT store_id FROM boutiques")
        valid_stores = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT cod_prod FROM produits")
        valid_prods = {r[0] for r in cur.fetchall()}

    rows    = []
    skipped = 0
    for _, r in df.iterrows():
        s = str(r['CD_DIST']).strip()
        p = str(r['COD_PROD']).strip()
        if s not in valid_stores or p not in valid_prods:
            skipped += 1
            continue
        try:
            qte_stk = int(r.get('QTE_STK', 0) or 0)
            qte_vte = int(r.get('QTE_VTE', 0) or 0)
            qres    = r.get('QTE_RES', 0)
            qte_res = 0 if (qres is None or (isinstance(qres, float) and np.isnan(qres))) else int(qres)
        except (TypeError, ValueError):
            qte_stk = qte_vte = qte_res = 0
        rows.append((s, p, qte_stk, qte_vte, qte_res))

    total = 0
    with conn.cursor() as cur:
        for i in range(0, len(rows), 5000):
            execute_values(cur, """
                INSERT INTO stock (store_id, cod_prod, qte_stk, qte_vte, qte_res)
                VALUES %s
                ON CONFLICT (store_id, cod_prod) DO UPDATE SET
                    qte_stk    = EXCLUDED.qte_stk,
                    qte_vte    = EXCLUDED.qte_vte,
                    updated_at = NOW()
            """, rows[i:i+5000])
            total += len(rows[i:i+5000])
            logger.info(f"  Stock: {total}/{len(rows)}...")
    conn.commit()
    logger.info(f"  OK - {total} lignes stock | {skipped} ignores")


def import_transactions(conn):
    logger.info("Import transactions (100K lignes)...")

    with conn.cursor() as cur:
        cur.execute("SELECT store_id FROM boutiques")
        valid_stores = {r[0] for r in cur.fetchall()}

    total  = 0
    errors = 0

    for chunk_df in pd.read_csv(TRANSACTION_FILE, chunksize=10000, dtype=str):
        rows = []
        for _, r in chunk_df.iterrows():
            store_id = str(r.get('CODE_CENTRE', '')).strip()
            if store_id not in valid_stores:
                errors += 1
                continue
            try:
                sale_id = clean(str(r['SALE_ID']), maxlen=40)
                if not sale_id:
                    errors += 1
                    continue

                agent_raw = str(r.get('AGENT_ID', '')).strip()
                agent_id  = agent_raw.zfill(4)[:10] if agent_raw and agent_raw != 'nan' else None

                def to_float(val):
                    try:
                        return float(val or 0)
                    except (TypeError, ValueError):
                        return 0.0

                def to_int(val):
                    try:
                        return int(float(val or 1))
                    except (TypeError, ValueError):
                        return 1

                rows.append((
                    sale_id,
                    clean(r['DATE_VENTE']),
                    store_id,
                    clean(str(r.get('CODE_PRODUIT', '')), maxlen=20),
                    clean(r.get('DES_PRODUIT'),           maxlen=300),
                    agent_id,
                    to_int(r.get('QTE_PRODUIT', 1)),
                    to_float(r.get('LIG_TTC')),
                    to_float(r.get('LIG_HT')),
                    to_float(r.get('LIG_TVA')),
                    to_float(r.get('TX_TVA')),
                    clean(r.get('FORFAIT'),         maxlen=100),
                    clean(r.get('TYPE_ABONNEMENT'), maxlen=30),
                    clean(r.get('TYPE_CLIENT'),     maxlen=30),
                    clean(r.get('NUM_CLIENT'),      maxlen=50),
                    clean(r.get('NUM_SERIE'),       maxlen=80),
                ))
            except Exception:
                errors += 1

        if rows:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO transactions (
                        sale_id, date_vente, store_id, cod_prod,
                        des_produit, agent_id, qte_produit,
                        lig_ttc, lig_ht, lig_tva, tx_tva,
                        forfait, type_abonnement, type_client,
                        num_client, num_serie
                    ) VALUES %s
                    ON CONFLICT (sale_id) DO NOTHING
                """, rows)
            conn.commit()
            total += len(rows)
            logger.info(f"  Transactions: {total} | ignorees: {errors}")

    logger.info(f"  OK - {total} transactions | {errors} ignorees")


def compute_ratios(conn):
    logger.info("Calcul ratios historiques...")
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO ratios_historiques
                (store_id, jour_semaine, heure,
                 ratio_eod, ca_heure_moyen, ca_eod_moyen, nb_observations)
            SELECT
                store_id, jour_semaine, heure,
                AVG(ca_eod / NULLIF(ca_cumul, 0)),
                AVG(ca_cumul),
                AVG(ca_eod),
                COUNT(*)
            FROM (
                SELECT
                    store_id, date_only, jour_semaine, heure,
                    SUM(SUM(lig_ttc)) OVER (
                        PARTITION BY store_id, date_only
                        ORDER BY heure
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS ca_cumul,
                    SUM(SUM(lig_ttc)) OVER (
                        PARTITION BY store_id, date_only
                    ) AS ca_eod
                FROM transactions WHERE lig_ttc > 0
                GROUP BY store_id, date_only, jour_semaine, heure
            ) sub
            WHERE ca_cumul > 0
            GROUP BY store_id, jour_semaine, heure
            ON CONFLICT (store_id, jour_semaine, heure) DO UPDATE SET
                ratio_eod       = EXCLUDED.ratio_eod,
                nb_observations = EXCLUDED.nb_observations,
                updated_at      = NOW()
        """)
    conn.commit()
    logger.info("  OK - Ratios calcules")


def compute_patterns(conn):
    logger.info("Calcul patterns horaires...")
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO patterns_horaires
                (store_id, jour_semaine, heure,
                 poids_moyen, ca_moyen, nb_observations)
            SELECT
                store_id, jour_semaine, heure,
                AVG(ca_heure / NULLIF(ca_jour, 0)),
                AVG(ca_heure),
                COUNT(*)
            FROM (
                SELECT
                    store_id, date_only, jour_semaine, heure,
                    SUM(lig_ttc) AS ca_heure,
                    SUM(SUM(lig_ttc)) OVER (
                        PARTITION BY store_id, date_only
                    ) AS ca_jour
                FROM transactions WHERE lig_ttc > 0
                GROUP BY store_id, date_only, jour_semaine, heure
            ) sub
            GROUP BY store_id, jour_semaine, heure
            ON CONFLICT (store_id, jour_semaine, heure) DO UPDATE SET
                poids_moyen     = EXCLUDED.poids_moyen,
                ca_moyen        = EXCLUDED.ca_moyen,
                nb_observations = EXCLUDED.nb_observations,
                updated_at      = NOW()
        """)
    conn.commit()
    logger.info("  OK - Patterns calcules")


def generate_objectifs(conn):
    logger.info("Generation objectifs...")
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO objectifs (store_id, agent_id, date_objectif, objectif_ca)
            SELECT store_id, NULL, CURRENT_DATE, AVG(ca_jour) * 1.10
            FROM (
                SELECT store_id, date_only, SUM(lig_ttc) AS ca_jour
                FROM transactions GROUP BY store_id, date_only
            ) sub GROUP BY store_id
            ON CONFLICT (store_id, agent_id, date_objectif) DO NOTHING
        """)
        cur.execute("""
            INSERT INTO objectifs (store_id, agent_id, date_objectif, objectif_ca)
            SELECT store_id, agent_id, CURRENT_DATE, AVG(ca_jour) * 1.10
            FROM (
                SELECT store_id, agent_id, date_only, SUM(lig_ttc) AS ca_jour
                FROM transactions WHERE agent_id IS NOT NULL
                GROUP BY store_id, agent_id, date_only
            ) sub GROUP BY store_id, agent_id
            ON CONFLICT (store_id, agent_id, date_objectif) DO NOTHING
        """)
    conn.commit()
    logger.info("  OK - Objectifs generes")


if __name__ == "__main__":
    logger.info("Demarrage setup PostgreSQL Ooredoo")

    for f in [BOUTIQUE_FILE, PRODUCT_FILE, STOCK_FILE, TRANSACTION_FILE]:
        if not f.exists():
            logger.error(f"Fichier manquant : {f}")
            exit(1)
        logger.info(f"  Fichier OK : {f.name}")

    conn = get_conn()
    logger.info("Connexion PostgreSQL OK")

    create_schema(conn)
    import_boutiques(conn)
    import_produits(conn)
    import_agents(conn)
    import_stock(conn)
    import_transactions(conn)
    compute_ratios(conn)
    compute_patterns(conn)
    generate_objectifs(conn)
    conn.close()

    logger.info("Termine avec succes !")
    logger.info("Copier postgres_provider.py dans sales-module/data/")
    logger.info("Remplacer mock_provider par postgres_provider dans nodes.py")