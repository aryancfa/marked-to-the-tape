"""
test_replay.py: valuing a book day by day (src/replay.py). Every price here
is made up: options are priced by the engine under inputs the test chooses,
so each answer is known in advance.
"""

from datetime import date

import pandas as pd
import pytest

from book import Position
from references import PAIR, SMILE
from replay import (NO_MARK, NO_SERIES, DayInputs, check_mark, dispute_dollars,
                    dividend_exposure, dividends_gone_ex, explain, fails, greeks_table,
                    maturity_bucket, next_session, reprice_with, value_position)
from surface import dividend_times
from tradingcal import year_fraction
from voleng import crr_american

D0, D1 = date(2026, 6, 1), date(2026, 6, 2)
EXPIRY = date(2026, 9, 18)
RATE = 0.03


def flat(T):
    return RATE


def option(cp="C", K=100.0, qty=10.0):
    return Position("option", "ZZZ", qty, 100.0, "MX", cp, K, EXPIRY)


def chain(day, spot, vol, divs, rows=(("C", 100.0), ("P", 100.0)), spread=0.10,
          two_sided=True, shift=0.0):
    """Exchange-style rows priced by the engine at `vol`."""
    out = []
    for cp, K in rows:
        T = year_fraction(day, EXPIRY)
        px = crr_american(spot, K, T, RATE, 0.0, vol, dividend_times(day, divs, EXPIRY),
                          cp == "C") + shift
        out.append(dict(date=pd.Timestamp(day), underlying="ZZZ", expiry=pd.Timestamp(EXPIRY),
                        strike=K, call_put=cp,
                        bid=px - spread / 2 if two_sided else 0.0,
                        ask=px + spread / 2 if two_sided else 0.0,
                        settle=px, two_sided=two_sided,
                        tier="quoted" if two_sided else "settlement_only",
                        mark=px, mark_source="mid" if two_sided else "settle"))
    return pd.DataFrame(out)


def inputs(day, spot, vol, divs=(), **kw):
    return DayInputs(day, spot, flat, list(divs), chain(day, spot, vol, divs, **kw))


# ---- valuing ---------------------------------------------------------------

def test_the_vol_that_priced_the_option_is_recovered_with_its_greeks():
    v = value_position(option(), inputs(D0, 100.0, 0.25))
    assert v.ok and v.iv == pytest.approx(0.25, abs=1e-4)
    assert 0.4 < v.delta < 0.7 and v.gamma > 0 and v.vega > 0 and v.theta < 0
    assert v.value == pytest.approx(1000 * v.price)
    assert v.half_spread == pytest.approx(0.05)


def test_a_missing_series_or_mark_gets_a_reason_and_no_vol():
    inp = inputs(D0, 100.0, 0.25)
    assert value_position(option(K=140.0), inp).reason == NO_SERIES
    inp.options.loc[0, "mark"] = float("nan")
    v = value_position(option(), inp)
    assert v.reason == NO_MARK and v.iv is None and not v.ok


def test_a_share_is_valued_at_the_share_price_with_delta_one():
    v = value_position(Position("equity", "ZZZ", -500.0, 1.0, "TSX"), inputs(D0, 100.0, 0.25))
    assert v.value == pytest.approx(-50_000.0) and v.delta == 1.0 and v.vega == 0.0


# ---- why the value changed -------------------------------------------------

def both_days(spot1=100.0, vol1=0.25, divs0=(), divs1=None, pos=None):
    pos = pos or option()
    a = inputs(D0, 100.0, 0.25, divs0)
    b = inputs(D1, spot1, vol1, divs0 if divs1 is None else divs1)
    return pos, value_position(pos, a), value_position(pos, b), a, b


def test_the_steps_always_add_up_to_the_change_in_the_mark():
    pos, v0, v1, a, b = both_days(spot1=103.0, vol1=0.28)
    e = explain(v0, v1, a, b)
    assert sum(e[k] for k in ("carry", "dividends", "delta", "gamma", "vol")) \
        == pytest.approx(e["total"], abs=1e-6)
    assert e["total"] == pytest.approx(v1.value - v0.value)


