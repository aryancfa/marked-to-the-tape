"""
wrds_usd_dividends.py — a company's dividends as DECLARED, in US dollars (A4).

    .venv/Scripts/python scripts/wrds_usd_dividends.py ABX

Barrick declares its dividend in US dollars. Compustat's Toronto listing
shows each one converted to Canadian dollars on the day, so a schedule built
from it repeats last year's exchange rate along with last year's dividend.
The same company's New York listing shows the amount as declared. This pulls
that: every cash dividend on its ex-date, with the currency.

Saves data/raw/wrds/usd_dividends_<TICKER>.csv (gitignored). Prints counts,
dates and currencies only. The login comes from your pgpass file and the
username from WRDS_USERNAME.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/wrds"
LIB = "comp_na_daily_all"


def main(tickers: list[str]) -> None:
    import wrds
    user = os.environ.get("WRDS_USERNAME")
    db = wrds.Connection(wrds_username=user) if user else wrds.Connection()
    for t in tickers:
        home = db.raw_sql(f"select gvkey from {LIB}.security where tic = '{t}.' "
                          "and excntry = 'CAN'")
        if home.empty:
            print(f"{t}: no Toronto listing found")
            continue
        g = home["gvkey"].iloc[0]
        us = db.raw_sql(f"select iid, tic, exchg from {LIB}.security where gvkey = '{g}' "
                        "and excntry = 'USA'")
        if us.empty:
            print(f"{t}: no United States listing found")
            continue
        print(f"{t}: United States listings {sorted(zip(us['tic'], us['exchg']))}")
        frames = []
        for iid in us["iid"]:
            frames.append(db.raw_sql(
                f"select datadate, tic, div, curcdd, exchg from {LIB}.secd where gvkey = '{g}' "
                f"and iid = '{iid}' and datadate >= '2024-01-01' and div > 0 order by datadate"))
        import pandas as pd
        df = pd.concat(frames).sort_values("datadate")
        df.to_csv(OUT / f"usd_dividends_{t}.csv", index=False)
        print(f"usd_dividends_{t}.csv: {len(df)} dividends, {df['datadate'].min()} to "
              f"{df['datadate'].max()}, currency {sorted(df['curcdd'].dropna().unique())}")
    db.close()


if __name__ == "__main__":
    main([a.upper() for a in sys.argv[1:]] or ["ABX"])
