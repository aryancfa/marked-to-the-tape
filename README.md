# Marked to the Tape

A risk system for a small book of Canadian listed options. It prices positions with its own engine for American options with discrete dividends. It compares the implied volatilities with Montreal Exchange settlements and with an independent source. Then it replays real historical days and shows what risk is left after hedging with SXF, ZEB and XIU.

**Status:** in progress. Done and tested: the pricing, inversion and Greeks core (`src/voleng.py`), the trading calendar (`src/tradingcal.py`), the Montreal Exchange loader (`src/loaders/mx.py`) and chain inversion (`src/surface.py`). The front chart exists for RY over 92 days (`scripts/front_chart.py`), against three of its four references. Getting there traced a small, persistent call-put gap to funding cost and to the timing of dividends, as described below.

## What it has found so far

On RY options, May to November 2026. The full account, with what was rejected and
what is still not trusted, is in [docs/findings.md](docs/findings.md).

- **The bid-ask spread is the wrong yardstick for model error.** Quotes are wide, but
  midpoints agree with each other far more closely than the spread suggests. Judged
  against day-to-day scatter, differences ten times smaller than the spread are real.
- **Options are funded above the overnight rate.** Call and put prices imply about 18
  basis points a year over a curve built from CORRA futures. A spread fitted on
  earlier days only removes the call-put gap on later days.
- **The time of day a dividend comes off matters.** Placing the share-price drop by
  trading sessions, at the open of the ex-dividend date, removed a 0.6 vol point gap
  on expiries just after an ex-date.
- **Calls, puts and neighbouring strikes then agree** to within about 0.1 vol points,
  and the exchange's own published vols differ from the model's in the direction
  early exercise predicts.

- **The engine matches a second model.** Against OptionMetrics on seven New York
  listings it agrees to 0.006 to 0.011 vol points wherever no dividend is involved.

- **An index and sector hedge removes 76 to 85% of a bank's or oil producer's daily
  variance,** out of sample over twelve years, and what is left behaves like noise.

Not built yet: the option layers of the second chart (gamma and vega after the
hedge). The front chart exists for RY and, on two months, for XIU. The plan for both is in [docs/work-list.md](docs/work-list.md).

## Front chart: model against market, RY

![Our implied vol against three references](reports/ry_front_chart.png)

Inverting a price gives one implied vol. To say it is wrong you need a second number,
so each vol is compared with three references and pooled over 92 days (24 June to
4 November 2026), by days to expiry and by strike relative to the share price. The
top row uses every quote; the bottom row only the tightest third.

- **Call minus put.** The call and the put at one strike should agree. A gap means a
  carry input (share price, rate, dividend) is off.
- **Strike minus smile.** Each strike against a curve fitted through the *other*
  strikes of its expiry. A gap means that quote is out of line with its neighbours.
- **Ours minus the exchange's.** Against the vol the exchange publishes in the same
  file, calls and puts apart.

A cell is in bold only when its mean is more than two standard errors from zero on
at least five days. Everything else is not distinguishable from zero.

What it shows:

1. **Calls and puts agree.** No cell is further than 0.14 vol points from zero. The
   only steady ones are the longest expiries, at +0.05 to +0.14, which is the
   dividend projection running slightly generous.
2. **Quotes sit on their smile.** Beyond 30 days no cell is further than 0.08. This
   needed a cubic curve: a plain quadratic missed by up to a full vol point in a
   mirror-image pattern across the money, the same way every day, because the real
   smile is steeper on the put side. That was the curve being the wrong shape, not
   the market disagreeing with itself.
3. **The exchange's vols differ from ours in the way predicted.** The expected signs
   were written into `scripts/front_chart.py` before the chart was drawn. In-the-money
   puts are up to 2.4 vol points lower on our side, which is what happens when
   early-exercise value is read as extra vol by a European formula. Calls are 0.2 to
   1.0 lower beyond 60 days. No cell has the opposite sign, which would have pointed
   at one of our inputs. These two panels compare two models, not two prices.
4. **Nothing is tradeable.** On the first two panels no cell exceeds 0.18 of a quote
   bar (half the bid-ask spread in vol points, median 0.7 for one option).

Counts behind it: 19,984 rows, 5.5% rejected (mostly quotes wider than the limit).
Of the rows used, 74% rest on a two-sided quote, 25% on a trade and 1% on a
settlement alone. 4% of expiry-days had a smile the curve could not fit and were
left out of that panel.

How far a wrong input moves the call-put gap, at the money, in vol points:

| Days to expiry | Share price 10 bp low | Rate 25 bp low | Dividend 5% high |
|---|---|---|---|
| 30 | 0.92 | 0.19 | 0.32 |
| 90 | 0.52 | 0.32 | 0.18 |
| 180 | 0.37 | 0.45 | 0.13 |
| 365 | 0.26 | 0.66 | 0.09 |

