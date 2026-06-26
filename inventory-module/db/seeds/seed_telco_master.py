"""
Seed Telco Master — populates market/supply/customer schemas from CSV data
and enriches existing sales.boutiques, sales.produits, sales.agents tables.

Run: python -m inventory-module.db.seeds.seed_telco_master
"""

import csv
import json
import uuid
import logging
from datetime import datetime, date
from pathlib import Path
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DB_PARAMS = {
    "host": "localhost",
    "port": 5432,
    "dbname": "ooredoo_sales",
    "user": "postgres",
    "password": "admin",
}

DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "telco"


def get_conn():
    return psycopg2.connect(**DB_PARAMS)


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_csv(filename: str) -> list[dict]:
    path = DATA_DIR / filename
    if not path.exists():
        log.warning(f"CSV not found: {path}")
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_float(val: str) -> float | None:
    try:
        return float(val) if val else None
    except ValueError:
        return None


def to_int(val: str) -> int | None:
    try:
        return int(val) if val else None
    except ValueError:
        return None


def to_date(val: str) -> date | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


# ── 1. Market Events ──────────────────────────────────────────────────────────

def seed_market_events(conn) -> int:
    rows = load_csv("evenements_marche.csv")
    if not rows:
        return 0

    data = []
    for r in rows:
        cats = json.dumps(["TERMINAL", "FORFAIT", "SIM", "RECHARGE"])
        data.append((
            str(uuid.uuid4()),
            r["event_name"],
            r["event_type"],
            r.get("sous_type") or None,
            to_date(r["start_date"]),
            to_date(r["end_date"]),
            r.get("scope", "NATIONAL"),
            None,
            cats,
            to_float(r.get("uplift_terminal", "0")),
            to_float(r.get("uplift_forfait", "0")),
            to_float(r.get("uplift_sim", "0")),
            to_float(r.get("uplift_recharge", "0")),
            to_float(r.get("uplift_accessoire", "0")),
            r.get("intensite", "MEDIUM"),
            "CSV_IMPORT",
            r.get("note_strategie") or None,
        ))

    with conn.cursor() as cur:
        cur.execute("DELETE FROM market.events")
        execute_values(cur, """
            INSERT INTO market.events
                (event_id, event_name, event_type, sous_type, start_date, end_date,
                 scope, region_ids, categories_impactees,
                 uplift_terminal, uplift_forfait, uplift_sim, uplift_recharge, uplift_accessoire,
                 intensite, source_donnee, note_strategie)
            VALUES %s
        """, data)
    conn.commit()
    log.info(f"  market.events: {len(data)} events inserted")
    return len(data)


# ── 2. Seasonal Patterns ──────────────────────────────────────────────────────

def seed_seasonal_patterns(conn) -> int:
    rows = load_csv("saisonnalite_demande.csv")
    if not rows:
        return 0

    data = []
    for r in rows:
        data.append((
            r["categorie"],
            int(r["mois"]),
            to_int(r.get("jour_semaine", "")),
            to_float(r["facteur_demande"]),
            to_float(r.get("facteur_std", "0.1")),
            to_int(r.get("nb_annees_data", "2")),
            r.get("confidence", "MEDIUM"),
            r.get("notes") or None,
        ))

    with conn.cursor() as cur:
        cur.execute("DELETE FROM market.seasonal_patterns")
        execute_values(cur, """
            INSERT INTO market.seasonal_patterns
                (categorie, mois, jour_semaine, facteur_demande, facteur_std,
                 nb_annees_data, confidence, notes)
            VALUES %s
            ON CONFLICT (categorie, mois, COALESCE(jour_semaine, -1)) DO UPDATE
                SET facteur_demande=EXCLUDED.facteur_demande,
                    facteur_std=EXCLUDED.facteur_std,
                    updated_at=NOW()
        """, data)
    conn.commit()
    log.info(f"  market.seasonal_patterns: {len(data)} patterns inserted")
    return len(data)


# ── 3. Competitors ────────────────────────────────────────────────────────────

