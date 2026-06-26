"""
csv_realtime_provider.py
========================
Solution fondamentale — Provider de données temps réel basé sur le CSV
transactions_2025_2026_v2.csv

Architecture :
- Charge le CSV complet en mémoire au démarrage (pandas, une seule fois)
- Simule "aujourd'hui" = un jour choisi du CSV (par défaut dernier jour I63)
- Accumule les ventes live du simulateur en mémoire (_live_sales)
- Agent Analyste voit : historique CSV + ventes live du simulateur

Colonnes CSV :
  DATE_VENTE, CODE_CENTRE, AGENT_ID, AGENT_NAME, AGENT_SURNAME,
  CODE_PRODUIT, DES_PRODUIT, CATEGORIE_PRODUIT, LIG_TTC, QTE_PRODUIT

Usage dans main.py :
    from csv_realtime_provider import get_csv_provider
    provider = get_csv_provider()
"""

import logging
import os
import random
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ── Chemins ──────────────────────────────────────────────────────────────────
# Ce fichier est conservé pour référence mais n'est plus utilisé.
# Toutes les données sont lues depuis PostgreSQL.
BASE = Path(__file__).resolve().parent
CSV_PATH = Path(os.getenv("CSV_TRANSACTIONS_PATH", str(BASE / "sales-module" / "data" / "transactions_2025_2026_v2.csv")))

STORE_ID = "I63"
DAILY_TARGET = 1007.0  # objectif journalier boutique I63

# ── Pattern horaire réel I63 (% du CA journalier par heure) ─────────────────
PATTERN_HORAIRE = {
    8: 1.20, 9: 3.27, 10: 7.35, 11: 10.19, 12: 14.34,
    13: 12.20, 14: 9.41, 15: 9.10, 16: 8.24, 17: 8.80,
    18: 7.72, 19: 5.10, 20: 2.44, 21: 0.90,
}

# ── Famille produit → catégorie ──────────────────────────────────────────────
CATEGORIE_MAP = {
    "RECHARGE": "Recharge",
    "FORFAIT": "Forfait Mobile",
    "SIM": "SIM / Ligne",
    "LIGNE": "SIM / Ligne",
    "TERMINAL": "Terminal",
    "ACCESSOIRE": "Accessoire",
    "BOX": "Box / Fibre",
    "FIBRE": "Box / Fibre",
    "SERVICE": "Services",
    "POSTPAYE": "Postpayé",
    "FACTURE": "Postpayé",
}


def _map_category(cat_str: str) -> str:
    if not cat_str or str(cat_str).strip() == "":
        return "Autre"
    upper = str(cat_str).upper()
    for key, val in CATEGORIE_MAP.items():
        if key in upper:
            return val
    return "Autre"


# ══════════════════════════════════════════════════════════════════════════════
# Classe principale
# ══════════════════════════════════════════════════════════════════════════════

