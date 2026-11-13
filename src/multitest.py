"""
multitest.py — which of many test results survive being one of many.

Fifteen names times two dividend groups is about thirty t statistics. Test
thirty true-zero gaps at the usual 5% level and one or two will look
significant by chance alone. So a single t above 2 in a table that size says
little until the table as a whole is accounted for.

The correction used is Benjamini and Hochberg's (1995). It controls the
FALSE DISCOVERY RATE: among the results it lets through, the expected share
that are chance is at most the level chosen. That is the right question for
a screen ("of the gaps I go and investigate, how many are wasted effort"),
and it is less severe than Bonferroni, which guards against even one false
alarm and so lets real effects through less often.

Benjamini-Hochberg is exact for independent tests and still valid when the
tests move together in the same direction. The names here share days and,
some of them, a pooled spread, so their tests are not independent. The
Benjamini-Yekutieli variant (2001) holds under any dependence at the cost of
a stricter bar; `survives` offers it as `any_dependence=True` and the report
shows both.

numpy only. Nothing here reads a file.
"""

from __future__ import annotations

import math

import numpy as np


def two_sided_p(t: float) -> float:
    """Two-sided p-value of a t statistic, read against the normal. With a
    hundred days behind each mean the normal and the t distribution differ
    in the third decimal; with fewer than about thirty the p-value is a
    little too small, and the caller should say so."""
    if t is None or not math.isfinite(t):
        return float("nan")
    return math.erfc(abs(t) / math.sqrt(2.0))


def survives(p_values, level: float = 0.05, any_dependence: bool = False) -> np.ndarray:
    """True for each p-value that passes the step-up rule at `level`.

    Sort the m p-values. Find the largest rank k with p_(k) <= level * k / m
    (divided by 1 + 1/2 + ... + 1/m when `any_dependence`). Everything ranked
    at or below k passes. Missing p-values never pass and are not counted in m.
    """
    p = np.asarray(p_values, dtype=float)
    out = np.zeros(p.shape, dtype=bool)
    ok = np.isfinite(p)
    m = int(ok.sum())
    if m == 0:
        return out
    penalty = sum(1.0 / i for i in range(1, m + 1)) if any_dependence else 1.0
    idx = np.flatnonzero(ok)
    order = idx[np.argsort(p[idx], kind="stable")]
    bar = level * np.arange(1, m + 1) / (m * penalty)
    passed = np.flatnonzero(p[order] <= bar)
    if len(passed):
        out[order[: passed[-1] + 1]] = True
    return out