def seed_competitors(conn) -> int:
    rows = load_csv("concurrents.csv")
    if not rows:
        return 0

    data = []
    for r in rows:
        data.append((
            r["concurrent_id"],
            r["nom"],
            r.get("code_operateur") or None,
            r.get("pays", "Tunisia"),
            to_float(r.get("part_marche_pct")),
            to_int(r.get("nb_abonnes")),
            r.get("positionnement", "MID"),
            r.get("points_forts") or "[]",
            r.get("points_faibles") or "[]",
            to_date(r.get("date_entree_marche")),
            True,
        ))

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO market.competitors
                (concurrent_id, nom, code_operateur, pays, part_marche_pct, nb_abonnes,
                 positionnement, points_forts, points_faibles, date_entree_marche, actif)
            VALUES %s
            ON CONFLICT (concurrent_id) DO UPDATE
                SET part_marche_pct=EXCLUDED.part_marche_pct,
                    nb_abonnes=EXCLUDED.nb_abonnes,
                    updated_at=NOW()
        """, data)
    conn.commit()
    log.info(f"  market.competitors: {len(data)} competitors inserted")
    return len(data)


# ── 4. Competitor Pricing ─────────────────────────────────────────────────────

def seed_competitor_pricing(conn) -> int:
    rows = load_csv("prix_concurrents.csv")
    if not rows:
        return 0

    data = []
    for r in rows:
        data.append((
            r["concurrent_id"],
            r["categorie"],
            r["produit_type"],
            to_float(r.get("donnees_go")),
            to_int(r.get("minutes_voix")),
            to_int(r.get("sms_count")),
            to_float(r.get("prix_ht")),
            to_float(r.get("prix_ttc")),
            to_int(r.get("engagement_mois", "0")),
            to_date(r["date_releve"]),
            r.get("source", "WEB"),
            True,
        ))

    with conn.cursor() as cur:
        cur.execute("DELETE FROM market.competitor_pricing")
        execute_values(cur, """
            INSERT INTO market.competitor_pricing
                (concurrent_id, categorie, produit_type, donnees_go, minutes_voix,
                 sms_count, prix_ht, prix_ttc, engagement_mois, date_releve, source, actif)
            VALUES %s
        """, data)
    conn.commit()
    log.info(f"  market.competitor_pricing: {len(data)} prices inserted")
    return len(data)


# ── 5. MNP Flows ──────────────────────────────────────────────────────────────

def seed_mnp_flows(conn) -> int:
    rows = load_csv("mnp_flows.csv")
    if not rows:
        return 0

    data = []
    for r in rows:
        data.append((
            str(uuid.uuid4()),
            r["direction"],
            r.get("operateur_origine") or None,
            r.get("operateur_destination") or None,
            to_date(r["mois"]),
            int(r["volume"]),
            r.get("categorie_client", "RESI"),
            r.get("raison_principale") or None,
            r.get("wilaya") or None,
        ))

    with conn.cursor() as cur:
        cur.execute("DELETE FROM market.mnp_flows")
        execute_values(cur, """
            INSERT INTO market.mnp_flows
                (mnp_id, direction, operateur_origine, operateur_destination,
                 mois, volume, categorie_client, raison_principale, wilaya)
            VALUES %s
        """, data)
    conn.commit()
    log.info(f"  market.mnp_flows: {len(data)} MNP flows inserted")
    return len(data)


# ── 6. Suppliers ──────────────────────────────────────────────────────────────

def seed_suppliers(conn) -> int:
    rows = load_csv("fournisseurs.csv")
    if not rows:
        return 0

    data = []
    for r in rows:
        data.append((
            r["supplier_id"],
            r["nom"],
            r.get("pays_origine") or None,
            r["type_fournisseur"],
            r.get("categories", "[]"),
            r.get("marques", "[]"),
            to_int(r.get("delai_livraison_moy", "14")),
            to_int(r.get("delai_livraison_std", "3")),
            to_float(r.get("taux_fiabilite", "0.90")),
            to_int(r.get("commande_min", "1")),
            to_int(r.get("commande_multiple", "1")),
            r.get("devise", "TND"),
            r.get("conditions_paiement") or None,
            r.get("contact_nom") or None,
            r.get("contact_email") or None,
            True,
        ))

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO supply.suppliers
                (supplier_id, nom, pays_origine, type_fournisseur, categories, marques,
                 delai_livraison_moy, delai_livraison_std, taux_fiabilite,
                 commande_min, commande_multiple, devise, conditions_paiement,
                 contact_nom, contact_email, actif)
            VALUES %s
            ON CONFLICT (supplier_id) DO UPDATE
                SET delai_livraison_moy=EXCLUDED.delai_livraison_moy,
                    taux_fiabilite=EXCLUDED.taux_fiabilite,
                    updated_at=NOW()
        """, data)
    conn.commit()
    log.info(f"  supply.suppliers: {len(data)} suppliers inserted")
    return len(data)


# ── 7. Customer Segments ──────────────────────────────────────────────────────

