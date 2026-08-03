"""
json_service.py — 100% PostgreSQL. Remplace tous les fichiers JSON mock.
Interface identique à l'ancien JsonDataService.
"""
from pathlib import Path
import logging
import os
from datetime import datetime, date 
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from app.core.config import DEFAULT_STORE_ID
# Load .env from project root
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

# ── Config PostgreSQL ─────────────────────────────────────────────────────────
_DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

# ── Mapping store_id → CD_DIST PostgreSQL ─────────────────────────────────────
_STORE_MAP = {
    "store-lac2":    DEFAULT_STORE_ID,
    "store-menzah":  "M23",
    "store-sfax":    "M03",
    "OOR_LAC_01":    DEFAULT_STORE_ID,
    "OOR_MENZAH_02": "M23",
    "OOR_SFAX_03":   "M03",
}

# ── Agents par boutique ───────────────────────────────────────────────────────
_AGENTS = {
    "I63": [
        {"agent_id":"7296","id":"adv-zi","advisor_code":"zi","name":"Zouiten Insaf",    "initials":"ZI","role":"Forfaits & Services",    "avatar_color":"#6C5CE7","weight":0.35,"coach_score":0.88},
        {"agent_id":"8987","id":"adv-mh","advisor_code":"mh","name":"Mansour Hela",     "initials":"MH","role":"Postpaye & Terminaux",   "avatar_color":"#00B894","weight":0.32,"coach_score":0.75},
        {"agent_id":"7451","id":"adv-bm","advisor_code":"bm","name":"Ben Ammar Meriam", "initials":"BM","role":"Smartphones & Data",     "avatar_color":"#F9A825","weight":0.25,"coach_score":0.62},
        {"agent_id":"8988","id":"adv-mk","advisor_code":"mk","name":"Mansour Khouloud","initials":"MK","role":"Recharge & Accessoires",  "avatar_color":"#2D9CDB","weight":0.08,"coach_score":0.41},
    ],
    "M23": [
        {"agent_id":"1104","id":"adv-dn","advisor_code":"dn","name":"Dridi Nabil",           "initials":"DN","role":"Postpaye Premium",   "avatar_color":"#6C5CE7","weight":0.30,"coach_score":0.82},
        {"agent_id":"0600","id":"adv-ba","advisor_code":"ba","name":"Benabdallah Abdelhedi","initials":"BA","role":"Forfaits Business",   "avatar_color":"#00B894","weight":0.28,"coach_score":0.74},
        {"agent_id":"0996","id":"adv-bi","advisor_code":"bi","name":"Ben Fadhla Issam",     "initials":"BI","role":"Smartphones",         "avatar_color":"#F9A825","weight":0.24,"coach_score":0.65},
        {"agent_id":"0440","id":"adv-ef","advisor_code":"ef","name":"Elmay Fethi",          "initials":"EF","role":"Accessoires",         "avatar_color":"#2D9CDB","weight":0.18,"coach_score":0.51},
    ],
    "M03": [
        {"agent_id":"3703","id":"adv-zi2","advisor_code":"zi2","name":"Zouari Imen",     "initials":"ZI","role":"Smartphones & Forfaits","avatar_color":"#6C5CE7","weight":0.45,"coach_score":0.85},
        {"agent_id":"1447","id":"adv-tm", "advisor_code":"tm", "name":"Trabelsi Med Ali","initials":"TM","role":"Postpaye",              "avatar_color":"#00B894","weight":0.24,"coach_score":0.70},
        {"agent_id":"2607","id":"adv-ra", "advisor_code":"ra", "name":"Rekhiss Aymen",   "initials":"RA","role":"Internet & Box",        "avatar_color":"#F9A825","weight":0.17,"coach_score":0.58},
        {"agent_id":"2664","id":"adv-ks", "advisor_code":"ks", "name":"Kharrat Slim",    "initials":"KS","role":"Recharge",             "avatar_color":"#2D9CDB","weight":0.14,"coach_score":0.44},
    ],
}

_STORE_NAMES = {
    "I63": "FR LAC2 TUNISIA MALL",
    "M23": "Boutique HABIB BOURGUIBA",
    "M03": "Boutique SFAX I",
}

# ── Objectifs par défaut (si table objectifs vide) ────────────────────────────
_OBJECTIFS_DEFAUT = {
    "I63": 1007.0,
    "M23": 2047.0,
    "M03": 1057.0,
}

