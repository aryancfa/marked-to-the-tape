# Work list

> **Historical note, 13 November 2026.** This file is the proposal written on
> 11 November 2026 and is kept as a record of the reasoning. It is NOT the status of
> the project. Since it was written the closing chart, the book, the review queue,
> stress, tail risk and the live page have all been built. The one place that says
> what is built and what is not is the "State" line under each item in
> `docs/build-sheet.md`. Where this file and the build sheet disagree, the build
> sheet is right.

What stands between this project and a risk system a desk would use, in the order to
do it. Written 11 November 2026 against an outside review. Item IDs refer to
`docs/build-sheet.md`.

## Where the review's five points stand

| Point | Status |
|---|---|
| One name, six months | True for the front chart. Section 1 and 2 below. |
| The closing chart is unbuilt | True. Section 3. |
| No independent check | **Closed for the engine.** `scripts/engine_check.py` prices OptionMetrics' own quotes for the New York listing and compares vols. Where no dividend falls in the option's life the two agree to 0.006 vol points on average. |
| No live path | True. Section 4. |
| Hedge ratios need more names and history | True. The history turns out to be available. Section 2 and 5. |

## 1. Universe

The build sheet names four banks, two energy producers, three ETFs and the index. That
is two sectors. A book only shows sector basis, single-name vol and dividend risk when
its names differ in the ways that matter, so the proposal is twelve single names chosen
so that each one tests something the others do not.

| Name | Sector | What it adds |
|---|---|---|
| RY, TD, BNS, BMO | Banks | The core. Steady dividends, tight quotes. ZEB is their sector hedge. |
| MFC | Insurer | A financial that is not a bank: tests whether ZEB hedges it. |
| CNQ, SU | Energy producers | Oil beta. XEG is their sector hedge. |
| ENB | Pipeline | High yield, low vol: the dividend is a large share of the option's value. |
| CNR | Rail | An industrial with no sector ETF in the hedge set, so its residual is all name-specific. |
| BCE | Telecom | Very high yield, and it cut its dividend in 2025: a real test of a dividend projection being wrong. |
| ABX | Gold | Moves against the index on stress days. Declares in US dollars. |
| SHOP | Technology | No dividend, high vol, wide strike range: the no-carry case, and the hardest chain to mark. |

Plus XIU, ZEB, XEG (ETFs), SXO (index options) and SXF (index future), as now.

All twelve also list in New York, so the engine check runs on every one, and all
twelve have daily prices on WRDS.

- [ ] **You:** confirm these are among the exchange's most active option classes. Its
  monthly statistics give volume and open interest by class. Swap out any that are
  thin; a name nobody quotes cannot be marked. (This list is from memory of which
  classes are active and has not been checked against those statistics.)
- [ ] **Me:** once confirmed, update the universe line in the project notes and the
  build sheet, and add the names to the trading calendar's table.

## 2. History: what each use needs

Different parts need different amounts, and only one of them needs options history.

| Use | Needs | Why that much | Source | On disk? |
|---|---|---|---|---|
| Front chart, per name | 3 to 6 months of option files | The carry checks reach a standard error of 0.02 vol points in six months. More days would not change a conclusion. | Exchange site, by hand | All names, 8 May to early November 2026 (SU from 8 June) |
| Engine check, per name | 3 months of New York option rows | 1,400 comparisons per name is ample for a mean and a tail | WRDS OptionMetrics | RY done, six running |
| Hedge ratios | Daily closes, 1 year window | A ratio fitted on n days is known to about 1/sqrt(n) of itself when the hedge explains half the variance: 13% on 60 days, 6% on 250, 4.5% on 500 | WRDS Compustat | Not pulled yet |
| Judging a hedge out of sample | A further 2 years or more | A variance ratio measured on m days is known to about sqrt(2/m): 13% on 124 days, 6% on 500 | Same | Not pulled yet |
| Tail of the residual | 5 years or more | A 97.5% expected shortfall on 250 days rests on 6 days. On 1,250 days it rests on 31. | Same | Not pulled yet |
| Named stress days | Back to January 2014 | Covers the January 2015 rate surprise, the 2015 oil fall, March 2020, and the 2022 to 2023 rate rises | Same | Not pulled yet |
| Vega P&L in the replay | Implied vol history by name | Option P&L needs the vol move as well as the share move | WRDS OptionMetrics surfaces, New York listing, to August 2025 | Not pulled yet |

So the answer to "how much history" is: **daily prices from January 2014 for every
name, ETF and the index** (about 3,200 trading days), and no more option history from
the exchange than is already planned.

What was checked on WRDS on 11 November 2026: the Toronto listings of all twelve
names, XIU, ZEB, XEG and the TSX 60 index are in Compustat's daily file, most from
1983 and all current to 10 November 2026. A few series carry rows dated in the future,
which need filtering.

Two gaps that stay open:

- **Implied vol between September 2025 and May 2026.** OptionMetrics on this
  subscription ends in August 2025 and the exchange files start in May 2026. The
  replay has no observed vols for those eight months.
- **SXF itself.** The futures price history is not on WRDS. The TSX 60 index is, and
  a future's daily return is the index's return plus a small carry term, so the index
  stands in for it and the chart says so.

- [ ] **Me:** `scripts/wrds_prices.py` to pull daily closes, dividends and split
  factors for the universe from January 2014, with a loader and tests.
- [ ] **Me:** pull OptionMetrics' standardised vol surfaces for the New York listings,
  2014 to August 2025.
- [x] **You:** download three to six months of option files for each added name, and
  the rest of XIU. Done 11 November 2026, with SXF and SXO.