def seed_customer_segments(conn) -> int:
    segments = [
        ("RESI_STANDARD", "Résidentiel Standard",
         "Client grand public, usage voice + data modéré",
         28.0, 8.0, 0.0420, 36, "BOUTIQUE",
         '["FORFAIT_PREPAYE","RECHARGE"]', 5800000, 32.0),
        ("RESI_DATA",     "Résidentiel Data-First",
         "Client résidentiel fort consommateur data, probable millennial",
         55.0, 15.0, 0.0280, 48, "DIGITAL",
         '["FORFAIT_DATA","BOX_4G","TERMINAL_MIDRANGE"]', 1800000, 9.9),
        ("RESI_PREMIUM",  "Résidentiel Premium",
         "Client premium, postpayé, smartphone haut de gamme",
         120.0, 30.0, 0.0180, 60, "BOUTIQUE",
         '["TERMINAL_PREMIUM","FORFAIT_POSTPAYE","ASSURANCE"]', 450000, 2.5),
        ("CORPO_PME",     "Corporate PME",
         "Petite et moyenne entreprise, lignes multiples",
         200.0, 50.0, 0.0150, 24, "B2B",
         '["FORFAIT_POSTPAYE_CORPO","TERMINAUX_FLOTTE"]', 85000, 0.5),
        ("CORPO_GRAND",   "Grand Compte Corporate",
         "Grande entreprise, contrat annuel, account manager dédié",
         800.0, 200.0, 0.0080, 36, "B2B",
         '["OFFRE_SUR_MESURE","FLOTTE_TERMINAUX"]', 12000, 0.07),
        ("SENIOR",        "Senior 55+",
         "Client senior, usage vocal principalement, besoin assistance",
         18.0, 5.0, 0.0350, 30, "BOUTIQUE",
         '["FORFAIT_BASIQUE","RECHARGE","TERMINAL_FEATURE"]', 650000, 3.6),
        ("ETUDIANT",      "Étudiant 18-25 ans",
         "Étudiant, budget serré, fort usage data mobile",
         30.0, 10.0, 0.0380, 24, "DIGITAL",
         '["FORFAIT_ETUDIANT","TERMINAL_ENTRÉE_GAMME"]', 920000, 5.1),
        ("ROAMING",       "Client Diaspora / Roaming",
         "Tunisien résidant à l'étranger, achats SIM en boutique lors des visites",
         45.0, 20.0, 0.0600, 12, "BOUTIQUE",
         '["SIM_PREPAYEE","RECHARGE","TERMINAL"]', 180000, 1.0),
    ]

    with conn.cursor() as cur:
        cur.execute("DELETE FROM customer.segments")
        execute_values(cur, """
            INSERT INTO customer.segments
                (segment_id, libelle, description, arpu_moyen_tnd, arpu_std_tnd,
                 churn_rate_base, duree_vie_mois, canal_prefere,
                 products_preferes, nb_clients_estime, poids_marche_pct, actif)
            VALUES %s
        """, segments)
    conn.commit()
    log.info(f"  customer.segments: {len(segments)} segments inserted")
    return len(segments)


# ── 8. Enrich sales.boutiques ─────────────────────────────────────────────────

