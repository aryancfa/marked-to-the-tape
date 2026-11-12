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
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Mapping

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
    spread: float = 0.0     # funding spread already added to every point
    source: str = "Treasury bills"
    # How to read between two points. False: straight line in the rate (right
    # for a few bill yields). True: the overnight rate is taken as constant
    # between points, which is what a strip of futures actually says.
    constant_forward: bool = False

    def plus(self, spread: float) -> "Curve":
        """
        The same curve with `spread` added at every tenor.

        Treasury bills are the cheapest money in the country, and nobody
        hedging options funds at that rate. The option market's forwards put
        dealers' funding above the bill curve. The spread comes from
        surface.trailing_spread, fitted from options on EARLIER days only,
        and is kept on the curve so every output can say it was used.
        """
        return replace(self, points=tuple((t, r + spread) for t, r in self.points),
                       spread=self.spread + spread)

    def rate(self, T: float) -> float:
        """Rate for an option with `T` years to expiry: straight-line between
        the two nearest tenors, flat beyond either end."""
        pts = self.points
        if T <= pts[0][0]:
            return pts[0][1]
        if self.constant_forward:
            # work with rate * time, which grows in straight lines between
            # points when the overnight rate is constant between them
            knots = [(0.0, 0.0)] + [(t, t * r) for t, r in pts]
            for (t0, a0), (t1, a1) in zip(knots[1:], knots[2:]):
                if T <= t1:
                    return (a0 + (a1 - a0) * (T - t0) / (t1 - t0)) / T
            (t0, a0), (t1, a1) = knots[-2], knots[-1]     # beyond the last point
            return (a1 + (a1 - a0) / (t1 - t0) * (T - t1)) / T
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


# --------------------------------------------------------------------------
# a curve from three-month CORRA futures
# --------------------------------------------------------------------------

def _third_wednesday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(2 - first.weekday()) % 7 + 14)


def quarter_start(quarter_end: date) -> date:
    """Start of a CORRA future's reference quarter: the third Wednesday three
    months before the third Wednesday it ends on."""
    m = quarter_end.year * 12 + quarter_end.month - 1 - 3
    return _third_wednesday(m // 12, m % 12 + 1)


def futures_curve(day: date, quotes: Iterable[tuple[date, date, float]],
                  corra: Mapping[date, float], max_stale_days: int = 4) -> Curve:
    """
    The rate for each expiry from three-month CORRA futures (MX symbol CRA).

    Why futures and not bills
    -------------------------
    A dealer hedging options funds overnight, at CORRA, day after day. What
    matters for an option expiring in six months is what that overnight rate
    is expected to average over those six months. Each CORRA future settles
    on exactly that average for one quarter, so a strip of them is the
    market's own forecast of the funding rate. Treasury bills pay less,
    because investors accept a lower yield to hold government paper.

    How it is built
    ---------------
    `quotes` is (observation date, quarter-end date, settlement price) for
    any number of days; the rows observed on `day` are used. A future priced
    at P means money grows over its quarter by 1 + (100 - P)/100 * days/365.

    * The front contract's quarter has partly happened. The growth already
      paid, from the published CORRA fixings in `corra`, is divided out, and
      what is left is the growth expected from `day` to the quarter's end.
      Without this a rate change expected next week would be averaged
      together with rates already paid.
    * Each later contract multiplies in the growth over its own quarter.
    * Between those dates the overnight rate is taken as constant.

    Refusals: if `day` has no futures, the latest earlier day within
    `max_stale_days` is used and recorded (the rate market is shut on a few
    days the stock exchange is open). Older than that raises. A strip that
    does not begin with the quarter `day` is in, or a hole in the CORRA
    fixings, raises. Nothing is filled in.

    Not adjusted for: the small difference between a futures rate and a swap
    rate (convexity), which is far below a basis point inside a year. The
    quarter dates follow the file's expiry dates and have not yet been
    checked against the exchange's contract specification.
    """
    quotes = list(quotes)
    used = next((day - timedelta(days=k) for k in range(max_stale_days + 1)
                 if any(q[0] == day - timedelta(days=k) for q in quotes)), None)
    if used is None:
        raise ValueError(f"no futures within {max_stale_days} days of {day}")
    strip = sorted((end, px) for obs, end, px in quotes if obs == used and end > day)
    if not strip:
        raise ValueError(f"no futures on {used} end after {day}")
    start = quarter_start(strip[0][0])
    if start > day:
        raise ValueError(f"the front contract's quarter starts {start}, after {day}; "
                         "the strip does not cover today")

    # growth already paid in the front quarter, from the overnight fixings
    fix = sorted(d for d in corra if d < day)
    if start < day:
        if not fix or fix[0] > start or (day - fix[-1]).days > max_stale_days:
            raise ValueError(f"CORRA fixing missing between {start} and {day}")
    # A hole INSIDE the run of fixings would otherwise be filled by carrying
    # the last rate across it, silently, for however long the hole is.
    inside = [d for d in fix if d >= start]
    for d0, d1 in zip(inside, inside[1:]):
        if (d1 - d0).days > max_stale_days:
            raise ValueError(f"CORRA fixings jump from {d0} to {d1}; "
                             f"a gap of more than {max_stale_days} days is not filled in")
    paid = 1.0
    for d0, d1 in zip(fix, fix[1:] + [day]):
        lo, hi = max(d0, start), min(d1, day)
        if hi > lo:
            paid *= 1.0 + corra[d0] * (hi - lo).days / 365.0

    points, growth, prev = [], 1.0, start
    for end, px in strip:
        if quarter_start(end) != prev:
            break                               # a hole in the strip: stop there
        q = 1.0 + (100.0 - px) / 100.0 * (end - prev).days / 365.0
        growth *= q / paid if prev == start else q
        t = (end - day).days / 365.0
        points.append((t, math.log(growth) / t))
        prev = end
    return Curve(day, tuple(points), (("CRA", used),), source="CORRA futures",
                 constant_forward=True)


def load_base_curve(folder: str | Path, day: date,
                    cra_quotes: Iterable[tuple[date, date, float]] | None = None,
                    max_stale_days: int = 4) -> Curve:
    """
    The rate curve to use on `day`: CORRA futures where the day has them,
    Treasury bills where it does not. The curve's `source` says which.

    Futures are the base because they are the market's forecast of the
    overnight rate a dealer actually funds at. Bills are the fallback for
    days before the futures files start. `folder` holds the Bank of Canada
    files; the CORRA fixings in it are needed for the futures curve too.
    """
    quotes = list(cra_quotes or [])
    if any(0 <= (day - q[0]).days <= max_stale_days for q in quotes):
        corra = read_boc_series(Path(folder) / "AVG.INTWO.csv")
        return futures_curve(day, quotes, corra, max_stale_days)
    return load_curve(folder, day, max_stale_days)
