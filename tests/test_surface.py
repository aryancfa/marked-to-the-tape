"""
test_surface.py: chain inversion and the call-put gap on a SYNTHETIC chain.

Prices are produced by the engine from a made-up share price, rate, dividend
and vol. Nothing here is market data.
"""

from datetime import date, timedelta

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
                                  dividend_times(VAL, DIVS, e), cp == "C")
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
                     pool_across_days, vs_exchange, mean_std_error, newey_west_lags)

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
    assert far["std_error_iid"] == pytest.approx(sd / 2.0)
    assert far["std_error"] == pytest.approx(mean_std_error([0.2, 0.4, 0.2, 0.4]))
    assert far["t_stat"] == pytest.approx(0.3 / far["std_error"])
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


# ---------------------------------------------------------------------------
# Forward error and the funding spread
# ---------------------------------------------------------------------------

from surface import trailing_spread

MID = date(2027, 1, 15)
THREE = (NEAR, MID, FAR)                     # three monthly expiries


def funded_chain(spread):
    """Prices made at R + spread with no dividends: a market that funds
    above the rate the model is about to be given."""
    rows = []
    for e in THREE:
        T = year_fraction(VAL, e)
        for K in WIDE:
            for cp in "CP":
                px = crr_american(S0, K, T, R + spread, 0.0, SIG, [], cp == "C")
                rows.append(dict(
                    date=pd.Timestamp(VAL), underlying="ZZZ", expiry=pd.Timestamp(e),
                    strike=K, call_put=cp, bid=px - HALF, ask=px + HALF, settle=px,
                    volume=0, open_interest=0, traded=False, two_sided=True,
                    tier="quoted", mark=px, mark_source="mid"))
    return pd.DataFrame(rows)


def test_a_market_that_funds_above_the_model_rate_shows_as_a_forward_error():
    """The market funds 20 bp a year above the model's rate. The share price
    is implied from the front expiry, so the error there is zero and grows
    with the time beyond it: 20 bp a year times that time."""
    day = analyse_day(funded_chain(0.002), approx_spot=S0, rate=R, divs=[])
    extra = year_fraction(NEAR, FAR)
    assert day.summary.loc[FAR, "fwd_error_bp"] == pytest.approx(20.0 * extra, rel=0.15)
    assert day.summary.loc[FAR, "mean_gap"] > 0          # calls look dear
    assert day.funding_spread == pytest.approx(0.002, rel=0.15)


def test_no_spread_is_found_when_the_rate_is_right():
    day = analyse_day(funded_chain(0.0), approx_spot=S0, rate=R, divs=[])
    assert abs(day.funding_spread) < 2e-4
    assert day.summary["fwd_error_bp"].abs().max() < 0.5


def test_the_spread_ignores_expiries_that_rest_on_a_projected_dividend():
    """Only MID is then usable, and one expiry is not enough to fit from."""
    day = analyse_day(funded_chain(0.002), approx_spot=S0, rate=R, divs=[],
                      first_projected=date(2027, 1, 25))
    assert day.funding_spread is None


def test_trailing_spread_uses_only_earlier_days():
    est = {date(2026, 11, d): 0.001 * d for d in (2, 3, 4, 5, 6)}
    # for the 5th: days 2, 3, 4 only. The 5th itself and the 6th are left out.
    assert trailing_spread(est, date(2026, 11, 5), min_days=3) == pytest.approx(0.003)
    # not enough history: no number, and the caller applies no spread
    assert trailing_spread(est, date(2026, 11, 4), min_days=3) is None
    # days with no estimate do not count
    est[date(2026, 11, 3)] = None
    assert trailing_spread(est, date(2026, 11, 5), min_days=3) is None