def enrich_boutiques(conn) -> int:
    # Mapping: store_id prefix → type + is_officielle + wilaya estimate
    STORE_ENRICHMENT = {
        "M01": ("M", True, "Tunis", "La Marsa", 36.8892, 10.3211, "2004-03-01"),
        "M02": ("M", True, "Tunis", "Tunis Centre", 36.7983, 10.1718, "2004-04-04"),
        "M03": ("M", True, "Sfax", "Sfax Ville", 34.7406, 10.7603, "2004-06-10"),
        "M04": ("M", True, "Sousse", "Sousse Ville", 35.8256, 10.6369, "2004-07-08"),
        "M05": ("M", True, "Tunis", "Bardo", 36.8089, 10.1356, "2005-05-27"),
        "M06": ("M", True, "Nabeul", "Nabeul Centre", 36.4561, 10.7376, "2005-05-27"),
        "M07": ("M", True, "Bizerte", "Bizerte Centre", 37.2746, 9.8739, "2005-05-27"),
        "M08": ("M", True, "Ariana", "Ariana Centre", 36.8625, 10.1956, "2005-05-27"),
        "M09": ("M", True, "Tunis", "Ezzahra", 36.7542, 10.2786, "2005-05-27"),
        "M10": ("M", True, "Tunis", "Aéroport Tunis", 36.8511, 10.2272, "2005-11-11"),
        "M11": ("M", True, "Kairouan", "Kairouan Centre", 35.6774, 10.0963, "2006-04-13"),
        "M13": ("M", True, "Gabès", "Gabès Centre", 33.8814, 10.0982, "2006-04-13"),
        "M14": ("M", True, "Sfax", "Sfax Sud", 34.7254, 10.7456, "2006-04-13"),
        "M16": ("M", True, "Mahdia", "Mahdia Centre", 35.5047, 11.0622, "2006-06-19"),
        "M17": ("M", True, "Monastir", "Monastir Centre", 35.7643, 10.8113, "2006-06-19"),
        "M18": ("M", True, "Tunis", "Les Berges du Lac", 36.8386, 10.2274, "2006-07-13"),
        "M19": ("M", True, "Monastir", "Aéroport Monastir", 35.7588, 10.7547, "2007-02-07"),
        "M20": ("M", True, "Kasserine", "Kasserine Centre", 35.1725, 8.8325, "2008-01-22"),
        "M21": ("M", True, "Tunis", "Carrefour Tunis", 36.8414, 10.2189, "2009-05-02"),
        "M22": ("M", True, "Tunis", "Siliana", 36.0847, 9.3697, "2009-02-20"),
        "M23": ("M", True, "Tunis", "Habib Bourguiba", 36.7992, 10.1868, "2009-05-02"),
        "C01": ("C", True, "Tunis", "Corporate Charguia", 36.8503, 10.2241, "2002-12-26"),
        "I03": ("I", False, "Tunis", "Ennasr", 36.8786, 10.1992, "2007-06-27"),
        "I08": ("I", False, "Sousse", "Ksibet Hellel", 35.7421, 10.7234, "2007-04-10"),
        "I11": ("I", False, "Médenine", "Médenine Centre", 33.3547, 10.4986, "2010-01-01"),
        "I19": ("I", False, "Le Kef", "Le Kef Centre", 36.1828, 8.7147, "2009-07-20"),
        "I20": ("I", False, "Sousse", "Sousse Erriadh", 35.8178, 10.6398, "2009-07-20"),
        "I24": ("I", False, "Tunis", "Hammam Lif", 36.7231, 10.3356, "2010-01-25"),
        "I27": ("I", False, "Tunis", "Sidi Hassine", 36.8053, 10.1236, "2009-01-01"),
        "I28": ("I", False, "Tataouine", "Tataouine Centre", 32.9295, 10.4518, "2009-01-01"),
        "I38": ("I", False, "Tunis", "Géant Tunis", 36.8089, 10.2356, "2008-01-01"),
        "I47": ("I", False, "Hammamet", "Mnaret Hammamet", 36.4000, 10.6200, "2010-01-01"),
        "I57": ("I", False, "Grombalia", "Grombalia Centre", 36.6000, 10.5000, "2011-01-01"),
        "I60": ("I", False, "Djerba", "FR Midoun Djerba", 33.8700, 10.9900, "2010-01-01"),
        "I63": ("I", False, "Tunis", "Tunisia Mall Lac2", 36.8403, 10.2189, "2012-01-01"),
        "I86": ("I", False, "Nabeul", "Dar Chaabane", 36.5000, 10.7000, "2013-01-01"),
        "I93": ("I", False, "Ben Guerdane", "Ben Guerdane Centre", 33.1200, 11.2200, "2014-01-01"),
        "I94": ("I", False, "Djerba", "Box Djerba", 33.8800, 10.8900, "2014-01-01"),
        "S13": ("S", False, "Kairouan", "Bouhajla", 35.6500, 10.1200, "2015-01-01"),
        "S47": ("S", False, "Tunis", "Carrefour La Marsa", 36.8892, 10.3211, "2016-01-01"),
        "S54": ("S", False, "Tunis", "Sous-dealer Tunis", 36.8000, 10.1800, "2016-01-01"),
        "T06": ("T", False, "Online", "Eshop Ooredoo", None, None, "2018-01-01"),
    }

    updated = 0
    with conn.cursor() as cur:
        # Update by store_id prefix matching
        for store_id, (btype, is_off, wilaya, zone, lat, lon, opened) in STORE_ENRICHMENT.items():
            canal = "ONLINE" if btype == "T" else "B2B" if btype == "C" else "PHYSIQUE"
            cur.execute("""
                UPDATE sales.boutiques
                SET type_boutique=%s, canal=%s, wilaya=%s, zone_commerciale=%s,
                    latitude=%s, longitude=%s, is_officielle=%s, date_ouverture=%s
                WHERE store_id=%s AND type_boutique IS NULL
            """, (btype, canal, wilaya, zone, lat, lon, is_off, to_date(opened), store_id))
            if cur.rowcount:
                updated += 1

        # Set defaults for remaining boutiques based on store_id prefix
        cur.execute("""
            UPDATE sales.boutiques SET
                type_boutique = LEFT(store_id, 1),
                canal = CASE WHEN LEFT(store_id,1)='T' THEN 'ONLINE'
                             WHEN LEFT(store_id,1)='C' THEN 'B2B'
                             ELSE 'PHYSIQUE' END,
                is_officielle = (LEFT(store_id, 1) = 'M'),
                wilaya = COALESCE(wilaya, 'Tunis')
            WHERE type_boutique IS NULL
        """)
        updated += cur.rowcount

        # Set region-based wilaya for stores with known region
        cur.execute("""
            UPDATE sales.boutiques SET
                wilaya = CASE region
                    WHEN 'Grand Tunis' THEN 'Tunis'
                    WHEN 'Sahel' THEN 'Sousse'
                    WHEN 'Cap Bon' THEN 'Nabeul'
                    WHEN 'Centre' THEN 'Kairouan'
                    WHEN 'Sud' THEN 'Sfax'
                    WHEN 'Sud-Ouest' THEN 'Kasserine'
                    WHEN 'Nord' THEN 'Bizerte'
                    ELSE wilaya
                END
            WHERE wilaya = 'Tunis' AND region != 'Grand Tunis'
        """)

    conn.commit()
    log.info(f"  sales.boutiques: enriched {updated} rows")
    return updated


