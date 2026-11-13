"""
book_hedge.py — the closing chart: what is left of a book's P&L after each hedge (C4, C4b).

    .venv/Scripts/python scripts/book_hedge.py books/diversified.csv
    .venv/Scripts/python scripts/book_hedge.py books/bank_heavy.csv

Three stages, every one of them a hedge that could have been put on with what
was known the evening before:

  unhedged      the book's daily change in value, from the exchange's marks
                (replay.explain: carry, dividend news, delta, gamma, vol).

  delta hedged  each name's dollar delta at the previous close, sold through
                the index and (banks, energy) the sector fund, by ratios
                fitted on the 250 trading days BEFORE the day (hedge.py). The
                TSX 60 index stands in for the SXF future. The same hedge
                with ratios fitted on every day, which nobody could have
                traded, is shown beside it.

  delta + vega  on top of that, a straddle in near-the-money XIU options of
                ONE stated expiry, sized each evening so that the book's
                beta-weighted vega is zero, its own delta passed to the
                index hedge, and half the quoted spread charged on every
                contract traded. The rule is fixed here and not searched:

                  expiry   the XIU monthly nearest the book's vega-weighted
                           expiry on its first day
                  strike   nearest the share price on entry; replaced when
                           the share price has moved RESTRIKE away from it
                  size     -(sum over names of beta x dollar vega) / the
                           straddle's dollar vega, whole contracts
                  beta     the name's at-the-money vol move per point of
                           XIU's, by regression on EARLIER days only; no
                           vega hedge until MIN_BETA_DAYS of them exist

What the vega hedge cannot do is said in the report: it sells index vol
against single-name vol, and a name's vol can move on its own.

Run scripts/run_days.py for every name in the book, and for XIU, first.
Everything written to reports/ is dollars summed over a made-up book, by
day or over the period, and ratios. No price or quote is printed.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import pandas as pd

from book import load_book
from book_report import cached_days
from hedge import (autocorrelation, expected_shortfall, full_sample_ratio, ljung_box,
                   rolling_ratio)
from loaders.compustat import load_index_returns, load_total_returns
from loaders.mx import load_mx_futures, load_mx_options
from rates import load_base_curve
from replay import STEPS, DayInputs, explain, value_position
from run_days import BOC, mx_files
from surface import dividend_times
from tradingcal import is_monthly_expiry, year_fraction
from voleng import greeks_american

WRDS = ROOT / "data/raw/wrds"
WINDOW = 250                    # days behind each delta-hedge ratio
SECTOR = {"RY": "ZEB", "TD": "ZEB", "BNS": "ZEB", "BMO": "ZEB", "CM": "ZEB", "MFC": "ZEB",
          "CNQ": "XEG", "SU": "XEG", "ENB": "XEG"}
HEDGE_NAME = "XIU"
ATM_BAND = 0.03                 # strikes within 3% of the share price count as at the money
CHAIN_BAND = 0.08               # hedge strikes kept: within 8%
RESTRIKE = 0.05                 # replace the straddle once the share is 5% from its strike
MIN_BETA_DAYS = 20              # earlier days of vol moves needed before a vol beta is used


def _date(e):
    return e.date() if hasattr(e, "date") else e


def one_name(task) -> dict:
    """Runs in a worker. For one underlying: each position's daily explain
    with the dollar delta and vega it carried INTO the day, the at-the-money
    vol of each expiry the book holds, and (for the hedge name) the hedge
    expiry's near-the-money chain with its Greeks."""
    name, positions, hedge_expiry = task
    rows, atm, chain = [], [], []
    if name == "SXF":
        fut = load_mx_futures(mx_files("SXF"))      # the file's own root for SXF is TX60
        for i, pos in enumerate(positions):
            f = fut[fut["expiry"].dt.date == pos.expiry].sort_values("date")
            if f.empty:
                raise SystemExit(f"SXF {pos.expiry}: no such contract in the exchange files; "
                                 f"listed: {sorted({e.date() for e in fut['expiry']})}")
            prev = None
            for ts, px in zip(f["date"], f["settle"]):
                inp = DayInputs(ts.date(), float(px), lambda T: 0.0, [], None)
                v = value_position(pos, inp)
                if prev:
                    e = explain(prev[0], v, prev[1], inp)
                    rows.append(dict(day=inp.day, name=name, i=i, kind="future",
                                     D0=prev[0].scale * prev[1].spot, V0=0.0, expiry=None,
                                     **{k: e[k] for k in (*STEPS, "total")}))
                prev = (v, inp)
        return dict(name=name, rows=rows, atm=atm, chain=chain)

    days = cached_days(name)
    opts = load_mx_options(mx_files(name))
    opts = opts[opts["underlying"] == name]
    by_day = {ts.date(): g for ts, g in opts.groupby("date")}
    fut = load_mx_futures(mx_files("CRA"))
    fut = fut[fut["root"] == "CRA"]
    cra = [(d.date(), e.date(), float(px))
           for d, e, px in zip(fut["date"], fut["expiry"], fut["settle"])]
    held = {p.expiry for p in positions if p.kind == "option"}
    if hedge_expiry:
        held.add(hedge_expiry)
    prev: dict[int, tuple] = {}
    for day in sorted(days):
        r = days[day]
        curve = load_base_curve(BOC, day, cra)
        if r.get("spread_used") is not None:
            curve = curve.plus(r["spread_used"])
        inp = DayInputs(day, r["spot"], curve, r["divs"], by_day.get(day))
        inv = r["inverted"]
        inv = inv[inv["iv"].notna()]
        exp = pd.Series([_date(e) for e in inv["expiry"]], index=inv.index)
        money = inv["strike"] / r["spot"] - 1.0
        for e in held:
            near = inv[(exp == e) & (money.abs() <= ATM_BAND)]
            if len(near) >= 2:
                atm.append(dict(day=day, name=name, expiry=e, iv=float(near["iv"].mean()) * 100))
        if hedge_expiry:
            T = year_fraction(day, hedge_expiry)
            dts = dividend_times(day, r["divs"], hedge_expiry)
            for row in inv[(exp == hedge_expiry) & (money.abs() <= CHAIN_BAND)].itertuples():
                g = greeks_american(r["spot"], row.strike, T, curve(T), 0.0, row.iv, dts,
                                    row.call_put == "C")
                chain.append(dict(
                    day=day, strike=float(row.strike), call_put=row.call_put,
                    mark=float(row.mark), tier=row.tier, spot=r["spot"],
                    half=0.5 * float(row.ask - row.bid) if row.two_sided else np.nan,
                    delta=g.delta, vega=g.vega))
        for i, pos in enumerate(positions):
            v = value_position(pos, inp)
            if i in prev and prev[i][0].ok and v.ok:
                v0, inp0 = prev[i]
                e = explain(v0, v, inp0, inp)
                rows.append(dict(day=day, name=name, i=i, kind=pos.kind, expiry=pos.expiry,
                                 D0=v0.scale * v0.delta * inp0.spot,
                                 V0=v0.scale * (v0.vega or 0.0),
                                 **{k: e[k] for k in (*STEPS, "total")}))
            prev[i] = (v, inp)
    return dict(name=name, rows=rows, atm=atm, chain=chain)