## 3. The closing chart

What a desk looks at: the book's daily P&L unhedged, after an index and sector hedge,
and after a vega hedge. Built in this order, each step runnable on its own.

1. [ ] **A3. Position format.** Listed option, future, ETF, share. Two sample books:
   one bank-heavy, one spread across the twelve names. *No data needed.*
2. [ ] **Share-level P&L and hedge ratios (part of C4).** Treat the book as its share
   deltas only. Daily P&L from prices; hedge with the index, then the index plus the
   sector ETF. Ratios from a rolling window that ends the day before. Report, out of
   sample: variance removed by each hedge, in percent and dollars; the same thing in
   sample, so the gap is visible; a minimum-variance ratio against a beta ratio.
   *Needs the WRDS price pull. This already answers the first of the chart's three
   questions for the delta part, on twelve years of data.*
3. [ ] **B6, C1, C2. Revalue the options.** Full revaluation of each option between
   two days, with a stated rule for how vol moves when the share moves. Greeks
   bucketed by maturity and moneyness.
4. [ ] **C3. Replay.** The named stress periods, every day labelled by whether its
   vols were observed in Montreal, observed in New York, or produced by the model.
5. [ ] **C4. Split the residual.** The delta the future missed, against the gamma and
   vega it cannot touch.
6. [ ] **C4b. Vega hedge.** Near-the-money XIU options in the bucket where the book's
   vega sits, by a fixed rule, then re-hedge the delta they bring.
7. [ ] **The three questions, in words.** How much variance each hedge removes. What
   the remainder is made of. Whether the remainder is noise: its autocorrelation, and
   its correlation with instruments not yet used.

Steps 1 and 2 can start now. Step 3 onward needs the implied vol history.

## 4. A live path

So the front chart can be refreshed after the close without anyone downloading files.

1. [ ] **You:** an Interactive Brokers account with market data for the Montreal
   Exchange, and Trader Workstation or the Gateway running with the API switched on.
   I cannot sign in or subscribe for you.
2. [x] **Me:** `scripts/capture_ibkr.py`. After the close, for each name: the chain's
   contracts, closing bid, ask and last for strikes near the money, and the share's
   close. Saved under `data/raw/ibkr/` by date. Written 12 November 2026 and NOT YET
   RUN against a live Gateway; the exchange code for Montreal ("CDE") is unverified.
   Read-only connection. Start with `--dry-run`.
3. [x] **Me:** `src/loaders/ibkr.py` returns the same tables as the exchange-file
   loader, so nothing downstream changes. 11 tests on made-up rows, one of which
   runs captured rows through the same inversion.
4. [x] **Me:** the capture log (`capture_summary`) and `missed_days`, which lists
   trading days with no capture for a name.
5. [ ] **Me:** `scripts/after_close.py`: capture, then the reproduction command, in
   one step.
6. [ ] **You:** a scheduled task on your machine to run it on trading days.
7. [ ] **Limits to state.** Interactive Brokers keeps no history for expired options,
   so this only builds history forward. Its closing quote is a snapshot, not the
   exchange's settlement, and the exchange's own implied vol is not in it, so the
   third reference on the front chart would be missing on live days.

This is the build sheet's SRC item. It comes after the closing chart exists, because a
chart refreshed daily is worth more than a chart that is merely current.

## 5. More names and longer history for hedge and residual work

1. [ ] Section 2's price pull, for all twelve names (me).
2. [ ] Dividend schedules for each name from OptionMetrics' distribution table, which
   has ex-dates, amounts and currencies, cross-checked against company pages for the
   most recent two (A4, me).
3. [ ] Hedge ratios and out-of-sample variance removed, name by name and by sector,
   over 2016 to 2026, with 2014 to 2015 as the first window (C4, me).
4. [ ] Which names the sector ETF fails to hedge, sized (expected: MFC against ZEB,
   CNR and SHOP against anything).
5. [ ] Borrow. Hard-to-borrow effects cannot be seen in this data: none of these
   names is hard to borrow, and the implied-borrow feature was cut for that reason.
   State it as a limit, not a finding.
6. [ ] Front chart and engine check for each added name as its option files arrive.

## Funding for index products

RY's fitted funding spread is not applied to XIU. The standard source for an index's
funding is the index itself: the gap between the SXF future and the index, or put-call
parity on the European SXO options, which gives the forward with no model at all.

- [x] **You:** download SXF and SXO files from the exchange. Done. The SXO file
  carries settlements only (no quotes, volume or open interest), so the SXF basis
  is the source to trust and SXO parity goes beside it as the exchange's view.
- [x] **Me:** imply the index's funding from SXO parity and from the SXF basis
  (`scripts/index_funding.py`, `reports/index_funding_numbers.txt`). The basis gives
  the spread minus the dividend yield: -173 bp a year (standard error 6). With
  XIU's trailing yield as a stand-in the spread is about +50, and +24, +72 and +36
  by contract, so it is a range of roughly 20 to 80, not a number. SXO parity turned
  out to carry no dividends at all, so it is not a funding source.
- [ ] **You or me:** the index's dividends over each contract's life. The S&P/TSX 60
  total-return index on WRDS would give them exactly for past contracts (total
  return less price return). It needs one more pull with your WRDS login.

## What you need to do

1. Confirm the twelve names against the exchange's volume statistics.
2. ~~Download option files for the added names and the rest of XIU.~~ Done.
3. Decide whether the first milestone's "live" is a static page.
4. When ready for section 4: the Interactive Brokers account and data subscription.
