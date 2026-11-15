# Findings so far

The first findings concern RY. Later sections cover other names, sample books and
historical share returns. This note records what was tried and rejected, and what
is still not trusted. The figures come from the reports named beside them, not
only `reports/ry_numbers.txt`, which `scripts/reproduce.py` writes.

The project has two charts. The front chart covers fifteen names. The closing
chart includes option-book delta hedges, a separate SXF comparison and an XIU
vega hedge on two sample books.

## The question

An option's price can be turned into an implied volatility, but that gives one
number, and one number cannot be wrong. To find errors you need a second number to
compare it with. The simplest is the put at the same strike. Priced in the same model
with the right inputs, a call and a put on the same share, strike and expiry should
imply the same volatility. If they don't, the volatility isn't what differs. One of
the carry inputs is off: the share price, the interest rate or the dividends.

This is a consistency test, not a law. For European options, put-call parity is an
exact identity that holds whatever the model. These options are American, where
parity is only a pair of bounds, because either side may be exercised early. So the
call and the put agree only if the model handles early exercise correctly and is fed
the right inputs, and a gap can come from the model as well as from an input. That is
why the engine was checked against a second implementation before the gaps were read
as evidence about inputs.

So the method throughout was to price calls and puts with the same inputs, look at
the gap, and work out which input the gap's shape points to.

## Finding 1: the spread is not the right yardstick

Quotes on these options are wide. Half the bid-ask spread is worth about 0.7 vol
points on a typical option and several points on a short weekly. The first version
of the chart drew every gap with that as its error bar, and every gap sat inside it.
The conclusion would have been that nothing could be learned.

That was wrong. Dealers quote wide around a fair value they largely agree on, so the
midpoints are far more consistent than the spread suggests. Across days, the mean
gap for an expiry is known to about 0.02 vol points.

There are two yardsticks, and they answer different questions:

- **The quote bar** (half the spread, in vol points) says whether a difference could
  be traded on. Nothing found here could be.
- **The standard error of the mean** helps identify means distinguishable from
  zero under the test's assumptions. Failing the screen does not establish a zero
  mean, and passing it does not establish the cause.

Everything below was invisible until the two were separated.

## Finding 2: options are funded above the overnight rate

With rates taken from Treasury bills, monthly expiries more than two months out
showed calls about 0.2 vol points above puts, on almost every day, and the same at
every strike. A gap that is flat across strikes is the mark of a carry error. Turned
into a forward price, the error grew steadily with maturity, which is what a rate
that is too low produces.

The first guess was that bills are simply the wrong curve. So a second curve was
built from three-month CORRA futures, which are the market's forecast of the
overnight rate. It sits within about 5 basis points of bills and leaves the same gap.
The options imply about **18 basis points a year over the futures curve** (standard
error 1.2) and 21 over bills.

The spread is an option-implied carry adjustment, conditional on the share price,
dividends and pricing conventions. It is not an independent measurement of funding
cost.

The spread is then added to the model, with one rule: each day uses the average of
the spreads implied on **earlier** days only, never its own. Fitted that way, the gap
on those expiries falls from +0.16 to -0.02 vol points, which is zero within its
error.

The limit of this result is stated plainly in the code and on the chart: the spread
is fitted from the same options it then explains. The futures curve supplies a
separate rate reference. It does not independently measure the carry adjustment.

## Finding 3: the time of day a dividend comes off matters

Weekly expiries just after an ex-dividend date showed puts about 0.6 vol points above
calls. It appeared on the expiry four days after each of the two ex-dates in the
sample, faded over the next two weeks, and was absent everywhere else.

Two explanations were tested and ruled out first:

- **Wide quotes.** Weighting by quote tightness removed only a third of it.
- **The dividend model itself.** The tree treats the dividend as cash set aside from
  the share price. A separately written pricer, in which the share price itself drops
  on the ex-date, agreed with the tree to 0.1 vol points.

The cause was where in the option's life the drop was placed. Counting calendar days
put it at the close of the ex-date and gave a weekend the same weight as two trading
days. But a share opens lower on its ex-date, and prices move during trading
sessions. For an option expiring four days later, one session is a quarter of the
time left, and that changes whether a call is worth exercising before the dividend.

Placing the drop by the share of sessions that are over before the ex-date's open
took those expiries from -0.63 to about +0.1, and all short weeklies from -0.16 to
-0.02.

## Finding 4: the market prices about half a dividend raise

RBC has declared dividends through October 2026. Longer options need 2027 dividends,
which nobody knows. Two projections were tried: hold the last amount flat, or apply a
rule from the company's own history (a raise at the same two points of the year, the
size of last year's). Neither uses option prices, so the call-put gap tests them.

Flat leaves long expiries at -0.05 vol points and the rule leaves them at +0.07. The
truth the market is pricing sits between, at about half the rule's raise. An earlier
version of this note said the rule clearly beat the flat line; that came from a run
with a different rate set-up and did not survive the robustness table.

The rule would also have under-predicted last time. Applied a year earlier it gives
two 6-cent raises for 2026, and the actual raises were 10 and 12 cents.

## Finding 5: the engine matches a second model, and the difference is dividends

OptionMetrics computes its own implied vol for every US-listed option, with its own
American-option model. The Canadian banks and energy names also list in New York. So
their quotes for those listings were priced with this project's engine, from
OptionMetrics' own closing share price and rate curve, and the two vols compared.
Using their share price and rate curve narrows the comparison. It does not match
every input: their dividend assumptions are unavailable.

Seven names (RY, TD, BNS, BMO, CM, CNQ, SU), June to August 2025:

- **Where no dividend falls in the option's life, the two engines agree.** On 7,884
  options the mean absolute difference is 0.006 to 0.011 vol points by name, and every
  one is within 0.25. That is inside this solver's own rounding of 0.04.
- **Where a dividend does fall in its life, they differ by one to three vol points.**
  Agreement without a dividend does not isolate the cause of this discrepancy.
  Currency conversion reduces the call-put gap, but the vendor's dividend
  assumptions are unavailable.

These companies declare dividends in Canadian dollars, and the New York options are
in US dollars. This project converts the amount at the exchange rate on the ex-date.
The market's own prices say which treatment is right: taking the Canadian amount as
if it were US dollars leaves calls 1.8 to 4.2 vol points above puts on every name (13
to 35 standard errors). Converting it leaves 0.1 to 1.1.

The converted gap is positive on every name, but the evidence differs. The t
statistics are RY 0.911, TD 9.680, BNS 4.669, BMO 4.028, CM 1.327, CNQ 10.892
and SU 11.282 (`reports/<name>_us_engine_numbers.txt`). RY and CM do not pass the
usual two-standard-error screen. Shared inputs mean these are not independent
tests. Funding and withholding tax remain untested candidates.

