"""test_risk_modules.py: realized vol (B9), vol rules (C1) and tail risk (C5, C7),
on made-up numbers."""

import numpy as np
import pandas as pd
import pytest

from realized import (atm_vol, bs_delta, hedge_pnl, realized_vol, risk_reversal_butterfly,
                      vol_at_delta)
from tailrisk import component_es, es, kupiec, marginal_es, tail_size, traffic_light, var
from volrule import (BETA, RULES, STICKY_DELTA, STICKY_MONEYNESS, STICKY_STRIKE, fitted_beta,
                     shocked_vol, smile_from_points)

# ----------------------------------------------------------------- realized


def test_realized_vol_recovers_the_vol_the_moves_were_drawn_from():
    rng = np.random.default_rng(1)
    T = 2.0
    r = rng.normal(0.0, 0.20 * np.sqrt(T / 2000), 2000)
    assert realized_vol(r, T) == pytest.approx(0.20, rel=0.05)
    assert np.isnan(realized_vol([], 1.0)) and np.isnan(realized_vol([0.01], 0.0))


def test_realized_vol_is_on_the_calendar_clock_not_a_count_of_days():
    """The same five moves over a week that holds a long weekend are a lower
    annual vol than over an ordinary week: more calendar time, same moves."""
    r = [0.01, -0.01, 0.01, -0.01, 0.01]
    assert realized_vol(r, 8 / 365) < realized_vol(r, 7 / 365)


def test_atm_vol_is_read_between_strikes_and_never_past_them():
    k, v = [95, 95, 100, 105], [0.24, 0.26, 0.22, 0.20]       # two quotes at 95: averaged
    assert atm_vol(k, v, 97.5) == pytest.approx(0.235)
    assert atm_vol(k, v, 110.0) is None and atm_vol([100], [0.2], 100.0) is None


def test_delta_and_the_25_delta_wings():
    assert bs_delta(100, 100, 1.0, 0.2, True) == pytest.approx(0.5398, abs=1e-3)
    assert bs_delta(100, 100, 1.0, 0.2, False) == pytest.approx(-0.4602, abs=1e-3)
    k = np.arange(80.0, 121.0, 5.0)
    skew = 0.20 - 0.002 * (k - 100.0)                           # lower strikes dearer
    rr, bf = risk_reversal_butterfly(k, skew, 100.0, 0.25, 100.0)
    assert rr < 0                                               # puts dearer than calls
    flat = np.full(len(k), 0.20)
    rr0, bf0 = risk_reversal_butterfly(k, flat, 100.0, 0.25, 100.0)
    assert rr0 == pytest.approx(0.0, abs=1e-9) and bf0 == pytest.approx(0.0, abs=1e-9)
    smile = 0.20 + 0.0004 * (k - 100.0) ** 2                    # both wings dearer
    assert risk_reversal_butterfly(k, smile, 100.0, 0.25, 100.0)[1] > 0
    # strikes that do not reach the 25-delta point give nothing, not a guess
    assert vol_at_delta([99, 100, 101], [0.2, 0.2, 0.2], 100.0, 0.25, 0.25) is None


def test_the_delta_hedge_earns_minus_delta_times_the_move_and_pays_the_dividend():
    assert hedge_pnl([0.5, 0.6, 0.0], [100, 102, 101]) == pytest.approx(-0.5 * 2 + 0.6 * 1)
    assert hedge_pnl([0.5, 0.5], [100, 99], [0.0, 1.0]) == pytest.approx(0.0)   # ex-day: no move
    with pytest.raises(ValueError):
        hedge_pnl([0.5], [100, 101])


# ----------------------------------------------------------------- vol rules

K = [80.0, 90.0, 100.0, 110.0, 120.0]
V = [0.30, 0.25, 0.20, 0.18, 0.17]
SMILE = smile_from_points(K, V)


def test_the_smile_is_flat_beyond_its_last_strikes():
    assert SMILE(100.0) == pytest.approx(0.20) and SMILE(60.0) == pytest.approx(0.30)
    assert SMILE(200.0) == pytest.approx(0.17)
    assert 0.20 < SMILE(95.0) < 0.25


def test_sticky_strike_keeps_the_options_own_vol():
    assert shocked_vol(STICKY_STRIKE, SMILE, 90.0, 100.0, 80.0, 0.5) == pytest.approx(0.25)


def test_sticky_moneyness_moves_the_smile_with_the_share():
    """After a 10% fall the 90 strike is at the money and takes the old
    at-the-money vol; the at-the-money vol itself has not changed."""
    assert shocked_vol(STICKY_MONEYNESS, SMILE, 90.0, 100.0, 90.0, 0.5) == pytest.approx(0.20)


