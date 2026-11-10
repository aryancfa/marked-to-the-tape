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


def test_plus_adds_a_funding_spread_at_every_tenor_and_records_it():
    from rates import Curve
    base = Curve(date(2026, 11, 2), ((1 / 365, 0.025), (1.0, 0.030)))
    funded = base.plus(0.002)
    for T in (0.0, 0.4, 1.0, 2.0):
        assert funded.rate(T) == pytest.approx(base.rate(T) + 0.002)
    assert funded.spread == pytest.approx(0.002) and base.spread == 0.0


# ---------------------------------------------------------------------------
# A curve from three-month CORRA futures (made-up prices)
# ---------------------------------------------------------------------------

import math
from datetime import timedelta

from rates import futures_curve

DAY = date(2026, 11, 2)
Q0, Q1, Q2, Q3 = (date(2026, 9, 16), date(2026, 12, 16), date(2027, 3, 17),
                  date(2027, 6, 16))                 # third Wednesdays


def fixings(f, first=date(2026, 6, 1), last=DAY):
    """Overnight fixings on weekdays that compound to exp(f * time)."""
    out, d = {}, first
    while d <= last:
        if d.weekday() < 5:
            n = 3 if d.weekday() == 4 else 1
            out[d] = (math.exp(f * n / 365) - 1) * 365 / n
        d += timedelta(days=1)
    return out


def price(growth, start, end):
    """Futures price for a quarter over which money grows by `growth`."""
    return 100.0 - 100.0 * (growth - 1.0) * 365 / (end - start).days


def grow(f, start, end):
    return math.exp(f * (end - start).days / 365)


def test_futures_curve_recovers_a_flat_rate():
    f = 0.025
    quotes = [(DAY, e, price(grow(f, s, e), s, e))
              for s, e in ((Q0, Q1), (Q1, Q2), (Q2, Q3))]
    c = futures_curve(DAY, quotes, fixings(f))
    for T in (0.0, 10 / 365, 0.3, 0.6):
        assert c.rate(T) == pytest.approx(f, abs=1e-7)
    assert c.source == "CORRA futures" and c.stale == []


def test_the_part_of_the_quarter_already_past_is_taken_out():
    """Overnight money paid 2% up to today. The front future prices the whole
    quarter, and it implies 3% for the days still to come (a rate rise is
    expected). The curve must say 3%, not a blend with the 2% already paid."""
    front = grow(0.02, Q0, DAY) * grow(0.03, DAY, Q1)
    quotes = [(DAY, Q1, price(front, Q0, Q1)),
              (DAY, Q2, price(grow(0.03, Q1, Q2), Q1, Q2))]
    c = futures_curve(DAY, quotes, fixings(0.02))
    for T in (5 / 365, 0.1, 0.3):
        assert c.rate(T) == pytest.approx(0.03, abs=1e-7)


def test_rates_average_over_the_quarters_they_span():
    """3% to the end of the first quarter, 4% in the second. A date half way
    through the second quarter gets the time-weighted average."""
    quotes = [(DAY, Q1, price(grow(0.03, Q0, Q1), Q0, Q1)),
              (DAY, Q2, price(grow(0.04, Q1, Q2), Q1, Q2))]
    c = futures_curve(DAY, quotes, fixings(0.03))
    t1 = (Q1 - DAY).days / 365
    T = t1 + 45 / 365
    assert c.rate(T) == pytest.approx((0.03 * t1 + 0.04 * 45 / 365) / T, abs=1e-7)


def test_futures_from_the_previous_day_are_carried_and_flagged():
    yesterday = DAY - timedelta(days=3)                  # the Friday before
    quotes = [(yesterday, Q1, price(grow(0.025, Q0, Q1), Q0, Q1))]
    c = futures_curve(DAY, quotes, fixings(0.025))
    assert c.stale == [("CRA", yesterday)]
    with pytest.raises(ValueError, match="no futures"):
        futures_curve(DAY + timedelta(days=30), quotes, fixings(0.025))


def test_a_strip_that_does_not_start_at_the_current_quarter_raises():
    quotes = [(DAY, Q2, price(grow(0.025, Q1, Q2), Q1, Q2))]
    with pytest.raises(ValueError, match="front"):
        futures_curve(DAY, quotes, fixings(0.025))


def test_missing_overnight_fixings_raise():
    quotes = [(DAY, Q1, price(grow(0.025, Q0, Q1), Q0, Q1))]
    with pytest.raises(ValueError, match="fixing"):
        futures_curve(DAY, quotes, fixings(0.025, first=date(2026, 10, 1)))


def test_a_spread_keeps_the_source_and_the_way_of_interpolating():
    quotes = [(DAY, Q1, price(grow(0.03, Q0, Q1), Q0, Q1)),
              (DAY, Q2, price(grow(0.04, Q1, Q2), Q1, Q2))]
    c = futures_curve(DAY, quotes, fixings(0.03))
    for T in (0.05, 0.2, 0.35):
        assert c.plus(0.001).rate(T) == pytest.approx(c.rate(T) + 0.001)
    assert c.plus(0.001).source == "CORRA futures"


def test_base_curve_prefers_futures_and_falls_back_to_bills(folder):
    """Futures when the day has them, bills when it does not, and the curve
    says which it is."""
    from rates import load_base_curve
    f = 0.025
    write(folder, "AVG.INTWO", [(d.isoformat(), f"{v * 100:.6f}")
                                for d, v in fixings(f).items()])
    quotes = [(DAY, Q1, price(grow(f, Q0, Q1), Q0, Q1))]
    c = load_base_curve(folder, DAY, quotes)
    assert c.source == "CORRA futures" and c.rate(0.05) == pytest.approx(f, abs=1e-6)
    assert load_base_curve(folder, DAY, []).source == "Treasury bills"
    assert load_base_curve(folder, DAY, None).source == "Treasury bills"
    old = [(DAY - timedelta(days=40), Q1, 97.5)]
    assert load_base_curve(folder, DAY, old).source == "Treasury bills"