OptionMetrics' vols on those options could not be reproduced exactly. On RY, taking
the Canadian amount at face value reproduces theirs on 93% of options; on the other
names it does not. Their dividend projections on this subscription end in 2023, so
what they assumed in 2025 is not visible.

## Finding 6: a sector fund hedges what it holds, and honesty is cheap

The closing chart starts with shares only. Thirteen names, daily returns from 2015 to
2026, each hedged with the index and then with the index plus a sector fund. The
ratio used on a day comes from the 250 days before it.

- The index alone removes 55 to 65% of a bank's daily variance. Adding the bank fund
  takes that to 76 to 81%, and to 97% for the five banks held together.
- For the two oil producers the index removes under 40% and the energy fund takes it
  to 82 to 85%.
- The insurer gains 3 points from the bank fund and the pipeline 2 from the energy
  fund. Neither is in the thing the fund holds.
- Ratios fitted with hindsight do no better than ratios from earlier days, to within
  a point or two. With a year of daily data the estimate is not the weak link.
This share-only experiment does not measure gamma or vega. The option-book report
splits what the delta hedge leaves into delta the hedge missed, gamma, vol, carry
and dividend news.

## Finding 7: which choices the results depend on

Each choice was changed on its own, on the same days.

- The funding lookback and the tree's step count change nothing.
- The number of strike pairs used for the share price changes little.
- **The source of the share price changes everything.** With the file's last trade in
  place of the price implied from options, the funding spread can no longer be
  measured (2.6 basis points, give or take 7.5) and calls and puts stop agreeing. The
  last trade is good to about 5 basis points, which is more than a spread of 18 a
  year amounts to over a few months.

So the funding result is only visible because the share price is implied from the
options. That is a real dependence: the same options supply the share price and the
spread. The futures curve supplies a separate rate reference, not an independent
measurement of funding cost.

## Finding 8: the method caught a wrong date on a second name

XIU was first run with no funding spread, on two months of data. The next distribution had
been projected for 19 November 2026, the date of the year before. One expiry, 20
November, then showed a gap of 34 basis points of forward price while the expiries on
either side showed 1 or 2, and the share price implied from that expiry sat 17 basis
points from the file's. A gap on exactly one expiry, the one just after a projected
ex-date, says the market does not expect the payout before it.

The fund's published schedule for 2026 lists 25 November. With that date the implied
share price is 2 basis points from the file's, as it is on RY.

That was on two months of XIU. On six months (126 days) the implied share price sits
1.0 basis points from the file's last trade, with a standard deviation of 5.2.

XIU's calls sit above its puts by an amount that grows with maturity, which is what
RY showed before a funding spread was added. RY's spread was not borrowed. With six
months XIU has enough expiries on a declared distribution to fit its own: 13 basis
points a year (standard error 4.2), against RY's 17.7 (2.0). Two names, fitted
separately, land within about one standard error of each other. After XIU's own
spread its long expiries still show +0.3 to +0.55 vol points. All of them rest on
projected distributions, so that remainder is either more funding cost or
distributions projected too high, and XIU's options alone cannot say which.

## Finding 9: the error bars were too narrow, and most conclusions survived

Every mean across days was judged against scatter / sqrt(days), which assumes each
day is a fresh measurement. It is not: a slightly wrong dividend or a slow quote
pushes today's gap the same way as yesterday's. The standard correction (Newey-West)
adds the co-movement between each day and the three or four before it.

| | Plain | Allowing for dependence |
|---|---|---|
| Funding spread, standard error (bp) | 1.2 | 2.0 |
| Long monthlies, declared dividends: t | -1.6 | -1.0 |
| Long monthlies, projected dividends: t | 5.7 | 3.2 |
| Short weeklies: t | -0.9 | -0.8 |

The funding spread is still about nine standard errors from zero. Every group on
declared dividends is still indistinguishable from zero. The one result that
weakened is the projected-dividend gap, from overwhelming to merely clear. The plain
figure is printed beside the corrected one everywhere.

## Finding 10: a yardstick has to be checked like anything else

The smile each strike is judged against went through four forms. A quadratic left a
mirror-image pattern of misses. A cubic removed it but is only a flexible line: it
can imply prices no market could show. It was replaced by SVI, fitted in strike over
forward and screened for butterfly arbitrage on a grid, not proven arbitrage-free.
SVI needs seven strikes, so thin expiries dropped out; they are now judged against
one surface fitted across the whole day (SSVI), and every expiry is counted.

The first version of that surface read each thin expiry's level off a straight line
between the two strikes around the forward. A smile curves upward, so the line sat
above it, and every strike beyond 200 days came out 0.1 to 0.3 vol points below the
curve, on every day. The pooled chart showed it at once as a block of cells all the
same sign. Fitting the level instead removed it (the at-the-money cell went from
-0.33 to +0.07). A uniform sign across a whole row of cells is the signature of the
yardstick being wrong, not the quotes.

## Finding 11: the index future agrees on the sign of funding, not yet on its size

The standard source for an index's funding is its own future. The SXF basis,
ln(future / index) / T less the CORRA futures rate, is the funding spread minus the
index's dividend yield. On 91 contract-days with more than 30 days to run it is
-173 basis points a year (standard error 6). With XIU's distributions over the last
year standing in for the index's yield (2.26%), that leaves a funding spread of
about +50, and by contract +24, +72 and +36.

Those three should be one number. They differ because dividends are seasonal and a
twelve-month average is not the dividends due in each contract's own life. So the
future confirms what RY's options (17.7) and XIU's (13.1) say, that funding costs
more than the overnight rate, and puts it somewhere between about 20 and 80 basis
points. Pinning it needs the index's actual dividend points, which are not on disk.

The index options were no help, and why is worth knowing. The exchange's SXO file
has settlements only. Call minus put on those settlements gives a forward that
matches the index grown at the interest rate with NO dividends to within 0.6 basis
points on average (standard deviation 8.5), and sits 72 above the future, which is
about the dividends due. The settlements were struck without the index's dividends.
They are the exchange's model and say nothing about funding.

## Finding 12: funding and dividends can be told apart, by the shape they leave

One spread had been standing for everything the options say about carry. A funding
cost moves the forward a little more with each extra day. A dividend the market
expects to differ from the schedule moves it by a step at the ex-date and by nothing
before. So the change in forward error from the last expiry before an ex-date to the
first one after it, times the share price, is the market's dividend minus the
schedule's (`scripts/funding_split.py`).

| Name | Dividend | Schedule (rule) | Implied by options | Difference |
|---|---|---|---|---|
| RY | January 2027 | 1.86 | 1.81 | -0.05 (t -4.0) |
| RY | April 2027 | 1.86 | 1.80 | -0.06 (t -2.5) |
| TD | January 2027 | 1.15 | 1.21 | +0.06 (t 13) |
| TD | April 2027 | 1.15 | 1.16 | +0.01 (t 1.7) |
| XIU | November 2026 | 0.300 | 0.286 | -0.014 (t -6.6) |
| XIU | May 2027 | 0.272 | 0.247 | -0.025 (t -7.4) |