def test_sticky_delta_is_close_to_sticky_moneyness_and_exact_with_no_move():
    for rule in RULES:
        assert shocked_vol(rule, SMILE, 90.0, 100.0, 100.0, 0.5) == pytest.approx(0.25, abs=1e-6)
    a = shocked_vol(STICKY_DELTA, SMILE, 95.0, 100.0, 97.0, 0.5)
    b = shocked_vol(STICKY_MONEYNESS, SMILE, 95.0, 100.0, 97.0, 0.5)
    assert a == pytest.approx(b, abs=0.01)


def test_only_the_beta_rule_raises_vol_at_the_money_in_a_fall():
    after = {r: shocked_vol(r, SMILE, 90.0, 100.0, 90.0, 0.5, beta=-0.5) for r in RULES}
    assert after[BETA] == pytest.approx(0.20 + 0.05)            # 10% fall x 0.5 points per 1%
    assert after[STICKY_MONEYNESS] == pytest.approx(0.20)
    assert after[BETA] > max(after[STICKY_MONEYNESS], after[STICKY_DELTA])


def test_the_skew_steepens_only_in_a_crash():
    mild = shocked_vol(BETA, SMILE, 85.5, 100.0, 95.0, 0.5, beta=0.0, crash=-0.10, steepen=0.5)
    assert mild == pytest.approx(SMILE(90.0))                   # a 5% fall: the smile rides, no more
    crash = shocked_vol(BETA, SMILE, 72.0, 100.0, 80.0, 0.5, beta=0.0, crash=-0.10, steepen=0.5)
    assert crash == pytest.approx(0.20 + 1.5 * (SMILE(90.0) - 0.20))


def test_vol_is_floored_and_bad_inputs_are_refused():
    assert shocked_vol(BETA, SMILE, 100.0, 100.0, 150.0, 0.5, beta=-1.0) == pytest.approx(0.01)
    with pytest.raises(ValueError):
        shocked_vol("sticky tape", SMILE, 100.0, 100.0, 90.0, 0.5)
    with pytest.raises(ValueError):
        shocked_vol(BETA, SMILE, 100.0, 100.0, -1.0, 0.5)


def test_fitted_beta_recovers_a_planted_response():
    rng = np.random.default_rng(2)
    ret = rng.normal(0, 0.01, 200)
    moves = -0.6 * ret * 100 + rng.normal(0, 0.1, 200)
    b, se, n = fitted_beta(ret, moves)
    assert b == pytest.approx(-0.6, abs=3 * se) and n == 200 and se < 0.05
    assert np.isnan(fitted_beta([0.01] * 3, [0.1] * 3)[0])


# ----------------------------------------------------------------- tail risk


def test_var_and_es_on_a_known_sample():
    pnl = -np.arange(1.0, 201.0)                                # losses of 1 to 200
    assert tail_size(200, 0.975) == 5 and tail_size(30, 0.99) == 1
    assert var(pnl, 0.975) == 196.0                             # the 5th worst
    assert es(pnl, 0.975) == pytest.approx(198.0)               # mean of 196..200
    assert es(pnl, 0.975) >= var(pnl, 0.975)


def test_components_add_up_to_the_books_expected_shortfall():
    rng = np.random.default_rng(3)
    common = rng.normal(0, 1, 1000)
    pnl = pd.DataFrame({"long": 100 * common + rng.normal(0, 20, 1000),
                        "also long": 50 * common + rng.normal(0, 20, 1000),
                        "hedge": -60 * common + rng.normal(0, 20, 1000)})
    comp = component_es(pnl, 0.975)
    assert comp.sum() == pytest.approx(es(pnl.sum(axis=1), 0.975))
    assert comp["hedge"] < 0 < comp["also long"] < comp["long"]


def test_marginal_es_is_close_to_the_component_for_a_small_bump():
    rng = np.random.default_rng(4)
    common = rng.normal(0, 1, 4000)
    pnl = pd.DataFrame({"a": 100 * common + rng.normal(0, 10, 4000),
                        "b": -40 * common + rng.normal(0, 10, 4000)})
    m, c = marginal_es(pnl, 0.975), component_es(pnl, 0.975)
    assert m["a"] == pytest.approx(c["a"], rel=0.05) and m["b"] < 0


def test_kupiec_accepts_the_expected_count_and_rejects_far_too_many():
    stat, p = kupiec(2, 250, 0.99)                              # 2.5 expected
    assert p > 0.5 and stat < 1.0
    stat, p = kupiec(12, 250, 0.99)
    assert p < 0.001 and stat > 10
    assert kupiec(0, 250, 0.99)[1] > 0.02                       # none at all: unusual, not damning
    with pytest.raises(ValueError):
        kupiec(5, 3, 0.99)


def test_the_traffic_light_matches_basels_table_on_250_days():
    zones = [traffic_light(x, 250, 0.99)[0] for x in range(12)]
    assert zones[:5] == ["green"] * 5 and zones[5:10] == ["yellow"] * 5
    assert zones[10:] == ["red"] * 2
    # on fewer days the same probabilities give lower cut-offs
    assert traffic_light(3, 100, 0.99)[0] == "yellow"
