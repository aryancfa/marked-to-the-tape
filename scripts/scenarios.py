"""
scenarios.py — a book repriced under share-price moves, with a vol rule (C1).

Shared by scripts/stress.py (a few chosen moves) and scripts/tail_risk.py
(every historical day's moves). Not run on its own.

Each option is repriced in full on the tree at the moved share price and at
the vol the chosen rule gives it (src/volrule.py). Nothing is approximated
with Greeks here. The rule is applied as a CHANGE to the option's own
implied vol: with no move every rule returns the option's own vol and its
P&L is zero, so the rules can be compared from the same starting point.

The valuation day is each name's latest day in the cache. Time does not
pass in a scenario: it is the P&L of an instant move, with no day's decay.

Every figure from here is a MODEL price on a rule-based vol (hard rule 6).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import pandas as pd

from book_report import cached_days
from hedge import rolling_ratio
from loaders.compustat import load_index_returns, load_total_returns
from loaders.mx import load_mx_futures, load_mx_options
from rates import load_base_curve
from replay import DayInputs, price_at, value_position
from run_days import BOC, mx_files
from tradingcal import year_fraction
from volrule import shocked_vol, smile_from_points

WRDS = ROOT / "data/raw/wrds"
INDEX = "index"


def label(pos) -> str:
    if pos.kind == "option":
        return f"{pos.underlying} {pos.expiry:%b %Y} {pos.strike:g} {pos.call_put}"
    return f"{pos.underlying} {'future' if pos.kind == 'future' else 'shares'}"


def returns_table(names: list[str]) -> pd.DataFrame:
    """Daily returns by date: the TSX 60 index (price return, standing in for
    the future) and each name's total return."""
    cols = {INDEX: load_index_returns(WRDS / "index_TSX60.csv")}
    for n in names:
        if n != "SXF":
            cols[n] = load_total_returns(WRDS / f"prices_{n}.csv")
    return pd.concat(cols, axis=1)


def index_betas(table: pd.DataFrame, window: int = 250) -> dict[str, float]:
    """Each name's move per unit of index move, fitted on the last `window`
    days on file. Used to turn one index shock into a move for every name."""
    out = {"SXF": 1.0}
    for n in table.columns:
        if n != INDEX:
            b = rolling_ratio(table[n], table[[INDEX]], window).dropna()
            out[n] = float(b[INDEX].iloc[-1])
    return out


CRASH_SHARE = 0.05          # the index's worst 5% of days count as a sell-off


def crash_betas(table: pd.DataFrame, share: float = CRASH_SHARE) -> pd.DataFrame:
    """How each name moved on the index's WORST days: the name's average
    return on the index's worst `share` of days on file, divided by the
    index's average return on those days.

    A beta fitted on the last 250 days says how a name moved in that year.
    In a sell-off names move together more than in a calm year, so a stress
    fall should not use a calm-year beta: CNQ's and Suncor's were negative
    in 2026, which moved them UP in an index crash. These betas come from
    every bad day since 2014 (the 2015 oil collapse, March 2020, 2022), and
    no number is typed in. A ratio of averages, not a regression: on a few
    extreme days a regression line is pulled about by one point, and the
    question asked is simply "when the index fell, how far did this fall".

    Returns, per name: crash_beta, days (the sell-off days the name has a
    return on) and fell (the share of them on which it fell too)."""
    if INDEX not in table:
        raise ValueError("the returns table has no index column")
    idx = table[INDEX].dropna()
    cut = idx.quantile(share)
    bad = idx[idx <= cut]
    out = {}
    for n in table.columns:
        if n == INDEX:
            continue
        r = table.loc[bad.index, n].dropna()
        if len(r) < 20:
            raise ValueError(f"{n}: {len(r)} sell-off days with a return, too few for a beta")
        out[n] = dict(crash_beta=float(r.mean() / bad.loc[r.index].mean()), days=len(r),
                      fell=float((r < 0).mean()))
    return pd.DataFrame(out).T


def latest_inputs(name: str):
    """One underlying's latest cached day: (day, DayInputs, the day runner's
    inverted table). The rate curve carries the funding spread the day was
    priced with, if any, so a position valued here matches the book report's
    mark on that day."""
    days = cached_days(name)
    day = max(days)
    r = days[day]
    opts = load_mx_options(mx_files(name))
    opts = opts[(opts["underlying"] == name) & (opts["date"].dt.date == day)]
    fut = load_mx_futures(mx_files("CRA"))
    fut = fut[fut["root"] == "CRA"]
    cra = [(d.date(), e.date(), float(px))
           for d, e, px in zip(fut["date"], fut["expiry"], fut["settle"])]
    curve = load_base_curve(BOC, day, cra)
    if r.get("spread_used") is not None:
        curve = curve.plus(r["spread_used"])
    return day, DayInputs(day, r["spot"], curve, r["divs"], opts), r["inverted"]


def reprice(task) -> dict:
    """Runs in a worker. One underlying's positions under an array of
    returns: dollars of P&L, one row per scenario and one column per position.

    task = (name, positions, returns, rule, params); params are shocked_vol's
    keyword arguments (beta, crash, steepen)."""
    name, positions, moves, rule, params = task
    moves = np.asarray(moves, dtype=float)
    cols, notes = {}, []
    if name == "SXF":
        fut = load_mx_futures(mx_files("SXF"))
        for pos in positions:
            f = fut[fut["expiry"].dt.date == pos.expiry].sort_values("date")
            if f.empty:
                raise SystemExit(f"SXF {pos.expiry}: no such contract in the exchange files")
            level = float(f["settle"].iloc[-1])
            cols[label(pos)] = pos.quantity * pos.multiplier * level * moves
        return dict(name=name, pnl=cols, notes=notes, day=f["date"].iloc[-1].date())

    day, inp, inv = latest_inputs(name)
    inv = inv[inv["iv"].notna()]
    exp = pd.Series([e.date() if hasattr(e, "date") else e for e in inv["expiry"]],
                    index=inv.index)
    for pos in positions:
        v = value_position(pos, inp)
        if pos.kind != "option":
            cols[label(pos)] = v.scale * inp.spot * moves
            continue
        if not v.ok:
            notes.append(f"{label(pos)}: not valued ({v.reason}); left out of every scenario")
            cols[label(pos)] = np.full(len(moves), np.nan)
            continue
        chain = inv[exp == pos.expiry]
        smile = smile_from_points(chain["strike"], chain["iv"])
        T = year_fraction(day, pos.expiry)
        base = price_at(pos, inp, inp.spot, v.iv)
        own = smile(pos.strike)
        out = np.empty(len(moves))
        for i, m in enumerate(moves):
            s1 = inp.spot * (1.0 + m)
            vol = max(v.iv + shocked_vol(rule, smile, pos.strike, inp.spot, s1, T, **params)
                      - own, 0.01)
            out[i] = v.scale * (price_at(pos, inp, s1, vol) - base)
        cols[label(pos)] = out
    return dict(name=name, pnl=cols, notes=notes, day=day)
