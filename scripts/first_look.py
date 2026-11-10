"""
first_look.py — one day of one chain: call minus put implied vol by strike.

The first real result (build sheet B5, first look). Reads the local data
snapshot, prints DERIVED numbers only (vol points, basis points, counts) and
saves one chart under reports/. All the calculation is in src/surface.py;
this file only reads inputs, prints and draws.

    .venv/Scripts/python scripts/first_look.py RY 2026-11-02
    .venv/Scripts/python scripts/first_look.py RY 2026-11-02 flat

The optional third word chooses the rate: "curve" (default) reads a rate for
each expiry off the Bank of Canada curve; "flat" uses overnight CORRA for
every expiry and exists only to show what the curve changes.

Inputs and where each comes from:
  options      data/raw/mx/            Montreal Exchange daily files (manual)
  rate         data/raw/boc/           Bank of Canada CORRA and T-bill yields
  dividends    data/reference/         ex-dates and cash, declared or projected
  share price  implied from the nearest monthly expiry's call-put pairs
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from loaders.mx import load_mx_options, load_mx_underlying
from loaders.reference import first_projected, load_dividends
from rates import flat_curve, load_curve
from surface import analyse_day, rejection_tally, vs_exchange


def main(underlying: str, day: date, rate_mode: str = "curve") -> None:
    opts = load_mx_options(ROOT / "data/raw/mx")
    und = load_mx_underlying(ROOT / "data/raw/mx")
    o = opts[(opts["underlying"] == underlying) & (opts["date"] == pd.Timestamp(day))]
    u = und[(und["underlying"] == underlying) & (und["date"] == pd.Timestamp(day))]
    if o.empty or u.empty:
        raise SystemExit(f"no {underlying} rows on {day}")
    file_mid, file_last = float(u["mid"].iloc[0]), float(u["last"].iloc[0])

    curve = load_curve(ROOT / "data/raw/boc", day)
    if rate_mode == "flat":
        curve = flat_curve(day, curve.rate(0.0))
    elif rate_mode != "curve":
        raise SystemExit('rate mode must be "curve" or "flat"')
    schedule = load_dividends(ROOT / "data/reference" / f"{underlying.lower()}_dividends.csv")
    divs = [(d, c) for d, c, _ in schedule]

    res = analyse_day(o, file_mid, curve, divs, first_projected(schedule, day))
    print(f"{underlying} {day}   rate mode: {rate_mode}   front expiry {res.front}, "
          f"{res.pairs_used} pairs")
    print("rates by tenor (continuous, %): "
          + ", ".join(f"{t*365:.0f}d {x*100:.2f}" for t, x in curve.points))
    if curve.stale:
        print("rate points carried from an earlier day:", curve.stale)
    if res.spot is None:
        raise SystemExit(f"share price not implied: {res.spot_reason}")
    spot, inv, gap, summary = res.spot, res.inverted, res.gap, res.summary
    print(f"implied share price vs file midpoint: {(spot/file_mid-1)*1e4:+.1f} bp;"
          f"  vs file last: {(spot/file_last-1)*1e4:+.1f} bp")
    print(f"\nrows inverted: {len(inv)}  by tier: {inv['tier'].value_counts().to_dict()}")
    print("rejection tally:", rejection_tally(inv).to_dict())
    print("\ncall minus put implied vol, in vol points, by expiry")
    print("(std_error = scatter across strikes / sqrt(pairs); a lower bound):")
    print(summary.round(2).to_string())
    v = vs_exchange(inv)
    print("\nour vol minus the exchange's (vol points): "
          f"calls mean {v['calls_mean']:+.2f} (n={v['calls_n']}), "
          f"puts mean {v['puts_mean']:+.2f} (n={v['puts_n']})")
    print(f"median half-spread of a quote: {inv['half_spread_vol'].median()*100:.2f} vol points")

    # chart: the points, and a band for how well each expiry's mean is known
    gap = gap.assign(expiry=gap["expiry"].dt.date)
    expiries = list(summary.index)
    cols = min(3, len(expiries))
    nrows = int(np.ceil(len(expiries) / cols))
    fig, axes = plt.subplots(nrows, cols, figsize=(4.6 * cols, 3.2 * nrows),
                             squeeze=False, sharey=True)
    for ax, e in zip(axes.ravel(), expiries):
        g, s = gap[gap["expiry"] == e], summary.loc[e]
        if np.isfinite(s["std_error"]):
            ax.axhspan(s["mean_gap"] - 2 * s["std_error"],
                       s["mean_gap"] + 2 * s["std_error"], color="#9db7d1", alpha=0.5)
            label = f"mean {s['mean_gap']:+.2f} ± {s['std_error']:.2f}"
        else:
            label = f"mean {s['mean_gap']:+.2f}"
        ax.plot(g["strike"] / spot, g["gap"], "o", ms=3.5, color="#1f4e79")
        ax.axhline(0.0, color="black", lw=0.7)
        ax.set_title(f"{e}  ({s['days']}d, {s['carry']})\n{label}; "
                     f"quote bar ±{s['median_quote_bar']:.1f}", fontsize=8.5)
        ax.set_xlabel("strike / share price", fontsize=8)
        ax.tick_params(labelsize=8)
    for ax in axes[:, 0]:
        ax.set_ylabel("call IV minus put IV (vol points)", fontsize=8)
    for ax in axes.ravel()[len(expiries):]:
        ax.set_visible(False)
    fig.suptitle(
        f"{underlying} {day}: call minus put implied vol by strike. Band = mean ± 2 "
        "standard errors (scatter across strikes).\nMarks are quote midpoints. "
        "Quote bar = the two quotes' half-spreads added, not drawn. Share price "
        "implied from the calibrated expiry.\nRate: "
        + ("overnight CORRA for every expiry" if rate_mode == "flat"
           else "Bank of Canada curve by expiry, flat beyond one year")
        + "; dividends after the last declared one are projected.", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    tag = "" if rate_mode == "curve" else f"_{rate_mode}"
    out = ROOT / "reports" / f"first_look_{underlying.lower()}_cp_gap_{day:%Y%m%d}{tag}.png"
    fig.savefig(out, dpi=130)
    print(f"\nchart: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1], date.fromisoformat(sys.argv[2]), *sys.argv[3:4])
