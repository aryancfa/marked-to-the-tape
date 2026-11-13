"""
review_queue.py — the marks to look at, in the order to look at them (C4).

    .venv/Scripts/python scripts/review_queue.py books/diversified.csv
    .venv/Scripts/python scripts/review_queue.py books/bank_heavy.csv --day 2026-10-07

The question it answers: which positions need attention today, how much do
they matter, and what should be checked first?

Three kinds of item
-------------------
  failed check   the mark's implied vol disagrees with its call or put
                 partner, or with its smile, by more than the quote explains
  no mark        the position could not be marked or inverted at all
  watch          nothing failed, but an ex-date that has NOT been announced
                 falls within a few sessions of the option's expiry. If the
                 real date lands on the other side, the whole dividend moves
                 in or out of the option. This is what happened on Manulife
                 and XIU, and it can be seen coming.

For every item
--------------
  what was observed   in vol points against the quote's bar, or in words
  what it is worth    dollars, by REPRICING: the mark against its value at
                      the vol its reference implies (a failed check), or with
                      the dividend on the other side of the expiry (a watch)
  what it points to   a LIKELY cause, from the neighbours (src/review.py)
  the evidence        what the neighbours showed, so the call can be checked
  what to check next  one action

Run scripts/run_days.py for each name first; this reads that cache and
prices nothing about the market again.

Written to reports/<book>_review.txt, with a drill-down chart for the largest
failed items under reports/review/. Every figure and every chart is a
vol-point DIFFERENCE, a count, or dollars of dispute on a made-up book; no
price, quote or vol level is published (hard rule 1). The full list of items
goes to data/derived/review/, which is not published.
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

import numpy as np
import pandas as pd

from book import load_book
from book_report import cached_days
from fx import load_usdcad, rate_on
from loaders.mx import load_mx_futures, load_mx_options
from loaders.reference import as_known_on, load_dividends, rule_for, uncertain_ex_dates
from rates import load_base_curve
from references import PAIR, SMILE, reference_differences
from replay import (DayInputs, check_mark, dispute_dollars, next_session, reprice_at_vol,
                    reprice_with, value_position)
from review import (CAUSES, NO_MARK, WATCH_DATE, evidence, expiry_context, likely_cause,
                    next_check, observation, runs)
from run_days import BOC, FX, mx_files
from surface import DATE_WINDOW, sessions_between

FAILED, UNMARKED, WATCH = "failed check", "no mark", "watch"


def reference_gap(c, call_put: str) -> float:
    """Vol points the mark sits ABOVE its reference, taking the failed check
    with the larger disagreement. Against the smile that is the residual.
    Against the partner it is call minus put for a call, and the reverse for
    a put."""
    size = {}
    if "smile" in c.failed:
        size["smile"] = c.smile
    if "pair" in c.failed:
        size["pair"] = c.pair if call_put == "C" else -c.pair
    return max(size.values(), key=abs)


def one_name(task) -> list[dict]:
    """Runs in a worker: every option position of one underlying, every day."""
    name, positions, bars = task
    positions = [p for p in positions if p.kind == "option"]
    if not positions:
        return []
    days = cached_days(name)
    opts = load_mx_options(mx_files(name))
    opts = opts[opts["underlying"] == name]
    by_day = {ts.date(): g for ts, g in opts.groupby("date")}
    fut = load_mx_futures(mx_files("CRA"))
    fut = fut[fut["root"] == "CRA"]
    cra = [(d.date(), e.date(), float(px))
           for d, e, px in zip(fut["date"], fut["expiry"], fut["settle"])]
    schedule = load_dividends(ROOT / "reference" / f"{name.lower()}_dividends.csv")
    rates_fx = load_usdcad(FX) if any(x.currency != "CAD" for x in schedule) else None
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
        known = as_known_on(schedule, day, rule_for(name),
                            fx=(lambda d: rate_on(rates_fx, d)) if rates_fx else None)
        guesses = uncertain_ex_dates(known, day)
        for i, pos in enumerate(positions):
            series = f"{name} {pos.expiry:%b %Y} {pos.strike:g} {pos.call_put}"
            row = dict(day=day, name=name, key=f"{name}#{i}", series=series, kind="",
                       call_put=pos.call_put, strike=pos.strike, expiry=pos.expiry)
            v = value_position(pos, inp)
            if not v.ok:
                row.update(kind=UNMARKED, cause=NO_MARK, checked=False, dollars=np.nan,
                           observed=f"not valued: {v.reason}",
                           next=next_check(NO_MARK, name, pos.call_put))
                rows.append(row)
                continue
            row["nth"] = seen[i] = seen.get(i, -1) + 1      # this position's nth valued day
            c = check_mark(v, checks, bars)
            row.update(checked=c.pair is not None or c.smile is not None, pair=c.pair,
                       pair_bar=c.pair_bar, smile=c.smile, smile_bar=c.smile_bar)
            if c.failed:
                ctx = expiry_context(checks, pos.expiry, pos.strike, pos.call_put)
                carry = labels.get(pos.expiry)
                cause = likely_cause(c.failed, c.pair, c.smile, ctx, carry)
                here = checks[[(e.date() if hasattr(e, "date") else e) == pos.expiry
                               for e in checks["expiry"]] & checks["reference"].isin([PAIR, SMILE])]
                row.update(kind=FAILED, cause=cause, carry=carry, ctx=ctx, tier=v.tier,
                           failed=",".join(c.failed),
                           dollars=abs(reprice_at_vol(v, inp, reference_gap(c, pos.call_put))),
                           dollars_linear=dispute_dollars(v, c),
                           observed=observation(c.failed, c.pair, c.pair_bar, c.smile,
                                                c.smile_bar, v.tier),
                           next=next_check(cause, name, pos.call_put),
                           detail=here[["strike", "reference", "side", "diff", "quote_bar"]]
                           .to_dict("records"))
            else:
                close = [d for d in guesses
                         if abs(sessions_between(pos.expiry, d)) <= DATE_WINDOW]
                if close:
                    d = min(close, key=lambda x: abs(sessions_between(pos.expiry, x)))
                    other_side = pos.expiry if d > pos.expiry else next_session(pos.expiry)
                    moved = [(other_side, cash) if x == d else (x, cash) for x, cash in r["divs"]]
                    n = sessions_between(pos.expiry, d)
                    row.update(
                        kind=WATCH, cause=WATCH_DATE,
                        dollars=abs(reprice_with(v, inp, moved) or 0.0),
                        observed=("nothing failed; a projected ex-date falls "
                                  + (f"{n} session(s) after" if n > 0 else
                                     "on" if n == 0 else f"{-n} session(s) before")
                                  + " this option's expiry"),
                        evidence="dollars are the position repriced with that dividend on "
                                 "the other side of the expiry",
                        next=next_check(WATCH_DATE, name, pos.call_put))
            rows.append(row)
    return rows


def build(book_path: Path, bars: float = 1.0, workers: int = 6) -> pd.DataFrame:
    """Every option position-day; items carry the queue's fields."""
    names: dict[str, list] = {}
    for p in load_book(book_path):
        names.setdefault(p.underlying, []).append(p)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(one_name, [(n, ps, bars) for n, ps in names.items()
                                       if n != "SXF"]))
    rows = pd.DataFrame([r for part in out for r in part])
    for col in ("ctx", "detail", "evidence", "carry"):
        if col not in rows:
            rows[col] = None
    flagged = rows[rows["kind"] == FAILED]
    run = runs(flagged[["key"]].assign(day=flagged["nth"]))
    rows["run"] = run.reindex(rows.index)
    rows.loc[flagged.index, "evidence"] = [
        evidence(c, k, int(n)) for c, k, n in zip(flagged["ctx"], flagged["carry"], run)]
    return rows.drop(columns=["ctx"])


