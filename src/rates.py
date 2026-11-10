"""
rates.py — a Canadian-dollar rate for each option expiry.

Why one rate is not enough
--------------------------
An option's forward price grows at the interest rate until expiry. The
overnight rate is right for an option expiring next week and wrong for one
expiring next year whenever the market expects rates to move. Using it for
every expiry produced a call-minus-put vol gap that grew steadily with
maturity on the first RY day: flat across strikes, larger with time, which is
what a rate that is too low looks like.

So the rate is read off a small curve, by time to expiry:

    overnight   CORRA                         (AVG.INTWO)
    1 month     Government of Canada T-bill   (TB.CDN.30D.MID)
    3 months    "                             (TB.CDN.90D.MID)
    6 months    "                             (TB.CDN.180D.MID)
    1 year      "                             (TB.CDN.1Y.MID)

All from the Bank of Canada's public data service, saved one file per series
under data/raw/boc/.

What it is not
--------------
* T-bills are the government's borrowing rate. A dealer funds a share
  position at more than that, so a small positive call-put gap can remain at
  long expiries. That remainder is a financing spread, and it is reported,
  not tuned away.
* The published T-bill yields are averages of quotes taken through the day,
  not closing levels.
* Beyond one year the curve is held flat at the one-year rate. That is an
  assumption and is labelled wherever it is used.

Conventions: published yields are simple annual rates on an actual/365 basis.
They are converted to continuously compounded rates, which is what voleng
takes: r = ln(1 + y * t) / t.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# series id -> tenor in years
TENORS: dict[str, float] = {
    "AVG.INTWO": 1.0 / 365.0,
    "TB.CDN.30D.MID": 30.0 / 365.0,
    "TB.CDN.90D.MID": 90.0 / 365.0,
    "TB.CDN.180D.MID": 180.0 / 365.0,
    "TB.CDN.1Y.MID": 1.0,
}


def read_boc_series(path: str | Path) -> dict[date, float]:
    """
    One Bank of Canada observations file -> {date: rate as a decimal}.

    The file has a preamble; the data starts after the line beginning
    "date". Empty observations are skipped, never filled.
    """
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith('"date"'))
    except StopIteration:
        raise ValueError(f"{Path(path).name}: no observations block") from None
    out: dict[date, float] = {}
    for line in lines[start + 1:]:
        parts = [x.strip().strip('"') for x in line.split(",")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        out[date.fromisoformat(parts[0])] = float(parts[1]) / 100.0
    return out


def continuous_rate(simple_yield: float, tenor: float) -> float:
    """Simple annual yield over `tenor` years -> continuously compounded."""
    return math.log(1.0 + simple_yield * tenor) / tenor


@dataclass(frozen=True)
class Curve:
    """
    Continuously compounded rates at a few tenors, for one day.

    as_of[series] is the observation date actually used for each point. It
    differs from `day` only when the bond market was shut on a day the
    exchange was open; see load_curve.
    """
    day: date
    points: tuple[tuple[float, float], ...]      # (tenor in years, rate), sorted
    as_of: tuple[tuple[str, date], ...] = ()

    def rate(self, T: float) -> float:
        """Rate for an option with `T` years to expiry: straight-line between
        the two nearest tenors, flat beyond either end."""
        pts = self.points
        if T <= pts[0][0]:
            return pts[0][1]
        if T >= pts[-1][0]:
            return pts[-1][1]
        for (t0, r0), (t1, r1) in zip(pts, pts[1:]):
            if t0 <= T <= t1:
                return r0 + (r1 - r0) * (T - t0) / (t1 - t0)
        raise AssertionError("unreachable")

    __call__ = rate

    @property
    def longest_tenor(self) -> float:
        return self.points[-1][0]

    @property
    def stale(self) -> list[tuple[str, date]]:
        """Points taken from an earlier day than `day`."""
        return [(s, d) for s, d in self.as_of if d != self.day]


def flat_curve(day: date, r: float) -> Curve:
    """One rate for every expiry. For tests and for comparison only."""
    return Curve(day, ((1.0 / 365.0, r),))


def load_curve(folder: str | Path, day: date, max_stale_days: int = 4) -> Curve:
    """
    Build the curve for `day` from the files in `folder` (one per series,
    named <series id>.csv).

    The Canadian bond market closes on a few days the stock exchange is open
    (Remembrance Day, for one). On such a day a series has no observation, and
    the most recent earlier one is used if it is at most `max_stale_days`
    calendar days old; the curve records which points were carried. Older than
    that, or a missing file, raises: a rate is not invented.
    """
    folder = Path(folder)
    points, as_of = [], []
    for series, tenor in TENORS.items():
        path = folder / f"{series}.csv"
        if not path.exists():
            raise FileNotFoundError(f"missing rate file {path.name}")
        obs = read_boc_series(path)
        used = next((day - timedelta(days=k) for k in range(max_stale_days + 1)
                     if day - timedelta(days=k) in obs), None)
        if used is None:
            raise ValueError(
                f"{series}: no observation within {max_stale_days} days of {day}")
        points.append((tenor, continuous_rate(obs[used], tenor)))
        as_of.append((series, used))
    return Curve(day, tuple(sorted(points)), tuple(as_of))
