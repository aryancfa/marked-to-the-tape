"""
wrds_ny_volume.py — daily volume of the New York listing of each interlisted name (C6).

    .venv/Scripts/python scripts/wrds_ny_volume.py 2014-01-01

The Toronto side is already on disk (prices_<T>.csv from wrds_prices.py,
column cshtrd). This pulls the same columns for the United States listing of
the same company: same Compustat company key (gvkey), the issue whose
exchange country is the United States. Found by company key, not ticker,
so ticker changes (Barrick became B in New York) and exchange moves (Shopify,
NYSE to Nasdaq) do not lose days.

Saves under data/raw/wrds/ (gitignored, never committed, never redistributed):
  ny_volume_<TICKER>.csv   one row per US trading day: datadate, tic, iid,
                           cshtrd, prccd, curcdd, exchg

Prints row counts, date ranges, issue ids and exchange codes only, never
rows. The login comes from your pgpass file and the username from
WRDS_USERNAME (hard rule 3).
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/wrds"
LIB = "comp_na_daily_all"

# the universe's names that also list in New York
INTERLISTED = ["RY", "TD", "BNS", "BMO", "CM", "MFC", "CNQ", "SU", "ENB", "CNR", "BCE",
               "ABX", "SHOP"]
COLS = ["datadate", "tic", "iid", "cshtrd", "prccd", "curcdd", "exchg"]


def main(start: date) -> None:
    import wrds
    user = os.environ.get("WRDS_USERNAME")
    db = wrds.Connection(wrds_username=user) if user else wrds.Connection()
    OUT.mkdir(parents=True, exist_ok=True)
    today = date.today()
    tics = ", ".join(f"'{t}.'" for t in INTERLISTED)
    can = db.raw_sql(f"select gvkey, iid, tic from {LIB}.security "
                     f"where tic in ({tics}) and excntry = 'CAN'")
    for t in INTERLISTED:
        row = can[can["tic"] == t + "."]
        if row.empty:
            print(f"{t}: no Toronto listing found")
            continue
        g = row["gvkey"].iloc[0]
        us = db.raw_sql(f"select iid, tic, exchg from {LIB}.security "
                        f"where gvkey = '{g}' and excntry = 'USA'")
        if us.empty:
            print(f"{t}: no United States issue under gvkey {g}")
            continue
        iids = ", ".join(f"'{i}'" for i in us["iid"])
        df = db.raw_sql(f"select {', '.join(COLS)} from {LIB}.secd where gvkey = '{g}' "
                        f"and iid in ({iids}) and datadate between '{start}' and '{today}' "
                        "order by datadate, iid")
        df.to_csv(OUT / f"ny_volume_{t}.csv", index=False)
        per = df.groupby("iid")["datadate"].agg(["count", "min", "max"])
        print(f"ny_volume_{t}.csv: {len(df)} rows; issues "
              + "; ".join(f"{i} ({','.join(sorted(set(us.loc[us['iid'] == i, 'tic'])))}, "
                          f"exchg {sorted(set(df.loc[df['iid'] == i, 'exchg'].dropna()))}) "
                          f"{r['count']} days {r['min']} to {r['max']}"
                          for i, r in per.iterrows()))
    db.close()


if __name__ == "__main__":
    main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2014, 1, 1))
