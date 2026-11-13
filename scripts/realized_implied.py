"""
realized_implied.py — implied vol against the vol that followed (B9).

    .venv/Scripts/python scripts/realized_implied.py

For every name with option files and every MONTHLY expiry that has already
expired inside the data:

  the gap        at-the-money implied vol on the first day of the window,
                 minus the vol the share then realized up to expiry. Windows
                 do not overlap: each starts the session after the previous
                 monthly expiry. Positive means the option was priced for
                 more movement than came.
  the straddle   one at-the-money straddle bought on that first day AT THE
                 ASK and held to expiry, its delta hedged at each close; and
                 the same straddle sold AT THE BID. Shown per expiry as what
                 happened. It is not a strategy and is not tested as one.
  the skew       the 25-delta risk reversal and butterfly on the monthly
                 expiry nearest 45 days out, by day.

Run scripts/run_days.py for each name first; this reads that cache.

What is published, and what is not. reports/realized_implied.txt holds gaps
in vol points, P&L as a percentage of the premium, counts and ratios. It
does NOT print an implied vol level or a premium: an at-the-money vol on a
named day is close to a price (hard rule 1). The levels behind each gap are
written to data/derived/realized/, which is not published.

Six months gives five windows a name. That is stated beside every figure.
"""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import pandas as pd

from book_report import cached_days
from loaders.mx import load_mx_futures, load_mx_options
from rates import load_base_curve
from book import Position
from realized import atm_vol, hedge_pnl, realized_vol, risk_reversal_butterfly
from replay import DayInputs, value_position
from run_days import BOC, mx_files
from tradingcal import is_monthly_expiry, year_fraction

NAMES = ["RY", "TD", "BNS", "BMO", "MFC", "CNQ", "SU", "ENB", "CNR", "BCE", "ABX", "SHOP",
         "XIU", "ZEB", "XEG"]
MIN_WINDOW_DAYS = 14            # a window shorter than this says little
SKEW_DAYS = (25, 70)            # the expiry used for the risk reversal: nearest 45 days out


def _date(e):
    return e.date() if hasattr(e, "date") else e


def one_name(name: str) -> dict:
    """Runs in a worker. Derived numbers only come back."""
    days = cached_days(name)
    order = sorted(days)
    opts = load_mx_options(mx_files(name))
    opts = opts[opts["underlying"] == name]
    by_day = {ts.date(): g for ts, g in opts.groupby("date")}
    fut = load_mx_futures(mx_files("CRA"))
    fut = fut[fut["root"] == "CRA"]
    cra = [(d.date(), e.date(), float(px))
           for d, e, px in zip(fut["date"], fut["expiry"], fut["settle"])]

    def rows(day, expiry):
        inv = days[day]["inverted"]
        inv = inv[inv["iv"].notna()]
        return inv[[_date(e) == expiry for e in inv["expiry"]]]

    expiries = sorted({_date(e) for d in order for e in days[d]["inverted"]["expiry"]})
    expired = [e for e in expiries if is_monthly_expiry(e) and e in days]
    windows, start = [], order[0]
    for e in expired:
        if (e - start).days >= MIN_WINDOW_DAYS:
            windows.append((start, e))
        later = [d for d in order if d > e]
        if not later:
            break
        start = later[0]

    out = []
    for entry, expiry in windows:
        r0 = days[entry]
        chain = rows(entry, expiry)
        T = year_fraction(entry, expiry)
        implied = atm_vol(chain["strike"], chain["iv"], r0["spot"]) if len(chain) else None
        span = [d for d in order if entry <= d <= expiry]
        divs = days[expiry]["divs"]
        spots = [days[d]["spot"] for d in span]
        cash = [0.0] + [sum(c for x, c in divs if a < x <= b) for a, b in zip(span, span[1:])]
        rets = [np.log((s1 + c) / s0) for s0, s1, c in zip(spots, spots[1:], cash[1:])]
        row = dict(name=name, entry=entry, expiry=expiry, sessions=len(span) - 1,
                   implied=implied, realized=realized_vol(rets, T))
        # ---- the straddle --------------------------------------------------
        book = by_day.get(entry)
        if book is not None:
            e_ = book[(pd.to_datetime(book["expiry"]).dt.date == expiry) & book["two_sided"]]
            both = e_.groupby("strike")["call_put"].nunique()
            ks = both[both == 2].index.to_numpy(dtype=float)
            if len(ks):
                K = float(ks[np.argmin(np.abs(ks - r0["spot"]))])
                leg = e_[e_["strike"] == K]
                ask, bid = float(leg["ask"].sum()), float(leg["bid"].sum())
                legs = [Position("option", name, 1.0, 1.0, "MX", cp, K, expiry) for cp in "CP"]
                deltas, missing = [], 0
                for d in span[:-1]:
                    r = days[d]
                    curve = load_base_curve(BOC, d, cra)
                    if r.get("spread_used") is not None:
                        curve = curve.plus(r["spread_used"])
                    inp = DayInputs(d, r["spot"], curve, r["divs"], by_day.get(d))
                    total = 0.0
                    for leg_ in legs:
                        v = value_position(leg_, inp)       # the day's own mark, inverted
                        if v.ok:
                            total += v.delta
                        else:
                            # No vol can be read from this mark (often a few days
                            # from expiry and well in the money). Its delta is then
                            # all or nothing, which is what such an option has left.
                            missing += 1
                            in_money = (r["spot"] > K) if leg_.call_put == "C" else (r["spot"] < K)
                            total += (1.0 if leg_.call_put == "C" else -1.0) if in_money else 0.0
                    deltas.append(total)
                deltas.append(0.0)
                payoff = abs(spots[-1] - K)
                h = hedge_pnl(deltas, spots, cash)
                row.update(long_at_ask=100 * (payoff + h - ask) / ask,
                           short_at_bid=100 * (bid - payoff - h) / bid,
                           spread=100 * (ask - bid) / (0.5 * (ask + bid)),
                           hedge_days_missing=missing,
                           off_the_money=100 * abs(K / r0["spot"] - 1.0))
        out.append(row)

    skew = []
    for d in order:
        r = days[d]
        near = [e for e in {_date(x) for x in r["inverted"]["expiry"]}
                if is_monthly_expiry(e) and SKEW_DAYS[0] <= (e - d).days <= SKEW_DAYS[1]]
        if not near:
            continue
        # sorted first: `near` comes from a set, whose order changes from run to
        # run, and two monthlies can be equally far from 45 days. Unsorted, the
        # tie went either way and the table moved in the second decimal between
        # two runs of the same code. The nearer expiry now wins a tie.
        e = min(sorted(near), key=lambda x: abs((x - d).days - 45))
        chain = rows(d, e)
        got = risk_reversal_butterfly(chain["strike"], chain["iv"], r["spot"],
                                      year_fraction(d, e), r["spot"]) if len(chain) else None
        if got:
            skew.append(dict(name=name, day=d, rr=got[0], bf=got[1]))
    return dict(windows=out, skew=skew, days=len(order))


