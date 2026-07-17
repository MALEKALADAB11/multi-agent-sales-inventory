"""
check_agents_csv.py
=====================
One-off diagnostic: prints every row in agents.csv whose agent_id doesn't
parse as an integer, plus its neighbors, so you can see whether it's a
column-shift (row corruption) or a genuinely non-numeric id.

Run: python check_agents_csv.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _seed_common import DATA_PROCESSED

df = pd.read_csv(DATA_PROCESSED / "agents.csv")

def is_bad(v):
    try:
        int(v)
        return False
    except (ValueError, TypeError):
        return True

bad_mask = df["agent_id"].apply(is_bad)
bad_rows = df[bad_mask]

print(f"Total rows: {len(df)}")
print(f"Bad agent_id rows: {len(bad_rows)}\n")

if len(bad_rows):
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    print(bad_rows)
    print("\nRow indices (0-based, +2 for the Excel/CSV line number incl. header):")
    print(list(bad_rows.index))