def test_nothing_moves_so_only_carry_is_left_and_it_is_decay():
    pos, v0, v1, a, b = both_days()
    e = explain(v0, v1, a, b)
    assert e["carry"] < 0 and e["carry"] == pytest.approx(v0.dollars("theta"), rel=0.15)
    assert abs(e["delta"]) < 1e-9 and abs(e["gamma"]) < 1e-6
    assert abs(e["vol"]) < 0.5 and e["dividends"] == 0.0


def test_a_share_move_lands_in_delta_and_gamma_and_a_vol_move_in_vol():
    pos, v0, v1, a, b = both_days(spot1=102.0)
    e = explain(v0, v1, a, b)
    assert e["delta"] == pytest.approx(1000 * v0.delta * 2.0)
    assert e["gamma"] > 0 and abs(e["vol"]) < 1.0           # long option: curvature pays
    pos, v0, v1, a, b = both_days(vol1=0.27)
    e = explain(v0, v1, a, b)
    assert e["vol"] == pytest.approx(2.0 * v0.dollars("vega"), rel=0.05)
    assert abs(e["delta"]) < 1e-9


def test_the_other_order_keeps_the_total_and_moves_only_the_interaction():
    # one input moves: the order cannot matter
    for kw in (dict(spot1=103.0), dict(vol1=0.28)):
        pos, v0, v1, a, b = both_days(**kw)
        e = explain(v0, v1, a, b, other_order=True)
        assert e["vol_first"] == pytest.approx(e["vol"], abs=0.02)
    # both move, on an out-of-the-money call whose vega grows as the share
    # rises: vol counts for more when it is stepped at the higher share price
    pos, rows = option(K=110.0), (("C", 110.0), ("P", 110.0))
    a, b = inputs(D0, 100.0, 0.25, rows=rows), inputs(D1, 106.0, 0.30, rows=rows)
    v0, v1 = value_position(pos, a), value_position(pos, b)
    plain = explain(v0, v1, a, b)
    e = explain(v0, v1, a, b, other_order=True)
    assert {k: e[k] for k in plain} == plain                  # nothing else changes
    assert e["vol"] - e["vol_first"] > 1.0
    share_second = e["delta"] + e["gamma"] + e["vol"] - e["vol_first"]
    assert e["carry"] + e["dividends"] + e["vol_first"] + share_second         == pytest.approx(e["total"], abs=1e-6)


def test_an_expected_ex_dividend_drop_is_carry_not_a_share_move():
    divs = [(D1, 1.00)]
    pos, v0, v1, a, b = both_days(spot1=99.0, divs0=divs)     # share opens 1.00 lower
    e = explain(v0, v1, a, b)
    assert dividends_gone_ex(divs, D0, D1) == 1.0
    assert abs(e["delta"]) < 1e-9 and abs(e["gamma"]) < 1e-6
    assert abs(e["vol"]) < 1.0                                # nothing surprising happened


def test_an_announced_change_to_a_dividend_shows_as_dividends():
    before, after = [(date(2026, 7, 24), 1.00)], [(date(2026, 7, 24), 1.50)]
    pos, v0, v1, a, b = both_days(divs0=before, divs1=after)
    e = explain(v0, v1, a, b)
    assert e["dividends"] < -50                # a call loses when the dividend grows
    assert abs(e["vol"]) < 1.0


def test_a_share_position_keeps_its_dividend():
    divs = [(D1, 1.00)]
    pos, v0, v1, a, b = both_days(spot1=99.0, divs0=divs,
                                  pos=Position("equity", "ZZZ", 100.0, 1.0, "TSX"))
    assert explain(v0, v1, a, b)["total"] == pytest.approx(0.0)


def test_no_mark_on_either_day_means_no_explanation():
    pos, v0, v1, a, b = both_days()
    b.options.loc[:, "mark"] = float("nan")
    assert explain(v0, value_position(pos, b), a, b) is None


# ---- marks in dispute ------------------------------------------------------

def checks(pair=None, smile=None, bar=0.5, side="C", poor=False):
    rows = []
    if pair is not None:
        rows.append(dict(expiry=EXPIRY, strike=100.0, reference=PAIR, side="pair",
                         diff=pair, quote_bar=2 * bar, poor_fit=False))
    if smile is not None:
        rows.append(dict(expiry=EXPIRY, strike=100.0, reference=SMILE, side=side,
                         diff=smile, quote_bar=bar, poor_fit=poor))
    return pd.DataFrame(rows)


