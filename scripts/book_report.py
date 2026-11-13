"""
book_report.py — one book, every day on file: marked, explained, and its marks tested.

    .venv/Scripts/python scripts/book_report.py books/bank_heavy.csv
    .venv/Scripts/python scripts/book_report.py books/diversified.csv --bars 0.5

Run scripts/run_days.py for each name in the book first: this reads the
share price, the funding spread and the dividends that run produced for each
day, and prices nothing about the market again.

What it answers, in this order
------------------------------
  1. Could every position be marked, and from what (quote, trade, settlement)?
  2. Where do the book's Greeks sit, by name and maturity?
  3. Why did the book's value change each day? Each option is repriced one
     input at a time (carry, dividend news, share, vol), so the parts add up
     to the change in the marks with nothing left over.
  4. WHICH MARKS ARE IN DISPUTE, and how many dollars of the day's P&L rest
     on them? This is the part a Greeks report does not have: every option
     mark is tested against its call or put partner and against the smile of
     its neighbours before its P&L is believed.
  5. What would a wrong dividend cost, in dollars: 5% on the amount, one
     session on the date, and two errors this project actually found.

Everything written to reports/ is an aggregate in dollars for a made-up
book: sums over positions, counts and vol-point differences. No price,
quote or per-position value is printed (hard rule 1).
"""

from __future__ import annotations

import argparse
import pickle
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
from loaders.mx import load_mx_futures, load_mx_options
from rates import load_base_curve
from references import reference_differences
from replay import (STEPS, DayInputs, check_mark, dispute_dollars, dividend_exposure,
                    explain, greeks_table, reprice_with, value_position)
from run_days import BOC, fingerprint, mx_files

# two dividend errors this project found, priced on the book (section 5)
RY_IMPLIED_SHORTFALL = 0.05      # options price RY's projected dividends about 5 cents lower
XIU_WRONG, XIU_RIGHT = date(2026, 11, 19), date(2026, 11, 25)


def whole(t: pd.DataFrame) -> str:
    """A table in whole dollars; a figure that does not exist prints as NA."""
    return t.to_string(float_format=lambda x: f"{x:,.0f}", na_rep="NA")


def cached_days(name: str) -> dict[date, dict]:
    """One result per day from the day runner's cache: the run with a funding
    spread where there is one, otherwise the run without."""
    folder = ROOT / "data/derived" / name.lower() / fingerprint(name)
    out: dict[date, dict] = {}
    for f in sorted(folder.glob("*_base.pkl")) + sorted(folder.glob("*_base_s*.pkl")):
        r = pickle.loads(f.read_bytes())           # this project's own cache files
        if r["ok"]:
            out[r["date"]] = r                     # a funded run replaces the base one
    return out


