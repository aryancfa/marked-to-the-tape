"""
review.py — the review queue: for each mark that failed a check, what was
observed, how much it matters, what the evidence points to, and what to
check next (C4; the exception report a desk works through each morning).

Nothing here reads a file or prices anything. It takes what replay.check_mark
already measured for one position and the day's table of reference
differences, and adds the neighbours' evidence.

Observation and explanation are kept apart
------------------------------------------
"The call and the put disagree by 1.6 vol points" is an observation. "The
dividend date is wrong" is an explanation, and the same observation has
others: one bad quote, or a bad quote on the OTHER side. A flag that mixes
the two sends someone to chase a quote when the fault is the model's input.
On Manulife, 14 of 17 flagged days turned out to be exactly that.

So each item carries the observation as measured, and a LIKELY cause chosen
by rules that only look at the neighbours:

  input        the pair check failed AND the other strikes of the same expiry
               disagree the same way. One wrong quote cannot move every
               strike; a wrong forward does (share price, dividend, funding).
               Split by what the expiry rests on: a projected dividend, or
               declared dividends only.
  partner      the pair check failed, the other strikes agree, and it is the
               OTHER side's quote that sits off its smile. The position's own
               mark is not the one in doubt.
  mark         the position's own quote sits off its smile and its partner
               does not, or the pair failed at this strike alone and this
               side is the further from its smile.
  curve        only the smile check failed, and the call and the put sit off
               the curve together by a similar amount. Two independent quotes
               rarely err alike; the fitted curve is the likelier culprit.
  unclear      a check failed and the neighbours do not say which.

These are rules of thumb with stated thresholds, not a model. Their hit rate
on planted errors is measured in scripts/review_benchmark.py; nothing here
should be read as more certain than that table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from references import PAIR, SMILE

INPUT_DIVIDEND = "input: projected dividend"
INPUT_CARRY = "input: share price, rate or funding"
PARTNER = "partner's quote"
MARK = "this mark"
CURVE = "the fitted curve"
UNCLEAR = "unclear"
CAUSES = (INPUT_DIVIDEND, INPUT_CARRY, PARTNER, MARK, CURVE, UNCLEAR)
# Two kinds of item that are not a failed check:
NO_MARK = "no usable mark"                      # could not be marked or inverted
WATCH_DATE = "watch: dividend date not announced"   # nothing failed; one date decides a lot

# Thresholds, each a judgement stated once:
MIN_OTHERS = 2          # other strikes needed before "the whole expiry" means anything:
                        # two strikes are four quotes, and one bad quote moves one of them
SHARED = 0.5            # the others' median gap must be at least this share of the mark's
TOGETHER = 0.5          # call and put count as off the curve together above this ratio


@dataclass
class Context:
    """What the neighbours say about one option mark, in vol points."""
    others: int = 0                     # other strikes of the expiry with a pair
    others_median: float | None = None  # their median call-minus-put
    partner_smile: float | None = None  # the other side's residual at this strike
    partner_bar: float | None = None


def _dates(col) -> pd.Series:
    return pd.Series([e.date() if hasattr(e, "date") else e for e in col], index=col.index)


def expiry_context(checks: pd.DataFrame | None, expiry: date, strike: float,
                   call_put: str) -> Context:
    """The same expiry's other strikes, and the other side at this strike."""
    out = Context()
    if checks is None or not len(checks):
        return out
    here = checks[_dates(checks["expiry"]) == expiry]
    same = (here["strike"] - strike).abs() < 1e-6
    pairs = here[(here["reference"] == PAIR) & ~same]["diff"].dropna()
    out.others = int(len(pairs))
    if out.others:
        out.others_median = float(pairs.median())
    other_side = "P" if call_put == "C" else "C"
    s = here[same & (here["reference"] == SMILE) & (here["side"] == other_side)]
    if "poor_fit" in s.columns:
        s = s[~s["poor_fit"].astype(bool)]
    if len(s):
        out.partner_smile = float(s["diff"].iloc[0])
        out.partner_bar = float(s["quote_bar"].iloc[0])
    return out