def item(n: int, r) -> list[str]:
    worth = "dollars not known" if pd.isna(r.dollars) else f"${r.dollars:,.0f}"
    return [f"   {n}. {r.series}   {worth}   {r.kind}; likely cause: {r.cause}",
            f"      observed: {r.observed}",
            f"      evidence: {r.evidence}",
            f"      next:     {r.next}"]


def draw(r, history: pd.DataFrame, path: Path) -> None:
    """The drill-down for one failed item. Three panels, all DIFFERENCES in
    vol points: every strike of the expiry against its smile, call minus put
    by strike, and this position's two disagreements over the days before."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = pd.DataFrame(r.detail)
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(15, 4.2))
    for side, marker, colour in (("C", "o", "#1f77b4"), ("P", "s", "#ff7f0e")):
        s = d[(d["reference"] == SMILE) & (d["side"] == side)]
        a1.errorbar(s["strike"], s["diff"], yerr=s["quote_bar"], fmt=marker, color=colour,
                    capsize=2, lw=0.8, ms=4, label="calls" if side == "C" else "puts")
    mine = d[(d["reference"] == SMILE) & (d["side"] == r.call_put) & (d["strike"] == r.strike)]
    if len(mine):
        a1.plot(mine["strike"], mine["diff"], "o", ms=14, mfc="none", mec="#d62728", mew=2)
    if not (d["reference"] == SMILE).any():
        a1.text(0.5, 0.5, "no smile for this expiry on this day:\ntoo few strikes near the "
                "money to fit one", ha="center", va="center", transform=a1.transAxes,
                fontsize=9)
    a1.axhline(0, color="black", lw=0.5)
    a1.set_title("each strike against its smile (bars: the quote's own uncertainty)\n"
                 "one point out = a quote; most points out = the curve", fontsize=9)
    a1.set_xlabel("strike"); a1.set_ylabel("vol points")
    if (d["reference"] == SMILE).any():
        a1.legend(fontsize=8, frameon=False)
    p = d[d["reference"] == PAIR]
    a2.errorbar(p["strike"], p["diff"], yerr=p["quote_bar"], fmt="D", color="#2ca02c",
                capsize=2, lw=0.8, ms=4)
    mine = p[p["strike"] == r.strike]
    if len(mine):
        a2.plot(mine["strike"], mine["diff"], "o", ms=14, mfc="none", mec="#d62728", mew=2)
    a2.axhline(0, color="black", lw=0.5)
    a2.set_title("call minus put, by strike\nall strikes off the same way = an input, "
                 "not a quote", fontsize=9)
    a2.set_xlabel("strike"); a2.set_ylabel("vol points")
    h = history.sort_values("day")
    a3.plot(h["day"], h["pair"], color="#2ca02c", lw=1, label="call minus put")
    a3.plot(h["day"], h["smile"], color="#1f77b4", lw=1, label="against its smile")
    f = h[h["kind"] == FAILED]
    a3.plot(f["day"], f["pair"].fillna(f["smile"]), "x", color="#d62728", ms=5,
            label="days it was queued")
    a3.axvline(pd.Timestamp(r.day), color="#d62728", lw=0.8, ls="--")
    a3.axhline(0, color="black", lw=0.5)
    a3.set_title("this position, day by day\na spike = a quote; a level that persists = "
                 "an input", fontsize=9)
    a3.set_ylabel("vol points"); a3.legend(fontsize=7, frameon=True, loc="best")
    fig.autofmt_xdate()
    fig.suptitle(f"{r.series}, {r.day:%d %b %Y}: ${r.dollars:,.0f} in dispute; likely cause: "
                 f"{r.cause} (the circled point is the position)", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("book")
    ap.add_argument("--day", help="the day whose queue to print (default: the latest "
                                  "day with at least one failed check)")
    ap.add_argument("--bars", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--charts", type=int, default=5,
                    help="drill-down charts to draw, for the largest failed items")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    path = Path(a.book)
    rows = build(path, a.bars, a.workers)
    rows["day"] = [d.date() if hasattr(d, "date") else d for d in rows["day"]]
    q = rows[rows["kind"] == FAILED].copy()
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    days = sorted(rows["day"].unique())
    say(f"Review queue for {path.name}, a made-up book: {days[0]:%d %b %Y} to "
        f"{days[-1]:%d %b %Y}, {len(days)} days.")
    say("A mark is queued when its implied vol disagrees with its call or put partner, or")
    say(f"with its smile, by more than {a.bars:g} quote bar(s) and the 0.04 noise floor.")
    say("Dollars are by repricing: the mark against its value at the vol its reference")
    say("implies. The cause is a LIKELY one, read from the neighbouring strikes by stated")
    say("rules (src/review.py); how often those rules are right is in")
    say("reports/review_benchmark.txt.")
    say()
    say("1. HOW MUCH THERE IS TO REVIEW")
    valued = rows[rows["kind"] != UNMARKED]
    checked = int(valued["checked"].sum())
    say(f"   option position-days: {len(rows)}; with no usable mark: "
        f"{int((rows['kind'] == UNMARKED).sum())}; checkable: {checked}; failed a check: "
        f"{len(q)} ({100 * len(q) / max(checked, 1):.1f}% of checkable); on watch: "
        f"{int((rows['kind'] == WATCH).sum())}")
    per_day = q.groupby("day").size().reindex(days, fill_value=0)
    say(f"   failed checks a day: mean {per_day.mean():.2f}, most {int(per_day.max())}; "
        f"days with none: {int((per_day == 0).sum())} of {len(days)}")
    lin = q["dollars_linear"].sum()
    say(f"   dollars in dispute over the period: {q['dollars'].sum():,.0f} by repricing, "
        f"{lin:,.0f} by vega times the disagreement "
        f"({100 * (q['dollars'].sum() / lin - 1):+.1f}%)")
    say()
    say("2. WHAT THE FAILED CHECKS ARE MADE OF, by likely cause")
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
    top = q.sort_values("dollars", ascending=False).head(a.top)
    chart_dir = ROOT / "reports" / "review"
    chart_dir.mkdir(parents=True, exist_ok=True)
    for old in chart_dir.glob(f"{path.stem}_*.png"):
        old.unlink()
    say(f"4. THE {a.top} LARGEST FAILED CHECKS OF THE PERIOD")
    for n, r in enumerate(top.itertuples(), 1):
        say(f"   [{r.day:%d %b %Y}]")
        for line in item(n, r):
            say(line)
        if n <= a.charts:
            png = chart_dir / f"{path.stem}_{n}.png"
            draw(r, rows[rows["key"] == r.key], png)
            say(f"      chart:    {png.relative_to(ROOT).as_posix()}")
    say()
    day = date.fromisoformat(a.day) if a.day else max(q["day"])
    today = rows[(rows["day"] == day) & (rows["kind"] != "")].sort_values(
        "dollars", ascending=False, na_position="first")
    say(f"5. THE QUEUE FOR {day:%d %b %Y}: {len(today)} item(s), largest first, a position "
        "with no mark before everything")
    if today.empty:
        say("   nothing to review")
    for n, r in enumerate(today.itertuples(), 1):
        for line in item(n, r):
            say(line)
    say()
    w = rows[rows["kind"] == WATCH]
    say("6. ON WATCH: nothing failed, but one unannounced date decides a lot")
    if w.empty:
        say("   none in the period")
    else:
        ws = w.groupby("series").agg(days=("day", "size"), first=("day", "min"),
                                     last=("day", "max"), largest=("dollars", "max"),
                                     mean=("dollars", "mean"))
        say(ws.sort_values("largest", ascending=False)
            .to_string(float_format=lambda x: f"{x:,.0f}").replace("\n", "\n   ")
            .join(["   ", ""]))
        say("   Dollars are the position repriced with that dividend on the other side of its")
        say("   expiry: what is at stake if the projected date is a few days out.")

    out = ROOT / "reports" / f"{path.stem}_review.txt"
    out.write_text("Derived statistics only: vol-point differences, counts and dollars of dispute\n"
                   "on a made-up book. No price or quote appears in this file.\n\n"
                   + "\n".join(lines) + "\n", encoding="utf-8")
    full = ROOT / "data/derived/review"
    full.mkdir(parents=True, exist_ok=True)
    rows[rows["kind"] != ""].drop(columns=["detail"]).to_csv(
        full / f"{path.stem}_items.csv", index=False)
    print(f"\nsaved: {out.relative_to(ROOT)}; every item: "
          f"{(full / (path.stem + '_items.csv')).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
