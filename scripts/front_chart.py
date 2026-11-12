"""
front_chart.py — the front chart: model against market (build sheet B3, B4, B2).

Run scripts/run_days.py first; this reads the results it cached and draws.

    .venv/Scripts/python scripts/run_days.py RY
    .venv/Scripts/python scripts/front_chart.py RY

Each implied vol is compared with three references (src/references.py), pooled
across days by days to expiry and by strike / share price:

  call minus put        same strike and expiry; a gap means a carry input is off
  strike minus smile    against a curve fitted through the other strikes
  ours minus exchange   against the vol the exchange publishes, calls and puts apart

EXPECTED SIGN of ours minus the exchange's, written down before drawing
(build sheet B3). Two effects, with different fingerprints:
  1. If the exchange inverts with a European formula, any early-exercise value
     in the price is read as extra vol, so its vol is ABOVE ours: negative
     differences, largest for in-the-money puts (strike above the share price)
     and for calls shortly before an ex-dividend date.
  2. If the exchange funds at a lower rate than we do (we add a spread fitted
     from options), its forward is lower, so it needs MORE vol on calls and
     LESS on puts: negative for calls, positive for puts, about the same size,
     the same at every strike.
A positive number on calls, or one that bends with strike in some other way,
would mean an input is wrong, most likely ours. (I had already seen the
all-strikes averages when writing this: calls -0.7, puts -0.4.)

Prints DERIVED numbers only and saves one chart under reports/.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from references import (EXCHANGE, PAIR, SMILE, add_buckets, pool_buckets,
                        reference_differences, sensitivity_table, smile_counts, tightest)
from run_days import fingerprint, one_per_day   # the cache this script reads

PANELS = [(PAIR, "pair", "Call minus put"),
          (SMILE, "all", "Strike minus smile"),
          (EXCHANGE, "C", "Ours minus exchange: calls"),
          (EXCHANGE, "P", "Ours minus exchange: puts")]
SOLVER_FLOOR = 0.04     # vol points, n=400 against n=1600 (tests/test_voleng.py)


def load(name: str) -> list[dict]:
    """The funded pass (futures curve plus trailing spread) from the cache, or
    the pass with no spread where no spread could be fitted for this name.
    Files this project wrote itself; pickle is not safe on anyone else's."""
    cache = ROOT / "data/derived" / name.lower() / fingerprint(name)
    for pattern in ("*_base_s*.pkl", "*_base.pkl"):
        files = sorted(cache.glob(pattern)) if cache.exists() else []
        out = [pickle.loads(f.read_bytes()) for f in files]
        out = [r for r in out if r.get("ok") and "inverted" in r]
        if out:
            return one_per_day(out, f"{name} front chart")
    raise SystemExit(f"no cached results for {name}; run scripts/run_days.py {name} first")


def grid(pooled: pd.DataFrame, reference: str, side: str, col: str) -> pd.DataFrame:
    p = pooled[(pooled["reference"] == reference) & (pooled["side"] == side)]
    return p.pivot(index="days_bucket", columns="moneyness_bucket", values=col)


def draw(ax, pooled, reference, side, title):
    mean, flag = (grid(pooled, reference, side, c) for c in ("mean", "disagreement"))
    days = grid(pooled, reference, side, "days")
    ax.imshow(mean.to_numpy(dtype=float), cmap="RdBu_r", vmin=-1.5, vmax=1.5, aspect="auto")
    for i in range(mean.shape[0]):
        for j in range(mean.shape[1]):
            v = mean.iat[i, j]
            if np.isfinite(v):
                real = bool(flag.iat[i, j])
                ax.text(j, i, f"{v:+.2f}" + ("*" if real else ""), ha="center", va="center",
                        fontsize=8, fontweight="bold" if real else "normal",
                        color="black" if real else "#555555")
                ax.text(j, i + 0.33, f"{int(days.iat[i, j])}d", ha="center", va="center",
                        fontsize=5.5, color="#555555")
    ax.set_xticks(range(mean.shape[1]), mean.columns, fontsize=6.5)
    ax.set_yticks(range(mean.shape[0]), mean.index, fontsize=7)
    ax.set_title(title, fontsize=9)


