"""
rag/ingest.py — Construction et indexation des quatre corpus.

    python -m app.sales.data.rag.ingest --all
    python -m app.sales.data.rag.ingest --domain product --domain decision
    python -m app.sales.data.rag.ingest --all --recreate      # repart de zéro

Le `doc_id` est déterministe (script:closing:03, product:SKU123, cycle:abc…) :
relancer l'ingestion met à jour les lignes existantes au lieu de les dupliquer.
C'est ce qui manquait à l'ancien seed — la collection `coaching_scripts` contient
1 653 lignes pour 86 scripts, soit 19 copies de chaque.
"""

import argparse
import logging
import sys
import time
from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from app.core.config import DEFAULT_STORE_ID
from app.core.db import get_conn
from app.sales.data.rag import store
from app.sales.data.rag.documents import Document
from app.sales.data.rag.settings import (
    DOMAIN_DECISION,
    DOMAIN_INVENTORY_PLAYBOOK,
    DOMAIN_PRODUCT,
    DOMAIN_SALES_SCRIPT,
)

logger = logging.getLogger(__name__)


def _slug(text: str, n: int = 40) -> str:
    keep = [c if c.isalnum() else "_" for c in (text or "").lower()]
    return "".join(keep)[:n].strip("_")


def _join(*parts) -> str:
    """Concatène les champs indexés. Postgres renvoie des int/Decimal/None."""
    return " | ".join(str(p) for p in parts if p not in (None, "", 0))


