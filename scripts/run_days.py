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
import json
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from loaders.mx import load_mx_futures, load_mx_options, load_mx_underlying
from loaders.reference import (PUBLISHED, as_known_on, first_projected, has_history,
                               load_dividends, rule_for, uncertain_ex_dates)
from rates import load_base_curve, load_curve
from surface import (OWN, POOLED, analyse_day, choose_spread, mean_std_error,
                     pool_across_days, rejection_tally, vs_exchange)
from tradingcal import is_monthly_expiry

BOC = ROOT / "data/raw/boc"
PRICING_MODULES = ("voleng.py", "surface.py", "rates.py",
                   "tradingcal.py", "loaders/mx.py", "loaders/reference.py")


MX = ROOT / "data/raw/mx"


def mx_files(underlying: str) -> list[Path]:
    """This underlying's exchange files (quote_<SYMBOL>_<from>_<to>.csv)."""
    return sorted(MX.glob(f"quote_{underlying.upper()}_*.csv"))


def fingerprint(underlying: str) -> str:
    """Short hash of everything a day's result depends on: the modules that
    price, the dividend file, the rate files, and the contents of THIS
    underlying's option files and of the CORRA futures files. Modules that do not touch a day's
    pricing (the position format, the OptionMetrics reader) and other names'
    files are left out, so adding to them does not throw the cache away."""
    h = hashlib.sha1()
    files = [ROOT / "src" / f for f in PRICING_MODULES]
    files += sorted(BOC.glob("*.csv"))
    files.append(ROOT / "reference" / f"{underlying.lower()}_dividends.csv")
    if (ROOT / "reference" / PUBLISHED).exists():       # ex-dates published in advance
        files.append(ROOT / "reference" / PUBLISHED)
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    # The exchange files are hashed by CONTENT, in pieces (they run to tens of
    # megabytes). Name and size would miss a corrected quote that leaves the
    # file the same length.
    for f in mx_files(underlying) + mx_files("CRA"):
        h.update(f.name.encode())
        with open(f, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
    return h.hexdigest()[:12]


def one_per_day(results: list[dict], what: str) -> list[dict]:
    """The results in date order, or stop if two of them are for the same day.

    A cache folder can hold more than one run of a day (a different funding
    spread, a robustness variant). Read together they would count that day
    twice and mix two set-ups in one average. Nothing downstream should ever
    have to wonder which run it is looking at."""
    seen: dict = {}
    for r in results:
        seen[r["date"]] = seen.get(r["date"], 0) + 1
    twice = sorted(d for d, c in seen.items() if c > 1)
    if twice:
        raise SystemExit(f"{what}: {len(twice)} days have more than one cached result "
                         f"(first {twice[0]}). Two runs are mixed in this folder; clear it "
                         "or re-run with --force.")
    return sorted(results, key=lambda r: r["date"])


def one_day(task) -> dict:
    """Runs in a worker process. Returns derived numbers only."""
    day, o, file_mid, file_last, schedule, spread, cra, opt = task
    t0 = time.time()
    out = dict(date=day, ok=False, spread_used=spread,
               spread_source=opt.get("spread_source"))
    # Point-in-time: price the day with the dividends as they could have been
    # known that day, not with amounts and dates declared later.
    rule = rule_for(opt["name"])
    out["dividend_history"] = has_history(schedule, day, rule)
    schedule = as_known_on(schedule, day, rule)
    if opt["flat"]:
        declared = [x.cash for x in schedule if x.status == "declared"]
        if declared:
            schedule = [x._replace(cash=declared[-1]) if x.status == "projected" else x
                        for x in schedule]
    try:
        curve = load_curve(BOC, day) if cra is None else load_base_curve(BOC, day, cra)
    except Exception as exc:                       # no rate for this day
        out["reason"] = f"rate: {exc}"
        return out
    out["rate_source"] = curve.source
    if spread is not None:
        curve = curve.plus(spread)
    divs = [(x.ex_date, x.cash) for x in schedule]
    # ex-dates whose DATE is a guess on this day: projected, and not published
    # in advance by the issuer. The share price is not implied from an expiry
    # that sits within a few sessions of one.
    uncertain = uncertain_ex_dates(schedule, day)
    res = analyse_day(o, file_mid, curve, divs, first_projected(schedule, day),
                      uncertain_ex_dates=uncertain,
                      ex_shift_days=opt["ex_shift"], n=opt["steps"],
                      spot_pairs=opt["spot_pairs"],
                      spot=file_last if opt["spot_last"] else None)
    out.update(front=res.front, reason=res.spot_reason, stale_rates=bool(curve.stale))
    if res.spot is None:
        return out
    out.update(
        ok=True,
        spot=res.spot,
        gap=res.gap,                # per strike; stays in the gitignored cache
        inverted=res.inverted,      # per option; front_chart.py compares it with the
        divs=divs,                  # references, so a smile change needs no re-pricing
        tiers=res.inverted["tier"].value_counts().to_dict(),
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
    g = per_day.sort_index(level="date").groupby("group")
    n = g["weighted_gap"].count()
    out = pd.DataFrame({"days": n, "gap": g["weighted_gap"].mean(),
                        "se": g["weighted_gap"].apply(mean_std_error),
                        "se_iid": g["weighted_gap"].std() / np.sqrt(n),
                        "plain": g["mean_gap"].mean(),
                        "fwd_bp": g["fwd_error_bp"].mean()})
    out["t"] = out["gap"] / out["se"]
    return out[["days", "gap", "se", "se_iid", "t", "plain", "fwd_bp"]]


def other_names_estimates(name: str) -> dict[str, dict]:
    """Each OTHER name's daily funding estimates, from its cached base pass:
    {name: {date: spread or None}}. Names with nothing cached are left out.
    The files are this project's own cache, never anyone else's pickles."""
    out: dict[str, dict] = {}
    for other in sorted({f.name.split("_")[1] for f in MX.glob("quote_*.csv")}):
        if other == name.upper() or not (
                ROOT / "reference" / f"{other.lower()}_dividends.csv").exists():
            continue
        folder = ROOT / "data/derived" / other.lower() / fingerprint(other)
        est = {}
        for f in sorted(folder.glob("*_base.pkl")) if folder.exists() else []:
            r = pickle.loads(f.read_bytes())
            if r.get("ok"):
                est[r["date"]] = r["funding_spread"]
        if est:
            out[other] = est
    return out


def spread_line(label: str, results: list[dict]) -> None:
    e = pd.Series([r["funding_spread"] for r in sorted(results, key=lambda r: r["date"])
                   if r["funding_spread"] is not None]) * 1e4
    print(f"funding spread the options imply over {label}: mean {e.mean():+.1f} bp a year, "
          f"standard error {mean_std_error(e):.1f} (allowing for dependence between days; "
          f"{e.std()/np.sqrt(len(e)):.1f} if days were independent), sd {e.std():.1f}, "
          f"days {len(e)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("underlying")
    ap.add_argument("--every", type=int, default=1, help="use every n-th day")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--min-history", type=int, default=20,
                    help="earlier daily estimates needed before a spread is used")
    ap.add_argument("--compare-bills", action="store_true",
                    help="also run every day on Treasury bills alone")
    ap.add_argument("--ex-shift", type=float, default=None,
                    help="place dividends by calendar days, this many days before the "
                         "ex-date's close (0 = the close). Default: by trading sessions")
    ap.add_argument("--steps", type=int, default=400, help="tree steps (default 400)")
    ap.add_argument("--spot-pairs", type=int, default=5,
                    help="strike pairs the share price is implied from (default 5)")
    ap.add_argument("--spot-last", action="store_true",
                    help="use the file's last trade as the share price; imply nothing")
    ap.add_argument("--flat-dividends", action="store_true",
                    help="hold projected dividends at the last declared amount")
    ap.add_argument("--window", type=int, default=None,
                    help="funding spread from only the latest N earlier estimates "
                         "(default: every earlier day)")
    ap.add_argument("--from-date", type=str, default=None,
                    help="report the comparison only from this date (YYYY-MM-DD)")
    ap.add_argument("--summary-json", type=str, default=None,
                    help="also write the comparison table to this file")
    ap.add_argument("--base-only", action="store_true",
                    help="stop after the pass with no funding spread (run this for every "
                         "name first, so each has the others' estimates to pool)")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    a = ap.parse_args()
    name = a.underlying

    if not mx_files(name):
        raise SystemExit(f"no quote_{name}_*.csv in data/raw/mx")
    opts = load_mx_options(mx_files(name))
    und = load_mx_underlying(mx_files(name))
    fut = load_mx_futures(mx_files("CRA"))
    fut = fut[fut["root"] == "CRA"]
    cra = [(d.date(), e.date(), float(px))
           for d, e, px in zip(fut["date"], fut["expiry"], fut["settle"])]
    opts, und = opts[opts["underlying"] == name], und[und["underlying"] == name]
    if opts.empty or und.empty:
        raise SystemExit(f"no {name} rows in data/raw/mx")
    schedule = load_dividends(ROOT / "reference" / f"{name.lower()}_dividends.csv")
    und = und.set_index("date")
    cache = ROOT / "data/derived" / name.lower() / fingerprint(name)
    cache.mkdir(parents=True, exist_ok=True)

    def task(ts, spread, quotes, source=None):
        return (ts.date(), opts[opts["date"] == ts], float(und.loc[ts, "mid"]),
                float(und.loc[ts, "last"]), schedule, spread, quotes,
                dict(opt, spread_source=source))

    opt = dict(ex_shift=a.ex_shift, steps=a.steps, spot_pairs=a.spot_pairs,
               spot_last=a.spot_last, name=name, flat=a.flat_dividends)
    tag = ("" if a.ex_shift is None else f"_x{a.ex_shift:g}") \
        + ("" if a.steps == 400 else f"_n{a.steps}") \
        + ("" if a.spot_pairs == 5 else f"_k{a.spot_pairs}") \
        + ("_last" if a.spot_last else "") + ("_flat" if a.flat_dividends else "")         + ("" if a.window is None else f"_w{a.window}")         + ("" if a.every == 1 else f"_e{a.every}")
    # Every-Nth-day and short-lookback runs are variants too: untagged, they
    # shared file names with the main run and deleted its funded days.
    if tag:
        print(f"variant: {tag.strip('_').replace('_', ', ')}")
    print("dividend drop placed by trading sessions before the ex-date's open (default)"
          if a.ex_shift is None else
          f"dividend drop placed {a.ex_shift:g} calendar day(s) before the ex-date's close")

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
    blind = sum(not r.get("dividend_history", True) for r in good)
    if blind:
        print(f"days with too little dividend history to project from, priced with the "
              f"file's amounts (hindsight): {blind}")
    print()
    spread_line("the base curve", good)
    if a.base_only:
        print("base pass only (--base-only); no spread applied")
        return

    # ---- funded: base plus the mean of EARLIER days' estimates ------------
    # The name's own trailing spread where it is well measured, otherwise the
    # median across the other names that are (surface.choose_spread).
    estimates = {r["date"]: r["funding_spread"] for r in good}
    others = {} if tag else other_names_estimates(name)
    picked = {ts: choose_spread(estimates, ts.date(), others, a.min_history,
                                window=a.window)
              for ts in days if ts.date() in estimates}
    used = {ts: s for ts, (s, _) in picked.items()}
    funded_days = [ts for ts, s in used.items() if s is not None]
    by_source = pd.Series([picked[ts][1] for ts in funded_days]).value_counts().to_dict()
    print(f"days with a spread to apply: {len(funded_days)} of {len(days)}, by source: "
          f"{by_source} (other names with cached estimates: {len(others)})")
    if not funded_days:
        print("no well-measured spread, own or pooled; funded pass skipped")
        if a.summary_json:
            # the robustness table still needs this variant: its finding IS that
            # the spread cannot be measured, so report the gaps with none applied
            est = pd.Series([s for s in estimates.values() if s is not None]) * 1e4
            base = groups(tested_rows(good)).add_suffix("_base")
            Path(a.summary_json).write_text(json.dumps({
                "variant": tag.strip("_") or "base", "days": len(good), "funded": False,
                "first": str(min(estimates)), "last": str(max(estimates)),
                "spread_mean_bp": float(est.mean()),
                "spread_se_bp": float(mean_std_error(est)),
                "groups": {g: {k: (None if pd.isna(v) else float(v)) for k, v in row.items()}
                           for g, row in base.to_dict(orient="index").items()}}, indent=1),
                encoding="utf-8")
        return
    u = pd.Series([used[ts] for ts in funded_days]) * 1e4
    print(f"spread applied (earlier days only): first {u.iloc[0]:+.1f}, "
          f"last {u.iloc[-1]:+.1f}, min {u.min():+.1f}, max {u.max():+.1f} bp a year\n")
    tasks = {f"{ts:%Y%m%d}_base{tag}_s{round(used[ts] * 1e6):+d}.pkl":
             task(ts, used[ts], cra, picked[ts][1]) for ts in funded_days}
    # a day run earlier under a different spread must not sit beside this one
    for stale in cache.glob(f"*_base{tag}_s*.pkl"):
        if stale.name not in tasks and (tag or stale.name.count("_") == 2):
            stale.unlink()
    funded = run(tasks, cache, a.workers, a.force, f"{name} base curve plus spread")
    funded = [r for r in funded if r["ok"]]
    same = {r["date"] for r in funded}
    if a.from_date:
        same = {d for d in same if d >= date.fromisoformat(a.from_date)}
        funded = [r for r in funded if r["date"] in same]
    before = tested_rows([r for r in good if r["date"] in same])
    after = tested_rows(funded)

    print(f"\ncall minus put implied vol on the SAME {len(same)} days (vol points).")
    print("gap = mean weighted by quote tightness, se = standard error across days allowing")
    print("a day to resemble the days before it (Newey-West), se_iid = if days were independent,")
    print("plain = unweighted mean, fwd_bp = market forward minus the model's:")
    cmp = groups(before).join(groups(after), lsuffix="_base", rsuffix="_funded")
    print(cmp.drop(columns=["days_funded"]).round(2).to_string())
    if a.summary_json:
        est = pd.Series([r["funding_spread"] for r in sorted(good, key=lambda r: r["date"])
                         if r["funding_spread"] is not None]) * 1e4
        Path(a.summary_json).write_text(json.dumps({
            "variant": tag.strip("_") or "base", "days": len(same),
            "first": str(min(same)), "last": str(max(same)),
            "spread_mean_bp": float(est.mean()),
            "spread_se_bp": float(mean_std_error(est)),
            "applied_min_bp": float(u.min()), "applied_max_bp": float(u.max()),
            "groups": {g: {k: (None if pd.isna(v) else float(v)) for k, v in row.items()}
                       for g, row in cmp.to_dict(orient="index").items()}}, indent=1),
            encoding="utf-8")

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