def delta_ratios(names: list[str]) -> tuple[dict, dict, pd.DataFrame]:
    """For each name: ratios by day fitted on the WINDOW days before, the
    hindsight ratios, and the hedges' daily returns."""
    index = load_index_returns(WRDS / "index_TSX60.csv")
    funds = {f: load_total_returns(WRDS / f"prices_{f}.csv") for f in ("ZEB", "XEG")}
    X = pd.concat([index.rename("index"), funds["ZEB"].rename("ZEB"),
                   funds["XEG"].rename("XEG")], axis=1)
    rolling, hindsight = {}, {}
    for n in names:
        if n == "SXF":
            continue                                # the future IS the index: ratio one
        cols = ["index"] + ([SECTOR[n]] if n in SECTOR else [])
        y = load_total_returns(WRDS / f"prices_{n}.csv")
        rolling[n] = rolling_ratio(y, X[cols].dropna(), WINDOW)
        hindsight[n] = full_sample_ratio(y, X[cols].dropna())
    return rolling, hindsight, X


def delta_hedge(D: pd.DataFrame, rolling: dict, hindsight: dict, X: pd.DataFrame,
                honest: bool = True) -> pd.Series:
    """Dollars the index and sector hedge earns each day. D holds each
    name's dollar delta carried into the day (columns are names)."""
    out = pd.Series(0.0, index=D.index)
    for n in D.columns:
        for day, dollars in D[n].dropna().items():
            if n == "SXF":
                b = {"index": 1.0}
            elif honest:
                if day not in rolling[n].index or rolling[n].loc[day].isna().any():
                    continue
                b = rolling[n].loc[day].to_dict()
            else:
                b = hindsight[n]
            if day not in X.index or X.loc[day, list(b)].isna().any():
                continue
            out[day] -= dollars * sum(b[h] * X.loc[day, h] for h in b)
    return out


