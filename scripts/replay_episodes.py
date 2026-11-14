"""
replay_episodes.py — today's book through real market episodes (C3).

    .venv/Scripts/python scripts/replay_episodes.py books/bank_heavy.csv

Each episode in src/episodes.py is replayed on the book as it stands on each
name's latest day on file: every name moves as it really moved, day by day
from the episode's first close, all together. No beta stands between the
history and the result.

Time passes as it did. Where the episode is shorter than the book's nearest
option expiry, each replay day is priced at the end of as many sessions as
have elapsed, with decay, funding and dividends kept apart from the move
(src/horizon.py, B6). An episode longer than that (the 2022-23 rate cycle,
343 sessions) would run every option past its expiry, so it is replayed as
an instant move instead, and the report says so.

THE THREE LABELS (build sheet C3). Every option-day says where its vol came
from:
  real settlement             the exchange's own mark on that day. None here:
                              Montreal history on disk starts in May 2026.
  model on observed NY vol    the change in the vol the New York listing
                              showed at the option's maturity and strike
                              (25-delta wings and the money), currency taken
                              out (src/nyvol.py), pulled by
                              scripts/wrds_episode_vols.py.
  model on rule-based vol     the C1 beta rule (src/volrule.py): a number
                              someone chose, used where nothing was observed.
The report counts each.

Every dollar figure is a MODEL price (hard rule 6) on a made-up book. The
moves are derived from Compustat prices; no price appears in the output.
Rates: today's curve, shifted on each session by how far the Bank of
Canada's Treasury bill curve (1, 3, 6 and 12 months) had moved since the
episode's first close, read at each option's maturity
(data/raw/boc_history, episodes.curve_move). What that adds is its own part,
"rates", priced last.
"""

from __future__ import annotations

import argparse
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
from episodes import EPISODES, curve_move, path, sessions, shift_function
from horizon import carry_parts, end_of_horizon, share_parts, shocked
from loaders.mx import load_mx_futures
from nyvol import atm_usd_vol, cad_vol, fx_terms, smile_usd_vol, strike_call_delta, usdcad_from_gbp
from rates import read_boc_series
from replay import value_position
from run_days import mx_files
from scenarios import INDEX, label, latest_inputs, returns_table
from tradingcal import year_fraction
from volrule import BETA, shocked_vol, smile_from_points

WRDS = ROOT / "data/raw/wrds"
RULE_VOL = "model on rule-based vol"
NY_VOL = "model on observed NY vol"
SETTLED = "real settlement"
LABELS = (SETTLED, NY_VOL, RULE_VOL)
PARTS = ("decay", "funding", "dividends", "shock", "rates")
BILLS = ROOT / "data/raw/boc_history"
BILL_FILES = {30: "TB.CDN.30D.MID.csv", 90: "TB.CDN.90D.MID.csv",
              180: "TB.CDN.180D.MID.csv", 365: "TB.CDN.1Y.MID.csv"}


def has_skew(surface: pd.DataFrame | None) -> bool:
    """True when the surface carries the 25-delta wings as well as the money."""
    return surface is not None and "delta" in surface and bool((surface["delta"].abs() == 25).any())


def observed_vol(surface: pd.DataFrame | None, share_tr: pd.Series | None,
                 usdcad: pd.Series | None, d, tau: float, cdelta: float) -> float | None:
    """The New York vol on day `d` at `tau` days to expiry, currency taken
    out: at the strike whose call delta is `cdelta` where the wings were
    pulled, at the money otherwise. None where any input is missing or the
    formula has no answer."""
    if surface is None or share_tr is None or usdcad is None:
        return None
    usd = (smile_usd_vol(surface, d, tau, cdelta) if has_skew(surface) and np.isfinite(cdelta)
           else atm_usd_vol(surface, d, tau))
    if not np.isfinite(usd):
        return None
    fx_vol, rho = fx_terms(share_tr, usdcad, d)
    return cad_vol(usd, fx_vol, rho)


