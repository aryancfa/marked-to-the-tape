"""
riskgrid.py — where a book's vega sits, and its delta in index terms (C2).

Two problems with one portfolio vega number
-------------------------------------------
"Vega $3,400 a vol point" adds a one-month vol point on RY to a four-month
vol point on Suncor as if they were the same risk. They are not: short-dated
vol moves more than long-dated vol, out-of-the-money puts move differently
from at-the-money calls, and two names' vols do not move together one for
one. So vega is shown on a grid of time to expiry by strike relative to the
share price, the same grid the front chart uses (references.M_EDGES), per
name.

Second, a grid cell means something only where there is a market in it. A
vega number in a cell where the exchange quotes two strikes is a sensitivity
to a vol nobody can observe or trade, so nobody can hedge it or check its
mark. A cell is POPULATED when the position's expiry has at least
`references.SVI_MIN_STRIKES` strikes with an implied vol that day (enough
for its own smile), the position's strike lies inside the quoted strikes,
and at least `min_in_cell` of them fall in the position's moneyness cell. Vega in a cell that is not populated is not dropped: it is
reported apart, as vega with no market behind it. A name with no populated
expiry at all is marked DELTA-ONLY: its options are risk-managed by their
delta, and their vega is a number the book cannot act on.

Delta mapped to the index
-------------------------
Each name's dollar delta is kept. For a book total, each is multiplied by the
name's beta to the TSX 60 (fitted on earlier days by the caller): $1 of RY
delta with a beta of 0.8 counts as $0.80 of index. That is the index
exposure a futures hedge would offset; what is left by name is the part an
index hedge cannot touch.

Nothing here reads a file or prices anything. numpy and pandas only.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from references import M_EDGES, SVI_MIN_STRIKES

MIN_IN_CELL = 1              # strikes with a vol inside the position's moneyness cell. Not 2:
                             # the at-the-money cell is 4% wide, so a $100 share with
                             # strikes every $2.50 has only one strike in it
DELTA_ONLY = "delta-only"    # a name with no populated expiry on the day
OFF_SURFACE = "no market in this cell"


def moneyness_cell(strike: float, spot: float, edges=M_EDGES) -> str:
    """The front chart's moneyness cell (strike over share price), or a
    label for beyond its edges."""
    m = strike / spot
    if m <= edges[0]:
        return f"under {edges[0]:.2f}"
    if m > edges[-1]:
        return f"over {edges[-1]:.2f}"
    for lo, hi in zip(edges, edges[1:]):
        if m <= hi:
            return f"{lo:.2f}-{hi:.2f}"
    raise AssertionError("unreachable")


def cell_order(edges=M_EDGES) -> list[str]:
    return ([f"under {edges[0]:.2f}"] + [f"{a:.2f}-{b:.2f}" for a, b in zip(edges, edges[1:])]
            + [f"over {edges[-1]:.2f}"])


def surface_strikes(inverted: pd.DataFrame) -> dict[date, np.ndarray]:
    """For one name on one day: each expiry's strikes that carry an implied
    vol (either side), sorted. `inverted` is the day runner's table."""
    inv = inverted[inverted["iv"].notna()]
    out: dict[date, np.ndarray] = {}
    for e, g in inv.groupby("expiry"):
        key = e.date() if hasattr(e, "date") else e
        out[key] = np.unique(g["strike"].to_numpy(dtype=float))
    return out


def is_populated(strikes: np.ndarray | None, strike: float, spot: float,
                 min_strikes: int = SVI_MIN_STRIKES, min_in_cell: int = MIN_IN_CELL) -> bool:
    """True when the expiry has its own smile's worth of strikes, the
    position's strike lies inside the quoted range, and its moneyness cell
    holds at least `min_in_cell` of them. The range condition matters for the
    two outer cells, which have no far edge: a strike at 1.5 times the share
    price is not covered because strikes at 1.15 are quoted. A smile says
    nothing beyond the strikes it was fitted through."""
    if strikes is None or len(strikes) < min_strikes:
        return False
    if not strikes.min() <= strike <= strikes.max():
        return False
    cell = moneyness_cell(strike, spot)
    return sum(moneyness_cell(k, spot) == cell for k in strikes) >= min_in_cell


def name_has_surface(by_expiry: Mapping[date, np.ndarray],
                     min_strikes: int = SVI_MIN_STRIKES) -> bool:
    """False marks the name delta-only for the day."""
    return any(len(k) >= min_strikes for k in by_expiry.values())


def off_market(t: pd.DataFrame) -> pd.Series:
    """True for each option row whose vega has no market behind it: its cell
    is not populated, or its name is delta-only.

    The flags are read with astype(bool). A table that also holds share rows
    stores them as Python objects, and on those `~` is not "not": ~True is -2
    and ~False is -1, both of which count as true. Written inline as
    `~t["populated"]`, that once marked every option in a book off-market."""
    return ~(t["populated"].astype(bool) & ~t["delta_only"].astype(bool))


def vega_grid(rows: Iterable[Mapping]) -> pd.DataFrame:
    """Sum dollars of vega per vol point by name, maturity and moneyness.

    Each row: name, maturity, moneyness, vega, populated (bool), and
    delta_only (bool, the name's). Vega on a delta-only name or in a cell
    that is not populated goes to the `off` column, never into the grid."""
    t = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if t.empty:
        return pd.DataFrame(columns=["name", "maturity", "moneyness", "vega", "off"])
    on = ~off_market(t)
    t["off"] = np.where(on, 0.0, t["vega"])
    t["vega"] = np.where(on, t["vega"], 0.0)
    return (t.groupby(["name", "maturity", "moneyness"], sort=False)[["vega", "off"]]
            .sum().reset_index())


def mapped_delta(deltas: Mapping[str, float], betas: Mapping[str, float]) -> pd.DataFrame:
    """Each name's dollar delta, its beta to the index, and the delta in
    index dollars. A name with no beta is an error: a total that silently
    skipped a name would be wrong without saying so."""
    missing = sorted(n for n in deltas if n not in betas or not np.isfinite(betas[n]))
    if missing:
        raise ValueError(f"no beta to the index for {', '.join(missing)}")
    t = pd.DataFrame({"delta": pd.Series(deltas, dtype=float)})
    t["beta"] = [float(betas[n]) for n in t.index]
    t["index_delta"] = t["delta"] * t["beta"]
    t.index.name = "name"
    return t
