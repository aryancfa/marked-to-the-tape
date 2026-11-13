"""
replay.py — one book, one day at a time: its marks, its Greeks, why its value
changed since yesterday, which marks are in dispute, and what a wrong
dividend would cost (C2, C3, B6 and the option part of C4).

Nothing here reads a file. A caller hands over, for each day, what the day
runner already produced for the name (share price implied from the options,
rate curve, dividends as known that day) and the exchange's rows for that
day. Everything returned is a derived number.

The day's change in an option's value is split by REPRICING, not by Greeks
---------------------------------------------------------------------------
A Greek is a slope at one point; multiplying slopes by moves leaves a
remainder nobody can name. Instead the option is repriced one input at a
time, from yesterday's state to today's:

  1. carry      a day passes, at yesterday's share price and vol. Time
                decay, the day's funding and the rate curve's move are all
                here. If the share went ex-dividend, the share price is
                first lowered by that dividend: the drop was expected, and
                an expected drop is not a market move.
  2. dividends  yesterday's dividend schedule is swapped for today's. Zero
                unless something was announced, or a projection changed.
  3. share      the share moves to today's price. Split into delta
                (yesterday's delta times the move) and gamma (the rest of
                this step: curvature and everything of higher order).
  4. vol        the vol moves to today's, which reproduces today's mark.

The order is a convention, and it matters for steps 3 and 4. The part of the
change that needs both the share and the vol to move belongs to neither
alone; it falls to whichever step is taken second. `explain(...,
other_order=True)` also prices the vol step first, so a report can show how
much moves between the two buckets when the order is reversed.

The four steps add up to today's mark minus yesterday's exactly, because
step 4 ends on the mark itself. There is no unexplained part to hide in;
what cannot be explained is a position with no mark, and that is counted.

Which marks are in dispute
--------------------------
A risk report takes the day's marks as given. Here each option mark is
tested two ways before its P&L is believed:

  pair    its implied vol against the other side's (call against put) at
          the same strike and expiry. They should agree; if not, one of
          the two marks, or a carry input, is off.
  smile   its implied vol against a curve fitted through the OTHER strikes
          of its expiry.

A mark FAILS a check when the disagreement is larger than the quote's own
uncertainty (its half-spread in vol points) and larger than the solver's
noise floor. The size of the dispute is put in dollars: the position's
vega times the disagreement. The exchange's published vol is not used as a
check, because it differs from ours by model (early exercise read as vol),
not by mark.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Sequence

import pandas as pd

from book import Position
from references import PAIR, SMILE
from surface import dividend_times
from tradingcal import TSX, trading_days, year_fraction
from voleng import Reject, crr_american, greeks_american, implied_vol_american

NOISE_FLOOR = 0.04          # vol points: below this a difference is the tree (hard rule 5)
NO_MARK = "no_mark_in_file"
NO_SERIES = "series_not_in_file"

Rate = Callable[[float], float]
Dated = Sequence[tuple[date, float]]


@dataclass
class DayInputs:
    """What one name needs on one day. `options` is the exchange loader's
    rows for that name and day; `checks` is references.reference_differences
    for the same day, or None when the day has none."""
    day: date
    spot: float
    rate: Rate
    divs: Dated
    options: pd.DataFrame
    checks: pd.DataFrame | None = None


@dataclass
class Valued:
    """One position on one day. Per-contract numbers are for ONE share's
    worth of option; `scale` (quantity times multiplier) turns them into the
    position's dollars."""
    position: Position
    day: date
    scale: float
    price: float | None = None          # the mark
    source: str = "none"                # mid, settle, share, none
    tier: str = "no_price"              # traded, quoted, settlement_only, no_price
    half_spread: float | None = None    # dollars per share, where two-sided
    iv: float | None = None
    reason: str = Reject.OK
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None           # per vol point
    theta: float | None = None          # per calendar day
    T: float | None = None

    @property
    def ok(self) -> bool:
        return self.price is not None and (self.position.kind != "option" or self.iv is not None)

    @property
    def value(self) -> float | None:
        return None if self.price is None else self.scale * self.price

    def dollars(self, greek: str) -> float | None:
        g = getattr(self, greek)
        return None if g is None else self.scale * g


