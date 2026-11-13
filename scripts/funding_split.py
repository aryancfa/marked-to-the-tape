"""
funding_split.py — split the carry the options imply into funding and dividends (B5).

Run scripts/run_days.py for the name first; this reads the results it cached.

    .venv/Scripts/python scripts/funding_split.py RY

One number has been standing for everything the options say about carry: the
funding spread. A desk keeps three things apart:

  funding    what it costs to finance the share position, per year
  dividends  what the market expects each future dividend to be
  borrow     what it costs to borrow the share to short it

How they are told apart here
----------------------------
Each leaves a different mark on the forward price (the share price for later
delivery), which the call-put gap measures expiry by expiry:

  * A funding cost moves the forward a little more with every extra day, the
    same way before and after any dividend. It is fitted on expiries whose
    dividends are all DECLARED, on earlier days only (run_days.py), and
    applied to every expiry.
  * A dividend the market expects to differ from the one in the schedule
    moves the forward by that difference, as a STEP: nothing before its
    ex-date, the full amount on every expiry after. So the change in forward
    error between the last expiry before an ex-date and the first one after
    it, times the share price, is the market's dividend minus the schedule's.
    Averaged over days, that is the dividend implied by options.
  * Borrow looks exactly like funding with the opposite sign (it lowers the
    forward steadily). The two cannot be separated from option prices alone,
    so borrow is ASSUMED ZERO and whatever borrow there is sits inside the
    funding number. That is harmless for names that are easy to borrow and
    wrong for hard-to-borrow ones.

What this cannot do. The funding spread is measured on expiries out to a few
months and assumed to hold for a year or more. If funding really costs more
at long maturities, that shows up here as a dividend the market expects to be
smaller. Each basis point a year of funding error is one basis point of share
price per year of maturity, in the implied dividends added together.

Prints DERIVED numbers only and writes reports/<name>_funding_split.txt.
"""

from __future__ import annotations

import pickle
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from loaders.reference import load_dividends
from fx import load_usdcad, rate_on
from run_days import FX, fingerprint, one_per_day
from surface import mean_std_error


def load(name: str) -> list[dict]:
    """The funded pass from the cache (pickles this project wrote itself)."""
    cache = ROOT / "data/derived" / name.lower() / fingerprint(name)
    files = sorted(cache.glob("*_base_s*.pkl")) if cache.exists() else []
    out = [pickle.loads(f.read_bytes()) for f in files]
    out = [r for r in out if r.get("ok")]
    if not out:
        raise SystemExit(f"no funded results cached for {name}; run scripts/run_days.py {name}")
    return one_per_day(out, f"{name} funding split")


def implied_steps(results: list[dict], schedule, fx=None) -> pd.DataFrame:
    """One row per (day, dividend not yet known that day): the schedule's
    amount and the amount the options imply, from the step in forward error
    across its ex-date. Only where exactly one such dividend falls between
    the two expiries used."""
    rows = []
    for r in results:
        day: date = r["date"]
        s = r["summary"]
        s = s[np.isfinite(s["fwd_error_bp"])].sort_index()
        expiries = list(s.index)
        unknown = [x for x in schedule if x.ex_date > day and (
            x.status == "projected" or (x.announced is not None and x.announced > day))]
        for x in unknown:
            before = [e for e in expiries if e < x.ex_date]
            after = [e for e in expiries if e >= x.ex_date]
            if not before or not after:
                continue
            e0, e1 = before[-1], after[0]
            if sum(1 for y in schedule if e0 < y.ex_date <= e1) != 1:
                continue                            # two dividends in the gap: not separable
            step_bp = float(s.loc[e1, "fwd_error_bp"] - s.loc[e0, "fwd_error_bp"])
            cash = x.cash if x.currency == "CAD" else x.cash * rate_on(fx, day)
            rows.append(dict(date=day, ex_date=x.ex_date, schedule=cash, status=x.status,
                             implied=cash - step_bp / 1e4 * r["spot"], step_bp=step_bp,
                             days_between=(e1 - e0).days))
    return pd.DataFrame(rows)


def main(name: str) -> None:
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text)
        lines.append(text)

    results = load(name)
    schedule = load_dividends(ROOT / "reference" / f"{name.lower()}_dividends.csv")
    used = pd.Series([r["spread_used"] for r in results]) * 1e4
    own = pd.Series([r["funding_spread"] for r in results
                     if r["funding_spread"] is not None]) * 1e4

    say("Derived statistics only. Dividend amounts are per share; the schedule's are")
    say("public or projected by rule, and the implied ones are computed from option prices.")
    say()
    say(f"{name}: {len(results)} days, {results[0]['date']} to {results[-1]['date']}.")
    say()
    say("1. FUNDING (fitted on expiries with declared dividends, earlier days only):")
    say(f"   applied: {used.min():.1f} to {used.max():.1f} bp a year, last {used.iloc[-1]:.1f}.")
    if len(own) < 2:
        say("   left over after applying it: cannot be measured. On these days no long expiry")
        say("   rests on declared dividends alone, so the spread applied is not tested here.")
    else:
        say(f"   left over on the same days after applying it: {own.mean():+.1f} bp a year "
            f"(standard error {mean_std_error(own):.1f}, {len(own)} days). Near zero means "
            "the spread did its job.")
    say()
    say("2. BORROW: assumed zero. It cannot be told from funding in option prices, so any")
    say("   borrow cost is inside the funding number above.")

    foreign = any(x.currency != "CAD" for x in schedule)
    steps = implied_steps(results, schedule, load_usdcad(FX) if foreign else None)
    if foreign:
        say()
        say("   This name declares in US dollars. Schedule amounts below are converted at")
        say("   each valuation day's rate, as the day runner does, and averaged.")
    say()
    say("3. DIVIDENDS the options imply, against the schedule, for each dividend that was")
    say("   not yet known on the day (projected, or announced later):")
    if steps.empty:
        say("   none could be isolated (no dividend sits alone between two expiries).")
    else:
        table = []
        for ex, g in steps.groupby("ex_date"):
            g = g.sort_values("date")
            m, se = g["implied"].mean(), mean_std_error(g["implied"])
            sched = float(g["schedule"].iloc[0])
            table.append({"ex_date": ex, "status": g["status"].iloc[0], "days": len(g),
                          "schedule": sched, "implied": m, "std_error": se,
                          "implied minus schedule": m - sched,
                          "t": (m - sched) / se if se and se > 0 else np.nan,
                          "expiries apart (days)": g["days_between"].median()})
        t = pd.DataFrame(table)
        say(t.round(3).to_string(index=False))
        proj = t[(t["status"] == "projected") & (t["days"] >= 20)]
        if len(proj):
            say(f"   Projected dividends seen on 20+ days: schedule total "
                f"{proj['schedule'].sum():.3f}, implied total {proj['implied'].sum():.3f} "
                f"({(proj['implied'].sum() / proj['schedule'].sum() - 1) * 100:+.1f}%).")
        say("   Read with the limit in mind: one basis point a year of funding left out is")
        say("   one basis point of share price per year of maturity, and lands in these.")
        spot = float(np.mean([r["spot"] for r in results]))
        say(f"   For scale, on this name one basis point of the share price is "
            f"{spot / 1e4:.3f} a share.")

    path = ROOT / "reports" / f"{name.lower()}_funding_split.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nnumbers: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1].upper())
