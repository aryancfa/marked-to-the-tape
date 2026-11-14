"""
stress.py — a book under a market move, with the vol rule as a control (C1).

    .venv/Scripts/python scripts/stress.py books/diversified.csv
    .venv/Scripts/python scripts/stress.py books/bank_heavy.csv --rule beta --beta -0.8
    .venv/Scripts/python scripts/stress.py books/diversified.csv --moves -30,-20,-10

The index is moved by each amount. Every name moves by its own beta to the
index (fitted on the last 250 days), and every option is repriced in full
at the moved share price and at the vol the rule gives it.

THE CONTROL. Four rules for what implied vol does when the share moves
(src/volrule.py): sticky strike, sticky moneyness, sticky delta, and beta,
where at-the-money vol moves by --beta vol points per 1% share move and the
skew steepens by --steepen beyond a fall of --crash. With no --rule the
report shows all four side by side, which is the point: the same book and
the same move give different losses, and only one of the four lets vol rise
in a fall. With --rule it shows that rule alone, by name.

The report also prints the beta the six months on file actually show, so the
number typed into --beta can be set against data. Six calm months hold no
crash, so that figure is a floor on the size of the response, not a forecast.

Every dollar figure is a MODEL price on a rule-based vol (hard rule 6), on a
made-up book. Nothing here is a mark.
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
from book_report import cached_days
from scenarios import (CRASH_SHARE, crash_betas, index_betas, reprice, reprice_horizon,
                       returns_table)
from volrule import BETA, RULES, STICKY_STRIKE, fitted_beta

ATM_BAND = 0.03


def observed_beta(name: str) -> tuple[float, float, int]:
    """At-the-money vol points per 1% share move on the days on file, for
    the longest-lived expiry the name has."""
    days = cached_days(name)
    order = sorted(days)
    counts: dict = {}
    for d in order:
        for e in {x.date() if hasattr(x, "date") else x for x in days[d]["inverted"]["expiry"]}:
            counts[e] = counts.get(e, 0) + 1
    expiry = max(counts, key=lambda e: (counts[e], e))
    spot, vol = [], []
    for d in order:
        r = days[d]
        inv = r["inverted"]
        inv = inv[inv["iv"].notna()]
        e = np.array([(x.date() if hasattr(x, "date") else x) == expiry for x in inv["expiry"]])
        near = inv[e & ((inv["strike"] / r["spot"] - 1.0).abs() <= ATM_BAND).to_numpy()]
        spot.append(r["spot"])
        vol.append(float(near["iv"].mean()) * 100 if len(near) >= 2 else np.nan)
    s, v = pd.Series(spot), pd.Series(vol)
    return fitted_beta(s.pct_change(), v.diff())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("book")
    ap.add_argument("--rule", choices=RULES, help="show one rule, by name; default all four")
    ap.add_argument("--beta", type=float, default=-0.5,
                    help="vol points of at-the-money vol per 1%% share move (default -0.5)")
    ap.add_argument("--crash", type=float, default=-0.10,
                    help="the fall beyond which the skew steepens (default -0.10)")
    ap.add_argument("--steepen", type=float, default=0.5)
    ap.add_argument("--moves", default="-20,-10,-5,5,10",
                    help="index moves in percent, comma-separated")
    ap.add_argument("--horizon", type=int, default=5,
                    help="trading sessions the move takes, for the carry table (default 5; "
                         "0 leaves the table out)")
    ap.add_argument("--betas", choices=("crash", "recent"), default="crash",
                    help="for index FALLS: betas from the index's worst days since 2014 "
                         "(default), or the last 250 days' as for rises")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    path = Path(a.book)
    moves = [float(x) / 100.0 for x in a.moves.split(",")]
    params = dict(beta=a.beta, crash=a.crash, steepen=a.steepen)
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    names: dict[str, list] = {}
    for p in load_book(path):
        names.setdefault(p.underlying, []).append(p)
    returns = returns_table(list(names))
    betas = index_betas(returns)
    crash = crash_betas(returns)["crash_beta"].to_dict()
    crash["SXF"] = 1.0
    fall = crash if a.betas == "crash" else betas

    def beta_for(n: str, m: float) -> float:
        return fall[n] if m < 0 else betas[n]

    rules = [a.rule] if a.rule else list(RULES)
    tasks = [(n, ps, [beta_for(n, m) * m for m in moves], rule, params)
             for rule in rules for n, ps in names.items()]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        res = list(pool.map(reprice, tasks))
    by_rule = {rule: res[i * len(names):(i + 1) * len(names)] for i, rule in enumerate(rules)}

    def total(rule):
        t = pd.DataFrame({k: v for x in by_rule[rule] for k, v in x["pnl"].items()})
        return t.fillna(0.0)

    say(f"Stress test of {path.name}, a made-up book. MODEL prices on a rule-based vol:")
    say("nothing below is a mark. An instant move, with no day's decay.")
    days = sorted({x["day"] for x in res})
    say(f"Valued on each name's latest day on file ({days[0]:%d %b} to {days[-1]:%d %b %Y}).")
    if a.betas == "crash":
        say("The index moves; each name moves by its beta to it. In a FALL the beta is how")
        say(f"the name moved on the index's worst {100 * CRASH_SHARE:.0f}% of days since 2014 "
            "(scripts/scenarios.crash_betas); in a")
        say("rise, its beta over the last 250 days. A calm year's beta understates how names")
        say("fall together: CNQ's and Suncor's were negative, which moved them UP in a crash.")
    else:
        say("The index moves; each name moves by its beta to it over the last 250 days, in a")
        say("rise and in a fall alike (--betas recent).")
    shown = pd.DataFrame({"fall": [fall[n] for n in names], "rise": [betas[n] for n in names]},
                         index=list(names))
    shown.index.name = "beta in a"
    say(shown.drop(index=[n for n in ("SXF",) if n in shown.index]).T.round(2).to_string()
        .replace("\n", "\n   ").join(["   ", ""]))
    for x in res[:len(names)]:
        for note in x["notes"]:
            say("NOTE: " + note)
    say()
    say("1. THE BOOK'S P&L BY INDEX MOVE AND VOL RULE, dollars")
    table = pd.DataFrame({rule: total(rule).sum(axis=1).to_numpy() for rule in rules},
                         index=[f"{100 * m:+.0f}%" for m in moves])
    table.index.name = "index move"
    say(table.to_string(float_format=lambda x: f"{x:,.0f}").replace("\n", "\n   ")
        .join(["   ", ""]))
    if len(rules) > 1:
        worst = min(moves)
        row = table.loc[f"{100 * worst:+.0f}%"]
        say(f"   At {100 * worst:+.0f}% the rules differ by "
            f"{row.max() - row.min():,.0f}: from {row.idxmin()} ({row.min():,.0f}) to "
            f"{row.idxmax()} ({row.max():,.0f}).")
        say(f"   The beta rule here: {a.beta:+.2f} vol points per 1% move, the skew "
            f"{100 * a.steepen:.0f}% steeper beyond a {100 * a.crash:.0f}% fall. Change them "
            "with --beta, --crash, --steepen.")
        say("   sticky strike, moneyness and delta leave at-the-money vol where the old smile")
        say("   puts it. Only beta lets it rise in a fall, which is what a fall does.")
    say()
    show = a.rule or BETA
    if a.horizon > 0:
        htasks = [(n, ps, [beta_for(n, m) * m for m in moves], show, params, a.horizon)
                  for n, ps in names.items()]
        with ProcessPoolExecutor(max_workers=a.workers) as pool:
            hres = list(pool.map(reprice_horizon, htasks))
        parts = [p for x in hres for p in x["parts"].values()]
        carry = {k: sum(p[k] for p in parts) for k in ("decay", "funding", "dividends")}
        shock = np.sum([p["shock"] for p in parts], axis=0)
        h = pd.DataFrame({"decay": carry["decay"], "funding": carry["funding"],
                          "dividends": carry["dividends"], "the move": shock}, index=table.index)
        h["total"] = h.sum(axis=1)
        h["instant"] = table[show].to_numpy()
        say(f"1b. THE SAME MOVES TAKING {a.horizon} TRADING SESSIONS, under the {show} rule, "
            "dollars")
        say(h.to_string(float_format=lambda x: f"{x:,.0f}").replace("\n", "\n   ")
            .join(["   ", ""]))
        say(f"   Each option is priced at the end of the {a.horizon} sessions (B6, "
            "src/horizon.py). decay = the days passing")
        say("   at a zero rate; funding = what the rate curve adds; dividends = ex-dates inside")
        say("   the window; the move = the shock on the share price expected at the end, which")
        say("   is today's less those dividends. The four add up to 'total'. 'instant' is table")
        say(f"   1's {show} column, the same move this second. Change the window with --horizon.")
        for x in hres:
            for note in x["notes"]:
                say("   NOTE: " + note)
        say()
    t = total(show)
    t.index = table.index
    say(f"2. BY NAME, under the {show} rule, dollars")
    by_name = t.T.groupby(lambda c: c.split()[0]).sum().T
    say(by_name.to_string(float_format=lambda x: f"{x:,.0f}").replace("\n", "\n   ")
        .join(["   ", ""]))
    say()
    say("3. WHAT THE DAYS ON FILE SHOW: at-the-money vol points per 1% share move")
    obs = []
    for n in names:
        if n in ("SXF",) or not any(p.kind == "option" for p in names[n]):
            continue
        b, se, k = observed_beta(n)
        obs.append(dict(name=n, beta=b, std_error=se, days=k))
    o = pd.DataFrame(obs).set_index("name")
    say(o.round(2).to_string().replace("\n", "\n   ").join(["   ", ""]))
    say(f"   Median {o['beta'].median():+.2f}. These are six months with no crash in them, so")
    say("   they measure the response to ordinary days. In a sell-off the response is larger;")
    say("   that is why the rule is a control and not a fitted number.")

    out = ROOT / "reports" / f"{path.stem}_stress.txt"
    out.write_text("Model prices on a rule-based vol, on a made-up book. Dollars summed over\n"
                   "positions, and ratios. No price or quote appears in this file.\n\n"
                   + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nsaved: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