The raise rule over-projects RY by about 5 cents a quarter and under-projects TD's
next dividend by 6: the market expects TD to raise by more than it did a year ago.
"Same as last year" runs about 7% high for XIU. These are what a desk would use in
place of a rule, and they are not used as inputs here, because a dividend taken from
the options would make calls and puts agree by construction.

Two limits. Borrow cannot be separated from funding in option prices, so it is
assumed zero and any borrow cost sits inside the funding number. And the funding
spread is measured on expiries a few months out and assumed to hold for a year: each
basis point a year of funding left out is a basis point of share price per year of
maturity, and it lands in the implied dividends.

## Finding 13: what holds on fifteen names, and what does not

Other names and funds were run the same way (`reports/all_names_summary.txt`).
The first comparison described an earlier run. In the current report,
declared-dividend gaps survive the stricter multiple-testing rule for CNR and ABX
only. ABX has too few days for a reliable p-value. The report holds the current
funding estimates and gaps. A gap flags a disagreement, not an established cause.

## Finding 14: three independent views of next year's dividends

Three views of dividends are compared: the schedule rule, a hybrid estimate
replacing schedule amounts with option-implied amounts where available, and
analysts' consensus. The options column still uses the rule where no amount could
be isolated, so these are not independent estimates (`reports/dividend_three_way.txt`).
Across fiscal years containing a projected dividend, the mean absolute difference
from analyst consensus is 1.8% for the rule and 2.4% for the hybrid options
estimate, over 11 name-years. Agreement does not independently check the
bookkeeping.

## Finding 15: "what is left looks like noise" was too kind

The share-hedge report said the residual's one-day autocorrelation was near zero and
called it noise. Four further checks (`reports/hedge_shares_numbers.txt`):

- **A test over ten lags finds structure** in what is left of RY, BNS, CM, MFC, ENB
  and BCE, and strongly in the five-bank basket against ZEB. One lag near zero was
  not evidence of none.
- **The tail is hedged less well than the variance.** For the banks the hedge
  removes about 80% of daily variance, but the average loss on the worst 1 day in
  40 only falls by about half (RY: 3.2% to 1.4%).
- **The hedge works better in stress, not worse,** for every name but ABX: RY's
  goes from 73% of variance removed on calm days to 87% on stressed ones. In
  stress everything moves with the index.
- **The ratio is not a constant.** The index ratio's yearly average runs from 0.70
  to 1.01 for RY, and from 0.30 to 1.92 for CNQ. A ratio fitted on last year is a
  moving target for the energy names.
- **Each figure has an error:** RY's 81% is known to about 3 points, BCE's 30% to
  12.

Stress periods are now compounded returns, not sums of daily returns, and show NA
where a hedge did not exist for the whole period.

## Finding 16: why five names disagreed, and what was changed

CNQ, CNR, ENB, MFC and XEG showed gaps on declared dividends. By expiry and by date
the cause was the same for four of them: the funding spread fitted from each name's
OWN options was too thinly measured. ENB and MFC announce a dividend days before it
goes ex, so almost no expiry rests on declared dividends; they collected under
twenty estimates and ran with no spread, leaving a gap that grows with maturity.
CNQ's daily estimates ran from -80 to +427 basis points and it was given 80 where
about 35 is right. XEG's quotes are five vol points wide. CNR was mild: its applied
spread lagged its own falling estimates by about ten basis points.

The change: a name's own spread is used only where it is well measured (twenty
earlier estimates whose mean has a standard error of at most 5 basis points);
otherwise it takes the median across the names that are. A desk has one funding
cost, not one per name; a name's own figure is kept where it is good because it
also carries what is particular to that name. CNQ had a second cause, its dividend
dates: last June's record date was moved by four days and the rule copied the
unusual date forward.

The first re-run changed the gaps but did not settle their causes. Later runs
tested the expiry-date rule and the published fund dates, as described below.
The current results are in `reports/all_names_summary.txt`: declared-dividend gaps
survive the stricter multiple-testing rule for CNR and ABX only. ABX has too few
days for a reliable p-value. The current funding estimates and gaps supersede the
earlier re-run's figures.

The rule was chosen after looking at which names failed. Applying it to earlier
days only keeps each day honest, but these six months are no longer an untouched
test of the rule; the next files downloaded are.

## Finding 17: the marks are less certain than the P&L computed from them

Two made-up books, struck near the money, valued at the exchange's marks on every
day (`scripts/book_report.py`). Each option's daily change is split by repricing one
input at a time, so the parts sum to the change in the marks exactly.

- Once the share move is taken out, the dollars resting on marks that fail a check
  (vega times the disagreement with the call or put partner, or with the smile)
  exceed half of the day's remaining P&L on 18 of 127 days in the bank-heavy book
  and 17 in the diversified one, and exceed all of it on 10 and 14. (At first the
  figures were 24 and 39. They fell as Manulife's and Barrick's inputs were fixed,
  and also because the median rule for a poor fit, Finding 20, switches the smile
  check off more often on the thin long expiries these books hold. That part is
  fewer checks made, not better marks.)
- The series flagged most in the diversified book was ABX, then MFC, through the
  call-put check. Both had a dividend input known to be wrong; fixing MFC's took
  its flags from 17 days to 3. A failed pair check is a wrong carry input as
  often as a wrong quote. The review queue now assigns a likely cause from
  neighbouring strikes. This is a diagnostic label, not independently established
  ground truth.
- Dividend exposure is small on these books: 5% on every dividend is about $1,400
  against $3,400 a vol point, and the two errors found earlier move them by under
  $100. Reported as small, not dressed up.
- On made-up data with planted bad quotes (`scripts/mark_benchmark.py`), the smile
  check catches 81% of 1-point errors at 10 false alarms per 1,000 clean quotes; a
  day-on-day rule net of the market's move catches 35% at the same rate, or all of
  them at 49. A raw day-on-day rule flags 280 per 1,000 when vol moves.
- **Found by the benchmark, then fixed:** the smile fit was least squares. A second
  bad quote in the same expiry dragged the curve, and the check then missed even a
  4-point error (one in six). The fit is now iteratively reweighted
  (`references._svi_robust`: Tukey weights on the median absolute deviation, so
  far-out points get no say). 4-point errors caught went from 83% to 100%, 1-point
  from 81% to 85%, false alarms from 10 to 3 per 1,000 clean quotes. On a clean
  smile the two fits are identical, and on ordinary scatter they agree to well
  inside it (both tested).
- The two sample books had to be re-struck: written before the option files were
  on disk, most strikes were 20 to 40% from the money and two were never listed.

