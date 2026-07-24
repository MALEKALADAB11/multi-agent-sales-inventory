"""
Chargement du panel journalier CA × boutique depuis PostgreSQL.

Uniquement l'historique de ventes : la contextualisation (événements, offres,
jours fériés, météo) appartient à l'agent Stratège, qui la charge via
`stratege/tools.fetch_full_context()`. Le modèle de l'Analyste reste un
prévisionniste de série temporelle pure.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

PANEL_QUERY = """
    SELECT store_id, date_only, ca_total
    FROM sales.vw_ca_par_boutique
    WHERE ca_total IS NOT NULL
    ORDER BY store_id, date_only
"""


async def load_panel(min_days: int = 120) -> pd.DataFrame:
    """
    Retourne le panel `store_id · date_only · ca_total · observed`.

    Les boutiques ayant moins de `min_days` observations sont écartées : leur
    échelle mobile sur 28 jours serait trop instable pour produire un ratio
    exploitable, et elles resteront servies par le moteur statistique.
    """
    import asyncpg
    from app.sales.data.postgres_provider import DB_CONFIG

    conn = await asyncpg.connect(**DB_CONFIG)
    try:
        rows = await conn.fetch(PANEL_QUERY)
    finally:
        await conn.close()

    df = pd.DataFrame(
        [{"store_id": r["store_id"], "date_only": r["date_only"],
          "ca_total": float(r["ca_total"] or 0.0)} for r in rows]
    )
    if df.empty:
        return df

    df["date_only"] = pd.to_datetime(df["date_only"])
    df["observed"] = True

    counts = df.groupby("store_id")["date_only"].count()
    keep = counts[counts >= min_days].index
    dropped = sorted(set(counts.index) - set(keep))
    if dropped:
        logger.info("[PANEL] %d boutiques écartées (< %d jours) : %s",
                    len(dropped), min_days, ", ".join(dropped[:10]))

    df = df[df["store_id"].isin(keep)].reset_index(drop=True)
    logger.info("[PANEL] %d lignes · %d boutiques · %s → %s",
                len(df), df["store_id"].nunique(),
                df["date_only"].min().date(), df["date_only"].max().date())
    return df
