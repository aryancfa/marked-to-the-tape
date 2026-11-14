"""
risk_grid.py — where a book's vega sits, and its delta in index terms (C2).

    .venv/Scripts/python scripts/risk_grid.py books/bank_heavy.csv

On each name's latest day on file, every position is valued at the exchange's
mark (src/replay.py) and its Greeks are read off the tree. Then:

  1. Vega on a grid of days to expiry by strike over share price (the front
     chart's own cells), by name. Vega in a cell where the exchange quotes no
     smile is reported apart, and a name with no expiry quoted widely enough
     for a smile is marked delta-only (src/riskgrid.py says exactly when).
  2. Each name's dollar delta, its beta to the TSX 60 over the 250 days on
     file, and the delta in index dollars: what an index future would offset,
     and what it would leave.

Writes reports/<book>_grid.txt and reports/<book>_grid.png. Derived numbers
only: dollars of Greeks summed over positions of a made-up book, and betas.
A share, fund or future position's dollar delta is its size times its price,
so those positions appear only inside a book or name total, never alone
(hard rule 1).
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from book import load_book
from loaders.mx import load_mx_futures
from replay import DayInputs, maturity_bucket, value_position
from riskgrid import (cell_order, is_populated, mapped_delta, moneyness_cell, name_has_surface,
                      off_market, surface_strikes, vega_grid)
from run_days import mx_files
from scenarios import INDEX, index_betas, label, latest_inputs, returns_table

MATURITIES = ["1-30d", "31-90d", "91-180d", "181-400d", "over 400d"]
WINDOW = 250                 # days behind each beta, as in scenarios.index_betas
EXPLAINED = 0.5              # below this correlation the index does not explain a name


def one_name(task) -> dict:
    """Runs in a worker: one underlying's positions on its latest day."""
    name, positions = task
    if name == "SXF":
        fut = load_mx_futures(mx_files("SXF"))
        rows = []
        for pos in positions:
            f = fut[fut["expiry"].dt.date == pos.expiry].sort_values("date")
            if f.empty:
                raise SystemExit(f"SXF {pos.expiry}: no such contract in the exchange files")
            day, level = f["date"].iloc[-1].date(), float(f["settle"].iloc[-1])
            v = value_position(pos, DayInputs(day, level, lambda T: 0.0, [], None))
            rows.append(dict(name=name, kind=pos.kind, label=label(pos), day=day, ok=v.ok,
                             delta=v.dollars("delta") * level, vega=0.0))
        return dict(name=name, rows=rows, surface=None, day=rows[0]["day"])
    day, inp, inv = latest_inputs(name)
    by_expiry = surface_strikes(inv)
    has_surface = name_has_surface(by_expiry)
    rows = []
    for pos in positions:
        v = value_position(pos, inp)
        row = dict(name=name, kind=pos.kind, label=label(pos), day=day, ok=v.ok,
                   reason=v.reason)
        if not v.ok:
            rows.append(row)
            continue
        row["delta"] = v.dollars("delta") * inp.spot
        if pos.kind == "option":
            row.update(vega=v.dollars("vega"), maturity=maturity_bucket(day, pos.expiry),
                       moneyness=moneyness_cell(pos.strike, inp.spot),
                       populated=is_populated(by_expiry.get(pos.expiry), pos.strike, inp.spot),
                       strikes=len(by_expiry.get(pos.expiry, ())),
                       delta_only=not has_surface)
        rows.append(row)
    return dict(name=name, rows=rows, surface=has_surface, day=day)


