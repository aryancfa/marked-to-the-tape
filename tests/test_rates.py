"""
test_rates.py: the rate curve on SYNTHETIC files in the Bank of Canada layout.
The yields below are made up.
"""

import math
from datetime import date

import pytest

from rates import (TENORS, Curve, continuous_rate, flat_curve, load_curve,
                   read_boc_series)

DAY = date(2026, 11, 2)
YIELDS = {"AVG.INTWO": 2.00, "TB.CDN.30D.MID": 2.10, "TB.CDN.90D.MID": 2.30,
          "TB.CDN.180D.MID": 2.60, "TB.CDN.1Y.MID": 3.00}


def write(folder, series, rows):
    text = ('"TERMS AND CONDITIONS"\n"https://example.invalid/terms/"\n\n'
            '"SERIES"\n"id","label","description","date_format"\n'
            f'"{series}","made up","made up","daily"\n\n'
            f'"OBSERVATIONS"\n"date","{series}"\n')
    text += "".join(f'"{d}","{v}"\n' for d, v in rows)
    (folder / f"{series}.csv").write_text(text, encoding="utf-8-sig")


@pytest.fixture
def folder(tmp_path):
    for s, y in YIELDS.items():
        write(tmp_path, s, [("2026-10-30", f"{y - 0.01:.2f}"), ("2026-11-02", f"{y:.2f}")])
    return tmp_path


def test_reads_percent_as_decimal_and_skips_blanks(tmp_path):
    write(tmp_path, "AVG.INTWO", [("2026-10-30", "2.00"), ("2026-11-02", "")])
    obs = read_boc_series(tmp_path / "AVG.INTWO.csv")
    assert obs == {date(2026, 10, 30): pytest.approx(0.02)}


def test_simple_yield_to_continuous():
    """A 3% simple yield for one year grows 1 to 1.03, so the continuous rate
    is ln(1.03), a little under 3%."""
    assert continuous_rate(0.03, 1.0) == pytest.approx(math.log(1.03))
    assert continuous_rate(0.03, 1 / 365) == pytest.approx(0.03, abs=2e-6)


def test_curve_hits_each_tenor_and_interpolates_between(folder):
    c = load_curve(folder, DAY)
    for s, tenor in TENORS.items():
        assert c.rate(tenor) == pytest.approx(continuous_rate(YIELDS[s] / 100, tenor))
    lo, hi = c.rate(90 / 365), c.rate(180 / 365)
    assert c.rate(135 / 365) == pytest.approx(0.5 * (lo + hi))
    assert c(135 / 365) == c.rate(135 / 365)            # callable


def test_curve_is_flat_beyond_its_ends(folder):
    c = load_curve(folder, DAY)
    assert c.rate(0.0) == c.rate(1 / 365)
    assert c.rate(3.0) == c.rate(1.0)
    assert c.longest_tenor == 1.0


def test_upward_curve_gives_longer_expiries_a_higher_rate(folder):
    c = load_curve(folder, DAY)
    assert c.rate(18 / 365) < c.rate(165 / 365) < c.rate(319 / 365)


def test_bond_holiday_carries_the_last_observation_and_says_so(folder):
    c = load_curve(folder, date(2026, 11, 3))          # no observation that day
    assert c.rate(1.0) == pytest.approx(continuous_rate(0.03, 1.0))
    assert len(c.stale) == len(TENORS)
    assert all(d == DAY for _, d in c.stale)
    assert load_curve(folder, DAY).stale == []


def test_too_stale_or_missing_raises(folder):
    with pytest.raises(ValueError):
        load_curve(folder, date(2026, 11, 20))
    (folder / "TB.CDN.1Y.MID.csv").unlink()
    with pytest.raises(FileNotFoundError):
        load_curve(folder, DAY)


def test_flat_curve():
    c = flat_curve(DAY, 0.025)
    assert c.rate(0.01) == c.rate(2.0) == 0.025
    assert isinstance(c, Curve)
