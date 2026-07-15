"""
Seed festivals été 2026 — market.events
========================================
Festivals tunisiens dont Ooredoo est partenaire/sponsor historique.
Impact ventes : recharges, SIM touristes, accessoires (batteries, écouteurs),
data roaming — et stock (pics de trafic en boutique les soirs de concert).

Idempotent : dédoublonne d'abord market.events (seeds relancés plusieurs fois),
puis insère chaque festival seulement s'il n'existe pas déjà (nom + année).

Usage :
    python -m app.sales.data.seeds.seed_festivals_2026
"""
import asyncio
import logging
import os
from datetime import date

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:admin@localhost:5432/ooredoo_sales")

FESTIVALS_2026 = [
    {
        "event_name": "Festival International de Carthage 2026",
        "sous_type":  "FESTIVAL",
        "start_date": "2026-07-16", "end_date": "2026-08-21",
        "intensite":  "HIGH", "scope": "NATIONAL",
        "uplift_terminal": 0.05, "uplift_forfait": 0.10, "uplift_sim": 0.20,
        "uplift_recharge": 0.30, "uplift_accessoire": 0.25,
        "note_strategie": (
            "Ooredoo partenaire officiel. Amphithéâtre de Carthage — forte affluence "
            "soirs de concert (19h-23h). Pousser recharges data, batteries externes, "
            "écouteurs ; SIM prépayées touristes. Prévoir stock accessoires nomades."
        ),
    },
    {
        "event_name": "Festival International de Hammamet 2026",
        "sous_type":  "FESTIVAL",
        "start_date": "2026-07-04", "end_date": "2026-08-08",
        "intensite":  "MEDIUM", "scope": "REGIONAL",
        "uplift_terminal": 0.03, "uplift_forfait": 0.08, "uplift_sim": 0.15,
        "uplift_recharge": 0.25, "uplift_accessoire": 0.20,
        "note_strategie": (
            "Théâtre de plein air de Hammamet — clientèle estivale + touristes. "
            "Focus recharges, SIM visiteurs, accessoires été (chargeurs, coques)."
        ),
    },
    {
        "event_name": "Festival International de Sousse 2026",
        "sous_type":  "FESTIVAL",
        "start_date": "2026-07-18", "end_date": "2026-08-22",
        "intensite":  "MEDIUM", "scope": "REGIONAL",
        "uplift_terminal": 0.03, "uplift_forfait": 0.06, "uplift_sim": 0.15,
        "uplift_recharge": 0.22, "uplift_accessoire": 0.18,
        "note_strategie": (
            "Zone touristique Sousse-Kantaoui — pic estival. SIM touristes, "
            "recharges, forfaits data courte durée."
        ),
    },
    {
        "event_name": "Festival International de Dougga 2026",
        "sous_type":  "FESTIVAL",
        "start_date": "2026-07-24", "end_date": "2026-08-09",
        "intensite":  "LOW", "scope": "REGIONAL",
        "uplift_terminal": 0.02, "uplift_forfait": 0.04, "uplift_sim": 0.08,
        "uplift_recharge": 0.12, "uplift_accessoire": 0.10,
        "note_strategie": (
            "Théâtre romain de Dougga — affluence culturelle ciblée. "
            "Opportunité data/recharges les soirs de représentation."
        ),
    },
]


async def main() -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        # 1) Dédoublonnage (seeds précédents relancés) — garde le plus ancien ctid
        deleted = await conn.execute("""
            DELETE FROM market.events a
            USING market.events b
            WHERE a.event_name = b.event_name
              AND a.start_date = b.start_date
              AND a.end_date   = b.end_date
              AND a.ctid > b.ctid
        """)
        logger.info("Doublons exacts supprimés : %s", deleted)

        # 1bis) Doublons « variantes d'accents » (mojibake des anciens seeds :
        # « Soldes �t� » vs « Soldes Été ») — comparaison sur le nom réduit
        # aux alphanumériques ASCII + mêmes dates.
        deleted2 = await conn.execute(r"""
            DELETE FROM market.events a
            USING market.events b
            WHERE lower(regexp_replace(a.event_name, '[^a-zA-Z0-9]', '', 'g'))
                = lower(regexp_replace(b.event_name, '[^a-zA-Z0-9]', '', 'g'))
              AND a.start_date = b.start_date
              AND a.end_date   = b.end_date
              AND a.ctid > b.ctid
        """)
        logger.info("Doublons accents/mojibake supprimés : %s", deleted2)

        # 2) Insertion idempotente des festivals
        inserted = 0
        for f in FESTIVALS_2026:
            row = await conn.fetchrow(
                "SELECT 1 FROM market.events WHERE event_name = $1", f["event_name"]
            )
            if row:
                logger.info("Déjà présent : %s", f["event_name"])
                continue
            # annee est une colonne générée (extraite de start_date) — ne pas l'insérer.
            # event_type contraint par events_event_type_check (pas de CULTUREL) :
            # NATIONAL + sous_type=FESTIVAL porte la sémantique.
            await conn.execute("""
                INSERT INTO market.events
                    (event_id, event_name, event_type, sous_type, start_date, end_date,
                     scope, uplift_terminal, uplift_forfait, uplift_sim,
                     uplift_recharge, uplift_accessoire, intensite,
                     source_donnee, note_strategie, created_at)
                VALUES (gen_random_uuid(), $1, 'NATIONAL', $2, $3, $4,
                        $5, $6, $7, $8, $9, $10, $11,
                        'seed_festivals_2026', $12, NOW())
            """,
                f["event_name"], f["sous_type"],
                date.fromisoformat(f["start_date"]), date.fromisoformat(f["end_date"]),
                f["scope"], f["uplift_terminal"], f["uplift_forfait"], f["uplift_sim"],
                f["uplift_recharge"], f["uplift_accessoire"], f["intensite"],
                f["note_strategie"],
            )
            inserted += 1
            logger.info("Inséré : %s", f["event_name"])

        total   = await conn.fetchval("SELECT COUNT(*) FROM market.events")
        ongoing = await conn.fetchval(
            "SELECT COUNT(*) FROM market.events "
            "WHERE start_date <= CURRENT_DATE AND end_date >= CURRENT_DATE")
        logger.info("Terminé — %d festival(s) inséré(s) | total events=%s | en cours=%s",
                    inserted, total, ongoing)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