def _price(pos: Position, day: date, spot: float, rate: Rate, divs: Dated,
           vol: float, n: int) -> float:
    T = year_fraction(day, pos.expiry)
    return crr_american(spot, pos.strike, T, rate(T), 0.0, vol,
                        dividend_times(day, divs, pos.expiry), pos.call_put == "C", n)


def price_at(pos: Position, inp: DayInputs, spot: float, vol: float, n: int = 400) -> float:
    """The model's price of one share's worth of the option on inp.day, at a
    share price and vol of the caller's choosing and the day's own rate and
    dividends. For stress tests and scenarios: every number it returns is a
    MODEL price, never a mark (hard rule 6)."""
    return _price(pos, inp.day, spot, inp.rate, inp.divs, vol, n)


def find_row(options: pd.DataFrame, pos: Position):
    """The exchange's row for this option series on the day, or None."""
    if options is None or not len(options):
        return None
    exp = pd.to_datetime(options["expiry"]).dt.date
    hit = options[(exp == pos.expiry) & (options["call_put"] == pos.call_put)
                  & ((options["strike"] - pos.strike).abs() < 1e-6)]
    return None if hit.empty else hit.iloc[0]


def value_position(pos: Position, inp: DayInputs, n: int = 400) -> Valued:
    """Mark one position and measure its Greeks. An option that cannot be
    marked or inverted comes back with a reason and no vol (hard rule 4)."""
    out = Valued(pos, inp.day, pos.quantity * pos.multiplier)
    if pos.kind != "option":
        out.price, out.source, out.tier, out.delta = inp.spot, "share", "quoted", 1.0
        out.gamma = out.vega = out.theta = 0.0
        return out
    row = find_row(inp.options, pos)
    if row is None:
        out.reason = NO_SERIES
        return out
    out.tier, out.source = row["tier"], row["mark_source"]
    if pd.isna(row["mark"]):
        out.reason = NO_MARK
        return out
    out.price = float(row["mark"])
    if row["two_sided"]:
        out.half_spread = 0.5 * float(row["ask"] - row["bid"])
    T = out.T = year_fraction(inp.day, pos.expiry)
    dts = dividend_times(inp.day, inp.divs, pos.expiry)
    is_call = pos.call_put == "C"
    res = implied_vol_american(out.price, inp.spot, pos.strike, T, inp.rate(T), 0.0,
                               dts, is_call, n)
    if res.iv is None:
        out.reason = res.reason
        return out
    out.iv = res.iv
    g = greeks_american(inp.spot, pos.strike, T, inp.rate(T), 0.0, res.iv, dts, is_call, n)
    out.delta, out.gamma, out.vega, out.theta = g.delta, g.gamma, g.vega, g.theta
    return out


def dividends_gone_ex(divs: Dated, day0: date, day1: date) -> float:
    """Cash per share going ex after day0's close and by day1's open."""
    return float(sum(c for d, c in divs if day0 < d <= day1))


STEPS = ("carry", "dividends", "delta", "gamma", "vol")


