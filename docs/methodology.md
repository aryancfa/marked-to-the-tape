# Marked to the Tape: methodology

This note says how every number in the project is made. It holds methods and
settings only; what the methods found is in the findings note
(`docs/findings.md`) and in the reports under `reports/`. Each setting named
here is a constant in the code, and the module that holds it is given in
brackets.

## 1. What the system is for

A small book of Canadian listed options is marked from the Montreal Exchange's
daily files, and two questions are asked of it.

1. **Can the marks be believed?** The front chart uses call-put, smile and
   exchange-vol references where available. It shows differences in vol points,
   by expiry and by moneyness. OptionMetrics is a separate engine check on earlier
   New York listings, not a same-day reference for every Montreal option.
2. **What is left after the hedge?** The book's daily P&L is shown unhedged,
   after an index and sector hedge in shares and in the SXF future, and after
   a vega hedge in listed options. That is the closing chart.

Everything runs offline from a local snapshot. The repository and the live
page carry derived statistics only: differences in vol points, counts,
residuals, sums over positions. Raw prices, volumes and vol levels stay under
`data/`, which is never committed.

## 2. Data

| Source | What is taken | Used for |
|---|---|---|
| Montreal Exchange daily summary files, downloaded by hand | bid, ask, last, settlement, volume, open interest, the exchange's implied vol; the underlying's row; CORRA and SXF futures | marks, references, rate curve, index hedge |
| Bank of Canada | CORRA fixings, Treasury bill yields, the USD/CAD rate | rate curve, episode rate moves, currency conversion |
| Company and fund releases | declared dividends with their announcement dates; BlackRock's published distribution schedule | dividend schedules (`reference/`, public facts) |
| WRDS: Compustat | daily prices, dividends and volume for Toronto and New York listings | hedge ratios, crash betas, dividend history, the venue panel |
| WRDS: OptionMetrics IvyDB US | New York option vols and their inputs; the standardised vol surface | engine check; vol moves in historical episodes |
| WRDS: CRSP, I/B/E/S | consolidated US volume; analysts' dividend forecasts | checking what a volume counts; a third view of next year's dividends |

The exchange's website files are not its official end-of-day product, and its
terms forbid automated retrieval, so they are downloaded by hand. Automated
capture is written against the Interactive Brokers API (`src/loaders/ibkr.py`) and
has not yet been run.

## 3. Time

Time to expiry is calendar days from the valuation close divided by 365
(`tradingcal.year_fraction`), so implied vols stay comparable with the
exchange's and OptionMetrics'. Dividends are placed differently, because a
share opens lower on its ex-date and prices move in sessions, not calendar
days. With n sessions over before the ex-date's open and N sessions to expiry,
the drop sits at T·n/N, never less than a quarter of a session
(`surface.dividend_times`). A dividend counts when it falls after the
valuation date and on or before expiry.

Toronto and New York close on different holidays. Any series built from both
uses the shared trading days only (`tradingcal`), because a zero return
inserted for a closed market pulls every correlation and hedge ratio toward
zero.

## 4. Pricing and inversion

- **American options** (single stocks and ETFs) are priced on a
  Cox-Ross-Rubinstein tree with 400 steps. Discrete cash dividends use the
  escrowed construction: the tree runs on the share price less the present
  value of the dividends to come (`voleng.crr_american`).
- **SXO is European** and is priced with Black-Scholes.
- **Inversion is by bisection**, up to 500% vol and down to the lowest vol the
  tree can represent (never below 0.01%). Newton's method needs vega, which
  vanishes in the wings, and walks off.
- **The noise floor** is 0.04 vol points: the difference between a 400-step
  and a 1600-step tree. Smaller differences are treated as rounding.

Before any inversion a quote is screened (`voleng.screen_quote`). A quote that
fails returns a reason, and the reason is counted; a vol is never guessed.

| Reason | Rule |
|---|---|
| no activity | no two-sided quote, no volume and no open interest |
| crossed | ask below bid (two-sided quotes) |
| too wide | spread more than 75% of the midpoint (two-sided quotes) |
| outside the bounds | below intrinsic value, or above the share price (call) or strike (put) |
| ill-conditioned | one vol point moves the price by less than one cent: a deep in-the-money American on its exercise boundary carries no vol information |
| no bracket, not converged, bad input | the solver's own refusals |