## Finding 18: MFC and CNR, the two names still unexplained, traced

**MFC: a dividend on the wrong side of an expiry, twice over.** Until its
announcement on 4 November the rule placed Manulife's November dividend on the
26th, a year on from 2025's. It went ex on the 20th, the day of the monthly expiry.

- The 20 November expiry was priced without a dividend the market had in it: calls
  1.24 vol points below puts on 87 days. It was labelled "declared only", because
  the projected date fell after it, so it was the whole of MFC's -0.97.
- From mid-October the share price was implied from that same expiry, so it came
  out about 27 basis points low, and every other expiry then showed calls 1 to 3
  points above puts. MFC's own "funding" estimates were negative for this reason.

This is the XIU lesson again (19 against 25 November). The rule now (hard rule 5
unchanged; `surface.date_uncertain`): an expiry with an unannounced ex-date within
five sessions either side is not used to imply the share price, and counts as
resting on a projected dividend. The model still does not take the date from the
options. The gap on that one expiry stays, and it is the signal: check the date.

**CNR: its spread drifts and the estimate lags.** CNR's own implied spread falls
from 45 basis points in May to about 14 by September; no other name shows the same
fall (the median across names stays at 30 to 38). The applied spread is the mean of
ALL earlier days, so late in the sample it is 31 against a current 15 to 25, and
CNR is over-funded: -0.11. Using only the latest 20 estimates halves it to -0.06;
the same window leaves RY where it was. Not adopted as the default: choosing a
window on the six months it is judged on would be fitting the rule to the sample.
What it does establish is that one constant spread per name is an approximation,
and CNR is where it shows.

**What the re-run under the expiry rule showed.** MFC went from -0.97 to -0.12
(t -0.6, on the 9 days of declared-dividend expiries left), and its implied share
price from 3.4 basis points below the tape to 2.3 above. In the diversified sample
book its option was flagged on 17 days before and 3 after: fourteen days of flags
had been the model's input, not the exchange's mark.

## Finding 19: a rule has to know which dates are guesses

The expiry rule in Finding 18 treated every unannounced ex-date as uncertain. The
re-run showed what that cost. XIU lost its own funding spread (42 days of estimates
became one), took the pooled 31 to 49 basis points, and its calls went from above
its puts to below. XEG went from +0.24 to +0.69.

The cause was the rule, not the funds. BlackRock Canada publishes its funds'
ex-dates for the whole year in advance (its 2026 Fund Distributions Schedule lists
XIU's and XEG's by name) and only the amount a week ahead. Those dates are known.
They are now recorded in `reference/published_dates.csv`; the model treats the date
as a fact and the amount as a projection. Manulife publishes nothing in advance,
so its dates stay guesses. With that, XIU's spread came back (13.1, 42 days), XEG
returned to +0.25 (t 1.5), and Manulife stayed at -0.12.

- The day the schedule was first posted could not be found. It is entered as the
  start of 2026 and labelled an assumption.
- The lesson is the general one: a fix written from one case (Manulife) was wrong
  for a case with different facts, and only a full re-run showed it.

## Finding 20: thirteen flags, and none was a bad quote

Every flag in this project that was followed to a cause is recorded in
[investigations.md](investigations.md). Seven were an input to the model (a
dividend's date, amount, currency or timing, or funding), five were this tool, one
was a stale price in the file, and none was a bad exchange quote.

That is what the review queue is built on (`scripts/review_queue.py`,
`src/review.py`). It keeps what was observed apart from the likely cause, and reads
the cause from the neighbours: if every strike of an expiry disagrees the same way,
one quote cannot be the reason.

- On the two sample books, 40 and 26 marks failed a check in six months. The
  position's own mark is not the likely fault on 35% and 65% of them.
- On made-up data with five kinds of fault planted, the named cause is right 87% of
  the time, averaged over the kinds, on markets that played no part in choosing the
  thresholds. An input fault is taken for a mark to re-mark 1% of the time.
- A dividend amount 25% out is flagged 1% of the time: it moves one option by less
  than its quote. Only pooled statistics see it. Barrick showed this on real data.
- Two faults in the tool were found by these tests. A second bad quote in an expiry
  hid the first (the fit is now robust). And one quote 4 points out marked its whole
  expiry a poor fit, switching the smile check off for every strike in it (fit
  quality is now a median). The earlier benchmark did not apply the book report's
  own filter, so its "100% of 4-point errors" for the smile check alone was too
  high: it is 83% alone and 100% with the call-put check.
- Each position's valuation is checked, and the hedge report counts coverage: no
  position dropped out while its name was valued, but whole names were missing on 4
  of 127 days (bank-heavy) and 7 (diversified). On the days every position was
  valued the delta hedge still removes 96% and 62%, and the tail report's VaR and
  ES on the marks are unchanged. Both versions are printed. The VaR backtest and the
  limits report leave those days out.

Not measured: time saved. That needs a trial against a desk's own review.

## Finding 21: the delta hedge works, and the index vega hedge does not

The closing chart on the two sample books (`scripts/book_hedge.py`), every hedge
set from what was known the evening before.

- **Delta.** Selling each name's delta through the index and its sector fund, by
  ratios from the 250 days before, removes 96% of the bank-heavy book's daily
  variance and 62% of the diversified book's in the published comparison. The
  point estimates are similar for earlier-day and hindsight ratios. The comparison
  remains provisional until all stages are scored on complete-book days. The
  option-book report gives no uncertainty interval for variance removed. Delta the
  hedge missed accounts for 89% and 94% of the residual variance.
- **Through the real future.** The SXF simulation trades whole contracts. It
  charges half the spread where quoted and a full tick otherwise. Days without
  settlements at both closes earn nothing. With the index leg in SXF, 96% and 60%
  of the variance is removed, against 96% and 62% through the index; trading cost
  is $260 and $2,180 over the period. The gap includes these conventions as well as
  basis, rounding and cost.
- **Vega.** The implemented XIU hedge showed little benefit on these books after
  spread costs (`reports/bank_heavy_hedge.txt`, `reports/diversified_hedge.txt`):
  the vol part of P&L has a daily st. dev. of 1,724 and 841 without it, 1,841 and
  847 with it, and the spread cost $12,084 and $6,391. No uncertainty interval is
  reported.
- **Read with care.** This result does not reject index-option hedging generally,
  or establish how it would perform in a sell-off.
- **Barrick.** Its schedule now holds US dollars, converted at each day's rate. The
  gap on projected dividends moved only from -0.82 to -0.77: the market prices its
  coming dividends about a quarter above "same as last year", because its policy
  pays a top-up tied to cash flow.
- **Index funding.** Compustat has no total-return series for the TSX 60. The
  Composite's dividends give 2.22% against XIU's 2.26%, so the yield is not the
  weak part. The spread still runs from 24 to 72 basis points by contract.

## Finding 22: the vol rule matters less than expected on these books, and the data cannot set it

Four rules for what implied vol does when the share moves (`src/volrule.py`), with
every option repriced in full (`scripts/stress.py`). On a 20% index fall the
bank-heavy book loses $470,000 to $492,000 depending on the rule, a spread of
$22,000; the diversified book, which carries a short future, gains $112,000 to
$123,000.

- **Which beta moves each name mattered more than the vol rule.** The first
  version moved each name by its beta over the last 250 days. CNQ's and Suncor's
  were negative in 2026, so a 20% index crash moved them up. Found while building
  C2, which prints each beta beside its correlation. Falls now use crash betas:
  each name's average move on the index's worst 5% of days since 2014, over the
  index's (`scenarios.crash_betas`). CNQ 1.43, Suncor 1.31, Barrick 0.58 (its calm
  year said 2.46), banks 0.9 to 1.0. The bank-heavy loss grew by about $80,000 and
  the diversified gain shrank by about $55,000: four times what the vol rule moves.