def explain(v0: Valued, v1: Valued, inp0: DayInputs, inp1: DayInputs,
            n: int = 400, other_order: bool = False) -> dict[str, float] | None:
    """Why the position's value changed from day0 to day1, in dollars, by
    the steps in the module note. None when either day has no usable mark.
    The steps sum to the change in the mark.

    The ORDER of the steps is a convention. The share moves before the vol,
    so the vol step is priced at today's share price. Done the other way
    round the total is the same but the amount called "vol" is not: the
    part of the change that needs BOTH moves (vanna, in desk language) goes
    to whichever step comes second. With `other_order` the result also
    carries `vol_first`, the vol step priced before the share moves; the
    interaction is `vol - vol_first`, and the share step in that order is
    `delta + gamma + vol - vol_first`."""
    pos = v0.position
    if not (v0.ok and v1.ok):
        return None
    gone = dividends_gone_ex(inp0.divs, inp0.day, inp1.day)
    if pos.kind != "option":
        # a share or fund: the holder receives the dividend, so the expected
        # drop nets to zero and the whole change is the share moving
        move = inp1.spot - (inp0.spot - gone)
        return dict(carry=0.0, dividends=0.0, delta=v0.scale * move, gamma=0.0, vol=0.0,
                    total=v0.scale * move)
    s_expected = inp0.spot - gone
    aged = _price(pos, inp1.day, s_expected, inp1.rate, inp0.divs, v0.iv, n)
    news = _price(pos, inp1.day, s_expected, inp1.rate, inp1.divs, v0.iv, n)
    moved = _price(pos, inp1.day, inp1.spot, inp1.rate, inp1.divs, v0.iv, n)
    share = moved - news
    delta = v0.delta * (inp1.spot - s_expected)
    k = v0.scale
    out = dict(carry=k * (aged - v0.price), dividends=k * (news - aged),
               delta=k * delta, gamma=k * (share - delta), vol=k * (v1.price - moved),
               total=k * (v1.price - v0.price))
    if other_order:
        # the same two steps the other way round: vol first, at the share
        # price before it moved, then the share. What lands in "vol" differs
        # from the figure above by the share-and-vol interaction.
        volled = _price(pos, inp1.day, s_expected, inp1.rate, inp1.divs, v1.iv, n)
        out["vol_first"] = k * (volled - news)
    return out


# --------------------------------------------------------------------------
# which marks are in dispute
# --------------------------------------------------------------------------

@dataclass
class MarkCheck:
    """One option mark against its two references, in vol points."""
    pair: float | None = None           # call minus put at this strike
    pair_bar: float | None = None       # the two quotes' bars added
    smile: float | None = None          # this strike minus the smile without it
    smile_bar: float | None = None
    failed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def worst(self) -> float:
        """Largest disagreement among the checks that failed, vol points."""
        size = [abs(x) for x, name in ((self.pair, "pair"), (self.smile, "smile"))
                if x is not None and name in self.failed]
        return max(size) if size else 0.0


def fails(diff: float | None, quote_bar: float | None, bars: float = 1.0,
          floor: float = NOISE_FLOOR) -> bool:
    """True when a disagreement is larger than the quote can explain: more
    than `bars` quote bars AND more than the solver's noise floor."""
    if diff is None or pd.isna(diff):
        return False
    bar = 0.0 if quote_bar is None or pd.isna(quote_bar) else float(quote_bar)
    return abs(diff) > max(bars * bar, floor)


def check_mark(v: Valued, checks: pd.DataFrame | None, bars: float = 1.0) -> MarkCheck:
    """Test one valued option against the day's reference differences."""
    out = MarkCheck()
    pos = v.position
    if pos.kind != "option":
        return out
    if v.tier == "settlement_only":
        out.notes.append("settlement only: no quote or trade behind this mark")
    if not v.ok:
        out.notes.append(f"not valued: {v.reason}")
        return out
    if checks is None or not len(checks):
        out.notes.append("no references for this day")
        return out
    exp = pd.Series([e.date() if hasattr(e, "date") else e for e in checks["expiry"]],
                    index=checks.index)
    here = checks[(exp == pos.expiry) & ((checks["strike"] - pos.strike).abs() < 1e-6)]
    p = here[here["reference"] == PAIR]
    if len(p):
        out.pair, out.pair_bar = float(p["diff"].iloc[0]), float(p["quote_bar"].iloc[0])
        if fails(out.pair, out.pair_bar, bars):
            out.failed.append("pair")
    s = here[(here["reference"] == SMILE) & (here["side"] == pos.call_put)]
    if "poor_fit" in s.columns:
        s = s[~s["poor_fit"].astype(bool)]
    if len(s):
        out.smile, out.smile_bar = float(s["diff"].iloc[0]), float(s["quote_bar"].iloc[0])
        if fails(out.smile, out.smile_bar, bars):
            out.failed.append("smile")
    if out.pair is None and out.smile is None:
        out.notes.append("outside the strikes the references cover")
    return out


def dispute_dollars(v: Valued, check: MarkCheck) -> float:
    """What the dispute is worth: position vega times the largest failed
    disagreement. Zero when nothing failed."""
    return abs(v.dollars("vega") or 0.0) * check.worst