def likely_cause(failed, pair: float | None, smile: float | None, ctx: Context,
                 carry: str | None = None, min_others: int = MIN_OTHERS,
                 shared: float = SHARED, together: float = TOGETHER) -> str:
    """One of CAUSES, from the rules in the module note. `failed` is the
    list of checks that failed ("pair", "smile"); `carry` is the expiry's
    label from surface.carry_label. The three thresholds are arguments only
    so the benchmark can vary them."""
    if not failed:
        raise ValueError("nothing failed: there is no cause to name")
    own = abs(smile) if smile is not None and not pd.isna(smile) else None
    other = abs(ctx.partner_smile) if ctx.partner_smile is not None else None
    if "pair" in failed and pair is not None:
        is_shared = (ctx.others >= min_others and ctx.others_median is not None
                  and ctx.others_median * pair > 0
                  and abs(ctx.others_median) >= shared * abs(pair))
        if is_shared:
            return INPUT_DIVIDEND if carry == "projected dividend" else INPUT_CARRY
        if own is not None and other is not None:
            return MARK if own >= other else PARTNER
        if "smile" in failed:
            return MARK
        return UNCLEAR
    # the smile check alone failed: the pair agrees, or there is no pair
    if own is not None and other is not None and smile * ctx.partner_smile > 0 \
            and other >= together * own:
        return CURVE
    return MARK


NEXT = {
    INPUT_DIVIDEND: "Check the next ex-date and amount for {name} against the issuer. "
                    "Do not re-mark the option.",
    INPUT_CARRY: "Check {name}'s share price at the close and the funding spread applied "
                 "to this expiry. Do not re-mark the option.",
    PARTNER: "No action on this mark. The {other} at the same strike is the quote off "
             "its smile; flag that one if it is held.",
    MARK: "Get a second quote for this series, or mark it to its smile and book the "
          "difference as a reserve.",
    CURVE: "Look at the expiry's fitted curve before the mark: the call and the put sit "
           "off it together.",
    UNCLEAR: "Too few neighbouring strikes to say. Check the quote and the next dividend.",
    NO_MARK: "Find a price for this series: it has no mark that gives a vol, so it has no "
             "Greeks and no place in the P&L explain.",
    WATCH_DATE: "Confirm {name}'s next ex-date with the issuer. It is a projection and it "
                "falls within a few sessions of this option's expiry.",
}


def next_check(cause: str, name: str, call_put: str) -> str:
    return NEXT[cause].format(name=name, other="put" if call_put == "C" else "call")


def _vp(x: float | None) -> str:
    return "none" if x is None or pd.isna(x) else f"{x:+.2f}"


def observation(failed, pair, pair_bar, smile, smile_bar, tier: str) -> str:
    """What was measured, with no interpretation."""
    parts = []
    if "pair" in failed:
        parts.append(f"call minus put {_vp(pair)} vol points against a quote bar of "
                     f"{pair_bar:.2f}")
    if "smile" in failed:
        parts.append(f"{_vp(smile)} from its smile against a quote bar of {smile_bar:.2f}")
    text = "; ".join(parts)
    if tier == "settlement_only":
        text += "; the mark is a settlement with no quote or trade behind it"
    return text


def evidence(ctx: Context, carry: str | None, run: int) -> str:
    """What the neighbours show, in words, for whoever reviews the item."""
    parts = []
    if ctx.others:
        parts.append(f"the expiry's other {ctx.others} strike(s) with both sides have a "
                     f"median call minus put of {_vp(ctx.others_median)}")
    else:
        parts.append("no other strike in this expiry has both a call and a put")
    parts.append(f"the other side at this strike sits {_vp(ctx.partner_smile)} from its smile"
                 if ctx.partner_smile is not None else
                 "the other side at this strike has no smile residual")
    if carry:
        parts.append(f"the expiry rests on: {carry}")
    parts.append("first day flagged" if run <= 1 else f"flagged {run} days running")
    return "; ".join(parts)


def runs(flags: pd.DataFrame) -> pd.Series:
    """For each flagged row (columns `key` and `day`, where `day` is the
    position's 0-based index among its own valued days), how many valued
    days in a row it has now been flagged, this one included."""
    out = pd.Series(1, index=flags.index, dtype=int)
    for _, g in flags.sort_values("day").groupby("key"):
        n, prev = 0, None
        for idx, d in zip(g.index, g["day"]):
            n = n + 1 if prev is not None and d == prev + 1 else 1
            out.loc[idx] = n
            prev = d
    return out
