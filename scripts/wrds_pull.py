"""
wrds_pull.py — download one New York listing from OptionMetrics (build sheet B8).

    .venv/Scripts/python scripts/wrds_pull.py RY 109722 2025-06-02 2025-08-29

Saves, under data/raw/wrds/ (gitignored, never committed, never redistributed):
  opprcd_<TICKER>_<start>_<end>.csv   every option row for the security
  secprd_<TICKER>_<start>_<end>.csv   the share's daily prices
  distrd_<TICKER>.csv                 every distribution on record (dividends, splits)
  zerocd_<start>_<end>.csv            the zero-coupon rate curve they price with

It prints row counts and column names only, never rows.

The login comes from your pgpass file. The username is read from the
environment variable WRDS_USERNAME; if it is not set the WRDS library asks
for it. No credential is written here or anywhere in git (hard rule 3).
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/wrds"
LIB = "optionm"


def main(ticker: str, secid: int, start: date, end: date) -> None:
    import wrds
    user = os.environ.get("WRDS_USERNAME")
    db = wrds.Connection(wrds_username=user) if user else wrds.Connection()
    OUT.mkdir(parents=True, exist_ok=True)
    span = f"{start:%Y%m%d}_{end:%Y%m%d}"

    def save(name: str, sql: str) -> None:
        df = db.raw_sql(sql)
        path = OUT / name
        df.to_csv(path, index=False)
        print(f"{name}: {len(df)} rows; columns {list(df.columns)}")

    between = f"date between '{start}' and '{end}'"
    for year in range(start.year, end.year + 1):
        tag = span if start.year == end.year else f"{year}"
        save(f"opprcd_{ticker}_{tag}.csv",
             f"select * from {LIB}.opprcd{year} where secid = {secid} and {between}")
        save(f"secprd_{ticker}_{tag}.csv",
             f"select * from {LIB}.secprd{year} where secid = {secid} and {between}")
    save(f"distrd_{ticker}.csv", f"select * from {LIB}.distrd where secid = {secid}")
    save(f"zerocd_{span}.csv", f"select * from {LIB}.zerocd where {between}")
    db.close()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), date.fromisoformat(sys.argv[3]),
         date.fromisoformat(sys.argv[4]))