def one_name(task) -> dict:
    """Runs in a worker. One underlying's positions through every episode.
    Returns rows: episode, session, position, the four parts, and a label."""
    name, positions, moves, aged, params, surface, share_tr, usdcad, starts, curves = task
    rows = []
    if name == "SXF":
        fut = load_mx_futures(mx_files("SXF"))
        for pos in positions:
            f = fut[fut["expiry"].dt.date == pos.expiry].sort_values("date")
            if f.empty:
                raise SystemExit(f"SXF {pos.expiry}: no such contract in the exchange files")
            level = float(f["settle"].iloc[-1])
            for key, m in moves.items():
                for d, x in m.items():
                    rows.append(dict(episode=key, session=d, position=label(pos), kind=pos.kind,
                                     decay=0.0, funding=0.0, dividends=0.0, rates=0.0,
                                     shock=pos.quantity * pos.multiplier * level * x))
        return dict(name=name, rows=rows, day=None)

    day, inp, inv = latest_inputs(name)
    inv = inv[inv["iv"].notna()]
    exp = pd.Series([e.date() if hasattr(e, "date") else e for e in inv["expiry"]],
                    index=inv.index)
    for pos in positions:
        v = value_position(pos, inp)
        if not v.ok:
            rows.append(dict(episode=None, position=label(pos), kind=pos.kind,
                             not_valued=v.reason))
            continue
        if pos.kind == "option":
            chain = inv[exp == pos.expiry]
            smile = smile_from_points(chain["strike"], chain["iv"])
            own = smile(pos.strike)
        for key, m in moves.items():
            base = None
            if pos.kind == "option":
                tau = float((pos.expiry - day).days)
                # where this strike sat on the smile when the episode began
                T0 = year_fraction(day, pos.expiry)
                base = observed_vol(surface, share_tr, usdcad, starts[key], tau,
                                    strike_call_delta(inp.spot, pos.strike, T0, v.iv))
            for k, (d, x) in enumerate(m.items(), start=1):
                end = end_of_horizon(day, k) if aged[key] else day
                if pos.kind != "option":
                    sp = share_parts(v.scale, inp.spot, inp.divs, day, end, [x])
                    rows.append(dict(episode=key, session=d, position=label(pos), kind=pos.kind,
                                     decay=0.0, funding=0.0, dividends=0.0, rates=0.0,
                                     shock=sp["shock"][0]))
                    continue
                c = carry_parts(pos, day, end, inp.spot, inp.rate, inp.divs, v.iv)
                s1 = c["expected_spot"] * (1.0 + x)
                T = year_fraction(end, pos.expiry)
                # the same strike on day d sits elsewhere on the smile: the
                # share has moved, so read the smile at its new call delta
                now = (observed_vol(surface, share_tr, usdcad, d, tau,
                                    strike_call_delta(s1, pos.strike, T, v.iv))
                       if base is not None else None)
                if now is not None:
                    # what the market's vol at this maturity and this strike
                    # did since the episode began, added to the option's own
                    # vol today
                    vol, where = max(v.iv + now - base, 0.01), NY_VOL
                else:
                    vol = max(v.iv + shocked_vol(BETA, smile, pos.strike, inp.spot, s1, T,
                                                 **params) - own, 0.01)
                    where = RULE_VOL
                after = shocked(pos, end, c["expected_spot"], x, inp.rate, inp.divs, vol)
                # last, the rate curve moves as the bill curve did since the
                # episode began; what that adds is the "rates" part
                move = (curves or {}).get(key, {}).get(d)
                rates = 0.0
                if move is not None:
                    shift = shift_function(*move)
                    moved = lambda T, s=shift: inp.rate(T) + s(T)   # noqa: E731
                    rates = v.scale * (shocked(pos, end, c["expected_spot"], x, moved, inp.divs,
                                               vol) - after)
                rows.append(dict(episode=key, session=d, position=label(pos), kind=pos.kind,
                                 decay=v.scale * c["decay"], funding=v.scale * c["funding"],
                                 dividends=v.scale * c["dividends"],
                                 shock=v.scale * (after - c["aged"]), rates=rates, label=where,
                                 vol_change=100 * (vol - v.iv), rate_moved=move is not None))
    return dict(name=name, rows=rows, day=day)


