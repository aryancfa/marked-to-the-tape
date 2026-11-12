# Findings so far

What this project has found on RY options between May and November 2026, what was
tried and rejected, and what is still not trusted. Every number here is a derived
statistic and can be checked against `reports/ry_numbers.txt`, which
`scripts/reproduce.py` writes.

The project has two charts. The front chart (model against market) exists for RY.
The closing chart (what is left after a hedge) exists for shares only, on thirteen
names; its option layers are not built.

## The question

An option's price can be turned into an implied volatility, but that gives one
number, and one number cannot be wrong. To find errors you need a second number to
compare it with. The simplest is the put at the same strike: a call and a put on the
same share, strike and expiry must imply the same volatility. If they don't, the
volatility isn't what differs. One of the carry inputs is off: the share price, the
interest rate or the dividends.

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
- **The standard error of the mean** says whether a difference is real. Several
  were.

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

So the spread is not a quirk of which risk-free curve is used. It is what the option
market charges to finance a share position.

The spread is then added to the model, with one rule: each day uses the average of
the spreads implied on **earlier** days only, never its own. Fitted that way, the gap
on those expiries falls from +0.16 to -0.02 vol points, which is zero within its
error.

The limit of this result is stated plainly in the code and on the chart: the spread
is fitted from the same options it then explains. The futures curve is independent
and shows a spread is needed. It cannot say how large it should be.

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
Using their inputs is the point: a difference then cannot come from the data.

Seven names (RY, TD, BNS, BMO, CM, CNQ, SU), June to August 2025:

- **Where no dividend falls in the option's life, the two engines agree.** On 7,884
  options the mean absolute difference is 0.006 to 0.011 vol points by name, and every
  one is within 0.25. That is inside this solver's own rounding of 0.04.
- **Where a dividend does fall in its life, they differ by one to three vol points.**
  The engines have already been shown to agree, so this is the dividend each side
  assumes.

These companies declare dividends in Canadian dollars, and the New York options are
in US dollars. This project converts the amount at the exchange rate on the ex-date.
The market's own prices say which treatment is right: taking the Canadian amount as
if it were US dollars leaves calls 1.8 to 4.2 vol points above puts on every name (13
to 35 standard errors). Converting it leaves 0.1 to 1.1.

That remaining 0.1 to 1.1 is positive on all seven names, so it is real. The run adds
no funding spread, which accounts for some of it. Withholding tax on dividends paid
to US holders is a second candidate. It has not been taken apart.

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
- What is left has no day-to-day pattern on any single name (autocorrelation within
  0.07 of zero) and is nearly uncorrelated with the fund not used. One exception: the
  bank basket's residual against the bank fund has an autocorrelation of -0.23. A
  residual that reverses the next day is what a stale closing price on one side
  produces, so that is more likely the fund's close than anything tradeable. It is
  not tested.

This says nothing yet about options. A futures hedge removes delta, and on an option
book most of what remains is gamma and vega.

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
spread. The check that does not share it is the futures curve, which showed that a
spread is needed.

## Finding 8: the method caught a wrong date on a second name

XIU was run with no funding spread, on two months of data. The next distribution had
been projected for 19 November 2026, the date of the year before. One expiry, 20
November, then showed a gap of 34 basis points of forward price while the expiries on
either side showed 1 or 2, and the share price implied from that expiry sat 17 basis
points from the file's. A gap on exactly one expiry, the one just after a projected
ex-date, says the market does not expect the payout before it.

The fund's published schedule for 2026 lists 25 November. With that date the implied
share price is 2 basis points from the file's, as it is on RY.

XIU's calls also sit above its puts by an amount that grows with maturity, 0.3 to 0.7
vol points, which is what RY showed before a funding spread was added. No spread was
applied to XIU, and RY's was not borrowed, so this is a second name pointing at the
same funding cost independently.

## The front chart

With those inputs settled, each implied vol is compared with three references over
92 days, pooled by days to expiry and by strike relative to the share price.

| Reference | Result |
|---|---|
| Call minus put | No cell further than 0.14 vol points from zero |
| Strike minus a smile fitted through the other strikes | No cell further than 0.08 beyond 30 days |
| Ours minus the exchange's published vol | In-the-money puts up to 2.4 lower on our side; calls 0.2 to 1.0 lower beyond 60 days |

The expected sign of the third was written into the script before the chart was
drawn: a European formula reads early-exercise value as extra volatility, so the
exchange's number should be above ours where early exercise matters. It is, and no
cell has the opposite sign.

Of 19,984 rows, 5.5% were rejected and counted by reason. Of those used, 74% rest on
a two-sided quote, 25% on a trade and 1% on a settlement alone.

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

## What is still not trusted

- **One name, six months.** Nothing here has seen a crisis, a dividend cut or a
  hard-to-borrow share.
- **The funding spread's size.** It is fitted from the options it explains, and it
  ran slightly high early in the sample.
- **Dividends beyond October 2026.** They follow a rule, and the rule is mine.
- **Days before a dividend was announced.** The model uses the amount declared later
  on those days. They are labelled and kept out of the spread fit, but it is
  hindsight.
- **Dividend projections, tested on one year.** The raise rule would have predicted
  two 6-cent raises for 2026 from 2025's. The actual raises were 10 and 12 cents.
  (The ex-dates themselves are now confirmed by a second source: OptionMetrics'
  record shows the ex-date equal to the record date on every RY dividend since
  June 2024.)
- **The standard errors.** They treat days as independent. Neighbouring days are not,
  so a result just past two standard errors deserves less weight than it looks.
- **The exchange's method.** Its published vol behaves like a European formula, but
  that is inferred from the pattern. Its inputs are not known.
- **The same-day comparison of the two markets.** The engine has been checked against
  OptionMetrics, but on 2025 dates and New York quotes. OptionMetrics on this
  subscription ends in August 2025 and the Montreal files start in May 2026, so the
  two markets have never been compared on the same day.
- **The gap left on New York options that span a dividend** (0.1 to 1.1 vol points,
  Finding 5). Funding and withholding tax are candidates. Neither is tested.
- **The end strikes of each smile.** They are judged by a curve extended past its
  data.
- **The share price on 29 May 2026.** The file's last trade sits 104 basis points
  from the implied price that day and looks stale.

## What does not exist yet

The option layers of the closing chart: the book's P&L with its options revalued each
day, split into the delta the future missed and the gamma and vega it cannot touch,
and then a vega hedge with listed options. They need implied vol history by name,
which for the New York listings is on WRDS to August 2025 and has not been pulled.
The front chart exists for one name.
