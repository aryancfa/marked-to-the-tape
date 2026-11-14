"""
oil_spread.py — the WCS discount as its own factor, and a month in which it
widens with WTI flat (C3).

    .venv/Scripts/python scripts/oil_spread.py books/diversified.csv

1. Each name's monthly sensitivity to the index, to WTI and to the WCS
   discount, fitted together since 2014 (src/oilfactor.py), with t
   statistics.
2. Two scenarios with the index and WTI FLAT and only the discount widening:
   the worst such month on record, and the discount's worst 5% of months.
   Each name moves by its fitted discount sensitivity; the book is priced at
   the end of a month (21 sessions, B6), with the C1 rule's vol. The same
   scenario is priced again with every sensitivity two standard errors more
   harmful and two less, because most of them are not distinguishable from
   zero and the dollar figure should show that.

Oil prices: Government of Alberta, economic data service, table OilPrices
(WTI and WCS, monthly averages, US dollars a barrel; Open Government Licence -
Alberta), saved by hand under data/raw/oil/. Every dollar figure is a MODEL
price on a rule-based vol (hard rule 6), on a made-up book. The report prints
fitted sensitivities and changes, not prices.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import pandas as pd

from book import load_book
from oilfactor import (FACTORS, fit, load_alberta_oil, monthly_average_returns, oil_factors,
                       widening_cases)
from scenarios import INDEX, reprice_horizon, returns_table
from volrule import BETA

OIL = ROOT / "data/raw/oil/alberta_oil_prices_api.json"
START = "2014-02"
MONTH = 21                     # sessions in the scenario month


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("book")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    path = Path(a.book)
    names: dict[str, list] = {}
    for p in load_book(path):
        names.setdefault(p.underlying, []).append(p)
    if not OIL.exists():
        raise SystemExit(f"{OIL.relative_to(ROOT)} is missing: see the module note for the source")

    oil = load_alberta_oil(OIL)
    daily = returns_table([n for n in names if n != "SXF"])
    monthly = monthly_average_returns(daily)
    X = oil_factors(oil, monthly[INDEX]).loc[START:]
    fits = {n: fit(monthly[n].rename(n), X) for n in names if n != "SXF"}
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    say(f"The WCS discount as a factor, for {path.name}, a made-up book.")
    say(f"Monthly, {X.index[0]} to {X.index[-1]} ({len(X)} months). MODEL prices on a "
        "rule-based vol below.")
    say()
    say("1. SENSITIVITIES, fitted together: % move per 1% move in the index, per 1% in WTI,")
    say("   and per 1% that WCS RISES against WTI (the discount narrowing). A negative")
    say("   'discount' figure means the name tended to do better when the discount WIDENED.")
    t = pd.DataFrame({n: {"months": f.months,
                          **{k: f.beta[k] for k in FACTORS},
                          **{f"t {k}": f.t(k) for k in ("wti", "discount")}}
                      for n, f in fits.items()}).T
    t["months"] = t["months"].astype(int)
    say(t.to_string(float_format=lambda x: f"{x:+.2f}").replace("\n", "\n   ")
        .join(["   ", ""]))
    strong = [n for n, f in fits.items() if abs(f.t("discount")) >= 2]
    say(f"   Names whose discount sensitivity is two standard errors from zero: "
        f"{', '.join(strong) if strong else 'none'}.")
    producers = [n for n in ("CNQ", "SU", "XEG") if n in fits]
    say("   No name's sensitivity to the discount is distinguishable from zero. If anything the")
    say(f"   producers ({', '.join(producers)}) did slightly BETTER when it widened: they own")
    say("   upgraders and refineries that turn heavy crude into products priced off WTI, so")
    say("   cheap heavy oil helps that part of the business. The discount was first measured")
    say("   in dollars a barrel, which showed nothing either; the share-of-price measure was")
    say("   chosen after both were tried. Read the signs as a hint, not a finding.")
    say()

    cases = widening_cases(X)
    say(f"2. THE INDEX AND WTI FLAT, THE DISCOUNT WIDENING, over {MONTH} sessions, dollars")
    rows = {}
    for label, change in cases:
        for how, k in (("fitted", 0.0), ("all fall more", -2.0), ("all fall less", 2.0)):
            # every name's sensitivity moved two standard errors at once, toward
            # a bigger fall on this move or a smaller one. Whether that is a loss
            # depends on whether the book is long or short each name: not used here
            moves = {}
            for n in names:
                if n == "SXF":
                    moves[n] = [0.0]
                    continue
                f = fits[n]
                b = f.beta["discount"] + k * f.se["discount"] * np.sign(change)
                moves[n] = [b * change]
            tasks = [(n, ps, moves[n], BETA, dict(beta=-0.5, crash=-0.10, steepen=0.5), MONTH)
                     for n, ps in names.items()]
            with ProcessPoolExecutor(max_workers=a.workers) as pool:
                res = list(pool.map(reprice_horizon, tasks))
            parts = [p for x in res for p in x["parts"].values()]
            move = float(sum(p["shock"][0] for p in parts))
            carry = float(sum(p["decay"] + p["funding"] + p["dividends"] for p in parts))
            rows[(label, how)] = dict(change=change, move=move, carry=carry)
        say(f"   {label}: WCS fell {100 * -change:.1f}% against WTI")
        for how in ("fitted", "all fall more", "all fall less"):
            r = rows[(label, how)]
            say(f"     {how:>14}: the move {r['move']:+,.0f}; with the month's carry "
                f"{r['move'] + r['carry']:+,.0f}")
    say("   'all fall more' and 'all fall less' move every name's sensitivity two standard")
    say("   errors at once, toward a bigger fall or a smaller one; they bound the answer")
    say("   loosely. The carry is the same month passing with no move at all, kept apart as")
    say("   in the stress report (B6).")
    say()
    say("Vol: the C1 beta rule on each name's move (no market vol exists for an imagined")
    say("month). Oil prices: Government of Alberta economic data, OilPrices, monthly averages.")

    out = ROOT / "reports" / f"{path.stem}_oil.txt"
    out.write_text("Fitted sensitivities and model prices on a made-up book.\n"
                   "No price or quote appears in this file.\n\n" + "\n".join(lines) + "\n",
                   encoding="utf-8")
    print(f"\nsaved: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
