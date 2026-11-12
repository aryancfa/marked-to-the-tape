"""
test_demo.py: the whole method, end to end, on made-up data (no licensed files).

scripts/demo_synthetic.py invents a share, prices its options under inputs it
knows, writes them in the exchange's layout and runs the real pipeline on
that file. This runs a short version and checks the pipeline recovers what
was planted. If a change anywhere in the chain (loader, inversion, implied
share price, funding fit, smile) breaks the method, this is the test that
fails, where a unit test of one function might not.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import demo_synthetic as demo


@pytest.fixture(scope="module")
def found():
    return demo.run(days=4, min_history=2, verbose=False)


def test_the_planted_funding_spread_is_recovered(found):
    assert found["spread_found_bp"] == pytest.approx(found["spread_planted_bp"], abs=4.0)


def test_the_implied_share_price_beats_the_tape(found):
    assert found["implied_spot_error_bp"] < 2.0
    assert found["implied_spot_error_bp"] < found["tape_error_bp"]


def test_the_planted_bad_quote_is_the_one_flagged(found):
    assert found["worst_strike"] == demo.BAD_STRIKE
    assert found["bad_quote_found"] == pytest.approx(found["bad_quote_planted"], abs=0.3)
    assert found["other_quotes_median"] < 0.2


def test_the_spread_from_earlier_days_closes_the_gap_on_later_days(found):
    assert found["funded_days"] == 2                  # days 3 and 4 have two earlier estimates
    assert found["long_gap_before"] > 0.15            # funding shows as calls above puts
    assert abs(found["long_gap_after"]) < 0.08
    assert found["largest_pair_cell"] < 0.15
