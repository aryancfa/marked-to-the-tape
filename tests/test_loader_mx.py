"""
test_loader_mx.py: the Montreal Exchange loader on a SYNTHETIC file.

Every price below is made up. Only the column layout copies the real file.
The ticker is fictional on purpose.
"""

import math

import pandas as pd
import pytest

from loaders.mx import (NO_PRICE, OPTION_COLUMNS, QUOTED, SETTLEMENT_ONLY,
                        TRADED, load_mx_options, load_mx_underlying,
                        tier_counts)

HEADER = ["Date", "Symbol", "Class Symbol", "Root Symbol", "Underlying Symbol",
          "Strike Price", "Expiry Date", "Call/Put", "Ins. Type", "Bid Price",
          "Ask Price", "Bid Size", "Ask Size", "Last Price", "Volume",
          "Last Close Price", "Net Change", "Open Price", "High Price",
          "Low Price", "Total Value", "Nb. Trades", "Settlement Price",
          "Open Interest", "Implied Volatility", "Accrued Financing Value",
          "Accrued Financing Date", "CORRA Rate"]


def option(date, strike, cp, bid, ask, volume, settle, oi=10, iv=20.0):
    letter = "C" if cp == 0 else "P"
    sym = f"ZZZ   270115{letter}{strike:.2f}"
    return [date, sym, "ZZZ", "ZZZ", "ZZZ", strike, "2027-01-15", cp, 0, bid,
            ask, 5, 5, 0.0, volume, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, settle, oi,
            iv, 0, "", 0]


def underlying(date, bid, ask, last):
    return [date, "ZZZ", "", "", "", 0, "", 0, 3, bid, ask, 1, 1, last, 1000,
            last, 0.0, 0.0, last, 0.0, 0, 0, 0.0, 0, 0.0, 0, "", 0]


ROWS = [
    underlying("2026-11-02", 49.98, 50.02, 50.01),
    option("2026-11-02", 50.0, 0, 2.00, 2.10, 7, 2.05),          # traded
    option("2026-11-02", 50.0, 1, 1.80, 1.90, 0, 1.86),          # quoted
    option("2026-11-02", 70.0, 0, 0.00, 0.05, 0, 0.01, iv=0.0),  # settlement only
    option("2026-11-02", 90.0, 0, 0.00, 0.00, 0, 0.00, oi=0),    # nothing
]


def write(tmp_path, rows, name="quote_ZZZ.csv", header=HEADER):
    path = tmp_path / name
    pd.DataFrame(rows, columns=header).to_csv(path, index=False)
    return path


@pytest.fixture
def opts(tmp_path):
    return load_mx_options(write(tmp_path, ROWS))


def test_tidy_columns_and_types(opts):
    assert list(opts.columns) == OPTION_COLUMNS
    assert len(opts) == 4                       # the underlying row is not an option
    assert set(opts["call_put"]) == {"C", "P"}
    assert str(opts["date"].dtype).startswith("datetime64")
    assert str(opts["expiry"].dtype).startswith("datetime64")


def test_three_tiers_of_evidence(opts):
    tier = {(r.strike, r.call_put): r.tier for r in opts.itertuples()}
    assert tier[(50.0, "C")] == TRADED
    assert tier[(50.0, "P")] == QUOTED
    assert tier[(70.0, "C")] == SETTLEMENT_ONLY
    assert tier[(90.0, "C")] == NO_PRICE
    counts = tier_counts(opts).loc["ZZZ"]
    assert list(counts) == [1, 1, 1, 1]


def test_mark_is_mid_when_quoted_else_settlement_else_empty(opts):
    row = {(r.strike, r.call_put): r for r in opts.itertuples()}
    assert row[(50.0, "C")].mark == pytest.approx(2.05)
    assert row[(50.0, "C")].mark_source == "mid"
    assert row[(50.0, "P")].mark == pytest.approx(1.85)       # mid, not the 1.86 settle
    assert row[(70.0, "C")].mark == pytest.approx(0.01)
    assert row[(70.0, "C")].mark_source == "settle"
    assert math.isnan(row[(90.0, "C")].mark)                  # never a guessed price
    assert row[(90.0, "C")].mark_source == "none"


def test_settlement_outside_the_quote_is_flagged(tmp_path):
    """A settlement outside the bid-ask cannot come from the same instant as
    the quote, so the row is marked rather than silently mixed."""
    rows = ROWS + [option("2026-11-02", 55.0, 0, 1.00, 1.10, 0, 1.25)]
    o = load_mx_options(write(tmp_path, rows))
    flag = {(r.strike, r.call_put): r.settle_in_quote for r in o.itertuples()}
    assert flag[(50.0, "C")] is True
    assert flag[(55.0, "C")] is False
    assert flag[(70.0, "C")] is None          # no two-sided quote to compare with


def test_exchange_vol_is_a_decimal_and_empty_when_zero(opts):
    row = {(r.strike, r.call_put): r for r in opts.itertuples()}
    assert row[(50.0, "C")].mx_iv == pytest.approx(0.20)
    assert math.isnan(row[(70.0, "C")].mx_iv)


def test_underlying_table(tmp_path):
    und = load_mx_underlying(write(tmp_path, ROWS))
    assert len(und) == 1
    r = und.iloc[0]
    assert r["underlying"] == "ZZZ"
    assert r["mid"] == pytest.approx(50.00) and r["last"] == pytest.approx(50.01)


def test_missing_option_columns_stop_the_load(tmp_path):
    keep = [c for c in HEADER if c not in ("Bid Price", "Settlement Price")]
    df = pd.DataFrame(ROWS, columns=HEADER)[keep]
    path = tmp_path / "bad.csv"
    df.to_csv(path, index=False)
    with pytest.raises(ValueError, match="missing columns"):
        load_mx_options(path)


def test_call_put_code_must_agree_with_symbol(tmp_path):
    bad = option("2026-11-02", 55.0, 0, 1.0, 1.1, 0, 1.05)
    bad[1] = bad[1].replace("C", "P")
    with pytest.raises(ValueError, match="disagrees"):
        load_mx_options(write(tmp_path, ROWS + [bad]))


def test_overlapping_files_identical_rows_collapse(tmp_path):
    a = write(tmp_path, ROWS, "a.csv")
    b = write(tmp_path, ROWS, "b.csv")
    assert len(load_mx_options([a, b])) == 4
    assert len(load_mx_options(tmp_path)) == 4            # a folder works too


def test_overlapping_files_conflicting_rows_raise(tmp_path):
    changed = [list(r) for r in ROWS]
    changed[1][22] = 2.50                                  # a different settlement
    a = write(tmp_path, ROWS, "a.csv")
    b = write(tmp_path, changed, "b.csv")
    with pytest.raises(ValueError, match="different"):
        load_mx_options([a, b])


def test_unknown_instrument_type_raises(tmp_path):
    odd = option("2026-11-02", 55.0, 0, 1.0, 1.1, 0, 1.05)
    odd[8] = 7
    with pytest.raises(ValueError, match="Ins. Type"):
        load_mx_options(write(tmp_path, ROWS + [odd]))