# ── 9. Enrich sales.produits ──────────────────────────────────────────────────

def enrich_produits(conn) -> int:
    """
    Enrich products with:
    - Computed margin (pa_ht estimated as 70-85% of prix_ttc depending on category)
    - Brand/family flags based on product name patterns
    - Telco category flags
    - Lead times by category
    """
    with conn.cursor() as cur:

        # ── Set category flags ────────────────────────────────────────────────
        cur.execute("""
            UPDATE sales.produits SET
                flag_terminal = (categorie = '50'),
                flag_forfait  = (categorie IN ('88', '80')),
                flag_sim      = (categorie IN ('20', '40')),
                flag_recharge = (categorie = '30'),
                flag_5g       = (
                    nom ILIKE '%5G%' OR nom ILIKE '%5 G%' OR
                    nom ILIKE '%Fixe Jdid%' OR nom ILIKE '%Box 5G%'
                ),
                flag_4g       = (
                    nom ILIKE '%4G%' OR nom ILIKE '%4 G%' OR
                    nom ILIKE '%Box%' OR nom ILIKE '%MiFi%' OR nom ILIKE '%MIFI%'
                ),
                serialisable  = (categorie = '50' AND actif = TRUE),
                stockable     = (categorie NOT IN ('88', '80', '99', '90'))
            WHERE flag_terminal IS NULL OR flag_terminal = FALSE
        """)

        # ── Assign brands from product names ──────────────────────────────────
        brands = [
            ("%SAMSUNG%", "Samsung"),
            ("%APPLE%",   "Apple"),
            ("%IPHONE%",  "Apple"),
            ("%NOKIA%",   "Nokia"),
            ("%XIAOMI%",  "Xiaomi"),
            ("%REDMI%",   "Xiaomi"),
            ("%POCO%",    "Xiaomi"),
            ("%HUAWEI%",  "Huawei"),
            ("%HONOR%",   "Honor"),
            ("%TECNO%",   "Tecno"),
            ("%SPARK%",   "Tecno"),
            ("%INFINIX%", "Infinix"),
            ("%ITEL%",    "Itel"),
            ("%MIBRO%",   "Mibro"),
            ("%ORAIMO%",  "Oraimo"),
            ("%OPPO%",    "Oppo"),
            ("%VIVO%",    "Vivo"),
            ("%MOTOROLA%","Motorola"),
            ("%ALCATEL%", "Alcatel"),
            ("%ZTE%",     "ZTE"),
        ]
        for pattern, brand in brands:
            cur.execute("""
                UPDATE sales.produits
                SET marque = %s
                WHERE nom ILIKE %s AND marque IS NULL
            """, (brand, pattern))

        # ── Compute pa_ht and marge_pct_calc ────────────────────────────────
        # Different markup by category:
        # - Terminals (50): 15-25% margin → pa = 75-85% of prix_ttc
        # - Forfaits (88): 70% margin (low cost to operator)
        # - SIM (20,40): 5% margin (distribution cost only)
        # - Recharge (30): 3% commission margin
        # - Accessories (70): 40-50% margin
        cur.execute("""
            UPDATE sales.produits SET
                pa_ht = CASE
                    WHEN categorie = '50'  THEN ROUND(prix_ht * 0.80, 4)
                    WHEN categorie IN ('88','80') THEN ROUND(prix_ht * 0.30, 4)
                    WHEN categorie IN ('20','40') THEN ROUND(prix_ht * 0.95, 4)
                    WHEN categorie = '30'  THEN ROUND(prix_ht * 0.97, 4)
                    WHEN categorie = '70'  THEN ROUND(prix_ht * 0.55, 4)
                    WHEN categorie = '32'  THEN ROUND(prix_ht * 0.60, 4)
                    WHEN categorie = '10'  THEN ROUND(prix_ht * 0.40, 4)
                    ELSE ROUND(prix_ht * 0.75, 4)
                END,
                marge_pct_calc = CASE
                    WHEN categorie = '50'  THEN 20.0
                    WHEN categorie IN ('88','80') THEN 70.0
                    WHEN categorie IN ('20','40') THEN 5.0
                    WHEN categorie = '30'  THEN 3.0
                    WHEN categorie = '70'  THEN 45.0
                    WHEN categorie = '32'  THEN 40.0
                    WHEN categorie = '10'  THEN 60.0
                    ELSE 25.0
                END
            WHERE (pa_ht IS NULL OR marge_pct_calc IS NULL)
              AND prix_ht IS NOT NULL AND prix_ht > 0
        """)

        # ── Set lifecycle stage ───────────────────────────────────────────────
        cur.execute("""
            UPDATE sales.produits SET lifecycle_stage =
                CASE
                    WHEN date_lancement IS NOT NULL
                         AND date_lancement > CURRENT_DATE - INTERVAL '6 months'
                    THEN 'growth'
                    WHEN date_eol IS NOT NULL AND date_eol < CURRENT_DATE
                    THEN 'discontinued'
                    WHEN actif = FALSE THEN 'discontinued'
                    ELSE 'mature'
                END
            WHERE lifecycle_stage IS NULL
        """)

        # ── Set lead times by category ────────────────────────────────────────
        cur.execute("""
            UPDATE sales.produits SET
                lead_time_days = CASE
                    WHEN categorie = '50' AND marque IN ('Apple') THEN 21
                    WHEN categorie = '50' AND marque IN ('Samsung') THEN 14
                    WHEN categorie = '50' THEN 12
                    WHEN categorie IN ('88','80') THEN 0
                    WHEN categorie IN ('20','40') THEN 5
                    WHEN categorie = '30' THEN 3
                    ELSE 10
                END,
                lead_time_std = CASE
                    WHEN categorie = '50' AND marque = 'Apple' THEN 5
                    WHEN categorie = '50' THEN 3
                    ELSE 2
                END,
                moq = CASE
                    WHEN categorie = '50' AND prix_ttc > 500 THEN 5
                    WHEN categorie = '50' THEN 10
                    WHEN categorie IN ('20','40') THEN 100
                    WHEN categorie = '30' THEN 50
                    ELSE 20
                END
            WHERE lead_time_days IS NULL
        """)

        # ── Gamme and famille libelles ────────────────────────────────────────
        GAMME_MAP = {
            "50": "TERMINAL",
            "88": "FORFAIT",
            "80": "FORFAIT_BOX",
            "20": "SIM_KIT",
            "40": "SIM_SERVICE",
            "30": "RECHARGE",
            "70": "ACCESSOIRE",
            "32": "ACCESSOIRE_PREMIUM",
            "99": "SERVICE",
            "90": "SERVICE_DIGITAL",
            "10": "BUSINESS",
        }
        for cat, gamme in GAMME_MAP.items():
            cur.execute("""
                UPDATE sales.produits SET gamme_libelle = %s
                WHERE categorie = %s AND gamme_libelle IS NULL
            """, (gamme, cat))

        # famille libelle mapping
        FAMILLE_MAP = {
            20: "SAMSUNG", 21: "WEARABLE_XIAOMI", 22: "HUAWEI",
            24: "ACCESSOIRE_CHARGEUR", 27: "SMARTWATCH",
            30: "NOKIA", 40: "OOREDOO_BOX", 44: "AUTRE_MARQUE",
            47: "ACCESSOIRE_PROTECTION", 48: "COQUE_ECRAN",
            52: "INFINIX", 53: "TECNO_SPARK", 55: "MIFI_ROUTER",
            60: "AUDIO_ACCESSOIRE", 70: "ACCESSOIRE_GENERIC",
            80: "FORFAIT_POSTPAYE", 90: "SERVICE_DIGITAL",
            92: "SIM_CARTE", 94: "RECHARGE_CARTE", 99: "AUTRE",
        }
        for fam_id, fam_lib in FAMILLE_MAP.items():
            cur.execute("""
                UPDATE sales.produits SET famille_libelle = %s
                WHERE famille::text = %s AND famille_libelle IS NULL
            """, (fam_lib, str(fam_id)))

    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sales.produits WHERE marge_pct_calc IS NOT NULL")
        n = cur.fetchone()[0]
    log.info(f"  sales.produits: {n} products enriched with margin/flags/specs")
    return n