class CSVRealtimeProvider:
    """
    Provider temps réel basé sur le CSV historique + simulateur live.
    
    Principe :
    - _df_hist    : tout le CSV chargé en mémoire (une fois)
    - _sim_date   : la date simulée comme "aujourd'hui" (dernier jour CSV I63)
    - _live_sales : liste des ventes ajoutées par le simulateur aujourd'hui
    """

    def __init__(self):
        self._df_hist: Optional[pd.DataFrame] = None
        self._sim_date: Optional[date] = None
        self._live_sales: list[dict] = []
        self._live_ca: float = 0.0
        self._live_nb_tx: int = 0
        self._loaded = False

    # ── Chargement ────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Charge le CSV en mémoire. Appeler une fois au démarrage."""
        if self._loaded:
            return

        if not CSV_PATH.exists():
            logger.error(f"[CSV_PROVIDER] CSV non trouvé: {CSV_PATH}")
            self._df_hist = pd.DataFrame()
            self._sim_date = date.today()
            self._loaded = True
            return

        logger.info(f"[CSV_PROVIDER] Chargement {CSV_PATH.name}...")

        df = pd.read_csv(
            CSV_PATH,
            parse_dates=["DATE_VENTE"],
            dtype={"CODE_CENTRE": str, "AGENT_ID": str, "LIG_TTC": float},
            low_memory=False,
        )

        # Normaliser colonnes
        df["date_only"] = df["DATE_VENTE"].dt.date
        df["heure"] = df["DATE_VENTE"].dt.hour
        df["LIG_TTC"] = pd.to_numeric(df["LIG_TTC"], errors="coerce").fillna(0)
        df["store_id"] = df["CODE_CENTRE"].str.strip()
        df["product_category"] = df["CATEGORIE_PRODUIT"].apply(_map_category)

        self._df_hist = df

        # Date simulée = dernier jour disponible pour I63
        i63 = df[df["store_id"] == STORE_ID]
        if not i63.empty:
            self._sim_date = i63["date_only"].max()
        else:
            self._sim_date = df["date_only"].max()

        self._loaded = True
        logger.info(
            f"[CSV_PROVIDER] ✅ {len(df):,} lignes chargées | "
            f"I63: {len(i63):,} tx | "
            f"Date simulée: {self._sim_date}"
        )

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    # ── CA journalier simulé ──────────────────────────────────────────────────

    def add_live_sale(self, sku: str, agent_id: str, price: float, qty: int = 1) -> None:
        """Appelé par RealtimeSimulator à chaque vente."""
        now = datetime.now()
        self._live_sales.append({
            "sale_id": f"LIVE_{now.strftime('%H%M%S%f')}",
            "date_only": date.today(),
            "heure": now.hour,
            "transaction_time": now,
            "agent_id": agent_id,
            "product_code": sku,
            "product_name": sku,
            "product_category": "Terminal",  # simplifié
            "revenue": price * qty,
            "revenue_tnd": price * qty,
            "quantity": qty,
            "time": now.strftime("%H:%M"),
            "minutes_ago": 0,
            "source": "live_simulator",
        })
        self._live_ca += price * qty
        self._live_nb_tx += 1
        logger.debug(f"[CSV_PROVIDER] Vente live: {sku} {price*qty:.0f} TND | Total: {self._live_ca:.0f} TND")

    def reset_live_sales(self) -> None:
        """Reset à minuit (nouveau jour de simulation)."""
        self._live_sales = []
        self._live_ca = 0.0
        self._live_nb_tx = 0
        logger.info("[CSV_PROVIDER] Reset ventes live")

    # ── fetch_pos_data ────────────────────────────────────────────────────────

    async def fetch_pos_data(self, store_id: str = STORE_ID) -> dict:
        """Données POS temps réel = historique CSV jour simulé + ventes live."""
        self._ensure_loaded()

        df = self._df_hist
        sim_date = self._sim_date
        now = datetime.now()
        current_hour = now.hour

        # Filtrer historique du jour simulé
        mask = (df["store_id"] == STORE_ID) & (df["date_only"] == sim_date) & (df["LIG_TTC"] > 0)
        df_today = df[mask]

        # CA historique jusqu'à l'heure actuelle (simulation progressive)
        df_until_now = df_today[df_today["heure"] <= current_hour]
        ca_hist = float(df_until_now["LIG_TTC"].sum())
        nb_tx_hist = len(df_until_now)

        # CA total = historique + live
        ca_total = ca_hist + self._live_ca
        nb_tx_total = nb_tx_hist + self._live_nb_tx

        # Objectif depuis l'historique ou défaut
        objectif = DAILY_TARGET

        # CA par heure (historique)
        hourly_ca = {}
        for h, grp in df_until_now.groupby("heure"):
            hourly_ca[int(h)] = float(grp["LIG_TTC"].sum())

        # Ajouter ventes live dans l'heure actuelle
        if self._live_ca > 0:
            hourly_ca[current_hour] = hourly_ca.get(current_hour, 0) + self._live_ca

        logger.info(
            f"[CSV_PROVIDER] POS {STORE_ID} | DATE={sim_date} | "
            f"CA_hist={ca_hist:.0f} + live={self._live_ca:.0f} = {ca_total:.0f} TND | "
            f"TX={nb_tx_total} | H={current_hour}h"
        )

        return {
            "store_id": STORE_ID,
            "business_date": str(sim_date),
            "sim_date": str(sim_date),
            "store_name": "FR LAC2 TUNISIA MALL",
            "ville": "Tunis",
            "type_boutique": "FRANCHISE",
            "daily_target": objectif,
            "daily_target_tnd": objectif,
            "current_revenue": round(ca_total, 2),
            "current_revenue_tnd": round(ca_total, 2),
            "ca_historique": round(ca_hist, 2),
            "ca_live": round(self._live_ca, 2),
            "nb_transactions_today": nb_tx_total,
            "nb_transactions_hist": nb_tx_hist,
            "nb_transactions_live": self._live_nb_tx,
            "avg_ticket": round(ca_total / max(nb_tx_total, 1), 2),
            "hourly_ca": hourly_ca,
            "current_hour": current_hour,
            "snapshot_time": now.strftime("%H:%M"),
            "closing_hour": 20,
            "source": "csv_realtime",
            "data_status": "available",
        }

    # ── fetch_pos_history ─────────────────────────────────────────────────────

    async def fetch_pos_history(self, store_id: str = STORE_ID) -> list[dict]:
        """Historique transactions du jour simulé + ventes live."""
        self._ensure_loaded()

        df = self._df_hist
        sim_date = self._sim_date
        now = datetime.now()
        current_hour = now.hour

        mask = (
            (df["store_id"] == STORE_ID)
            & (df["date_only"] == sim_date)
            & (df["LIG_TTC"] > 0)
            & (df["heure"] <= current_hour)
        )
        df_today = df[mask].sort_values("DATE_VENTE", ascending=False).head(200)

        history = []
        for _, row in df_today.iterrows():
            tx_time = row["DATE_VENTE"]
            # Simuler minutes_ago : comme si c'était aujourd'hui
            fake_tx = now.replace(hour=int(row["heure"]), minute=random.randint(0, 59))
            minutes_ago = max(0, int((now - fake_tx).total_seconds() / 60))

            agent_name = f"{str(row.get('AGENT_NAME','')).strip()} {str(row.get('AGENT_SURNAME','')).strip()}".strip()

            history.append({
                "sale_id": str(row.get("SALE_ID", "")),
                "time": f"{int(row['heure']):02d}:{random.randint(0,59):02d}",
                "transaction_time": fake_tx,
                "minutes_ago": minutes_ago,
                "agent_id": str(row.get("AGENT_ID", "")),
                "agent_name": agent_name or "Inconnu",
                "product_code": str(row.get("CODE_PRODUIT", "")),
                "product_name": str(row.get("DES_PRODUIT", "")),
                "product_category": str(row.get("product_category", "Autre")),
                "revenue_tnd": float(row.get("LIG_TTC", 0)),
                "revenue": float(row.get("LIG_TTC", 0)),
                "quantity": int(row.get("QTE_PRODUIT", 1) or 1),
                "hour": int(row["heure"]),
                "source": "csv_history",
            })

        # Ajouter ventes live
        for sale in self._live_sales:
            history.insert(0, sale)

        logger.info(
            f"[CSV_PROVIDER] History {STORE_ID} | DATE={sim_date} | "
            f"{len(history)} tx (hist={len(df_today)}, live={len(self._live_sales)})"
        )

        return history

    # ── fetch_timesfm_prediction ──────────────────────────────────────────────

    async def fetch_timesfm_prediction(self, store_id: str = STORE_ID) -> dict:
        """
        Forecast EOD robuste basé sur :
        1. Ratio historique (même heure, même jour semaine, 4 semaines précédentes)
        2. Tendance du jour en cours
        3. Pattern horaire Ooredoo
        """
        self._ensure_loaded()

        df = self._df_hist
        sim_date = self._sim_date
        now = datetime.now()
        current_hour = now.hour

        # CA actuel (historique + live)
        mask_today = (df["store_id"] == STORE_ID) & (df["date_only"] == sim_date) & (df["LIG_TTC"] > 0)
        df_today = df[mask_today]
        ca_hist_full = float(df_today["LIG_TTC"].sum())  # CA total du jour historique
        ca_hist_until_now = float(df_today[df_today["heure"] <= current_hour]["LIG_TTC"].sum())
        ca_current = ca_hist_until_now + self._live_ca

        objectif = DAILY_TARGET

        # ── Méthode 1 : Ratio EOD basé sur 4 semaines similaires ──────────────
        forecasts = []
        weights = []

        jour_semaine = pd.Timestamp(sim_date).dayofweek

        for weeks_back in [1, 2, 3, 4]:
            ref_date = sim_date - timedelta(weeks=weeks_back)
            mask_ref = (
                (df["store_id"] == STORE_ID)
                & (df["date_only"] == ref_date)
                & (df["LIG_TTC"] > 0)
            )
            df_ref = df[mask_ref]
            if df_ref.empty:
                continue

            ca_ref_at_hour = float(df_ref[df_ref["heure"] <= current_hour]["LIG_TTC"].sum())
            ca_ref_eod = float(df_ref["LIG_TTC"].sum())

            if ca_ref_at_hour > 10 and ca_current > 0:
                ratio = ca_ref_eod / ca_ref_at_hour
                forecast = ca_current * ratio
                weight = 1.0 / weeks_back  # semaines récentes = plus de poids
                forecasts.append(forecast)
                weights.append(weight)

        # ── Méthode 2 : Pattern horaire Ooredoo ──────────────────────────────
        pct_done = sum(PATTERN_HORAIRE.get(h, 0) for h in range(8, current_hour + 1)) / 100
        if pct_done > 0.01 and ca_current > 0:
            forecast_pattern = ca_current / pct_done
            forecasts.append(forecast_pattern)
            weights.append(0.5)

        # ── Méthode 3 : Tendance 7 derniers jours ────────────────────────────
        last_7_ca = []
        for d in range(1, 8):
            ref_date = sim_date - timedelta(days=d)
            mask_r = (df["store_id"] == STORE_ID) & (df["date_only"] == ref_date) & (df["LIG_TTC"] > 0)
            ca_r = float(df[mask_r]["LIG_TTC"].sum())
            if ca_r > 0:
                last_7_ca.append(ca_r)

        if last_7_ca:
            avg_7d = np.mean(last_7_ca)
            # Ajuster selon le rythme actuel
            if pct_done > 0.01:
                expected_at_hour = avg_7d * pct_done
                if expected_at_hour > 0:
                    adjustment = ca_current / expected_at_hour
                    forecast_trend = avg_7d * adjustment
                else:
                    forecast_trend = avg_7d
            else:
                forecast_trend = avg_7d
            forecasts.append(forecast_trend)
            weights.append(0.3)

        # ── Weighted average ──────────────────────────────────────────────────
        if forecasts and sum(weights) > 0:
            forecast_eod = sum(f * w for f, w in zip(forecasts, weights)) / sum(weights)
        else:
            # Dernier fallback : pattern simple
            forecast_eod = ca_current / max(pct_done, 0.05) if pct_done > 0 else ca_hist_full

        # Cap à 150% de l'objectif
        forecast_eod = min(round(forecast_eod), int(objectif * 1.5))
        forecast_eod = max(forecast_eod, round(ca_current))

        # Intervalle de confiance
        std_7d = float(np.std(last_7_ca)) if len(last_7_ca) >= 2 else forecast_eod * 0.10
        ci_spread = min(std_7d * 0.5, forecast_eod * 0.15)

        # MAPE estimé depuis l'écart moyen des 4 semaines
        mape = 14.3  # valeur par défaut
        if forecasts and ca_hist_full > 0:
            errors = [abs(f - ca_hist_full) / ca_hist_full * 100 for f in forecasts[:2]]
            if errors:
                mape = round(min(np.mean(errors), 30.0), 1)

        # Forecast restant
        forecast_remaining = max(0, forecast_eod - round(ca_current))

        logger.info(
            f"[CSV_PROVIDER] Forecast {STORE_ID} | DATE={sim_date} | "
            f"CA={ca_current:.0f} | EOD={forecast_eod:.0f} | "
            f"Obj={objectif:.0f} | Sources={len(forecasts)} | MAPE={mape:.1f}%"
        )

        return {
            "forecast_end_of_day": forecast_eod,
            "forecast_end_of_day_tnd": forecast_eod,
            "forecast_remaining": forecast_remaining,
            "forecast_remaining_tnd": forecast_remaining,
            "confidence_interval": {
                "low": max(round(ca_current), forecast_eod - round(ci_spread)),
                "high": min(forecast_eod + round(ci_spread), int(objectif * 1.5)),
            },
            "objectif": objectif,
            "business_date": str(sim_date),
            "ca_current": round(ca_current, 2),
            "ca_historique": round(ca_hist_until_now, 2),
            "ca_live": round(self._live_ca, 2),
            "nb_sources": len(forecasts),
            "avg_7d_ca": round(float(np.mean(last_7_ca)) if last_7_ca else 0, 2),
            "pct_done": round(pct_done * 100, 1),
            "mape": mape,
            "model_version": "csv-multi-source-v2",
            "source": "csv_forecast",
        }

    # ── fetch_sellers ─────────────────────────────────────────────────────────

    async def fetch_sellers(self, store_id: str = STORE_ID) -> list[dict]:
        """CA par conseiller pour le jour simulé jusqu'à maintenant."""
        self._ensure_loaded()

        df = self._df_hist
        sim_date = self._sim_date
        now = datetime.now()
        current_hour = now.hour

        mask = (
            (df["store_id"] == STORE_ID)
            & (df["date_only"] == sim_date)
            & (df["LIG_TTC"] > 0)
            & (df["heure"] <= current_hour)
        )
        df_today = df[mask]

        sellers = []
        per_seller_target = round(DAILY_TARGET / 4)

        agents = {
            "7296": {"name": "Zouiten Insaf",     "target_share": 0.35},
            "7451": {"name": "Ben Ammar Meriam",  "target_share": 0.25},
            "8987": {"name": "Mansour Hela",      "target_share": 0.32},
            "8988": {"name": "Mansour Khouloud",  "target_share": 0.08},
        }

        for agent_id, info in agents.items():
            df_agent = df_today[df_today["AGENT_ID"] == agent_id]
            ca = float(df_agent["LIG_TTC"].sum())
            nb_tx = len(df_agent)

            # Ajouter ventes live de cet agent
            live_ca = sum(s["revenue"] for s in self._live_sales if s.get("agent_id") == agent_id)
            ca += live_ca

            target = round(DAILY_TARGET * info["target_share"])
            att = round(ca / target * 100) if target > 0 else 0

            sellers.append({
                "agent_id": agent_id,
                "name": info["name"],
                "revenue_today": round(ca, 2),
                "nb_ventes": nb_tx,
                "objectif": target,
                "attainment_pct": att,
                "business_date": str(sim_date),
                "source": "csv_realtime",
            })

        return sorted(sellers, key=lambda x: -x["revenue_today"])


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_csv_provider: Optional[CSVRealtimeProvider] = None


def get_csv_provider() -> CSVRealtimeProvider:
    global _csv_provider
    if _csv_provider is None:
        _csv_provider = CSVRealtimeProvider()
        _csv_provider.load()
    return _csv_provider