Still missing from this chart: every name other than RY. The fourth reference,
OptionMetrics, is a separate check below, because its data ends before the
Montreal files begin.

## Front chart: XIU, two months

![XIU front chart](reports/xiu_front_chart.png)

The same chart for the index fund, on the 41 days held so far (10 September to
6 November 2026). **No funding spread is applied.** A fund announces each
distribution about a week ahead, so nearly every XIU expiry rests on a projected
payout, and a spread cannot be fitted from XIU's own options the way it is from RY's.
RY's spread is not borrowed: a number fitted from one name's options carries that
name's own dividend and borrow effects.

- **Calls sit above puts, more so with maturity:** +0.3 vol points at two to four
  months, +0.7 beyond 200 days. That is the same shape RY showed before its funding
  spread was added, so XIU is saying the same thing about funding, unprompted.
- **Quotes sit on their smile** to within 0.1 beyond 60 days.
- **The options found a wrong input.** The next distribution was first projected for
  19 November, last year's date. Only the 20 November expiry then showed a gap, of 34
  basis points of forward price, and the implied share price sat 17 basis points from
  the file's. The fund's published 2026 schedule gives 25 November. With that date
  the implied share price is 2 basis points from the file's.

12.9% of rows were rejected, against 5.5% for RY: the fund's chain has more strikes
too far from the money to carry a vol.

## Does any one choice drive the result?

Each choice behind the front chart was changed on its own, on the same days
(`scripts/robustness.py`, numbers in `reports/ry_robustness.txt`). Call minus put in
vol points, funding spread applied.

| One thing changed | Funding spread (bp a year) | Long expiries, declared dividends | Long expiries, projected dividends | Short weeklies |
|---|---|---|---|---|
| Baseline | 18.8 | -0.02 | +0.07 | -0.02 |
| No dividend raise projected | 18.8 | -0.02 | -0.05 | -0.02 |
| Funding from the latest 10 estimates | 18.8 | -0.01 | +0.09 | -0.03 |
| Funding from the latest 20 estimates | 18.8 | -0.01 | +0.08 | -0.02 |
| Share price from 3 strike pairs, not 5 | 17.3 | +0.01 | +0.12 | -0.03 |
| Share price from the file's last trade | 2.6 | +0.11 | +0.37 | -0.21 |
| Tree with 800 steps, not 400 | 18.7 | -0.01 | +0.09 | -0.04 |

Every second trading day (46 with a spread); the tree row uses every fourth and
matches its own baseline to 0.00.

- **Holds under every choice but one:** calls and puts agree on expiries that rest on
  declared dividends, and on short weeklies.
- **Does not matter:** how far back the funding spread looks, or the tree's step count.
- **Matters:** where the share price comes from. With the file's last trade the
  funding spread cannot be measured at all (2.6, give or take 7.5) and gaps open up.
  The last trade is only good to about 5 basis points, and a spread of 18 a year is
  smaller than that over a few months. So the funding result rests on implying the
  share price from the options, which is a dependence worth stating.
- **Not settled:** the dividend projection. Projecting no raise leaves -0.05 and the
  raise rule leaves +0.07. The market prices something in between, about half the
  rule's raise. Neither beats the other.

## Closing chart, first layer: what the index and sector hedges leave

![Variance left after hedging, share by share](reports/hedge_shares.png)

Thirteen names held as shares, hedged with the index and then with the index plus a
sector fund, on daily returns from January 2015 to November 2026
(`scripts/hedge_shares.py`). The hedge ratio used on a day is fitted on the 250 days
before it. The black ticks show the same hedge with ratios fitted on every day, which
nobody could have traded.

| | Variance removed by the index | Adding the sector fund |
|---|---|---|
| Five banks, one by one | 55 to 65% | 76 to 81% (ZEB) |
| The five banks as a basket | 73% | 97% |
| CNQ, SU | 37 to 38% | 82 to 85% (XEG) |
| MFC (insurer) | 53% | 56%: the bank fund does not hedge it |
| ENB (pipeline) | 49% | 51%: the energy fund does not hedge it |
| CNR, BCE, ABX, SHOP | 40%, 30%, 8%, 23% | no sector fund in the hedge set |

What it shows:

- **Honest ratios cost almost nothing here.** Out of sample and hindsight differ by a
  point or two. A year of daily data pins a ratio down well enough.
- **The sector fund matters more than the index** for banks and oil producers, and
  not at all for the insurer and the pipeline. A fund hedges what it holds.
- **What is left looks like noise.** Its day-to-day autocorrelation is within 0.07 of
  zero for every single name, and it is almost uncorrelated with the fund not used.