- The books are mostly delta, so the rule moves the answer by about 6%. On a book
  that was long or short vega it would decide it.
- The days on file show at-the-money vol moving a median -0.13 points per 1% share
  move. Six months with no crash measure the response to ordinary days only. The
  beta in the rule (-0.5 by default) is therefore a control someone sets, labelled
  as such, and every scenario price is labelled a model price.

## Finding 23: the tail is a few positions, and a hedge can add nothing

The book is repriced in full under each day's joint moves since 2015
(`scripts/tail_risk.py`). Valuation starts each name from its own latest cached
day, not one common book date. With every name valued on 4 November the book's ES
is 100,519 against 101,261 as published (bank-heavy) and 13,336 against 12,627
(diversified); both are printed. A position that cannot be valued would be left
at zero and named in a NOTE line; the current reports have none. Component ES is
each position's average P&L on the book's worst days; the components sum to the
book's ES.

- **Bank-heavy:** ES $101,300. Three January calls (RY, TD, BNS) are 68% of it. The
  RY share position, which is short, is -13%: it pays on the bad days.
- **Diversified:** ES $12,600. The two SHOP options are 46% and the CNQ call 19%.
- **The delta hedge as positions of its own** takes the bank-heavy shortfall to
  $20,300. On the diversified book it leaves it at $13,700: that book already holds
  a short index future, and hedging each name through the index and a sector fund
  trades the little index risk left for sector-against-name risk of the same size.
  A hedge is not an improvement by definition; it depends on what the book already
  is.

## Finding 24: a VaR from a calm year failed, on the book that was exposed

A one-day 99% VaR set each evening from the 250 days before, against the next day's
actual marked P&L.

- **Bank-heavy:** 5 losses past it in 123 days, where 1.2 are expected. Kupiec
  p-value 0.01; Basel yellow. The book's actual daily standard deviation over those
  days was about a quarter above what the trailing windows implied.
- **Diversified:** 2 in 120. Kupiec p-value 0.50; green.
- The mechanism is the ordinary one: a historical VaR knows only the market its
  window had. The cautions are in the report: 120 days cannot tell 99% from 98%,
  and this VaR prices through delta, gamma and vega.

## Finding 25: over five months, options were priced for a little less than came

At-the-money implied vol on the first day of each monthly window against the vol
realized to expiry (`scripts/realized_implied.py`). 75 name-windows; five months.

- Implied was below realized by 1.0 point on average, and above it in 25 of 75.
  Four of five months were negative.
- A straddle bought at the ask and delta hedged each close made 2.5% of its premium
  on average; sold at the bid it lost 13.6%. The quoted spread, 10.6% of the
  premium, is most of the gap between the two.
- Five windows do not show that options here are cheap. The same table in a month
  of falling realized vol would read the other way.
- The first version froze the hedge for the last week before expiry, because the
  cached vols stop seven days out, and reported the buyer at +10.7%. Hedging from
  each day's own mark took that to +2.5%. A hedged-straddle figure is mostly a
  statement about the hedge.
- The skew table was not repeatable at first. Two runs of the same code differed
  by up to 0.05 vol points in most cells and by 0.4 in SHOP's butterfly. The
  script picks the monthly expiry nearest 45 days out, and when two were equally
  far the choice depended on an ordering Python changes from run to run. The
  nearer expiry now wins a tie, and two runs give identical output. The banks'
  and XIU's figures above did not move at the first decimal.

## Finding 26: real episodes show where the stress rules are wrong, in both directions

Today's two books were replayed through January 2015, February to March 2020,
April 2020 and the 2022 to 2023 rate cycle, every name moving as it really moved
and vols taken from the New York listings with the currency removed
(`scripts/replay_episodes.py`, C3). Two of the stress test's simplifications were
measured, not assumed.

- **The C1 vol rule understates a crash and overstates a grind.** At the 2020 low
  the market had raised the vols at the bank-heavy book's own strikes by 46 points
  on average; the rule, at -0.5 points per 1% fall with a steeper skew past -10%,
  by 17. In the 2022 grind the rule added 3.1 and the market took away 5.6. Vol responds to the speed
  of a fall as well as its size, which a rule in the size alone cannot carry.
- **One-day crash betas understate a month-long crash.** CNQ fell about 1.9 times
  the index over February to March 2020, against a crash beta of 1.43 from single
  bad days. The beta stress stays as a quick sensitivity; the replay is the
  measurement.
- **A beta misses what is not the market.** In January 2015 the index ended flat
  and the banks fell about 8% on a surprise rate cut. The bank-heavy book lost
  $262,000 in a month a beta stress would call quiet.
- **The dollar effect of the vol source is modest on these books** ($17,000 on a
  $710,000 loss in 2020, $40,000 in 2022), because they are mostly delta.
- **A held option keeps its strike, and the smile is not flat.** Read at each
  option's own strike instead of at the money, the 2020 vol rise on the bank-heavy
  book is 46 points, not 52, and the 2022 move is -5.6, not +1.2: as the banks
  fell, the calls struck near the money became high strikes, where equity smiles
  sit lower. A parallel shift overstated the vol the book was charged. (The
  at-the-money figures: the parallel-shift run, reports at commit 118d0ed.)
- **The heavy-oil discount is not a material risk for these books.** Fitted beside
  the index and WTI on monthly data since 2014, no name's sensitivity to the
  WCS-WTI discount is distinguishable from zero, and the producers lean, if
  anything, toward doing better when it widens (they upgrade and refine heavy
  crude). The worst month with WTI flat (February 2018) moves the books by
  -$1,600 and -$500, inside about ±$10,000 at two standard errors. The measure (as a
  share of WTI, not dollars a barrel) was chosen after trying both.