def test_a_difference_fails_only_beyond_the_quote_bar_and_the_noise_floor():
    assert fails(0.6, 0.5) and not fails(0.4, 0.5)
    assert not fails(0.03, 0.0)                 # inside the solver's noise
    assert fails(0.6, 1.0, bars=0.5) and not fails(None, 0.5)


def test_a_mark_off_its_smile_is_flagged_and_sized_in_dollars():
    v = value_position(option(), inputs(D0, 100.0, 0.25))
    c = check_mark(v, checks(pair=0.3, smile=2.0))
    assert c.failed == ["smile"] and c.worst == 2.0
    assert dispute_dollars(v, c) == pytest.approx(2.0 * abs(v.dollars("vega")))
    ok = check_mark(v, checks(pair=0.3, smile=0.2))
    assert ok.failed == [] and dispute_dollars(v, ok) == 0.0


def test_the_smile_check_is_for_the_positions_own_side_and_skips_poor_fits():
    v = value_position(option("C"), inputs(D0, 100.0, 0.25))
    assert check_mark(v, checks(smile=2.0, side="P")).smile is None
    assert check_mark(v, checks(smile=2.0, poor=True)).failed == []


def test_marks_that_cannot_be_checked_say_why():
    inp = inputs(D0, 100.0, 0.25, two_sided=False)
    v = value_position(option(), inp)
    c = check_mark(v, checks(pair=0.1))
    assert any("settlement only" in n for n in c.notes)
    assert any("outside" in n for n in check_mark(v, checks(smile=1.0, side="P")).notes)
    assert any("no references" in n for n in check_mark(v, None).notes)


# ---- dividend exposure -----------------------------------------------------

def test_a_bigger_dividend_costs_a_call_and_pays_a_put():
    divs = [(date(2026, 7, 24), 1.50)]
    inp = inputs(D0, 100.0, 0.25, divs)
    call = dividend_exposure(value_position(option("C"), inp), inp)
    put = dividend_exposure(value_position(option("P"), inp), inp)
    assert call["amount"] < 0 < put["amount"]


def test_a_date_one_session_later_matters_only_across_the_expiry():
    inside = [(date(2026, 7, 24), 1.50)]
    inp = inputs(D0, 100.0, 0.25, inside)
    v = value_position(option("P"), inp)
    assert abs(dividend_exposure(v, inp)["date"]) < 0.02 * abs(dividend_exposure(v, inp)["amount"]) + 1.0
    on_expiry = [(EXPIRY, 1.50)]                 # ex-date on expiry counts; a day later it is gone
    inp = inputs(D0, 100.0, 0.25, on_expiry)
    v = value_position(option("P"), inp)
    assert dividend_exposure(v, inp)["date"] < -300          # the put loses the whole dividend


def test_pricing_one_named_dividend_error():
    inp = inputs(D0, 100.0, 0.25, [(date(2026, 7, 24), 1.50)])
    v = value_position(option("C"), inp)
    assert reprice_with(v, inp, inp.divs) == pytest.approx(0.0)
    assert reprice_with(v, inp, []) > 0          # no dividend: the call is worth more


def test_next_session_skips_the_weekend():
    assert next_session(date(2026, 6, 5)) == date(2026, 6, 8)


# ---- where the vega sits ---------------------------------------------------

def test_greeks_add_up_by_name_and_bucket_and_count_what_could_not_be_valued():
    inp = inputs(D0, 100.0, 0.25)
    vs = [value_position(p, inp) for p in
          (option("C"), option("P", qty=-5.0), option(K=140.0),
           Position("equity", "ZZZ", -200.0, 1.0, "TSX"))]
    t = greeks_table(vs, {"ZZZ": 100.0})
    opt = t.loc[("ZZZ", maturity_bucket(D0, EXPIRY))]
    assert opt["not_valued"] == 1
    assert opt["vega"] == pytest.approx(vs[0].dollars("vega") + vs[1].dollars("vega"))
    assert t.loc[("ZZZ", "shares"), "delta"] == pytest.approx(-20_000.0)
    assert maturity_bucket(D0, EXPIRY) == "91-180d"
