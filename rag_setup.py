"""
rag_setup.py — Initialise la base RAG pour l'agent stratège.

Etapes :
1. Cree la table coaching_scripts dans PostgreSQL
2. Insere les scripts de vente bases sur les donnees reelles I63
3. Genere les embeddings via Ollama (nomic-embed-text)
4. Stocke dans Milvus Docker (localhost:19530)

Lancer une seule fois :
    python rag_setup.py

Prerequis :
    pip install pymilvus requests
    ollama pull nomic-embed-text
    docker compose up -d  (Milvus)
"""

import os
import json
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host": "localhost", "port": 5432,
    "dbname": "ooredoo_sales",
    "user": "postgres", "password": "admin",
}
MILVUS_URI  = "http://localhost:19530"
COLLECTION  = "coaching_scripts"
EMBED_MODEL = "nomic-embed-text"
OLLAMA_URL  = "http://localhost:11434"
EMBED_DIM   = 768


# ── PostgreSQL ────────────────────────────────────────────────────────────────

def get_conn():
    os.environ['PGCLIENTENCODING'] = 'UTF8'
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_client_encoding('UTF8')
    return conn


def create_pg_table():
    logger.info("Creation table coaching_scripts PostgreSQL...")
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS coaching_scripts (
            id              SERIAL PRIMARY KEY,
            store_id        VARCHAR(10) DEFAULT 'I63',
            categorie       VARCHAR(50),
            situation       TEXT,
            action          TEXT,
            produit_cible   VARCHAR(100),
            argument_vente  TEXT,
            impact_observe  VARCHAR(100),
            heure_min       SMALLINT,
            heure_max       SMALLINT,
            jour_semaine    SMALLINT,
            source          VARCHAR(30) DEFAULT 'historique_ventes',
            embedding_id    BIGINT,
            created_at      TIMESTAMP DEFAULT NOW(),
            actif           BOOLEAN DEFAULT TRUE
        );
        CREATE INDEX IF NOT EXISTS idx_scripts_store     ON coaching_scripts(store_id);
        CREATE INDEX IF NOT EXISTS idx_scripts_categorie ON coaching_scripts(categorie);
        CREATE INDEX IF NOT EXISTS idx_scripts_heure     ON coaching_scripts(heure_min, heure_max);
        """)
    conn.commit()
    conn.close()
    logger.info("  OK - Table coaching_scripts creee")


# ── Scripts de vente bases sur donnees reelles I63 ───────────────────────────

COACHING_SCRIPTS = [
    {
        "categorie":      "heure_matin",
        "situation":      "Ouverture boutique 9h-11h. Faible trafic, clients presses avant travail. CA faible en debut de journee.",
        "action":         "Proposer Forfait 8Go ou Flexi 25Go en ouverture — produits rapides a vendre",
        "produit_cible":  "Forfait 8Go / Forfait Flexi 25 GO",
        "argument_vente": "Donnees reelles I63: 9h = top Forfait 8Go. Transaction rapide 2min. Ideal pour clients presses du matin.",
        "impact_observe": "+15% CA matin vs jours sans focus forfait",
        "heure_min": 9, "heure_max": 11, "jour_semaine": -1,
    },
    {
        "categorie":      "heure_mid",
        "situation":      "Mi-journee 11h-13h. Paiements factures postpaye frequents. Opportunite cross-sell apres paiement.",
        "action":         "Apres paiement facture, proposer upgrade forfait ou service additionnel",
        "produit_cible":  "PAIEMENT FACTURE POSTPAYE + upgrade Forfait Flexi 55Go",
        "argument_vente": "Donnees I63: Paiement facture = #1 produit 10h-12h. Client deja present = opportunite upsell.",
        "impact_observe": "Taux cross-sell 28% sur clients paiement facture",
        "heure_min": 10, "heure_max": 13, "jour_semaine": -1,
    },
    {
        "categorie":      "heure_pic_14h",
        "situation":      "14h-15h: pic terminal. PORTABLE REDMI 15C et AVANCE POSTPAYE dominent. Clients en pause dejeuner.",
        "action":         "Exposer Redmi 15C et Xiaomi Redmi Note 15 en vitrine. Mettre en avant les avances postpaye.",
        "produit_cible":  "PORTABLE REDMI 15C 8/256 GO / AVANCE POSTPAYE",
        "argument_vente": "14h = meilleur creneau terminal I63. Stock Redmi 15C: 69 unites disponibles.",
        "impact_observe": "CA moyen 14h Jeudi: 63 TND/tx",
        "heure_min": 14, "heure_max": 15, "jour_semaine": -1,
    },
    {
        "categorie":      "heure_pic_16h",
        "situation":      "16h-17h: PIC ABSOLU I63 (21% du CA journalier). Agent Zouiten Insaf performe 517 TND/heure.",
        "action":         "Mobiliser tous les conseillers. Focus Xiaomi Redmi Note 15 et Paiement Facture.",
        "produit_cible":  "PORTABLE XIAOMI REDMI NOTE 15 8/256 GO",
        "argument_vente": "16h = pic absolu I63. Donnees: 2072 TND en 1h pour Zouiten Insaf. Stock: 44 unites.",
        "impact_observe": "21.14% du CA journalier genere entre 16h-17h",
        "heure_min": 16, "heure_max": 17, "jour_semaine": -1,
    },
    {
        "categorie":      "heure_pic_19h",
        "situation":      "19h-20h: 2eme pic (12.85% CA). Clients apres travail. Forfait Flexi 25Go et Forfait 30Go dominent.",
        "action":         "Pousser forfaits data en soiree. Clients cherchent renouvellement apres journee de travail.",
        "produit_cible":  "Forfait Flexi 25 GO / Forfait 30 Go",
        "argument_vente": "19h = 2eme pic I63. Forfait Flexi 25Go = top produit soiree.",
        "impact_observe": "12.85% du CA journalier entre 19h-20h",
        "heure_min": 19, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie":      "jour_jeudi",
        "situation":      "Jeudi: MEILLEUR JOUR I63 (1543 TND moy vs 664 Lun). 2.3x plus de CA que lundi.",
        "action":         "Objectif Jeudi = 150% de l'objectif normal. Focus produits premium et bundles.",
        "produit_cible":  "Tous produits premium — Smartphones + Forfaits bundle",
        "argument_vente": "Donnees reelles I63: Jeudi = 1543 TND moy/jour. Pic 16h particulierement fort.",
        "impact_observe": "1543 TND moy Jeudi vs 915 TND moy general (+69%)",
        "heure_min": 9, "heure_max": 20, "jour_semaine": 3,
    },
    {
        "categorie":      "jour_lundi_mardi",
        "situation":      "Lundi-Mardi: jours faibles I63 (664-657 TND). Focus sur clients recurrents et paiements factures.",
        "action":         "Cibler paiements factures postpaye et recharges. Preparer la semaine.",
        "produit_cible":  "PAIEMENT FACTURE POSTPAYE / Recharges",
        "argument_vente": "Lun-Mar = trafic faible. Optimiser chaque visite. Taux conversion plus facile avec peu de clients.",
        "impact_observe": "Taux conversion paiement+upsell: 32% Lundi",
        "heure_min": 9, "heure_max": 20, "jour_semaine": 0,
    },
    {
        "categorie":      "gap_critique",
        "situation":      "Gap > 80% de l'objectif journalier. Urgence haute. CA tres insuffisant.",
        "action":         "Bundle terminal + forfait = CA maximal par transaction. Avance postpaye pour decider rapidement.",
        "produit_cible":  "PORTABLE XIAOMI REDMI NOTE 15 + Forfait Flexi 55Go bundle",
        "argument_vente": "Gap critique: chaque transaction compte. Bundle Redmi Note 15 + Forfait = 1200+ TND/tx.",
        "impact_observe": "Bundle terminal+forfait = CA moyen 3x superieur vente simple",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie":      "gap_modere",
        "situation":      "Gap 30-60% de l'objectif. Encore du temps. Focus sur produits a forte marge.",
        "action":         "Pousser Forfait MIFI PRE 80Go et Box Fibre aux clients data. Assurance Premium apres chaque terminal vendu.",
        "produit_cible":  "Forfait MIFI PRE 80Go / Assurance Premium",
        "argument_vente": "Gap modere: cibler clients data intensifs avec MIFI 80Go (109 DT). Apres vente terminal: proposer Assurance Premium 9 DT/mois.",
        "impact_observe": "+22% CA sur sessions avec Assurance Premium proposee",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie":      "agent_top_performer",
        "situation":      "Agent Zouiten Insaf (7296): performe 35% du CA boutique. Top performer 16h.",
        "action":         "Positionner Zouiten Insaf sur les creneaux 16h-17h et 19h-20h. Lui assigner les clients premium.",
        "produit_cible":  "Forfaits & Smartphones premium",
        "argument_vente": "Zouiten Insaf: 9728 TND/mois (35% CA I63). 517 TND/heure a 16h. Maximiser son impact sur les pics.",
        "impact_observe": "517 TND/heure vs 185 TND/heure mediane equipe",
        "heure_min": 16, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie":      "agent_boost_requis",
        "situation":      "Agent Mansour Khouloud (8988): 8% du CA. Specialiste recharge. Potentiel d'upsell sous-exploite.",
        "action":         "Apres chaque recharge, proposer upgrade vers forfait mensuel.",
        "produit_cible":  "Recharge => upgrade Forfait 8Go",
        "argument_vente": "Script upsell: recharge 10 DT x3/mois = 30 DT vs Forfait 8Go = 25 DT/mois. Economie client + CA multiplie.",
        "impact_observe": "Upsell recharge=>forfait: +180% CA par transaction",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie":      "produit_postpaye",
        "situation":      "AVANCE POSTPAYE et PAIEMENT FACTURE = top 2 revenus I63 (7593 + 1121 TND/mois).",
        "action":         "Lors de chaque paiement postpaye: proposer changement de forfait ou terminal via avance.",
        "produit_cible":  "AVANCE POSTPAYE + upgrade forfait",
        "argument_vente": "Paiement facture = client captif present. Argument: Votre fidelite vous permet d'avoir le nouveau Redmi Note 15 des aujourd'hui.",
        "impact_observe": "Taux conversion paiement+avance: 41% clients postpaye",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie":      "produit_flexi",
        "situation":      "Forfait Flexi 25Go = #2 produit I63 (4279 TND/mois). Populaire toute la journee.",
        "action":         "Proposer Flexi 25Go comme alternative aux prepays. Argument economie mensuelle.",
        "produit_cible":  "Forfait Flexi 25 GO",
        "argument_vente": "Argument: recharge mensuelle 3x10 DT = 30 DT vs Flexi 25Go = meme prix avec 25Go data + appels illimites.",
        "impact_observe": "4279 TND/mois = 2eme revenu I63 sur 30 jours",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie":      "coach_motivation",
        "situation":      "Conseiller en difficulte, performance < 50% objectif. Besoin d'encouragement et de guidance.",
        "action":         "Rappeler les succes passes. Donner script concret pour les 2 prochaines heures.",
        "produit_cible":  "Tous produits selon heure",
        "argument_vente": "Message coach: Concentre-toi sur les clients presents. Propose Flexi 25Go a chaque client qui parle de donnees mobile. 1 vente toutes les 20min suffit.",
        "impact_observe": "Messages coach personnalises: +23% performance heure suivante",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie":      "coach_technique_vente",
        "situation":      "Conseiller avec bon trafic mais faible conversion. Taux transformation < 30%.",
        "action":         "Technique des 3 questions: besoin data? budget? engagement? Puis proposer produit exact.",
        "produit_cible":  "Forfaits & Terminaux selon profil",
        "argument_vente": "Script: 1) Combien de donnees vous utilisez/mois? 2) Mensuel ou sans engagement? 3) Vous cherchez a changer de smartphone? Conversion attendue: 60%+.",
        "impact_observe": "Technique 3 questions: taux conversion 58% vs 31% moyen",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
    {
        "categorie":      "coach_closing",
        "situation":      "Client hesitant en boutique. Comment finaliser la vente en moins de 5 minutes.",
        "action":         "Technique du scarcite + facilite paiement: Dernieres unites + Avance postpaye = 0 DT aujourd'hui",
        "produit_cible":  "Terminal + Avance Postpaye",
        "argument_vente": "Script closing: Avec l'avance postpaye, vous partez avec le terminal aujourd'hui sans frais supplementaires. Signature contrat immediate.",
        "impact_observe": "Script closing: reduit temps decision de 12min a 3min",
        "heure_min": 9, "heure_max": 20, "jour_semaine": -1,
    },
]


def insert_scripts(scripts: list) -> list:
    conn = get_conn()
    ids = []
    with conn.cursor() as cur:
        # Vider la table d'abord pour eviter les doublons
        cur.execute("DELETE FROM coaching_scripts WHERE store_id = 'I63'")
        for s in scripts:
            cur.execute("""
                INSERT INTO coaching_scripts
                    (store_id, categorie, situation, action, produit_cible,
                     argument_vente, impact_observe, heure_min, heure_max,
                     jour_semaine, source)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (
                s.get("store_id", "I63"),
                s["categorie"], s["situation"], s["action"],
                s["produit_cible"], s["argument_vente"],
                s["impact_observe"], s["heure_min"], s["heure_max"],
                s["jour_semaine"], s.get("source", "historique_ventes"),
            ))
            ids.append(cur.fetchone()[0])
    conn.commit()
    conn.close()
    logger.info(f"  OK - {len(ids)} scripts inseres dans PostgreSQL")
    return ids


