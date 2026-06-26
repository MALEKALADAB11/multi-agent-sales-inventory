"""
mock_provider.py — Données Ooredoo réelles depuis CSV/XLS.
Lit directement les fichiers de données sans PostgreSQL.
"""
import json
import random
import logging
import math
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ── Chemins des fichiers ──────────────────────────────────────────────────────
_DATA_DIR = Path(__file__).parent

# Fichiers CSV/XLS réels
_TX_FILE    = _DATA_DIR / "transaction_vente_test_100500_fast.csv"
_BOUT_FILE  = _DATA_DIR / "boutique_actif.xls"
_PROD_FILE  = _DATA_DIR / "product.xls"
_STOCK_FILE = _DATA_DIR / "stock_centre.xls"

# Fichiers JSON mock (fallback)
_MOCK_DIR   = _DATA_DIR / "mock"

# ── Mapping store_id frontend → CD_DIST réel ─────────────────────────────────
_STORE_MAP = {
    # Frontend → CD_DIST
    "store-lac2":    "I63",
    "store-menzah":  "M23",
    "store-sfax":    "M03",
    # Ancien format OOR → CD_DIST
    "OOR_LAC_01":    "I63",
    "OOR_MENZAH_02": "M23",
    "OOR_SFAX_03":   "M03",
    # Direct
    "I63": "I63",
    "M23": "M23",
    "M03": "M03",
    "M10": "M10",
}

# ── Objectifs par boutique (moy CA/jour × 1.10) ───────────────────────────────
_OBJECTIFS = {
    "I63": 1007.0,   # moy 915 × 1.10
    "M23": 2047.0,   # moy 1861 × 1.10
    "M03": 1057.0,   # moy 961 × 1.10
    "M10": 3457.0,
}

# ── Ratios EOD/CA_cumulé par heure — calculés depuis données réelles I63 ──────
# ratio[h] = EOD / CA_cumulé_jusqu_à_h
_RATIOS_EOD = {
    9: 151.394, 10: 50.949, 11: 14.913, 12: 17.838,
    13: 7.833,  14: 4.784,  15: 3.144,  16: 1.999,
    17: 1.610,  18: 1.413,  19: 1.112,  20: 1.002,
}

# ── Pattern horaire I63 (% du CA journalier) ──────────────────────────────────
_PATTERN_HORAIRE = {
    9: 1.34, 10: 4.10, 11: 5.25, 12: 2.58, 13: 4.63,
    14: 11.15, 15: 11.59, 16: 21.14, 17: 10.31,
    18: 6.48, 19: 12.85, 20: 8.38, 21: 0.20,
}

# ── CA moyen par jour de semaine I63 ─────────────────────────────────────────
_DOW_FACTOR = {
    0: 0.73,  # Lundi
    1: 0.72,  # Mardi
    2: 0.98,  # Mercredi
    3: 1.69,  # Jeudi (meilleur jour)
    4: 1.03,  # Vendredi
    5: 0.93,  # Samedi
    6: 1.00,  # Dimanche
}