def test_weighted_gap_leans_on_the_tight_quotes():
    """Two tight strikes agree at zero; one strike quoted ten times wider
    says -3. The plain mean is -1. Weighting each strike by 1 / bar squared,
    the way any measurement with known error bars is averaged, gives nearly
    zero."""
    g = _gap_frame(FAR, [0.0, 0.0, -3.0])
    g["error_bar"] = [1.0, 1.0, 10.0]
    row = gap_summary(g, VAL).loc[FAR]
    assert row["mean_gap"] == pytest.approx(-1.0)
    assert row["weighted_gap"] == pytest.approx(-3.0 * 0.01 / 2.01)


def test_weighted_gap_falls_back_to_the_plain_mean_without_quote_bars():
    g = _gap_frame(FAR, [0.1, 0.3, 0.5], bar=np.nan)
    row = gap_summary(g, VAL).loc[FAR]
    assert row["weighted_gap"] == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# When the share price drops on the ex-date
# ---------------------------------------------------------------------------

EX, WEEKLY = date(2026, 11, 12), date(2026, 11, 16)     # a Thursday, the Monday after


def test_dividend_is_placed_by_trading_sessions():
    """Share prices move during trading sessions and hardly at all over a
    weekend. From Monday 2 November to the expiry on Monday 16 November there
    are 10 sessions, and 7 of them are over before the share opens lower on
    the 12th. So the drop sits 7/10 of the way through the option's life,
    not 10/14 as a count of calendar days would have it."""
    (t, cash), = dividend_times(VAL, [(EX, 0.5)], WEEKLY)
    assert cash == 0.5
    assert t == pytest.approx(year_fraction(VAL, WEEKLY) * 7 / 10)


def test_an_ex_date_right_after_a_weekend_still_counts():
    """Valued on a Friday, ex-dividend on the Monday: no session lies between,
    but the dividend is still to come. It is placed a quarter of a session
    in, never at zero."""
    friday, monday, expiry = date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 13)
    (t, _), = dividend_times(friday, [(monday, 0.5)], expiry)
    assert t == pytest.approx(year_fraction(friday, expiry) * 0.25 / 5)


def test_which_dividends_count_is_unchanged():
    T = year_fraction(VAL, WEEKLY)
    on_expiry, after, gone, today = (dividend_times(VAL, [(d, 0.5)], WEEKLY)[0][0] for d in (
        WEEKLY, date(2026, 11, 17), date(2026, 10, 26), VAL))
    assert 0 < on_expiry < T          # the share has dropped before the option expires
    assert after > T                  # the next session after expiry: not this option's
    assert gone < 0 and today == 0.0  # already ex-dividend at the valuation close


def test_calendar_placement_is_still_available():
    divs = [(date(2026, 11, 3), 0.5), (EX, 0.5)]
    assert dividend_times(VAL, divs, WEEKLY, ex_shift_days=0.0) == [(1 / 365, 0.5), (10 / 365, 0.5)]
    one = dividend_times(VAL, divs, WEEKLY, ex_shift_days=1.0)
    assert one[0][0] == pytest.approx(0.25 / 365) and one[1][0] == pytest.approx(9 / 365)


def _weekly_chain(times):
    T = year_fraction(VAL, WEEKLY)
    rows = []
    for K in (49.0, 50.0, 51.0):
        for cp in "CP":
            px = crr_american(S0, K, T, R, 0.0, SIG, times, cp == "C")
            rows.append(dict(
                date=pd.Timestamp(VAL), underlying="ZZZ", expiry=pd.Timestamp(WEEKLY),
                strike=K, call_put=cp, bid=px - HALF, ask=px + HALF, settle=px,
                volume=0, open_interest=0, traded=False, two_sided=True,
                tier="quoted", mark=px, mark_source="mid"))
    return pd.DataFrame(rows)


