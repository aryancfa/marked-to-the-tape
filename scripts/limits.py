"""
limits.py — the desk's P&L explain and its limits, with usage (C8).

    .venv/Scripts/python scripts/limits.py books/bank_heavy.csv

1. P&L EXPLAIN. Each day's change in the marks, split by the previous
   evening's Greeks: delta, gamma, vega, theta, rho, and the RESIDUAL they
   leave (replay.greek_explain). This is what a desk's risk system shows
   each morning. The repricing split in book_report.py sums exactly by
   construction; this one does not, and the residual is the point: a large
   one says the Greeks, the marks or the model missed something that day.
   A day is FLAGGED when the residual is over RESIDUAL_SHARE of the day's
   change and over RESIDUAL_FLOOR dollars.

2. LIMITS AND USAGE on mapped delta, vega by maturity bucket, and one-day
   97.5% ES, each made from one stated loss tolerance (src/limits.py), with
   usage on every day the whole book was valued and bars for the latest.

Mark history: the Montreal files on disk (May to November 2026), not yet the
Interactive Brokers capture, which has not run. Everything written is
dollars summed over a made-up book, limits, usage and counts: no price, and
no share, fund or future position's exposure on its own.
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
from hedge import rolling_ratio
from limits import (AMBER, ES_LEVEL, LEVEL, LOSS, RED, band, greek_es, limit_from,
                    mapped_delta, move_size, usage, vol_changes_by_bucket)
from loaders.compustat import load_index_returns, load_total_returns
from replay import GREEK_TERMS, maturity_bucket
from scenarios import INDEX, returns_table

WRDS = ROOT / "data/raw/wrds"
WINDOW = 250
VOL_BETA = -0.5                 # the C1 rule's vol points per 1% move, as in the VaR backtest
RESIDUAL_SHARE = 0.20           # a residual over 20% of the day's change ...
RESIDUAL_FLOOR = 1_000.0        # ... and over $1,000 flags the day


def money(x: float) -> str:
    return f"{x:,.0f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("book")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    path = Path(a.book)
    names: dict[str, list] = {}
    for p in load_book(path):
        names.setdefault(p.underlying, []).append(p)
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        res = list(pool.map(bh.one_name, [(n, ps, None) for n, ps in names.items()]))
    rows = pd.DataFrame([r for x in res for r in x["rows"]])
    rows["day"] = pd.to_datetime(rows["day"])
    atm = pd.DataFrame([r for x in res for r in x["atm"]])
    g_cols = [f"g_{k}" for k in GREEK_TERMS]
    daily = rows.groupby("day")[[*g_cols, "total", "dividends", "theta_days"]].sum()
    daily.columns = [c[2:] if c.startswith("g_") else c for c in daily.columns]
    D = rows.pivot_table(index="day", columns="name", values="D0", aggfunc="sum")
    whole = D.notna().all(axis=1)            # days every underlying in the book was valued

    say(f"P&L explain and limits for {path.name}, a made-up book: "
        f"{daily.index.min():%d %b} to {daily.index.max():%d %b %Y}, {len(daily)} days.")
    say("Mark history: the Montreal files on disk (the Interactive Brokers capture has not run).")
    say()

    # ---- 1. P&L explain ---------------------------------------------------
    say("1. P&L EXPLAIN: each day's change in the marks through the evening's Greeks, dollars")
    t = daily[list(GREEK_TERMS)]
    tot = daily["total"]
    tab = pd.DataFrame({"sum": t.sum(), "daily st. dev.": t.std(),
                        "share of the day's variance %": [100 * t[c].cov(tot) / tot.var()
                                                          for c in t]})
    tab.loc["total"] = [tot.sum(), tot.std(), 100.0]
    say(tab.to_string(float_format=money).replace("\n", "\n   ").join(["   ", ""]))
    res_ = daily["residual"]
    flagged = (res_.abs() > RESIDUAL_SHARE * tot.abs()) & (res_.abs() > RESIDUAL_FLOOR)
    say(f"   The residual's typical size: {res_.abs().median():,.0f} a day (median), against a "
        f"typical day's change of {tot.abs().median():,.0f}.")
    say(f"   Days flagged (residual over {100 * RESIDUAL_SHARE:.0f}% of the day's change and over "
        f"${RESIDUAL_FLOOR:,.0f}): {int(flagged.sum())} of {len(daily)}.")
    # what the residual is made of: the repricing split's dividend news is one
    # thing the Greeks cannot see; a big share move is another
    ex_days = rows[rows["ex"].fillna(False).astype(bool)].groupby("day").size()
    say(f"   Correlation of the residual with the repricing split's dividend news: "
        f"{res_.corr(daily['dividends']):+.2f}.")
    worst = res_.abs().sort_values(ascending=False).head(5).index
    w = pd.DataFrame({"change in marks": tot[worst], "residual": res_[worst],
                      "dividend news (repriced)": daily.loc[worst, "dividends"],
                      "positions going ex": ex_days.reindex(worst).fillna(0).astype(int)})
    w.index = w.index.strftime("%Y-%m-%d")
    say("   The five largest residuals:")
    say(w.to_string(float_format=money).replace("\n", "\n   ").join(["   ", ""]))
    # the first version took theta as the evening's one-day theta times the
    # calendar days passed; over a weekend that counts a dividend's jump once
    # for every day, so its residual is shown beside the one in use
    naive = res_ + daily["theta"] - daily["theta_days"]
    nw = naive.abs().sort_values(ascending=False).head(2).index
    say("   Theta as the one-day theta times calendar days passed (the first version) instead")
    say(f"   of repricing to the next session: typical residual {naive.abs().median():,.0f}; "
        "largest " + ", ".join(f"{naive[d]:+,.0f} on {d:%a %d %b} "
                               f"(positions going ex: {int(ex_days.get(d, 0))})" for d in nw) + ".")
    say("   Read: the Greeks are a second-order local picture. They miss moves large enough")
    say("   for the third order to matter, vol and share moving together (vanna), a dividend")
    say("   whose amount or date changed, and a mark that jumped for reasons of its own. The")
    say("   review queue (scripts/review_queue.py) is where a flagged day's marks are checked.")
    say()

    # ---- 2. limits --------------------------------------------------------
    index = load_index_returns(WRDS / "index_TSX60.csv")
    first = daily.index.min()
    idx_move = move_size(index[index.index < first].tail(WINDOW))
    betas = {}
    for n in D.columns:
        if n == "SXF":
            continue
        y = load_total_returns(WRDS / f"prices_{n}.csv")
        betas[n] = rolling_ratio(y, index.to_frame("index").dropna(), WINDOW)["index"]
    vch = vol_changes_by_bucket(atm, maturity_bucket) if len(atm) else pd.DataFrame(
        columns=["day", "name", "bucket", "change"])
    # one vol move size from every name and bucket together: per bucket there
    # are too few changes, and one name's event (an earnings day) would set a
    # whole bucket's limit
    v_all = move_size(vch["change"])
    v_by = vch.groupby("bucket")["change"].agg(["count", move_size])
    lim = {"mapped delta": limit_from(idx_move), "ES": LOSS, "vega, all buckets": limit_from(v_all)}
    for b in sorted(vch["bucket"].unique(), key=lambda s: int(s.split("-")[0].split()[-1])):
        lim[f"vega {b}"] = limit_from(v_all)

    table = returns_table([n for n in names if n != "SXF"])
    o = rows[rows["kind"] == "option"].copy()
    o["bucket"] = [maturity_bucket(d.date(), e) for d, e in zip(o["day"], o["expiry"])]
    G = rows.pivot_table(index="day", columns="name", values="G0", aggfunc="sum")
    V = rows.pivot_table(index="day", columns="name", values="V0", aggfunc="sum")
    VB = o.pivot_table(index="day", columns="bucket", values="V0", aggfunc="sum")
    hist = []
    for day in daily.index[whole.reindex(daily.index).fillna(False).to_numpy()]:
        b = {n: float(betas[n].get(day, np.nan)) for n in betas}
        b["SXF"] = 1.0
        d_ = {n: float(D.loc[day, n]) for n in D.columns if pd.notna(D.loc[day, n])}
        before = table.loc[table.index < day].tail(WINDOW)
        window = pd.DataFrame({n: before[INDEX if n == "SXF" else n] for n in d_})
        h = {"day": day, "mapped delta": mapped_delta(d_, b),
             "ES": greek_es(d_, {n: float(G.loc[day, n]) for n in d_ if pd.notna(G.loc[day, n])},
                            {n: float(V.loc[day, n]) for n in d_ if pd.notna(V.loc[day, n])},
                            window, VOL_BETA) if len(before) >= WINDOW else np.nan,
             "unmapped names": [n for n in d_ if not np.isfinite(b.get(n, np.nan))]}
        for bk in VB.columns:
            h[f"vega {bk}"] = float(VB.loc[day, bk]) if pd.notna(VB.loc[day, bk]) else 0.0
        h["vega, all buckets"] = float(VB.loc[day].fillna(0.0).sum())
        hist.append(h)
    H = pd.DataFrame(hist).set_index("day")
    U = pd.DataFrame({k: H[k].abs() / lim[k] for k in lim if k in H})

    say("2. LIMITS, from one stated tolerance: the desk accepts losing at most "
        f"${LOSS:,.0f} on a 1-in-100 day.")
    say("   Each limit is the exposure that loses that much on a 1-in-100 day move of its own")
    say("   factor, everything else still. The tolerance is made up, like the book.")
    how = {"mapped delta": f"index dollars; the index's 99th-percentile daily move "
                           f"{100 * idx_move:.2f}%, from the {WINDOW} sessions before {first:%d %b %Y}",
           "ES": f"one-day {100 * ES_LEVEL:.1f}% expected shortfall through delta, gamma "
                 "and vega (not the tail report's ES, repriced in full), dollars; equal to the "
                 "tolerance (for a normal, 97.5% ES = 99% VaR)"}
    vega_how = (f"dollars per vol point, net; the 99th-percentile daily change in the book's "
                f"at-the-money vols, {v_all:.2f} points ({len(vch)} changes, every name and "
                "bucket)")
    for k in lim:
        say(f"   {k:<18} limit {money(lim[k]):>11}   "
            + how.get(k, vega_how if k == "vega, all buckets" else "the same, per bucket"))
    say("   One vol move size for every bucket. Measured bucket by bucket (changes, 99th")
    say("   percentile in points): " + "; ".join(f"{b} {int(r['count'])}, {r['move_size']:.2f}"
                                               for b, r in v_by.iterrows()) + ".")
    big = vch.loc[vch["change"].abs().sort_values(ascending=False).index[:2]]
    say(f"   Too few changes per bucket to tell them apart, and one name's event sets a thin")
    say("   bucket: the two largest single changes are "
        + " and ".join(f"{r['name']} on {r['day']:%d %b %Y}, {r['change']:+.1f} points ({r['bucket']})"
                       for _, r in big.iterrows()) + ".")
    say("   Vol history on disk starts in May 2026, so this move size is measured on the days")
    say("   on file: the one limit set with some hindsight.")
    say()
    last = H.index.max()
    say(f"3. USAGE carried into {last:%d %b %Y}, the latest day the whole book was valued: "
        "the exposure set at the previous evening's close, not that day's close")
    say(f"   (green under {100 * AMBER:.0f}%, amber to {100 * RED:.0f}%, then a breach)")
    for k in lim:
        if k not in H:
            continue
        u = U.loc[last, k]
        bar = "#" * int(min(round(20 * u), 40)) if np.isfinite(u) else ""
        say(f"   {k:<18} {money(H.loc[last, k]):>11} of {money(lim[k]):>9}  "
            f"{100 * u:5.0f}%  {bar:<20} {band(u)}")
    missing = H.loc[last, "unmapped names"]
    if missing:
        say(f"   Not in mapped delta (no beta yet): {', '.join(missing)}")
    say()
    say(f"4. USAGE OVER TIME: {len(H)} days the whole book was valued, each the exposure "
        "carried into the day")
    s = pd.DataFrame({"latest %": 100 * U.iloc[-1], "highest %": 100 * U.max(),
                      "days amber": (U >= AMBER).sum() - (U > RED).sum(),
                      "days in breach": (U > RED).sum()})
    say(s.to_string(float_format=lambda x: f"{x:,.0f}").replace("\n", "\n   ").join(["   ", ""]))
    say("   A vega bucket's usage is its net vega: long and short vega in one bucket offset.")
    say("   That is the convention, and it hides a book long one name's vol and short")
    say("   another's; the C2 grid (reports/<book>_grid.txt) shows them by name.")

    out = ROOT / "reports" / f"{path.stem}_limits.txt"
    out.write_text("Derived statistics only: dollars summed over a made-up book, limits made from\n"
                   "one stated tolerance, usage and counts. No price or quote appears here.\n\n"
                   + "\n".join(lines) + "\n", encoding="utf-8")
    draw(U, path.stem)
    print(f"\nsaved: {out.relative_to(ROOT)}")


def draw(U: pd.DataFrame, stem: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colour = {"green": "#2ca02c", "amber": "#ff9f1c", "breach": "#d62728", "no limit": "#999999"}
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [2, 3]})
    last = U.iloc[-1].dropna()
    ax1.barh(list(last.index)[::-1], (100 * last).to_numpy()[::-1],
             color=[colour[band(u)] for u in last.to_numpy()[::-1]])
    for x in (100 * AMBER, 100 * RED):
        ax1.axvline(x, color="black", lw=0.6, ls=":" if x < 100 else "-")
    ax1.set_xlabel("usage, % of limit")
    ax1.set_title(f"{stem}: usage carried into {U.index[-1]:%d %b %Y}", fontsize=10)
    for k in U.columns:
        ax2.plot(U.index, 100 * U[k], lw=1.1, label=k)
    ax2.axhline(100 * RED, color="black", lw=0.6)
    ax2.axhline(100 * AMBER, color="black", lw=0.6, ls=":")
    ax2.set_ylabel("usage, % of limit")
    ax2.set_title("usage by day (whole-book days)", fontsize=10)
    ax2.legend(fontsize=7, frameon=False, ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(ROOT / "reports" / f"{stem}_limits.png", dpi=130)


if __name__ == "__main__":
    main()
