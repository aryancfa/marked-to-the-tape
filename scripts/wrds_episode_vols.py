"""
wrds_episode_vols.py — the vols the New York listings showed in each replay
episode, and the exchange rate to take the currency out of them (C3).

    .venv/Scripts/python scripts/wrds_episode_vols.py

Saves, under data/raw/wrds/ (gitignored, never committed, never
redistributed):
  vsurfd_episodes.csv   OptionMetrics' standardized at-the-money implied vol
                        (call delta 50, put delta -50) at 30 to 365 days, for
                        each New York listing, over each episode in
                        src/episodes.py plus 90 days before it
  fx_gbp_daily.csv      Compustat's daily rates from pounds to CAD and USD,
                        2014 to 2023, from which USD/CAD is the ratio

It prints row counts and column names only, never rows. The login comes from
the pgpass file and the username from WRDS_USERNAME (hard rule 3).
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from episodes import EPISODES

OUT = ROOT / "data/raw/wrds"
LEAD = timedelta(days=90)       # history before an episode, for the FX correlation
DAYS = (30, 60, 91, 122, 152, 182, 273, 365)

# OptionMetrics security ids of the New York listings (looked up in
# optionm.secnmd, 13 November 2026). Barrick trades as GOLD in New York since
# 2019 and CNR as CNI; Shopify's id starts on 21 May 2015.
SECIDS = {"RY": 109722, "TD": 111089, "BNS": 115462, "BMO": 101977, "CNQ": 102669,
          "SU": 110619, "MFC": 107209, "ENB": 104306, "CNR": 102668, "BCE": 101872,
          "ABX": 102021, "SHOP": 207294}


def main() -> None:
    import wrds
    user = os.environ.get("WRDS_USERNAME")
    db = wrds.Connection(wrds_username=user) if user else wrds.Connection()
    OUT.mkdir(parents=True, exist_ok=True)
    ids = ", ".join(str(s) for s in SECIDS.values())
    days = ", ".join(str(d) for d in DAYS)
    parts = []
    for ep in EPISODES:
        lo, hi = ep.start - LEAD, ep.end
        for year in range(lo.year, hi.year + 1):
            sql = (f"select secid, date, days, delta, cp_flag, impl_volatility "
                   f"from optionm.vsurfd{year} where secid in ({ids}) and days in ({days}) "
                   f"and abs(delta) = 50 and date between '{lo}' and '{hi}'")
            parts.append(db.raw_sql(sql).assign(episode=ep.key))
    vs = pd.concat(parts, ignore_index=True)
    vs["name"] = vs["secid"].map({v: k for k, v in SECIDS.items()})
    vs.to_csv(OUT / "vsurfd_episodes.csv", index=False)
    print(f"vsurfd_episodes.csv: {len(vs)} rows; columns {list(vs.columns)}")
    print("rows by name and episode:")
    print(vs.groupby(["episode", "name"]).size().unstack(0).fillna(0).astype(int).to_string())

    fx = db.raw_sql("select datadate, fromcurd, tocurd, exratd from comp.g_exrt_dly "
                    "where tocurd in ('CAD', 'USD') "
                    "and datadate between '2014-01-01' and '2023-12-31'")
    fx.to_csv(OUT / "fx_gbp_daily.csv", index=False)
    print(f"fx_gbp_daily.csv: {len(fx)} rows; columns {list(fx.columns)}; "
          f"from {sorted(fx['fromcurd'].unique())}")
    db.close()


if __name__ == "__main__":
    main()
