"""
index_funding.py — what the index future says about funding (build sheet B5).

    .venv/Scripts/python scripts/index_funding.py

RY's and XIU's funding spreads are fitted from their own options. The standard
source for an index's funding is the index itself, and this script reads it
two ways from the exchange's files:

  1. The SXF basis. A future is the index for later delivery, so
         future / index = exp((rate + funding spread - dividend yield) * T).
     The rate comes from the CORRA futures curve. What is left,
     ln(future / index) / T - rate, is the funding spread MINUS the dividend
     yield over the contract's life. Only contracts with open interest above
     1,000 and more than 30 days to run are used: nearer than that, the few
     basis points of noise between the index's close and the future's
     settlement are divided by a very short time.

  2. SXO parity. SXO is European, so call minus put at one strike gives the
     forward with no model. The exchange's SXO file carries SETTLEMENTS ONLY
     (no bid, ask, volume or open interest), so this forward is the one the
     exchange's settlement procedure assumed, not one a market traded at.

THE LIMIT, stated before the numbers. The basis gives funding minus dividend
yield, and the two cannot be separated without the index's dividends over the
contract's life. The index total-return series is not on disk. The split
below uses XIU's distributions over the last twelve months as a stand-in for
the index's yield, and every 10 basis points of error in that yield is 10
basis points of error in the spread. Dividends are also seasonal, which a
twelve-month average ignores. Treat the spread as a range, not a number.

Prints DERIVED numbers only and writes them to reports/index_funding_numbers.txt.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd

from loaders.mx import load_mx_futures, load_mx_options
from rates import load_base_curve
from surface import mean_std_error
from voleng import implied_forward_european

MX, BOC, WRDS = ROOT / "data/raw/mx", ROOT / "data/raw/boc", ROOT / "data/raw/wrds"
MIN_OPEN_INTEREST, MIN_DAYS = 1000, 30
YIELD_ERROR_BP = 30.0       # how wrong the stand-in yield could plausibly be


def main() -> None:
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text)
        lines.append(text)

    fut = load_mx_futures(sorted(MX.glob("quote_SXF_*.csv")))
    cra = load_mx_futures(sorted(MX.glob("quote_CRA_*.csv")))
    cra = [(d.date(), e.date(), float(px))
           for d, e, px in zip(cra["date"], cra["expiry"], cra["settle"])]
    idx = pd.read_csv(WRDS / "index_TSX60.csv", parse_dates=["datadate"]).set_index(
        "datadate")["prccd"]
    xiu = pd.read_csv(WRDS / "prices_XIU.csv", parse_dates=["datadate"]).set_index("datadate")
    xiu["div"] = pd.to_numeric(xiu["div"], errors="coerce").fillna(0.0)
    last = min(fut["date"].max(), xiu.index.max())
    year = xiu.loc[last - pd.DateOffset(days=364):last]
    yld = float(year["div"].sum() / year["prccd"].mean())

    def rate(day, T):
        try:
            return load_base_curve(BOC, day.date(), cra).rate(T)
        except Exception:
            return None

    say("Derived statistics only. No prices appear in this file.")
    say()
    say(f"Index funding from the exchange's SXF and SXO files, "
        f"{fut['date'].min().date()} to {fut['date'].max().date()}.")
    say(f"Stand-in for the index's dividend yield: XIU's distributions over the twelve months "
        f"to {last.date()}, {yld * 100:.2f}% a year.")

    rows = []
    for (day, exp), g in fut.groupby(["date", "expiry"]):
        q = g.iloc[0]
        days = (exp - day).days
        if (days <= MIN_DAYS or day not in idx.index or not np.isfinite(q["settle"])
                or q["open_interest"] < MIN_OPEN_INTEREST):
            continue
        r = rate(day, days / 365.0)
        if r is None:
            continue
        net = (np.log(q["settle"] / idx[day]) / (days / 365.0) - r) * 1e4
        rows.append(dict(date=day, expiry=exp.date(), days=days, net=net))
    d = pd.DataFrame(rows).sort_values("date")

    say()
    say("1. SXF basis: ln(future / index) / T minus the CORRA futures rate, in basis points a")
    say("   year. This is the funding spread MINUS the dividend yield. The standard error")
    say("   allows a day to resemble the days before it.")
    table = []
    for exp, g in d.groupby("expiry"):
        m, se = g["net"].mean(), mean_std_error(g["net"])
        table.append({"contract": exp, "days": len(g),
                      "days to expiry": f"{g['days'].min()}-{g['days'].max()}",
                      "spread minus yield": m, "std_error": se, "sd": g["net"].std(),
                      "spread at the stand-in yield": m + yld * 1e4})
    m, se = d["net"].mean(), mean_std_error(d["net"])
    table.append({"contract": "all", "days": len(d), "days to expiry": "",
                  "spread minus yield": m, "std_error": se, "sd": d["net"].std(),
                  "spread at the stand-in yield": m + yld * 1e4})
    say(pd.DataFrame(table).round(1).to_string(index=False))
    centre = m + yld * 1e4
    say(f"   Read as a range: {centre - YIELD_ERROR_BP:+.0f} to {centre + YIELD_ERROR_BP:+.0f} "
        f"bp a year, taking the stand-in yield as good to {YIELD_ERROR_BP:.0f} bp. The "
        f"statistical error ({se:.0f} bp) is small beside that.")
    say("   For comparison: RY's options imply 17.7 bp (standard error 2.0) and XIU's own")
    say("   13.1 (4.2), both over the same CORRA futures curve.")

    opt = load_mx_options(sorted(MX.glob("quote_SXO_*.csv")))
    opt = opt[np.isfinite(opt["settle"]) & (opt["settle"] > 0)]
    out = []
    for (day, exp), g in opt.groupby(["date", "expiry"]):
        f = fut[(fut["date"] == day) & (fut["expiry"] == exp)]
        days = (exp - day).days
        if f.empty or day not in idx.index or days <= MIN_DAYS:
            continue
        T = days / 365.0
        r = rate(day, T)
        if r is None:
            continue
        c = g[g["call_put"] == "C"].set_index("strike")["settle"]
        p = g[g["call_put"] == "P"].set_index("strike")["settle"]
        both = c.index.intersection(p.index)
        both = both[np.abs(both / idx[day] - 1.0) < 0.03]
        if len(both) < 2:
            continue
        fw = float(np.mean([implied_forward_european(c[k], p[k], k, T, r) for k in both]))
        sxf = float(f["settle"].iloc[0])
        out.append(dict(date=day, expiry=exp.date(), T=T,
                        vs_future=(fw / sxf - 1.0) * 1e4,
                        vs_no_dividend=(fw / (idx[day] * np.exp(r * T)) - 1.0) * 1e4,
                        dividends=yld * T * 1e4))
    o = pd.DataFrame(out).sort_values("date")
    say()
    say("2. SXO parity: the forward implied by the exchange's SETTLEMENTS for calls and puts")
    say("   within 3% of the index, against the SXF settlement for the same expiry, and")
    say("   against the index grown at the rate with NO dividends, in basis points:")
    by = o.groupby("expiry").agg(expiry_days=("vs_future", "size"),
                                 above_future=("vs_future", "mean"),
                                 above_no_dividend_forward=("vs_no_dividend", "mean"),
                                 dividends_at_stand_in_yield=("dividends", "mean"))
    say(by.round(1).to_string())
    say(f"   All {len(o)} expiry-days: the parity forward sits {o['vs_future'].mean():+.1f} bp "
        f"above the future (sd {o['vs_future'].std():.1f}) and "
        f"{o['vs_no_dividend'].mean():+.1f} from a forward with no dividends "
        f"(sd {o['vs_no_dividend'].std():.1f}).")
    say("   If the parity forward matches the no-dividend forward and sits above the future by")
    say("   about the dividends due, the SXO settlements were struck without the index's")
    say("   dividends. They are the exchange's model, and are not used for funding here.")

    path = ROOT / "reports" / "index_funding_numbers.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nnumbers: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
