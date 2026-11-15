"""
venue_panel.py — the interlisted volume panel (C6).

    .venv/Scripts/python scripts/venue_panel.py

Writes reports/venues.txt and reports/venues.png. Reads the Toronto volumes
(data/raw/wrds/prices_<T>.csv, wrds_prices.py), the New York volumes
(ny_volume_<T>.csv, wrds_ny_volume.py), the Montreal option and futures
files for the books' series, and both sample books.

Published: shares of volume, ratios, counts and days to liquidate grouped
into buckets. Never a volume, an average daily volume or a price (hard rule
1), and never one position's figure (the book reports' rule). Each
position's own figures go to data/derived/venues/ for the user only.
"""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from book import load_book
from loaders.compustat import load_volume_pair
from loaders.mx import load_mx_futures, load_mx_options
from replay import value_position
from run_days import mx_files
from scenarios import latest_inputs
from venues import (BUCKETS, PARTICIPATION, WINDOW, adv, bucket, days_to_liquidate, one_for_one,
                    share_over, single_venue_days, toronto_share)

WRDS = ROOT / "data/raw/wrds"
OUT = ROOT / "reports"
PRIVATE = ROOT / "data/derived/venues"
BOOKS = ["bank_heavy", "diversified"]
INTERLISTED = ["RY", "TD", "BNS", "BMO", "CM", "MFC", "CNQ", "SU", "ENB", "CNR", "BCE",
               "ABX", "SHOP"]
GROUP = {**{n: "banks and insurer" for n in ("RY", "TD", "BNS", "BMO", "CM", "MFC")},
         **{n: "energy and pipeline" for n in ("CNQ", "SU", "ENB")},
         **{n: "rail, telecom, gold, tech" for n in ("CNR", "BCE", "ABX", "SHOP")}}
COLOURS = {"banks and insurer": "#2f6db3", "energy and pipeline": "#c4622d",
           "rail, telecom, gold, tech": "#5a9a4a"}


def pair_of(name: str) -> pd.DataFrame:
    ny = WRDS / f"ny_volume_{name}.csv"
    return load_volume_pair(WRDS / f"prices_{name}.csv", ny if ny.exists() else None)


def one_name(task) -> dict:
    """Runs in a worker: days to liquidate each of one underlying's positions,
    and the name's net share delta, on its latest day."""
    book, name, positions = task
    rows = []
    if name == "SXF":
        fut = load_mx_futures(mx_files("SXF"))
        for pos in positions:
            f = fut[fut["expiry"].dt.date == pos.expiry].groupby("date")["volume"].sum()
            if f.empty:
                raise SystemExit(f"SXF {pos.expiry}: no such contract in the exchange files")
            day = f.index.max()
            rows.append(dict(book=book, name=name, kind="future", day=day.date(),
                             size=pos.quantity, days=days_to_liquidate(pos.quantity, adv(f, day))))
        return dict(rows=rows, share_rows=[])
    day, inp, _ = latest_inputs(name)
    when = pd.Timestamp(day)
    opts = load_mx_options(mx_files(name))
    opts = opts[opts["underlying"] == name]
    dates = pd.DatetimeIndex(sorted(opts["date"].unique()))
    net = 0.0
    for pos in positions:
        if pos.kind == "option":
            s = (opts[(opts["expiry"] == pd.Timestamp(pos.expiry)) & (opts["strike"] == pos.strike)
                      & (opts["call_put"] == pos.call_put)]
                 .groupby("date")["volume"].sum().reindex(dates, fill_value=0))
            v = value_position(pos, inp)
            if v.ok:
                net += v.dollars("delta")              # shares
            rows.append(dict(book=book, name=name, kind="option", day=day, size=pos.quantity,
                             days=days_to_liquidate(pos.quantity, adv(s, when))))
        else:
            net += pos.quantity * pos.multiplier
    p = pair_of(name)
    tor = p["tor"].dropna()
    both = (p["tor"].fillna(0.0) + p["ny"].fillna(0.0))
    listed_ny = p["ny"].notna().any()
    share_rows = [dict(book=book, name=name, day=day, size=net, listed_ny=listed_ny,
                       toronto=days_to_liquidate(net, adv(tor, when)),
                       both=days_to_liquidate(net, adv(both, when)) if listed_ny else np.nan)]
    return dict(rows=rows, share_rows=share_rows)


