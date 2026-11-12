"""
mark_benchmark.py — do the mark checks catch a bad quote, and do they beat a plain rule?

    .venv/Scripts/python scripts/mark_benchmark.py              # about ten minutes
    .venv/Scripts/python scripts/mark_benchmark.py --days 12    # quick look

MADE-UP DATA ONLY. A share and its option chain are invented
(scripts/demo_synthetic.py), with quotes about as wide and as noisy as a
liquid Montreal name's. Bad quotes of known size are planted at random:
on any day, about one quote in fifty has its vol pushed up or down by 0.5,
1, 2 or 4 vol points. Because the truth is known, every rule can be scored.

The rules compared
------------------
  smile            this project's check: the quote's vol against a curve
                   fitted through the OTHER strikes of its expiry, failed
                   beyond one quote bar (and the solver's noise floor)
  smile or pair    the same, or its call/put partner disagrees. The pair
                   check cannot say which side is wrong, so it flags both.
  day-on-day       the plain rule a mark review often starts with: the
                   quote's vol moved more than a threshold since yesterday
  day-on-day, net  the same after taking out the day's median move across
                   the chain, so a market-wide vol move is not flagged

Scored on two markets: one where the vol level never moves, and one where
the whole surface shifts by about one vol point a day, as it really does.
'Caught' is the share of planted quotes flagged, by planted size. 'False
alarms' is flags per 1,000 clean quotes; a day-on-day rule also fires on
the day AFTER a bad quote, when the mark comes back, and that good mark
counts against it.

If the checks did not beat the plain rule the table would say so.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import pandas as pd

import demo_synthetic as demo
from loaders.mx import load_mx_options, load_mx_underlying
from rates import flat_curve
from references import PAIR, SMILE, reference_differences
from replay import fails
from surface import analyse_day

SIZES = (0.5, 1.0, 2.0, 4.0)            # vol points
RATE_PLANTED = 0.02                     # share of quotes made bad on any day
HALF_SPREAD, MID_NOISE = 0.12, 0.02     # dollars: quote bars of roughly 0.3 to 1.5 vol points
THRESHOLDS = (0.5, 1.0)                 # vol points, for the day-on-day rules


def planter(seed: int):
    """A reproducible plan of which quotes are bad, decided quote by quote."""
    def plant(i, j, K, cp):
        rng = np.random.default_rng([seed, i, j, int(K * 100), cp == "C"])
        if rng.random() >= RATE_PLANTED:
            return 0.0
        return float(rng.choice(SIZES) * rng.choice((-1.0, 1.0))) / 100.0
    return plant


def one_day(task):
    """Runs in a worker: one day's vols and reference differences."""
    opts, mid, divs = task
    day = opts["date"].iloc[0].date()
    # the carry is known here (the made-up file's own rate and funding), so
    # what is tested is the mark checks and nothing else
    res = analyse_day(opts, mid, flat_curve(day, demo.RATE + demo.FUNDING), divs)
    if res.inverted is None:
        return None
    inv = res.inverted
    d = reference_differences(inv, res.spot, divs=divs)
    key = ["expiry", "strike"]
    iv = inv[["date", "expiry", "strike", "call_put", "iv", "half_spread_vol"]].copy()
    iv["expiry"] = [e.date() if hasattr(e, "date") else e for e in iv["expiry"]]
    sm = d[d["reference"] == SMILE].rename(columns={"side": "call_put", "diff": "smile"})
    iv = iv.merge(sm[key + ["call_put", "smile"]], on=key + ["call_put"], how="left")
    pr = d[d["reference"] == PAIR].rename(columns={"diff": "pair", "quote_bar": "pair_bar"})
    iv = iv.merge(pr[key + ["pair", "pair_bar"]], on=key, how="left")
    iv["front"] = iv["expiry"] == res.front       # the expiry the share price came from
    return iv