The price inverted is the quote midpoint where a two-sided quote exists and the
settlement otherwise, and each row is labelled traded, quoted or
settlement-only.

## 5. The inputs a mark rests on

**Share price.** The underlying's row in the file is not synchronous with the
option quotes. The share price is therefore implied from the options: it is
the price at which calls and puts on the nearest monthly expiry's five
nearest-the-money strikes imply the same vol (`voleng.implied_spot_american`).
That expiry's call-put gap is zero by construction and is left out of the
evidence. An expiry with an unannounced ex-date within 5 sessions of it is not
used (`surface.DATE_WINDOW`).

**Rates.** Three-month CORRA futures and the CORRA fixings give a curve to each
expiry (`rates.futures_curve`). Treasury bills are the fallback on days without
futures. The curve stops on a gap of more than four days in the fixings.

**Funding spread.** This is an option-implied carry adjustment, conditional on
the share price, dividends and pricing conventions. It is not an independent
measurement of funding cost. One spread a day is fitted from
monthly expiries resting only on declared dividends
(`surface.fit_funding_spread`). The spread applied on a day is the mean of
earlier days' estimates only (`surface.trailing_spread`). A name uses its own
spread once it has 20 earlier estimates with a standard error of at most 5
basis points a year; until then it uses the median of the names that do
(`surface.choose_spread`). This spread comes from the same market the call-put
check tests, so on rates that check is not independent. The futures curve supplies
a separate rate reference, not an independent measurement of the adjustment.

**Dividends.** Each name's schedule is held in `reference/`, with the date
each dividend was announced. A day is priced with the schedule as it was known
that day (`loaders.reference.as_known_on`). A dividend not yet announced is replaced,
date and amount, by a projection from the ones then known. For companies the
projection is last year's dates with last year's raise added again; funds and
Barrick repeat last year. Dates published a year ahead (BlackRock's
distribution schedule) count as known, and only the amount is projected.
Barrick's dividends are held in US dollars as declared and converted at each
day's Bank of Canada rate. Every expiry resting on a projected dividend is
labelled projected.

## 6. The front chart: references and error bars

| Reference | What a gap means |
|---|---|
| Call minus put at the same strike | a carry input (share price, rate, funding, dividend) is off |
| Strike minus smile | this quote is out of line with its neighbours |
| Ours minus the exchange's vol | the two models differ; the exchange uses Black-Scholes on American options |
| Ours minus OptionMetrics (New York listings) | a separate engine check; agreement without a dividend does not isolate the cause of dividend-spanning discrepancies |

**The smile.** Each strike is judged against an SVI curve in strike over
forward, fitted through the other strikes of its expiry and screened for
butterfly arbitrage on a grid (`references.fit_svi`). An expiry needs seven
strikes. Thinner expiries are judged against one SSVI surface for the day,
whose level at each expiry is fitted, not interpolated (`references.fit_ssvi`).
The fit is robust: Tukey weights with constant 4.685, so a bad quote pulls the
curve toward itself far less than it would in a least-squares fit. Neighbouring expiries are checked for calendar
arbitrage and the failures counted.

**Buckets.** Moneyness edges at strike over share 0.88, 0.95, 0.98, 1.02,
1.05 and 1.12; expiry edges at 6, 30, 60, 120, 200 and 400 days
(`references.M_EDGES`, `D_EDGES`).

**Two error bars.** They answer two different questions.

- *Could anyone trade on the difference?* The quote bar is half the bid-ask
  spread in vol points, from the solver's own vega.
- *Is a model input wrong?* That is judged by the scatter of the measurement
  across strikes and days. A mean is set against its standard error, which is
  Newey-West across days because neighbouring days share inputs.

Expiry means are weighted by quote tightness (one over the quote bar squared),
and the plain mean is kept beside them. Where many gaps are tested at once,
the count that survives a multiple-testing correction is reported
(`multitest`).

