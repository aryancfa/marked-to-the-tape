"""
run_days.py — the call-put gap on every day of the snapshot, pooled across days.

One day says little: its quote noise and a wrong input look alike. Across
many days the noise averages out and a wrong input does not, so the pooled
mean with its day-to-day standard error is the number to trust (B3, B5).

    .venv/Scripts/python scripts/run_days.py RY
    .venv/Scripts/python scripts/run_days.py RY --every 3 --workers 4 --force

Days are independent, so they run in parallel, one process per worker. Each
day's result is cached under data/derived/ (gitignored). The cache folder is
named by a fingerprint of the code, the dividend file, the rate files and the
option files, so changing any of them recomputes everything and nothing stale
is ever reused. --force recomputes regardless.

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
import pandas as pd

from loaders.mx import load_mx_options, load_mx_underlying
from loaders.reference import first_projected, load_dividends
from rates import load_curve
from surface import analyse_day, pool_across_days, rejection_tally, vs_exchange


def fingerprint(underlying: str) -> str:
    """Short hash of everything a day's result depends on."""
    h = hashlib.sha1()
    files = sorted((ROOT / "src").rglob("*.py"))
    files += sorted((ROOT / "data/raw/boc").glob("*.csv"))
    files.append(ROOT / "data/reference" / f"{underlying.lower()}_dividends.csv")
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    for f in sorted((ROOT / "data/raw/mx").glob("*.csv")):   # large: name and size
        h.update(f"{f.name}:{f.stat().st_size}".encode())
    return h.hexdigest()[:12]


def one_day(task) -> dict:
    """Runs in a worker process. Returns derived numbers only."""
    day, o, file_mid, file_last, schedule = task
    t0 = time.time()
    out = dict(date=day, ok=False)
    try:
        curve = load_curve(ROOT / "data/raw/boc", day)
    except Exception as exc:                       # no rate for this day
        out["reason"] = f"rate: {exc}"
        return out
    divs = [(d, c) for d, c, _ in schedule]
    res = analyse_day(o, file_mid, curve, divs, first_projected(schedule, day))
    out.update(front=res.front, reason=res.spot_reason, stale_rates=bool(curve.stale))
    if res.spot is None:
        return out
    out.update(
        ok=True,
        bp_vs_last=(res.spot / file_last - 1.0) * 1e4,
        bp_vs_mid=(res.spot / file_mid - 1.0) * 1e4,
        tally=rejection_tally(res.inverted).to_dict(),
        summary=res.summary.assign(date=pd.Timestamp(day)),
        vs_exchange=vs_exchange(res.inverted),
        median_half_spread=float(res.inverted["half_spread_vol"].median() * 100.0),
        seconds=time.time() - t0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("underlying")
    ap.add_argument("--every", type=int, default=1, help="use every n-th day")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    a = ap.parse_args()
    name = a.underlying

    opts = load_mx_options(ROOT / "data/raw/mx")
    und = load_mx_underlying(ROOT / "data/raw/mx")
    opts, und = opts[opts["underlying"] == name], und[und["underlying"] == name]
    if opts.empty or und.empty:
        raise SystemExit(f"no {name} rows in data/raw/mx")
    schedule = load_dividends(ROOT / "data/reference" / f"{name.lower()}_dividends.csv")
    und = und.set_index("date")
    cache = ROOT / "data/derived" / name.lower() / fingerprint(name)
    cache.mkdir(parents=True, exist_ok=True)

    days = sorted(set(opts["date"]) & set(und.index))[::a.every]
    results, tasks = [], []
    for ts in days:
        f = cache / f"{ts:%Y%m%d}.pkl"
        if f.exists() and not a.force:
            results.append(pickle.loads(f.read_bytes()))
        else:
            tasks.append((ts.date(), opts[opts["date"] == ts],
                          float(und.loc[ts, "mid"]), float(und.loc[ts, "last"]), schedule))
    print(f"{name}: {len(days)} days, {len(results)} from cache, "
          f"{len(tasks)} to compute on {a.workers} workers")
    t0 = time.time()
    if tasks:
        with ProcessPoolExecutor(max_workers=a.workers) as pool:
            for r in pool.map(one_day, tasks):
                (cache / f"{r['date']:%Y%m%d}.pkl").write_bytes(pickle.dumps(r))
                results.append(r)
        print(f"computed in {time.time() - t0:.0f} s")
    results.sort(key=lambda r: r["date"])

    good = [r for r in results if r["ok"]]
    print(f"\ndays with a result: {len(good)} of {len(results)}")
    for r in results:
        if not r["ok"]:
            print(f"  {r['date']}: no result ({r['reason']})")
    if not good:
        return
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

    daily = pd.concat([r["summary"].reset_index() for r in good], ignore_index=True)
    # the calibrated expiry is zero by construction: it is not evidence
    tested = daily[daily["carry"] != "calibrated"]
    pooled = pool_across_days(tested)
    pooled["carry"] = tested.groupby("expiry")["carry"].agg(
        lambda s: "projected dividend" if (s == "projected dividend").any() else "declared only")
    pooled["within_day_scatter"] = tested.groupby("expiry")["scatter"].median()
    pooled["median_quote_bar"] = tested.groupby("expiry")["median_quote_bar"].median()
    print("\ncall minus put implied vol by expiry, pooled across days (vol points).")
    print("Days on which an expiry was the calibrated one are left out.")
    print(pooled.round(2).to_string())

    p = pooled[pooled["days_observed"] >= 5]
    fig, ax = plt.subplots(figsize=(11, 5.4))
    x = range(len(p))
    colours = ["#b5651d" if c == "projected dividend" else "#1f4e79" for c in p["carry"]]
    for i, (m, se, qb, col) in enumerate(zip(p["mean_gap"], p["std_error"],
                                             p["median_quote_bar"], colours)):
        ax.plot([i, i], [-qb, qb], color="#d9d9d9", lw=9, solid_capstyle="butt", zorder=1)
        ax.errorbar(i, m, yerr=2 * se, fmt="o", ms=5, capsize=4, color=col, zorder=3)
    ax.axhline(0.0, color="black", lw=0.7)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{e:%d %b %y} ({n})" for e, n in zip(p.index, p["days_observed"])],
                       fontsize=7, rotation=60, ha="right")
    ax.set_ylabel("call IV minus put IV (vol points)")
    ax.set_title(
        f"{name}: call minus put implied vol by expiry, {good[0]['date']} to "
        f"{good[-1]['date']} ({len(good)} days).\nPoint = mean across days, bar = ± 2 "
        "standard errors (day-to-day scatter). Grey = ± the typical quote bar.\n"
        "Blue: declared dividends only. Brown: rests on a projected dividend. "
        "Label: expiry (days observed).", fontsize=9)
    fig.tight_layout()
    out = ROOT / "reports" / f"{name.lower()}_cp_gap_pooled.png"
    fig.savefig(out, dpi=130)
    print(f"\nchart: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