def run_market(days: int, vol_shock_sd: float, seed: int, workers: int) -> pd.DataFrame:
    plant = planter(seed)
    with tempfile.TemporaryDirectory() as tmp:
        path, _, divs = demo.make_file(Path(tmp), date(2026, 3, 2), days, seed=seed,
                                       plant=plant, vol_shock_sd=vol_shock_sd,
                                       half_spread=HALF_SPREAD, mid_noise=MID_NOISE)
        opts, und = load_mx_options(path), load_mx_underlying(path).set_index("date")
    stamps = sorted(set(opts["date"]))
    tasks = [(opts[opts["date"] == ts], float(und.loc[ts, "mid"]), divs) for ts in stamps]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        frames = [f for f in pool.map(one_day, tasks) if f is not None]
    q = pd.concat(frames, ignore_index=True)
    order = {ts: i for i, ts in enumerate(stamps)}
    expiries = sorted(set(pd.to_datetime(opts["expiry"]).dt.date))
    q["i"] = q["date"].map(order)
    q["planted"] = [plant(i, expiries.index(e), K, cp) * 100.0
                    for i, e, K, cp in zip(q["i"], q["expiry"], q["strike"], q["call_put"])]
    q = q.sort_values(["expiry", "strike", "call_put", "i"])
    g = q.groupby(["expiry", "strike", "call_put"])
    q["move"] = (q["iv"] - g["iv"].shift(1)) * 100.0
    q.loc[g["i"].diff() != 1, "move"] = np.nan          # only a true next-day move counts
    q["reversal"] = (g["planted"].shift(1).fillna(0.0) != 0.0) & (q["planted"] == 0.0)
    q["net_move"] = q["move"] - q.groupby("i")["move"].transform("median")
    bar = q["half_spread_vol"] * 100.0
    q["smile_flag"] = [fails(s, b) for s, b in zip(q["smile"], bar)]
    q["either_flag"] = q["smile_flag"] | np.array(
        [fails(p, b) for p, b in zip(q["pair"], q["pair_bar"])]) & ~q["front"]
    for t in THRESHOLDS:
        q[f"move_{t}"] = q["move"].abs() > t
        q[f"net_{t}"] = q["net_move"].abs() > t
    return q[q["move"].notna()]                         # every rule judged on the same quotes


RULES = [("smile", "smile_flag"), ("smile or pair", "either_flag")] \
    + [(f"day-on-day > {t:g}", f"move_{t}") for t in THRESHOLDS] \
    + [(f"day-on-day, net > {t:g}", f"net_{t}") for t in THRESHOLDS]


def score(q: pd.DataFrame) -> pd.DataFrame:
    bad, clean = q[q["planted"] != 0.0], q[q["planted"] == 0.0]
    rows = []
    for label, col in RULES:
        row = {"rule": label}
        for s in SIZES:
            hit = bad[bad["planted"].abs() == s]
            row[f"caught, {s:g} pt %"] = 100.0 * hit[col].mean() if len(hit) else np.nan
        row["caught, all %"] = 100.0 * bad[col].mean()
        row["false alarms per 1,000"] = 1000.0 * clean[col].mean()
        row["of which the day after"] = 1000.0 * (clean[col] & clean["reversal"]).sum() / len(clean)
        flagged = int(q[col].sum())
        row["flags that are real %"] = 100.0 * bad[col].sum() / flagged if flagged else np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index("rule")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    say("Made-up data: an invented share and option chain with bad quotes planted at random.")
    say(f"About {100 * RATE_PLANTED:.0f}% of quotes are bad on any day, by 0.5, 1, 2 or 4 vol "
        "points, up or down.")
    for label, sd in (("VOL LEVEL NEVER MOVES", 0.0),
                      ("VOL LEVEL MOVES ABOUT ONE POINT A DAY", 0.01)):
        q = run_market(a.days, sd, a.seed, a.workers)
        say()
        say(f"{label}: {len(q)} quotes over {q['i'].nunique()} days, {int((q['planted'] != 0).sum())} "
            f"of them planted; median quote bar {np.median(q['half_spread_vol']) * 100:.2f} vol "
            f"points; quotes the smile could not judge: {int(q['smile'].isna().sum())}")
        say(score(q).round(1).to_string())
    say()
    say("caught = planted quotes flagged; false alarms = flags per 1,000 clean quotes;")
    say("the day after = a good mark flagged because yesterday's was bad;")
    say("flags that are real = of everything the rule flags, the share that was planted.")
    out = ROOT / "reports" / "mark_benchmark.txt"
    out.write_text("Made-up data only: nothing here comes from a market.\n\n"
                   + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nsaved: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
