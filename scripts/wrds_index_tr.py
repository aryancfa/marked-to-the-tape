"""
wrds_index_tr.py — look for the TSX 60's total-return series, and pull it (B5).

    .venv/Scripts/python scripts/wrds_index_tr.py            # list candidates only
    .venv/Scripts/python scripts/wrds_index_tr.py 000000     # pull that gvkeyx

Why: the SXF basis gives the funding spread MINUS the index's dividend yield
(scripts/index_funding.py). To split them the index's own dividends are
needed. A total-return index less the price index is exactly that: the
dividends the index paid, in index points, day by day.

The price index on disk (gvkeyx 118341) has an empty total-return column, so
this first lists every Compustat index whose name mentions the TSX 60, with
how many days of price and of total return each has. Index names and row
counts are printed; no level is.

Saves data/raw/wrds/index_<gvkeyx>.csv (gitignored, never committed). The
login comes from your pgpass file and the username from WRDS_USERNAME.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/wrds"
LIB = "comp_na_daily_all"


def main(pull: list[str]) -> None:
    import wrds
    user = os.environ.get("WRDS_USERNAME")
    db = wrds.Connection(wrds_username=user) if user else wrds.Connection()
    names = db.raw_sql(
        f"select gvkeyx, conm, tic, indextype, idxcstflg from {LIB}.idx_index "
        "where upper(conm) like '%%TSX%%60%%' or upper(conm) like '%%TSX 60%%' "
        "or upper(conm) like '%%S&P/TSX%%' order by conm")
    print(f"{len(names)} indices whose name mentions the TSX:")
    for r in names.itertuples():
        n = db.raw_sql(
            f"select count(prccd) as px, count(prccddiv) as tr, min(datadate) as a, "
            f"max(datadate) as b from {LIB}.idx_daily where gvkeyx = '{r.gvkeyx}' "
            "and datadate >= '2025-01-01'").iloc[0]
        print(f"  {r.gvkeyx}  {str(r.conm)[:52]:52s} tic {str(r.tic):10s} "
              f"since 2025: price days {int(n['px'])}, total-return days {int(n['tr'])}, "
              f"to {n['b']}")
    for g in pull:
        df = db.raw_sql(f"select datadate, prccd, prccddiv from {LIB}.idx_daily "
                        f"where gvkeyx = '{g}' and datadate >= '2014-01-01' order by datadate")
        df.to_csv(OUT / f"index_{g}.csv", index=False)
        print(f"index_{g}.csv: {len(df)} days, {df['datadate'].min()} to {df['datadate'].max()}")
    db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
