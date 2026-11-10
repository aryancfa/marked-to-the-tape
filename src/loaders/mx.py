"""
loaders/mx.py — Montreal Exchange daily summary files (build sheet CAP, B2).

Reads the CSV files downloaded BY HAND from the exchange's website into two
tidy tables: one of options, one of the underlying. It only reads and tags;
no pricing happens here.

What each option row is worth as evidence (the `tier` column)
------------------------------------------------------------
An exchange publishes a settlement price for every series, traded or not. For
a series that did not trade, the settlement comes from the exchange's own
model, so comparing our model with it compares two models. But a series with
no trade can still carry a live two-sided quote, and a quote is a price
someone was willing to deal at. So there are three kinds of evidence:

    traded           volume > 0
    quoted           no trade, but bid > 0 and ask >= bid
    settlement_only  no trade and no two-sided quote, settlement > 0
    no_price         none of the above; nothing to invert

The price to invert (the `mark` column)
---------------------------------------
The quote midpoint wherever the quote is two-sided, because it is the market's
own number and comes with its own error bar (the spread). The settlement only
where there is no two-sided quote. `mark_source` says which, and it is never
filled in by guessing: no quote and no settlement leaves `mark` empty.

Settlement and quote are not taken at the same moment
------------------------------------------------------
On the first files about 6% of quoted RY rows have a settlement OUTSIDE the
closing bid-ask. A settlement cannot sit outside a quote from the same
instant, so the two were stamped at different times and the share moved in
between. `settle_in_quote` records this per row (True, False, or empty when
there is no two-sided quote or no settlement). The consequences:
  * never mix the two in one comparison: a row is inverted from its midpoint
    or from its settlement, and says which;
  * the share price used with a midpoint must belong to the quote's moment,
    which is why the spot is implied from the quotes themselves
    (voleng.implied_spot_american) instead of read from a closing print;
  * a settlement-only row has no quote to date it by, so it is shown as the
    weakest tier.

Licence: these files are for personal use. Nothing here writes them anywhere,
and tests use made-up prices in the same column layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# Column names exactly as they appear in the exchange's file.
_COLS = {
    "Date": "date",
    "Symbol": "symbol",
    "Underlying Symbol": "underlying",
    "Strike Price": "strike",
    "Expiry Date": "expiry",
    "Call/Put": "call_put_code",
    "Ins. Type": "ins_type",
    "Bid Price": "bid",
    "Ask Price": "ask",
    "Last Price": "last",
    "Volume": "volume",
    "Settlement Price": "settle",
    "Open Interest": "open_interest",
    "Implied Volatility": "mx_iv_pct",
}

INS_OPTION, INS_UNDERLYING = 0, 3
_CALL_PUT = {0: "C", 1: "P"}

TRADED, QUOTED, SETTLEMENT_ONLY, NO_PRICE = (
    "traded", "quoted", "settlement_only", "no_price")

OPTION_COLUMNS = [
    "date", "underlying", "expiry", "strike", "call_put", "bid", "ask",
    "settle", "volume", "open_interest", "traded", "two_sided", "tier",
    "mark", "mark_source", "settle_in_quote", "mx_iv",
]
UNDERLYING_COLUMNS = ["date", "underlying", "bid", "ask", "mid", "last", "volume"]


def _paths(source: str | Path | Iterable[str | Path]) -> list[Path]:
    if isinstance(source, (str, Path)):
        p = Path(source)
        found = sorted(p.glob("*.csv")) if p.is_dir() else [p]
    else:
        found = [Path(s) for s in source]
    if not found:
        raise FileNotFoundError(f"no CSV files found in {source!r}")
    return found


def _read(source) -> pd.DataFrame:
    frames = []
    for path in _paths(source):
        df = pd.read_csv(path, low_memory=False)
        missing = [c for c in _COLS if c not in df.columns]
        if missing:
            # The plan says: if the option columns are absent, stop. Capture
            # then switches to Interactive Brokers.
            raise ValueError(f"{path.name}: missing columns {missing}")
        frames.append(df[list(_COLS)].rename(columns=_COLS))
    raw = pd.concat(frames, ignore_index=True)

    unknown = sorted(set(raw["ins_type"].unique()) - {INS_OPTION, INS_UNDERLYING})
    if unknown:
        raise ValueError(f"unknown 'Ins. Type' codes {unknown}; "
                         "check what they are before loading")
    raw["date"] = pd.to_datetime(raw["date"], format="%Y-%m-%d")
    raw["expiry"] = pd.to_datetime(raw["expiry"], format="%Y-%m-%d")

    # Downloads overlap when the window is rolled forward. Identical repeats
    # are dropped. The same series on the same day with DIFFERENT values is
    # not something to choose between silently.
    raw = raw.drop_duplicates()
    clash = raw.duplicated(["date", "symbol"], keep=False)
    if clash.any():
        raise ValueError(
            f"{int(clash.sum())} rows repeat a (date, symbol) with different "
            "values across files; resolve the files before loading")
    return raw


def load_mx_options(source: str | Path | Iterable[str | Path]) -> pd.DataFrame:
    """
    One row per option series per day, sorted, with the columns in
    OPTION_COLUMNS.

    `source` is a file, a folder of CSV files, or a list of files.
    `mx_iv` is the exchange's own implied vol as a decimal (0.25 = 25%), empty
    where the file has zero. The exchange computes it with Black-Scholes on
    American options; it is a reference to compare against, never an input.
    """
    raw = _read(source)
    opt = raw[raw["ins_type"] == INS_OPTION].copy()

    bad = sorted(set(opt["call_put_code"].unique()) - set(_CALL_PUT))
    if bad:
        raise ValueError(f"unknown 'Call/Put' codes {bad}")
    opt["call_put"] = opt["call_put_code"].map(_CALL_PUT)

    # The series symbol carries its own C or P. If it ever disagrees with the
    # code, the code's meaning has changed and every sign downstream is wrong.
    letter = opt["symbol"].astype(str).str.extract(r"\d{6}([CP])\d")[0]
    disagree = letter.notna() & (letter != opt["call_put"])
    if disagree.any():
        raise ValueError(f"{int(disagree.sum())} rows where the symbol's C/P "
                         "letter disagrees with the 'Call/Put' code")
    if opt["expiry"].isna().any() or opt["underlying"].isna().any():
        raise ValueError("option rows with no expiry or underlying")

    opt["traded"] = opt["volume"] > 0
    opt["two_sided"] = (opt["bid"] > 0) & (opt["ask"] >= opt["bid"])
    has_settle = opt["settle"] > 0

    opt["tier"] = np.select(
        [opt["traded"], opt["two_sided"], has_settle],
        [TRADED, QUOTED, SETTLEMENT_ONLY], default=NO_PRICE)

    mid = 0.5 * (opt["bid"] + opt["ask"])
    opt["mark"] = np.where(opt["two_sided"], mid,
                           np.where(has_settle, opt["settle"], np.nan))
    opt["mark_source"] = np.select(
        [opt["two_sided"], has_settle], ["mid", "settle"], default="none")

    inside = (opt["settle"] >= opt["bid"] - 1e-9) & (opt["settle"] <= opt["ask"] + 1e-9)
    opt["settle_in_quote"] = inside.astype(object).where(
        opt["two_sided"] & has_settle, None)

    opt["mx_iv"] = np.where(opt["mx_iv_pct"] > 0, opt["mx_iv_pct"] / 100.0, np.nan)
    opt["strike"] = opt["strike"].astype(float)

    out = opt[OPTION_COLUMNS].sort_values(
        ["date", "underlying", "expiry", "strike", "call_put"])
    return out.reset_index(drop=True)


def load_mx_underlying(source: str | Path | Iterable[str | Path]) -> pd.DataFrame:
    """
    One row per underlying per day: bid, ask, their midpoint, last trade and
    volume, as printed in the same file as the options.

    The midpoint matters because it is taken with the option quotes. An
    underlying price stamped at a different moment from the option price
    shows up as a false gap between call and put implied vols: 10 basis
    points of staleness is about one vol point at one month.

    UNVERIFIED: whether `last` is the official Toronto close, and at what time
    the file's quotes are taken. Check against an independent close before
    relying on either.

    MEASURED on the first files: the underlying's own bid-ask in this file is
    around 20 basis points wide, far wider than these shares trade during the
    day, and `last` sits a median 4 to 5 basis points from the midpoint (up to
    74). So this is not a tight closing quote, and neither number pins the
    share price to better than about half a vol point at one month. Treat the
    spot as an input with a stated error, not as a known.
    """
    raw = _read(source)
    und = raw[raw["ins_type"] == INS_UNDERLYING].copy()
    und["underlying"] = und["symbol"].astype(str).str.strip()
    ok = (und["bid"] > 0) & (und["ask"] >= und["bid"])
    und["mid"] = np.where(ok, 0.5 * (und["bid"] + und["ask"]), np.nan)
    und["last"] = und["last"].where(und["last"] > 0)
    out = und[UNDERLYING_COLUMNS].sort_values(["date", "underlying"])
    return out.reset_index(drop=True)


def tier_counts(options: pd.DataFrame) -> pd.DataFrame:
    """Rows per underlying and tier: the split the front chart must show."""
    return (options.groupby(["underlying", "tier"]).size()
            .unstack(fill_value=0)
            .reindex(columns=[TRADED, QUOTED, SETTLEMENT_ONLY, NO_PRICE],
                     fill_value=0))