**Rejections** are counted by reason on every chart, with the split between
traded, quoted and settlement-only rows.

**Every day and expiry** is also published on its own (`scripts/day_tables.py`):
the same differences for one name on one day, by expiry and by moneyness
bucket, never by strike, because a strike with its moneyness gives the
implied share price back.

## 7. The book

**Positions** are listed options, index futures, ETFs and shares, read from a
plain file that reports every problem at once, by line (`book.py`).

**P&L explain by repricing** (`replay.explain`). Each option is repriced one
input at a time from the last valuation to today's. The steps are carry (time
passes to today, including any expected ex-dividend drop), dividend news, share move
(split into delta and the rest, called gamma), and vol. The parts therefore
sum to the change in the mark exactly. A Greek-based explain is shown beside
it, and its unexplained remainder is what the limits report watches.

**Mark check** (`replay.check_mark`). A mark fails when its call-put gap, or
its distance from the smile, exceeds both one quote bar and the 0.04 noise
floor.
The exchange's vol is not a check: a difference there is a difference of
model.

**Review queue** (`review.py`). Every failed mark becomes an item: what was
observed, the dollars it moves (repriced at the reference vol), and a likely
cause read from the neighbours.

- *Input:* the other strikes of the expiry disagree the same way.
- *Partner:* the other side's quote is the one off its smile.
- *This mark:* this side is off its smile and its partner is not.
- *Curve:* only the smile check failed, and the call and the put sit off the
  curve together.
- *Unclear:* none of the above.

The thresholds are at least 2 other strikes, a shared gap of at least half the
mark's, and, for "together", a partner off the smile on the same side by at
least half as much as the mark. An expiry with an
unannounced ex-date near it is put on a watch list even when nothing fails.
Published output is sums over positions and counts, never one position's
value.

## 8. The closing chart: hedges

**Ratios.** Hedge ratios are least-squares betas fitted on the 250 trading days
before the day they are applied (`hedge.rolling_ratio`). Ratios fitted on the
whole sample are shown beside them as a check, never used.

**Delta hedge.**
- The index leg is shown two ways: in the TSX 60 total return, and in whole
  SXF contracts on their own settlements (`book_hedge.sxf_leg`). The SXF
  contract is the nearest one more than 5 days from expiry, it rolls before
  that. Trades pay half the spread where quoted and a full tick otherwise.
  Days without settlements at both closes earn nothing. The comparison includes
  these conventions as well as basis, rounding and cost.
- ZEB is added where the book is bank-heavy, and XEG for energy.

**Vega hedge** (C4b, `book_hedge.py`). An XIU straddle is sized each evening
to the book's vega, weighted by vol betas from earlier days. Its expiry is chosen
once, from the book as first written. It is the monthly nearest the book's average
option expiry, weighted by contracts held
(quantity times multiplier), not by vega. The vega stage is added to the delta
hedge with the index stand-in, not to the row with the SXF leg.

**Diagnostics** (`hedge.py`). The share hedges (`hedge_shares.py`) report:
- the share of variance removed, with a 20-day block-bootstrap error;
- expected shortfall;
- autocorrelation and the Ljung-Box test, to see whether what is left is
  predictable;
- variance removed split between calm and stressed days.

The option-book stages (`book_hedge.py`) report the share of variance removed,
expected shortfall, the worst day, autocorrelation and the Ljung-Box test, with
no block-bootstrap error and no regime split, so a small gap between two stages
is not read as an improvement. A position counts on a day only if it was valued
that day and the evening before; the report counts coverage and repeats the stages
on the days every position was valued (`book_hedge.coverage`).

## 9. Stress and tail risk

**Vol rule** (`volrule.py`). A stress test needs to say what vol does when the
share moves. Four rules are offered: sticky strike, sticky moneyness, sticky
delta, and beta. Beta, the default, raises at-the-money vol by 0.5 points for
each 1% fall and steepens the skew by half in a fall of 10% or more. The rule is applied
as a change to each option's own vol, and every price from it is labelled a
model price.

