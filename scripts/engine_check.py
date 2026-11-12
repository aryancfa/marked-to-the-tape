"""
engine_check.py — this project's engine against OptionMetrics' (build sheet B8).

    .venv/Scripts/python scripts/wrds_pull.py RY 109722 2025-06-02 2025-08-29
    .venv/Scripts/python scripts/engine_check.py RY

OptionMetrics publishes an implied vol for every US-listed option from its own
American-option model. The Canadian banks also list in New York. This script
takes OptionMetrics' quotes for that listing, inverts them with THIS engine
from OptionMetrics' own inputs (their closing share price, their zero curve,
their dividend record), and reports our vol minus theirs in vol points.

Using their inputs is the point. Then a difference cannot be a data
difference. It is the two models, or a convention one of them applies.

Three runs, to see which conventions matter:
  A  ours: dividends placed by New York trading sessions at the ex-date's
     open, Canadian-dollar dividends converted to US dollars
  B  the dividend drop at the close of the ex-date by calendar days
  C  as A, but the Canadian-dollar amount taken as if it were US dollars
  D  both changes: not converted, and the drop at the ex-date's close

No funding spread is added in any run: the question here is the engine.

Limits. The dates are June to August 2025; the Montreal files start May 2026,
so this is not a same-day comparison of the two markets. Dividends are the
ones on record, so expiries past the last recorded ex-date are left out.

Prints DERIVED numbers only, saves a chart and a numbers file under reports/.
"""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from loaders.optionm import (load_optionm_curve, load_optionm_dividends,
                             load_optionm_options, load_optionm_prices)
from rates import read_boc_series
from references import EXCHANGE, PAIR, pool_buckets, reference_differences
from surface import invert_chain, mean_std_error, near_the_money, rejection_tally
from tradingcal import NYSE

WRDS = ROOT / "data/raw/wrds"
RUNS = {"A": dict(convert=True, ex_shift=None),
        "B": dict(convert=True, ex_shift=0.0),
        "C": dict(convert=False, ex_shift=None),
        "D": dict(convert=False, ex_shift=0.0)}
LABEL = {"A": "A  ours (sessions, converted)",
         "B": "B  drop at the ex-date's close",
         "C": "C  dividend not converted",
         "D": "D  not converted, drop at close"}
MAX_DAYS = 240


def one(task):
    day, o, spot, zero_path, divs, last_known, ex_shift = task
    curve = load_optionm_curve(zero_path, day)
    near = near_the_money(o, spot, 0.12, 7, MAX_DAYS)
    near = near[near["expiry"] <= pd.Timestamp(last_known)]
    inv = invert_chain(near, spot, curve, divs, ex_shift_days=ex_shift, venue=NYSE)
    d = reference_differences(inv, spot, published="om_iv", divs=divs, venue=NYSE)
    ex_dates = [x for x, _ in divs if x > day]
    nxt = min(ex_dates) if ex_dates else None
    d["after_ex"] = [nxt is not None and e >= nxt for e in d["expiry"]]
    theirs = int(near["om_iv"].notna().sum())
    return dict(date=day, diffs=d, tally=rejection_tally(inv).to_dict(),
                theirs=theirs, ours=int(inv["iv"].notna().sum()),
                both=int((inv["iv"].notna() & inv["om_iv"].notna()).sum()))