def test_a_drop_placed_at_the_close_shows_as_a_gap_just_after_the_ex_date():
    """Prices are made with the drop where the sessions put it. Inverting
    with the drop at the close of the ex-date, by calendar days, leaves puts
    above calls on an expiry four days later. The default leaves no gap."""
    c = _weekly_chain(dividend_times(VAL, [(EX, 0.5)], WEEKLY))
    at_close = call_put_gap(invert_chain(c, S0, R, [(EX, 0.5)], ex_shift_days=0.0))["gap"]
    assert (at_close < -0.05).all()
    by_sessions = call_put_gap(invert_chain(c, S0, R, [(EX, 0.5)]))["gap"]
    assert by_sessions.abs().max() < 0.01


def test_dividend_timing_follows_the_calendar_of_the_listing():
    """Thursday 26 November 2026 is US Thanksgiving: New York is shut and
    Toronto is open. So an option listed in New York has one session fewer
    before a Friday ex-date than the same option listed in Montreal, and the
    drop sits at a different point in its life."""
    from tradingcal import NYSE
    val, ex, expiry = date(2026, 11, 23), date(2026, 11, 27), date(2026, 11, 30)
    T = year_fraction(val, expiry)
    (tsx, _), = dividend_times(val, [(ex, 0.5)], expiry)
    (nyse, _), = dividend_times(val, [(ex, 0.5)], expiry, venue=NYSE)
    assert tsx == pytest.approx(T * 3 / 5) and nyse == pytest.approx(T * 2 / 4)
    rows = []
    for K in (49.0, 50.0, 51.0):
        for cp in "CP":
            px = crr_american(S0, K, T, R, 0.0, SIG, [(nyse, 0.5)], cp == "C")
            rows.append(dict(
                date=pd.Timestamp(val), underlying="ZZZ:US", expiry=pd.Timestamp(expiry),
                strike=K, call_put=cp, bid=px - HALF, ask=px + HALF, settle=px,
                volume=0, open_interest=0, traded=False, two_sided=True,
                tier="quoted", mark=px, mark_source="mid"))
    c = pd.DataFrame(rows)
    right = call_put_gap(invert_chain(c, S0, R, [(ex, 0.5)], venue=NYSE))["gap"]
    wrong = call_put_gap(invert_chain(c, S0, R, [(ex, 0.5)]))["gap"]
    assert right.abs().max() < 0.01 and wrong.abs().max() > 0.05


# ---------------------------------------------------------------------------
# Choices the robustness table varies (build sheet B10)
# ---------------------------------------------------------------------------

def test_trailing_spread_can_use_only_the_most_recent_days():
    """All earlier days by default. With a window, only the latest few, so an
    old estimate stops counting."""
    est = {date(2026, 11, d): 0.001 * d for d in (2, 3, 4, 5, 6)}
    day = date(2026, 11, 9)
    assert trailing_spread(est, day, min_days=3) == pytest.approx(0.004)
    assert trailing_spread(est, day, min_days=3, window=2) == pytest.approx(0.0055)
    # still nothing until there is enough history, whatever the window
    assert trailing_spread(est, date(2026, 11, 4), min_days=3, window=2) is None


def test_the_share_price_can_be_implied_from_fewer_pairs():
    day = analyse_day(chain(strikes=WIDE), approx_spot=S0 * 1.005, rate=R, divs=DIVS,
                      spot_pairs=3)
    assert day.pairs_used == 3 and day.spot == pytest.approx(S0, rel=1e-4)


def test_a_given_share_price_is_used_as_it_is():
    """Hand it a share price 0.5% too high and tell it not to imply one. It
    uses that price, and every expiry then shows calls below puts: the gap a
    wrong share price leaves, largest on the nearest expiry."""
    day = analyse_day(chain(strikes=WIDE), approx_spot=S0, rate=R, divs=DIVS,
                      spot=S0 * 1.005)
    assert day.spot == pytest.approx(S0 * 1.005) and day.pairs_used == 0
    gaps = day.summary["mean_gap"]
    assert (gaps < -1.0).all() and gaps.iloc[0] < gaps.iloc[-1]
    assert list(day.summary["carry"]) == ["declared only", "declared only"]


