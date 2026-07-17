import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "db" / "seeds"))

from seed_promotions import normalize_discount_pct


def test_normalize_discount_pct_clamps_out_of_range_values():
    assert normalize_discount_pct("1200.00") == 999.99
    assert normalize_discount_pct("-1200.00") == -999.99
    assert normalize_discount_pct(None) == 0.0
    assert normalize_discount_pct("12.345") == 12.35