def money(x: float) -> str:
    return f"{x:,.0f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("book")
    ap.add_argument("--beta", type=float, default=-0.5,
                    help="the C1 rule's vol points per 1%% move, where no vol was observed")
    ap.add_argument("--crash", type=float, default=-0.10)
    ap.add_argument("--steepen", type=float, default=0.5)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    path_ = Path(a.book)
    params = dict(beta=a.beta, crash=a.crash, steepen=a.steepen)
    names: dict[str, list] = {}
    for p in load_book(path_):
        names.setdefault(p.underlying, []).append(p)

    returns = returns_table([n for n in names if n != "SXF"])
    paths, missing = {}, {}
    for ep in EPISODES:
        m, miss = path(returns, ep)
        paths[ep.key], missing[ep.key] = m, miss
    # an episode can be aged only if it ends before the book's first expiry:
    # count the sessions from the latest valuation day to the day before it
    first = min(p.expiry for ps in names.values() for p in ps if p.kind == "option")
    room, d = 0, max(latest_inputs(n)[0] for n in names if n != "SXF")
    while True:
        nxt = end_of_horizon(d, 1)
        if nxt >= first:
            break
        room, d = room + 1, nxt
    aged = {ep.key: len(sessions(ep)) <= room for ep in EPISODES}

    # the skew file has the 25-delta wings AND the money; the older file the
    # money only, used if the skew pull has not been run
    fx_path = WRDS / "fx_gbp_daily.csv"
    vs_path = next((p for p in (WRDS / "vsurfd_skew.csv", WRDS / "vsurfd_episodes.csv")
                    if p.exists()), None)
    surfaces, usdcad = {}, None
    if vs_path is not None and fx_path.exists():
        vs = pd.read_csv(vs_path, parse_dates=["date"])
        surfaces = {n: g.drop_duplicates(["date", "days", "delta", "cp_flag"])
                    for n, g in vs.groupby("name")}
        usdcad = usdcad_from_gbp(pd.read_csv(fx_path))
    starts = {ep.key: ep.start for ep in EPISODES}
    # the Bank of Canada bill curve's move on each session of each episode,
    # from data/raw/boc_history (kept apart from data/raw/boc, whose files are
    # in every name's cache key)
    curves: dict = {}
    if all((BILLS / f).exists() for f in BILL_FILES.values()):
        bills = {}
        for t, f in BILL_FILES.items():
            s = pd.Series(read_boc_series(BILLS / f)).sort_index()
            s.index = pd.to_datetime(s.index)
            bills[t] = s
        curves = {ep.key: {d: curve_move(bills, ep.start, d) for d in sessions(ep)}
                  for ep in EPISODES}
    tasks = []
    for n, ps in names.items():
        col = INDEX if n == "SXF" else n
        moves = {k: paths[k][col] for k in paths if col in paths[k]}
        tr = returns[n] if n in returns else None
        tasks.append((n, ps, moves, aged, params, surfaces.get(n), tr, usdcad, starts, curves))
    # the same replay with the C1 rule everywhere: what the rule alone would
    # have said, set beside what the market's own vols say
    rule_tasks = [t[:5] + (None, None, None, starts, curves) for t in tasks]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        res = list(pool.map(one_name, tasks))
        res_rule = list(pool.map(one_name, rule_tasks))
    rows = pd.DataFrame([r for x in res for r in x["rows"]])
    rule_rows = pd.DataFrame([r for x in res_rule for r in x["rows"]])
    if "not_valued" in rule_rows:
        rule_rows = rule_rows[rule_rows["not_valued"].isna()]
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    say(f"Historical replay of {path_.name}, a made-up book, as it stands on each name's")
    say("latest day on file. MODEL prices: nothing below is a mark. Rates are today's curve")
    say("moved as the Treasury bill curve moved in each episode ('rates', priced last).")
    if not curves:
        say("NOTE: no bill history in data/raw/boc_history, so rates did NOT move: 'rates' is 0.")
    if "not_valued" in rows:
        nv = rows[rows["not_valued"].notna()]
        for r in nv.itertuples():
            say(f"NOT VALUED, left out of every episode: {r.position} ({r.not_valued})")
        rows = rows[rows["not_valued"].isna()]
    say(f"Options can be aged by at most {room} sessions before the book's first expiry "
        f"({first:%d %b %Y}).")
    say()

    for ep in EPISODES:
        e = rows[rows["episode"] == ep.key]
        how = (f"aged session by session ({len(sessions(ep))} sessions)" if aged[ep.key]
               else f"an INSTANT move: {len(sessions(ep))} sessions would run the options "
                    "past expiry")
        say(f"{ep.title.upper()}")
        say(f"   {ep.why}")
        say(f"   {ep.start:%d %b %Y} to {ep.end:%d %b %Y}; {how}.")
        gone = [n for n in names if (INDEX if n == "SXF" else n) in missing[ep.key]]
        if gone:
            say("   LEFT OUT, no price history for every session: "
                + ", ".join(f"{n} ({len(missing[ep.key][n])} sessions missing)" for n in gone))
        idx = paths[ep.key][INDEX]
        book = e.groupby("session")[list(PARTS)].sum()
        book["total"] = book.sum(axis=1)
        worst = book["total"].idxmin()
        say(f"   index: {100 * idx.iloc[-1]:+.1f}% at the end, lowest {100 * idx.min():+.1f}% "
            f"on {idx.idxmin():%d %b %Y}")
        table = pd.DataFrame({"at the book's worst day": book.loc[worst],
                              "at the end": book.iloc[-1]})
        say(f"   the book's worst day was {worst:%d %b %Y}, session {list(book.index).index(worst) + 1}:")
        say(table.to_string(float_format=money).replace("\n", "\n   ").join(["   ", ""]))
        # options by name; share, fund and future positions only as one line,
        # because a lone holding's P&L is its size times its price times a
        # move anyone can look up (hard rule 1)
        day_rows = e[e["session"] == worst]
        name_of = np.where(day_rows["kind"] == "option", day_rows["position"].str.split().str[0],
                           "shares, funds and futures together")
        byname = (day_rows.assign(name=name_of).groupby("name")[list(PARTS)].sum()
                  .sum(axis=1).sort_values())
        say("   by name on that day, all four parts (options; hedges as one line): "
            + ", ".join(f"{n} {money(x)}" for n, x in byname.items()))
        opt = e[e["kind"] == "option"]
        at = opt[opt["session"] == worst].assign(name=lambda t: t["position"].str.split().str[0])
        # "source", not "where": on a pandas row r.where is a method, not the column
        moved = at.groupby("name").agg(points=("vol_change", "mean"), source=("label", "first"))
        say("   the options' vol change on that day, vol points (* = from the C1 rule): "
            + ", ".join(f"{n} {r['points']:+.1f}{'*' if r['source'] == RULE_VOL else ''}"
                        for n, r in moved.iterrows()))
        counts = opt["label"].value_counts().reindex(LABELS, fill_value=0)
        say("   option-days by where the vol came from: "
            + ", ".join(f"{k} {v}" for k, v in counts.items()))
        r = rule_rows[rule_rows["episode"] == ep.key]
        rb = r.groupby("session")[list(PARTS)].sum().sum(axis=1)
        ra = r[(r["session"] == worst) & (r["kind"] == "option")]
        say(f"   with the C1 rule in place of every observed vol: {money(rb.loc[worst])} on that "
            f"day, {money(rb.iloc[-1])} at the end")
        if len(ra):
            say(f"   (the rule moved the options' vol {ra['vol_change'].mean():+.1f} points on "
                f"average that day, where the market's moved {at['vol_change'].mean():+.1f})")
        say()

    skew = vs_path is not None and vs_path.name == "vsurfd_skew.csv"
    say("WHERE THE VOL COMES FROM. For names listed in New York, the change in the vol their")
    say("New York options showed since the episode's first close (OptionMetrics), at the")
    if skew:
        say("position's own maturity AND its own strike: the strike is placed on the smile by")
        say("the delta a call there would have, recomputed each day from the moved share price,")
        say("and read between the 25-delta put, the money and the 25-delta call (flat beyond).")
        say("A skew that steepened moves a low strike more than the money.")
    else:
        say("position's own maturity, at the money only: the whole smile moves in parallel.")
    say("The Canadian dollar's part is taken out (src/nyvol.py) and the change is added to the")
    say("option's own vol today. Where no New York vol exists (the Toronto funds, a New York")
    say(f"holiday), the C1 beta rule: {a.beta:+.2f} vol points per 1% move, skew "
        f"{100 * a.steepen:.0f}% steeper past a")
    say(f"{100 * a.crash:.0f}% fall. No real settlement exists for any episode: Montreal history")
    say("on disk starts in May 2026. The WCS-WTI discount is measured apart (the _oil report):")
    say("no name's sensitivity to it is distinguishable from zero. The rate move is the bill")
    say("curve's, added to today's curve; the funding spread on top is held at today's level.")

    out = ROOT / "reports" / f"{path_.stem}_episodes.txt"
    out.write_text("Model prices on a made-up book, and moves derived from historical prices.\n"
                   "No price or quote appears in this file.\n\n" + "\n".join(lines) + "\n",
                   encoding="utf-8")
    print(f"\nsaved: {out.relative_to(ROOT)}")
    draw(rows, aged, path_.stem)


