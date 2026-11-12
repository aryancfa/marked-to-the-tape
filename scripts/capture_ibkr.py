"""
capture_ibkr.py — after-close snapshot of each option chain through the
Interactive Brokers API (build sheet SRC; docs/work-list.md section 4).

    .venv/Scripts/python scripts/capture_ibkr.py RY XIU TD
    .venv/Scripts/python scripts/capture_ibkr.py --dry-run RY      # ask for nothing, show the plan

The exchange's website may not be scripted (hard rule 2). This is the
automated route: it reads quotes and nothing else. The connection is opened
READ-ONLY, so this script cannot place, change or cancel an order.

NOT YET RUN AGAINST A LIVE GATEWAY. It was written from the library's
documented interface and cannot be tested without an account. Run it with
--dry-run first, then on one name, and read the capture log before trusting it.

What you need first (these are yours to do; the script cannot)
--------------------------------------------------------------
  1. An Interactive Brokers account with a market data subscription that
     covers Montreal Exchange options and Toronto shares.
  2. IB Gateway (or Trader Workstation) running and signed in, with
     Configure > Settings > API > Settings: "Enable ActiveX and Socket
     Clients" ticked and "Read-Only API" ticked.
  3. The library:  .venv/Scripts/python -m pip install ib_async
  Ports: IB Gateway 4001 (live) or 4002 (paper); Trader Workstation 7496 or 7497.

What it saves
-------------
  data/raw/ibkr/<YYYYMMDD>/quote_<NAME>.csv   one row per option asked for
                                              (loaders/ibkr.py reads it)
  data/raw/ibkr/capture_log.csv               one line per name per run

data/ is gitignored. Interactive Brokers market data is for the account
holder's own use, so nothing here is ever committed or published.

Which options: expiries up to 400 days ahead and strikes within 12% of the
share price, the same window the front chart keeps (loaders.ibkr.choose_contracts).

Limits, also listed in loaders/ibkr.py: a snapshot is not an official close,
carries no settlement, no exchange implied vol and no open interest, and
Interactive Brokers keeps nothing for expired options.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from loaders.ibkr import CAPTURE_COLUMNS, LOG_COLUMNS, capture_summary, choose_contracts
from tradingcal import TSX, trading_days

OUT = ROOT / "data/raw/ibkr"
BATCH = 40              # option quotes asked for at once; the API rations requests


def _value(x) -> float:
    """The API reports a missing number as nan or -1."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v if v == v and v > 0 else float("nan")


def capture_name(ib, name: str, day: date, option_exchange: str, dry_run: bool):
    """Returns (rows, asked, note) for one underlying."""
    from ib_async import Option, Stock

    share = Stock(name, "SMART", "CAD", primaryExchange="TSE")
    if not ib.qualifyContracts(share):
        return [], 0, "share contract not found"
    [st] = ib.reqTickers(share)
    und = (_value(st.bid), _value(st.ask), _value(st.last))
    mid = 0.5 * (und[0] + und[1]) if und[0] == und[0] and und[1] == und[1] else und[2]
    if not mid == mid:
        return [], 0, "no share quote (market data subscription?)"

    chains = ib.reqSecDefOptParams(share.symbol, "", share.secType, share.conId)
    chain = next((c for c in chains if c.exchange == option_exchange), None)
    if chain is None:
        return [], 0, (f"no option chain on {option_exchange}; exchanges offered: "
                       f"{sorted({c.exchange for c in chains})}")
    expiries = [datetime.strptime(e, "%Y%m%d").date() for e in chain.expirations]
    want = choose_contracts(mid, expiries, chain.strikes, day)
    if dry_run:
        return [], len(want), f"dry run: would ask for {len(want)} options"

    options = [Option(name, e.strftime("%Y%m%d"), k, cp, option_exchange, currency="CAD",
                      tradingClass=chain.tradingClass) for e, k, cp in want]
    stamp = datetime.now().isoformat(timespec="seconds")
    rows = []
    for i in range(0, len(options), BATCH):
        good = [c for c in ib.qualifyContracts(*options[i:i + BATCH]) if c.conId]
        for t in ib.reqTickers(*good):
            c = t.contract
            rows.append(dict(
                captured_at=stamp, date=day.isoformat(), underlying=name,
                expiry=datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d").date().isoformat(),
                strike=float(c.strike), call_put=c.right,
                bid=_value(t.bid), ask=_value(t.ask), last=_value(t.last),
                volume=_value(t.volume),
                und_bid=und[0], und_ask=und[1], und_last=und[2]))
    return rows, len(want), ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="+")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4001)
    ap.add_argument("--client-id", type=int, default=17)
    ap.add_argument("--option-exchange", default="CDE",
                    help="Interactive Brokers' code for the Montreal Exchange (unverified; "
                         "if wrong, the log line lists the exchanges offered)")
    ap.add_argument("--date", default=None, help="trading day to file under (default today)")
    ap.add_argument("--dry-run", action="store_true", help="look up chains, ask for no quotes")
    a = ap.parse_args()

    day = date.fromisoformat(a.date) if a.date else date.today()
    if day not in trading_days(TSX, day, day):
        raise SystemExit(f"{day} is not a Toronto trading day; nothing to capture")
    try:
        from ib_async import IB
    except ImportError:
        raise SystemExit("ib_async is not installed: "
                         ".venv/Scripts/python -m pip install ib_async")

    ib = IB()
    try:
        ib.connect(a.host, a.port, clientId=a.client_id, readonly=True, timeout=15)
    except Exception as exc:
        raise SystemExit(f"could not connect to {a.host}:{a.port} ({exc}). Nothing is "
                         "listening on that port. Is Trader Workstation or IB Gateway "
                         "open and signed in, with the API switched on, and is the port "
                         "the one shown under its API settings?")
    ib.reqMarketDataType(2)         # live when the market is open, last quotes ("frozen") after

    folder = OUT / day.strftime("%Y%m%d")
    log = []
    try:
        for name in [n.upper() for n in a.names]:
            t0 = time.time()
            try:
                rows, asked, note = capture_name(ib, name, day, a.option_exchange, a.dry_run)
            except Exception as exc:                # one name failing must not lose the rest
                rows, asked, note = [], 0, f"failed: {exc}"
            frame = pd.DataFrame(rows, columns=CAPTURE_COLUMNS)
            if len(frame):
                folder.mkdir(parents=True, exist_ok=True)
                frame.to_csv(folder / f"quote_{name}.csv", index=False)
            line = capture_summary(day, name, asked, frame, time.time() - t0, note)
            log.append(line)
            print(f"{name}: asked {line['asked']}, returned {line['returned']}, two-sided "
                  f"{line['two_sided']}, missed {line['missed']}, {line['seconds']} s"
                  + (f"  [{note}]" if note else ""))
    finally:
        ib.disconnect()

    if not a.dry_run:
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / "capture_log.csv"
        pd.DataFrame(log, columns=LOG_COLUMNS).to_csv(
            path, mode="a", index=False, header=not path.exists())
        print(f"log: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