def vol_betas(atm: pd.DataFrame, hedge_expiry: date) -> pd.DataFrame:
    """For each (name, expiry) and day: the name's at-the-money vol move per
    point of the hedge's, fitted through the origin on EARLIER days only.
    NaN until MIN_BETA_DAYS earlier days exist."""
    a = atm.pivot_table(index="day", columns=["name", "expiry"], values="iv").sort_index()
    moves = a.diff()
    x = moves[(HEDGE_NAME, hedge_expiry)]
    out = {}
    for col in moves.columns:
        y, b = moves[col], pd.Series(np.nan, index=moves.index)
        ok = (x.notna() & y.notna()).to_numpy()
        xv, yv = x.to_numpy(), y.to_numpy()
        for t in range(len(moves)):
            use = ok[:t]                             # strictly earlier days
            if use.sum() >= MIN_BETA_DAYS:
                b.iloc[t] = float((xv[:t][use] * yv[:t][use]).sum()
                                  / (xv[:t][use] ** 2).sum())
        out[col] = b
    return pd.DataFrame(out)


def vega_hedge(V: pd.DataFrame, beta: pd.DataFrame, chain: pd.DataFrame) -> pd.DataFrame:
    """Day by day: the straddle carried into the day, what it earned, what
    trading it cost, and the dollar delta it carried (for the index hedge).

    V has one column per (name, expiry): dollar vega carried INTO each day.
    The size decided on an evening uses that evening's vega and betas."""
    days = sorted(chain["day"].unique())
    by_day = {d: g for d, g in chain.groupby("day")}

    def straddle(day, strike):
        g = by_day.get(day)
        if g is None:
            return None
        c = g[(g["strike"] == strike) & (g["call_put"] == "C")]
        p = g[(g["strike"] == strike) & (g["call_put"] == "P")]
        if c.empty or p.empty:
            return None
        c, p = c.iloc[0], p.iloc[0]
        return dict(mark=c["mark"] + p["mark"], vega=c["vega"] + p["vega"],
                    delta=c["delta"] + p["delta"], half=c["half"] + p["half"],
                    spot=c["spot"], soft=int(c["tier"] == "settlement_only")
                    + int(p["tier"] == "settlement_only"))

    def nearest(day):
        g = by_day[day]
        both = g.groupby("strike")["call_put"].nunique()
        ks = both[both == 2].index
        return None if len(ks) == 0 else float(ks[np.argmin(np.abs(ks - g["spot"].iloc[0]))])

    out, n, strike, last = [], 0, None, None
    # V and beta are indexed by the day the exposure is carried INTO; the
    # evening before that day is when the hedge is set
    for t, day in enumerate(days):
        row = dict(day=day, contracts_in=n, pnl=0.0, cost=0.0, delta_in=0.0, unmarked=0,
                   soft=0, restruck=0)
        now = straddle(day, strike) if n else None
        if n and now is not None and last is not None:
            row["pnl"] = n * 100.0 * (now["mark"] - last["mark"])
            row["delta_in"] = n * 100.0 * last["delta"] * last["spot"]
            row["soft"] = now["soft"]
        elif n:
            row["unmarked"] = 1                      # no mark for the strike held today
        # ---- this evening: what to carry into tomorrow --------------------
        nxt = days[t + 1] if t + 1 < len(days) else None
        target = None
        if nxt is not None and nxt in V.index:
            v, b = V.loc[nxt], beta.loc[nxt] if nxt in beta.index else None
            if b is not None:
                both = pd.concat([v, b], axis=1, keys=["v", "b"]).dropna()
                both = both[both["v"] != 0.0]
                held = v.dropna()
                held = held[held != 0.0]             # a position with no mark carries no vega row
                if len(both) == len(held) and len(held):     # a beta for every vega held
                    target = float((both["v"] * both["b"]).sum())
        new_strike = strike
        if target is not None:
            spot = by_day[day]["spot"].iloc[0]
            if strike is None or now is None or abs(spot / strike - 1.0) > RESTRIKE:
                new_strike = nearest(day)
        fresh = straddle(day, new_strike) if new_strike is not None else None
        want = 0
        if target is not None and fresh is not None and fresh["vega"] > 0:
            want = int(round(-target / (100.0 * fresh["vega"])))
        half_now = now["half"] if now is not None and np.isfinite(now["half"]) else 0.0
        half_new = fresh["half"] if fresh is not None and np.isfinite(fresh["half"]) else 0.0
        if new_strike != strike and n:
            row["cost"] += abs(n) * 100.0 * half_now + abs(want) * 100.0 * half_new
            row["restruck"] = 1
        else:
            row["cost"] += abs(want - n) * 100.0 * (half_new if fresh is not None else half_now)
        n, strike, last = want, new_strike, fresh
        row["contracts_out"] = n
        out.append(row)
    return pd.DataFrame(out).set_index("day")