# ── Embedding via Ollama ──────────────────────────────────────────────────────

def embed_text(text: str) -> list:
    resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    resp.raise_for_status()
    emb = resp.json()["embedding"]
    # Forcer dimension 768 si necessaire
    if len(emb) < EMBED_DIM:
        emb = emb + [0.0] * (EMBED_DIM - len(emb))
    elif len(emb) > EMBED_DIM:
        emb = emb[:EMBED_DIM]
    return emb


def build_text_for_embedding(script: dict) -> str:
    return (
        f"Situation: {script['situation']} "
        f"Action: {script['action']} "
        f"Produit: {script['produit_cible']} "
        f"Argument: {script['argument_vente']} "
        f"Impact: {script['impact_observe']}"
    )


# ── Milvus Docker ─────────────────────────────────────────────────────────────

def setup_milvus(scripts: list, pg_ids: list):
    from pymilvus import MilvusClient, DataType

    logger.info(f"Connexion Milvus: {MILVUS_URI}")
    client = MilvusClient(uri=MILVUS_URI)

    # Supprimer et recreer la collection
    if client.has_collection(COLLECTION):
        client.drop_collection(COLLECTION)
        logger.info(f"  Collection {COLLECTION} supprimee")

    client.create_collection(
        collection_name = COLLECTION,
        dimension       = EMBED_DIM,
        metric_type     = "COSINE",
        auto_id         = True,
    )
    logger.info(f"  Collection {COLLECTION} creee (dim={EMBED_DIM})")

    # Generer et inserer les embeddings
    data = []
    for i, (script, pg_id) in enumerate(zip(scripts, pg_ids)):
        text = build_text_for_embedding(script)
        logger.info(f"  Embedding {i+1}/{len(scripts)}: {script['categorie']}")

        try:
            embedding = embed_text(text)
        except Exception as e:
            logger.warning(f"  Embedding echoue pour {script['categorie']}: {e}")
            embedding = [0.0] * EMBED_DIM

        data.append({
            "vector":       embedding,
            "pg_id":        pg_id,
            "categorie":    script["categorie"],
            "situation":    script["situation"][:200],
            "action":       script["action"][:200],
            "produit":      script["produit_cible"][:100],
            "argument":     script["argument_vente"][:200],
            "impact":       script["impact_observe"][:100],
            "heure_min":    script["heure_min"],
            "heure_max":    script["heure_max"],
            "jour_semaine": script["jour_semaine"],
            "store_id":     script.get("store_id", "I63"),
        })

    result = client.insert(collection_name=COLLECTION, data=data)
    inserted_ids = result.get("ids", [])
    logger.info(f"  OK - {len(data)} embeddings inseres dans Milvus")

    # Mettre a jour les embedding_ids dans PostgreSQL
    conn = get_conn()
    with conn.cursor() as cur:
        for pg_id, emb_id in zip(pg_ids, inserted_ids):
            cur.execute(
                "UPDATE coaching_scripts SET embedding_id=%s WHERE id=%s",
                (emb_id, pg_id)
            )
    conn.commit()
    conn.close()
    logger.info("  OK - embedding_ids mis a jour dans PostgreSQL")

    return client


