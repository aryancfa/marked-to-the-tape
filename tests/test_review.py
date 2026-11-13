"""test_review.py: the review queue's rules, on made-up reference tables."""

from datetime import date

import pandas as pd
import pytest

from references import PAIR, SMILE
from review import (CURVE, INPUT_CARRY, INPUT_DIVIDEND, MARK, PARTNER, UNCLEAR, Context,
                    evidence, expiry_context, likely_cause, next_check, observation, runs)

EXP = date(2027, 1, 15)
OTHER = date(2027, 3, 19)


def table(pairs: dict, smiles: dict, expiry=EXP) -> pd.DataFrame:
    """pairs: strike -> call minus put. smiles: (strike, side) -> residual."""
    rows = [dict(expiry=expiry, strike=k, reference=PAIR, side="pair", diff=d,
                 quote_bar=0.5, poor_fit=False) for k, d in pairs.items()]
    rows += [dict(expiry=expiry, strike=k, reference=SMILE, side=s, diff=d,
                  quote_bar=0.3, poor_fit=False) for (k, s), d in smiles.items()]
    return pd.DataFrame(rows)


def test_context_reads_the_other_strikes_and_the_other_side():
    t = table({90: 1.0, 95: 1.2, 100: 1.6, 105: 1.1, 110: 0.9},
              {(100, "C"): 0.1, (100, "P"): -1.5})
    t = pd.concat([t, table({100: -9.0}, {}, expiry=OTHER)])   # another expiry: ignored
    c = expiry_context(t, EXP, 100, "C")
    assert c.others == 4 and c.others_median == pytest.approx(1.05)
    assert c.partner_smile == -1.5
    assert expiry_context(t, EXP, 100, "P").partner_smile == 0.1


def test_context_with_no_table_is_empty():
    c = expiry_context(None, EXP, 100, "C")
    assert c.others == 0 and c.others_median is None and c.partner_smile is None


def test_a_poorly_fitted_smile_is_not_evidence_about_the_partner():
    t = table({}, {(100, "P"): -1.5})
    t["poor_fit"] = True
    assert expiry_context(t, EXP, 100, "C").partner_smile is None


def test_every_strike_disagreeing_the_same_way_is_an_input_not_a_quote():
    """One bad quote cannot move five strikes. A wrong forward does."""
    ctx = Context(others=4, others_median=1.05)
    assert likely_cause(["pair"], 1.6, 0.1, ctx, "projected dividend") == INPUT_DIVIDEND
    assert likely_cause(["pair"], 1.6, 0.1, ctx, "declared only") == INPUT_CARRY


def test_neighbours_that_agree_point_at_one_of_the_two_quotes():
    ctx = Context(others=4, others_median=0.05, partner_smile=-1.5)
    assert likely_cause(["pair"], 1.6, 0.1, ctx) == PARTNER       # the put is off its smile
    ctx = Context(others=4, others_median=0.05, partner_smile=0.1)
    assert likely_cause(["pair", "smile"], 1.6, 1.5, ctx) == MARK


def test_a_shared_gap_of_the_opposite_sign_is_not_shared():
    ctx = Context(others=4, others_median=-1.0, partner_smile=0.1)
    assert likely_cause(["pair", "smile"], 1.6, 1.5, ctx) == MARK


def test_too_few_neighbours_cannot_blame_the_input():
    ctx = Context(others=1, others_median=1.5)
    assert likely_cause(["pair"], 1.6, None, ctx, "projected dividend") == UNCLEAR


def test_call_and_put_off_the_curve_together_is_the_curve():
    ctx = Context(others=4, others_median=0.0, partner_smile=0.8)
    assert likely_cause(["smile"], 0.05, 0.9, ctx) == CURVE
    ctx = Context(others=4, others_median=0.0, partner_smile=-0.8)   # opposite sides
    assert likely_cause(["smile"], 0.05, 0.9, ctx) == MARK
    ctx = Context(others=4, others_median=0.0, partner_smile=0.1)    # partner is fine
    assert likely_cause(["smile"], 0.05, 0.9, ctx) == MARK


def test_no_failure_has_no_cause():
    with pytest.raises(ValueError):
        likely_cause([], 0.1, 0.1, Context())


def test_the_words_keep_observation_and_cause_apart():
    obs = observation(["pair"], 1.6, 0.5, 0.1, 0.3, "quoted")
    assert "1.60" in obs and "smile" not in obs
    assert "settlement" in observation(["smile"], None, None, 0.9, 0.3, "settlement_only")
    ev = evidence(Context(others=4, others_median=1.05, partner_smile=-1.5),
                  "projected dividend", 3)
    assert "+1.05" in ev and "projected dividend" in ev and "3 days running" in ev
    assert "first day" in evidence(Context(), None, 1)
    assert "ZZZ" in next_check(INPUT_DIVIDEND, "ZZZ", "C")
    assert "put" in next_check(PARTNER, "ZZZ", "C") and "call" in next_check(PARTNER, "ZZZ", "P")


def test_runs_count_days_in_a_row_per_series():
    f = pd.DataFrame(dict(key=["a", "a", "a", "a", "b"], day=[3, 4, 5, 9, 4]))
    assert list(runs(f)) == [1, 2, 3, 1, 1]
