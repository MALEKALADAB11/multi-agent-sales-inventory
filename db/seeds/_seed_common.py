"""
_seed_common.py
================
Shared pool/env/path helper for every db/seeds/*.py script.

Fixes a path bug present in the old seed_static_data.py: that script assumed
an extra `inventory-module/` nesting level between the seeds folder and the
project root (`MODULE_ROOT.parent`) that no longer exists in this project —
db/seeds/ is now two levels below root, not three. PROJECT_ROOT here is
computed correctly for the current tree:  db/seeds/<script>.py -> db/ -> root.
"""
import logging
import math
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]   # db/seeds/../.. -> project root
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"

load_dotenv(PROJECT_ROOT / ".env")


def clean(value):
    """
    Normalize a pandas cell value for asyncpg binding into a TEXT/VARCHAR column.

    Two distinct problems, both handled here:

    1. pandas represents a blank/missing CSV cell as float NaN, not None —
       even for text columns. asyncpg's text codec only accepts str or
       NULL, so passing a raw NaN into a text argument raises:
           asyncpg.exceptions.DataError: invalid input for query argument
           $N: nan (expected str, got float)
       -> NaN is converted to None here.

    2. A column pandas infers as float64 can also contain real, non-blank
       numeric values (e.g. products.csv:subcategory holding bare numbers
       like 40.0 for some rows) even though the destination DB column is
       text. asyncpg rejects those too, just as strictly:
           asyncpg.exceptions.DataError: invalid input for query argument
           $N: 40.0 (expected str, got float)
       -> any non-null, non-NaN value is coerced to str here. Whole-number
       floats (40.0) are stringified without the trailing ".0" (-> "40"),
       since that's virtually always what the source data meant.

    IMPORTANT: `row.get("col", default)` does NOT protect against either
    case — Series.get()'s default only kicks in when the key/column is
    entirely absent, not when the cell is blank or numeric-but-wrong-type.
    Every optional text field pulled from a DataFrame row (via [] or
    .get(), with or without a default) should be passed through clean()
    before being bound to an asyncpg query, unless it's already been
    through pd.notna()/pd.to_datetime()/int()/float() etc for a genuinely
    numeric or date column.

    Use as: clean(r.get("responsable"))
    or with a default: clean(r.get("channel")) or "PHYSIQUE"
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return str(value)


def clean_trunc(value, maxlen: int, field: str = "?"):
    """
    clean() a value, then hard-cap it to maxlen characters.

    Some destination columns are narrower than the source data can be
    (e.g. sales.boutiques.type_boutique is varchar(5)). Passing a longer
    string straight to asyncpg raises
        asyncpg.exceptions.StringDataRightTruncationError
    instead of truncating for you, unlike a plain SQL varchar assignment.

    This truncates on the Python side so the insert succeeds, and logs a
    warning whenever truncation actually removes characters, so data loss
    is visible rather than hidden in a passing run.

    Use as: clean_trunc(r.get("store_type"), 5, field="type_boutique")
    """
    value = clean(value)
    if value is None:
        return None
    s = str(value)
    if len(s) > maxlen:
        logger.warning(
            f"  \u26a0 truncating {field}: {s!r} ({len(s)} chars) -> {s[:maxlen]!r} "
            f"({maxlen} chars) \u2014 destination column is varchar({maxlen})"
        )
        return s[:maxlen]
    return s


async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "ooredoo_sales"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "root"),
        min_size=2,
        max_size=5,
    )