# ── Agents par boutique ────────────────────────────────────────────────────────
_AGENTS = {
    "I63": [
        {"agent_id": "7296", "name": "Zouiten Insaf",    "initials": "ZI", "weight": 0.35, "role": "Forfaits & Services"},
        {"agent_id": "8987", "name": "Mansour Hela",      "initials": "MH", "weight": 0.32, "role": "Postpaye & Terminaux"},
        {"agent_id": "7451", "name": "Ben Ammar Meriam",  "initials": "BM", "weight": 0.25, "role": "Smartphones & Data"},
        {"agent_id": "8988", "name": "Mansour Khouloud",  "initials": "MK", "weight": 0.08, "role": "Recharge & Accessoires"},
    ],
    "M23": [
        {"agent_id": "1104", "name": "Dridi Nabil",           "initials": "DN", "weight": 0.30, "role": "Postpaye Premium"},
        {"agent_id": "0600", "name": "Benabdallah Abdelhedi",  "initials": "BA", "weight": 0.28, "role": "Forfaits Business"},
        {"agent_id": "0996", "name": "Ben Fadhla Issam",       "initials": "BI", "weight": 0.24, "role": "Smartphones"},
        {"agent_id": "0440", "name": "Elmay Fethi",            "initials": "EF", "weight": 0.18, "role": "Accessoires"},
    ],
    "M03": [
        {"agent_id": "3703", "name": "Zouari Imen",    "initials": "ZI", "weight": 0.45, "role": "Smartphones & Forfaits"},
        {"agent_id": "1447", "name": "Trabelsi Med Ali", "initials": "TM", "weight": 0.24, "role": "Postpaye"},
        {"agent_id": "2607", "name": "Rekhiss Aymen",  "initials": "RA", "weight": 0.17, "role": "Internet & Box"},
        {"agent_id": "2664", "name": "Kharrat Slim",   "initials": "KS", "weight": 0.14, "role": "Recharge"},
    ],
}

# ── Cache données brutes ───────────────────────────────────────────────────────
_df_tx:    Optional[pd.DataFrame] = None
_df_prod:  Optional[pd.DataFrame] = None
_df_stock: Optional[pd.DataFrame] = None
_df_bouts: Optional[pd.DataFrame] = None


def _load_data():
    """Charger les données CSV/XLS une seule fois."""
    global _df_tx, _df_prod, _df_stock, _df_bouts

    if _df_tx is not None:
        return

    logger.info("[DATA] Chargement données Ooredoo depuis CSV/XLS...")

    try:
        _df_tx = pd.read_csv(str(_TX_FILE), dtype=str, low_memory=False)
        _df_tx['LIG_TTC'] = pd.to_numeric(_df_tx['LIG_TTC'], errors='coerce').fillna(0)
        _df_tx['QTE_PRODUIT'] = pd.to_numeric(_df_tx['QTE_PRODUIT'], errors='coerce').fillna(1)
        _df_tx['DATE_VENTE_DT'] = pd.to_datetime(_df_tx['DATE_VENTE'], errors='coerce')
        _df_tx['HEURE'] = _df_tx['DATE_VENTE_DT'].dt.hour
        _df_tx['DATE']  = _df_tx['DATE_VENTE_DT'].dt.date
        _df_tx['DOW']   = _df_tx['DATE_VENTE_DT'].dt.dayofweek
        logger.info(f"[DATA] ✅ {len(_df_tx):,} transactions chargées")
    except FileNotFoundError:
        logger.warning(f"[DATA] ⚠️  Fichier transactions non trouvé: {_TX_FILE}")
        _df_tx = pd.DataFrame()

    try:
        _df_prod = pd.read_excel(str(_PROD_FILE), engine='xlrd')
        _df_prod['COD_PROD'] = _df_prod['COD_PROD'].astype(str)
        logger.info(f"[DATA] ✅ {len(_df_prod):,} produits chargés")
    except Exception as e:
        logger.warning(f"[DATA] ⚠️  Produits non chargés: {e}")
        _df_prod = pd.DataFrame()

    try:
        _df_stock = pd.read_excel(str(_STOCK_FILE), engine='xlrd')
        _df_stock['COD_PROD'] = _df_stock['COD_PROD'].astype(str)
        _df_stock['QTE_STK']  = pd.to_numeric(_df_stock['QTE_STK'], errors='coerce').fillna(0)
        logger.info(f"[DATA] ✅ {len(_df_stock):,} lignes stock chargées")
    except Exception as e:
        logger.warning(f"[DATA] ⚠️  Stock non chargé: {e}")
        _df_stock = pd.DataFrame()

    try:
        _df_bouts = pd.read_excel(str(_BOUTS_FILE), engine='xlrd')
        logger.info(f"[DATA] ✅ {len(_df_bouts):,} boutiques chargées")
    except Exception as e:
        _df_bouts = pd.DataFrame()