# ── 10. Enrich sales.agents ───────────────────────────────────────────────────

def enrich_agents(conn) -> int:
    """
    Set realistic hire dates, certifications, and quotas based on
    performance level and store CA ranking.
    """
    with conn.cursor() as cur:
        # Hire dates based on performance_level (rough estimation)
        cur.execute("""
            UPDATE sales.agents SET
                date_embauche = CASE performance_level
                    WHEN 'senior'   THEN (CURRENT_DATE - INTERVAL '4 years' - (RANDOM()*365||' days')::INTERVAL)::DATE
                    WHEN 'confirmed' THEN (CURRENT_DATE - INTERVAL '2 years' - (RANDOM()*365||' days')::INTERVAL)::DATE
                    WHEN 'junior'   THEN (CURRENT_DATE - INTERVAL '6 months' - (RANDOM()*180||' days')::INTERVAL)::DATE
                    ELSE (CURRENT_DATE - INTERVAL '1 year' - (RANDOM()*365||' days')::INTERVAL)::DATE
                END,
                niveau_certification = CASE performance_level
                    WHEN 'senior'   THEN 4 + (RANDOM() * 1)::INT
                    WHEN 'confirmed' THEN 3
                    WHEN 'junior'   THEN 1 + (RANDOM() * 1)::INT
                    ELSE 2
                END,
                quota_mensuel_ca = CASE performance_level
                    WHEN 'senior'   THEN 12000 + (RANDOM() * 3000)::NUMERIC(10,2)
                    WHEN 'confirmed' THEN 8000 + (RANDOM() * 2000)::NUMERIC(10,2)
                    WHEN 'junior'   THEN 4000 + (RANDOM() * 1500)::NUMERIC(10,2)
                    ELSE 6000 + (RANDOM() * 2000)::NUMERIC(10,2)
                END,
                quota_activations = CASE performance_level
                    WHEN 'senior'   THEN 90 + (RANDOM() * 30)::INT
                    WHEN 'confirmed' THEN 60 + (RANDOM() * 20)::INT
                    WHEN 'junior'   THEN 30 + (RANDOM() * 20)::INT
                    ELSE 50 + (RANDOM() * 20)::INT
                END,
                quota_postpaye = CASE performance_level
                    WHEN 'senior'   THEN 15
                    WHEN 'confirmed' THEN 10
                    WHEN 'junior'   THEN 5
                    ELSE 8
                END,
                anciennete_mois = CASE performance_level
                    WHEN 'senior'   THEN 48 + (RANDOM() * 24)::INT
                    WHEN 'confirmed' THEN 24 + (RANDOM() * 12)::INT
                    WHEN 'junior'   THEN 3 + (RANDOM() * 9)::INT
                    ELSE 12 + (RANDOM() * 12)::INT
                END,
                coach_score = CASE performance_level
                    WHEN 'senior'   THEN 75 + (RANDOM() * 25)::NUMERIC(5,2)
                    WHEN 'confirmed' THEN 55 + (RANDOM() * 25)::NUMERIC(5,2)
                    WHEN 'junior'   THEN 25 + (RANDOM() * 30)::NUMERIC(5,2)
                    ELSE 40 + (RANDOM() * 30)::NUMERIC(5,2)
                END
            WHERE date_embauche IS NULL
        """)
        n = cur.rowcount

    conn.commit()
    log.info(f"  sales.agents: {n} agents enriched with dates/quotas")
    return n