def draw(rows: pd.DataFrame, aged: dict, stem: str) -> None:
    fig, axes = plt.subplots(1, len(EPISODES), figsize=(4.2 * len(EPISODES), 3.4))
    for ax, ep in zip(axes, EPISODES):
        e = rows[rows["episode"] == ep.key]
        book = e.groupby("session")[list(PARTS)].sum()
        x = np.arange(1, len(book) + 1)
        ax.plot(x, book.sum(axis=1) / 1000, color="black", lw=1.6, label="book")
        ax.plot(x, book["shock"] / 1000, color="#1f5fa8", lw=1, label="the move")
        ax.plot(x, book[["decay", "funding", "dividends"]].sum(axis=1) / 1000, color="#c0392b",
                lw=1, label="carry")
        ax.axhline(0, color="#999", lw=0.6)
        ax.set_title(ep.title.split(":")[0] + ("" if aged[ep.key] else
                                               "\n(instant move: no carry)"), fontsize=9)
        ax.set_xlabel("sessions from the first close", fontsize=8)
        ax.tick_params(labelsize=7)
    axes[0].set_ylabel("$ thousand (model prices)", fontsize=8)
    axes[0].legend(fontsize=7, loc="lower left")
    fig.suptitle("Today's book replayed through real episodes (model prices; vol from New York "
                 "listings where one exists, the C1 rule elsewhere)", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    p = ROOT / "reports" / f"{stem}_episodes.png"
    fig.savefig(p, dpi=130)
    print(f"chart: {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