# Target uplift over historical average
TARGET_UPLIFT = 1.10

# ── Helpers SQL ───────────────────────────────────────────────────────────────

def _get_conn():
    """
    Connexion directe hors pool. Conservée pour les appelants qui gèrent
    eux-mêmes le cycle de vie (scripts, seeds). Le chemin chaud du dashboard
    passe par _query(), qui utilise le pool partagé.
    """
    os.environ['PGCLIENTENCODING'] = 'UTF8'
    conn = psycopg2.connect(**_DB_CONFIG)
    conn.set_client_encoding('UTF8')
    return conn


def _query(sql: str, params=None) -> list:
    """
    SELECT sur une connexion du pool partagé (app.core.db).

    Auparavant chaque appel ouvrait puis fermait une connexion PostgreSQL :
    ~30-130 ms de handshake TCP+auth par requête, et get_store_metrics() seul
    en enchaîne quatre. Le pool ramène ce coût à zéro sur le chemin chaud.
    Le search_path par défaut du pool est identique à celui d'une connexion
    neuve, donc les requêtes non qualifiées (stock, boutiques) résolvent pareil.
    """
    from app.core.db import getconn, putconn

    conn = None
    try:
        conn = getconn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        # Termine la transaction implicite : sans ça la connexion repart au pool
        # en « idle in transaction » et bloque VACUUM côté serveur.
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error(f"[PG] Erreur: {e} | SQL: {sql[:80]}")
        return []
    finally:
        if conn is not None:
            putconn(conn)


def _query_one(sql: str, params=None) -> dict:
    rows = _query(sql, params)
    return rows[0] if rows else {}


# ── Source unique des ventes du jour ──────────────────────────────────────────
#
# Les ventes du jour vivent dans DEUX tables :
#   - sales.transactions     : l'historique importé (s'arrête à la dernière
#                              journée réellement chargée)
#   - sales.transactions_rt  : ce que le RealtimeSimulator encaisse en direct
#
# Le dashboard ne lisait que la première. Comme elle est vide pour aujourd'hui,
# tout le calcul du jour retombait sur des replis : CA figé sur la dernière
# journée connue, et — pire — un CA par conseiller *fabriqué* en répartissant
# le total au prorata des poids du roster. Cette répartition rendait
# mécaniquement ca/cible identique pour tout le monde (ca_total*w / (obj*w)),
# d'où les 127,4 % affichés pour les quatre conseillers, avec « 0 vente » à
# côté puisque le comptage, lui, n'avait pas de repli.
#
# Cette CTE réunit les deux sources. Tout ce qui concerne « aujourd'hui » doit
# passer par elle, sinon l'écran redevient incohérent avec le ticker temps réel.
# Elle attend le store_id DEUX fois en paramètre.
_VENTES_JOUR = """
    WITH ventes_jour AS (
        SELECT agent_id::text AS agent_id, sku::text AS sku,
               lig_ttc, quantity AS units, heure, transaction_date AS ts,
               NULL::text AS des_produit
        FROM sales.transactions
        WHERE store_id = %s AND date_only = CURRENT_DATE AND lig_ttc > 0
        UNION ALL
        SELECT agent_id::text, cod_prod::text,
               lig_ttc, qte_produit AS units, heure, date_vente AS ts,
               des_produit
        FROM sales.transactions_rt
        WHERE store_id = %s AND date_only = CURRENT_DATE AND lig_ttc > 0
    )
"""


# ── Service ───────────────────────────────────────────────────────────────────

