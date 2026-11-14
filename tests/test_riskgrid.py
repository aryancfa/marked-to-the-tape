"""The vega grid and the index-mapped delta (C2): made-up numbers only."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from riskgrid import (cell_order, is_populated, mapped_delta, moneyness_cell,
                      name_has_surface, off_market, surface_strikes, vega_grid)

E1, E2 = date(2027, 1, 15), date(2027, 3, 19)


def test_cells_match_the_front_chart_and_cover_everything():
    assert moneyness_cell(100, 100) == "0.98-1.02"
    assert moneyness_cell(102, 100) == "0.98-1.02"          # upper edge belongs to the cell
    assert moneyness_cell(80, 100) == "under 0.88"
    assert moneyness_cell(130, 100) == "over 1.12"
    labels = cell_order()
    assert len(labels) == 7 and all(moneyness_cell(k, 100) in labels for k in range(50, 160))


def test_strikes_by_expiry_ignore_rows_with_no_vol():
    inv = pd.DataFrame({"expiry": [pd.Timestamp(E1)] * 4 + [pd.Timestamp(E2)],
                        "strike": [95, 100, 100, 105, 100],
                        "iv": [0.2, 0.21, 0.22, np.nan, 0.2]})
    got = surface_strikes(inv)
    assert got[E1].tolist() == [95.0, 100.0] and got[E2].tolist() == [100.0]


def test_a_cell_needs_a_smile_and_strikes_of_its_own():
    wide = np.arange(80.0, 125.0, 2.5)                       # 18 strikes
    assert is_populated(wide, 100, 100)
    assert not is_populated(wide[:6], 100, 100)              # too few for a smile
    assert not is_populated(wide, 150, 100)                  # beyond the last quoted strike
    assert is_populated(wide, 120, 100)                      # same outer cell, inside the range
    assert not is_populated(None, 100, 100)
    gap = np.array([80, 85, 90, 95, 97, 103, 110, 115, 120.0])
    assert not is_populated(gap, 100, 100)                   # no strike in 0.98-1.02 at all


def test_a_name_with_no_full_expiry_is_delta_only():
    assert name_has_surface({E1: np.arange(7.0), E2: np.arange(2.0)})
    assert not name_has_surface({E1: np.arange(6.0)})
    assert not name_has_surface({})


def test_vega_off_the_surface_is_kept_apart_not_dropped():
    rows = [dict(name="AAA", maturity="31-90d", moneyness="0.98-1.02", vega=500.0,
                 populated=True, delta_only=False),
            dict(name="AAA", maturity="91-180d", moneyness="over 1.12", vega=120.0,
                 populated=False, delta_only=False),
            dict(name="BBB", maturity="31-90d", moneyness="0.98-1.02", vega=80.0,
                 populated=True, delta_only=True)]
    g = vega_grid(rows)
    assert g["vega"].sum() == 500.0 and g["off"].sum() == 200.0
    assert g["vega"].sum() + g["off"].sum() == sum(r["vega"] for r in rows)


@pytest.mark.filterwarnings("ignore:Bitwise inversion:DeprecationWarning")
def test_flags_stored_as_objects_are_read_as_flags():
    # the warning silenced here is Python saying the same thing: ~ on a bool
    # does not negate it. The test sets the trap off on purpose.
    # a table that also holds share rows stores the option flags as objects,
    # where ~False is -1 and so counts as true; that once sent every option off
    t = pd.DataFrame([dict(name="AAA", maturity="31-90d", moneyness="0.98-1.02", vega=500.0,
                           populated=True, delta_only=False),
                      dict(name="AAA", maturity="31-90d", moneyness="0.98-1.02", vega=70.0,
                           populated=False, delta_only=False),
                      dict(name="AAA", vega=np.nan)])
    opt = t.iloc[:2]
    assert opt["populated"].dtype == object
    assert bool((~opt["populated"]).iloc[0])                  # the trap: ~True is -2
    assert off_market(opt).tolist() == [False, True]
    g = vega_grid(opt)
    assert g["vega"].sum() == 500.0 and g["off"].sum() == 70.0


def test_delta_is_mapped_by_beta_and_kept_by_name():
    t = mapped_delta({"AAA": 1_000_000.0, "BBB": -200_000.0}, {"AAA": 0.8, "BBB": 1.5})
    assert t.loc["AAA", "index_delta"] == pytest.approx(800_000.0)
    assert t["index_delta"].sum() == pytest.approx(500_000.0)
    assert t["delta"].sum() == pytest.approx(800_000.0)      # per-name deltas kept


def test_a_name_without_a_beta_stops_the_total():
    with pytest.raises(ValueError, match="BBB"):
        mapped_delta({"AAA": 1.0, "BBB": 1.0}, {"AAA": 1.0})
    with pytest.raises(ValueError):
        mapped_delta({"AAA": 1.0}, {"AAA": float("nan")})