def main(name: str) -> None:
    results = load(name)
    # the comparison with the references is done here, from the vols the day
    # runner cached, so changing the smile never means re-pricing
    daily = [reference_differences(r["inverted"], r["spot"], divs=r["divs"]) for r in results]
    fits = smile_counts(daily)
    # The share price is implied from one expiry's call-put pairs, so on that
    # expiry calls and puts agree BY CONSTRUCTION. Its pairs are not evidence
    # and are left out of the call-minus-put panel (the carry tables in
    # run_days.py leave it out too).
    fitted = 0
    for i, (r, d) in enumerate(zip(results, daily)):
        own = (d["reference"] == PAIR) & (d["expiry"] == r.get("front"))
        fitted += int(own.sum())
        daily[i] = d[~own]
    diffs = pd.concat(daily, ignore_index=True)
    smile = diffs[diffs["reference"] == SMILE]
    diffs.loc[diffs["reference"] == SMILE, "side"] = "all"      # calls and puts on one curve
    first, last = results[0]["date"], results[-1]["date"]
    spreads = [r["spread_used"] * 1e4 for r in results if r["spread_used"] is not None]
    sources = sorted({r.get("spread_source") or "own options, earlier days"
                      for r in results if r["spread_used"] is not None})
    rates = (f"CORRA futures plus a funding spread FITTED FROM OPTIONS "
             f"({'; '.join(sources)}; {min(spreads):.0f} to {max(spreads):.0f} bp a year)"
             if spreads else
             "CORRA futures, NO funding spread (none could be fitted for this name)")

    print(f"{name}: {len(results)} days, {first} to {last}. Rates: {rates}.")
    tiers = pd.DataFrame([r["tiers"] for r in results]).fillna(0).sum().astype(int)
    tally = pd.DataFrame([r["tally"] for r in results]).fillna(0).sum().astype(int)
    tally = tally.sort_values(ascending=False)
    used, total = int(tally.get("ok", 0)), int(tally.sum())
    print(f"\nrows: {total}; inverted {used}; rejected {total - used} "
          f"({(total - used) / total:.1%}), by reason:")
    print("  " + ", ".join(f"{k} {v}" for k, v in tally.items() if k != "ok"))
    print("evidence behind the rows: " + ", ".join(
        f"{k} {v} ({v / tiers.sum():.0%})" for k, v in tiers.items()))
    by_exp = smile.groupby(["date", "expiry"])["poor_fit"].first()
    print(f"smile fits: {len(by_exp)} expiry-days, {int(by_exp.sum())} flagged as poor fits "
          f"({by_exp.mean():.0%}) and left out of the smile panel")
    print(f"call-put pairs left out because their expiry was used to imply the share "
          f"price: {fitted}")
    print(f"smile coverage: {fits['expiries']} expiry-days; {fits['slice']} fitted by their own "
          f"curve, {fits['surface']} by the day's surface (too few strikes for a curve of "
          f"their own), {fits['unfitted']} not fitted")
    print(f"calendar check: {fits['calendar_pairs']} pairs of neighbouring expiries tested, "
          f"{fits['calendar_violations']} where the later curve sits below the earlier one "
          f"({fits['calendar_violations'] / max(fits['calendar_pairs'], 1):.1%}); the surface "
          f"lifted {fits['levels_raised']} at-the-money levels")

    pooled, tight = pool_buckets(diffs), pool_buckets(tightest(diffs))
    cols = ["days_bucket", "moneyness_bucket", "days", "mean", "std_error", "std_error_iid", "t_stat",
            "mean_quote_bar", "in_quote_bars", "disagreement"]
    for ref, side, title in PANELS:
        p = pooled[(pooled["reference"] == ref) & (pooled["side"] == side)]
        print(f"\n{title} (vol points; * = more than 2 standard errors from zero on 5+ days):")
        print(p[cols].round(2).to_string(index=False))
        n = int(p["disagreement"].sum())
        worst = p["in_quote_bars"].abs().max()
        print(f"  buckets called a disagreement: {n} of {len(p)}; largest mean is "
              f"{worst:.2f} of a quote bar")
    bars = add_buckets(diffs[diffs["side"].isin(["C", "P", "all"])])
    print("\nmedian quote bar of one option (vol points) by days to expiry: "
          + ", ".join(f"{k} {v:.2f}" for k, v in
                      bars.groupby("days_bucket", observed=True)["quote_bar"].median().items()))

    market = pooled[pooled["reference"].isin([PAIR, SMILE])]
    sens = sensitivity_table(100.0, 0.18, 0.027, 0.70)
    print("\nwhat a stated input error does to the call-put gap, at the money (vol points;\n"
          "a 100 share, 18% vol, 2.7% rate, one 0.70 dividend half way to expiry):")
    print(sens.round(2).to_string())

    fig, axes = plt.subplots(2, 4, figsize=(17, 7.6))
    for j, (ref, side, title) in enumerate(PANELS):
        draw(axes[0, j], pooled, ref, side, title)
        draw(axes[1, j], tight, ref, side, title + "\ntightest third of quotes")
    for ax in axes[1]:
        ax.set_xlabel("strike / share price", fontsize=7.5)
    for ax in axes[:, 0]:
        ax.set_ylabel("days to expiry", fontsize=7.5)
    fig.suptitle(
        f"{name}: our implied vol against three references, in vol points. {len(results)} days, "
        f"{first} to {last}.\nCell = mean across days (small number = days behind it). "
        "Bold with * = more than 2 standard errors from zero on at least 5 days; grey = not "
        f"distinguishable from zero.\nRates: {rates}. Marks "
        "are quote midpoints where a two-sided quote exists, settlements otherwise.",
        fontsize=9.5)
    foot = (f"Rows {total}: inverted {used}, rejected {total - used} ("
            + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in tally.items() if k != "ok")
            + ").   Evidence: " + ", ".join(f"{k.replace('_', ' ')} {v / tiers.sum():.0%}"
                                            for k, v in tiers.items())
            + f".   Call-put pairs on the expiry the share price was implied from "
            f"({fitted}) are left out: they agree by construction."
            + f"   Smile (SVI against the forward): {fits['slice']} expiry-days on their own "
            f"curve, {fits['surface']} on the day's surface, {fits['unfitted']} not fitted; "
            f"{by_exp.mean():.0%} flagged as poor fits and left out; calendar check failed on "
            f"{fits['calendar_violations']} of {fits['calendar_pairs']} neighbouring pairs."
            + f"\nThree yardsticks: the standard error (decides bold); the quote bar, median "
            f"{bars['quote_bar'].median():.1f} vol points for one option (on the first two panels "
            f"no cell exceeds {market['in_quote_bars'].abs().max():.2f} of it, so nothing there "
            f"is tradeable); the solver's rounding, {SOLVER_FLOOR} vol points. The exchange "
            "panels compare two MODELS, not two prices.")
    import textwrap
    foot = "\n".join(textwrap.fill(line, 215) for line in foot.split("\n"))
    fig.text(0.01, 0.01, foot, fontsize=7.5, va="bottom")
    fig.tight_layout(rect=(0, 0.09, 1, 0.93))
    out = ROOT / "reports" / f"{name.lower()}_front_chart.png"
    fig.savefig(out, dpi=130)
    print(f"\nchart: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1])
