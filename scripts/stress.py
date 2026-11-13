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
from scenarios import index_betas, reprice, returns_table
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
    betas = index_betas(returns_table(list(names)))
    rules = [a.rule] if a.rule else list(RULES)
    tasks = [(n, ps, [betas[n] * m for m in moves], rule, params)
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
    say(f"The index moves; each name moves by its beta to it ({min(betas.values()):.2f} to "
        f"{max(betas.values()):.2f} here, fitted on the last 250 days).")
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
