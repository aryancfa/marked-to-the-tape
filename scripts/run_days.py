"""
run_days.py — the call-put gap on every day of the snapshot, pooled across days.

One day says little: its quote noise and a wrong input look alike. Across
many days the noise averages out and a wrong input does not, so the pooled
mean with its day-to-day standard error is the number to trust (B3, B5).

    .venv/Scripts/python scripts/run_days.py RY
    .venv/Scripts/python scripts/run_days.py RY --compare-bills
    .venv/Scripts/python scripts/run_days.py RY --every 3 --workers 4 --force

The passes
  Base    rates from three-month CORRA futures (CRA files in data/raw/mx),
          Treasury bills on any day the futures do not cover. No spread.
          Each day also yields its own estimate of the funding spread the
          options imply over that curve.
  Funded  each day again, with the base curve plus the MEAN OF THE EARLIER
          DAYS' estimates. A day never sees its own estimate or a later one.
          Days with fewer than --min-history earlier estimates are left out.
  Bills   only with --compare-bills: Treasury bills on every day, no spread,
          to show how little the choice of base curve matters.

Days are independent within a pass, so they run in parallel. Each result is
cached under data/derived/ (gitignored). The cache folder is named by a
fingerprint of the code, the dividend file, the rate files and the option
files, so changing any of them recomputes everything. --force recomputes
regardless.

The cache is pickle files this script wrote itself, on this machine. Pickle
can run code when loaded, so never point it at a file from anywhere else.

Prints DERIVED numbers only and saves one chart under reports/.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from loaders.mx import load_mx_futures, load_mx_options, load_mx_underlying
from loaders.reference import first_projected, load_dividends
from rates import load_base_curve, load_curve
from surface import (EX_SHIFT_DAYS, analyse_day, pool_across_days, rejection_tally,
                     trailing_spread, vs_exchange)
from tradingcal import is_monthly_expiry

BOC = ROOT / "data/raw/boc"


def fingerprint(underlying: str) -> str:
    """Short hash of everything a day's result depends on."""
    h = hashlib.sha1()
    files = sorted((ROOT / "src").rglob("*.py"))
    files += sorted(BOC.glob("*.csv"))
    files.append(ROOT / "data/reference" / f"{underlying.lower()}_dividends.csv")
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    for f in sorted((ROOT / "data/raw/mx").glob("*.csv")):   # large: name and size
        h.update(f"{f.name}:{f.stat().st_size}".encode())
    return h.hexdigest()[:12]


def one_day(task) -> dict:
    """Runs in a worker process. Returns derived numbers only."""
    day, o, file_mid, file_last, schedule, spread, cra, ex_shift = task
    t0 = time.time()
    out = dict(date=day, ok=False, spread_used=spread)
    try:
        curve = load_curve(BOC, day) if cra is None else load_base_curve(BOC, day, cra)
    except Exception as exc:                       # no rate for this day
        out["reason"] = f"rate: {exc}"
        return out
    out["rate_source"] = curve.source
    if spread is not None:
        curve = curve.plus(spread)
    divs = [(x.ex_date, x.cash) for x in schedule]
    res = analyse_day(o, file_mid, curve, divs, first_projected(schedule, day),
                      ex_shift_days=ex_shift)
    out.update(front=res.front, reason=res.spot_reason, stale_rates=bool(curve.stale))
    if res.spot is None:
        return out
    out.update(
        ok=True,
        spot=res.spot,
        gap=res.gap,                # per strike; stays in the gitignored cache
        funding_spread=res.funding_spread,
        bp_vs_last=(res.spot / file_last - 1.0) * 1e4,
        bp_vs_mid=(res.spot / file_mid - 1.0) * 1e4,
        tally=rejection_tally(res.inverted).to_dict(),
        summary=res.summary.assign(date=pd.Timestamp(day)),
        vs_exchange=vs_exchange(res.inverted),
        median_half_spread=float(res.inverted["half_spread_vol"].median() * 100.0),
        seconds=time.time() - t0)
    return out