# ── 11. Compute Reorder Parameters ────────────────────────────────────────────

def compute_reorder_params(conn) -> int:
    """
    Pre-compute EOQ and reorder points for each (sku, store_id) pair
    using the last 90 days of sales data.
    """
    with conn.cursor() as cur:
        cur.execute("DELETE FROM supply.reorder_params")
        cur.execute("""
            INSERT INTO supply.reorder_params
                (sku, store_id, demande_moy_jour, demande_std_jour,
                 lead_time_moy, lead_time_std, stock_securite, point_commande, eoq,
                 niveau_service, jours_stock_cible)
            SELECT
                t.sku,
                t.store_id,
                AVG(daily.qty)::NUMERIC(10,4)                   AS demande_moy,
                COALESCE(STDDEV(daily.qty),0)::NUMERIC(10,4)    AS demande_std,
                COALESCE(p.lead_time_days, 14)::NUMERIC(8,2)    AS lt_moy,
                COALESCE(p.lead_time_std, 3)::NUMERIC(8,2)      AS lt_std,
                -- Safety stock: z=1.65 for 95% service level
                CEIL(1.65 * COALESCE(STDDEV(daily.qty),0) *
                     SQRT(COALESCE(p.lead_time_days,14)))        AS ss,
                -- Reorder point = demand*LT + safety_stock
                CEIL(AVG(daily.qty) * COALESCE(p.lead_time_days,14) +
                     1.65 * COALESCE(STDDEV(daily.qty),0) *
                     SQRT(COALESCE(p.lead_time_days,14)))        AS rop,
                -- EOQ = sqrt(2 * D * S / H)
                GREATEST(1, CEIL(SQRT(
                    2 * AVG(daily.qty) * 365 *
                    COALESCE(p.order_cost, 50) /
                    (COALESCE(p.prix_ttc,100) * 0.20)
                )))                                              AS eoq,
                0.95,
                30
            FROM (
                SELECT store_id, sku,
                       date_only AS day,
                       SUM(quantity) AS qty
                FROM sales.transactions
                WHERE transaction_date >= CURRENT_DATE - INTERVAL '90 days'
                  AND quantity > 0
                GROUP BY store_id, sku, date_only
            ) daily
            JOIN (
                SELECT DISTINCT store_id, sku
                FROM sales.transactions
            ) t ON t.store_id=daily.store_id AND t.sku=daily.sku
            JOIN sales.produits p ON p.sku = t.sku
            WHERE p.stockable = TRUE
            GROUP BY t.sku, t.store_id, p.lead_time_days, p.lead_time_std,
                     p.order_cost, p.prix_ttc
            HAVING AVG(daily.qty) > 0
        """)
        n = cur.rowcount
    conn.commit()
    log.info(f"  supply.reorder_params: {n} (sku, store) params computed")
    return n


