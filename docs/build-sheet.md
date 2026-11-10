# Build sheet

The plan for this repo. Each item has a reason, a definition of done and its dependencies.
Work roughly in phase order. Nothing outside this sheet gets built without a deliberate decision.

**The product is two charts.** Front: implied vol on Montreal options against four references (call vs put at the same strike, a fitted smile, the exchange's own implied vol, OptionMetrics), in vol points by expiry and moneyness, with a count of everything excluded and why. Closing: residual P&L after short SXF and ZEB, then after a listed-option vega hedge, by name and by sector. The demo opens on the first and closes on the second.

## Data stack

| Source | Used for | Cost | Licence |
|---|---|---|---|
| Montreal Exchange website (historical daily summaries) | Option history and the ongoing capture | Free | Personal, non-commercial use. Publishing needs their permission |
| Interactive Brokers Canada | Automated capture, live Montreal chains, intraday data | Montreal Exchange Level I: CAD 6/month. Canadian Stock Bundle Level I: CAD 13.50/month | Exchange data for your own use. Read their agreement |
| WRDS OptionMetrics US | Independent check on the New York listings (B8) | Through Northwestern | No redistribution. Publish your own statistics only |
| Python and voleng.py | Pricing, implied volatility, risk | Free | Yours |
| TradingView (free plan) | Watching markets; not a data source for the project | Free. US real-time: $3 per exchange or $9.95/month bundle | For viewing |

## First milestone: one chart, live

Before anything in Week 3 is started: the front chart for RY only, from real files, with the rejection tally and the three-tier split, on a live link. It needs CAP, A1, A4 for RY, B1, B2, B3, B4 and B5 and nothing else. Every later item makes this chart wider or adds the second one; none of them is worth more than having it exist.

## Start: Get the data

Four items that decide what data you have and what you may do with it. Finish these before writing application code.

### CAP. Download Montreal Exchange option history  _(start here)_

The Montreal Exchange website offers free historical daily summaries for its options and futures, downloadable as CSV in six-month chunks for personal use. If the option files include bid, ask, settlement price, volume and open interest, you have months of history today and the capture becomes a routine download. Confirm the option columns in a browser before relying on them.

- **Done when:** Six months of RY and XIU option summaries downloaded, with the column list and the earliest available date written into the repo README. XIU has to come down in pieces of about two months, because the site limits the size of one file.
- **Depends on:** nothing

### SRC. Set up the ongoing capture

Each website download holds up to six months, so downloading by hand every week or two keeps the history complete. Use Interactive Brokers Canada if the website files fall short, or once you want automation: Montreal Exchange Level I data costs CAD 6 a month for non-professionals, and its Python interface can save chains on a schedule. Don't script the website itself, because its terms forbid retrieving content without permission.

- **Done when:** Raw files saved under `data/raw/` by date. A written routine (manual download, or an IBKR script on a schedule) and a capture log listing any missed days.
- **Depends on:** CAP

### LIC. Keep raw exchange data out of public view  _(licence)_

Montreal Exchange data is licensed for personal, non-commercial use, and publishing or distributing it requires their permission. Market data bought through Interactive Brokers and OptionMetrics data from WRDS carry their own restrictions. The public repo and the live page show your code and the numbers you calculate. They never show the exchange's raw prices.

- **Done when:** `data/` listed in `.gitignore` before the first commit. The live page shows computed results only: errors in vol points, residuals, statistics. The offline demo runs from your laptop. Optional: email TMX to ask permission to show data in a non-commercial educational project.
- **Depends on:** nothing

### WRDS. Check what WRDS OptionMetrics gives you

Your Northwestern WRDS access includes OptionMetrics US but not Canada. The US data covers the New York listings of RY, TD, BNS, BMO, CM, CNQ and SU, with OptionMetrics' own implied volatility for each option, which makes it the independent check in B8. Find the table names and how current the data is before planning around it.

- **Done when:** Library and table names recorded, the latest available date noted, security IDs found for the seven New York listings, and EWC's presence checked.
- **Depends on:** nothing

## Week 1: Data spine

Statistics are only as good as the series under them. A correlation or hedge ratio estimated on misaligned dates is wrong, and nothing downstream repairs it.

### A1. Trading-calendar alignment

TSX and NYSE close on different holidays: Family Day, Victoria Day, Canada Day, the Civic Holiday and Canadian Thanksgiving close Toronto while New York trades. A naive join on date inserts zero returns on one side, which pulls every correlation and hedge ratio toward zero.

The same module owns the one rule for turning dates into the times the pricer uses. Valuation is at the close, and both time to expiry and time to each ex-dividend date are calendar days divided by 365. Doing this two ways in two places is how a one-day error gets in. Don't name the file `calendar.py`: with `src/` on the import path it would hide Python's own `calendar` module.

- **Done when:** A calendar module (`src/tradingcal.py`) returning the shared trading days for any list of instruments; a test that the August Civic Holiday and February Family Day are excluded; a report of stale days per series. One year-fraction function used by every caller, with a test. The SXO final-settlement rule (opening or closing index level on expiry day, and the last trading day) read from the contract specification in a browser and written into the README, with SXO's time to expiry following it.
- **Depends on:** –

### A2. Closing times and interlisted continuation

TSX closes at 16:00 ET, while the Canadian dollar and crude oil trade around the clock, and the interlisted names keep trading in New York after Toronto closes. Comparing prices stamped at different times measures the timing gap and passes it off as a relationship.

The same problem sits inside the option files. Each file carries a row for the underlying, but its bid and ask are about 20 basis points apart and its last trade sits a median 4 to 5 basis points from their midpoint, so it does not pin the share price at the moment the options were quoted. Ten basis points of error in the share price is about one vol point of call-put gap at one month.

The fix is to take the share price from the option quotes themselves. A call and a put at the same strike must imply the same vol; too high a share price makes every call look cheap and every put dear, and too low does the reverse. The price at which they agree, on the nearest expiry and the strikes closest to the money, is the price the option market was quoting against, at the same instant as the quotes. It really pins the forward for that expiry, so it is used on the nearest one, where a wrong rate matters least, and the carry assumptions are then tested on the later expiries.

- **Done when:** The share price for inversion implied from the nearest monthly expiry's five nearest-the-money call-put pairs (weeklies are too wide to calibrate on) (`voleng.implied_spot_american`), refused with a reason when it can't be found. Its distance from the file's last and midpoint, and from an independent official close, reported in basis points per day. The call-put gap on the expiry used for this marked as zero by construction and left out of B5's evidence. All factor series resampled to one 16:00 ET stamp, with the convention documented; New York trading after 16:00 kept as a separate series for the dual-listed names; the change in correlation before and after shown.
- **Depends on:** A1

### A3. A position format that holds a real book

A format that only knows listed options fails the moment someone adds a futures hedge. It should accept an interviewer typing “short 200 SXF against a bank basket”.

- **Done when:** Types: listed option, index future, ETF, equity. Fields for underlying, call or put, strike, expiry, quantity, multiplier and venue. Upload with readable error messages, and two sample books in the repo.
- **Depends on:** –

### A4. Dividend schedule for the bank and energy names

The early-exercise logic depends on ex-dividend dates and cash amounts. One date off by a day puts the call exercise point in the wrong place, which is exactly what you claim to handle correctly.

- **Done when:** Ex-dividend dates and amounts covering every live expiry, checked against two sources (OptionMetrics' distribution table is one for the New York listings), with a test that a known past ex-date lands on the right trading day. Dividend times follow the engine's rule: an ex-date on the expiry date counts, an ex-date on the valuation date does not. ETF distributions (XIU quarterly, ZEB monthly) are included with the amounts actually paid, since they vary.
- **Depends on:** A1

### A5. Offline snapshot, caching and a fixed random seed

Demos happen on bad WiFi. The app has to run without a network, and no number may change between two reloads in front of the same person.

- **Done when:** Cached fetches; a Live/Snapshot switch that reads the local data folder; a global seed; under three seconds to load from the snapshot. The snapshot stays on your laptop, outside the repo.
- **Depends on:** SRC, LIC, A3

### A6. A rate for each expiry

An option's forward grows at the interest rate until expiry, so the rate has to match the expiry. The overnight rate used for every expiry left a call-minus-put gap on the first RY day that grew with maturity to 1.1 vol points at 319 days, the same at every strike. Reading the rate off the Bank of Canada curve by time to expiry cut that to 0.3.

- **Done when:** A curve from CORRA and the 1, 3, 6 and 12 month Government of Canada T-bill yields, converted to continuous rates and interpolated by time to expiry (`src/rates.py`, done). Held flat beyond one year and labelled so. Days when the bond market is shut carry the last observation and say so. The call-put gap that remains at long expiries reported as a financing spread over T-bills, not tuned away.
- **Depends on:** A1

## Week 2: Mark

This is where the project gets checked against the market. Everything here supports one chart and makes it trustworthy, which mostly means being able to say what you excluded and why.

### B1. Wire in the pricer and inverter  _(already built)_

Binomial pricing for American options with discrete cash dividends, Black-Scholes for SXO, implied volatility by bisection with the vega screen and the lattice floor, and quote screening with reason codes. Twenty-six tests pass. Building your own matters here: the Montreal Exchange's own implied volatility uses Black-Scholes on American options, and its page says that overstates the result.

- **Done when:** voleng.py behind the app's pricing functions, with its tests running in CI on every push.
- **Depends on:** A3, A4

### B2. Tag which settlements came from trades  _(the trap)_

Exchanges don't settle every option off a trade. Options that didn't trade get a settlement price the exchange calculates from a model, so comparing your model with those prices compares two models.

A series with no trade is not always without market information. In the first files only 15% of RY rows and 4% of XIU rows traded, but 93% carried a two-sided quote, and a quote is a price someone would deal at. So there are three kinds of evidence, and the price to invert is the quote midpoint wherever a two-sided quote exists, because it is the market's number and brings its own error bar, the spread. The settlement is used only where there is no quote.

- **Done when:** Every point tagged as traded (volume above zero), quoted (no trade, two-sided quote) or settlement-only (exchange-calculated), and drawn differently, with the count of each beside the chart. The inverted price is the midpoint where quoted and the settlement otherwise, and each point says which. If the settlement-only points come out smooth and close to zero everywhere, report that as a finding: you are matching a model. The share of settlements that fall outside the closing quote reported, since it measures how far apart in time the two are; each row carries the flag (`settle_in_quote`), a row is inverted from its midpoint or its settlement and never a blend, and the share price paired with a midpoint is the one implied from the quotes (A2).
- **Depends on:** B1, CAP

### B3. The error surface  _(front chart)_

A disagreement needs two numbers, and inverting a market price gives you only one. Use four references, strongest last: the call against the put at the same strike (B5), each strike against a smooth curve fitted through its neighbours, your engine against the exchange's own published implied vol, and your engine against OptionMetrics (B8). The exchange's number comes free in the same file. It uses Black-Scholes on American options, so the gap should have a sign you can predict from early-exercise value and dividends; a gap with the wrong sign means an input is wrong, most likely yours.

A difference only means something if it is bigger than what the market itself can't resolve. That limit is the bid-ask spread, not the tree: a 5-cent-wide market on a one-month at-the-money option is about 0.4 vol points wide, ten times the solver's rounding. Input errors are the same size: spot stale by 10 basis points or a dividend amount off by 5% each move the call-put gap by 0.3 to 1 vol point.

Wide quotes don't make the chart useless, they change what it can show. One day's difference inside the spread proves nothing. The same difference on the same strike region day after day does: noise from a wide quote averages away across days, a real disagreement does not. So the chart has a second reading that pools days.

- **Done when:** For each moneyness and expiry bucket, the mean difference across days with its standard error, and the count of days behind it; a bucket is called a disagreement only when the mean is more than two standard errors from zero. A view restricted to the tightest third of quotes by spread in vol points. Your implied vol minus the exchange's, by moneyness and expiry, with the expected sign stated before the chart is drawn. Differences in vol points by expiry and moneyness for strikes that pass the screen, and the same differences divided by the quote's half-spread in vol points (the half-spread in dollars divided by vega; checked against inverting the bid and the ask). Three bars kept apart on the chart: the standard error of the mean, which decides whether an input is wrong; the half-spread, which decides whether anyone could trade on it; and the solver's 0.04 vol points at 400 steps beneath both. A table of how far a stated error in spot, rate and dividend moves the result. A smile fitted per expiry, weighted by vega, starting with a quadratic in log-moneyness; the fit's own error reported per expiry, and any expiry where the curve misses by more than the half-spread flagged as a poor fit instead of read as a disagreement.
- **Depends on:** B1, B2, B5

### B4. Rejection tally

A chart that hides how many quotes it dropped isn't a measurement. Deep in-the-money American options on the exercise boundary carry no volatility information, and a solver returns a confident wrong number for them unless you screen them out. Five of forty-two synthetic quotes hit this.

The vega screen is a floor set at one cent, the smallest tick. It removes quotes with no information; it doesn't say the rest are precise. Many series tick in five cents (one cent only below $0.50, or below $3.00 in the penny program), so one tick can be worth more than a vol point on a cheap option. That shows up in B3's half-spread, not here.

- **Done when:** Counts by reason shown next to the surface: no activity, crossed market, spread too wide, below intrinsic value, no solution, vega too small. The tick rule and each class's penny-program status confirmed against the current exchange rules and recorded.
- **Depends on:** B3

### B5. Call against put at the same strike

Calls and puts at the same strike and expiry should imply the same volatility. A steady gap across strikes means your dividend or financing assumption is off, because real skew doesn't produce that pattern.

- **Done when:** Both sides inverted with the same carry assumptions, with call minus put plotted by strike for each expiry, each expiry's mean drawn with its standard error (scatter across strikes on one day, scatter across days when pooled), the quote bar (the two half-spreads in vol points) stated beside it, and the expiry the share price was calibrated on left out of the pooled test. First full run, RY, 124 days: monthlies beyond 60 days sit at +0.25 vol points with a standard error of 0.02, weeklies under 30 days at about -0.4. Both are inside the quote bar and outside the standard error. Explaining them is the open part of this item.
- **Depends on:** B1

### B6. Carry over the stress horizon

A five-day stress includes five days of time decay and funding, and an ex-dividend date inside the window moves the forward price. Shocking prices instantly makes a long-option book look better than it is.

- **Done when:** Scenario P&L computed as the shocked revaluation at the end of the horizon minus today's revaluation, with a horizon control and time decay, funding and dividends shown separately from the shock.
- **Depends on:** B1, A4

### B7. Invariant tests in CI  _(already built)_

Each one is cheap and easy to explain, and they catch sign errors before a demo does.

- **Done when:** Put-call parity; analytic Greeks against bumped repricing (done, with American delta and gamma read off the tree because a bumped gamma was wrong by a factor of three); zero volatility gives intrinsic value; ES at least VaR; American worth at least European; an empty book gives exactly zero. Passing on every push.
- **Depends on:** B1

### B8. Check your engine against OptionMetrics  _(second model)_

The last limitation on this sheet used to read “no independent model to check against”. OptionMetrics computes implied volatility for every US-listed option using a binomial tree with discrete dividends, the same method as yours. Feed your engine their bid-ask midpoint, dividends and interest rates, and any remaining difference comes from method alone.

- **Done when:** For the seven New York listings over at least one month: the distribution of your implied vol minus theirs in vol points, by moneyness and days to expiry, with the largest gaps explained. Statistics and charts published, never their rows. The IvyDB version recorded.
- **Depends on:** B1, WRDS

### B9. Realized against implied volatility  _(the trading view)_

Implied vol is the price of an option quoted as a volatility. Realized vol is how much the share then actually moved. Implied above what followed means the option was expensive, and that is a view a trader can argue with. The comparison has to look forward: implied on day t against realized from t to expiry. Comparing with trailing realized is the usual mistake, because it matches a forecast with the past.

- **Done when:** For the liquid names, at-the-money implied vol for the nearest monthly expiry against the realized vol over that same window (close to close, on the shared calendar, ex-dividend days adjusted). The gap's history and distribution, with the number of non-overlapping windows stated beside it, since six months gives about six. The 25-delta risk reversal and butterfly. The P&L of holding one at-the-money straddle to expiry with a daily delta hedge, entered at the quoted bid or ask and not the mid, shown per expiry as what happened and not as a strategy.
- **Depends on:** B3, A1, A4

## Week 3: Risk, hedge, ship

The closing chart and the limitations page. Ship at the end of this week in whatever state it's in, because a rough live link beats a polished one a month later.

### C1. Volatility response control

A 20% drop in the share price with implied volatility unchanged has never happened. Holding volatility flat is the biggest single error in student stress tests.

- **Done when:** A selectable rule (sticky strike, sticky delta, sticky moneyness or a custom spot-vol beta), with at-the-money vol moved by beta times the return and skew steepening in crash cases. Exposed as a control the reader can change.
- **Depends on:** B3

### C2. Bucket the vega, map the delta

One portfolio vega number adds one-month volatility on one name to one-year volatility on another as if they were the same risk. Most Montreal single names have no populated surface, so the buckets have to match the market that exists.

- **Done when:** Vega on a maturity-by-moneyness grid only where the surface is populated, with names without one marked delta-only. Delta mapped to the TSX 60 by beta for totals, with per-name deltas kept.
- **Depends on:** B3

### C3. Historical replay  _(label the marks)_

Made-up shocks are numbers someone typed, so replay real dated moves applied together. Where Montreal history reaches a replay date, use real settlements. Before that, reprice the option leg with your model on historical share prices and label it as model-priced. The model needs a volatility for each of those days, and a volatility produced by the C1 rule is a number someone typed. For the names listed in New York, OptionMetrics has the implied vols the market actually showed on those dates, so use them, with the currency effect removed (the formula is in Deferred item 2). The C1 rule fills in only where no observed vol exists.

- **Done when:** Joint moves for February to March 2020, April 2020 negative WTI, the 2022–23 Bank of Canada cycle and January 2015, plus the WCS–WTI spread as its own factor with at least one case of WTI flat and the spread widening. Every replay day carries one of three labels: real settlement, model price on an observed New York vol, or model price on a rule-based vol. The page shows the count of each.
- **Depends on:** A1, A2, B6, C1, WRDS

### C4. Hedge and residual  _(closing chart)_

What's left after a hedge you could actually put on is the question a desk asks, so it's the last thing they see.

Two things keep this chart honest. A hedge ratio fitted on the same days it is judged on always looks better than it would have been, so each day's ratio comes only from data before that day. And a futures hedge removes delta only, so on an options book most of what's left is gamma and vega, which would be misread as sector risk unless it is shown separately.

- **Done when:** Short SXF, plus ZEB where the book is bank-heavy, applied to the historical book P&L. Hedge ratios estimated on a rolling window that ends the day before they are applied, with the in-sample result shown beside it so the gap is visible. Residual split into the delta part the hedge missed and the option part (gamma and vega) it could not reach, which C4b takes on. The delta part shown against the TSX 60 and the sector ETF, by name and by sector, with minimum-variance and beta hedge ratios compared.
- **Depends on:** C2, C3

### C4b. Vega hedge with listed options

Futures can't hedge volatility. A book that is long options loses when implied vol falls even if the share price never moves, and the only thing that offsets that is another option. A desk sells or buys index or ETF options against it, then cleans up the delta those options bring with the future.

- **Done when:** A fixed, stated rule and not a search: near-the-money XIU options (SXO as the alternative) in the maturity bucket where the book's vega sits, sized to bring that bucket's beta-weighted vega to zero, with SXF then re-sized for the delta the hedge adds. Hedge options marked at real settlements and charged half the quoted spread on entry and exit; any day marked from the model labelled. The closing chart shows three stages side by side: unhedged, delta-hedged, delta and vega hedged. What still remains (gamma, single-name vol moving differently from index vol) is named on the chart.
- **Depends on:** C2, C4, B3

### C5. Component ES

Which position drives the worst days is what a risk head asks. Euler allocation splits tail risk across positions so the pieces add up to the total.

- **Done when:** Marginal and component ES per position, summing to the total, ranked, on the real universe.
- **Depends on:** C4b

### C6. Interlisted volume panel  _(buy-side door)_

The same company trades in Toronto and New York with different liquidity, and choosing where to work an order is a real decision. This panel makes the project relevant to pension and asset-manager trading desks as well as risk teams.

- **Done when:** Daily volume by venue for the dual-listed names, with New York trading after 16:00 shown. Intraday time buckets only if your data source provides intraday bars. Optional: days to liquidate each position against average daily volume.
- **Depends on:** A2, C4

### C7. Fund tab  _(second click)_

Gives a risk or pension interviewer a page of their own: same book, same prices, one click away.

- **Done when:** Historical VaR and 97.5% ES on the marked P&L, with exception dates, the Kupiec test and the Basel traffic light. Never the page the demo opens on.
- **Depends on:** C4

### C8. P&L explain and limits

Desks split daily P&L into its sources and watch what's left unexplained.

- **Done when:** Attribution to delta, gamma, vega, theta, rho and residual, from the mark history the capture has built up. Limits on mapped delta, bucketed vega and ES, with usage bars.
- **Depends on:** SRC, C2

### C9. Ship it

Nobody clones a repo. A reader spends a few seconds, and a live link is what those seconds need to contain.

- **Done when:** Limitations page, a four-to-six page methodology PDF, the app deployed showing computed results only, a ninety-second walkthrough following the demo order, the live URL on your resume, and a commit history that shows the work in sequence.
- **Depends on:** everything

## Deferred (add later, in this order)

These wait until both charts exist and the project is live.

1. **Spot and volatility grid.** P&L across a grid of share-price and volatility moves. It's the standard daily desk view and costs almost nothing once full revaluation works.
2. **Same company, two markets.** Your Montreal implied volatility against OptionMetrics' New York implied volatility for the same bank. The New York option is priced in US dollars, so remove the currency effect first: σ²(USD) = σ²(CAD) + σ²(FX) + 2ρ·σ(CAD)·σ(FX). Then explain what's left.
3. **Reverse stress testing.** The smallest plausible joint move that costs a set amount, solved against full revaluation from several starting points. Show the count of similar historical days first and the Mahalanobis distance second, with a caveat.
4. **Bootstrap intervals on VaR and ES.** A 99% VaR from 250 days rests on about two and a half bad days. Correct and cheap, but it changes nothing in the demo.
5. **The morning note.** Realized against implied (B9) feeds it. Top risks by component ES, overnight attribution, limit usage and what changed. It depends on C5, C7 and C8.

**The gate:** nothing from the out-of-scope list comes back until the design or the data changes, meaning a second production system or prices you could actually trade at.

## Out of scope

- The FRTB P&L attribution traffic light, which compares two separate production systems
- Implied borrow bands and the joint volatility-borrow solver
- FRTB liquidity horizons as a capital classification
- A hedge recommender ranked on theoretical prices
- Revenue-share FX as a pricing input
- The Bank of Canada to loan-loss provision to bank-volatility chain
- Shrinkage and matrix repair unless an eigenvalue check fails
- Order book depth, queue position and size at the touch
- A volatility surface on names that don't trade
- Raw exchange prices in the public repo or on the live page
- A second repo for any new audience. New audiences get a tab or a panel

## Known limitations (these become the limitations page)

- Model value is not a market price
- Untraded options settle at prices the exchange calculates, so comparing against them is model against model
- Last-trade implied vols are not mid-market
- Most series don't trade on a given day; the chart rests on quote midpoints, and a midpoint is not a price anyone dealt at
- The share price at the moment the options were quoted is known only to within several basis points, which is around half a vol point at one month
- Quotes on these options are wide (a median near 50 cents on RY), so most differences will sit inside the spread
- Deep in-the-money American options carry no recoverable implied vol, so screen on vega
- The solver's noise floor is 0.04 vol points at 400 steps; smaller differences are rounding
- The bid-ask spread is a far higher floor, often 0.4 to 2 vol points; a difference inside it is not a disagreement
- A fitted quadratic smile misses short-dated skew, so wing residuals there are partly fit error
- The vega hedge uses index or ETF options, so gamma and single-name vol that moves apart from index vol stay in the residual
- Realized-against-implied rests on a handful of non-overlapping windows; it is a description, not a tested strategy
- Time is calendar days over 365, so weekends carry the same variance as trading days
- Borrow cost acts like a dividend yield and creates early-exercise value in calls, so one call-put pair can't separate financing from volatility
- The exchange's website files are not its official end-of-day data
- The exchange's own published implied vols use Black-Scholes on American options, which it says overstates them
- Replay option prices are model prices wherever exchange history doesn't reach
- Historical moves say nothing about today's liquidity
- No borrow, no specials, no implied-versus-scheduled dividend
- Rates are government T-bill yields, below what a dealer funds at; they are day averages, not closing levels, and flat beyond one year
- No depth or size at the touch, so exit paths aren't modelled
- Your engine is not the exchange's model
- The OptionMetrics check covers the New York listings, not the Montreal ones

## Demo order (ninety seconds)

1. **Load the book:** two sample books ship with the repo, and an interviewer can paste in their own
2. **Error surface:** Montreal options against their references, with the rejection tally and the traded-versus-calculated split visible
3. **Engine check:** your implied vols against OptionMetrics on the New York listings, in vol points
4. **Call against put:** a flat gap across strikes means the carry assumptions hold
5. **Shock RY through its dividend week:** full revaluation, a selectable volatility rule, carry over the horizon
6. **Hedge with SXF and ZEB, then the vega hedge:** minimum-variance against beta ratio, out of sample
7. **What's left:** the residual by name and by sector, and stop there

If a week slips, cut in this order: the fund tab goes entirely, the interlisted volume panel goes, the replay loses scenarios, the vega grid becomes delta-only. Never cut the error surface, realized against implied, the hedge residual or the limitations page.