def run(tasks: dict, cache: Path, workers: int, force: bool, label: str) -> list[dict]:
    """tasks: cache file name -> task. Loads what is cached, computes the rest."""
    results, todo = [], []
    for fname, task in tasks.items():
        f = cache / fname
        if f.exists() and not force:
            results.append(pickle.loads(f.read_bytes()))
        else:
            todo.append((fname, task))
    print(f"{label}: {len(tasks)} days, {len(results)} from cache, "
          f"{len(todo)} to compute on {workers} workers")
    if todo:
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for (fname, _), r in zip(todo, pool.map(one_day, [t for _, t in todo])):
                (cache / fname).write_bytes(pickle.dumps(r))
                results.append(r)
        print(f"  computed in {time.time() - t0:.0f} s")
    results.sort(key=lambda r: r["date"])
    for r in results:
        if not r["ok"]:
            print(f"  {r['date']}: no result ({r['reason']})")
    return results


def tested_rows(results: list[dict]) -> pd.DataFrame:
    """One row per (day, expiry), without the calibrated expiry: its gap is
    zero by construction, so it is not evidence."""
    daily = pd.concat([r["summary"].reset_index() for r in results], ignore_index=True)
    daily = daily[daily["carry"] != "calibrated"].copy()
    daily["monthly"] = [is_monthly_expiry(e) for e in daily["expiry"]]
    return daily


def groups(daily: pd.DataFrame) -> pd.DataFrame:
    """Mean gap by kind of expiry, with the standard error taken across DAYS
    (each day's rows are averaged first, so a day counts once). `gap` is the
    mean weighted by quote tightness; `plain` is the unweighted mean."""
    d = daily.copy()
    d["group"] = np.select(
        [d["monthly"] & (d["days"] > 60) & (d["carry"] == "declared only"),
         d["monthly"] & (d["days"] > 60), d["monthly"], d["days"] <= 30],
        ["monthly over 60d, declared dividends", "monthly over 60d, projected dividend",
         "monthly up to 60d", "weekly up to 30d"], "weekly over 30d")
    per_day = d.groupby(["group", "date"])[["weighted_gap", "mean_gap", "fwd_error_bp"]].mean()
    g = per_day.groupby("group")
    n = g["weighted_gap"].count()
    out = pd.DataFrame({"days": n, "gap": g["weighted_gap"].mean(),
                        "se": g["weighted_gap"].std() / np.sqrt(n),
                        "plain": g["mean_gap"].mean(),
                        "fwd_bp": g["fwd_error_bp"].mean()})
    out["t"] = out["gap"] / out["se"]
    return out[["days", "gap", "se", "t", "plain", "fwd_bp"]]


