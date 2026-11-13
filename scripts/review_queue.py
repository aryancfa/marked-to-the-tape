"""
review_queue.py — the marks to look at, in the order to look at them (C4).

    .venv/Scripts/python scripts/review_queue.py books/diversified.csv
    .venv/Scripts/python scripts/review_queue.py books/bank_heavy.csv --day 2026-10-07

For every option mark in the book that failed a check on a day, one item:

  what was observed   the disagreement in vol points, against the quote's bar
  what it is worth    dollars: position vega times the disagreement
  what it points to   a LIKELY cause, from the neighbours (src/review.py)
  the evidence        what the neighbours showed, so the call can be checked
  what to check next  one action

Run scripts/run_days.py for each name first; this reads that cache and
prices nothing about the market again.

Written to reports/<book>_review.txt: the count and dollars by likely cause,
the largest items, and the queue for one day (the latest by default). Every
figure is a vol-point difference, a count, or dollars of dispute on a
made-up book; no price or quote is printed (hard rule 1). The full list of
items goes to data/derived/review/, which is not published.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd

from book import load_book
from book_report import cached_days
from loaders.mx import load_mx_futures, load_mx_options
from rates import load_base_curve
from references import reference_differences
from replay import DayInputs, check_mark, dispute_dollars, value_position
from review import (CAUSES, evidence, expiry_context, likely_cause, next_check, observation,
                    runs)
from run_days import BOC, mx_files


def one_name(task) -> list[dict]:
    """Runs in a worker: every option position of one underlying, every day."""
    name, positions, bars = task
    positions = [p for p in positions if p.kind == "option"]
    if not positions or name == "SXF":
        return []
    days = cached_days(name)
    opts = load_mx_options(mx_files(name))
    opts = opts[opts["underlying"] == name]
    by_day = {ts.date(): g for ts, g in opts.groupby("date")}
    fut = load_mx_futures(mx_files("CRA"))
    fut = fut[fut["root"] == "CRA"]
    cra = [(d.date(), e.date(), float(px))
           for d, e, px in zip(fut["date"], fut["expiry"], fut["settle"])]
    expiries = {p.expiry for p in positions}
    rows, seen = [], {}
    for day in sorted(days):
        r = days[day]
        curve = load_base_curve(BOC, day, cra)
        if r.get("spread_used") is not None:
            curve = curve.plus(r["spread_used"])
        inv = r["inverted"]
        exp = pd.Series([e.date() if hasattr(e, "date") else e for e in inv["expiry"]],
                        index=inv.index)
        near = inv[exp.isin(expiries)]
        checks = reference_differences(near, r["spot"], divs=r["divs"]) if len(near) else None
        inp = DayInputs(day, r["spot"], curve, r["divs"], by_day.get(day), checks)
        labels = r["summary"]["carry"].to_dict()
        for i, pos in enumerate(positions):
            v = value_position(pos, inp)
            if not v.ok:
                continue
            nth = seen[i] = seen.get(i, -1) + 1         # this position's nth valued day
            c = check_mark(v, checks, bars)
            row = dict(day=day, name=name, key=f"{name}#{i}", nth=nth,
                       series=f"{name} {pos.expiry:%b %Y} {pos.strike:g} {pos.call_put}",
                       checked=c.pair is not None or c.smile is not None,
                       failed=",".join(c.failed), dollars=dispute_dollars(v, c))
            if c.failed:
                ctx = expiry_context(checks, pos.expiry, pos.strike, pos.call_put)
                carry = labels.get(pos.expiry)
                cause = likely_cause(c.failed, c.pair, c.smile, ctx, carry)
                row.update(cause=cause, call_put=pos.call_put, carry=carry, ctx=ctx,
                           tier=v.tier, pair=c.pair, pair_bar=c.pair_bar, smile=c.smile,
                           smile_bar=c.smile_bar,
                           observed=observation(c.failed, c.pair, c.pair_bar, c.smile,
                                                c.smile_bar, v.tier),
                           next=next_check(cause, name, pos.call_put))
            rows.append(row)
    return rows


def build(book_path: Path, bars: float = 1.0, workers: int = 6) -> pd.DataFrame:
    """Every valued option position-day; flagged ones carry the item fields."""
    names: dict[str, list] = {}
    for p in load_book(book_path):
        names.setdefault(p.underlying, []).append(p)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(one_name, [(n, ps, bars) for n, ps in names.items()]))
    rows = pd.DataFrame([r for part in out for r in part])
    flagged = rows[rows["failed"] != ""]
    run = runs(flagged[["key"]].assign(day=flagged["nth"]))
    rows["run"] = run.reindex(rows.index)
    rows.loc[flagged.index, "evidence"] = [
        evidence(c, k, int(n)) for c, k, n in zip(flagged["ctx"], flagged["carry"], run)]
    return rows.drop(columns=["ctx"])


def item(n: int, r) -> list[str]:
    return [f"   {n}. {r.series}   ${r.dollars:,.0f} in dispute   likely cause: {r.cause}",
            f"      observed: {r.observed}",
            f"      evidence: {r.evidence}",
            f"      next:     {r.next}"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("book")
    ap.add_argument("--day", help="the day whose queue to print (default: the latest "
                                  "day with at least one item)")
    ap.add_argument("--bars", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    path = Path(a.book)
    rows = build(path, a.bars, a.workers)
    q = rows[rows["failed"] != ""].copy()
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    days = sorted(rows["day"].unique())
    say(f"Review queue for {path.name}, a made-up book: {days[0]:%d %b %Y} to "
        f"{days[-1]:%d %b %Y}, {len(days)} days.")
    say("A mark is queued when its implied vol disagrees with its call or put partner, or")
    say(f"with its smile, by more than {a.bars:g} quote bar(s) and the 0.04 noise floor.")
    say("Dollars are the position's vega times the disagreement. The cause is a LIKELY")
    say("one, read from the neighbouring strikes by stated rules (src/review.py).")
    say()
    say("1. HOW MUCH THERE IS TO REVIEW")
    checked = int(rows["checked"].sum())
    say(f"   option position-days valued: {len(rows)}; checkable: {checked}; "
        f"queued: {len(q)} ({100 * len(q) / max(checked, 1):.1f}% of checkable)")
    per_day = q.groupby("day").size().reindex(days, fill_value=0)
    say(f"   items a day: mean {per_day.mean():.2f}, most {int(per_day.max())}; "
        f"days with none: {int((per_day == 0).sum())} of {len(days)}")
    say()
    say("2. WHAT THE QUEUE IS MADE OF, by likely cause")
    by = q.groupby("cause").agg(items=("dollars", "size"), dollars=("dollars", "sum"),
                                series=("series", "nunique"),
                                longest_run=("run", "max")).reindex(CAUSES).dropna(how="all")
    by["share of dollars %"] = 100 * by["dollars"] / by["dollars"].sum()
    say(by.to_string(float_format=lambda x: f"{x:,.0f}").replace("\n", "\n   ")
        .join(["   ", ""]))
    not_mark = q[~q["cause"].isin(["this mark", "unclear"])]
    say(f"   items where the position's own mark is NOT the likely fault: {len(not_mark)} of "
        f"{len(q)} ({100 * len(not_mark) / max(len(q), 1):.0f}%), "
        f"${not_mark['dollars'].sum():,.0f} of ${q['dollars'].sum():,.0f}.")
    say("   Those are the ones a plain 'call and put disagree' flag would send to the wrong")
    say("   desk: the fix is an input or someone else's quote, not a re-mark.")
    say()
    say("3. BY NAME")
    nm = q.pivot_table(index="name", columns="cause", values="dollars", aggfunc="size",
                       fill_value=0).reindex(columns=[c for c in CAUSES if c in set(q["cause"])])
    nm["items"] = nm.sum(axis=1)
    say(nm.sort_values("items", ascending=False).to_string().replace("\n", "\n   ")
        .join(["   ", ""]))
    say()
    say(f"4. THE {a.top} LARGEST ITEMS OF THE PERIOD")
    for n, r in enumerate(q.sort_values("dollars", ascending=False).head(a.top).itertuples(), 1):
        say(f"   [{r.day:%d %b %Y}]")
        for line in item(n, r):
            say(line)
    say()
    day = date.fromisoformat(a.day) if a.day else max(q["day"])
    today = q[q["day"] == day].sort_values("dollars", ascending=False)
    say(f"5. THE QUEUE FOR {day:%d %b %Y}, largest first "
        f"({len(today)} item(s), ${today['dollars'].sum():,.0f})")
    if today.empty:
        say("   nothing to review")
    for n, r in enumerate(today.itertuples(), 1):
        for line in item(n, r):
            say(line)

    out = ROOT / "reports" / f"{path.stem}_review.txt"
    out.write_text("Derived statistics only: vol-point differences, counts and dollars of dispute\n"
                   "on a made-up book. No price or quote appears in this file.\n\n"
                   + "\n".join(lines) + "\n", encoding="utf-8")
    full = ROOT / "data/derived/review"
    full.mkdir(parents=True, exist_ok=True)
    q.to_csv(full / f"{path.stem}_items.csv", index=False)
    print(f"\nsaved: {out.relative_to(ROOT)}; every item: "
          f"{(full / (path.stem + '_items.csv')).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