def main(name: str) -> None:
    opt_file = sorted(WRDS.glob(f"opprcd_{name}_*.csv"))
    if not opt_file:
        raise SystemExit(f"no OptionMetrics file for {name}; run scripts/wrds_pull.py first")
    opts = load_optionm_options(opt_file[-1], f"{name}:US")
    prices = load_optionm_prices(sorted(WRDS.glob(f"secprd_{name}_*.csv"))[-1])
    zero_path = sorted(WRDS.glob("zerocd_*.csv"))[-1]
    fx = {d: v * 100.0 for d, v in                    # the reader divides by 100
          read_boc_series(ROOT / "data/raw/fx/FXUSDCAD.csv").items()}
    div_file = WRDS / f"distrd_{name}.csv"
    days = sorted(d.date() for d in opts["date"].unique())
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text)
        lines.append(text)

    say(f"{name} New York listing, OptionMetrics, {days[0]} to {days[-1]} ({len(days)} days).")
    say("Our vol minus OptionMetrics' vol, in vol points, from their own inputs.")
    out = {}
    for key, cfg in RUNS.items():
        divs = load_optionm_dividends(div_file, fx if cfg["convert"] else None,
                                      since=days[0] - timedelta(days=5))
        last_known = max(d for d, _ in divs) + timedelta(days=80)
        tasks = [(day, opts[opts["date"] == pd.Timestamp(day)], prices[day], zero_path,
                  divs, last_known, cfg["ex_shift"]) for day in days if day in prices]
        with ProcessPoolExecutor(8) as pool:
            out[key] = list(pool.map(one, tasks))
    a = out["A"]
    say(f"dividends used (ex-date, US dollars after conversion): "
        + ", ".join(f"{d} {c:.3f}" for d, c in
                    load_optionm_dividends(div_file, fx, since=days[0] - timedelta(days=5))))
    tally = pd.DataFrame([r["tally"] for r in a]).fillna(0).sum().astype(int)
    say(f"\nrows near the money: {int(tally.sum())}; we inverted {sum(r['ours'] for r in a)}, "
        f"they published {sum(r['theirs'] for r in a)}, both {sum(r['both'] for r in a)}")
    say("our rejections: " + ", ".join(f"{k} {v}" for k, v in
                                       tally.sort_values(ascending=False).items() if k != "ok"))

    say("\nsummary by run (all rows where both have a vol):")
    rows = []
    for key in RUNS:
        d = pd.concat([r["diffs"] for r in out[key]], ignore_index=True)
        x = d[d["reference"] == EXCHANGE]
        for label, sub in (("all", x), ("calls", x[x["side"] == "C"]), ("puts", x[x["side"] == "P"]),
                           ("expiry after next ex-date", x[x["after_ex"]]),
                           ("expiry before next ex-date", x[~x["after_ex"]])):
            v = sub["diff"]
            rows.append({"run": LABEL[key], "rows": label, "n": len(v), "mean": v.mean(),
                         "median": v.median(), "p5": v.quantile(.05), "p95": v.quantile(.95),
                         "within 0.25": (v.abs() <= 0.25).mean(),
                         "mean |diff|": v.abs().mean()})
    say(pd.DataFrame(rows).round(3).to_string(index=False))

    say("")
    say("which dividend does the MARKET price? Call minus put on expiries after the next "
        "ex-date,")
    say("weighted by quote tightness (zero means that run's forward matches the market's):")
    rows = []
    for key in RUNS:
        dd = pd.concat([r["diffs"] for r in out[key]], ignore_index=True)
        x = dd[(dd["reference"] == PAIR) & dd["after_ex"] & (dd["quote_bar"] > 0)]
        x = x.assign(w=1.0 / x["quote_bar"] ** 2)
        per_day = x.groupby("date").apply(lambda g: np.average(g["diff"], weights=g["w"]),
                                          include_groups=False)
        rows.append({"run": LABEL[key], "days": len(per_day), "mean gap": per_day.mean(),
                     "std_error": mean_std_error(per_day.sort_index()),
                     "t": per_day.mean() / mean_std_error(per_day.sort_index())})
    say(pd.DataFrame(rows).round(3).to_string(index=False))

    d = pd.concat([r["diffs"] for r in a], ignore_index=True)
    pooled = pool_buckets(d)
    cols = ["days_bucket", "moneyness_bucket", "days", "mean", "std_error", "std_error_iid", "t_stat",
            "mean_quote_bar", "in_quote_bars", "disagreement"]
    for side, title in (("C", "calls"), ("P", "puts")):
        p = pooled[(pooled["reference"] == EXCHANGE) & (pooled["side"] == side)]
        say(f"\nrun A, ours minus OptionMetrics, {title} (vol points):")
        say(p[cols].round(2).to_string(index=False))
    p = pooled[pooled["reference"] == PAIR]
    say("\nrun A, call minus put on the New York quotes, their rates and no funding spread:")
    say(p[cols].round(2).to_string(index=False))
    x = d[d["reference"] == EXCHANGE]
    bar = x["quote_bar"].median()
    say(f"\nmedian quote bar of one option: {bar:.2f} vol points")

    from front_chart import draw
    # the same comparison twice: options with no dividend in their life, where
    # only the two engines can differ, and options with one, where the
    # dividend each side assumes differs too
    clean, spanning = pool_buckets(d[~d["after_ex"]]), pool_buckets(d[d["after_ex"]])
    fig, axes = plt.subplots(2, 2, figsize=(11, 8.4))
    for i, (pool, label) in enumerate(((clean, "no dividend before expiry"),
                                       (spanning, "a dividend before expiry"))):
        draw(axes[i, 0], pool, EXCHANGE, "C", f"Calls, {label}")
        draw(axes[i, 1], pool, EXCHANGE, "P", f"Puts, {label}")
        axes[i, 0].set_ylabel("days to expiry", fontsize=7.5)
    for ax in axes[1]:
        ax.set_xlabel("strike / share price", fontsize=7.5)
    fig.suptitle(
        f"{name} New York listing: this engine's implied vol minus OptionMetrics', in vol "
        f"points. {len(a)} days, {days[0]} to {days[-1]}.\nSame quotes, their share price and "
        "zero curve, no funding spread. Cell = mean across days; bold * = more than 2 standard "
        "errors from zero.\nTop: only the engines can differ. Bottom: the dividend assumed "
        "differs as well (ours is the declared amount converted to US dollars).", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    chart = ROOT / "reports" / f"{name.lower()}_us_engine_check.png"
    fig.savefig(chart, dpi=130)
    say(f"\nchart: {chart.relative_to(ROOT)}")
    (ROOT / "reports" / f"{name.lower()}_us_engine_numbers.txt").write_text(
        "Derived statistics only. No prices or quotes appear in this file.\n\n"
        + "\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