def spread_line(label: str, results: list[dict]) -> None:
    e = pd.Series([r["funding_spread"] for r in results
                   if r["funding_spread"] is not None]) * 1e4
    print(f"funding spread the options imply over {label}: mean {e.mean():+.1f} bp a year, "
          f"standard error {e.std()/np.sqrt(len(e)):.1f}, sd {e.std():.1f}, days {len(e)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("underlying")
    ap.add_argument("--every", type=int, default=1, help="use every n-th day")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--min-history", type=int, default=20,
                    help="earlier daily estimates needed before a spread is used")
    ap.add_argument("--compare-bills", action="store_true",
                    help="also run every day on Treasury bills alone")
    ap.add_argument("--ex-shift", type=float, default=EX_SHIFT_DAYS,
                    help="days before the ex-date's close at which the share price "
                         "drops (default 1 = the open; 0 = the close)")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    a = ap.parse_args()
    name = a.underlying

    opts = load_mx_options(ROOT / "data/raw/mx")
    und = load_mx_underlying(ROOT / "data/raw/mx")
    fut = load_mx_futures(ROOT / "data/raw/mx")
    fut = fut[fut["root"] == "CRA"]
    cra = [(d.date(), e.date(), float(px))
           for d, e, px in zip(fut["date"], fut["expiry"], fut["settle"])]
    opts, und = opts[opts["underlying"] == name], und[und["underlying"] == name]
    if opts.empty or und.empty:
        raise SystemExit(f"no {name} rows in data/raw/mx")
    schedule = load_dividends(ROOT / "data/reference" / f"{name.lower()}_dividends.csv")
    und = und.set_index("date")
    cache = ROOT / "data/derived" / name.lower() / fingerprint(name)
    cache.mkdir(parents=True, exist_ok=True)

    def task(ts, spread, quotes):
        return (ts.date(), opts[opts["date"] == ts], float(und.loc[ts, "mid"]),
                float(und.loc[ts, "last"]), schedule, spread, quotes, a.ex_shift)

    tag = "" if a.ex_shift == EX_SHIFT_DAYS else f"_x{a.ex_shift:g}"
    print(f"dividend drop placed {a.ex_shift:g} day(s) before the close of the ex-date"
          + (" (default: the open)" if not tag else ""))

    days = sorted(set(opts["date"]) & set(und.index))[::a.every]

    # ---- base: CORRA futures (bills where there are none), no spread ------
    results = run({f"{ts:%Y%m%d}_base{tag}.pkl": task(ts, None, cra) for ts in days},
                  cache, a.workers, a.force, f"{name} base curve")
    good = [r for r in results if r["ok"]]
    print(f"\ndays with a result: {len(good)} of {len(results)}")
    if not good:
        return
    print("rate source by day:",
          pd.Series([r["rate_source"] for r in good]).value_counts().to_dict(),
          "| days with rates carried from an earlier day:",
          sum(r["stale_rates"] for r in good))
    spot = pd.DataFrame({"vs file last (bp)": [r["bp_vs_last"] for r in good],
                         "vs file midpoint (bp)": [r["bp_vs_mid"] for r in good]})
    print("\nimplied share price against the file's:")
    print(spot.agg(["mean", "std", "min", "max"]).round(1).to_string())
    tally = pd.DataFrame([r["tally"] for r in good]).fillna(0).sum().astype(int)
    print("\nrejection tally, all days:", tally.sort_values(ascending=False).to_dict())
    ex = pd.DataFrame([r["vs_exchange"] for r in good])
    print(f"our vol minus the exchange's, mean of daily means (vol points): "
          f"calls {ex['calls_mean'].mean():+.2f}, puts {ex['puts_mean'].mean():+.2f}")
    print("median quote half-spread, median day: "
          f"{pd.Series([r['median_half_spread'] for r in good]).median():.2f} vol points")
    print()
    spread_line("the base curve", good)

    # ---- funded: base plus the mean of EARLIER days' estimates ------------
    estimates = {r["date"]: r["funding_spread"] for r in good}
    used = {ts: trailing_spread(estimates, ts.date(), a.min_history)
            for ts in days if ts.date() in estimates}
    funded_days = [ts for ts, s in used.items() if s is not None]
    print(f"days with at least {a.min_history} earlier estimates: {len(funded_days)} "
          f"of {len(days)}")
    if not funded_days:
        print("not enough history to apply a spread; funded pass skipped")
        return
    u = pd.Series([used[ts] for ts in funded_days]) * 1e4
    print(f"spread applied (mean of earlier days): first {u.iloc[0]:+.1f}, "
          f"last {u.iloc[-1]:+.1f}, min {u.min():+.1f}, max {u.max():+.1f} bp a year\n")
    funded = run({f"{ts:%Y%m%d}_base{tag}_s{round(used[ts] * 1e6):+d}.pkl": task(ts, used[ts], cra)
                  for ts in funded_days},
                 cache, a.workers, a.force, f"{name} base curve plus trailing spread")
    funded = [r for r in funded if r["ok"]]
    same = {r["date"] for r in funded}
    before = tested_rows([r for r in good if r["date"] in same])
    after = tested_rows(funded)

    print(f"\ncall minus put implied vol on the SAME {len(same)} days (vol points).")
    print("gap = mean weighted by quote tightness, se = standard error across days,")
    print("plain = unweighted mean, fwd_bp = market forward minus the model's:")
    cmp = groups(before).join(groups(after), lsuffix="_base", rsuffix="_funded")
    print(cmp.drop(columns=["days_funded"]).round(2).to_string())

    for d in (before, after):
        d["mean_gap"] = d["weighted_gap"]          # pool the weighted mean
    pb, pa = pool_across_days(before), pool_across_days(after)
    pooled = pb.join(pa[["mean_gap", "std_error", "t_stat"]], rsuffix="_funded")
    # an expiry's label can change as dividends are announced: take most days
    pooled["carry"] = after.groupby("expiry")["carry"].agg(
        lambda s: "projected dividend" if (s == "projected dividend").mean() > 0.5
        else "declared only")
    pooled["median_quote_bar"] = after.groupby("expiry")["median_quote_bar"].median()
    print("\nby expiry, same days (base | funded), weighted mean:")
    print(pooled.round(2).to_string())

    p = pooled[pooled["days_observed"] >= 5]
    fig, ax = plt.subplots(figsize=(11, 5.6))
    for i, row in enumerate(p.itertuples()):
        col = "#b5651d" if row.carry == "projected dividend" else "#1f4e79"
        ax.plot([i, i], [-row.median_quote_bar, row.median_quote_bar], color="#e3e3e3",
                lw=9, solid_capstyle="butt", zorder=1)
        ax.errorbar(i - 0.17, row.mean_gap, yerr=2 * row.std_error, fmt="o", ms=4,
                    capsize=3, color="#8c8c8c", mfc="white", zorder=2)
        ax.errorbar(i + 0.17, row.mean_gap_funded, yerr=2 * row.std_error_funded, fmt="o",
                    ms=5, capsize=3, color=col, zorder=3)
    ax.axhline(0.0, color="black", lw=0.7)
    ax.set_xticks(range(len(p)))
    ax.set_xticklabels([f"{e:%d %b %y} ({n})" for e, n in zip(p.index, p["days_observed"])],
                       fontsize=7, rotation=60, ha="right")
    ax.set_ylabel("call IV minus put IV (vol points)")
    ax.set_title(
        f"{name}: call minus put implied vol by expiry, {min(same)} to {max(same)} "
        f"({len(same)} days). Point = mean across days, bar = ± 2 standard errors.\n"
        "Hollow grey: rates from CORRA futures. Filled: the same plus a funding spread "
        f"FITTED FROM OPTIONS on earlier days only ({u.min():.0f} to {u.max():.0f} bp a year).\n"
        "Blue: declared dividends only. Brown: rested on a projected dividend on most days. "
        "Pale band = ± the typical quote bar. Label: expiry (days observed).", fontsize=9)
    fig.tight_layout()
    out = ROOT / "reports" / f"{name.lower()}_cp_gap_pooled{tag.replace('.', 'p')}.png"
    fig.savefig(out, dpi=130)
    print(f"\nchart: {out.relative_to(ROOT)}")

    # ---- optional: Treasury bills alone, to compare base curves -----------
    if a.compare_bills:
        print()
        bills = run({f"{ts:%Y%m%d}_bills{tag}.pkl": task(ts, None, None) for ts in days},
                    cache, a.workers, a.force, f"{name} Treasury bills only")
        bills = [r for r in bills if r["ok"]]
        both = {r["date"] for r in bills} & {r["date"] for r in good}
        cmp = groups(tested_rows([r for r in bills if r["date"] in both])).join(
            groups(tested_rows([r for r in good if r["date"] in both])),
            lsuffix="_bills", rsuffix="_base")
        print(f"\nbills against the base curve, no spread, same {len(both)} days:")
        print(cmp.drop(columns=["days_base"]).round(2).to_string())
        spread_line("Treasury bills", bills)


if __name__ == "__main__":
    main()
