# Build sheet

The plan for this repo. Each item has a reason, a definition of done and its dependencies.
Work roughly in phase order. Nothing outside this sheet gets built without a deliberate decision.

**The product is two charts.** Front: implied vol on Montreal options against three references (call vs put at the same strike, a fitted smile, OptionMetrics), in vol points by expiry and moneyness, with a count of everything excluded and why. Closing: residual P&L after short SXF and ZEB, by name and by sector. The demo opens on the first and closes on the second.

## Data stack

| Source | Used for | Cost | Licence |
|---|---|---|---|
| Montreal Exchange website (historical daily summaries) | Option history and the ongoing capture | Free | Personal, non-commercial use. Publishing needs their permission |
| Interactive Brokers Canada | Automated capture, live Montreal chains, intraday data | Montreal Exchange Level I: CAD 6/month. Canadian Stock Bundle Level I: CAD 13.50/month | Exchange data for your own use. Read their agreement |
| WRDS OptionMetrics US | Independent check on the New York listings (B8) | Through Northwestern | No redistribution. Publish your own statistics only |
| Python and voleng.py | Pricing, implied volatility, risk | Free | Yours |
| TradingView (free plan) | Watching markets; not a data source for the project | Free. US real-time: $3 per exchange or $9.95/month bundle | For viewing |

## Start: Get the data

Four items that decide what data you have and what you may do with it. Finish these before writing application code.

### CAP. Download Montreal Exchange option history  _(start here)_

The Montreal Exchange website offers free historical daily summaries for its options and futures, downloadable as CSV in six-month chunks for personal use. If the option files include bid, ask, settlement price, volume and open interest, you have months of history today and the capture becomes a routine download. Confirm the option columns in a browser before relying on them.

- **Done when:** Six months of RY and XIU option summaries downloaded, with the column list and the earliest available date written into the repo README.
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

- **Done when:** A calendar module returning the shared trading days for any list of instruments; a test that the August Civic Holiday and February Family Day are excluded; a report of stale days per series.
- **Depends on:** –

### A2. Closing times and interlisted continuation

TSX closes at 16:00 ET, while the Canadian dollar and crude oil trade around the clock, and the interlisted names keep trading in New York after Toronto closes. Comparing prices stamped at different times measures the timing gap and passes it off as a relationship.

- **Done when:** All factor series resampled to one 16:00 ET stamp, with the convention documented; New York trading after 16:00 kept as a separate series for the dual-listed names; the change in correlation before and after shown.
- **Depends on:** A1

### A3. A position format that holds a real book

A format that only knows listed options fails the moment someone adds a futures hedge. It should accept an interviewer typing “short 200 SXF against a bank basket”.

- **Done when:** Types: listed option, index future, ETF, equity. Fields for underlying, call or put, strike, expiry, quantity, multiplier and venue. Upload with readable error messages, and two sample books in the repo.
- **Depends on:** –

### A4. Dividend schedule for the bank and energy names

The early-exercise logic depends on ex-dividend dates and cash amounts. One date off by a day puts the call exercise point in the wrong place, which is exactly what you claim to handle correctly.

