"""
test_surface.py: chain inversion and the call-put gap on a SYNTHETIC chain.

Prices are produced by the engine from a made-up share price, rate, dividend
and vol. Nothing here is market data.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from surface import (EXPIRED, NO_MARK, call_put_gap, dividend_times,
                     front_pairs, invert_chain, rejection_tally)
from tradingcal import year_fraction
from voleng import Reject, crr_american

VAL = date(2026, 11, 2)
NEAR, FAR = date(2026, 12, 18), date(2027, 3, 19)
S0, R, SIG = 50.0, 0.03, 0.20
DIVS = [(date(2027, 1, 25), 0.50)]          # ex-date falls only before FAR
HALF = 0.03                                 # half the quoted spread, dollars


def chain(strikes=(47.5, 50.0, 52.5), expiries=(NEAR, FAR)):
    rows = []
    for e in expiries:
        T = year_fraction(VAL, e)
        for K in strikes:
            for cp in "CP":
                px = crr_american(S0, K, T, R, 0.0, SIG,
                                  dividend_times(VAL, DIVS), cp == "C")
                rows.append(dict(
                    date=pd.Timestamp(VAL), underlying="ZZZ",
                    expiry=pd.Timestamp(e), strike=K, call_put=cp,
                    bid=px - HALF, ask=px + HALF, settle=px, volume=0,
                    open_interest=0, traded=False, two_sided=True,
                    tier="quoted", mark=px, mark_source="mid"))
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def inverted():
    return invert_chain(chain(), S0, R, DIVS)


def test_recovers_the_vol_and_gives_each_row_an_error_bar(inverted):
    assert (inverted["reason"] == Reject.OK).all()
    assert np.allclose(inverted["iv"], SIG, atol=1e-5)
    # the bid vol sits below the mark vol and the ask vol above it
    assert (inverted["iv_bid"] < inverted["iv"]).all()
    assert (inverted["iv_ask"] > inverted["iv"]).all()
    assert (inverted["half_spread_vol"] > 0).all()


def test_quoted_rows_are_not_rejected_for_lacking_volume(inverted):
    """Zero volume and zero open interest, but a live two-sided quote."""
    assert rejection_tally(inverted).to_dict() == {Reject.OK: len(inverted)}


def test_gap_is_zero_with_the_right_inputs(inverted):
    gap = call_put_gap(inverted)
    assert len(gap) == 6
    assert gap["gap"].abs().max() < 0.01            # vol points
    assert gap["gap_in_error_bars"].abs().max() < 0.1


def test_missing_dividend_shows_as_a_flat_gap_on_that_expiry_only():
    """Leave the dividend out. The expiry before the ex-date is untouched;
    the expiry after it shows calls cheap and puts dear at EVERY strike,
    which is the signature of a carry error and not of skew."""
    gap = call_put_gap(invert_chain(chain(), S0, R, divs=[]))
    near = gap[gap["expiry"] == pd.Timestamp(NEAR)]["gap"]
    far = gap[gap["expiry"] == pd.Timestamp(FAR)]["gap"]
    assert near.abs().max() < 0.01
    assert (far < -1.0).all()                       # more than a vol point, same sign


def test_rows_that_cannot_be_inverted_keep_a_reason_and_no_vol():
    c = chain(strikes=(50.0,), expiries=(NEAR,))
    dead = c.iloc[[0]].assign(mark=np.nan, two_sided=False, tier="no_price",
                              mark_source="none", strike=60.0)
    gone = c.iloc[[0]].assign(expiry=pd.Timestamp(VAL), strike=55.0)
    cheap = c.iloc[[0]].assign(mark=0.5, bid=0.45, ask=0.55, strike=40.0)
    out = invert_chain(pd.concat([c, dead, gone, cheap], ignore_index=True),
                       S0, R, DIVS)
    reason = dict(zip(out["strike"], out["reason"]))
    assert reason[60.0] == NO_MARK
    assert reason[55.0] == EXPIRED
    assert reason[40.0] == Reject.BELOW_INTRINSIC   # a 40 call on a 50 share at 0.50
    assert out.loc[out["reason"] != Reject.OK, "iv"].isna().all()


def test_settlement_marks_keep_the_activity_screen():
    c = chain(strikes=(50.0,), expiries=(NEAR,)).iloc[[0]]
    c = c.assign(two_sided=False, bid=0.0, ask=0.0, tier="settlement_only",
                 mark_source="settle")
    assert invert_chain(c, S0, R, DIVS)["reason"].iloc[0] == Reject.NO_ACTIVITY
    live = c.assign(open_interest=25)
    out = invert_chain(live, S0, R, DIVS)
    assert out["reason"].iloc[0] == Reject.OK and np.isnan(out["half_spread_vol"].iloc[0])


def test_front_pairs_takes_the_nearest_expiry_and_nearest_strikes():
    c = chain(strikes=(45.0, 47.5, 50.0, 52.5, 55.0))
    expiry, pairs = front_pairs(c, approx_spot=50.4, k=3)
    assert expiry == NEAR
    assert [p[0] for p in pairs] == [47.5, 50.0, 52.5]
    assert len(front_pairs(c, approx_spot=50.4)[1]) == 5      # five by default
    # an expiry inside the minimum window is skipped
    expiry, _ = front_pairs(c, approx_spot=50.4, min_days=60, k=3)
    assert expiry == FAR


def test_front_pairs_skips_weeklies_unless_asked():
    weekly = date(2026, 11, 13)                 # a Friday, but not the third
    c = chain(strikes=(47.5, 50.0, 52.5), expiries=(weekly, NEAR))
    assert front_pairs(c, 50.0, k=3)[0] == NEAR
    assert front_pairs(c, 50.0, k=3, monthly_only=False)[0] == weekly


def test_rate_can_depend_on_expiry():
    """Price the far expiry at a higher rate than the near one. Inverting
    with one flat rate leaves a call-put gap on the far expiry; inverting
    with the rate for each expiry removes it."""
    def curve(T):
        return 0.03 if T < 0.25 else 0.045

    rows = []
    for e in (NEAR, FAR):
        T = year_fraction(VAL, e)
        for K in (47.5, 50.0, 52.5):
            for cp in "CP":
                px = crr_american(S0, K, T, curve(T), 0.0, SIG, [], cp == "C")
                rows.append(dict(
                    date=pd.Timestamp(VAL), underlying="ZZZ", expiry=pd.Timestamp(e),
                    strike=K, call_put=cp, bid=px - HALF, ask=px + HALF, settle=px,
                    volume=0, open_interest=0, traded=False, two_sided=True,
                    tier="quoted", mark=px, mark_source="mid"))
    c = pd.DataFrame(rows)
    flat = call_put_gap(invert_chain(c, S0, 0.03, []))
    far_flat = flat[flat["expiry"] == pd.Timestamp(FAR)]["gap"]
    assert (far_flat > 0.5).all()                 # rate too low: calls look dear
    by_expiry = invert_chain(c, S0, curve, [])
    assert call_put_gap(by_expiry)["gap"].abs().max() < 0.01
    assert set(by_expiry["r"].round(4)) == {0.03, 0.045}


def test_one_date_and_one_underlying_only():
    c = chain()
    two = pd.concat([c, c.assign(underlying="YYY")])
    with pytest.raises(ValueError):
        invert_chain(two, S0, R, DIVS)


# ---------------------------------------------------------------------------
# Error bars from vega, the day pipeline, and the gap statistics
# ---------------------------------------------------------------------------

from surface import (analyse_day, carry_label, gap_summary, near_the_money,
                     pool_across_days, vs_exchange)

WIDE = (45.0, 47.5, 50.0, 52.5, 55.0)


def test_vega_error_bar_matches_the_exact_one(inverted):
    """Default: half-spread in dollars divided by vega. exact_bars=True
    inverts the bid and the ask separately. For an ordinary quote the two
    agree, and the default costs a third of the tree evaluations."""
    exact = invert_chain(chain(), S0, R, DIVS, exact_bars=True)
    ratio = inverted["half_spread_vol"] / exact["half_spread_vol"]
    assert ratio.between(0.98, 1.02).all()
    assert np.allclose(inverted["iv"], exact["iv"])


def test_near_the_money_keeps_strikes_and_expiries_in_range():
    weekly = date(2026, 11, 6)                          # 4 days out
    c = chain(strikes=(40.0, 47.5, 50.0, 52.5, 60.0), expiries=(weekly, NEAR, FAR))
    kept = near_the_money(c, spot=50.0, moneyness=0.12, min_days=7, max_days=100)
    assert set(kept["strike"]) == {47.5, 50.0, 52.5}
    assert set(kept["expiry"]) == {pd.Timestamp(NEAR)}  # FAR is 137 days out


def test_carry_label():
    front, projected = NEAR, date(2027, 1, 25)
    assert carry_label(NEAR, front, projected) == "calibrated"
    assert carry_label(date(2027, 1, 15), front, projected) == "declared only"
    assert carry_label(FAR, front, projected) == "projected dividend"
    assert carry_label(FAR, front, None) == "declared only"


def _gap_frame(expiry, gaps, bar=3.0):
    return pd.DataFrame(dict(expiry=pd.Timestamp(expiry),
                             strike=50.0 + np.arange(len(gaps)), gap=gaps,
                             error_bar=bar))


def test_gap_summary_reports_scatter_standard_error_and_t():
    """Three strikes with gaps 0.1, 0.3, 0.5: mean 0.3, scatter 0.2. The
    mean is known to 0.2/sqrt(3), far tighter than the 3-point quote bar."""
    s = gap_summary(_gap_frame(FAR, [0.1, 0.3, 0.5]), VAL, front=NEAR)
    row = s.loc[FAR]
    assert row["days"] == (FAR - VAL).days and row["pairs"] == 3
    assert row["mean_gap"] == pytest.approx(0.3)
    assert row["scatter"] == pytest.approx(0.2)
    assert row["std_error"] == pytest.approx(0.2 / np.sqrt(3))
    assert row["t_stat"] == pytest.approx(0.3 / (0.2 / np.sqrt(3)))
    assert row["median_quote_bar"] == pytest.approx(3.0)
    assert row["carry"] == "declared only"


def test_gap_summary_gives_no_error_from_fewer_than_three_strikes():
    """Two points cannot say how much strikes scatter. No number, not a
    guessed one."""
    row = gap_summary(_gap_frame(FAR, [0.1, 0.5]), VAL).loc[FAR]
    assert row["mean_gap"] == pytest.approx(0.3)
    assert np.isnan(row["std_error"]) and np.isnan(row["t_stat"])


def test_pool_across_days_uses_the_day_to_day_scatter():
    daily = pd.DataFrame(dict(
        date=pd.to_datetime(["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05",
                             "2026-11-02"]),
        expiry=[FAR] * 4 + [NEAR], mean_gap=[0.2, 0.4, 0.2, 0.4, 0.1]))
    p = pool_across_days(daily)
    far = p.loc[FAR]
    assert far["days_observed"] == 4
    assert far["mean_gap"] == pytest.approx(0.3)
    sd = np.std([0.2, 0.4, 0.2, 0.4], ddof=1)
    assert far["scatter_across_days"] == pytest.approx(sd)
    assert far["std_error"] == pytest.approx(sd / 2.0)
    assert far["t_stat"] == pytest.approx(0.3 / (sd / 2.0))
    assert np.isnan(p.loc[NEAR, "std_error"])           # one day: no error


def test_vs_exchange_splits_calls_and_puts(inverted):
    inv = inverted.assign(mx_iv=np.where(inverted["call_put"] == "C",
                                         inverted["iv"] + 0.01, np.nan))
    v = vs_exchange(inv)
    assert v["calls_mean"] == pytest.approx(-1.0) and v["calls_n"] == 6
    assert np.isnan(v["puts_mean"]) and v["puts_n"] == 0


def test_analyse_day_recovers_the_share_price_and_a_zero_gap():
    """Hand it a share price 0.5% wrong. It implies the right one from the
    front expiry's pairs, then finds no call-put gap anywhere."""
    day = analyse_day(chain(strikes=WIDE), approx_spot=S0 * 1.005, rate=R,
                      divs=DIVS, first_projected=date(2027, 1, 25))
    assert day.front == NEAR and day.pairs_used == 5
    assert day.spot == pytest.approx(S0, rel=1e-4)
    assert set(day.inverted["strike"]) == set(WIDE)
    assert day.summary["mean_gap"].abs().max() < 0.05
    assert list(day.summary["carry"]) == ["calibrated", "projected dividend"]


def test_analyse_day_reports_why_when_no_share_price_can_be_implied():
    day = analyse_day(chain(strikes=(50.0,)), approx_spot=S0, rate=R, divs=DIVS)
    assert day.spot is None and day.spot_reason == Reject.TOO_FEW_PAIRS
    assert day.inverted is None and day.summary is None