@contextmanager
def _cursor():
    """get_conn() yield une connexion ; les requêtes ici veulent un RealDictCursor."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur


# ══════════════════════════════════════════════════════════════════════════════
# 1. SCRIPTS DE VENTE (corpus embarqué)
# ══════════════════════════════════════════════════════════════════════════════

def build_sales_scripts() -> list[Document]:
    from app.sales.data.seeds.seed_rag_scripts import SCRIPTS

    docs: list[Document] = []
    for i, s in enumerate(SCRIPTS):
        # Le texte indexé concatène tout ce qui porte du sens pour la recherche :
        # la situation (ce que décrit le conseiller), l'action et l'argument
        # (le vocabulaire du corpus qu'on veut faire matcher).
        text = _join(
            s.get("categorie"), s.get("situation"), s.get("action"),
            s.get("produit"), s.get("argument"), s.get("impact"),
        )
        docs.append(Document(
            doc_id=f"script:{_slug(s.get('categorie',''))}:{i:03d}",
            domain=DOMAIN_SALES_SCRIPT,
            doc_type=s.get("categorie", ""),
            title=s.get("situation", "")[:200],
            text=text,
            categorie=s.get("categorie", ""),
            produit=s.get("produit", ""),
            heure_min=int(s.get("heure_min", 0)),
            heure_max=int(s.get("heure_max", 24)),
            jour_semaine=int(s.get("jour_semaine", -1)),
            payload={
                "situation": s.get("situation", ""),
                "action":    s.get("action", ""),
                "argument":  s.get("argument", ""),
                "impact":    s.get("impact", ""),
                "produit":   s.get("produit", ""),
            },
        ))
    return docs


# ══════════════════════════════════════════════════════════════════════════════
# 2. PLAYBOOKS STOCK (corpus embarqué)
# ══════════════════════════════════════════════════════════════════════════════

def build_inventory_playbooks() -> list[Document]:
    from app.sales.data.rag.corpora.inventory_playbooks import PLAYBOOKS

    docs: list[Document] = []
    for i, p in enumerate(PLAYBOOKS):
        text = _join(
            p.get("categorie"), p.get("doc_type"), p.get("situation"),
            p.get("action"), p.get("regle"), p.get("impact"),
        )
        docs.append(Document(
            doc_id=f"playbook:{_slug(p.get('doc_type',''))}:{i:03d}",
            domain=DOMAIN_INVENTORY_PLAYBOOK,
            doc_type=p.get("doc_type", ""),
            title=p.get("situation", "")[:200],
            text=text,
            categorie=p.get("categorie", ""),
            produit=p.get("produit", ""),
            heure_min=int(p.get("heure_min", 0)),
            heure_max=int(p.get("heure_max", 24)),
            payload={
                "situation": p.get("situation", ""),
                "action":    p.get("action", ""),
                "regle":     p.get("regle", ""),
                "impact":    p.get("impact", ""),
            },
        ))
    return docs


# ══════════════════════════════════════════════════════════════════════════════
# 3. CATALOGUE PRODUITS (PostgreSQL)
# ══════════════════════════════════════════════════════════════════════════════

# Pas de filtre sur `p.actif` : la colonne est trompeuse (2 303 terminaux y sont
# marqués inactifs, dont tous les iPhone 16 Pro, alors qu'ils sont vendus et
# stockés). On expose `actif` + le volume vendu dans le payload : c'est le
# reranker, pas le SQL, qui arbitre la pertinence.
#
# On écarte en revanche ce dont un coach ne peut rien dire : les 25 références
# dont le nom est vide ou « . », et celles qui cumulent zéro vente, zéro stock
# et prix nul. Indexées, elles remontaient devant de vrais scripts de vente
# parce que leur texte court gonfle artificiellement le cosinus.
_SQL_PRODUCTS = """
    WITH ventes AS (
        SELECT sku, SUM(quantity) AS qte_30j
          FROM sales.transactions
         WHERE transaction_date >= NOW() - INTERVAL '30 days'
         GROUP BY sku
    ),
    ventes_totales AS (
        SELECT sku, SUM(quantity) AS qte_totale
          FROM sales.transactions
         GROUP BY sku
    )
    SELECT p.sku, p.nom, p.categorie, p.famille, p.gamme_libelle, p.marque, p.modele,
           p.prix_ttc, p.lifecycle_stage, p.actif,
           -- marge_pct est NULL sur 4 115 des 4 593 references ; marge_pct_calc
           -- porte la vraie valeur (20 pour cent sur l'iPhone 16 Pro). Sans ce
           -- COALESCE, le coach annoncait une marge nulle en citant sa fiche.
           -- (Pas de signe pourcent dans cette requete : psycopg2 le lit comme
           --  un debut de placeholder et leve "dict is not a sequence".)
           COALESCE(p.marge_pct, p.marge_pct_calc) AS marge_pct,
           p.flag_5g, p.flag_terminal, p.flag_forfait, p.stockage_gb, p.ram_gb,
           v.quantity_available, v.stock_status,
           pr.discount_pct, pr.promo_name,
           COALESCE(ve.qte_30j, 0) AS qte_30j
      FROM sales.produits p
      LEFT JOIN inventory.vw_stock_enriched v
             ON v.sku = p.sku AND v.store_id = %(store)s
      LEFT JOIN inventory.vw_active_promotions pr
             ON pr.sku = p.sku
      LEFT JOIN ventes ve ON ve.sku = p.sku
      LEFT JOIN ventes_totales vt ON vt.sku = p.sku
     WHERE LENGTH(BTRIM(COALESCE(p.nom, ''))) >= 3
       AND (COALESCE(vt.qte_totale, 0) > 0
            OR COALESCE(v.quantity_available, 0) > 0
            OR COALESCE(p.prix_ttc, 0) > 0)
     ORDER BY COALESCE(ve.qte_30j, 0) DESC, v.quantity_available DESC NULLS LAST
     LIMIT %(limit)s
"""


def build_products(store_id: str = DEFAULT_STORE_ID, limit: int = 5000) -> list[Document]:
    docs: list[Document] = []
    now = int(time.time())

    with _cursor() as cur:
        cur.execute(_SQL_PRODUCTS, {"store": store_id, "limit": limit})
        rows = cur.fetchall()

    for r in rows:
        specs = []
        if r["stockage_gb"]:
            specs.append(f"{r['stockage_gb']} Go")
        if r["ram_gb"]:
            specs.append(f"{r['ram_gb']} Go RAM")
        if r["flag_5g"]:
            specs.append("5G")

        promo_active = r["discount_pct"] is not None
        kind = ("terminal" if r["flag_terminal"] else
                "forfait" if r["flag_forfait"] else "accessoire")

        # Le texte inclut prix et specs : une requête « iPhone 5G pas cher »
        # doit matcher lexicalement (BM25) autant que sémantiquement.
        marge = float(r["marge_pct"]) if r["marge_pct"] is not None else None

        text = _join(
            r["nom"], r["marque"], r["modele"], r["categorie"], r["famille"],
            r["gamme_libelle"], kind, " ".join(specs),
            f"sku {r['sku']}", f"prix {r['prix_ttc']} TND",
            f"marge {marge}%" if marge is not None else "",
            f"promotion {r['promo_name']}" if promo_active else "",
        )

        docs.append(Document(
            doc_id=f"product:{r['sku']}",
            domain=DOMAIN_PRODUCT,
            doc_type=kind,
            title=r["nom"] or r["sku"],
            text=text,
            categorie=r["categorie"] or "",
            produit=r["nom"] or "",
            sku=r["sku"],
            store_id=store_id,
            updated_at=now,
            payload={
                "prix_ttc":     float(r["prix_ttc"] or 0),
                # None (et non 0) quand la marge est inconnue : « marge 0 % » est
                # une information fausse que le coach citerait comme un fait.
                "marge_pct":    marge,
                "stock_dispo":  int(r["quantity_available"]) if r["quantity_available"] is not None else None,
                "stock_status": r["stock_status"] or "unknown",
                "promo_active": promo_active,
                "discount_pct": float(r["discount_pct"]) if promo_active else None,
                "lifecycle":    r["lifecycle_stage"] or "",
                "famille":      r["famille"] or "",
                # gamme_libelle ('TERMINAL', 'SIM_KIT', ...) : seul champ qui
                # sépare vraiment les types de produits. `famille` est un code
                # qui range les téléphones et les kits SIM ensemble.
                "gamme":        r["gamme_libelle"] or "",
                "actif":        bool(r["actif"]),
                "qte_30j":      int(r["qte_30j"] or 0),
            },
        ))
    return docs


# ══════════════════════════════════════════════════════════════════════════════
# 4. MÉMOIRE DES DÉCISIONS (PostgreSQL)
# ══════════════════════════════════════════════════════════════════════════════

_SQL_CYCLES = """
    SELECT cycle_id, store_id, strategie, cause_racine, gap_pct, urgency_level,
           nb_actions, status, created_at
      FROM monitoring.cycle_logs
     WHERE strategie IS NOT NULL AND strategie <> ''
     ORDER BY created_at DESC
     LIMIT %(limit)s
"""

_SQL_RECOS = """
    SELECT id, sku, store_id, recommendation_type, action, order_qty, urgency,
           confidence, recommendation_text, status, created_at
      FROM inventory.recommendations
     WHERE recommendation_text IS NOT NULL AND recommendation_text <> ''
     ORDER BY created_at DESC
     LIMIT %(limit)s
"""

_SQL_COACHING = """
    SELECT id, advisor_id, store_id, advice_text, produit_a_pousser,
           urgency_level, gap_pct, was_effective, feedback_score, created_at
      FROM monitoring.coaching_interactions
     WHERE advice_text IS NOT NULL AND advice_text <> ''
       AND (was_effective IS NOT NULL OR feedback_score IS NOT NULL)
     ORDER BY created_at DESC
     LIMIT %(limit)s
"""


def build_decisions(cycles: int = 400, recos: int = 300, coaching: int = 200) -> list[Document]:
    docs: list[Document] = []

    with _cursor() as cur:
        cur.execute(_SQL_CYCLES, {"limit": cycles})
        for r in cur.fetchall():
            ts = int(r["created_at"].timestamp()) if r["created_at"] else 0
            text = _join("strategie cycle", r["cause_racine"], r["strategie"],
                         f"gap {r['gap_pct']}%", r["urgency_level"])
            docs.append(Document(
                doc_id=f"cycle:{r['cycle_id']}",
                domain=DOMAIN_DECISION,
                doc_type="strategie_cycle",
                title=f"Stratégie du {r['created_at']:%d/%m %H:%M} (gap {r['gap_pct']}%)"
                      if r["created_at"] else "Stratégie de cycle",
                text=text,
                categorie=r["urgency_level"] or "",
                store_id=r["store_id"] or "",
                updated_at=ts,
                payload={
                    "action": r["strategie"] or "",
                    "impact": f"{r['nb_actions']} actions, statut {r['status']}",
                    "cause_racine": r["cause_racine"] or "",
                    "gap_pct": float(r["gap_pct"] or 0),
                    "updated_at": ts,
                },
            ))

        cur.execute(_SQL_RECOS, {"limit": recos})
        for r in cur.fetchall():
            ts = int(r["created_at"].timestamp()) if r["created_at"] else 0
            text = _join("recommandation stock", r["recommendation_type"], r["action"],
                         r["recommendation_text"], f"sku {r['sku']}", r["urgency"])
            docs.append(Document(
                doc_id=f"reco:{r['id']}",
                domain=DOMAIN_DECISION,
                doc_type="recommandation_stock",
                title=f"{r['recommendation_type'] or 'Reco'} sur {r['sku']} ({r['status'] or 'en attente'})",
                text=text,
                categorie=r["urgency"] or "",
                sku=r["sku"] or "",
                store_id=r["store_id"] or "",
                updated_at=ts,
                payload={
                    "action": r["recommendation_text"] or "",
                    "impact": (f"qty {r['order_qty']}, confiance {r['confidence']}, "
                               f"statut {r['status'] or 'en attente'}"),
                    "updated_at": ts,
                },
            ))

        cur.execute(_SQL_COACHING, {"limit": coaching})
        for r in cur.fetchall():
            ts = int(r["created_at"].timestamp()) if r["created_at"] else 0
            # Un conseil noté inefficace reste indexé : le coach doit pouvoir
            # apprendre de ce qui n'a PAS marché, pas seulement des succès.
            verdict = ("conseil efficace" if r["was_effective"]
                       else "conseil inefficace" if r["was_effective"] is False
                       else "conseil non evalue")
            text = _join("coaching passe", verdict, r["advice_text"],
                         r["produit_a_pousser"], r["urgency_level"])
            docs.append(Document(
                doc_id=f"coaching:{r['id']}",
                domain=DOMAIN_DECISION,
                doc_type="coaching_passe",
                title=f"Conseil {verdict} ({r['created_at']:%d/%m})"
                      if r["created_at"] else f"Conseil {verdict}",
                text=text,
                categorie=r["urgency_level"] or "",
                produit=r["produit_a_pousser"] or "",
                store_id=r["store_id"] or "",
                updated_at=ts,
                payload={
                    "action": r["advice_text"] or "",
                    "impact": (f"{verdict}"
                               + (f", note {r['feedback_score']}/5" if r["feedback_score"] else "")),
                    "was_effective": r["was_effective"],
                    "updated_at": ts,
                },
            ))

    return docs


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

_BUILDERS = {
    DOMAIN_SALES_SCRIPT:       build_sales_scripts,
    DOMAIN_INVENTORY_PLAYBOOK: build_inventory_playbooks,
    DOMAIN_PRODUCT:            build_products,
    DOMAIN_DECISION:           build_decisions,
}


def ingest(domains: list[str], recreate: bool = False, purge: bool = False) -> dict:
    client = store.get_client(recreate=recreate)
    if client is None:
        logger.error("[RAG] Milvus indisponible — ingestion annulée")
        return {}

    report: dict[str, int] = {}
    for domain in domains:
        started = time.perf_counter()
        docs = _BUILDERS[domain]()
        if purge and not recreate:
            store.delete_domain(domain)
        written = store.upsert(docs)
        report[domain] = written
        logger.info("[RAG] %-20s %4d docs indexés en %.1fs",
                    domain, written, time.perf_counter() - started)
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    parser = argparse.ArgumentParser(description="Ingestion RAG retail_knowledge")
    parser.add_argument("--all", action="store_true", help="tous les domaines")
    parser.add_argument("--domain", action="append", choices=list(_BUILDERS),
                        default=[], help="domaine à (ré)indexer, répétable")
    parser.add_argument("--recreate", action="store_true",
                        help="DROP puis recrée la collection")
    parser.add_argument("--purge", action="store_true",
                        help="supprime les documents du domaine avant réindexation")
    args = parser.parse_args()

    domains = list(_BUILDERS) if args.all else args.domain
    if not domains:
        parser.error("préciser --all ou au moins un --domain")

    report = ingest(domains, recreate=args.recreate, purge=args.purge)
    if not report:
        return 1

    # ASCII uniquement : la console Windows par defaut est en cp1252.
    print("\n-- Ingestion terminee --")
    for domain, n in report.items():
        print(f"  {domain:<22} {n:>5} documents")
    print(f"\n  collection : {store.stats()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
