"""
test_mark_benchmark.py: the scoring behind scripts/mark_benchmark.py, on a
hand-made table. The full benchmark prices a made-up chain for minutes; these
check only that a rule's catch rate and false alarms are counted correctly.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import mark_benchmark as mb


def table():
    cols = [c for _, c in mb.RULES]
    rows = []
    # 4 planted quotes (two of 2 points, two of 4), 96 clean
    for size, caught in ((2.0, True), (-2.0, False), (4.0, True), (-4.0, True)):
        rows.append(dict(planted=size, reversal=False, **{c: caught for c in cols}))
    for i in range(96):
        # 3 clean quotes flagged, one of them the day after a bad quote
        rows.append(dict(planted=0.0, reversal=i == 0, **{c: i < 3 for c in cols}))
    return pd.DataFrame(rows)


def test_catch_rate_false_alarms_and_precision_are_counted():
    s = mb.score(table()).loc["smile"]
    assert s["caught, 2 pt %"] == 50.0 and s["caught, 4 pt %"] == 100.0
    assert s["caught, all %"] == 75.0
    assert s["false alarms per 1,000"] == pytest.approx(1000 * 3 / 96)
    assert s["of which the day after"] == pytest.approx(1000 * 1 / 96)
    assert s["flags that are real %"] == pytest.approx(100 * 3 / 6)


def test_the_plan_of_bad_quotes_is_repeatable_and_about_as_dense_as_stated():
    plant = mb.planter(11)
    grid = [(i, j, K, cp) for i in range(40) for j in range(5)
            for K in (92.0, 96.0, 100.0, 104.0, 108.0) for cp in "CP"]
    first = [plant(*g) for g in grid]
    assert first == [mb.planter(11)(*g) for g in grid]
    bad = [x for x in first if x != 0.0]
    assert 0.005 < len(bad) / len(grid) < 0.05
    assert {round(abs(x) * 100, 1) for x in bad} <= set(mb.SIZES)
    assert any(x > 0 for x in bad) and any(x < 0 for x in bad)
