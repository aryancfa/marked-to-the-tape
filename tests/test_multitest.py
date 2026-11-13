"""The false-discovery correction: made-up p-values only."""

import math

import numpy as np

from multitest import survives, two_sided_p


def test_p_value_of_known_t():
    assert abs(two_sided_p(1.959964) - 0.05) < 1e-6
    assert abs(two_sided_p(-1.959964) - 0.05) < 1e-6
    assert two_sided_p(0.0) == 1.0
    assert math.isnan(two_sided_p(float("nan")))


def test_textbook_example():
    # ten p-values at 5%: the bars are 0.005, 0.010, ..., 0.050.
    # 0.001, 0.008, 0.012 sit under their bars; 0.041 at rank 4 does not
    # (bar 0.020) and nothing later does, so three pass.
    p = [0.001, 0.008, 0.012, 0.041, 0.06, 0.2, 0.3, 0.5, 0.7, 0.9]
    assert survives(p).tolist() == [True] * 3 + [False] * 7


def test_step_up_rescues_an_earlier_miss():
    # rank 1 misses its bar (0.02 > 0.0125) but rank 4 meets its own
    # (0.05 <= 0.05), and the rule passes everything up to the last that does
    p = [0.02, 0.03, 0.04, 0.05]
    assert survives(p).all()


def test_order_of_input_does_not_matter():
    p = np.array([0.9, 0.001, 0.3, 0.008, 0.012])
    assert survives(p).tolist() == [False, True, False, True, True]


def test_missing_values_never_pass_and_do_not_count():
    with_gap = survives([0.01, float("nan"), 0.04, 0.9])
    without = survives([0.01, 0.04, 0.9])
    assert with_gap.tolist() == [True, False, False, False]
    assert without.tolist() == [True, False, False]


def test_any_dependence_is_stricter():
    p = [0.001, 0.02, 0.03, 0.04]
    assert survives(p).sum() == 4
    assert survives(p, any_dependence=True).sum() == 1


def test_nothing_passes_when_nothing_is_small():
    assert not survives([0.2, 0.5, 0.9]).any()
    assert not survives([]).any()


def test_false_discoveries_are_rare_when_nothing_is_real():
    # 400 tables of thirty true-zero results: the plain 5% rule flags about
    # 1.5 per table; the corrected rule should flag anything at all in
    # roughly 5% of tables.
    rng = np.random.default_rng(7)
    tables = rng.uniform(size=(400, 30))
    plain = (tables < 0.05).sum(axis=1).mean()
    any_flag = np.mean([survives(row).any() for row in tables])
    assert 1.0 < plain < 2.0
    assert any_flag < 0.10
