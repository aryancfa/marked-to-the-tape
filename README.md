# Marked to the Tape

A risk system for a small book of Canadian listed options. It prices positions with its own engine for American options with discrete dividends. It compares the implied volatilities with Montreal Exchange settlements and with an independent source. Then it replays real historical days and shows what risk is left after hedging with SXF, ZEB and XIU.

**Status:** in progress. Done and tested: the pricing, inversion and Greeks core (`src/voleng.py`), the trading calendar (`src/tradingcal.py`), the Montreal Exchange loader (`src/loaders/mx.py`) and chain inversion (`src/surface.py`). The call-put check has run on all 124 RY days (`scripts/run_days.py`); it shows a small, persistent gap that is not yet explained, described below.

## Data

This repository contains no exchange or vendor data. Montreal Exchange, Interactive Brokers and OptionMetrics data are licensed for personal use and can't be redistributed. Raw files stay on the author's machine under `data/`, which is excluded from version control. Everything published here is computed from them: errors in vol points, residuals and summary statistics.

### Montreal Exchange daily summary files (CAP)

Downloaded by hand from the exchange's website. Earliest date held: 8 May 2026 for RY
(124 trading days to 4 November 2026) and 10 September 2026 for XIU (41 trading days to
6 November 2026; four earlier months are still to download, in pieces of about two
months because the site limits the file size). The trading calendar accounts for every
date in both files, with none missing and none on a holiday.

Columns, identical in both files:

`Date, Symbol, Class Symbol, Root Symbol, Underlying Symbol, Strike Price, Expiry Date,
Call/Put, Ins. Type, Bid Price, Ask Price, Bid Size, Ask Size, Last Price, Volume,
Last Close Price, Net Change, Open Price, High Price, Low Price, Total Value,
Nb. Trades, Settlement Price, Open Interest, Implied Volatility,
Accrued Financing Value, Accrued Financing Date, CORRA Rate`

Notes from the header and counts only:

- `Call/Put` is 0 for a call and 1 for a put, confirmed two ways: the series symbol
  carries the letter, and call prices fall as the strike rises in every chain.
- `Ins. Type` is 0 for an option and 3 for the underlying, which has one row per day
  with a bid, an ask and a last trade.
- `Implied Volatility` is the exchange's own figure, in percent.
- `CORRA Rate` and `Accrued Financing Value` are zero on every row, so the interest
  rate has to come from another source.
- About 15% of RY option rows and 4% of XIU option rows have volume above zero. About
  93% carry a two-sided quote. The loader tags each row traded, quoted or
  settlement-only and inverts the quote midpoint where there is one.
- Near the money (within 10% of spot, 7 to 180 days), 99% of RY rows and 96% of XIU
  rows have a midpoint.
- The settlement falls outside the closing quote on about 6% of quoted RY rows and 1%
  of XIU rows, so the two are not taken at the same moment.
- The underlying's own quote in the file is about 20 basis points wide, so it does not
  pin the share price at the time the options were quoted.

## Run the tests

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -v
```

On macOS or Linux, use `.venv/bin/python` instead.

## First result: calls against puts on RY, 124 days

![Call minus put implied vol by expiry, pooled across days](reports/ry_cp_gap_pooled.png)

A call and a put at the same strike and expiry should imply the same volatility. If
they don't, a carry input (share price, rate or dividend) is wrong, because that kind
of error moves every strike of an expiry the same way.

The chart shows the mean gap for each expiry across 8 May to 4 November 2026. Each
point has two bars, and they answer different questions:

- **Grey: the quote bar.** Half the bid-ask spread of the call plus that of the put,
  in vol points. A gap inside it cannot be traded on.
- **Blue or brown: two standard errors of the mean.** How well the average is known
  from its day-to-day scatter. A mean outside it is not noise.

What it shows: every gap is inside the quote bar, so nothing here is a trade. But
monthly expiries more than 60 days out sit about 0.25 vol points above zero on almost
every day, and weekly expiries under 30 days sit about 0.4 below. Both are many
standard errors from zero. The model has a small systematic error that is not yet
explained. The candidates are the rate (T-bill yields sit below dealers' funding
rate), the ex-dividend date convention, and lopsided midpoints on wide weekly quotes.

Limits: the standard error treats days as independent, and neighbouring days are not.
The share price is implied from the nearest monthly expiry, so that expiry is left out
on the days it is used. Dividends after the last declared one are projected.