- **So is the options' own exposure to rates.** Today's curve is shifted by the
  Treasury bill curve's move in each episode (the 3-month bill +454 basis points
  over the 2022 to 2023 cycle). That added $23,000 to the bank-heavy book over the whole cycle. The
  cycle reached the book through the banks' share prices.

- **Funding stress does not matter to these books either.** The 3-month bankers'
  acceptance less bill spread moved about -3 basis points in January 2015 (-$100);
  Canada stopped publishing it after 2018, so 2020 and 2022 are not measured. A
  stated +100 basis point funding shock adds +$2,000 to +$6,300 (bank-heavy) and
  at most +$2,900 (diversified): a wider spread raises the forward, and the books
  are long calls.

What is not in it: funding moves for 2020 and 2022 (no published Canadian rate;
shown as a stated shock instead), the smile beyond the 25-delta points (held flat),
and a real Montreal settlement for any episode.

## Finding 27: the Greeks explain the book, once theta is measured the desk's way

Each day's change in the marks, split through the previous evening's Greeks, leaves
a typical residual of $51 (bank-heavy) and $32 (diversified) against typical days
of $14,600 and $4,400 (`scripts/limits.py`, C8). No day is flagged.

- **One-day theta times days passed is wrong across a weekend ex-date.** It left
  -$27,900 and -$25,400 of residual (bank-heavy; -$6,000 and -$5,600
  diversified) on the two Mondays the banks went ex-dividend, 27 July and
  26 October. The limits report prints both versions. Friday's one-day theta holds the jump as the dividend leaves the
  option's life, and three days counted it three times. Repricing theta to the
  next session at the expected share price removed it.
- The residual moves with dividend news (correlation +0.52 with the repricing split's
  dividend step on the bank-heavy book); the report does not show that dividend
  news explains all of it.
- **Limits from one stated tolerance** ($50,000 on a 1-in-100 day) put the
  bank-heavy book in breach on mapped delta (105%) and expected shortfall (130%)
  in the exposure carried into the latest day, and on 10 and 44 of 123 days over
  the period. The diversified book uses 20% and 38%. Vega uses 13 to 14% on both.
  Usage is from the previous valuation, not that day's closing exposure.
- **Bucketed vega limits need more history than six months.** Per bucket, one
  name's earnings day (Shopify, -12.7 vol points on 3 November) set a limit by
  itself, so one vol move size from every bucket is used, measured on the days on
  file.

## Finding 28: Toronto is the main market for the banks, and liquidity does not cross a holiday

`scripts/venue_panel.py` (C6) compares daily share volume in Toronto and New York
for thirteen interlisted names since 2014.

- **The two volumes are not the same kind of number.** New York's is consolidated
  (within 1% of CRSP's on 96.9% of 32,868 name-days); Toronto's is most likely the
  TSX alone (within 1% of the Montreal file's on 94.7% of 1,500 name-days, and no
  step when TMX began a consolidated figure in September 2025). Toronto's share is therefore a lower bound on Canada's, and a fall over
  time cannot be told apart from trading moving to another Canadian venue.
- **Over the last 12 months** Toronto took 66 to 75% of the banks' and Manulife's
  volume, 59 to 65% of the energy names' and Enbridge's, and 30% and 17% of
  Barrick's and Shopify's. The energy names have moved toward Toronto since 2014
  (CNQ +17 points, SU +14): a rise, so not an effect of the basis.
- **On a US holiday Toronto trades at a median 0.34 of its normal volume; on a
  Canadian holiday New York trades at 0.60 of its.** Flow that normally uses both
  markets does not move to the one that is open.
- **The options are the illiquid part of the books.** At a fifth of average volume,
  every name's share delta exits within a day; the options take a median 58 to 59
  sessions in their own series.
- **A bug caught before publishing:** a twelve-month share computed with a pandas
  grouping bin was split at New Year, so the table kept only the weeks before it
  and overstated CNQ by about ten points (64% is right). Replaced with a plain
  total (`venues.share_over`) and a test across New Year.
- **A ticker is not an identifier.** CRSP matched by Barrick's New York ticker B
  returned another company that used B before Barrick did; the agreement check
  failed on every day and found it. CRSP is now matched by CUSIP, and Barrick,
  whose CUSIP is newer than CRSP's last year on file, is left unchecked rather
  than matched wrongly.

## A fourth outside review (13 November 2026)

A reviewer read the live page as an equity derivatives desk would. It ran nothing,
so its scores are an opinion on the write-up. Its points were checked against the
code before any was acted on.

Accepted and done:

- **The plan for the next month is fixed in advance** (`docs/frozen-test-plan.md`).
  Every rule here was chosen on the same six months, so none has had a clean test.
  The code, the thresholds and the pass marks were committed before any later file
  was downloaded.
- **Calls and puts agreeing is a consistency test, not an identity.** The opening of
  this note said they "must" agree. For American options parity is a pair of
  bounds; they agree only inside a model that handles early exercise correctly.
  Reworded.
- **The order of the P&L split is a convention, and now it is measured.** The split
  moves the share before the vol. Reversed, the total is identical and the part
  that needs both moves changes bucket. On the diversified book that is $24 on a
  typical day and $424 at most, against a daily standard deviation of $876 for the
  vol step; the two orders' daily vol figures correlate 0.997. The convention does
  not drive any conclusion here. It would on a book with large positions far from
  the money, where the interaction is bigger.
- **Many tests at once.** Sixteen of 28 gaps pass two standard errors; twelve
  survive a false-discovery correction. RY's and CNQ's declared-dividend gaps do
  not survive, CNR's does. CNQ was listed as an open miss; it is now better
  described as not distinguishable from chance on this sample, and the next month
  decides.
- **Dollars in dispute, counted once.** The period totals count a mark on every day
  it stays failed. Per position, once, they are about a third as large, and the
  most open on one day was $3,012 and $2,111.
- **A desk summary opens the page**, with every figure read from the reports by the
  build, which stops if one is missing.
- **The work list was stale** and is now marked as a historical proposal.

Already on the plan: residual gamma and vega by name and maturity (C2), and stress
with the share and the vol moving together (built the same day).

Not done, and why:

- **Owners, assignment and sign-off for each review item.** There is no desk to
  assign to. It would be decoration.
- **Rebalancing schedules, slippage and market impact.** An execution model is out
  of scope. Scoring the hedge on the SXF future's own prices with whole contracts
  is now done (14 November): see the closing chart's SXF row.
- **A set of independently labelled real cases.** It cannot be produced from here.
  The thirteen investigations are a small, selected set and are described that way.

## What a third outside review found (12 November 2026)

Accepted, and acted on in the same release:

- **Reports, caches and text did not describe one run.** Every name was re-run under
  the final conventions and the tables above come from that run.
- **"Funding spread" claims more than the evidence gives.** It is an option-implied
  carry adjustment, conditional on the share price, the dividends and the pricing
  conventions used; borrow cannot be separated from it. With the file's last trade
  as the share price it cannot be measured at all.
- **The smile is screened, not proven arbitrage-free:** butterfly conditions are
  checked on a grid of strikes, slices can cross between expiries (counted), and the
  thin-expiry surface keeps the held-out strike in its shared shape.
- **Many cells, one threshold.** With 25 to 50 cells tested at two standard errors,
  one or two flags are expected by chance.
- **The hedge headline follows the diagnostics:** not "noise".

Accepted, not done:

- **The project had not shown a shortcoming it fixes.** Greeks and scenarios exist
  elsewhere. Done since (Finding 17): two sample books valued end to end, the
  dollars of daily P&L sitting on marks that fail a check, a catch rate on planted
  bad quotes against plain rules, and dividend exposure in dollars. The
  option-book hedge stages are now built.
- Days with too little dividend history fall back to the file's amounts (none in
  the current sample, and counted if any occur). Hand-entered projected dates
  carry no date on which they became public. The New York check converts
  dividends at the ex-date's exchange rate, which is hindsight.