class JsonDataService:
    """
    Service de données 100% PostgreSQL.
    Interface identique à l'ancien JsonDataService basé sur JSON mock.
    """

    def __init__(self, store_id: str = DEFAULT_STORE_ID):
        self._cd  = _STORE_MAP.get(store_id, store_id)
        # Roster de secours si la boutique est absente du dict local
        # (les vrais rosters vivent dans sales.agents — cf. DATA_GAPS.md)
        self._agents = _AGENTS.get(self._cd, next(iter(_AGENTS.values())))
        logger.info(f"[PG] JsonDataService — store={self._cd}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _objectif(self) -> float:
        row = _query_one(
            "SELECT objectif_ca FROM sales.objectifs WHERE store_id=%s AND agent_id IS NULL AND date_objectif=CURRENT_DATE",
            (self._cd,)
        )
        val = row.get("objectif_ca")
        if val:
            return float(val)
        # Fallback: calculer depuis les données historiques
        row2 = _query_one("""
            SELECT AVG(ca_jour) * 1.10 AS obj
            FROM (
                SELECT date_only, SUM(lig_ttc) AS ca_jour
                FROM sales.transactions
                WHERE store_id = %s AND lig_ttc > 0
                GROUP BY date_only
            ) sub
        """, (self._cd,))
        val2 = row2.get("obj")
        return float(val2) if val2 else _OBJECTIFS_DEFAUT.get(self._cd, 1007.0)

    # ── Store ─────────────────────────────────────────────────────────────────

    def get_store(self) -> dict:
        row = _query_one(
            "SELECT store_id, store_name, ville FROM sales.boutiques WHERE store_id=%s",
            (self._cd,)
        )
        return {
            "store_id":      row.get("store_id",   self._cd),
            "name":          row.get("store_name", _STORE_NAMES.get(self._cd, self._cd)),
            "ville":         row.get("ville",      "Tunis"),
            "opening_hour":  9,
            "closing_hour":  21,
        }

    # ── CA ────────────────────────────────────────────────────────────────────

    def get_nb_transactions(self) -> int:
        """Nombre de ventes du jour — historique + temps réel."""
        row = _query_one(
            _VENTES_JOUR + "SELECT COUNT(*) AS nb FROM ventes_jour",
            (self._cd, self._cd),
        )
        return int(row.get("nb", 0) or 0)

    def get_ca_total(self) -> float:
        """
        CA du jour — historique + ventes temps réel.

        Plus de repli sur « la dernière journée disponible » : il affichait le
        CA d'un autre jour comme si c'était celui d'aujourd'hui, et il ne
        pouvait de toute façon plus s'accorder avec le total par conseiller ni
        avec le ticker temps réel. Sans vente, la réponse honnête est 0.
        """
        row = _query_one(
            _VENTES_JOUR + "SELECT COALESCE(SUM(lig_ttc), 0) AS ca FROM ventes_jour",
            (self._cd, self._cd),
        )
        return max(0.0, float(row.get("ca", 0) or 0))

    def get_ca_by_advisor(self) -> dict:
        """
        CA du jour par conseiller — historique + temps réel.

        Aucun repli fabriqué : le CA d'un conseiller est la somme de SES ventes
        ou rien. L'ancienne répartition au prorata des poids inventait un
        chiffre par personne et écrasait toute différence de performance réelle.
        """
        rows = _query(
            _VENTES_JOUR + """
            SELECT agent_id, SUM(lig_ttc) AS ca
            FROM ventes_jour GROUP BY agent_id
            """,
            (self._cd, self._cd),
        )
        agent_map = {ag["agent_id"]: ag["id"] for ag in self._agents}
        result: dict = {}
        for r in rows:
            aid = str(r.get("agent_id", "")).zfill(4)
            adv_id = agent_map.get(aid)
            if adv_id is None:
                continue   # agent hors roster : ne pas l'imputer au premier venu
            result[adv_id] = result.get(adv_id, 0.0) + float(r.get("ca", 0) or 0)
        return result

    def get_nb_ventes_by_advisor(self) -> dict:
        """Nombre de ventes du jour par conseiller — même source que le CA."""
        rows = _query(
            _VENTES_JOUR + """
            SELECT agent_id, COUNT(*) AS nb
            FROM ventes_jour GROUP BY agent_id
            """,
            (self._cd, self._cd),
        )
        agent_map = {ag["agent_id"]: ag["id"] for ag in self._agents}
        result: dict = {}
        for r in rows:
            adv_id = agent_map.get(str(r.get("agent_id", "")).zfill(4))
            if adv_id is None:
                continue
            result[adv_id] = result.get(adv_id, 0) + int(r.get("nb", 0) or 0)
        return result

    def get_units_by_sku(self) -> dict:
        rows = _query(
            _VENTES_JOUR + "SELECT sku, SUM(units) AS units FROM ventes_jour GROUP BY sku",
            (self._cd, self._cd),
        )
        return {str(r["sku"]): int(r.get("units", 0) or 0) for r in rows}

    # ── Transactions ──────────────────────────────────────────────────────────

    def get_transactions_today(self) -> list:
        """
        Ventes du jour, la plus récente d'abord — historique + temps réel.

        Alimente le détail « toutes les ventes effectuées » ouvert depuis le
        compteur de ventes du dashboard : chaque ligne porte son vendeur, son
        produit, son montant et son heure.
        """
        rows = _query(
            _VENTES_JOUR + """
            SELECT v.agent_id, v.sku, v.lig_ttc, v.units, v.heure, v.ts,
                   COALESCE(v.des_produit, p.nom, v.sku) AS produit,
                   COALESCE(p.categorie, 'Autre')             AS categorie
            FROM ventes_jour v
            LEFT JOIN sales.produits p ON p.sku::text = v.sku
            ORDER BY v.ts DESC
            LIMIT 200
            """,
            (self._cd, self._cd),
        )

        # Nom du conseiller : le roster local est la reference (cf. _AGENTS),
        # la table agents ne porte pas toujours ces vendeurs.
        by_agent = {ag["agent_id"]: ag for ag in self._agents}
        result = []
        for r in rows:
            aid = str(r.get("agent_id", "")).zfill(4)
            ag  = by_agent.get(aid)
            ts  = r.get("ts")
            result.append({
                "advisor_id":     ag["id"]       if ag else None,
                "agent_id":       aid,
                "advisor_name":   ag["name"]     if ag else f"Agent {aid}",
                "advisor_initials": ag["initials"] if ag else "??",
                "avatar_color":   ag["avatar_color"] if ag else "#94A3B8",
                "sku":            str(r.get("sku", "")),
                "product_name":   str(r.get("produit", ""))[:60],
                "category":       str(r.get("categorie", "Autre")),
                "amount":         round(float(r.get("lig_ttc", 0) or 0), 2),
                "units":          int(r.get("units", 1) or 1),
                "hour":           int(r.get("heure", 9) or 9),
                "time":           ts.strftime("%H:%M") if ts else "--:--",
            })
        return result

    def get_hourly_ca(self) -> list:
        """CA par heure depuis PostgreSQL."""
        rows = _query(
            _VENTES_JOUR + "SELECT heure, SUM(lig_ttc) AS ca FROM ventes_jour GROUP BY heure ORDER BY heure",
            (self._cd, self._cd),
        )

        hourly   = {int(r["heure"]): float(r["ca"]) for r in rows}
        objectif = self._objectif()

        # "actual" = None seulement pour les heures sans aucune transaction
        # enregistrée (heure pas encore atteinte). On se base sur la présence
        # réelle en base plutôt que sur l'horloge murale (datetime.now().hour) :
        # le RealtimeSimulator peut générer les ventes d'une journée plus vite
        # que le temps réel, ce qui rendait "h <= now_h" faux-négatif et
        # masquait des heures ayant pourtant déjà du CA réel.
        result = []
        for h in range(9, 21):
            result.append({
                "hour":   f"{h}h",
                "actual": round(hourly[h], 2) if h in hourly else None,
                "target": round(objectif / 12, 2),
            })
        return result

    # ── Advisors ──────────────────────────────────────────────────────────────

    def get_advisors(self) -> list:
        objectif = self._objectif()
        return [
            {
                "id":           ag["id"],
                "advisor_code": ag["advisor_code"],
                "agent_id":     ag["agent_id"],
                "name":         ag["name"],
                "initials":     ag["initials"],
                "role":         ag["role"],
                "avatar_color": ag["avatar_color"],
                "ca_target":    round(objectif * ag["weight"], 2),
                "coach_score":  ag["coach_score"],
            }
            for ag in self._agents
        ]

    def get_advisor(self, advisor_id: str) -> Optional[dict]:
        return next((a for a in self.get_advisors() if a["id"] == advisor_id), None)

    def _poids_reels(self) -> dict:
        """
        Part de chaque conseiller dans le CA de la boutique, mesurée sur les
        90 derniers jours.

        Remplace les poids codés en dur de _AGENTS, qui étaient des estimations
        et servaient à la fois à répartir l'objectif ET (avant) à fabriquer un
        CA par conseiller. Répartition égale si l'historique est absent — au
        moins c'est explicite, plutôt qu'une clé arbitraire.
        """
        rows = _query("""
            SELECT agent_id, SUM(lig_ttc) AS ca
            FROM sales.transactions
            WHERE store_id=%s AND date_only >= CURRENT_DATE - 90 AND lig_ttc>0
            GROUP BY agent_id
        """, (self._cd,))
        by_agent = {ag["agent_id"]: ag["id"] for ag in self._agents}
        parts = {}
        for r in rows:
            adv_id = by_agent.get(str(r.get("agent_id", "")).zfill(4))
            if adv_id:
                parts[adv_id] = parts.get(adv_id, 0.0) + float(r.get("ca", 0) or 0)
        total = sum(parts.values())
        if total <= 0:
            n = max(len(self._agents), 1)
            return {ag["id"]: 1.0 / n for ag in self._agents}
        # Les conseillers sans historique ne reçoivent pas une cible nulle
        # (elle rendrait leur pourcentage infini) : plancher à 5 %.
        return {
            ag["id"]: max(parts.get(ag["id"], 0.0) / total, 0.05)
            for ag in self._agents
        }

    def get_advisors_performance(self) -> list:
        ca_map   = self.get_ca_by_advisor()
        objectif = self._objectif()
        poids    = self._poids_reels()
        now_h    = datetime.now().hour

        # Même source que le CA (historique + temps réel), sinon on réaffiche
        # « 0 vente » à côté d'un CA non nul, comme avant.
        nb_ventes_map = self.get_nb_ventes_by_advisor()

        result = []
        for ag in self._agents:
            ca        = round(ca_map.get(ag["id"], 0.0), 2)
            ca_target = round(objectif * poids.get(ag["id"], 0.0), 2)
            perf      = round((ca / ca_target * 100), 1) if ca_target else 0
            status    = "top" if perf >= 80 else "ok" if perf >= 50 else "urgent"

            result.append({
                "id":            ag["id"],
                "advisor_code":  ag["advisor_code"],
                "name":          ag["name"],
                "initials":      ag["initials"],
                "role":          ag["role"],
                "avatar_color":  ag["avatar_color"],
                "ca_realized":   ca,
                "ca_target":     ca_target,
                "performance":   perf,
                "attainment":    perf,
                "revenue":       ca,
                "target":        ca_target,
                "nb_ventes":     nb_ventes_map.get(ag["id"], 0),
                "coach_score":   ag["coach_score"],
                "status":        status,
                "prevision_eod": round(ca * (20 / max(now_h - 8, 1)), 2) if now_h > 8 else ca_target,
                "rank":          0,
            })

        result.sort(key=lambda x: x["performance"], reverse=True)
        for i, r in enumerate(result):
            r["rank"] = i + 1

        return result

    # ── Targets ───────────────────────────────────────────────────────────────

    def get_targets(self) -> list:
        objectif = self._objectif()
        return [
            {
                "advisor_id":   ag["id"],
                "agent_id":     ag["agent_id"],
                "ca_target":    round(objectif * ag["weight"], 2),
                "units_target": max(1, int(objectif * ag["weight"] / 30)),
            }
            for ag in self._agents
        ]

    def get_target(self, advisor_id: str) -> Optional[dict]:
        return next((t for t in self.get_targets() if t["advisor_id"] == advisor_id), None)

    # ── Inventory ─────────────────────────────────────────────────────────────

    def get_inventory(self) -> list:
        rows = _query("""
            SELECT
                s.sku, s.quantity,
                p.des_prod, p.cod_famille, p.pv_ttc, p.categorie
            FROM stock s
            LEFT JOIN sales.produits p ON s.sku = p.sku
            WHERE s.store_id = %s
            ORDER BY s.quantity DESC
            LIMIT 50
        """, (self._cd,))

        demand_rows = _query("""
            SELECT sku, SUM(quantity) / 30.0 AS demand_day
            FROM sales.transactions
            WHERE store_id=%s
              AND date_only >= CURRENT_DATE - INTERVAL '30 days'
              AND lig_ttc > 0
            GROUP BY sku
        """, (self._cd,))
        demand_map = {str(r["sku"]): float(r["demand_day"]) for r in demand_rows}

        inventory = []
        for r in rows:
            cod    = str(r["sku"])
            stock  = int(r.get("qte_stk", 0))
            fam    = r.get("cod_famille")
            pv     = float(r.get("pv_ttc", 0) or 0)
            demand = demand_map.get(cod, 0.5)

            is_digital = fam in [10, 11, 12, 13, 15, 52, 80, 90, 92, 70]
            if is_digital:
                stock = 999; risk = "ok"; risk_s = 0.0; coverage = 99.9
            else:
                coverage = round(stock / max(demand, 0.1), 1)
                if stock == 0:
                    risk = "critical"; risk_s = 0.95
                elif coverage < 3:
                    risk = "high";    risk_s = 0.72
                elif coverage < 7:
                    risk = "medium";  risk_s = 0.45
                else:
                    risk = "ok";      risk_s = 0.10

            inventory.append({
                "sku":                 cod,
                "name":               str(r.get("des_prod", cod))[:50],
                "category":           str(r.get("categorie", "Autre")),
                "stock":              stock,
                "stock_min":          max(1, int(demand * 3)),
                "stock_max":          max(10, int(demand * 15)),
                "demand_24h":         round(demand, 1),
                "risk_level":         risk,
                "risk_score":         risk_s,
                "coverage_ratio":     min(coverage, 99.9),
                "recommendation":     f"Stock {stock} | Demande ~{round(demand,1)}/j",
                "recommendation_detail": f"PV: {pv:.0f} DT",
                "confidence":         0.85,
                "trend":              "stable",
                "last_updated":       "LIVE",
                "unit_price":         pv,
            })
        return inventory

    def get_inventory_item(self, sku: str) -> Optional[dict]:
        return next((i for i in self.get_inventory() if i["sku"] == sku), None)

    def get_alerts(self) -> list:
        alerts = []
        for item in self.get_inventory():
            if item["risk_level"] in ["critical", "high"]:
                alerts.append({
                    "id":      f"alert-{item['sku']}",
                    "sku":     item["sku"],
                    "type":    "stockout",
                    "urgency": item["risk_level"],
                    "message": f"{item['name']} — {item['stock']} unites | {item['coverage_ratio']}j couverture",
                    "action":  item["recommendation"],
                    "time":    "LIVE",
                })
        return alerts

    # ── Context ───────────────────────────────────────────────────────────────

    def get_context(self) -> dict:
        return {
            "weather":             "Partiellement nuageux - Tunis Lac",
            "weather_impact":      "+0% trafic boutique",
            "weather_icon":        "cloudy",
            "event":               "",
            "event_time":          "",
            "event_impact":        "",
            "stock_alert":         "Forfait Flexi 25Go disponible",
            "traffic_h":           26,
            "traffic_capacity":    40,
            "traffic_vs_forecast": -35,
        }

    # ── Métriques boutique ────────────────────────────────────────────────────

    def get_store_metrics(self) -> dict:
        ca_total  = self.get_ca_total()
        ca_target = self._objectif()
        attain    = round((ca_total / ca_target * 100), 1) if ca_target else 0

        row = _query_one(
            "SELECT store_name, ville FROM sales.boutiques WHERE store_id=%s",
            (self._cd,)
        )

        # Compteur affiché sur la carte CA : doit compter les MÊMES ventes que
        # le CA lui-même, donc passer par get_nb_transactions() (historique +
        # temps réel). Il interrogeait encore sales.transactions seul et
        # affichait 0 à côté d'un CA de plusieurs milliers de dinars.
        nb_tx = self.get_nb_transactions()

        return {
            "store_id":       self._cd,
            "name":           row.get("store_name", _STORE_NAMES.get(self._cd, self._cd)),
            "ca_today":       round(ca_total, 2),
            "ca_target":      round(ca_target, 2),
            "attainment_pct": attain,
            "attainment":     attain,
            "visitors_h":     26,
            "agents_live":    len(self._agents),
            "nb_transactions": nb_tx,
            "context":        self.get_context(),
            "updated_at":     datetime.utcnow().isoformat(),
            "source":         "postgresql",
        }

    

    def add_transaction(self, tx: dict) -> None:
        pass

    def reset_day(self) -> None:
        pass

    # ── Debug ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        ca_map = self.get_ca_by_advisor()
        tx_row = _query_one(
            "SELECT COUNT(*) AS nb FROM sales.transactions WHERE store_id=%s AND date_only=CURRENT_DATE",
            (self._cd,)
        )
        return {
            "total_transactions": int(tx_row.get("nb", 0)),
            "ca_total":           round(self.get_ca_total(), 2),
            "ca_target":          round(self._objectif(), 2),
            "ca_by_advisor":      {k: round(v, 2) for k, v in ca_map.items()},
            "source":             "postgresql",
            "store_id":           self._cd,
        }
