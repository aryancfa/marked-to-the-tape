# Frozen test plan for the next month of data

Written and committed on 13 November 2026, before any exchange file dated after
10 November 2026 was downloaded. The commit date of this file is the evidence
that the plan came before the data.

## Why this exists

Every rule in this project was chosen while looking at the same six months
(8 May to 10 November 2026). A rule chosen after seeing the data it is judged on
will look better than it is. The pooled funding spread, the five-session window
around an unannounced ex-date, the poor-fit rule and the review thresholds were
all adopted after a failure in this sample. So nothing in the current reports is
a clean test of any of them.

The next month of files is the first data none of these rules has seen. This
note fixes, in advance, what will be run, what counts as a pass, and what will
be reported. Then the result cannot be shaped after the fact.

## What is frozen

Pricing and day pipeline: the code at commit `5d80469`. The pricing modules
listed in `scripts/run_days.py` (`PRICING_MODULES`) do not change before the
test is reported.

The settings, as they stand in the code today:

| Setting | Value | Where |
|---|---|---|
| Tree steps | 400 | `voleng` |
| Inversion noise floor | 0.04 vol points | `replay.NOISE_FLOOR` |
| Own funding spread is used when | 20 earlier estimates, standard error at most 5 bp a year | `surface.choose_spread` |
| Otherwise | median across the other names that qualify, earlier days only | `surface.choose_spread` |
| Window around an unannounced ex-date | 5 sessions | `surface.DATE_WINDOW` |
| Dividend projection | funds and ABX repeat last year; companies add last year's raise | `loaders.reference.project` |
| A mark fails a check when | the gap exceeds its quote's half-spread in vol points and the noise floor | `replay.check_mark` |
| Smile | SVI, robust fit, seven strikes; surface fit below that | `references` |
| Poor fit | by the median miss | `references.typical_miss` |
| Cause labels | other strikes needed 2, shared 0.5, together 0.5 | `review.MIN_OTHERS`, `SHARED`, `TOGETHER` |
| Delta-hedge ratios | 250 days before each day | `book_hedge.WINDOW` |
| Vega hedge | XIU straddle, re-struck at 5% | `book_hedge` |
| Day-ahead VaR | 99%, 250 days before each day | `tail_risk.WINDOW` |
| Books | `books/bank_heavy.csv`, `books/diversified.csv`, unchanged | |

Dividend schedules change only by adding announcements made after 10 November,
each with its true announcement date. A projection is never edited to match
what was later declared.

Code that only reports (new tables, new pages) may change. Code that prices,
chooses a spread, fits a smile or decides a flag may not.

## The test days

Every trading day after 10 November 2026 in the files downloaded next, for all
fifteen names. Spreads and hedge ratios for those days come from earlier days
only, as they do now.

## What will be measured, and what counts as a pass

About twenty days is a small sample. A pass means "not contradicted by a month
it had not seen". It does not mean proven. Each threshold below was set from the
in-sample figure and its scatter, with room for a month's noise.

**1. Declared-dividend gap (the funding rule).** For each name, the mean call
minus put gap on monthly expiries over 60 days out that rest on declared
dividends, weighted as now.
- Scored names: RY, TD, BNS, BMO, SU, ENB, BCE, SHOP, XIU. Pass: the mean is
  within 0.10 vol points of zero.
- CNQ and CNR are known misses (-0.17 and -0.11 in sample). Prediction: both
  stay negative. If either comes back inside 0.05, the in-sample miss was
  partly chance and the findings will say so.
- MFC, XEG, ABX, ZEB: too few declared-dividend expiries to score. Reported.

**2. The share price implied from the options.** For each name, the mean
distance from the file's last trade. Pass: within 8 basis points on every name.

**3. Does a name's spread predict itself.** The spread fitted on the new days
alone against the trailing spread that was applied to them. Pass: within two
standard errors of the difference, on at least 80% of the names that have a
fitted spread. This is the direct test of whether the spread is a stable
quantity or a number that soaks up whatever the month needs.

**4. Dividend projections against what companies announce.** The banks
announce in late November and early December. For every dividend announced in
the test month: the projected date against the actual (pass: within 5
sessions) and the projected amount against the actual (reported as a
percentage; no pass mark, because the raise rule is known to be rough). The
option-implied amounts already printed by `funding_split.py` will be scored
beside the rule's.

**5. Share of marks failing a check.** In sample 3.5% (bank-heavy) and 2.1%
(diversified) of checkable position-days. Pass: at most 7.0% and 4.2%, twice
the in-sample rate. The split of likely causes is reported, not scored.

**6. Delta hedge.** Share of daily variance removed, ratios from earlier days.
In sample 96% and 62%. Pass: at least 85% (bank-heavy) and at least 40%
(diversified).

**7. Vega hedge.** Prediction: no reduction in the vol part of P&L, as in
sample. If it does reduce it, that is reported as one month's result, and the
hedge is still not adopted on it.

**8. Day-ahead VaR.** Exceptions on the new days are added to the count and
Kupiec's test is re-run on the whole period. In sample the bank-heavy VaR was
too small (5 in 123). Prediction: it stays too small unless the 250-day window
has by then taken in more of the recent moves. Reported, not scored.

## What will be reported

One table: every item above, the in-sample figure, the new figure, pass or
fail. Every failure is listed, with no item dropped. The six-month figures in
the README stay as they are, labelled in-sample, and the new month is shown
beside them, never blended in.

If a bug is found while running the test, the result is reported both ways,
before and after the fix, with what the bug was.

No rule is changed in response to the test until after its report is
committed. A change after that starts the clock again: it needs its own
untouched month.