- The share-only study remains an index-proxy comparison before trading costs.
  The separate SXF comparison includes whole contracts, rolls and spread costs;
  the XIU hedge also charges spread costs.
- The live path is untested and the day runner reads exchange files only.

## What an outside review found (12 November 2026)

Three faults in controls, all confirmed against the code:

- **A return across a missing day was set beside a one-day return.** The price
  reader took a return over any gap, and returns are joined by the day they end on.
  In the data the index has no row for 23 November 2017, a day every share traded,
  so that Friday the index's two-day move was paired with each share's one-day move.
  Only one-session returns are kept now, and the ones dropped are listed in the
  report. One day in 3,225; no published figure changed.
- **A book could hold a quantity of `nan` or a multiplier of `inf`.** Both parse as
  numbers. They are refused now.
- **The cache key used each exchange file's name and size, not its contents,** so a
  corrected quote of the same length would be missed. The files are hashed by
  content now, and the existing cache was moved to the new keys without re-pricing.

And two boundaries it asked to be drawn more tightly, both fair:

- Twelve years of share prices support hedge ratios. They say nothing about what is
  left after hedging a book's OPTIONS, and the index stands in for a future whose
  basis, rolls and costs are not in it.
- Unannounced dividends are now projected from information then known, rather
  than priced at amounts declared later. Announcement-date estimates and
  BlackRock's assumed publication date remain limitations. The New York engine
  check still converts dividends at the ex-date's exchange rate.

## A second outside review (12 November 2026)

Checked item by item against the code. Fixed:

- **The solver answered for inputs it could not answer for.** A price of `nan`
  compares False with everything, so it passed every bracket test and came back as a
  500% vol marked "ok"; with no iterations allowed it returned the middle of the
  bracket, also "ok". The chain screen stops such rows first, but the function is
  called directly too. It now refuses non-finite inputs and reports
  non-convergence, each with its own reason and no vol.
- **The share-price expiry was counted as evidence on the front chart.** The share
  price is implied from one expiry's call-put pairs, so calls and puts agree there
  by construction. Those pairs are now left out of the call-minus-put panel, as they
  already were from the carry tables, and the number left out is printed.
- **Two runs of a day could be read as two days.** A cache folder can hold a day
  under more than one set-up. The current folders hold one each (checked), but
  nothing enforced it. Every reader now stops if a day appears twice.
- **The surface's no-arbitrage claim was too strong.** The documented condition only
  implies the real ones while at-the-money total variance stays at or below 1. That
  covers every equity here, but it is not the guarantee the note claimed. The
  fitter now checks the two actual conditions at the largest level fitted, and the
  note says these are conditions on a European surface, not on the American prices.
- **A claim that did not match its table.** "Autocorrelation within 0.07 of zero for
  every single name" was wrong for CM (-0.13), and "looks like noise" claimed more
  than a lag-one correlation can show. Both are reworded.

Accepted, and done the same day:

- **Dividends are point-in-time.** Each day is priced with the schedule as it could
  have been written that day: a dividend not yet announced is replaced, in amount
  and date, by what the rule projects from the dividends then known
  (`loaders.reference.as_known_on`). Where an announcement date could not be found
  the dividend counts as unknown until it goes ex. Still retrospective: the exchange
  rate used on the New York listings is the one on the ex-date.
- **A gap inside the CORRA fixings stops the curve builder** instead of the last
  rate being carried across it.
- **The project-wide runner records its commit and completed steps**
  (`scripts/reproduce_all.py`). A failed subprocess restores reports, but
  interruption or another exception can bypass restoration. A partial run can
  replace the manifest. The committed manifest does not certify every artifact
  against the current source.
- **Hedge interpretation** includes tail and dependence diagnostics. The
  share-only report also has bootstrap errors and a regime split. The option-book
  stages do not, and their complete-book coverage remains to be reconciled.
- **A made-up-data example runs the whole method** with no licensed files
  (`scripts/demo_synthetic.py`, also the end-to-end test).

## The front chart

The current RY front chart pools 104 days. Each implied vol is compared with the
available references by days to expiry and by strike relative to the share price.
Current bucket results are in `reports/ry_numbers.txt`.

| Reference | Result |
|---|---|
| Call minus put | Current bucket means and standard errors are in reports/ry_numbers.txt |
| Strike minus a smile fitted through the other strikes | Current bucket means and fit coverage are in reports/ry_numbers.txt |
| Ours minus the exchange's published vol | Current model differences are in reports/ry_numbers.txt |

The expected sign of the third was written into the script before the chart was
drawn: a European formula reads early-exercise value as extra volatility, so the
exchange's number should be above ours where early exercise matters. The current
report shows the observed signs and any exceptions.

Of 22,674 rows, 1,293 were rejected and counted by reason, or 5.7%. The evidence
split is 74% quoted, 25% traded and 1% settlement-only.

## What was tried and rejected