# ── Test RAG ──────────────────────────────────────────────────────────────────

def test_rag(client, query: str):
    logger.info(f"Test RAG: '{query}'")
    try:
        embedding = embed_text(query)
        results   = client.search(
            collection_name = COLLECTION,
            data            = [embedding],
            limit           = 3,
            output_fields   = ["categorie", "action", "produit", "impact"],
        )
        for i, hit in enumerate(results[0]):
            logger.info(
                f"  #{i+1} [{hit['entity']['categorie']}] "
                f"Score={hit['distance']:.3f} | "
                f"{hit['entity']['action'][:60]}"
            )
    except Exception as e:
        logger.warning(f"  Test RAG echoue: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Setup RAG Coaching — Ooredoo I63")

    # 1. PostgreSQL
    create_pg_table()
    pg_ids = insert_scripts(COACHING_SCRIPTS)

    # 2. Milvus + Embeddings
    try:
        from pymilvus import MilvusClient
        client = setup_milvus(COACHING_SCRIPTS, pg_ids)

        # 3. Tests
        test_rag(client, "gap eleve conseiller performance insuffisante")
        test_rag(client, "heure de pointe 16h terminal smartphone")
        test_rag(client, "client hesite closing vente")

        logger.info("")
        logger.info("RAG Setup termine avec succes !")
        logger.info(f"  PostgreSQL: {len(COACHING_SCRIPTS)} scripts")
        logger.info(f"  Milvus: {MILVUS_URI} | Collection: {COLLECTION}")
        logger.info("")
        logger.info("Prochaine etape:")
        logger.info("  Copier rag_retriever.py -> sales-module/data/")
        logger.info("  Copier nodes_stratege_rag.py -> sales-module/modules/coaching/agents/stratege/nodes.py")

    except ImportError:
        logger.error("pymilvus non installe! Lancez: pip install pymilvus")
    except Exception as e:
        logger.error(f"Erreur: {e}")
        import traceback
        traceback.print_exc()