def table(t: pd.DataFrame, fmt="{:,.0f}") -> str:
    return (t.to_string(float_format=lambda x: fmt.format(x)).replace("\n", "\n   ")
            .join(["   ", ""]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("book")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    path = Path(a.book)
    names: dict[str, list] = {}
    for p in load_book(path):
        names.setdefault(p.underlying, []).append(p)
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        res = list(pool.map(one_name, list(names.items())))
    rows = pd.DataFrame([r for x in res for r in x["rows"]])
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    days = sorted({x["day"] for x in res})
    say(f"Risk grid for {path.name}, a made-up book, on each name's latest day "
        f"({days[0]:%d %b} to {days[-1]:%d %b %Y}).")
    bad = rows[~rows["ok"]]
    if len(bad):
        say(f"NOT VALUED, left out of every figure below: "
            + "; ".join(f"{r.label} ({r.reason})" for r in bad.itertuples()))
    say()

    # ---- 1. vega -----------------------------------------------------------
    opt = rows[(rows["kind"] == "option") & rows["ok"]].copy()
    g = vega_grid(opt[["name", "maturity", "moneyness", "vega", "populated", "delta_only"]]
                  .to_dict("records"))
    say("1. WHERE THE VEGA SITS: dollars per vol point, by days to expiry and strike over")
    say("   share price (the front chart's cells)")
    only = [x["name"] for x in res if x["surface"] is False]
    say(f"   names with no expiry quoted widely enough for a smile (delta-only): "
        f"{', '.join(only) if only else 'none'}")
    grid = (g.pivot_table(index="maturity", columns="moneyness", values="vega", aggfunc="sum")
            .reindex(index=[m for m in MATURITIES if m in set(g["maturity"])],
                     columns=[c for c in cell_order() if c in set(g["moneyness"])]))
    say("   the book, all names added (see the caution below):")
    say(table(grid.fillna(0.0)))
    per = g.groupby(["name", "maturity", "moneyness"], sort=False)["vega"].sum()
    per = per[per != 0].rename("vega").to_frame()
    say("   by name:")
    say(table(per))
    off = opt[off_market(opt)]
    total = opt["vega"].sum()
    say(f"   vega with no market behind it: {off['vega'].sum():,.0f} of {total:,.0f} "
        f"({100 * off['vega'].abs().sum() / max(opt['vega'].abs().sum(), 1e-9):.0f}% of "
        "gross vega)")
    for r in off.itertuples():
        why = ("name delta-only" if r.delta_only else
               f"its expiry has {r.strikes} strikes with a vol" if r.strikes < 7 else
               "strike outside the quoted range or no quoted strike in its cell")
        say(f"     {r.label}: {r.vega:,.0f} ({why})")
    gross = opt.groupby("name")["vega"].sum()
    say(f"   Netting: the book's vega adds to {total:,.0f}; added name by name without "
        f"netting it is {gross.abs().sum():,.0f}.")
    say("   Caution: a vol point on one name is not a vol point on another, and a one-month")
    say("   vol point moves more than a six-month one. The book grid adds them dollar for")
    say("   dollar; the by-name rows are the ones to act on.")
    say()

    # ---- 2. delta mapped to the index -------------------------------------
    # OPTIONS ONLY. A share, fund or future position's dollar delta is its size
    # times its price, and adding it into a name's row would let anyone take
    # the book report's option-only figure away from it and divide by the
    # share count (hard rule 1). Even a book total with and without them,
    # beside the index-mapped total with its known betas, solves for two
    # prices. The hedge positions are the closing chart's subject.
    deltas = opt.groupby("name")["delta"].sum()
    returns = returns_table(list(deltas.index))
    betas = index_betas(returns, WINDOW)
    m = mapped_delta(deltas.to_dict(), betas)
    recent = returns.dropna().tail(WINDOW)
    m["correlation"] = [1.0 if n == "SXF" else float(recent[n].corr(recent[INDEX]))
                        for n in m.index]
    say("2. THE OPTIONS' DELTA, BY NAME AND IN INDEX TERMS: dollars of share, and of TSX 60")
    say(f"   at each name's beta over the last {WINDOW} days on file")
    hedges = sorted(set(rows.loc[rows["kind"] != "option", "name"]))
    show = m[["delta", "beta", "correlation", "index_delta"]]
    show.columns = ["dollar delta", "beta", "correlation", "index dollars"]
    shown = show.copy().astype(object)
    for c in ("dollar delta", "index dollars"):
        shown[c] = [f"{x:,.0f}" for x in show[c]]
    for c in ("beta", "correlation"):
        shown[c] = [f"{x:+.2f}" for x in show[c]]
    say(table(shown))
    say(f"   the options: {m['delta'].sum():,.0f} of share delta, "
        f"{m['index_delta'].sum():,.0f} in index terms.")
    if hedges:
        say(f"   Left out: the book's share, fund and future positions ({', '.join(hedges)}).")
        say("   Their dollar delta is their size times their price, so printing it would")
        say("   publish a price. Their effect is in the closing chart, summed with everything.")
    left = (m["delta"] - m["index_delta"])
    say("   An index future sized to the index figure offsets the market's move. What it")
    say(f"   leaves is each name's own move: dollar delta not carried by the index, gross "
        f"{left.abs().sum():,.0f}.")
    weak = m[m["correlation"].abs() < EXPLAINED]
    if len(weak):
        say(f"   THE INDEX DOES NOT EXPLAIN {', '.join(weak.index)} (correlation under "
            f"{EXPLAINED:.1f} over these {WINDOW} days).")
        say("   For those names the beta is close to noise, so their index figure is too: an")
        say("   index future neither hedges nor offsets their delta. Their own sector fund or")
        say("   their own shares do. A beta fitted on a calm year is also not a crash beta:")
        say("   in a sell-off correlations tend to rise toward one.")

    out = ROOT / "reports" / f"{path.stem}_grid.txt"
    out.write_text("Derived statistics only: dollars of Greeks summed over a made-up book, "
                   "and betas.\nNo price or quote appears in this file.\n\n"
                   + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nsaved: {out.relative_to(ROOT)}")
    draw(per.reset_index(), off, path.stem)


def draw(per: pd.DataFrame, off: pd.DataFrame, stem: str) -> None:
    """One panel per name: vega by days to expiry and moneyness cell."""
    names = list(dict.fromkeys(per["name"]))
    if not names:
        return
    cols = [c for c in cell_order() if c in set(per["moneyness"])]
    mats = [m for m in MATURITIES if m in set(per["maturity"])]
    lim = max(per["vega"].abs().max(), 1.0)
    across = min(len(names), 6)
    down = -(-len(names) // across)
    fig, axes = plt.subplots(down, across, figsize=(2.3 * across + 0.6, 2.6 * down + 0.5),
                             squeeze=False)
    for ax in axes.flat[len(names):]:
        ax.axis("off")
    for ax, n in zip(axes.flat, names):
        sub = per[per["name"] == n].pivot_table(index="maturity", columns="moneyness",
                                                values="vega", aggfunc="sum")
        sub = sub.reindex(index=mats, columns=cols)
        ax.imshow(sub.to_numpy(dtype=float), cmap="RdBu", vmin=-lim, vmax=lim, aspect="auto")
        for (i, j), x in np.ndenumerate(sub.to_numpy(dtype=float)):
            if np.isfinite(x):
                ax.text(j, i, f"{x:,.0f}", ha="center", va="center", fontsize=8,
                        color="white" if abs(x) > 0.6 * lim else "black")
        ax.set_title(n, fontsize=9)
        ax.set_xticks(range(len(cols)), cols, rotation=60, fontsize=7)
        ax.set_yticks(range(len(mats)), mats, fontsize=7)
    fig.suptitle("Vega by name, days to expiry and strike over share price "
                 "($ per vol point; model Greeks at the exchange's marks)", fontsize=9)
    note = (f"Not shown, no market in the cell: {len(off)} position(s)" if len(off)
            else "Every option sits in a cell the exchange quotes")
    fig.text(0.01, 0.01, note, fontsize=7)
    fig.tight_layout(rect=(0, 0.04, 1, 0.93))
    p = ROOT / "reports" / f"{stem}_grid.png"
    fig.savefig(p, dpi=130)
    print(f"chart: {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