| Choice | Rejected because |
|---|---|
| Newton's method to invert prices | Vega vanishes in the wings and Newton walks off. Bisection always brackets the answer. |
| Inverting every quote | A deep in-the-money American option on its exercise boundary has the same price at every low volatility. A solver returns a confident wrong number, so those are screened out and counted. |
| The settlement price as the mark | Only about 15% of series trade on a day, and the settlement falls outside the closing quote 6% of the time. The quote midpoint is the market's number where a two-sided quote exists. |
| The share price from the file | Its own quote is about 20 basis points wide. The price implied from near-the-money call-put pairs is known to about 5. |
| Calibrating that on the nearest weekly | Weekly quotes are the widest on the chart. The nearest monthly, with five strike pairs, is used. |
| One overnight rate for every expiry | Left a gap that grew with maturity to over a vol point. |
| Fitting the funding spread on the day it is used | Calls and puts would agree by construction, and the chart could no longer fail. |
| An implied borrow rate for each name | A one-sided band misses the truth on hard-to-borrow names, and the two-sided band is too wide to use. The function is kept with tests that show why. |
| The quote spread as the error bar | It answers whether you could trade, not whether an input is wrong (Finding 1). |
| Gamma by bumping the share price | Wrong by a factor of three on a tree. Delta and gamma are read off the tree's own nodes. |
| Inverting the bid and the ask for each quote bar | Three inversions per row. Dividing the spread by the vega the solver already measures gives the same bar in a third of the time. |
| Calendar days for dividend timing | Finding 3. |
| Counting all time in trading sessions | It would fix nothing further and would put every implied vol on a different scale from the exchange's and OptionMetrics', so they could no longer be compared. |
| Taking a dividend at face value across currencies | On the New York listings it leaves calls two to four vol points above puts. The amount is converted at the exchange rate on the ex-date. |
| A quadratic smile | Its misses repeated in a mirror-image pattern across the money every day, up to a vol point, because the real smile is steeper on the put side. A cubic removed it. |
| A cubic smile | It fitted, but a polynomial can imply prices that admit arbitrage and runs off past its last strike. Replaced by SVI against the forward, with a surface for thin expiries (Finding 10). |
| Reading a thin expiry's level off a line between two strikes | A line across a curved smile sits above it; it biased every long expiry by 0.1 to 0.3 vol points. The level is fitted instead. |
| Standard errors that treat days as independent | Neighbouring days move together, so they claimed too much (Finding 9). |
| Applying RY's funding spread to XIU | A spread fitted from one name's options carries that name's dividend and borrow effects. XIU uses its own, and the index future is the proper source. |

## What is still not trusted

- **Fifteen names, six months.** The Montreal option-mark sample contains no
  crisis. Earlier share-return histories and model-priced crisis replays exist,
  but they are not historical Montreal option marks.
- **Gaps on declared dividends:** CNQ (-0.17, half explained; significant alone but
  not under the stricter multiple-testing rule) and CNR (-0.11, a drifting spread,
  diagnosed and not changed; significant under it). ABX (-0.23) is significant
  too, but on 2 days, which makes its p-value too small. MFC is explained on 9
  days only.
- **The funding spread differs by name, 13 to 49 basis points.** It is one number
  standing for funding, expected dividends and borrow together.
- **Barrick's projected dividends.** The market prices them about a quarter above
  the rule. The rule repeats last year; the company's top-up follows its cash flow.
- **Analysts' consensus is dated 20 August 2026,** the latest on WRDS, and predates
  some November announcements.
- **The funding spread's size.** It is fitted from the options it explains, and it
  ran slightly high early in the sample.
- **Dividends beyond October 2026.** They follow a rule, and the rule is mine.
- **Days before a dividend was announced.** Unannounced dividends are projected
  from information then known, rather than priced at amounts declared later.
  Announcement-date estimates (`reference/dividend_announcements.csv`) and
  BlackRock's assumed publication date remain limitations. The New York engine
  check still converts dividends at the ex-date's exchange rate.
- **Dividend projections, tested on one year.** The raise rule would have predicted
  two 6-cent raises for 2026 from 2025's. The actual raises were 10 and 12 cents.
  (The ex-dates themselves are now confirmed by a second source: OptionMetrics'
  record shows the ex-date equal to the record date on every RY dividend since
  June 2024.)
- **The standard errors.** They now allow a day to resemble the three or four before
  it (Finding 9). They still take neighbouring days in the series as neighbouring
  days, and dependence that lasts longer than a week is not captured.
- **The smile across time.** Each expiry's curve is screened for butterfly
  arbitrage on a grid, not proven arbitrage-free. Expiry curves are fitted
  separately, and calendar-check failures are counted. Thin expiries use a shared
  surface.
- **The smile under 30 days.** Misses of 0.1 to 0.6 vol points alternate in sign
  across strike bands, the same way every day: the curve's shape, not the quotes.
- **XIU's funding spread.** Thirteen basis points from its own options, 24 to 72
  from the index future. Bracketed, not measured.
- **When BlackRock's schedule was first public.** Assumed the start of 2026. XIU's
  and XEG's results rest on its dates being known on each day.
- **The expiry rule's window.** Five sessions either side, set from two cases
  (XIU and MFC). It has not been tested on data it was not chosen on.
- **The review queue's cause labels.** Right 87% of the time on planted faults. On
  real marks the truth is known only for the thirteen cases investigated, all by
  the person who built the checks.
- **The vega hedge's betas.** Fitted from daily at-the-money vols whose own noise
  is as large as the moves, which biases them toward zero.
- **Poor fits on thin expiries.** The median rule drops the smile check on more
  long, thin expiries than the rule before it. Fewer marks are judged there.
- **The index option file.** The exchange's SXO file carries settlements only: no
  bids, asks, volume or open interest. Parity on it shows the exchange's model.
- **The exchange's method.** Its published vol behaves like a European formula, but
  that is inferred from the pattern. Its inputs are not known.
- **The same-day comparison of the two markets.** The engine has been checked against
  OptionMetrics, but on 2025 dates and New York quotes. OptionMetrics on this
  subscription ends in August 2025 and the Montreal files start in May 2026, so the
  two markets have never been compared on the same day.
- **The gap left on New York options that span a dividend** (0.1 to 1.1 vol points,
  Finding 5). Funding and withholding tax are candidates. Neither is tested.
- **The end strikes of each smile.** They are judged by a curve extended past its
  data. SVI's wings go straight, which is better behaved than a polynomial there.
- **The share price on 29 May 2026.** The file's last trade sits 104 basis points
  from the implied price that day and looks stale.

## What does not exist yet

- Live capture. The script for Interactive Brokers is written and has never run
  against a live session; every file so far was downloaded by hand.
- A vol rule fitted to a crash. The stress test offers four rules and a control;
  the six months on file hold no crash to fit one to.
- A VaR priced in full each evening. The backtest goes through delta, gamma and
  vega.
- New York trading after the close and intraday volume buckets. Vega limits,
  usage bars (C8) and the daily interlisted volume panel (C6) are built.
- In the historical replay (C3): measured funding moves after 2018 (no Canadian
  rate is published; a stated shock stands in), and the smile beyond the 25-delta
  points.
- Correlation that changes with the size of the move. The stress test uses one
  crash beta per name for every fall, so a 5% fall and a 20% fall move a name by
  the same multiple of the index. The crash betas come from days averaging a 2%
  fall; a 20% fall is ten times larger than any of them.
- Earlier Montreal option marks. Earlier share-return histories and model-priced
  crisis replays exist, but OptionMetrics on this subscription has no Canadian
  listings from which to extend the Montreal option history.
- A walkthrough. The live page is charts, reports, the method
  (`docs/methodology.md`) and a page for every day and name.