def main() -> None:
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    with ProcessPoolExecutor(max_workers=6) as pool:
        res = list(pool.map(one_name, NAMES))
    w = pd.DataFrame([r for x in res for r in x["windows"]])
    skew = pd.DataFrame([r for x in res for r in x["skew"]])
    w["gap"] = (w["implied"] - w["realized"]) * 100.0
    full = ROOT / "data/derived/realized"
    full.mkdir(parents=True, exist_ok=True)
    w.to_csv(full / "windows.csv", index=False)           # holds vol levels: not published
    ok = w[w["gap"].notna()]

    say("Derived statistics only: gaps in vol points, P&L as a percentage of the premium,")
    say("and counts. No implied vol level, premium or price appears in this file.")
    say()
    say("Implied vol against the vol that followed. For each monthly expiry that has")
    say("expired inside the data: at-the-money implied vol on the first day of the window,")
    say("minus the vol the share realized from that day to expiry. Windows do not overlap.")
    say()
    say("1. HOW MANY WINDOWS THERE ARE")
    say(f"   {len(NAMES)} names, {ok['expiry'].nunique()} expiries, {len(ok)} name-windows with "
        f"both numbers; {len(w) - len(ok)} where the share price sat outside the strikes "
        "with a vol on the first day, so no at-the-money vol was read.")
    say(f"   A name has at most {int(ok.groupby('name').size().max())} windows. Windows on "
        "different names in the same month are NOT independent: one market move reaches")
    say(f"   all of them. The honest count of independent observations is nearer "
        f"{ok['expiry'].nunique()} than {len(ok)}.")
    say()
    say("2. THE GAP, implied minus realized, vol points")
    by_name = ok.groupby("name")["gap"].agg(windows="size", mean="mean", lowest="min",
                                            highest="max")
    by_name["implied was higher"] = ok.groupby("name")["gap"].apply(lambda g: int((g > 0).sum()))
    say(by_name.reindex([n for n in NAMES if n in by_name.index]).round(1).to_string()
        .replace("\n", "\n   ").join(["   ", ""]))
    say()
    say("   by expiry, across names (each row is one month, the unit that is independent):")
    by_exp = ok.groupby("expiry")["gap"].agg(names="size", mean="mean", median="median",
                                             lowest="min", highest="max")
    by_exp["implied was higher"] = ok.groupby("expiry")["gap"].apply(lambda g: int((g > 0).sum()))
    say(by_exp.round(1).to_string().replace("\n", "\n   ").join(["   ", ""]))
    m = by_exp["mean"]
    say(f"   Over all name-windows the gap averages {ok['gap'].mean():+.1f} points (median "
        f"{ok['gap'].median():+.1f}); implied was above what followed in "
        f"{int((ok['gap'] > 0).sum())} of {len(ok)}.")
    say(f"   Month by month the average runs from {m.min():+.1f} to {m.max():+.1f}, and "
        f"{int((m > 0).sum())} of {len(m)} months are positive. {len(m)} months cannot")
    say("   establish that options here are dear or cheap. This describes what happened.")
    say()
    say("3. ONE AT-THE-MONEY STRADDLE, HELD TO EXPIRY, DELTA HEDGED EACH CLOSE")
    s = w[w["long_at_ask"].notna()] if "long_at_ask" in w else w.iloc[0:0]
    say("   P&L as a percentage of the premium. The buyer pays the ask; the seller gets the")
    say("   bid. Both lose the spread, so the two columns do not mirror each other.")
    st = s.groupby("expiry").agg(names=("name", "size"), buyer=("long_at_ask", "mean"),
                                 seller=("short_at_bid", "mean"),
                                 buyers_ahead=("long_at_ask", lambda x: int((x > 0).sum())),
                                 sellers_ahead=("short_at_bid", lambda x: int((x > 0).sum())),
                                 spread=("spread", "mean"))
    say(st.round(1).to_string().replace("\n", "\n   ").join(["   ", ""]))
    say(f"   All {len(s)} straddles: buyer {s['long_at_ask'].mean():+.1f}% of premium on "
        f"average, seller {s['short_at_bid'].mean():+.1f}%; quoted spread "
        f"{s['spread'].mean():.1f}% of the premium; the strike was "
        f"{s['off_the_money'].mean():.1f}% from the share price on average.")
    say(f"   Leg-days where the mark gave no vol and the delta was taken as all or nothing: "
        f"{int(s['hedge_days_missing'].sum())} of {2 * int(s['sessions'].sum())}.")
    say("   A delta hedge at each close with the option's own implied vol is one choice of")
    say("   several, and the result depends on it. No financing of the premium is charged.")
    say()
    say("4. THE SKEW: 25-delta risk reversal and butterfly, vol points, monthly expiry")
    say("   nearest 45 days. Risk reversal = call wing minus put wing (negative: downside")
    say("   costs more). Butterfly = average wing minus the at-the-money vol.")
    sk = skew.groupby("name").agg(days=("rr", "size"), risk_reversal=("rr", "median"),
                                  rr_low=("rr", lambda x: x.quantile(0.1)),
                                  rr_high=("rr", lambda x: x.quantile(0.9)),
                                  butterfly=("bf", "median"))
    say(sk.reindex([n for n in NAMES if n in sk.index]).round(2).to_string()
        .replace("\n", "\n   ").join(["   ", ""]))
    say("   Deltas here are Black-Scholes deltas on the tree's vols, used only to find the")
    say("   25-delta strikes. A day is left out when the strikes on file do not reach them.")

    out = ROOT / "reports" / "realized_implied.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.4))
    exps = sorted(ok["expiry"].unique())
    a1.boxplot([ok.loc[ok["expiry"] == e, "gap"] for e in exps],
               tick_labels=[f"{e:%b %Y}" for e in exps], showfliers=True)
    a1.axhline(0, color="black", lw=0.6)
    a1.set_ylabel("implied minus realized, vol points")
    a1.set_title(f"the gap by expiry, across {ok['name'].nunique()} names\n"
                 f"({len(exps)} non-overlapping months: a description, not a test)", fontsize=10)
    if len(s):
        a2.scatter(s["gap"], s["long_at_ask"], s=18, color="#1f77b4")
        a2.axhline(0, color="black", lw=0.6)
        a2.axvline(0, color="black", lw=0.6)
        a2.set_xlabel("implied minus realized at entry, vol points")
        a2.set_ylabel("straddle buyer's P&L, % of premium")
        a2.set_title("a straddle bought at the ask and delta hedged:\nit paid where realized "
                     "beat implied by more than the spread", fontsize=10)
    fig.tight_layout()
    fig.savefig(ROOT / "reports" / "realized_implied.png", dpi=130)
    print(f"\nsaved: {out.relative_to(ROOT)}, reports/realized_implied.png")


if __name__ == "__main__":
    main()