# ---------------------------------------------------------------------------
# Standard error of a mean when days resemble their neighbours (Newey-West)
# ---------------------------------------------------------------------------

def test_with_no_lags_the_standard_error_is_the_plain_formula():
    x = [0.1, 0.4, 0.2, 0.5, 0.3, 0.0]
    assert mean_std_error(x, lags=0) == pytest.approx(np.std(x, ddof=1) / np.sqrt(6))


def test_days_that_resemble_their_neighbours_widen_the_standard_error():
    """A slow drift: each day close to the one before. Ninety such days are
    not ninety independent measurements, so the error must be wider than the
    plain formula says."""
    rng = np.random.default_rng(7)
    x = np.zeros(90)
    for i in range(1, 90):
        x[i] = 0.8 * x[i - 1] + rng.normal(0.0, 0.1)
    plain = np.std(x, ddof=1) / np.sqrt(90)
    assert mean_std_error(x) > 1.5 * plain


def test_independent_days_leave_the_standard_error_about_where_it_was():
    x = np.random.default_rng(11).normal(0.0, 0.1, 400)
    plain = np.std(x, ddof=1) / np.sqrt(400)
    assert mean_std_error(x) == pytest.approx(plain, rel=0.2)


def test_standard_error_needs_two_days_and_ignores_missing_ones():
    assert np.isnan(mean_std_error([0.3]))
    assert mean_std_error([0.1, np.nan, 0.3], lags=0) == pytest.approx(
        np.std([0.1, 0.3], ddof=1) / np.sqrt(2))


def test_lag_rule():
    assert [newey_west_lags(n) for n in (1, 50, 100, 250)] == [0, 3, 4, 4]


# ---------------------------------------------------------------------------
# Which funding spread to use: the name's own, or the pooled one
# ---------------------------------------------------------------------------

from surface import OWN, POOLED, choose_spread


def _est(values, start=date(2026, 6, 1)):
    return {start + timedelta(days=i): v for i, v in enumerate(values)}


DAY = date(2026, 8, 1)
STEADY = _est([0.0018 + 0.0001 * (i % 3 - 1) for i in range(40)])      # 18 bp, tight
WILD = _est([0.0040 * (-1) ** i + 0.0030 for i in range(40)])          # swings by 80 bp
OTHERS = {"A": _est([0.0030] * 40), "B": _est([0.0032 + 0.00005 * (i % 2) for i in range(40)]),
          "C": _est([0.0028 - 0.00005 * (i % 2) for i in range(40)]), "D": WILD}


def test_a_well_measured_name_uses_its_own_spread():
    s, source = choose_spread(STEADY, DAY, OTHERS)
    assert source == OWN and s == pytest.approx(0.0018, abs=1e-5)


def test_a_badly_measured_name_takes_the_median_of_the_well_measured_ones():
    """Its own estimates swing by 80 basis points. The pooled figure is the
    median of A, B and C; D is as wild as the name itself and is left out."""
    s, source = choose_spread(WILD, DAY, OTHERS)
    assert source == POOLED and s == pytest.approx(0.0030, abs=5e-5)


def test_a_name_with_too_few_estimates_also_takes_the_pooled_spread():
    s, source = choose_spread(_est([0.0020] * 5), DAY, OTHERS)
    assert source == POOLED


def test_no_spread_when_too_few_names_are_well_measured():
    s, source = choose_spread(WILD, DAY, {"A": OTHERS["A"], "D": WILD})
    assert s is None and source == "none"


def test_only_earlier_days_count_for_own_and_pooled():
    late = {DAY + timedelta(days=i): 0.0100 for i in range(40)}       # all after the day
    s, source = choose_spread(late, DAY, {k: late for k in "ABC"})
    assert s is None
    own, _ = choose_spread({**STEADY, **late}, DAY, OTHERS)
    assert own == pytest.approx(0.0018, abs=1e-5)                      # later days ignored
