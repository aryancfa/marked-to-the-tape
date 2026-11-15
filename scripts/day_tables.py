"""
day_tables.py — every day and every expiry of the front chart, as tables the
live page can open one at a time (build sheet C9: "any day or expiry
reachable from the demo in under a minute").

    .venv/Scripts/python scripts/day_tables.py            # every name
    .venv/Scripts/python scripts/day_tables.py RY XIU     # some

Reads the cache scripts/run_days.py wrote (the same pass front_chart.py
draws) and writes, per name, under reports/days/:

  <name>_days.csv      one row per day: rows, how many were inverted, every
                       rejection reason with its count, the evidence split
                       (traded / quoted / settlement only), and the funding
                       spread applied with where it came from.
  <name>_expiries.csv  one row per day and expiry: days to expiry, what the
                       carry inputs rest on, the same counts, the median quote
                       bar, and each reference's mean difference in VOL POINTS,
                       over the expiry and by moneyness bucket.

What is left out, and why (hard rule 1: derived statistics only)
-----------------------------------------------------------------
No strike, no share price and no vol level. A row by strike would carry both
the strike and its moneyness, and their ratio IS the share price the options
imply on a named day. So differences are shown by moneyness BUCKET (the front
chart's own cells), never by strike. The day's implied share price against
the file's last trade, in basis points, is also left out: with a public last
trade it gives the model's share price back. Its distribution over days is
in each name's numbers report.

The expiry the share price was implied from is labelled "calibrated" and its
call-minus-put figures are left blank: they are zero by construction, as on
the front chart.
"""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from references import EXCHANGE, M_EDGES, PAIR, SMILE, reference_differences

OUT = ROOT / "reports" / "days"
NAMES = ["RY", "TD", "BNS", "BMO", "MFC", "CNQ", "SU", "ENB", "CNR", "BCE", "ABX", "SHOP",
         "XIU", "ZEB", "XEG"]
TIERS = ("traded", "quoted", "settlement_only", "no_price")   # over ALL rows, as the front chart counts
# (column prefix, reference, side): the four panels of the front chart
PANELS = (("pair", PAIR, "pair"), ("smile", SMILE, None),
          ("exch_call", EXCHANGE, "C"), ("exch_put", EXCHANGE, "P"))
BUCKETS = [f"{a:.2f}-{b:.2f}" for a, b in zip(M_EDGES, M_EDGES[1:])]
CALIBRATED = "calibrated"


def bucket_of(moneyness: pd.Series) -> pd.Series:
    return pd.cut(moneyness, list(M_EDGES), labels=BUCKETS).astype(object)


def reasons(series: pd.Series) -> str:
    """'reason n; reason n' for every reason other than ok, largest first."""
    counts = series[series != "ok"].value_counts()
    return "; ".join(f"{k.replace('_', ' ')} {v}" for k, v in counts.items())


def counts(rows: pd.DataFrame) -> dict:
    ok = rows["reason"] == "ok"
    tiers = rows["tier"].value_counts()
    return dict(rows=len(rows), inverted=int(ok.sum()), rejected=int((~ok).sum()),
                rejections=reasons(rows["reason"]),
                **{t: int(tiers.get(t, 0)) for t in TIERS})


def reconcile(day, whole: dict, tally: dict, tiers: dict) -> None:
    """Stop unless the day's counts are the ones the day runner recorded, so
    a day page can never disagree with the front chart's totals."""
    if tally and (whole["rows"], whole["inverted"]) != (sum(tally.values()), tally.get("ok", 0)):
        raise SystemExit(f"{day}: {whole['rows']} rows / {whole['inverted']} inverted here, "
                         f"{sum(tally.values())} / {tally.get('ok', 0)} in the day's tally")
    bad = {t: (whole[t], tiers.get(t, 0)) for t in TIERS if tiers and whole[t] != tiers.get(t, 0)}
    if bad:
        raise SystemExit(f"{day}: evidence counts differ from the day's record: {bad}")


def mean(x: pd.Series) -> float:
    return round(float(x.mean()), 2) if len(x) else np.nan


def one_day(r: dict) -> tuple[dict, list[dict]]:
    """The day's row and its expiry rows, from one cached result."""
    inv = r["inverted"].copy()
    inv["expiry"] = [e.date() if hasattr(e, "date") else e for e in inv["expiry"]]
    diffs = reference_differences(r["inverted"], r["spot"], divs=r["divs"])
    diffs["bucket"] = bucket_of(diffs["moneyness"])
    carry = r["summary"]["carry"].to_dict() if r.get("summary") is not None else {}
    carry = {(k.date() if hasattr(k, "date") else k): v for k, v in carry.items()}
    day = r["date"].date() if hasattr(r["date"], "date") else r["date"]

    whole = counts(inv)
    reconcile(day, whole, r.get("tally") or {}, r.get("tiers") or {})
    spread = r.get("spread_used")
    head = dict(date=day, **whole,
                spread_bp=round(spread * 1e4, 1) if spread is not None else np.nan,
                spread_source=(r.get("spread_source") or "own options, earlier days")
                if spread is not None else "none applied",
                rates=r.get("rate_source") or "")

    rows = []
    for expiry, block in inv.groupby("expiry", sort=True):
        d = diffs[diffs["expiry"] == expiry]
        front = r.get("front")
        front = front.date() if hasattr(front, "date") else front
        label = CALIBRATED if expiry == front else carry.get(expiry, "")
        ok = block[block["reason"] == "ok"]
        row = dict(date=day, expiry=expiry, days=(expiry - day).days, carry=label,
                   **counts(block),
                   quote_bar=round(float(ok["half_spread_vol"].median() * 100.0), 2)
                   if len(ok) else np.nan)
        smile = d[d["reference"] == SMILE]
        row["smile_fit"] = (smile["smile_model"].iloc[0] if len(smile) else "not fitted")
        row["smile_poor_fit"] = bool(smile["poor_fit"].iloc[0]) if len(smile) else False
        for prefix, ref, side in PANELS:
            p = d[d["reference"] == ref]
            if side is not None:
                p = p[p["side"] == side]
            if prefix == "pair" and label == CALIBRATED:
                p = p.iloc[0:0]                     # zero by construction: not evidence
            row[f"{prefix}_n"] = len(p)
            row[f"{prefix}_mean"] = mean(p["diff"])
            for b in BUCKETS:
                row[f"{prefix}_{b}"] = mean(p.loc[p["bucket"] == b, "diff"])
        rows.append(row)
    return head, rows


def one_name(name: str) -> tuple[str, int, int]:
    import front_chart                          # the cache front_chart.py reads
    results = front_chart.load(name)
    days, expiries = [], []
    for r in results:
        head, rows = one_day(r)
        days.append(head)
        expiries += rows
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(days).to_csv(OUT / f"{name.lower()}_days.csv", index=False)
    pd.DataFrame(expiries).to_csv(OUT / f"{name.lower()}_expiries.csv", index=False)
    return name, len(days), len(expiries)


def main(names: list[str]) -> None:
    print("Derived statistics only: counts and differences in vol points by moneyness "
          "bucket.\nNo strike, share price, vol level or price is written.\n")
    with ProcessPoolExecutor() as pool:
        for name, n_days, n_exp in pool.map(one_name, names):
            print(f"{name:5s} {n_days:4d} days, {n_exp:5d} expiry-days -> "
                  f"reports/days/{name.lower()}_days.csv, _expiries.csv")


if __name__ == "__main__":
    main([n.upper() for n in sys.argv[1:]] or NAMES)