# ── 12. Backfill Stock Movements from transactions ────────────────────────────

def backfill_stock_movements(conn, limit_days: int = 30) -> int:
    """
    Create VENTE entries in supply.stock_movements from recent transactions.
    Only runs for stockable products (terminals, accessories).
    Skips if table already has recent data.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM supply.stock_movements WHERE date_mouvement > NOW() - INTERVAL '7 days'")
        existing = cur.fetchone()[0]
        if existing > 0:
            log.info(f"  supply.stock_movements: {existing} recent rows already exist, skipping backfill")
            return existing

        cur.execute(f"""
            INSERT INTO supply.stock_movements
                (sku, store_id, type_mouvement, quantite, reference_type,
                 agent_id, date_mouvement)
            SELECT
                t.sku,
                t.store_id,
                'VENTE',
                -t.quantity,
                'VENTE',
                t.agent_id,
                t.transaction_date
            FROM sales.transactions t
            JOIN sales.produits p ON p.sku = t.sku
            WHERE p.stockable = TRUE
              AND t.transaction_date >= CURRENT_DATE - INTERVAL '{limit_days} days'
              AND t.quantity > 0
            ON CONFLICT DO NOTHING
        """)
        n = cur.rowcount
    conn.commit()
    log.info(f"  supply.stock_movements: {n} sale movements backfilled (last {limit_days} days)")
    return n


# ── Main ──────────────────────────────────────────────────────────────────────

def run_all():
    log.info("=== Seed Telco Master — START ===")
    conn = get_conn()
    try:
        log.info("1/10 Market events...")
        seed_market_events(conn)

        log.info("2/10 Seasonal patterns...")
        seed_seasonal_patterns(conn)

        log.info("3/10 Competitors...")
        seed_competitors(conn)

        log.info("4/10 Competitor pricing...")
        seed_competitor_pricing(conn)

        log.info("5/10 MNP flows...")
        seed_mnp_flows(conn)

        log.info("6/10 Suppliers...")
        seed_suppliers(conn)

        log.info("7/10 Customer segments...")
        seed_customer_segments(conn)

        log.info("8/10 Enrich boutiques...")
        enrich_boutiques(conn)

        log.info("9/10 Enrich products (margins + flags)...")
        enrich_produits(conn)

        log.info("10/10 Enrich agents (quotas + certifications)...")
        enrich_agents(conn)

        log.info("BONUS: Compute reorder parameters...")
        compute_reorder_params(conn)

        log.info("BONUS: Backfill stock movements (last 30 days)...")
        backfill_stock_movements(conn, limit_days=30)

    finally:
        conn.close()

    log.info("=== Seed Telco Master — DONE ===")


if __name__ == "__main__":
    run_all()