def stats(x: pd.Series, base: pd.Series) -> dict:
    x = x.dropna()
    lb, struct = ljung_box(x)
    return {"daily st. dev.": x.std(), "variance removed %": 100 * (1 - x.var() / base.var()),
            "97.5% shortfall": expected_shortfall(x), "worst day": x.min(),
            "sum": x.sum(), "lag-1 autocorr.": autocorrelation(x),
            "structure over 10 lags": "yes" if struct else "no"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("book")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    path = Path(a.book)
    book = load_book(path)
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    names: dict[str, list] = {}
    for p in book:
        names.setdefault(p.underlying, []).append(p)
    names.setdefault(HEDGE_NAME, [])

    # the hedge expiry: decided once, from the book as first written
    opt = [p for p in book if p.kind == "option"]
    first = min(cached_days(HEDGE_NAME))
    listed = sorted({_date(e) for e in cached_days(HEDGE_NAME)[first]["inverted"]["expiry"]})
    listed = [e for e in listed if is_monthly_expiry(e)]
    weight = np.array([abs(p.quantity * p.multiplier) for p in opt], dtype=float)
    mean_days = float((weight * np.array([(p.expiry - first).days for p in opt])).sum()
                      / weight.sum())
    hedge_expiry = min(listed, key=lambda e: abs((e - first).days - mean_days))

    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        res = list(pool.map(one_name, [(n, ps, hedge_expiry if n == HEDGE_NAME else None)
                                       for n, ps in names.items()]))
    rows = pd.DataFrame([r for x in res for r in x["rows"]])
    rows["day"] = pd.to_datetime(rows["day"])
    atm = pd.DataFrame([r for x in res for r in x["atm"]])
    atm["day"] = pd.to_datetime(atm["day"])
    chain = pd.DataFrame(next(x["chain"] for x in res if x["name"] == HEDGE_NAME))
    chain["day"] = pd.to_datetime(chain["day"])

    daily = rows.groupby("day")[[*STEPS, "total"]].sum()
    D = rows.pivot_table(index="day", columns="name", values="D0", aggfunc="sum")
    rolling, hindsight, X = delta_ratios(list(D.columns) + [HEDGE_NAME])
    h1 = delta_hedge(D, rolling, hindsight, X)
    h1_hind = delta_hedge(D, rolling, hindsight, X, honest=False)

    o = rows[rows["kind"] == "option"]
    V = o.pivot_table(index="day", columns=["name", "expiry"], values="V0", aggfunc="sum")
    beta = vol_betas(atm, hedge_expiry)
    beta.index = pd.to_datetime(beta.index)
    vh = vega_hedge(V, beta.reindex(columns=V.columns), chain)
    vh = vh.reindex(daily.index).fillna(0.0)
    h2_delta = delta_hedge(vh[["delta_in"]].rename(columns={"delta_in": HEDGE_NAME}),
                           rolling, hindsight, X)

    stage0 = daily["total"]
    stage1 = stage0 + h1
    stage1_hind = stage0 + h1_hind
    stage2 = stage1 + vh["pnl"] + h2_delta - vh["cost"]
    on = vh["contracts_in"] != 0

    say(f"Closing chart for {path.name}, a made-up book: {daily.index.min():%d %b %Y} to "
        f"{daily.index.max():%d %b %Y}, {len(daily)} days.")
    say("Dollars a day, summed over positions. Every hedge is set from what was known the")
    say("evening before. The TSX 60 index stands in for the SXF future.")
    say()
    say("1. THREE STAGES")
    t = pd.DataFrame({"unhedged": stats(stage0, stage0),
                      "delta hedged": stats(stage1, stage0),
                      "delta hedged, hindsight ratios": stats(stage1_hind, stage0),
                      "delta + vega hedged": stats(stage2, stage0)}).T
    say(t.to_string(float_format=lambda x: f"{x:,.2f}" if abs(x) < 1 else f"{x:,.0f}")
        .replace("\n", "\n   ").join(["   ", ""]))
    say("   'variance removed' is against the unhedged book. 'hindsight ratios' are fitted on")
    say("   every day at once: nobody could have traded them, and the gap to the row above")
    say("   is what honesty costs.")
    if on.sum() >= 10:
        s1, s2 = stage1[on], stage2[on]
        say(f"   On the {int(on.sum())} days a vega hedge was on: daily st. dev. "
            f"{s1.std():,.0f} delta hedged, {s2.std():,.0f} with the vega hedge "
            f"({100 * (1 - s2.var() / s1.var()):+.0f}% of that variance removed).")
    say()
    say("2. WHAT THE DELTA HEDGE LEAVES, AND WHAT IT IS MADE OF")
    parts = pd.DataFrame({"delta the hedge missed": daily["delta"] + h1,
                          "gamma": daily["gamma"], "vol": daily["vol"],
                          "carry": daily["carry"], "dividend news": daily["dividends"]})
    comp = pd.DataFrame({"daily st. dev.": parts.std(), "sum": parts.sum(),
                         "share of the residual's variance %":
                             [100 * parts[c].cov(stage1) / stage1.var() for c in parts]})
    say(comp.to_string(float_format=lambda x: f"{x:,.0f}").replace("\n", "\n   ")
        .join(["   ", ""]))
    say("   The shares add to 100. 'delta the hedge missed' is each name moving apart from")
    say("   the index and its sector: nothing listed here hedges it. Gamma and vol are the")
    say("   option part a future cannot reach.")
    hedge_atm = atm[(atm["name"] == HEDGE_NAME) & (atm["expiry"] == hedge_expiry)] \
        .set_index("day")["iv"].diff()
    say(f"   Correlation of what is left with the day's move in {HEDGE_NAME} at-the-money vol "
        f"(the instrument not yet used): {stage1.corr(hedge_atm):+.2f}; of its vol part alone: "
        f"{daily['vol'].corr(hedge_atm):+.2f}.")
    say()
    say(f"3. THE VEGA HEDGE: {HEDGE_NAME} {hedge_expiry:%d %b %Y} straddle")
    say(f"   days with a hedge on: {int(on.sum())} of {len(daily)} (none until every vega "
        f"held has {MIN_BETA_DAYS} earlier days of vol moves behind its beta)")
    if on.any():
        b = beta.reindex(columns=V.columns).dropna(how="all")
        by_name = b.T.groupby(level=0).mean().T
        say("   vol beta to the hedge, by name (mean; first and last day's value):")
        say(pd.DataFrame({"mean": by_name.mean(), "first": by_name.bfill().iloc[0],
                          "last": by_name.ffill().iloc[-1]}).round(2).to_string()
            .replace("\n", "\n   ").join(["   ", ""]))
        say(f"   contracts held: mean {vh.loc[on, 'contracts_in'].mean():+.0f}, from "
            f"{vh['contracts_in'].min():+.0f} to {vh['contracts_in'].max():+.0f}; "
            f"re-struck {int(vh['restruck'].sum())} times")
        say(f"   hedge P&L {vh['pnl'].sum():+,.0f}; its delta's hedge {h2_delta.sum():+,.0f}; "
            f"trading cost at half the quoted spread {-vh['cost'].sum():,.0f} "
            f"({vh['cost'].sum() / max(on.sum(), 1):,.0f} a day)")
        say(f"   days the strike held had no mark: {int(vh['unmarked'].sum())}; hedge marks "
            f"resting on a settlement alone: {int(vh['soft'].sum())} (model-free but untraded)")
        v_only = daily["vol"][on]
        say(f"   the book's vol P&L on those days has a daily st. dev. of {v_only.std():,.0f}; "
            f"with the hedge's P&L added, {(v_only + vh['pnl'][on] + h2_delta[on]).std():,.0f}.")
    say("   What it cannot reach: it sells index vol against single-name vol. A name whose")
    say("   vol moves on its own (earnings, a dividend surprise) is not hedged by it, and a")
    say("   beta fitted from noisy at-the-money vols is biased toward zero, so the hedge is")
    say("   on the small side.")
    say()
    say("4. THE FIVE WORST UNHEDGED DAYS, stage by stage")
    worst = pd.DataFrame({"unhedged": stage0, "delta hedged": stage1,
                          "delta + vega hedged": stage2}).sort_values("unhedged").head(5)
    worst.index = worst.index.strftime("%Y-%m-%d")
    say(worst.to_string(float_format=lambda x: f"{x:,.0f}").replace("\n", "\n   ")
        .join(["   ", ""]))

    out = ROOT / "reports" / f"{path.stem}_hedge.txt"
    out.write_text("Derived statistics only: dollars summed over a made-up book, and ratios.\n"
                   "No price, quote or per-position value appears in this file.\n\n"
                   + "\n".join(lines) + "\n", encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6),
                                   gridspec_kw={"width_ratios": [3, 2]})
    for s, label, colour in ((stage0, "unhedged", "#999999"),
                             (stage1, "delta hedged", "#1f77b4"),
                             (stage2, "delta + vega hedged", "#d62728")):
        ax1.plot(s.index, s.cumsum(), label=label, color=colour, lw=1.3)
    ax1.axhline(0, color="black", lw=0.5)
    ax1.set_ylabel("cumulative dollars")
    ax1.set_title(f"{path.stem}: the book's P&L after each hedge", fontsize=10)
    ax1.legend(fontsize=8, frameon=False)
    sd = [stage0.std(), stage1.std(), stage2.std()]
    ax2.bar(["unhedged", "delta\nhedged", "delta + vega\nhedged"], sd,
            color=["#999999", "#1f77b4", "#d62728"])
    for i, v in enumerate(sd):
        ax2.text(i, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=9)
    ax2.set_ylabel("daily standard deviation, dollars")
    ax2.set_title("what is left: gamma, single-name moves,\n"
                  "and single-name vol moving apart from index vol", fontsize=9)
    fig.autofmt_xdate()
    fig.tight_layout()
    png = ROOT / "reports" / f"{path.stem}_hedge.png"
    fig.savefig(png, dpi=130)
    print(f"\nsaved: {out.relative_to(ROOT)}, {png.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
