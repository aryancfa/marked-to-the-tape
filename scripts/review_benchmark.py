"""
review_benchmark.py — when the review queue names a cause, how often is it right?

    .venv/Scripts/python scripts/review_benchmark.py            # about ten minutes
    .venv/Scripts/python scripts/review_benchmark.py --days 8   # quick look

MADE-UP DATA ONLY (scripts/demo_synthetic.py). Five faults are planted, one
per invented market, so the true cause of every flag is known:

  bad quote        about one quote in fifty has its vol pushed 1, 2 or 4
                   points. Truth for that quote: "this mark". Truth for the
                   clean quote on the other side of the same strike, if the
                   pair check flags it: "partner's quote".
  stale quote      about one quote in fifty keeps YESTERDAY'S price while
                   the share has moved. Same two truths.
  dividend date    the model places the dividend three sessions after an
                   expiry it actually falls before (Manulife, November
                   2026). Truth for that expiry's quotes: an input.
  dividend amount  the model's dividend is 25% too large. Truth for every
                   expiry past the ex-date: an input.
  share price      the model's share price is 25 basis points high (a stale
                   close). Truth for every quote: an input.

Every quote is treated as if it were held, so the counts are large enough
to mean something. For each fault the table gives how many planted quotes
were flagged at all, and what the queue called the ones it flagged.

Thresholds are not judged on the data they were chosen on
---------------------------------------------------------
The rules in src/review.py have thresholds. Each market is generated twice
from different random seeds. On the FIRST set a small grid of thresholds is
searched for the best share of right causes; on the SECOND set that choice
and the shipped defaults are both scored. The second set's numbers are the
ones to quote. If the tuned choice does no better there than the default,
the default stays, and the table says so.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import pandas as pd

import demo_synthetic as demo
import review
from loaders.mx import load_mx_options, load_mx_underlying
from rates import flat_curve
from references import PAIR, SMILE, reference_differences
from replay import fails
from surface import analyse_day
from tradingcal import TSX, trading_days

SIZES = (1.0, 2.0, 4.0)                 # vol points
RATE_PLANTED = 0.02
HALF_SPREAD, MID_NOISE = 0.12, 0.02     # dollars, as in mark_benchmark.py
AMOUNT_ERROR = 1.25                     # the model's dividend over the true one
SPOT_ERROR = 25e-4                      # the model's share price over the true one
FAULTS = ("bad quote", "stale quote", "dividend date", "dividend amount", "share price")
MARK, PARTNER, INPUT, CLEAN = "this mark", "partner's quote", "an input", "clean"
GRID = dict(min_others=(1, 2, 3), shared=(0.3, 0.5, 0.7), together=(0.3, 0.5, 0.7))


def chosen(seed: int, i: int, j: int, K: float, cp: str) -> float:
    """Reproducibly: 0.0, or the signed size of the fault on this quote."""
    rng = np.random.default_rng([seed, i, j, int(K * 100), cp == "C"])
    if rng.random() >= RATE_PLANTED:
        return 0.0
    return float(rng.choice(SIZES) * rng.choice((-1.0, 1.0)))


def make_stale(path: Path, seed: int) -> None:
    """Rewrite the made-up file so the chosen quotes carry the day before's
    bid, ask and settlement: a price nobody refreshed."""
    df = pd.read_csv(path)
    opt = df["Ins. Type"] == 0
    days = {d: i for i, d in enumerate(sorted(df["Date"].unique()))}
    exps = {e: j for j, e in enumerate(sorted(df.loc[opt, "Expiry Date"].unique()))}
    cols = ["Bid Price", "Ask Price", "Settlement Price"]
    prev = {(s, days[d]): tuple(v) for s, d, *v in
            df.loc[opt, ["Symbol", "Date", *cols]].itertuples(index=False)}
    for idx in df.index[opt]:
        r = df.loc[idx]
        i, cp = days[r["Date"]], "C" if r["Call/Put"] == 0 else "P"
        if i and chosen(seed, i, exps[r["Expiry Date"]], r["Strike Price"], cp):
            df.loc[idx, cols] = prev[(r["Symbol"], i - 1)]
    df.to_csv(path, index=False)


def one_day(task) -> pd.DataFrame | None:
    """Runs in a worker: every quote of one day with its checks and context."""
    opts, mid, model_divs, first_projected, uncertain, spot = task
    day = opts["date"].iloc[0].date()
    res = analyse_day(opts, mid, flat_curve(day, demo.RATE + demo.FUNDING), model_divs,
                      first_projected=first_projected, spot=spot,
                      uncertain_ex_dates=uncertain)
    if res.inverted is None:
        return None
    inv = res.inverted[res.inverted["iv"].notna()]
    d = reference_differences(inv, res.spot, divs=model_divs)
    labels = res.summary["carry"].to_dict()
    exp = pd.Series([e.date() if hasattr(e, "date") else e for e in d["expiry"]], index=d.index)
    rows = []
    for e, K, cp, bar, vega in zip(inv["expiry"], inv["strike"], inv["call_put"],
                                   inv["half_spread_vol"] * 100.0, inv["vega"]):
        e = e.date() if hasattr(e, "date") else e
        here = d[(exp == e) & ((d["strike"] - K).abs() < 1e-6)]
        p = here[here["reference"] == PAIR]
        s = here[(here["reference"] == SMILE) & (here["side"] == cp) & ~here["poor_fit"]]
        ctx = review.expiry_context(d, e, K, cp)
        rows.append(dict(
            day=day, expiry=e, strike=K, call_put=cp, bar=bar, vega=vega,
            pair=float(p["diff"].iloc[0]) if len(p) else np.nan,
            pair_bar=float(p["quote_bar"].iloc[0]) if len(p) else np.nan,
            smile=float(s["diff"].iloc[0]) if len(s) else np.nan,
            others=ctx.others, others_median=ctx.others_median,
            partner_smile=ctx.partner_smile, carry=labels.get(e),
            front=res.front is not None and e == res.front))
    return pd.DataFrame(rows)


def market(fault: str, days: int, seed: int, workers: int) -> pd.DataFrame:
    """One invented market with one kind of fault, every quote labelled with
    the true cause of any flag on it."""
    quote_fault = fault in ("bad quote", "stale quote")

    def plant(i, j, K, cp):
        return chosen(seed, i, j, K, cp) / 100.0 if fault == "bad quote" else 0.0

    with tempfile.TemporaryDirectory() as tmp:
        path, truth, divs = demo.make_file(Path(tmp), date(2026, 3, 2), days, seed=seed,
                                           plant=plant, half_spread=HALF_SPREAD,
                                           mid_noise=MID_NOISE)
        if fault == "stale quote":
            make_stale(path, seed)
        opts, und = load_mx_options(path), load_mx_underlying(path).set_index("date")
    stamps = sorted(set(opts["date"]))
    expiries = sorted(set(pd.to_datetime(opts["expiry"]).dt.date))
    (ex_date, cash), = divs
    model, first_proj, uncertain, hit = list(divs), None, [], set()
    if fault == "dividend date":
        after = min(e for e in expiries if e >= ex_date)      # the expiry that holds it
        late = [d for d in trading_days(TSX, after, after + timedelta(days=10)) if d > after][2]
        model, first_proj, uncertain, hit = [(late, cash)], late, [late], {after}
    elif fault == "dividend amount":
        model, first_proj = [(ex_date, cash * AMOUNT_ERROR)], ex_date
        hit = {e for e in expiries if e >= ex_date}
    elif fault == "share price":
        hit = set(expiries)
    tasks = [(opts[opts["date"] == ts], float(und.loc[ts, "mid"]), model, first_proj, uncertain,
              truth[ts.date()] * (1.0 + SPOT_ERROR) if fault == "share price" else None)
             for ts in stamps]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        q = pd.concat([f for f in pool.map(one_day, tasks) if f is not None],
                      ignore_index=True)
    order = {ts.date(): i for i, ts in enumerate(stamps)}
    q["i"] = q["day"].map(order)
    own = np.array([chosen(seed, i, expiries.index(e), K, cp) if quote_fault and
                    (fault == "bad quote" or i) else 0.0
                    for i, e, K, cp in zip(q["i"], q["expiry"], q["strike"], q["call_put"])])
    other = np.array([chosen(seed, i, expiries.index(e), K, "P" if cp == "C" else "C")
                      if quote_fault and (fault == "bad quote" or i) else 0.0
                      for i, e, K, cp in zip(q["i"], q["expiry"], q["strike"], q["call_put"])])
    q["truth"] = np.where(own != 0, MARK, np.where(other != 0, PARTNER, CLEAN))
    q["planted"] = np.abs(own)                           # vol points, on this quote
    if not quote_fault:
        q["truth"] = np.where(q["expiry"].isin(hit), INPUT, CLEAN)
    q["fault"] = fault
    q["failed_smile"] = [fails(s, b) for s, b in zip(q["smile"], q["bar"])]
    q["failed_pair"] = np.array([fails(p, b) for p, b in zip(q["pair"], q["pair_bar"])]) \
        & ~q["front"]
    q["flagged"] = q["failed_smile"] | q["failed_pair"]
    return q


def causes(q: pd.DataFrame, **thresholds) -> pd.Series:
    """The queue's call for every flagged quote, in the benchmark's three words."""
    out = {}
    for r in q[q["flagged"]].itertuples():
        failed = [n for n, f in (("pair", r.failed_pair), ("smile", r.failed_smile)) if f]
        ctx = review.Context(int(r.others),
                             None if pd.isna(r.others_median) else r.others_median,
                             None if pd.isna(r.partner_smile) else r.partner_smile)
        c = review.likely_cause(failed, None if pd.isna(r.pair) else r.pair,
                                None if pd.isna(r.smile) else r.smile, ctx, r.carry,
                                **thresholds)
        out[r.Index] = INPUT if c.startswith("input") else c
    return pd.Series(out, dtype=object)


def ranking(q: pd.DataFrame) -> dict:
    """Does the queue put the faults that matter most at the top? Bad quotes
    only, where the planted size is known. Each quote is held one for one,
    so its true cost is the planted size times its vega, and the queue's
    figure is the failed disagreement times its vega.

      top_n     each day, take as many items from the top of the queue as
                there are planted quotes in it: the share that are planted
      spearman  rank correlation between the queue's dollars and the true
                cost, over the planted quotes it flagged
      captured  the share of all planted cost that sits in flagged quotes
    """
    b = q[q["fault"] == "bad quote"].copy()
    worst = np.where(b["failed_pair"] & (b["pair"].abs() > b["smile"].abs().fillna(0.0)),
                     b["pair"].abs(), b["smile"].abs())
    worst = np.where(b["failed_smile"] | b["failed_pair"], np.nan_to_num(worst), 0.0)
    b["queue_dollars"], b["true_cost"] = worst * b["vega"], b["planted"] * b["vega"]
    f = b[b["flagged"]]
    hits = total = 0
    for _, g in f.groupby("day"):
        n = int((g["truth"] == MARK).sum())
        top = g.sort_values("queue_dollars", ascending=False).head(n)
        hits, total = hits + int((top["truth"] == MARK).sum()), total + n
    m = f[f["truth"] == MARK]
    return dict(top_n=hits / total if total else float("nan"),
                spearman=float(m["queue_dollars"].rank().corr(m["true_cost"].rank())),
                captured=float(m["true_cost"].sum() / b["true_cost"].sum()), items=len(m))


def right_share(q: pd.DataFrame, **thresholds) -> float:
    """Of flagged quotes that carry a planted fault, the share called right,
    worked out for each (fault, true cause) group and then averaged. A plain
    share would be the input faults' score and nothing else: one wrong share
    price flags hundreds of quotes, one bad quote flags one."""
    c = causes(q, **thresholds)
    f = q.loc[c.index]
    f = f[f["truth"] != CLEAN]
    if f.empty:
        return float("nan")
    ok = (c.loc[f.index] == f["truth"]).groupby([f["fault"], f["truth"]]).mean()
    return float(ok.mean())


def table(q: pd.DataFrame, **thresholds) -> pd.DataFrame:
    c = causes(q, **thresholds)
    rows = []
    for (fault, truth), g in q.groupby(["fault", "truth"], sort=False):
        f = g[g["flagged"]]
        called = c.reindex(f.index)
        row = {"fault planted": fault, "true cause": truth, "quotes": len(g),
               "flagged %": 100 * len(f) / len(g)}
        for name in (MARK, PARTNER, INPUT, review.CURVE, review.UNCLEAR):
            row[f"called {name} %"] = 100 * float((called == name).mean()) if len(f) else np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index(["fault planted", "true cause"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=16)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    sets = {}
    for label, seed in (("first", 11), ("second", 23)):
        parts = [market(f, a.days, seed, a.workers) for f in FAULTS]
        cols = list(parts[0].columns)
        sets[label] = pd.DataFrame([r for part in parts for r in part[cols].to_dict("records")],
                                   columns=cols)
        print(f"{label} set generated: {len(sets[label])} quotes", flush=True)
    default = dict(min_others=review.MIN_OTHERS, shared=review.SHARED,
                   together=review.TOGETHER)
    grid = [dict(zip(GRID, v)) for v in product(*GRID.values())]
    tuned = max(grid, key=lambda t: right_share(sets["first"], **t))

    say("Every figure here comes from invented data (scripts/demo_synthetic.py).")
    say(f"Two sets of five markets, {a.days} days each, quotes about as wide as a liquid")
    say("Montreal name's. Thresholds were searched on the first set; everything below")
    say("is the SECOND set, which played no part in choosing them.")
    say()
    say("1. THRESHOLDS: CHOSEN ON ONE SET, SCORED ON THE OTHER")
    say("   share of flagged, faulty quotes whose cause was called right, averaged over")
    say("   the kinds of fault so that no one kind decides it:")
    say(f"   {'':34s} first set   second set")
    for name, t in (("shipped defaults", default), ("best on the first set", tuned)):
        say(f"   {name:22s} {str(tuple(t.values())):11s} "
            f"{100 * right_share(sets['first'], **t):8.1f}%   "
            f"{100 * right_share(sets['second'], **t):8.1f}%")
    say(f"   (thresholds are {', '.join(GRID)}; {len(grid)} combinations searched)")
    gain = right_share(sets["second"], **tuned) - right_share(sets["second"], **default)
    say("   On the second set the tuned choice is "
        + (f"{100 * gain:+.1f} points against the defaults: "
           + ("not enough to change them." if gain < 0.02 else
              "a real gain; the defaults should be reconsidered.")))
    say()
    say("2. WHAT THE QUEUE CALLED EACH PLANTED FAULT (second set, shipped defaults)")
    t = table(sets["second"], **default)
    say(t.to_string(float_format=lambda x: f"{x:.0f}", na_rep="-").replace("\n", "\n   ")
        .join(["   ", ""]))
    say("   'flagged %' is how many of those quotes failed a check at all. The 'called'")
    say("   columns split the flagged ones and add to 100. A clean row is false alarms.")
    say()
    q2 = sets["second"]
    c = causes(q2, **default)
    f = q2.loc[c.index]
    faulty = f[f["truth"] != CLEAN]
    say("3. IN ONE LINE EACH")
    say(f"   cause called right, averaged over kinds of fault: "
        f"{100 * right_share(q2, **default):.0f}% ({len(faulty)} flagged faulty quotes)")
    inp = faulty[faulty["truth"] == INPUT]
    say(f"   an input fault mistaken for a quote to re-mark (called 'this mark'): "
        f"{100 * (c.loc[inp.index] == MARK).mean():.0f}% of {len(inp)}")
    mk = faulty[faulty["truth"] == MARK]
    say(f"   a bad or stale mark mistaken for an input: "
        f"{100 * (c.loc[mk.index] == INPUT).mean():.0f}% of {len(mk)}")
    clean = q2[q2["truth"] == CLEAN]
    say(f"   false alarms: {1000 * clean['flagged'].mean():.0f} per 1,000 clean quotes")
    say()
    rk = ranking(q2)
    say("4. DO THE FAULTS THAT MATTER MOST COME FIRST? (bad quotes, where the size is known)")
    say(f"   of the planted cost in the market, the share sitting in flagged quotes: "
        f"{100 * rk['captured']:.0f}%")
    say(f"   each day, of the top of the queue (as many items as it holds planted quotes), "
        f"the share that are planted: {100 * rk['top_n']:.0f}%")
    say("   (the rest are mostly the clean quote across the same strike: the call-put check")
    say("   flags both sides at the same size, and it is the cause label that tells them apart)")
    say(f"   rank correlation between the queue's dollars and the true cost, over the "
        f"{rk['items']} planted quotes flagged: {rk['spearman']:+.2f}")
    out = ROOT / "reports" / "review_benchmark.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nsaved: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
