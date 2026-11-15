"""
venues.py — where the interlisted names trade, and how long a position takes to exit (C6).

Thirteen names in the universe list both in Toronto and in New York, one
share for one share (checked: the Toronto close over the New York close sits
at the exchange rate). A desk working an order in one of them chooses a
venue, and how much trades on each is the first fact behind that choice.

What the volumes measure (checked 14 November 2026, see docs/findings.md):
  New York   consolidated: every US exchange and off-exchange trade. Compustat's
             figure agrees with CRSP's (matched by CUSIP) within 1% on 97% of
             days, 2014 to 2024 (same_count; scripts/venue_panel.py prints it).
  Toronto    most likely the Toronto Stock Exchange's own trades only, not the
             other Canadian venues: it agrees with the volume the Montreal file
             prints, and shows no step when TMX began publishing a consolidated
             Canadian figure in September 2025 (common_step). NOT confirmed.
So Toronto's share computed here is a LOWER bound on Canada's share: the
comparison puts one Canadian venue against every US one. A fall over time
is ambiguous (trading may have left the TSX for another Canadian venue,
not for New York); a rise is not (the TSX gained on every US venue at once).

Not measurable from daily data: how much trades in New York after 16:00
(the regular sessions in both cities run 9:30 to 16:00 Eastern; the US
figure includes any extended-hours trade but does not separate it). That
needs intraday bars, which the Interactive Brokers capture could provide.

Days to liquidate: a position's size over PARTICIPATION times its average
daily volume over the WINDOW sessions before the day (earlier days only).
Trading more than about a fifth of the market's volume moves the price
against you, which is why desks size exits as a share of volume.

Nothing here reads a file or prices anything.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PARTICIPATION = 0.20      # share of the market's volume an exit may take
WINDOW = 20               # sessions behind an average daily volume
FX_BAND = (0.9, 1.7)      # Toronto close / New York close for a one-for-one listing
BUCKETS = ("within a day", "1 to 5 days", "6 to 20 days", "21 to 60 days", "over 60 days",
           f"no volume in {WINDOW} sessions")
EDGES = (1, 5, 20, 60)    # upper ends of the first four buckets, sessions


def one_for_one(pair: pd.DataFrame, band: tuple[float, float] = FX_BAND) -> tuple[float, bool]:
    """Median Toronto close over New York close on days both priced, and
    whether it sits where a one-for-one listing must: at the exchange rate.
    A depositary receipt for two shares would sit near twice the rate."""
    both = pair.dropna(subset=["tor_px", "ny_px"])
    if both.empty:
        return float("nan"), False
    m = float((both["tor_px"] / both["ny_px"]).median())
    return m, band[0] <= m <= band[1]


def same_count(a: pd.Series, b: pd.Series, tol: float = 0.01) -> dict:
    """Two daily volume series for the same share from two sources, on the
    days both have a positive figure: how many days, the median ratio a/b,
    and the share of days the two agree within `tol`. Two sources counting
    the same trades agree on nearly every day."""
    m = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    m = m[(m["a"] > 0) & (m["b"] > 0)]
    r = m["a"] / m["b"]
    return dict(days=len(m), median=float(r.median()) if len(m) else float("nan"),
                agree=float((r.sub(1).abs() <= tol).mean()) if len(m) else float("nan"))


def common_step(pairs: dict[str, pd.DataFrame], month: str) -> tuple[float, float]:
    """Did the basis of either volume change in `month` (for example
    '2025-09')? Per name, the monthly change in log(Toronto / New York);
    across names, its median. A change in what one source counts moves
    every name the same way at once. Returns the median change in that
    month and the 95th percentile of its absolute size over all months."""
    logs = {}
    for n, p in pairs.items():
        mm = both_open(p)[["tor", "ny"]].resample("ME").sum()
        mm = mm[(mm["tor"] > 0) & (mm["ny"] > 0)]
        logs[n] = np.log(mm["tor"] / mm["ny"])
    med = pd.DataFrame(logs).diff().median(axis=1).dropna()
    at = med[med.index.strftime("%Y-%m") == month]
    return (float(at.iloc[0]) if len(at) else float("nan"), float(med.abs().quantile(0.95)))


def both_open(pair: pd.DataFrame) -> pd.DataFrame:
    """Days both markets traded the share."""
    return pair[(pair["tor"] > 0) & (pair["ny"] > 0)]


def toronto_share(pair: pd.DataFrame, freq: str = "YE") -> pd.DataFrame:
    """Toronto's share of the two listings' combined share volume, per
    period, on days both traded: sum(Toronto) / sum(Toronto + New York).
    A volume-weighted share, so a heavy day counts for more than a quiet one."""
    b = both_open(pair)
    g = b.groupby(pd.Grouper(freq=freq))
    out = pd.DataFrame({"share": g["tor"].sum() / (g["tor"].sum() + g["ny"].sum()),
                        "days": g.size()})
    return out[out["days"] > 0]


def share_over(pair: pd.DataFrame) -> float:
    """Toronto's share over the whole of `pair`, on days both traded."""
    b = both_open(pair)
    total = b["tor"].sum() + b["ny"].sum()
    return float(b["tor"].sum() / total) if total > 0 else float("nan")


def single_venue_days(pair: pd.DataFrame, window: int = WINDOW) -> pd.DataFrame:
    """Days only one of the two markets had a session (a holiday in the
    other country): the open market's volume against its own median over
    the `window` sessions before, when both were open. Columns: venue
    ('Toronto alone' or 'New York alone'), ratio."""
    out = []
    b = both_open(pair)
    for day, r in pair.iterrows():
        alone = ("tor" if pd.notna(r["tor"]) and pd.isna(r["ny"]) else
                 "ny" if pd.notna(r["ny"]) and pd.isna(r["tor"]) else None)
        if alone is None or not r[alone] > 0:
            continue
        before = b.loc[b.index < day, alone].tail(window)
        if len(before) < window:
            continue
        out.append(dict(day=day, venue="Toronto alone" if alone == "tor" else "New York alone",
                        ratio=float(r[alone] / before.median())))
    return pd.DataFrame(out, columns=["day", "venue", "ratio"])


def adv(volume: pd.Series, day, window: int = WINDOW) -> float:
    """Average daily volume over the `window` sessions BEFORE `day` (a
    session with no volume counts as zero). nan with fewer sessions."""
    v = volume[volume.index < pd.Timestamp(day)].fillna(0.0).tail(window)
    return float(v.mean()) if len(v) == window else float("nan")


def days_to_liquidate(size: float, average: float, participation: float = PARTICIPATION) -> float:
    """Sessions to exit `size` (contracts or shares, either sign) taking
    `participation` of an average day's volume. inf where nothing traded."""
    if not np.isfinite(average):
        return float("nan")
    if average <= 0:
        return float("inf") if size else 0.0
    return abs(size) / (participation * average)


def bucket(days: float) -> str:
    if np.isnan(days):
        return "no volume history"
    if np.isinf(days):
        return BUCKETS[-1]
    for edge, name in zip(EDGES, BUCKETS):
        if days <= edge:
            return name
    return BUCKETS[len(EDGES)]