- **In March 2020** the five-bank basket's daily returns summed to -42% unhedged and
  +6% hedged.

What this is not: it holds shares, so it says nothing about gamma or vega, which is
most of what is left on an options book. The TSX 60 index stands in for the SXF
future (the index fund XIU tracks it with a correlation of 0.994). Options are the
next layers.

## Engine check: against OptionMetrics on the New York listings

![This engine's implied vol minus OptionMetrics'](reports/ry_us_engine_check.png)

OptionMetrics computes an implied vol for every US-listed option with its own model.
The same companies list in New York, so their quotes there were priced with this
engine, from OptionMetrics' own share price and rate curve, and the vols compared.
Seven names, June to August 2025 (`scripts/engine_check.py`).

| | Options | Mean absolute difference |
|---|---|---|
| No dividend before expiry (only the engines can differ) | 7,884 | 0.006 to 0.011 vol points by name; all within 0.25 |
| A dividend before expiry | about 15,000 | 1 to 3 vol points |

The top row of the chart is the first case and the bottom row the second, for RY.
The engines agree to within this solver's own rounding. Where a dividend is involved
the two sides assume different dividends: these companies declare in Canadian
dollars and the options are in US dollars. Market prices settle which is right.
Taking the Canadian amount at face value leaves calls 1.8 to 4.2 vol points above
puts on every name; converting it leaves 0.1 to 1.1.

This is not a same-day comparison of the two markets. OptionMetrics on this
subscription ends in August 2025 and the Montreal files start in May 2026.

## How the carry inputs were found: calls against puts on RY

![Call minus put implied vol by expiry, pooled across days](reports/ry_cp_gap_pooled.png)

A call and a put at the same strike and expiry should imply the same volatility. If
they don't, a carry input (share price, rate or dividend) is wrong, because that kind
of error moves every strike of an expiry the same way.

The chart shows the mean gap for each expiry over 92 days (24 June to 4 November
2026). Three things are drawn for each expiry:

- **Pale band: the quote bar.** Half the bid-ask spread of the call plus that of the
  put, in vol points. A gap inside it cannot be traded on.
- **Hollow grey point: rates from CORRA futures alone.** Its bar is two standard
  errors of the mean, from the day-to-day scatter. A mean outside it is not noise.
- **Filled point: the same with a funding spread added.**

| Expiries | Futures only | Plus funding spread |
|---|---|---|
| Monthly, over 60 days, declared dividends | +0.16 | -0.02 |
| Monthly, over 60 days, projected dividends | +0.41 | +0.09 |
| Weekly, up to 30 days | -0.06 | -0.02 |

What it shows:

1. **Nothing here is a trade.** Every gap is inside the quote bar.
2. **The overnight rate is too low a funding rate.** The base curve is built from
   three-month CORRA futures, the market's forecast of the overnight rate. With it
   alone, long monthly expiries show calls 0.16 vol points above puts, the same at
   every strike. As a forward price the error grows with maturity at about 17 basis
   points a year. Treasury bills give the same picture (20 basis points).
3. **A spread fitted from earlier days removes it.** Each day uses the mean spread
   implied by the options on previous days only, never its own. On expiries that
   rest on declared dividends the gap falls to -0.02, which is zero within error.
4. **Dividend projections are tested, not fitted.** 2027 dividends are projected by
   a rule from RBC's own history (a raise twice a year, the size of last year's).
   The market prices about half that raise: projecting none leaves -0.05 and the
   rule leaves +0.07 (see the robustness table).
5. **The time of day a dividend comes off matters.** Weekly expiries just after an
   ex-dividend date showed puts about 0.6 vol points above calls. The model was
   dropping the share price at the close of the ex-date; it really opens lower that
   morning, and prices move during trading sessions, not over weekends. Placing
   the drop by the share of sessions that are over before the ex-date's open
   removed the gap: all short weekly expiries now sit at -0.02.

Limits. The spread is fitted from the same option market the check then tests, so
on rates this check is not independent; it is labelled "fitted from options"
wherever it is used. The futures curve is independent, and it shows a spread is
needed but cannot say what it should be. Standard errors treat days as independent,
and neighbouring days are not. The share price is implied from the nearest monthly
expiry, so that expiry is left out on the days it is used. This is one name over
six months.

## Reproduce every number

With the data files in place (see Data below):

```
.venv\Scripts\python scripts\reproduce.py RY
```

That prices every day, draws both RY charts under `reports/`, and writes every
reported number to `reports/ry_numbers.txt`. The first run takes about a quarter of
an hour on eight cores; later runs load from a local cache in seconds. The numbers
file holds derived statistics only and is in the repository, so the write-up can be
checked against it without the licensed data.

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
