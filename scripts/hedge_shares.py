"""
hedge_shares.py — what the index and sector hedges remove, share by share
(build sheet C4, first layer; docs/work-list.md section 3 step 2).

    .venv/Scripts/python scripts/wrds_prices.py 2014-01-01
    .venv/Scripts/python scripts/hedge_shares.py

This is the delta layer of the closing chart. Each name is held as SHARES,
with no options yet, and hedged three ways:

  one for one    short the index dollar for dollar. No estimation at all.
  index          short the index by a ratio fitted on the 250 days BEFORE
  index+sector   the same with the sector fund added (ZEB for financials,
                 XEG for energy), both ratios fitted together

For each it reports the share of daily variance removed, out of sample, with
the hindsight figure (one ratio fitted on every day) beside it so the cost of
honesty is visible. Then what is left: its size, whether it has a pattern,
and whether the hedge NOT used would have helped.

What this is not. It holds shares, so it says nothing about gamma or vega,
which is most of what is left on an options book. And the TSX 60 index
stands in for the SXF future, whose history is not in this data: a future's
daily return is the index's plus a small carry term.

Prints DERIVED numbers only, saves a chart and a numbers file under reports/.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from hedge import (autocorrelation, by_regime, compounded, expected_shortfall,
                   full_sample_ratio, hedge_residual, ljung_box, rolling_ratio,
                   variance_removed, variance_removed_error)
from loaders.compustat import load_index_returns, load_total_returns

WRDS = ROOT / "data/raw/wrds"
WINDOW = 250
SECTOR = {"RY": "ZEB", "TD": "ZEB", "BNS": "ZEB", "BMO": "ZEB", "CM": "ZEB", "MFC": "ZEB",
          "CNQ": "XEG", "SU": "XEG", "ENB": "XEG",
          "CNR": None, "BCE": None, "ABX": None, "SHOP": None}
BASKETS = {"banks (5, equal)": ["RY", "TD", "BNS", "BMO", "CM"],
           "all 13 (equal)": list(SECTOR)}
STRESS = [("Jan 2015 rate cut", "2015-01-15", "2015-01-30"),
          ("Feb-Mar 2020", "2020-02-20", "2020-03-23"),
          ("Jun-Oct 2022", "2022-06-01", "2022-10-14")]
ANN = np.sqrt(252.0)


def analyse(name: str, y: pd.Series, index: pd.Series, etf: dict[str, pd.Series],
            sector: str | None) -> tuple[dict, pd.Series, pd.Series]:
    X1 = index.to_frame()
    b1 = rolling_ratio(y, X1, WINDOW)
    r1 = hedge_residual(y, X1, b1)
    days = r1.dropna().index
    if sector:
        # Every stage is judged on the SAME days: those on which both the index
        # hedge and the index-plus-sector hedge exist. Otherwise a difference
        # between two stages could be a difference between two samples.
        X2 = pd.concat([index, etf[sector].rename("sector")], axis=1)
        b2 = rolling_ratio(y, X2, WINDOW)
        r2 = hedge_residual(y, X2, b2)
        days = days.intersection(r2.dropna().index)
    yv = y.reindex(days)
    naive = hedge_residual(y, X1, {"index": 1.0}).reindex(days)
    hind1 = hedge_residual(y, X1, full_sample_ratio(yv, X1.reindex(days))).reindex(days)
    row = {"name": name, "days": len(days), "from": days[0].date(),
           "vol %": yv.std() * ANN * 100,
           "1:1": variance_removed(yv, naive),
           "index": variance_removed(yv, r1.reindex(days)),
           "index, hindsight": variance_removed(yv, hind1),
           "ratio": b1["index"].mean(), "ratio sd": b1["index"].std()}
    final = r1.reindex(days)
    if sector:
        hind2 = hedge_residual(y, X2, full_sample_ratio(yv, X2.reindex(days)))
        row["index+sector"] = variance_removed(yv, r2.reindex(days))
        row["index+sector, hindsight"] = variance_removed(yv, hind2.reindex(days))
        row["sector"] = sector
        final = r2.reindex(days)
    f = final.dropna()
    row["left vol %"] = f.std() * ANN * 100
    row["left, $ a day per $1m"] = f.std() * 1e6
    row["autocorr"] = autocorrelation(f)
    # how well the answer is known, where the hedge is weakest, and the tail
    row["removed"] = variance_removed(yv, f)
    row["removed se"] = variance_removed_error(yv, f)
    q, pattern = ljung_box(f, 10)
    row["Ljung-Box Q(10)"], row["pattern"] = q, "yes" if pattern else "no"
    row.update({f"removed, {k}": v for k, v in by_regime(yv, f, index).items()})
    row["worst days, unhedged %"] = expected_shortfall(yv) * 100
    row["worst days, hedged %"] = expected_shortfall(f) * 100
    by_year = b1["index"].reindex(days).groupby(days.year).mean()
    row["ratio, lowest year"], row["ratio, highest year"] = by_year.min(), by_year.max()
    for other in ("ZEB", "XEG"):
        if other != sector:
            row[f"corr with {other}"] = float(f.corr(etf[other].reindex(f.index)))
    return row, y, final


def main() -> None:
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text)
        lines.append(text)

    index = load_index_returns(WRDS / "index_TSX60.csv")
    etf = {t: load_total_returns(WRDS / f"prices_{t}.csv") for t in ("ZEB", "XEG", "XIU")}
    rets = {t: load_total_returns(WRDS / f"prices_{t}.csv") for t in SECTOR}
    say(f"Daily returns {index.index[0].date()} to {index.index[-1].date()}, "
        f"{len(index)} days. Hedge ratios from the {WINDOW} days before each day.")
    say("The TSX 60 index stands in for the SXF future. Shares only: no gamma or vega here.")
    gaps = {t: [str(d) for d in r.attrs.get("dropped_dates", [])]
            for t, r in {"index": index, **etf, **rets}.items() if r.attrs.get("dropped")}
    say("returns that would have covered more than one session (a missing row before "
        f"them), dropped so no two-day move is set beside a one-day move: {gaps or 'none'}")
    big = {t: int((r.abs() > 0.25).sum()) for t, r in rets.items() if (r.abs() > 0.25).any()}
    say(f"daily moves beyond 25% (kept, listed so they can be checked): {big or 'none'}")
    say(f"XIU against the index, as a check on the stand-in: correlation "
        f"{etf['XIU'].corr(index):.4f}")

    rows, series = [], {}
    for name, sector in SECTOR.items():
        row, y, final = analyse(name, rets[name], index, etf, sector)
        rows.append(row)
        series[name] = (y, final)
    for label, members in BASKETS.items():
        y = pd.concat([rets[m] for m in members], axis=1).dropna().mean(axis=1).rename(label)
        sector = "ZEB" if label.startswith("banks") else None
        row, y, final = analyse(label, y, index, etf, sector)
        rows.append(row)
        series[label] = (y, final)
    t = pd.DataFrame(rows).set_index("name")

    say()
    say("Share of daily variance removed, out of sample (hindsight beside it):")
    a = t[["days", "vol %", "1:1", "index", "index, hindsight", "index+sector",
           "index+sector, hindsight", "ratio", "ratio sd"]].copy()
    for c in ("1:1", "index", "index, hindsight", "index+sector", "index+sector, hindsight"):
        a[c] = (a[c] * 100).round(0)
    say(a.round(2).to_string())
    say()
    say("What is left after the best hedge used (annual vol; one day's standard deviation")
    say("on a $1,000,000 holding; day-to-day autocorrelation; correlation with the fund NOT used):")
    say(t[["sector", "left vol %", "left, $ a day per $1m", "autocorr",
           "corr with ZEB", "corr with XEG"]].round(2).fillna("-").to_string())

    say()
    say("How well that is known, where it is weakest, and the tail. 'removed' is the share of")
    say("daily variance taken away by the best hedge used; 'se' is its standard error from")
    say("resampling 20-day blocks; calm and stressed split the days by the index's volatility")
    say("over the 20 days before (top fifth = stressed); 'worst days' is the average loss on")
    say("the worst 1 day in 40 (%); 'pattern' is yes if the Ljung-Box test over ten lags finds")
    say("structure in what is left; the last two columns are the index ratio's lowest and")
    say("highest yearly average.")
    d = t[["removed", "removed se", "removed, calm", "removed, stressed",
           "worst days, unhedged %", "worst days, hedged %", "Ljung-Box Q(10)", "pattern",
           "ratio, lowest year", "ratio, highest year"]].copy()
    for c in ("removed", "removed se", "removed, calm", "removed, stressed"):
        d[c] = (d[c] * 100).round(1)
    say(d.round(2).to_string())

    say()
    say("Stress periods: total return over the period, COMPOUNDED from daily returns, unhedged")
    say("and after the best hedge used (%). NA where the hedge did not exist for the whole")
    say("period (a name with too little history then), never zero. The hedged figure compounds")
    say("daily residuals: it is what a daily re-hedged position earned, before costs.")
    srows = []
    for name, (y, final) in series.items():
        row = {"name": name}
        for label, a_, b_ in STRESS:
            span = index.loc[a_:b_].index
            row[f"{label}: unhedged"] = compounded(y.reindex(span)) * 100
            row[f"{label}: hedged"] = compounded(final.reindex(span)) * 100
        srows.append(row)
    say(pd.DataFrame(srows).set_index("name").round(1).fillna("NA").to_string())

    fig, ax = plt.subplots(figsize=(11, 6.2))
    names = list(t.index)
    ypos = np.arange(len(names))[::-1]
    left_naive = (1 - t["1:1"]) * 100
    left_index = (1 - t["index"]) * 100
    left_both = (1 - t["index+sector"]) * 100
    ax.barh(ypos + 0.27, left_naive, 0.25, color="#c9c9c9", label="index, one for one")
    ax.barh(ypos, left_index, 0.25, color="#7da7cf", label="index, ratio from earlier days")
    ax.barh(ypos - 0.27, left_both.fillna(0), 0.25, color="#1f4e79",
            label="index and sector fund, ratios from earlier days")
    ax.plot((1 - t["index, hindsight"]) * 100, ypos, "|", color="black", ms=9, mew=1.5,
            label="the same with hindsight ratios")
    ax.plot((1 - t["index+sector, hindsight"]) * 100, ypos - 0.27, "|", color="black",
            ms=9, mew=1.5)
    ax.axvline(100, color="black", lw=0.7)
    ax.set_yticks(ypos, names, fontsize=8.5)
    ax.set_xlabel("daily variance LEFT after the hedge, % of unhedged (lower is better)")
    ax.legend(fontsize=8, loc="lower right")
    ax.set_title(
        f"What an index and sector hedge leaves, share by share. Daily returns, "
        f"{t['from'].min()} to {index.index[-1].date()}.\nHedge ratios fitted on the "
        f"{WINDOW} days before each day. Shares only (no gamma or vega). TSX 60 index "
        "in place of the SXF future.", fontsize=9.5)
    fig.tight_layout()
    chart = ROOT / "reports" / "hedge_shares.png"
    fig.savefig(chart, dpi=130)
    say(f"\nchart: {chart.relative_to(ROOT)}")
    (ROOT / "reports" / "hedge_shares_numbers.txt").write_text(
        "Derived statistics only. No prices appear in this file.\n\n"
        + "\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
