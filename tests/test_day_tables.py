"""test_day_tables.py: the per-day, per-expiry tables the live page opens (C9).
Synthetic frames only."""

import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import day_tables as dt

# Every column a day table may publish. A strike, share price, vol level or
# price must never appear: strike with moneyness gives the share price back.
ALLOWED = re.compile(
    r"^(date|expiry|days|carry|rows|inverted|rejected|rejections|traded|quoted|"
    r"settlement_only|no_price|quote_bar|smile_fit|smile_poor_fit|spread_bp|spread_source|rates|"
    r"(pair|smile|exch_call|exch_put)_(n|mean|\d\.\d\d-\d\.\d\d))$")


def test_buckets_are_the_front_charts_cells():
    b = dt.bucket_of(pd.Series([0.90, 1.00, 1.10, 1.20, 0.80]))
    assert list(b[:3]) == ["0.88-0.95", "0.98-1.02", "1.05-1.12"]
    assert pd.isna(b.iloc[3]) and pd.isna(b.iloc[4])        # outside: in "All" only


def test_counts_cover_every_row_and_name_each_reason():
    rows = pd.DataFrame({"reason": ["ok", "ok", "below_intrinsic", "spread_wider_than_limit",
                                    "spread_wider_than_limit"],
                         "tier": ["traded", "quoted", "quoted", "quoted", "no_price"]})
    c = dt.counts(rows)
    assert (c["rows"], c["inverted"], c["rejected"]) == (5, 2, 3)
    assert c["rejections"] == "spread wider than limit 2; below intrinsic 1"
    assert (c["traded"], c["quoted"], c["no_price"]) == (1, 3, 1)


def test_a_day_that_does_not_reconcile_stops_the_tables():
    whole = dict(rows=5, inverted=2, rejected=3, traded=1, quoted=3, settlement_only=0,
                 no_price=1)
    dt.reconcile("d", whole, {"ok": 2, "below_intrinsic": 3},
                 {"traded": 1, "quoted": 3, "no_price": 1})
    with pytest.raises(SystemExit):
        dt.reconcile("d", whole, {"ok": 3, "below_intrinsic": 2}, {})
    with pytest.raises(SystemExit):
        dt.reconcile("d", whole, {}, {"traded": 2, "quoted": 2, "no_price": 1})


def test_published_tables_hold_only_derived_columns():
    files = sorted((ROOT / "reports" / "days").glob("*.csv"))
    if not files:
        pytest.skip("reports/days not produced yet")
    for f in files:
        header = f.read_text(encoding="utf-8").splitlines()[0].split(",")
        bad = [c for c in header if not ALLOWED.match(c)]
        assert not bad, f"{f.name} publishes {bad}"