- **Done when:** Ex-dividend dates and amounts covering every live expiry, checked against two sources (OptionMetrics' distribution table is one for the New York listings), with a test that a known past ex-date lands on the right trading day.
- **Depends on:** A1

### A5. Offline snapshot, caching and a fixed random seed

Demos happen on bad WiFi. The app has to run without a network, and no number may change between two reloads in front of the same person.

- **Done when:** Cached fetches; a Live/Snapshot switch that reads the local data folder; a global seed; under three seconds to load from the snapshot. The snapshot stays on your laptop, outside the repo.
- **Depends on:** SRC, LIC, A3

## Week 2: Mark

This is where the project gets checked against the market. Everything here supports one chart and makes it trustworthy, which mostly means being able to say what you excluded and why.

### B1. Wire in the pricer and inverter  _(already built)_

Binomial pricing for American options with discrete cash dividends, Black-Scholes for SXO, implied volatility by bisection with the vega screen and the lattice floor, and quote screening with reason codes. Eighteen tests pass. Building your own matters here: the Montreal Exchange's own implied volatility uses Black-Scholes on American options, and its page says that overstates the result.

- **Done when:** voleng.py behind the app's pricing functions, with its tests running in CI on every push.
- **Depends on:** A3, A4

### B2. Tag which settlements came from trades  _(the trap)_

Exchanges don't settle every option off a trade. Options that didn't trade get a settlement price the exchange calculates from a model, so comparing your model with those prices compares two models.

- **Done when:** Every point tagged as traded (volume above zero) or exchange-calculated, and drawn differently. If the surface comes out smooth and close to zero everywhere, report that as a finding: you are matching a model.
- **Depends on:** B1, CAP

### B3. The error surface  _(front chart)_

A disagreement needs two numbers, and inverting a market price gives you only one. Use three references, strongest last: the call against the put at the same strike (B5), each strike against a smooth curve fitted through its neighbours, and your engine against OptionMetrics (B8).

- **Done when:** Differences in vol points by expiry and moneyness for strikes that pass the screen. A smile fitted per expiry (a quadratic in log-moneyness is enough to start). The noise floor shown on the chart: 0.04 vol points at 400 steps, so anything smaller is your own rounding.
- **Depends on:** B1, B2, B5

### B4. Rejection tally

A chart that hides how many quotes it dropped isn't a measurement. Deep in-the-money American options on the exercise boundary carry no volatility information, and a solver returns a confident wrong number for them unless you screen them out. Five of forty-two synthetic quotes hit this.

- **Done when:** Counts by reason shown next to the surface: no activity, crossed market, spread too wide, below intrinsic value, no solution, vega too small.
- **Depends on:** B3

### B5. Call against put at the same strike

Calls and puts at the same strike and expiry should imply the same volatility. A steady gap across strikes means your dividend or financing assumption is off, because real skew doesn't produce that pattern.

- **Done when:** Both sides inverted with the same carry assumptions, with call minus put plotted by strike for each expiry.
- **Depends on:** B1

### B6. Carry over the stress horizon

A five-day stress includes five days of time decay and funding, and an ex-dividend date inside the window moves the forward price. Shocking prices instantly makes a long-option book look better than it is.

- **Done when:** Scenario P&L computed as the shocked revaluation at the end of the horizon minus today's revaluation, with a horizon control and time decay, funding and dividends shown separately from the shock.
- **Depends on:** B1, A4

### B7. Invariant tests in CI  _(already built)_

Each one is cheap and easy to explain, and they catch sign errors before a demo does.

- **Done when:** Put-call parity; analytic Greeks against bumped repricing; zero volatility gives intrinsic value; ES at least VaR; American worth at least European; an empty book gives exactly zero. Passing on every push.
- **Depends on:** B1

### B8. Check your engine against OptionMetrics  _(second model)_

The last limitation on this sheet used to read “no independent model to check against”. OptionMetrics computes implied volatility for every US-listed option using a binomial tree with discrete dividends, the same method as yours. Feed your engine their bid-ask midpoint, dividends and interest rates, and any remaining difference comes from method alone.

- **Done when:** For the seven New York listings over at least one month: the distribution of your implied vol minus theirs in vol points, by moneyness and days to expiry, with the largest gaps explained. Statistics and charts published, never their rows. The IvyDB version recorded.
- **Depends on:** B1, WRDS

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

Made-up shocks are numbers someone typed, so replay real dated moves applied together. Where Montreal history reaches a replay date, use real settlements. Before that, reprice the option leg with your model on historical share prices and label it as model-priced.

- **Done when:** Joint moves for February to March 2020, April 2020 negative WTI, the 2022–23 Bank of Canada cycle and January 2015, plus the WCS–WTI spread as its own factor with at least one case of WTI flat and the spread widening. The page says which days use real settlements and which use model prices.
- **Depends on:** A1, A2, B6, C1

### C4. Hedge and residual  _(closing chart)_

What's left after a hedge you could actually put on is the question a desk asks, so it's the last thing they see.

- **Done when:** Short SXF, plus ZEB where the book is bank-heavy, applied to the historical book P&L. Residual shown against the TSX 60 and the sector ETF, by name and by sector, with minimum-variance and beta hedge ratios compared.
- **Depends on:** C2, C3

### C5. Component ES

Which position drives the worst days is what a risk head asks. Euler allocation splits tail risk across positions so the pieces add up to the total.

- **Done when:** Marginal and component ES per position, summing to the total, ranked, on the real universe.
- **Depends on:** C4

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

1. **Realized versus implied volatility, and skew summaries.** Implied above realized means options look expensive, and that's a view a trader can argue with. Add the 25-delta risk reversal and butterfly on the liquid names. OptionMetrics has realized volatility and surface tables for the New York listings; use your own data for Montreal. These numbers feed the monthly note.
2. **Spot and volatility grid.** P&L across a grid of share-price and volatility moves. It's the standard daily desk view and costs almost nothing once full revaluation works.
3. **Same company, two markets.** Your Montreal implied volatility against OptionMetrics' New York implied volatility for the same bank. The New York option is priced in US dollars, so remove the currency effect first: σ²(USD) = σ²(CAD) + σ²(FX) + 2ρ·σ(CAD)·σ(FX). Then explain what's left.
4. **Reverse stress testing.** The smallest plausible joint move that costs a set amount, solved against full revaluation from several starting points. Show the count of similar historical days first and the Mahalanobis distance second, with a caveat.
5. **Bootstrap intervals on VaR and ES.** A 99% VaR from 250 days rests on about two and a half bad days. Correct and cheap, but it changes nothing in the demo.
6. **The morning note.** Top risks by component ES, overnight attribution, limit usage and what changed. It depends on C5, C7 and C8.

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
- Deep in-the-money American options carry no recoverable implied vol, so screen on vega
- The solver's noise floor is 0.04 vol points at 400 steps; smaller differences are rounding
- Borrow cost acts like a dividend yield and creates early-exercise value in calls, so one call-put pair can't separate financing from volatility
- The exchange's website files are not its official end-of-day data
- The exchange's own published implied vols use Black-Scholes on American options, which it says overstates them
- Replay option prices are model prices wherever exchange history doesn't reach
- Historical moves say nothing about today's liquidity
- No borrow, no specials, no implied-versus-scheduled dividend
- No depth or size at the touch, so exit paths aren't modelled
- Your engine is not the exchange's model
- The OptionMetrics check covers the New York listings, not the Montreal ones

## Demo order (ninety seconds)

1. **Load the book:** two sample books ship with the repo, and an interviewer can paste in their own
2. **Error surface:** Montreal options against their references, with the rejection tally and the traded-versus-calculated split visible
3. **Engine check:** your implied vols against OptionMetrics on the New York listings, in vol points
4. **Call against put:** a flat gap across strikes means the carry assumptions hold
5. **Shock RY through its dividend week:** full revaluation, a selectable volatility rule, carry over the horizon
6. **Hedge with SXF and ZEB:** minimum-variance against beta ratio
7. **What's left:** the residual by name and by sector, and stop there

If a week slips, cut in this order: the fund tab loses Kupiec, the replay loses scenarios, the vega grid becomes delta-only. Never cut the error surface, the hedge residual or the limitations page.