def one_name(task) -> dict:
    """Runs in a worker: everything for one underlying's positions."""
    name, positions, bars = task
    rows, last = [], {}
    if name == "SXF":
        fut = load_mx_futures(mx_files("SXF"))      # the file's own root for SXF is TX60
        for pos in positions:
            f = fut[fut["expiry"].dt.date == pos.expiry].sort_values("date")
            if f.empty:
                # Until 12 November 2026 this was skipped in silence: the filter
                # asked for a root the file does not use, and the sample book had
                # the last trading day where the file has the expiry.
                raise SystemExit(f"SXF {pos.expiry}: no such contract in the exchange files; "
                                 f"listed: {sorted({e.date() for e in fut['expiry']})}")
            prev = None
            for ts, px in zip(f["date"], f["settle"]):
                inp = DayInputs(ts.date(), float(px), lambda T: 0.0, [], None)
                v = value_position(pos, inp)
                e = explain(prev[0], v, prev[1], inp) if prev else None
                rows.append(dict(day=inp.day, name=name, kind="future", valued=True,
                                 tier="settlement_only", funded=True,
                                 **({k: e[k] for k in (*STEPS, "total")} if e else {})))
                prev = (v, inp)
        return dict(name=name, rows=rows, last={})

    days = cached_days(name)
    opts = load_mx_options(mx_files(name))
    opts = opts[opts["underlying"] == name]
    by_day = {ts.date(): g for ts, g in opts.groupby("date")}
    fut = load_mx_futures(mx_files("CRA"))
    fut = fut[fut["root"] == "CRA"]
    cra = [(d.date(), e.date(), float(px))
           for d, e, px in zip(fut["date"], fut["expiry"], fut["settle"])]
    expiries = {p.expiry for p in positions if p.kind == "option"}
    prev: dict[int, tuple] = {}
    first: dict[int, tuple] = {}
    for day in sorted(days):
        r = days[day]
        curve = load_base_curve(BOC, day, cra)
        if r.get("spread_used") is not None:
            curve = curve.plus(r["spread_used"])
        inv = r["inverted"]
        exp = pd.Series([e.date() if hasattr(e, "date") else e for e in inv["expiry"]],
                        index=inv.index)
        near = inv[exp.isin(expiries)]
        # the references for the book's own expiries only: an expiry's smile
        # needs that expiry's strikes and nothing else
        checks = reference_differences(near, r["spot"], divs=r["divs"]) if len(near) else None
        inp = DayInputs(day, r["spot"], curve, r["divs"], by_day.get(day), checks)
        for i, pos in enumerate(positions):
            v = value_position(pos, inp)
            c = check_mark(v, checks, bars)
            row = dict(day=day, name=name, kind=pos.kind, i=i, valued=v.ok, reason=v.reason,
                       tier=v.tier, funded=r.get("spread_used") is not None,
                       failed=",".join(c.failed), notes="; ".join(c.notes),
                       pair=c.pair, smile=c.smile, dispute=dispute_dollars(v, c),
                       series=(f"{name} {pos.expiry:%b %Y} {pos.strike:g} {pos.call_put}"
                               if pos.kind == "option" else f"{name} shares"))
            if i in prev and prev[i][0].ok and v.ok:
                e = explain(prev[i][0], v, prev[i][1], inp)
                row.update({k: e[k] for k in (*STEPS, "total")})
                row["gap_days"] = (day - prev[i][1].day).days
            rows.append(row)
            prev[i] = (v, inp)
            if v.ok:
                first.setdefault(i, (v, inp))
    # the latest day: Greeks and dividend exposure
    if prev:
        vs = [prev[i][0] for i in sorted(prev)]
        inp = prev[min(prev)][1]
        g = greeks_table(vs, {name: inp.spot})
        expo = [dividend_exposure(v, inp) for v in vs]
        named = None
        if name == "RY":
            alt = [(d, c - RY_IMPLIED_SHORTFALL) if d > inp.day and d.year >= 2027 else (d, c)
                   for d, c in inp.divs]
            named = ("RY's 2027 dividends 5 cents a quarter lower, as its options imply",
                     sum(x for x in (reprice_with(v, inp, alt) for v in vs) if x is not None))
        early = [dividend_exposure(v, i0) for v, i0 in first.values()]
        last = dict(day=inp.day, greeks=g,
                    amount_first=sum(e["amount"] for e in early if e),
                    date_first=sum(e["date"] for e in early if e),
                    amount=sum(e["amount"] for e in expo if e),
                    date=sum(e["date"] for e in expo if e), named=named)
        if name == "XIU":
            # price the wrong distribution date on the last day before it
            early = max((d for d in days if d < XIU_WRONG), default=None)
            if early and any(d == XIU_RIGHT for d, _ in days[early]["divs"]):
                r = days[early]
                curve = load_base_curve(BOC, early, cra)
                if r.get("spread_used") is not None:
                    curve = curve.plus(r["spread_used"])
                inp2 = DayInputs(early, r["spot"], curve, r["divs"], by_day.get(early))
                alt = [(XIU_WRONG, c) if d == XIU_RIGHT else (d, c) for d, c in r["divs"]]
                total = sum(x for x in (reprice_with(value_position(p, inp2), inp2, alt)
                                        for p in positions) if x is not None)
                last["named"] = (f"XIU's distribution on 19 November, not 25 (valued {early})",
                                 total)
    return dict(name=name, rows=rows, last=last)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("book")
    ap.add_argument("--bars", type=float, default=1.0,
                    help="a mark fails a check beyond this many quote bars (default 1)")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    path = Path(a.book)
    book = load_book(path)
    stem = path.stem
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    names: dict[str, list] = {}
    for p in book:
        names.setdefault(p.underlying, []).append(p)
    missing = [n for n in names if not mx_files(n)]
    if missing:
        raise SystemExit(f"no exchange files for {', '.join(missing)}")
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        results = list(pool.map(one_name, [(n, ps, a.bars) for n, ps in names.items()]))
    never = [res["name"] for res in results if not res["rows"]]
    if never:
        raise SystemExit(f"no day could be valued for {', '.join(never)}: a position that "
                         "is never valued must not vanish from the totals")
    rows = pd.DataFrame([r for res in results for r in res["rows"]])
    rows["day"] = pd.to_datetime(rows["day"])
    opt = rows[rows["kind"] == "option"]

    say(f"Book {path.name}: {len(book)} positions on {len(names)} underlyings "
        f"({sum(p.kind == 'option' for p in book)} options), a made-up book.")
    say(f"Days: {rows['day'].min():%d %b %Y} to {rows['day'].max():%d %b %Y}, "
        f"{rows['day'].nunique()} with at least one position valued.")
    say("All figures are dollars summed over positions, or counts. Marks are the exchange's")
    say("(quote midpoint where two-sided, settlement otherwise): real, not model prices.")

    # ---- 1. coverage ------------------------------------------------------
    say()
    say("1. COULD EVERY POSITION BE MARKED?")
    n = len(opt)
    ok = int(opt["valued"].sum())
    say(f"   option position-days: {n}; valued {ok} ({100 * ok / n:.1f}%); not valued {n - ok}")
    if n - ok:
        say("   not valued, by reason: "
            + str(opt.loc[~opt["valued"], "reason"].value_counts().to_dict()))
    say("   evidence behind the marks used: "
        + str(opt.loc[opt["valued"], "tier"].value_counts().to_dict()))
    unfunded = sorted(opt.loc[~opt["funded"], "name"].unique())
    if len(unfunded):
        say(f"   days priced with no funding spread (none fitted yet): "
            f"{int((~opt['funded']).sum())} position-days, on {', '.join(unfunded)}")

    # ---- 2. Greeks --------------------------------------------------------
    say()
    last_day = max(res["last"]["day"] for res in results if res["last"])
    say(f"2. WHERE THE GREEKS SIT, latest day ({last_day:%d %b %Y}), dollars:")
    say("   delta = dollars of share held; gamma = dollars of delta gained per 1% share move;")
    say("   vega = per vol point; theta = per calendar day.")
    g = pd.concat([res["last"]["greeks"] for res in results if res["last"]])
    # Options only. A share or future position's dollar delta is its size times
    # the price, so printing it would publish a price (hard rule 1). Its P&L is
    # in section 3, summed with everything else.
    g = g[g.index.get_level_values("bucket") != "shares"]
    say("   options only; share, fund and future positions are left out of this table")
    say(g[["delta", "gamma", "vega", "theta", "not_valued"]].pipe(whole).replace("\n", "\n   ").join(["   ", ""]))
    by_bucket = g.groupby("bucket")[["delta", "gamma", "vega", "theta"]].sum()
    say("   by maturity, all names (vega on different names added dollar for dollar, which")
    say("   treats one name's vol point as another's; no beta weighting yet):")
    say(by_bucket.pipe(whole).replace("\n", "\n   ").join(["   ", ""]))

    # ---- 3. P&L explained -------------------------------------------------
    say()
    say("3. WHY THE BOOK'S VALUE CHANGED, day by day")
    ex = rows[rows["total"].notna()]
    daily = ex.groupby("day")[[*STEPS, "total"]].sum()
    lost = int((rows["total"].isna() & (rows.groupby(["name", "i"], dropna=False)
                                        .cumcount() > 0)).sum()) if "i" in rows else 0
    say(f"   {len(daily)} days explained; position-days with no explanation because a mark "
        f"was missing on that day or the day before: {lost}")
    summary = pd.DataFrame({"sum over the period": daily.sum(),
                            "daily st. dev.": daily.std(),
                            "worst day": daily.min(), "best day": daily.max()})
    var = daily["total"].var()
    summary["share of daily variance %"] = [
        100 * daily[k].cov(daily["total"]) / var for k in daily.columns]
    say(summary.pipe(whole).replace("\n", "\n   ").join(["   ", ""]))
    say("   carry = a day passing (decay, funding, rates); dividends = a change in the")
    say("   schedule; delta and gamma = the share moving; vol = implied vol moving. The five")
    say("   add up to the change in the marks exactly. 'share of daily variance' is each")
    say("   part's covariance with the total, so the column adds to 100.")
    worst = daily.sort_values("total").head(5)
    say("   five worst days:")
    say(worst.pipe(whole).replace("\n", "\n   ").join(["   ", ""]))

    # ---- 4. marks in dispute ---------------------------------------------
    say()
    say(f"4. WHICH MARKS ARE IN DISPUTE (a check fails beyond {a.bars:g} quote bar(s) and the")
    say("   solver's noise floor of 0.04 vol points)")
    v = opt[opt["valued"]]
    checked = v[v["pair"].notna() | v["smile"].notna()]
    flagged = v[v["failed"] != ""]
    say(f"   valued option position-days: {len(v)}; with at least one reference: "
        f"{len(checked)} ({100 * len(checked) / max(len(v), 1):.1f}%); failed a check: "
        f"{len(flagged)} ({100 * len(flagged) / max(len(checked), 1):.1f}% of those checked)")
    say("   failed, by check: " + str({k: int(flagged["failed"].str.contains(k).sum())
                                       for k in ("pair", "smile")}))
    uncheck = v[v["pair"].isna() & v["smile"].isna()]
    if len(uncheck):
        say(f"   not checkable: {len(uncheck)} position-days, "
            + str(uncheck["notes"].value_counts().head(3).to_dict()))
    say(f"   marks resting on a settlement alone: {int((v['tier'] == 'settlement_only').sum())}")
    d = v.groupby("day").agg(dispute=("dispute", "sum"), flagged=("failed", lambda s: (s != "").sum()))
    d = d.join(daily["total"].abs().rename("abs_pnl"), how="left")
    say(f"   days with at least one failed mark: {int((d['flagged'] > 0).sum())} of {len(d)}")
    say(f"   dollars in dispute per day (position vega x disagreement): mean "
        f"{d['dispute'].mean():,.0f}, median {d['dispute'].median():,.0f}, "
        f"largest {d['dispute'].max():,.0f}")
    ratio = (d["dispute"] / d["abs_pnl"]).replace([np.inf, -np.inf], np.nan).dropna()
    say(f"   against the day's P&L (absolute): the dispute is more than 10% of it on "
        f"{int((ratio > 0.10).sum())} days and more than 50% on {int((ratio > 0.50).sum())} "
        f"of {len(ratio)}")
    # A desk hedges the share move, so the P&L it answers for is what is left.
    hedged = (daily["total"] - daily["delta"]).abs().rename("abs_hedged")
    r2 = (d["dispute"] / d.join(hedged)["abs_hedged"]).replace([np.inf, -np.inf], np.nan).dropna()
    say(f"   against the day's P&L with the share move taken out (carry, gamma, vol and "
        f"dividends;")
    say(f"   daily st. dev. {(daily['total'] - daily['delta']).std():,.0f}): the dispute is "
        f"more than 10% of it on {int((r2 > 0.10).sum())} days, more than 50% on "
        f"{int((r2 > 0.50).sum())} and larger than it on {int((r2 > 1.0).sum())} of {len(r2)}")
    fp = ex[ex["failed"].fillna("") != ""]
    say(f"   P&L booked on marks that failed a check that day: {fp['total'].abs().sum():,.0f} "
        f"of {ex.loc[ex['kind'] == 'option', 'total'].abs().sum():,.0f} "
        "(sum of absolute daily option P&L)")
    if len(flagged):
        top = flagged.groupby("series").agg(
            days=("day", "count"), mean_abs_pair=("pair", lambda x: x.abs().mean()),
            mean_abs_smile=("smile", lambda x: x.abs().mean()),
            mean_dispute=("dispute", "mean")).sort_values("days", ascending=False).head(8)
        say("   series flagged most often (vol points; dollars per flagged day):")
        say(top.round(2).to_string().replace("\n", "\n   ").join(["   ", ""]))
        vol_only = ex[(ex["failed"].fillna("") != "")]["vol"].abs().sum()
        say(f"   of the P&L on failed marks, the vol step is {vol_only:,.0f}: a mark that is")
        say("   off shows up as a vol move today and its reversal later.")

    # ---- 5. dividends -----------------------------------------------------
    say()
    say("5. WHAT A WRONG DIVIDEND WOULD COST, dollars (vol and share price held): every")
    say("   dividend in each option's life 5% larger, and every ex-date one session later, on")
    say("   the first day each position was valued and on the latest")
    div = pd.DataFrame([dict(name=res["name"], **{
        "5% larger, first day": res["last"]["amount_first"],
        "one session later, first day": res["last"]["date_first"],
        "5% larger, latest day": res["last"]["amount"],
        "one session later, latest day": res["last"]["date"]})
        for res in results if res["last"]]).set_index("name")
    div.loc["BOOK"] = div.sum()
    say(div.pipe(whole).replace("\n", "\n   ").join(["   ", ""]))
    say(f"   for scale: one vol point on the whole book is {g['vega'].sum():,.0f}; "
        f"one day's decay is {g['theta'].sum():,.0f}")
    for res in results:
        if res["last"] and res["last"].get("named"):
            label, dollars = res["last"]["named"]
            say(f"   found by this project: {label}: {dollars:+,.0f}")

    out = ROOT / "reports" / f"{stem}_book.txt"
    out.write_text("Derived statistics only: dollars summed over a made-up book, and counts.\n"
                   "No price, quote or per-position value appears in this file.\n\n"
                   + "\n".join(lines) + "\n", encoding="utf-8")

    # ---- chart ------------------------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]})
    pos_base, neg_base = np.zeros(len(daily)), np.zeros(len(daily))
    colours = dict(carry="#888888", dividends="#b8860b", delta="#1f77b4",
                   gamma="#2ca02c", vol="#d62728")
    for k in STEPS:
        y = daily[k].to_numpy()
        base = np.where(y >= 0, pos_base, neg_base)
        ax1.bar(daily.index, y, bottom=base, color=colours[k], label=k, width=0.9)
        pos_base, neg_base = pos_base + np.clip(y, 0, None), neg_base + np.clip(y, None, 0)
    ax1.plot(daily.index, daily["total"], color="black", lw=0.8, label="change in marks")
    ax1.axhline(0, color="black", lw=0.5)
    ax1.set_ylabel("dollars a day")
    ax1.set_title(f"{stem}: why the book's value changed each day (the parts add up to the "
                  "change in the exchange's marks)", fontsize=10)
    ax1.legend(ncol=6, fontsize=8, frameon=False)
    ax2.bar(d.index, d["dispute"], color="#d62728", width=0.9)
    ax2.set_ylabel("dollars in dispute")
    ax2.set_title("dollars resting on marks that fail a check (position vega x the "
                  "disagreement with the call/put partner or the smile)", fontsize=10)
    fig.autofmt_xdate()
    fig.tight_layout()
    png = ROOT / "reports" / f"{stem}_book.png"
    fig.savefig(png, dpi=130)
    print(f"\nsaved: {out.relative_to(ROOT)}, {png.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