def main() -> None:
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    pairs = {n: pair_of(n) for n in INTERLISTED}
    last = min(p.dropna(subset=["tor", "ny"]).index.max() for p in pairs.values())
    year_ago = last - pd.DateOffset(years=1)

    say("INTERLISTED VOLUME PANEL (C6): where thirteen names trade, Toronto against New York.")
    say(f"Daily share volume, January 2014 (Shopify from its 2015 listing) to {last:%d %b %Y}.")
    say("Compustat on WRDS; published as shares of volume, ratios and counts only.")
    say()

    # ---- 0. what the numbers measure ---------------------------------------
    say("0. WHAT THE TWO VOLUMES MEASURE")
    say("   New York: consolidated, every US exchange and off-exchange trade (Compustat's")
    say("   figure equals CRSP's to the share on 1,761 days, 2018 to 2024; checked 14 Nov 2026).")
    say("   Toronto: most likely the Toronto Stock Exchange's own trades only. It equals the")
    say("   volume the Montreal file prints, and shows no step in September 2025, when TMX began")
    say("   publishing a consolidated Canadian figure. Not confirmed by a written definition.")
    say("   So Toronto's share below is a LOWER BOUND on Canada's: one Canadian venue against")
    say("   every US one. A FALL over time is ambiguous: it can be trading leaving the TSX for")
    say("   another Canadian venue, not for New York. A RISE is not: the TSX gained on every US")
    say("   venue together. Differences between names in the same year share the same basis.")
    checks = {n: one_for_one(p) for n, p in pairs.items()}
    bad = [n for n, (_, ok) in checks.items() if not ok]
    say(f"   One share for one share: Toronto close / New York close has a median of "
        f"{np.median([m for m, _ in checks.values()]):.3f} across names (the exchange rate);")
    say("   every name between 0.9 and 1.7: " + ("yes." if not bad else f"NO for {', '.join(bad)}."))
    say()

    # ---- 1. Toronto's share ------------------------------------------------
    say("1. TORONTO'S SHARE of the two listings' combined share volume, days both traded")
    yearly = {n: toronto_share(p, "YE")["share"] for n, p in pairs.items()}
    recent = {n: share_over(p[(p.index > year_ago) & (p.index <= last)]) for n, p in pairs.items()}
    first = {n: share_over(p[p.index.year == p.dropna(subset=['tor', 'ny']).index.min().year
                             + (1 if n == "SHOP" else 0)])
             for n, p in pairs.items()}
    t = pd.DataFrame({"group": pd.Series(GROUP), "first full year %": 100 * pd.Series(first),
                      "last 12 months %": 100 * pd.Series(recent)}).loc[INTERLISTED]
    t["change, points"] = t["last 12 months %"] - t["first full year %"]
    t = t.sort_values("last 12 months %", ascending=False)
    say(t.to_string(float_format=lambda x: f"{x:,.1f}").replace("\n", "\n   ").join(["   ", ""]))
    say("   (first full year: 2014; Shopify 2016, its first full year listed in both.)")
    by_group = t.groupby("group")["last 12 months %"].median()
    say("   Median by group, last 12 months: " + "; ".join(f"{g} {v:.0f}%"
                                                         for g, v in by_group.items()) + ".")
    up = int((t["change, points"] > 0).sum())
    say(f"   Toronto's share rose on {up} of {len(t)} names and fell on {len(t) - up}.")
    say("   Read: Toronto is the main market for the banks and the insurer; the energy names")
    say("   and the pipeline have moved toward it (a rise, so not a basis effect); Barrick and")
    say("   Shopify trade mostly in New York. The banks' falls (CM, BNS, RY) may be trading")
    say("   moving to other Canadian venues: this data cannot tell.")
    say("   With Toronto counted as one venue, a Canadian share above a half here is certain")
    say("   to be above a half on the whole Canadian market.")
    say()

    # ---- 2. single-venue days ----------------------------------------------
    say(f"2. DAYS ONLY ONE MARKET IS OPEN: the open venue's volume against its own median over")
    say(f"   the {WINDOW} sessions before (1.00 = a normal day)")
    sv = pd.concat([single_venue_days(p).assign(name=n) for n, p in pairs.items()])
    for venue, g in sv.groupby("venue"):
        days = g["day"].nunique()
        q = g["ratio"].quantile([0.25, 0.5, 0.75])
        say(f"   {venue:<15} {days:3d} days, {len(g):4d} name-days: median {q[0.5]:.2f} "
            f"(middle half {q[0.25]:.2f} to {q[0.75]:.2f})")
    say("   Read: on a US holiday Toronto trades alone, and on a Canadian holiday New York")
    say("   does. The ratio says how much of the usual liquidity is still there; part of the")
    say("   flow that normally crosses between the two markets does not turn up on the other.")
    say()

    # ---- 3. days to liquidate ----------------------------------------------
    tasks = []
    for b in BOOKS:
        names: dict[str, list] = {}
        for p in load_book(ROOT / "books" / f"{b}.csv"):
            names.setdefault(p.underlying, []).append(p)
        tasks += [(b, n, ps) for n, ps in names.items()]
    with ProcessPoolExecutor(max_workers=6) as pool:
        res = list(pool.map(one_name, tasks))
    pos = pd.DataFrame([r for x in res for r in x["rows"]])
    shr = pd.DataFrame([r for x in res for r in x["share_rows"]])
    PRIVATE.mkdir(parents=True, exist_ok=True)
    pos.to_csv(PRIVATE / "positions.csv", index=False)
    shr.to_csv(PRIVATE / "share_delta.csv", index=False)
    say(f"3. DAYS TO LIQUIDATE on each name's latest day, taking {100 * PARTICIPATION:.0f}% of the "
        f"average daily volume over the {WINDOW} sessions before it")
    say("   Counts of positions (options in their own Montreal series, the future in its own")
    say("   contract) and of names (the net share delta of options and shares together).")
    rows = []
    for b in BOOKS:
        o = pos[(pos["book"] == b)]
        rows.append({"book": b, "what": "option and future positions",
                     **o["days"].map(bucket).value_counts().to_dict()})
        s = shr[shr["book"] == b]
        rows.append({"book": b, "what": "names' share delta, Toronto only",
                     **s["toronto"].map(bucket).value_counts().to_dict()})
        s2 = s[s["listed_ny"]]
        rows.append({"book": b, "what": "names' share delta, both listings",
                     **s2["both"].map(bucket).value_counts().to_dict()})
    tab = pd.DataFrame(rows).set_index(["book", "what"]).reindex(
        columns=[*BUCKETS, "no volume history"]).fillna(0).astype(int)
    tab = tab.loc[:, (tab != 0).any(axis=0) | tab.columns.isin(BUCKETS)]
    say(tab.to_string().replace("\n", "\n   ").join(["   ", ""]))
    o = pos[pos["kind"] == "option"]
    say("   Option positions, median sessions to exit: " + "; ".join(
        f"{b} {o.loc[o['book'] == b, 'days'].median():.0f}" for b in BOOKS)
        + f" (an untraded series counts as never).")
    noday = int(np.isinf(o["days"]).sum())
    say(f"   Option positions in a series that did not trade at all in the {WINDOW} sessions before:")
    say(f"   {noday} of {len(o)}. Leaving one is a negotiated trade, not a share of volume.")
    say(f"   Share deltas: every name's net delta can be exited within "
        f"{'a day' if (shr['toronto'] <= 1).all() else 'the days above'} on Toronto alone.")
    say("   Read: the hedge (shares, futures) is liquid and the options are not. At a fifth of")
    say("   their series' own volume these made-up books would take months to leave; a desk")
    say("   would exit by asking market makers for a price, and that price is the mark the")
    say("   review queue checks.")
    say()

    # ---- 4. what is not here ----------------------------------------------
    say("4. NOT MEASURED")
    say("   New York trading after 16:00 and intraday time buckets: both regular sessions run")
    say("   9:30 to 16:00 Eastern, and the daily US figure does not separate extended hours.")
    say("   That needs intraday bars; the Interactive Brokers capture (not yet run) can record")
    say("   them. Canada's other venues: not on disk (see section 0).")
    (OUT / "venues.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    draw(yearly, sv, OUT / "venues.png")


def draw(yearly: dict[str, pd.Series], sv: pd.DataFrame, path: Path) -> None:
    fig, (a, b) = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [2.2, 1]})
    ends = []
    for n, s in yearly.items():
        a.plot(s.index.year, 100 * s.values, color=COLOURS[GROUP[n]], lw=1.6, alpha=0.9)
        ends.append([100 * s.values[-1], n, s.index.year[-1]])
    # end labels in order, at least GAP points apart so none overlap
    GAP = 2.8
    ends.sort()
    for i in range(1, len(ends)):
        ends[i][0] = max(ends[i][0], ends[i - 1][0] + GAP)
    for y, n, x in ends:
        a.annotate(n, (x + 0.15, y), fontsize=8, va="center", color=COLOURS[GROUP[n]],
                   annotation_clip=False)
    a.set_xlim(right=max(x for _, _, x in ends) + 0.9)
    a.axhline(50, color="0.6", lw=0.8, ls="--")
    a.set_ylim(0, 100)
    a.set_ylabel("Toronto's share of combined share volume, %")
    a.set_title("Toronto (TSX alone) against New York (consolidated), by year (2026 to date)")
    for g, c in COLOURS.items():
        a.plot([], [], color=c, label=g)
    a.legend(loc="lower left", bbox_to_anchor=(0.0, 0.24), fontsize=8, frameon=False)
    groups = [g["ratio"].to_numpy() for _, g in sv.groupby("venue")]
    b.boxplot(groups, tick_labels=[v for v, _ in sv.groupby("venue")], showfliers=False)
    b.axhline(1.0, color="0.6", lw=0.8, ls="--")
    b.set_ylabel("volume against that venue's normal day")
    b.set_title("When only one market is open")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
