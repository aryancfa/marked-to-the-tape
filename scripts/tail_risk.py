"""
tail_risk.py — the fund page: the worst days, which positions cause them, and
whether the VaR has kept its word (C5, C7).

    .venv/Scripts/python scripts/tail_risk.py books/diversified.csv
    .venv/Scripts/python scripts/tail_risk.py books/bank_heavy.csv --rule "sticky strike"

Three parts, and they use two different kinds of P&L. Each is labelled.

  1. ON THE MARKS (C7). The book's actual daily P&L from the exchange's
     marks, about 127 days: historical 99% VaR and 97.5% expected shortfall,
     unhedged and after the delta hedge. Real, and short: at 97.5% the tail
     is three days.

  2. HAS THE VaR BEEN HONEST? (C7). Each evening a one-day 99% VaR is set
     from the 250 trading days before, applied to that evening's book through
     its delta, gamma and vega (vega through the beta vol rule). The next
     day's ACTUAL marked P&L is set against it. Days the loss went past it
     are listed, with Kupiec's test and the Basel traffic light.

  3. WHICH POSITION DRIVES THE TAIL (C5). Six months has too few bad days to
     split among fourteen positions. So today's book is repriced IN FULL
     under every day's joint moves since 2015 (about 2,900 scenarios), with
     vol given by the chosen rule. Component ES is each position's average
     P&L on the book's worst 2.5% of scenarios; the components add up to the
     book's ES exactly. The same is shown with the delta hedge added as
     positions of its own. These are MODEL prices on a rule-based vol.

Published: dollars summed over a made-up book or by position of it, dates,
counts and test statistics. No price or quote.
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

import book_hedge as bh
from book import load_book
from replay import STEPS
from scenarios import INDEX, reprice, returns_table
from tailrisk import component_es, es, kupiec, marginal_es, tail_size, traffic_light, var
from volrule import BETA, RULES

WINDOW = 250
FIRST_SCENARIO = "2015-01-01"


def money(t: pd.DataFrame) -> str:
    return t.to_string(float_format=lambda x: f"{x:,.0f}").replace("\n", "\n   ").join(["   ", ""])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("book")
    ap.add_argument("--rule", choices=RULES, default=BETA)
    ap.add_argument("--beta", type=float, default=-0.5)
    ap.add_argument("--crash", type=float, default=-0.10)
    ap.add_argument("--steepen", type=float, default=0.5)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    path = Path(a.book)
    params = dict(beta=a.beta, crash=a.crash, steepen=a.steepen)
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    names: dict[str, list] = {}
    for p in load_book(path):
        names.setdefault(p.underlying, []).append(p)
    table = returns_table(list(names))

    # ---- the marked history, with each evening's Greeks -------------------
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        marked = list(pool.map(bh.one_name, [(n, ps, None) for n, ps in names.items()]))
    rows = pd.DataFrame([r for x in marked for r in x["rows"]])
    rows["day"] = pd.to_datetime(rows["day"])
    daily = rows.groupby("day")[[*STEPS, "total"]].sum()
    D = rows.pivot_table(index="day", columns="name", values="D0", aggfunc="sum")
    G = rows.pivot_table(index="day", columns="name", values="G0", aggfunc="sum")
    V = rows.pivot_table(index="day", columns="name", values="V0", aggfunc="sum")
    rolling, hindsight, X = bh.delta_ratios(list(D.columns))
    hedged = daily["total"] + bh.delta_hedge(D, rolling, hindsight, X)

    say(f"Tail risk for {path.name}, a made-up book.")
    say()
    say(f"1. ON THE MARKS: the book's actual daily P&L, {len(daily)} days "
        f"({daily.index.min():%d %b} to {daily.index.max():%d %b %Y}), dollars")
    k = tail_size(len(daily), 0.975)
    t = pd.DataFrame({"unhedged": [var(daily["total"], 0.99), es(daily["total"], 0.975),
                                   -daily["total"].min(), daily["total"].std()],
                      "delta hedged": [var(hedged, 0.99), es(hedged, 0.975), -hedged.min(),
                                       hedged.std()]},
                     index=["99% VaR", "97.5% expected shortfall", "worst day's loss",
                            "daily st. dev."])
    say(money(t))
    say(f"   From {len(daily)} days the 97.5% tail is {k} days and the 99% VaR is the "
        f"{'worst' if tail_size(len(daily), 0.99) == 1 else 'second worst'} day. These are "
        "real marks and a thin sample.")
    say()

    # ---- the backtest -----------------------------------------------------
    say("2. HAS THE VaR BEEN HONEST? One-day 99% VaR set each evening, against the next")
    say("   day's actual marked P&L")
    hist = table.dropna(subset=[INDEX])
    out = []
    whole = D.notna().all(axis=1)           # days every underlying in the book was valued
    for day in daily.index:
        before = hist.loc[hist.index < day].tail(WINDOW)
        if len(before) < WINDOW or day not in D.index or not whole.loc[day]:
            continue
        sim = np.zeros(len(before))
        for n in D.columns:
            r = (before[INDEX] if n == "SXF" else before[n]).fillna(0.0).to_numpy()
            d, g, v = (float(x.loc[day, n]) if pd.notna(x.loc[day, n]) else 0.0
                       for x in (D, G, V))
            # delta, then curvature, then vol moved by the rule: beta points per 1%
            sim += d * r + g * r * r + v * (a.beta * r * 100.0)
        out.append(dict(day=day, var=var(sim, 0.99), actual=float(daily.loc[day, "total"])))
    bt = pd.DataFrame(out).set_index("day")
    bt["exception"] = bt["actual"] < -bt["var"]
    x, n = int(bt["exception"].sum()), len(bt)
    stat, pval = kupiec(x, n, 0.99)
    zone, cdf = traffic_light(x, n, 0.99)
    say(f"   Only days on which every underlying in the book was valued are tested "
        f"({n} of {len(daily)}): on the")
    say("   others some files had not started or had ended, and the book was a part of itself.")
    say(f"   {n} days tested; VaR averaged {bt['var'].mean():,.0f} (from {bt['var'].min():,.0f} "
        f"to {bt['var'].max():,.0f}).")
    say(f"   Days the loss went past it: {x}, where a correct 99% VaR expects "
        f"{0.01 * n:.1f}.")
    if x:
        say("   Those days: " + ", ".join(
            f"{d:%d %b %Y} (loss {-r['actual']:,.0f} against {r['var']:,.0f})"
            for d, r in bt[bt["exception"]].iterrows()))
    say(f"   Kupiec's test: statistic {stat:.2f}, p-value {pval:.2f}. "
        + ("Not distinguishable from a correct VaR." if pval >= 0.05 else
           "The count is not chance: the VaR is mis-sized."))
    say(f"   Basel traffic light: {zone.upper()} (a correct model shows this many or fewer "
        f"{100 * cdf:.2f}% of the time; yellow starts at 95%, red at 99.99%).")
    say(f"   Read with care: {n} days cannot tell a 99% VaR from a 98% one. The VaR here")
    say("   prices each evening's book through its delta, gamma and vega, not in full. And a")
    say("   window of the 250 days before knows only the market those days had: when moves")
    say("   get larger than that year's, a historical VaR is too small until it catches up.")
    say(f"   Over these days the book's actual daily st. dev. was {bt['actual'].std():,.0f}; "
        f"the windows behind the VaR implied about {bt['var'].mean() / 2.326:,.0f}.")
    say()

    # ---- component ES by full revaluation --------------------------------
    cols = [c for c in table.columns if c == INDEX or c in names]
    scen = table.loc[FIRST_SCENARIO:, cols].dropna()
    tasks = [(nm, ps, scen[INDEX if nm == "SXF" else nm].to_numpy(), a.rule, params)
             for nm, ps in names.items()]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        res = list(pool.map(reprice, tasks))
    pnl = pd.DataFrame({k_: v_ for x_ in res for k_, v_ in x_["pnl"].items()}, index=scen.index)
    notes = [nt for x_ in res for nt in x_["notes"]]
    pnl = pnl.fillna(0.0)
    total = pnl.sum(axis=1)
    k = tail_size(len(pnl), 0.975)
    say(f"3. WHICH POSITION DRIVES THE TAIL. Today's book under each of {len(pnl):,} days'")
    say(f"   joint moves, {scen.index.min():%d %b %Y} to {scen.index.max():%d %b %Y}, repriced in "
        f"full. Vol rule: {a.rule}"
        + (f" ({a.beta:+.2f} points per 1%)" if a.rule == BETA else "") + ".")
    say("   MODEL prices on a rule-based vol: none of this is a mark.")
    for nt in notes:
        say("   NOTE: " + nt)
    say(f"   Book: 97.5% expected shortfall {es(total, 0.975):,.0f} (the average of the worst "
        f"{k} scenarios); 99% VaR {var(total, 0.99):,.0f}.")
    comp = pd.DataFrame({"component ES": component_es(pnl, 0.975),
                         "marginal ES": marginal_es(pnl, 0.975),
                         "own ES, alone": pnl.apply(lambda c: es(c, 0.975))})
    comp["share of the book's ES %"] = 100 * comp["component ES"] / comp["component ES"].sum()
    comp = comp.sort_values("component ES", ascending=False)
    say(money(comp))
    say(f"   The components add to {comp['component ES'].sum():,.0f}, the book's ES. A negative "
        "one is a position that")
    say("   makes money on the book's worst days. 'Own ES, alone' is what the position would")
    say("   lose in ITS worst scenarios; the gap between that and its component is what")
    say("   holding it beside the others saves.")
    worst = total.sort_values().index[:k]
    say(f"   The worst scenarios fall in: "
        + ", ".join(f"{y} ({c})" for y, c in
                    pd.Series(worst.year).value_counts().sort_index().items()) + ".")
    say()
    # the same with the delta hedge as positions of its own
    # Each name's delta on ITS latest day: the files do not all end on one date,
    # and a name missing on the last day must still be hedged.
    legs = {}
    for nm in D.columns:
        mine = D[nm].dropna()
        if mine.empty:
            continue
        last, d = mine.index[-1], float(mine.iloc[-1])
        b = {"index": 1.0} if nm == "SXF" else rolling[nm].loc[:last].dropna().iloc[-1].to_dict()
        for h, ratio in b.items():
            # X holds the hedges' own returns: the index and the two sector funds
            legs[h] = legs.get(h, 0.0) - d * ratio * X[h].reindex(scen.index).fillna(0.0)
    hedge_cols = pd.DataFrame({f"hedge: short {h}": v_ for h, v_ in legs.items()})
    both = pd.concat([pnl, hedge_cols], axis=1)
    say("4. THE SAME, WITH THE DELTA HEDGE HELD AS POSITIONS OF ITS OWN")
    say("   Hedge sized from each name's delta and ratios on its latest day. Book and hedge: 97.5% "
        f"expected shortfall {es(both.sum(axis=1), 0.975):,.0f}, against "
        f"{es(total, 0.975):,.0f} without.")
    say(f"   St. dev. over the scenarios: {total.std():,.0f} without the hedge, "
        f"{both.sum(axis=1).std():,.0f} with it.")
    c2 = pd.DataFrame({"component ES": component_es(both, 0.975)})
    c2["share %"] = 100 * c2["component ES"] / c2["component ES"].sum()
    c2 = c2.sort_values("component ES", ascending=False)
    say(money(pd.concat([c2.head(6), c2.tail(4)])))
    say("   (the six largest and the four smallest). A hedge leg's sign says which way it")
    say("   was pulling on the hedged book's worst days, which are no longer the days the")
    say("   index fell: they are the days single names moved against the book.")

    out_file = ROOT / "reports" / f"{path.stem}_tail.txt"
    out_file.write_text("Derived statistics only: dollars on a made-up book, dates, counts and test\n"
                        "statistics. Sections 3 and 4 are model prices on a rule-based vol. No\n"
                        "price or quote appears in this file.\n\n" + "\n".join(lines) + "\n",
                        encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.6), gridspec_kw={"width_ratios": [3, 2]})
    a1.bar(bt.index, bt["actual"], color="#999999", width=0.9, label="actual P&L on the marks")
    a1.plot(bt.index, -bt["var"], color="#d62728", lw=1.2, label="minus the 99% VaR set the evening before")
    ex = bt[bt["exception"]]
    a1.plot(ex.index, ex["actual"], "v", color="#d62728", ms=8, label=f"exceptions: {x}")
    a1.axhline(0, color="black", lw=0.5)
    a1.set_ylabel("dollars a day")
    a1.set_title(f"{path.stem}: the VaR against what happened ({zone} zone, "
                 f"Kupiec p {pval:.2f})", fontsize=10)
    a1.legend(fontsize=8, frameon=False)
    fig.autofmt_xdate()
    top = comp["component ES"]
    a2.barh([c[:22] for c in top.index[::-1]], top[::-1],
            color=["#d62728" if v_ > 0 else "#2ca02c" for v_ in top[::-1]])
    a2.axvline(0, color="black", lw=0.5)
    a2.tick_params(axis="y", labelsize=7)
    a2.set_xlabel("component of the book's 97.5% ES, dollars")
    a2.set_title("which position drives the tail\n(model prices; green = pays on the bad days)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(ROOT / "reports" / f"{path.stem}_tail.png", dpi=130)
    print(f"\nsaved: {out_file.relative_to(ROOT)}, reports/{path.stem}_tail.png")


if __name__ == "__main__":
    main()
