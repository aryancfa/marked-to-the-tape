"""
demo_synthetic.py — the whole front-chart method on MADE-UP data, start to finish.

    .venv/Scripts/python scripts/demo_synthetic.py            # about a minute
    .venv/Scripts/python scripts/demo_synthetic.py --days 30

Nothing licensed is needed: no exchange files, no WRDS, no Bank of Canada
files. The script invents a share, prices its options with the engine under
inputs it knows, writes them out in the exchange's file layout, and then runs
the real pipeline on that file as if it had been downloaded. Because the
truth was planted, the output can be marked right or wrong.

What is planted, and what the method has to find WITHOUT being told
-------------------------------------------------------------------
  1. a funding cost of 25 basis points a year above the rate curve
       -> the call-put gap must grow with maturity, and the spread fitted
          from it must come back near 25
  2. the share price at the moment of the quotes, which the file's own
     "last" misstates by a few basis points
       -> the price implied from the options must sit closer to the truth
  3. one quote 2 vol points out of line with its neighbours, every day
       -> the smile reference must single that strike out
  4. on the second pass, with the fitted spread applied (from EARLIER days
     only), calls and puts must agree

It is also the project's end-to-end regression test (tests/test_demo.py runs
a short version), and the example to read to see how the pieces fit.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd

from loaders.mx import load_mx_options, load_mx_underlying
from rates import flat_curve
from references import PAIR, SMILE, pool_buckets, reference_differences
from surface import analyse_day, dividend_times, mean_std_error, trailing_spread
from tradingcal import TSX, is_monthly_expiry, trading_days, year_fraction
from voleng import crr_american

NAME = "ZZZ"
RATE, FUNDING = 0.027, 0.0025           # the curve, and the planted spread above it
HALF_SPREAD, MID_NOISE = 0.03, 0.002    # dollars
BAD_STRIKE, BAD_BY = 104.0, 0.02        # the planted bad quote: +2 vol points
STRIKES = (92.0, 94.0, 96.0, 98.0, 100.0, 102.0, 104.0, 106.0, 108.0)


def true_vol(strike: float, spot: float, T: float) -> float:
    k = np.log(strike / spot)
    return 0.20 - 0.30 * k + 0.9 * k * k + 0.02 * np.sqrt(T)


def make_file(folder: Path, first: date, days: int, seed: int = 7, plant=None,
              vol_shock_sd: float = 0.0, half_spread: float = HALF_SPREAD,
              mid_noise: float = MID_NOISE) -> tuple[Path, dict, list]:
    """Write one made-up exchange file. Returns (path, true share price by
    day, dividend schedule). Every number in it is invented.

    `plant(day_number, expiry_number, strike, "C" or "P")` returns the vol to
    add to that quote (0.0 for none); by default the one fixed bad quote in
    the module note. `vol_shock_sd` moves the whole surface's level by a
    random amount each day (in vol, 0.01 = one point), as real vol does.
    Both exist for scripts/mark_benchmark.py."""
    rng = np.random.default_rng(seed)
    if plant is None:
        def plant(i, j, K, cp):
            return BAD_BY if (K == BAD_STRIKE and cp == "C" and j == 2) else 0.0
    level = 0.0
    sessions = trading_days(TSX, first, first + timedelta(days=3 * days + 10))[:days]
    ex_date = sessions[-1] + timedelta(days=40)
    while ex_date not in set(trading_days(TSX, ex_date, ex_date)):
        ex_date += timedelta(days=1)
    divs = [(ex_date, 0.60)]
    horizon = trading_days(TSX, sessions[-1] + timedelta(days=8),
                           sessions[-1] + timedelta(days=330))
    expiries = [d for d in horizon if is_monthly_expiry(d)][:5]
    spot, truth, rows = 100.0, {}, []
    for i, day in enumerate(sessions):
        spot *= float(np.exp(rng.normal(0.0, 0.008)))
        if vol_shock_sd:
            level = float(np.clip(level + rng.normal(0.0, vol_shock_sd), -0.08, 0.15))
        truth[day] = spot
        last = spot * (1.0 + rng.normal(0.0, 6e-4))          # the tape is a little stale
        rows.append({"Date": day.isoformat(), "Symbol": NAME, "Underlying Symbol": NAME,
                     "Strike Price": 0.0, "Expiry Date": "", "Call/Put": "",
                     "Ins. Type": 3, "Bid Price": round(spot - 0.05, 2),
                     "Ask Price": round(spot + 0.05, 2), "Last Price": round(last, 2),
                     "Volume": 100000, "Settlement Price": 0.0, "Open Interest": 0,
                     "Implied Volatility": 0.0})
        for j, e in enumerate(expiries):
            T = year_fraction(day, e)
            dts = dividend_times(day, divs, e)
            for K in STRIKES:
                for cp in "CP":
                    vol = true_vol(K, spot, T) + level + plant(i, j, K, cp)
                    px = crr_american(spot, K, T, RATE + FUNDING, 0.0, vol, dts, cp == "C")
                    mid = px + rng.normal(0.0, mid_noise)
                    rows.append({
                        "Date": day.isoformat(),
                        "Symbol": f"{NAME}{e:%y%m%d}{cp}{K:.2f}",
                        "Underlying Symbol": NAME, "Strike Price": K,
                        "Expiry Date": e.isoformat(), "Call/Put": 0 if cp == "C" else 1,
                        "Ins. Type": 0, "Bid Price": round(max(mid - half_spread, 0.01), 3),
                        "Ask Price": round(mid + half_spread, 3), "Last Price": 0.0,
                        "Volume": int(rng.integers(0, 3) == 0) * 10,
                        "Settlement Price": round(px, 3), "Open Interest": 50,
                        "Implied Volatility": round(vol * 100.0, 2)})
    path = folder / f"quote_{NAME}_synthetic.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path, truth, divs


def run(days: int = 12, min_history: int = 5, verbose: bool = True) -> dict:
    """Make the file, run the pipeline on it, and return what was recovered."""
    say = print if verbose else (lambda *a, **k: None)
    with tempfile.TemporaryDirectory() as tmp:
        path, truth, divs = make_file(Path(tmp), date(2026, 3, 2), days)
        opts, und = load_mx_options(path), load_mx_underlying(path).set_index("date")
    say(f"made-up file: {len(opts)} option rows over {days} days, read by the exchange loader")

    base, estimates = {}, {}
    for ts in sorted(set(opts["date"])):
        day = ts.date()
        res = analyse_day(opts[opts["date"] == ts], float(und.loc[ts, "mid"]),
                          flat_curve(day, RATE), divs)
        base[day], estimates[day] = res, res.funding_spread
    est = pd.Series([s for s in estimates.values() if s is not None]) * 1e4
    spot_err = pd.Series({d: (r.spot / truth[d] - 1.0) * 1e4 for d, r in base.items()})
    tape_err = pd.Series({ts.date(): (float(und.loc[ts, "last"]) / truth[ts.date()] - 1.0) * 1e4
                          for ts in und.index})

    funded, diffs = {}, []
    for day, res in base.items():
        s = trailing_spread(estimates, day, min_history)
        if s is None:
            continue
        ts = pd.Timestamp(day)
        again = analyse_day(opts[opts["date"] == ts], float(und.loc[ts, "mid"]),
                            flat_curve(day, RATE).plus(s), divs)
        funded[day] = again
        d = reference_differences(again.inverted, again.spot, divs=divs)
        diffs.append(d[d["expiry"] != again.front])
    all_diffs = pd.concat(diffs, ignore_index=True)
    gap_before = float(np.mean([r.summary["weighted_gap"].iloc[-1] for d, r in base.items()
                                if d in funded]))
    gap_after = float(np.mean([r.summary["weighted_gap"].iloc[-1] for r in funded.values()]))
    smile = all_diffs[all_diffs["reference"] == SMILE]
    bad = smile[(smile["strike"] == BAD_STRIKE) & (smile["side"] == "C")]
    worst = smile.groupby(["expiry", "strike", "side"])["diff"].mean().abs().idxmax()
    rest = smile.drop(bad.index)["diff"].abs().median()
    # the planted quote shows in call minus put at its own strike too, so that
    # strike is left out when asking whether calls and puts otherwise agree
    clean = all_diffs[~((all_diffs["reference"] == PAIR) & (all_diffs["strike"] == BAD_STRIKE))]
    pooled = pool_buckets(clean, min_days=2)
    pair = pooled[pooled["reference"] == PAIR]

    out = dict(days=days, funded_days=len(funded),
               spread_planted_bp=FUNDING * 1e4, spread_found_bp=float(est.mean()),
               spread_se_bp=float(mean_std_error(est)),
               implied_spot_error_bp=float(spot_err.abs().mean()),
               tape_error_bp=float(tape_err.abs().mean()),
               long_gap_before=gap_before, long_gap_after=gap_after,
               bad_quote_planted=BAD_BY * 100, bad_quote_found=float(bad["diff"].max()),
               worst_strike=float(worst[1]), other_quotes_median=float(rest),
               largest_pair_cell=float(pair["mean"].abs().max()))
    say()
    say("PLANTED                                   FOUND")
    say(f"funding spread {out['spread_planted_bp']:.0f} bp a year              "
        f"{out['spread_found_bp']:.1f} bp (standard error {out['spread_se_bp']:.1f})")
    say(f"true share price (tape off by {out['tape_error_bp']:.1f} bp)      "
        f"implied price off by {out['implied_spot_error_bp']:.1f} bp")
    say(f"one call {out['bad_quote_planted']:.0f} vol points high, strike {BAD_STRIKE:.0f}   "
        f"strike {out['worst_strike']:.0f} flagged, {out['bad_quote_found']:+.2f} against the "
        f"smile (other quotes: {out['other_quotes_median']:.2f})")
    say(f"longest expiry, calls minus puts          {out['long_gap_before']:+.2f} vol points "
        f"with no spread, {out['long_gap_after']:+.2f} with the spread from earlier days")
    say(f"largest call-put cell after the spread    {out['largest_pair_cell']:.2f} vol points "
        "(the planted strike left out)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=12)
    run(ap.parse_args().days)