# --------------------------------------------------------------------------
# what a wrong dividend would cost
# --------------------------------------------------------------------------

def next_session(d: date, venue: str = TSX) -> date:
    """The first trading day strictly after d."""
    return next(x for x in trading_days(venue, d, d + timedelta(days=10)) if x > d)


def dividend_exposure(v: Valued, inp: DayInputs, bump: float = 0.05,
                      n: int = 400) -> dict[str, float] | None:
    """Dollars the position gains if every dividend in its life is `bump`
    larger ('amount'), and if every ex-date falls one session later
    ('date'). Vol and share price held. None for anything but a valued
    option; a share's dividend risk is the dividend itself."""
    pos = v.position
    if pos.kind != "option" or not v.ok:
        return None
    base = _price(pos, inp.day, inp.spot, inp.rate, inp.divs, v.iv, n)
    bigger = [(d, c * (1.0 + bump)) for d, c in inp.divs]
    later = [(next_session(d) if d > inp.day else d, c) for d, c in inp.divs]
    return dict(
        amount=v.scale * (_price(pos, inp.day, inp.spot, inp.rate, bigger, v.iv, n) - base),
        date=v.scale * (_price(pos, inp.day, inp.spot, inp.rate, later, v.iv, n) - base))


def reprice_at_vol(v: Valued, inp: DayInputs, vol_points_off: float,
                   n: int = 400) -> float | None:
    """Dollars between the position's mark and its value at a vol
    `vol_points_off` lower: what a disagreement of that many vol points is
    worth by REPRICING. Vega times the disagreement is the straight-line
    version of this; far from the money, or for a large disagreement, the
    two differ. None for anything but a valued option."""
    pos = v.position
    if pos.kind != "option" or not v.ok:
        return None
    vol = max(v.iv - vol_points_off / 100.0, 1e-4)
    return v.scale * (v.price - _price(pos, inp.day, inp.spot, inp.rate, inp.divs, vol, n))


def reprice_with(v: Valued, inp: DayInputs, divs: Dated, n: int = 400) -> float | None:
    """Dollars the position gains if the dividend schedule were `divs`
    instead of the day's, vol and share price held. For pricing one named
    dividend error."""
    pos = v.position
    if pos.kind != "option" or not v.ok:
        return None
    return v.scale * (_price(pos, inp.day, inp.spot, inp.rate, divs, v.iv, n)
                      - _price(pos, inp.day, inp.spot, inp.rate, inp.divs, v.iv, n))


# --------------------------------------------------------------------------
# where the vega sits (C2)
# --------------------------------------------------------------------------

MATURITY_EDGES = (0, 30, 90, 180, 400)


def maturity_bucket(day: date, expiry: date) -> str:
    days = (expiry - day).days
    for lo, hi in zip(MATURITY_EDGES, MATURITY_EDGES[1:]):
        if days <= hi:
            return f"{lo + 1}-{hi}d"
    return f"over {MATURITY_EDGES[-1]}d"


def greeks_table(valued: Sequence[Valued], spots: dict[str, float]) -> pd.DataFrame:
    """Dollar Greeks by name and maturity bucket. Delta is dollars of share
    held (delta times share price); gamma is dollars of delta gained per 1%
    share move; vega is dollars per vol point; theta dollars per day.
    Positions that could not be valued are counted in 'not valued', never
    given a guessed Greek."""
    rows = []
    for v in valued:
        p = v.position
        S = spots.get(p.underlying)
        bucket = maturity_bucket(v.day, p.expiry) if p.kind == "option" else "shares"
        if not v.ok or S is None:
            rows.append(dict(name=p.underlying, bucket=bucket, not_valued=1))
            continue
        rows.append(dict(name=p.underlying, bucket=bucket, not_valued=0,
                         value=v.value, delta=v.dollars("delta") * S,
                         gamma=v.dollars("gamma") * S * S * 0.01,
                         vega=v.dollars("vega"), theta=v.dollars("theta")))
    t = pd.DataFrame(rows)
    return t.groupby(["name", "bucket"], sort=True).sum(numeric_only=True)