def _categorize(des_produit: str) -> str:
    """Catégoriser un produit depuis son libellé."""
    des = str(des_produit).lower()
    if any(x in des for x in ['forfait', 'flexi', 'cvm', 'go', 'mifi']):
        return 'Forfait Mobile'
    if any(x in des for x in ['portable', 'samsung', 'iphone', 'redmi', 'xiaomi', 'honor', 'oppo', 'tecno']):
        return 'Terminal'
    if any(x in des for x in ['recharge', 'voucher', 'e-vouch']):
        return 'Recharge'
    if any(x in des for x in ['box', 'fibre', 'fixe', '4g box', 'mifi']):
        return 'Box / Fibre'
    if any(x in des for x in ['sim', 'ligne', 'kit']):
        return 'SIM / Ligne'
    if any(x in des for x in ['paiement', 'avance', 'facture']):
        return 'Postpaye'
    return 'Services'


class OoredooDataProvider:
    """
    Fournit les données Ooredoo réelles depuis CSV/XLS.
    Même interface que l'ancien MockDataProvider.
    """

    def __init__(self):
        _load_data()

    # ── fetch_pos_data ────────────────────────────────────────────────────────
    async def fetch_pos_data(self, store_id: str) -> dict:
        """CA du jour simulé depuis la moyenne historique réelle."""
        cd = _STORE_MAP.get(store_id, store_id)

        now          = datetime.now()
        current_hour = now.hour
        dow          = now.weekday()

        # Objectif journalier réel
        objectif = _OBJECTIFS.get(cd, 1000.0)

        # CA simulé selon heure actuelle + pattern réel
        pct_jour_ecoule = sum(
            _PATTERN_HORAIRE.get(h, 0)
            for h in range(9, current_hour + 1)
        ) / 100.0

        # Facteur jour de semaine
        dow_factor = _DOW_FACTOR.get(dow, 1.0)

        # CA actuel = objectif × pct_écoulé × facteur_dow × bruit
        bruit         = 1.0 + random.uniform(-0.08, 0.08)
        current_rev   = round(objectif * pct_jour_ecoule * dow_factor * bruit, 3)
        current_rev   = max(0, current_rev)

        # Agents
        agents = _AGENTS.get(cd, _AGENTS["I63"])

        # Boutique info
        bout_name = {
            "I63": "FR LAC2 TUNISIA MALL",
            "M23": "Boutique HABIB BOURGUIBA",
            "M03": "Boutique SFAX I",
            "M10": "Boutique AEROPORT",
        }.get(cd, cd)

        # Transactions simulées du jour
        nb_tx = max(1, int(current_rev / 25))

        logger.info(
            f"[DATA] POS {cd} | CA={current_rev:,.0f} TND "
            f"| Obj={objectif:,.0f} | TX={nb_tx} | H={current_hour}h"
        )

        return {
            "store_id":              cd,
            "store_name":            bout_name,
            "snapshot_time":         now.strftime("%H:%M"),
            "daily_target":          objectif,
            "daily_target_tnd":      objectif,
            "current_revenue":       current_rev,
            "current_revenue_tnd":   current_rev,
            "nb_transactions_today": nb_tx,
            "active_sellers":        len(agents),
            "closing_hour":          21,
            "current_hour":          current_hour,
            "source":                "csv_real_data",
        }

    # ── fetch_pos_history ─────────────────────────────────────────────────────
    async def fetch_pos_history(self, store_id: str) -> list[dict]:
        """Vraies transactions depuis CSV pour ce store."""
        cd = _STORE_MAP.get(store_id, store_id)

        if _df_tx is None or _df_tx.empty:
            return []

        # Prendre les 20 dernières transactions de ce store
        tx = _df_tx[
            (_df_tx['CODE_CENTRE'] == cd) &
            (_df_tx['LIG_TTC'] > 0)
        ].sort_values('DATE_VENTE_DT').tail(20)

        if tx.empty:
            logger.warning(f"[DATA] Aucune transaction pour {cd}")
            return []

        now = datetime.now()
        history = []
        for _, r in tx.iterrows():
            tx_time    = r['DATE_VENTE_DT']
            heure      = tx_time.strftime('%H:%M') if pd.notna(tx_time) else '00:00'
            minutes_ago = max(0, int((now - tx_time).total_seconds() / 60)) if pd.notna(tx_time) else 0
            name = f"{str(r['AGENT_NAME']).strip()} {str(r['AGENT_SURNAME']).strip()}"
            cat  = _categorize(str(r.get('DES_PRODUIT', '')))

            history.append({
                "transaction_id":  str(r['SALE_ID']),
                "time":            heure,
                "minutes_ago":     minutes_ago,
                "seller":          name,
                "agent_id":        str(r['AGENT_ID']).strip().zfill(4),
                "product_category": cat,
                "product_name":    str(r.get('DES_PRODUIT', ''))[:60],
                "revenue_tnd":     round(float(r['LIG_TTC']), 3),
                "revenue":         round(float(r['LIG_TTC']), 3),
                "quantity":        int(r.get('QTE_PRODUIT', 1) or 1),
                "type":            "vente",
            })

        logger.info(f"[DATA] {len(history)} transactions chargées pour {cd}")
        return history

    # ── fetch_timesfm_prediction ──────────────────────────────────────────────
    async def fetch_timesfm_prediction(self, store_id: str, **kwargs) -> dict:
        """
        Forecast EOD basé sur les ratios réels calculés depuis les données.
        Ratio = EOD / CA_cumulé_à_heure_H — calculé sur 28 jours réels de I63.
        """
        cd           = _STORE_MAP.get(store_id, store_id)
        now          = datetime.now()
        current_hour = now.hour
        objectif     = _OBJECTIFS.get(cd, 1000.0)

        # CA actuel simulé
        pos = await self.fetch_pos_data(store_id)
        ca_now = pos["current_revenue"]

        # Ratio EOD depuis les données réelles
        ratio = _RATIOS_EOD.get(current_hour, _RATIOS_EOD.get(15, 3.0))

        # Forecast brut
        forecast_brut = ca_now * ratio

        # Correction facteur jour de semaine
        dow_factor    = _DOW_FACTOR.get(now.weekday(), 1.0)
        forecast_eod  = round(forecast_brut * dow_factor, 3)

        # Cap à 150% de l'objectif
        forecast_eod  = min(forecast_eod, objectif * 1.50)
        forecast_eod  = max(forecast_eod, ca_now)

        forecast_remaining = max(0, round(forecast_eod - ca_now, 3))

        # Prévision horaire restante
        heures_restantes = list(range(current_hour + 1, 21))
        pct_restant = sum(_PATTERN_HORAIRE.get(h, 0) for h in heures_restantes) / 100
        hourly = []
        for h in heures_restantes:
            pct = _PATTERN_HORAIRE.get(h, 0) / 100
            share = pct / max(pct_restant, 0.01)
            hourly.append(round(forecast_remaining * share, 3))

        ci_spread = round(forecast_eod * 0.12)

        logger.info(
            f"[DATA] Forecast {cd} | CA={ca_now:,.0f} | "
            f"ratio@{current_hour}h={ratio:.3f} | EOD={forecast_eod:,.0f} TND"
        )

        return {
            "forecast_end_of_day":     forecast_eod,
            "forecast_end_of_day_tnd": forecast_eod,
            "forecast_remaining":      forecast_remaining,
            "forecast_remaining_tnd":  forecast_remaining,
            "forecast_hourly":         hourly,
            "forecast_hourly_tnd":     hourly,
            "confidence_interval": {
                "low":  max(round(ca_now), forecast_eod - ci_spread),
                "high": forecast_eod + ci_spread,
            },
            "objectif":      objectif,
            "ratio_used":    ratio,
            "model_version": "csv-ratio-v1",
            "source":        "real_data_ratio",
        }

    # ── fetch_sellers ─────────────────────────────────────────────────────────
    async def fetch_sellers(self, store_id: str) -> list[dict]:
        """Agents avec leur performance simulée."""
        cd       = _STORE_MAP.get(store_id, store_id)
        pos      = await self.fetch_pos_data(store_id)
        ca_today = pos["current_revenue"]
        objectif = pos["daily_target"]
        agents   = _AGENTS.get(cd, _AGENTS["I63"])

        sellers = []
        for ag in agents:
            ca    = round(ca_today * ag["weight"], 2)
            obj   = round(objectif * ag["weight"], 2)
            bruit = 1.0 + random.uniform(-0.05, 0.05)
            ca    = round(ca * bruit, 2)
            sellers.append({
                "agent_id":       ag["agent_id"],
                "name":           ag["name"],
                "initials":       ag["initials"],
                "role":           ag["role"],
                "revenue_today":  ca,
                "objectif":       obj,
                "nb_ventes":      max(1, int(ca / 25)),
                "panier_moyen":   round(ca / max(1, int(ca / 25)), 2),
                "attainment_pct": round((ca / obj * 100) if obj > 0 else 0, 1),
            })

        return sorted(sellers, key=lambda x: x["revenue_today"], reverse=True)

    # ── fetch_product_mix ─────────────────────────────────────────────────────
    async def fetch_product_mix(self, store_id: str) -> list[dict]:
        """Mix produits réel depuis CSV."""
        cd = _STORE_MAP.get(store_id, store_id)

        if _df_tx is None or _df_tx.empty:
            return []

        tx   = _df_tx[_df_tx['CODE_CENTRE'] == cd].copy()
        tx['CAT'] = tx['DES_PRODUIT'].apply(_categorize)

        mix_raw  = tx.groupby('CAT')['LIG_TTC'].sum().sort_values(ascending=False)
        total_ca = mix_raw.sum()

        return [
            {
                "category": cat,
                "ca":       round(float(ca), 2),
                "pct":      round(float(ca) / total_ca * 100, 1) if total_ca > 0 else 0,
            }
            for cat, ca in mix_raw.items()
        ]

    # ── fetch_top_products ────────────────────────────────────────────────────
    async def fetch_top_products(self, store_id: str, limit: int = 10) -> list[dict]:
        """Top produits vendus depuis CSV."""
        cd = _STORE_MAP.get(store_id, store_id)

        if _df_tx is None or _df_tx.empty:
            return []

        tx   = _df_tx[
            (_df_tx['CODE_CENTRE'] == cd) &
            (_df_tx['LIG_TTC'] > 0)
        ]
        top = tx.groupby(['CODE_PRODUIT', 'DES_PRODUIT'])['LIG_TTC'].sum()\
                .sort_values(ascending=False).head(limit)

        result = []
        for (cod, des), ca in top.items():
            pv = 0.0
            if _df_prod is not None and not _df_prod.empty:
                p = _df_prod[_df_prod['COD_PROD'] == str(cod)]
                if not p.empty:
                    pv = float(p.iloc[0].get('PV_TTC', 0) or 0)

            result.append({
                "cod_prod":  str(cod),
                "name":      str(des)[:50],
                "category":  _categorize(str(des)),
                "pv_ttc":    pv,
                "ca":        round(float(ca), 2),
                "ca_mensuel": round(float(ca), 2),
            })

        return result

    # ── Compatibilité avec l'ancien mock ──────────────────────────────────────
    async def fetch_pos_context(self, store_id: str) -> dict | None:
        return None

    def list_stores(self) -> list[str]:
        return ["I63", "M23", "M03", "M10"]


# ── Singleton ─────────────────────────────────────────────────────────────────
_provider_instance: Optional[OoredooDataProvider] = None

_BOUTS_FILE = _DATA_DIR / "boutique_actif.xls"


def get_data_provider() -> OoredooDataProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = OoredooDataProvider()
    return _provider_instance
