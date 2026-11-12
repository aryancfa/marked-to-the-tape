"""
wrds_prices.py — daily share, ETF and index history from Compustat on WRDS.

    .venv/Scripts/python scripts/wrds_prices.py 2014-01-01

This is the history the hedge ratios and the residual analysis need (see
docs/work-list.md, section 2): daily closes, cash dividends by ex-date and
split factors for the Toronto listing of every name in the universe, the
three ETFs, and the TSX 60 index, which stands in for the SXF future.

Saves under data/raw/wrds/ (gitignored, never committed, never redistributed):
  prices_<TICKER>.csv   one row per trading day for one Toronto listing
  index_TSX60.csv       the index level by day

It prints row counts, date ranges and column names only, never rows. The
login comes from your pgpass file and the username from WRDS_USERNAME.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/wrds"
LIB = "comp_na_daily_all"

# Compustat writes a Toronto listing's ticker with a trailing dot
UNIVERSE = ["RY", "TD", "BNS", "BMO", "CM", "MFC", "CNQ", "SU", "ENB", "CNR", "BCE",
            "ABX", "SHOP", "XIU", "ZEB", "XEG"]
TSX60 = "118341"
# columns wanted, if the table has them: close, high, low, open, volume, the
# cash dividend on its ex-date, the cumulative split factor, the total-return
# factor and the currency
WANT = ["datadate", "tic", "prccd", "prchd", "prcld", "prcod", "cshtrd", "div", "divd",
        "divsp", "ajexdi", "trfd", "curcdd", "exchg"]


def main(start: date) -> None:
    import pandas as pd
    import wrds
    user = os.environ.get("WRDS_USERNAME")
    db = wrds.Connection(wrds_username=user) if user else wrds.Connection()
    OUT.mkdir(parents=True, exist_ok=True)
    today = date.today()
    have = set(db.describe_table(library=LIB, table="secd")["name"])
    cols = [c for c in WANT if c in have]
    print(f"secd columns used: {cols}")
    print(f"wanted but absent: {[c for c in WANT if c not in have]}")

    tics = ", ".join(f"'{t}.'" for t in UNIVERSE)
    ids = db.raw_sql(f"select gvkey, iid, tic from {LIB}.security "
                     f"where tic in ({tics}) and excntry = 'CAN'")
    for t in UNIVERSE:
        row = ids[ids["tic"] == t + "."]
        if row.empty:
            print(f"{t}: no Toronto listing found")
            continue
        g, i = row["gvkey"].iloc[0], row["iid"].iloc[0]
        df = db.raw_sql(
            f"select {', '.join(cols)} from {LIB}.secd where gvkey = '{g}' and iid = '{i}' "
            f"and datadate between '{start}' and '{today}' order by datadate")
        df.to_csv(OUT / f"prices_{t}.csv", index=False)
        # a name that has never paid comes back with an empty, untyped column
        paid = int((pd.to_numeric(df["div"], errors="coerce") > 0).sum()) if "div" in df else -1
        print(f"prices_{t}.csv: {len(df)} days, {df['datadate'].min()} to "
              f"{df['datadate'].max()}, dividend days {paid}, "
              f"currency {sorted(df['curcdd'].dropna().unique()) if 'curcdd' in df else '?'}")

    idx_cols = set(db.describe_table(library=LIB, table="idx_daily")["name"])
    keep = [c for c in ("datadate", "prccd", "prccddiv", "prchd", "prcld") if c in idx_cols]
    df = db.raw_sql(f"select {', '.join(keep)} from {LIB}.idx_daily where gvkeyx = '{TSX60}' "
                    f"and datadate between '{start}' and '{today}' order by datadate")
    df.to_csv(OUT / "index_TSX60.csv", index=False)
    print(f"index_TSX60.csv: {len(df)} days, {df['datadate'].min()} to "
          f"{df['datadate'].max()}, columns {keep}")
    db.close()


if __name__ == "__main__":
    main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2014, 1, 1))
