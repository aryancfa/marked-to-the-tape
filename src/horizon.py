"""
horizon.py — a stress over several days, with the carry kept apart (B6).

An instant shock prices the move as if it happened this second. A five-day
stress also takes five days of time decay and funding, and a share that goes
ex-dividend inside the window drops by the dividend whatever the market does.
Shocking prices instantly makes a long-option book look better than it is,
because it skips the decay the book pays while the move happens.

So the scenario's P&L is the position's value at the END of the horizon,
shocked, minus its value today. It is split by REPRICING one input at a
time, in a fixed order, so the parts add up to the total exactly:

  decay      the days pass at a zero rate, with no ex-date in the window:
             time alone.
  funding    the same days at the day's actual rate curve, less decay: what
             the cost of carrying the position adds.
  dividends  the same days with the window's ex-dates in place and the share
             lowered by them, less the two above: what the dividends going ex
             inside the window add. Zero when none does.
  shock      the scenario's move and vol, applied at the end of the window,
             against the position aged without them.

The move is applied to the share price EXPECTED at the end of the window,
which is today's less the dividends going ex in it: the drop is known in
advance, so it is not part of the shock.

A share or fund holder receives the dividend as the share drops by it, so
for them the two cancel; this module does not charge funding on share
positions (the books hold no financing line), so their carry is zero and
says so.

Nothing here reads a file. The pricer is the book's own (replay._price).
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from replay import Dated, Rate, _price, dividends_gone_ex, next_session

PARTS = ("decay", "funding", "dividends")


def end_of_horizon(day: date, sessions: int) -> date:
    """The close `sessions` trading sessions after `day`'s close."""
    if sessions < 0:
        raise ValueError("a horizon cannot be negative")
    d = day
    for _ in range(sessions):
        d = next_session(d)
    return d


def carry_parts(pos, day: date, end: date, spot: float, rate: Rate, divs: Dated,
                vol: float, n: int = 400) -> dict[str, float]:
    """Per share of option: decay, funding and dividends over the window,
    and `aged`, the value at the end with none of the scenario applied. The
    caller multiplies by the position's scale."""
    if end < day:
        raise ValueError("the horizon ends before it starts")
    if end >= pos.expiry:
        raise ValueError(f"the horizon ({end}) reaches the option's expiry ({pos.expiry})")
    gone = dividends_gone_ex(divs, day, end)
    kept = [(d, c) for d, c in divs if not day < d <= end]
    zero: Rate = lambda T: 0.0                                   # noqa: E731
    now = _price(pos, day, spot, rate, divs, vol, n)
    t0_zero = _price(pos, day, spot, zero, kept, vol, n)
    t1_zero = _price(pos, end, spot, zero, kept, vol, n)
    t0_rate = _price(pos, day, spot, rate, kept, vol, n)
    t1_rate = _price(pos, end, spot, rate, kept, vol, n)
    aged = _price(pos, end, spot - gone, rate, divs, vol, n)
    decay = t1_zero - t0_zero
    funding = (t1_rate - t0_rate) - decay
    dividends = (aged - now) - (t1_rate - t0_rate)
    return dict(decay=decay, funding=funding, dividends=dividends, now=now, aged=aged,
                expected_spot=spot - gone)


def shocked(pos, end: date, expected_spot: float, move: float, rate: Rate, divs: Dated,
            vol: float, n: int = 400) -> float:
    """Per share of option: the value at the end of the window after the
    share moves by `move` from its expected price and vol becomes `vol`."""
    return _price(pos, end, expected_spot * (1.0 + move), rate, divs, vol, n)


def share_parts(scale: float, spot: float, divs: Dated, day: date, end: date,
                moves: Sequence[float]) -> dict:
    """A share, fund or future holding: no decay or funding charged here, the
    dividend received cancels the expected drop, and the shock is the move on
    the expected price."""
    expected = spot - dividends_gone_ex(divs, day, end)
    return dict(decay=0.0, funding=0.0, dividends=0.0,
                shock=[scale * expected * m for m in moves])