**Crash betas.** Index stresses move each name by its beta measured on the
worst 5% of index days since 2014 (`scenarios.crash_betas`). Calm-year betas
understate how names move together in a fall.

**Horizon.** A stress over several sessions includes the carry over them:
decay, funding and dividends (`horizon.py`).

**Historical episodes** (`episodes.py`). Four episodes are replayed on the book, with
each name's share moving as it did then. Valuation starts each name from its own
latest cached day, not one common book date. The episodes:
- January 2015, the oil collapse and the surprise rate cut;
- February to March 2020, the pandemic crash;
- April 2020, negative WTI;
- 2022 to 2023, the Bank of Canada's hiking cycle.

How the other inputs move in a replay:
- *Vol.* The Montreal files do not reach back to these episodes. Vols move by
  OptionMetrics' New York change at each option's own strike, with the
  currency's contribution removed (`nyvol.py`), and the vol rule is run beside
  it.
- *Rates.* The rate curve moves by the Treasury bill curve's change over the
  episode.
- *Funding.* A stated +100 basis point funding shock is shown separately.

Episode output is labelled as model prices throughout.

**Expected shortfall** (`tailrisk.py`). The book is fully repriced under every
day's moves since 2015. Valuation starts each name from its own latest cached day,
not one common book date; the report prints each name's date and repeats the ES
with every name valued on the earliest of them. A position that cannot be valued
would be left at zero
and named in a NOTE line; the current reports have none. ES is the average loss
beyond the 97.5% level.
Component ES is each position's average P&L on the book's worst days, so the
components add up to the book's ES exactly.

**VaR back-test.** A day-ahead 99% VaR comes from delta, gamma and vega on the
250 days before. It is tested against the next day's actual P&L on days with a
valuation row for every underlying. This is not a position-by-position
completeness check. The exceptions are counted and judged with Kupiec's test and
the Basel traffic light.

**Risk grid** (`riskgrid.py`). Vega is shown on the front chart's cells,
counted only where the market quotes a smile, and delta is mapped to the TSX 60.

**Venues** (`venues.py`). Volume is compared between the Toronto and New York
listings. Days to exit each position assume trading 20% of the average volume
over the 20 sessions before.

## 10. How the method is checked

- **Tests.** The tests run on synthetic data only, on every push. They cover
  pricing identities, round trips, put-call parity, the dividend time rule,
  the quote screen and every report module.
- **A known answer.** `scripts/demo_synthetic.py` runs the whole method on
  made-up data with a planted funding spread and a planted bad quote, and must
  recover both.
- **Planted faults.** `scripts/mark_benchmark.py` and
  `scripts/review_benchmark.py` measure how often a planted error is caught and
  how often a clean mark is flagged. The review queue's thresholds are tuned
  on one made-up set and scored on a second, independent one.
- **Robustness.** `scripts/robustness.py` changes one choice at a time: the
  dividend rule, the funding lookback, tree steps and the share-price source.
- **A second engine.** The OptionMetrics comparison agrees closely where no
  dividend intervenes. Dividend-spanning options remain discrepant. Currency
  conversion reduces the call-put gap, but the vendor's dividend assumptions are
  unavailable, so the comparison does not isolate the cause.
- **Reproduction.** `scripts/reproduce_all.py` records its commit and completed
  steps. A failed subprocess restores reports, but interruption or another
  exception can bypass restoration. A partial run can replace the manifest. The
  committed manifest does not certify every artifact against the current source.
- **Frozen rules.** Pricing modules, thresholds and projection rules are frozen
  (`docs/frozen-test-plan.md`). The next month of exchange files is the first
  test the rules have not seen.

## 11. What the method cannot do

The full list is the limitations page on the site. The main points:

- A model value is not a market price.
- Most series do not trade on a given day, so the chart rests on quote
  midpoints, and an untraded settlement is the exchange's own model.
- The bid-ask spread is far wider than the noise floor, and most differences
  sit inside it.
- The exchange's history on disk is about six months, and none of it is a
  crisis.
- Borrow cannot be separated from funding in option prices. Dividends beyond
  the last declared one are a rule, not a forecast.
- There is no depth or size at the touch, so exit paths are not modelled.
