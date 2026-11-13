# Investigations: what each flag turned out to be

A check that flags something is only useful if someone follows it to a cause. This
is the record of every flag in this project that was followed, in the order found,
and who turned out to be at fault: the market's price, an input to the model, or
the tool itself. All on Montreal Exchange options, May to November 2026.

The planted-error benchmarks (`reports/mark_benchmark.txt`,
`reports/review_benchmark.txt`) measure the checks where the truth is known. This
file is the other half: real cases, where the truth had to be found.

## The count

| At fault | Cases |
|---|---|
| An input to the model (dividend date, amount, currency, timing, funding) | 7 |
| The tool (a check or a report that was wrong) | 5 |
| The data (a stale price in the file) | 1 |
| The market's quote | 0 |

Thirteen investigations and not one bad exchange quote. That is the result. On
liquid names with two-sided quotes, a disagreement between a call and a put, or
between a strike and its neighbours, has so far always been this project's own
input or code. A flag that says "the quote is wrong" would have been wrong every
time. This is why the review queue (`scripts/review_queue.py`) separates what was
observed from the likely cause, and why its first question is whether the whole
expiry disagrees or one strike does.

It is also a small sample from six calm months, on names chosen for liquidity. It
says nothing about illiquid series, where bad quotes are common.

## Inputs to the model

**1. Royal Bank weeklies after an ex-dividend date (found 10 November).**
- Flagged by: call minus put, by expiry.
- Observed: -0.63 vol points on the weekly expiring four days after each of two
  ex-dates, fading over the next two expiries, and nowhere else.
- Cause: the dividend was placed at the close of the ex-date, counting calendar
  days. The share opens lower on the ex-date and prices move in sessions.
- Changed: the drop is placed by trading sessions before the ex-date's open
  (hard rule 5). Those expiries went to +0.15 and +0.08.

**2. Option forwards above the overnight rate (10 November).**
- Flagged by: call minus put growing with maturity.
- Observed: +0.16 on monthlies over 60 days on Royal Bank, on declared dividends.
- Cause: no funding cost in the carry. Options imply about 18 basis points a year
  over CORRA futures on Royal Bank, about 30 across names.
- Changed: a spread fitted from earlier days only. Not independent of the check
  that found it, which is stated wherever it is used.

**3. XIU's November distribution on the wrong date (11 November).**
- Flagged by: call minus put on one expiry.
- Observed: only the 20 November expiry showed a gap, 34 basis points of forward
  price; the share price implied from options sat 17 basis points off the tape.
- Cause: the date was last year's, 19 November. The fund's published schedule
  gives 25 November, on the other side of that expiry.
- Changed: the date. The implied share price then sat 1 basis point from the tape.

**4. Royal Bank's projected raise (11 November).**
- Flagged by: call minus put on expiries resting on projected dividends.
- Observed: +0.07 with the raise rule, -0.05 with no raise.
- Cause: the market prices about half the raise the rule projects.
- Changed: nothing. The rule stays the input; the implied amount and analysts'
  consensus are shown beside it. Taking the dividend from the options would make
  calls and puts agree by construction.

**5. Five names with thinly measured funding (12 November).**
- Flagged by: call minus put on declared dividends: ENB +0.24, XEG +0.81,
  CNQ -0.35, MFC +0.29, CNR -0.11.
- Cause, for ENB, XEG and half of CNQ: each name's own spread rested on too few
  expiries. A name now uses its own only where it is well measured, and the
  median across names otherwise.
- Still open: CNR (its spread drifts and the estimate lags; diagnosed, not
  changed), and the rest of CNQ.

**6. Manulife's November dividend on the wrong side of an expiry (12 November).**
- Flagged by: call minus put on one expiry, and by the book report: Manulife's
  option failed the pair check on 17 days.
- Observed: -1.24 on the 20 November expiry on 87 days; the implied share price
  27 basis points low from mid-October; every other expiry +1 to +3.
- Cause: the rule placed the dividend on 26 November. It went ex on the 20th, the
  expiry day. The share price was then being implied from that same expiry.
- Changed: an expiry within five sessions of an unannounced ex-date is not used
  to imply the share price and counts as resting on a projection. The gap went
  from -0.97 to -0.12 and the book's flags from 17 days to 3.
- The lesson for the queue: 14 of those 17 flags would have sent someone to
  re-mark an option whose mark was fine.

**7. Barrick's dividends in the wrong currency (12 November).**
- Flagged by: call minus put on projected dividends, -1.17, the largest in the
  project; and by the book report, where Barrick's option was flagged most often.
- Cause: Barrick declares in US dollars. The schedule held last year's amounts
  already converted to Canadian dollars, so it repeated last year's exchange rate
  and missed the higher base dividend.
- Changed: the schedule holds US dollars and is converted at each valuation day's
  rate. It also gained a dividend declared on 9 November that the file lacked.

## The tool

**8. A second bad quote hid the first (12 November).**
- Found by: the planted-quote benchmark, not by a real flag.
- Observed: the smile check missed one in six 4-point errors.
- Cause: the curve was fitted by least squares, so a second bad quote in the same
  expiry dragged it.
- Changed: a robust fit. 4-point errors caught went from 83% to 100%.

**9. One bad quote switched the smile check off (12 November).**
- Found by: the review-queue benchmark, where half the planted bad quotes came
  back with no smile evidence at all.
- Cause: fit quality was a root mean square. One quote 4 points out made the
  whole expiry a "poor fit" and dropped the smile check for every strike in it.
  The earlier benchmark did not apply that drop, so its figure for the smile
  check overstated what the book report did.
- Changed: fit quality is a median, and the benchmark uses the production filter.

**10. The uncertain-date rule set aside dates that were known (12 November).**
- Found by: the re-run after case 6. XIU lost its own funding spread and its gap
  changed sign; XEG went from +0.24 to +0.69.
- Cause: the rule treated every unannounced dividend date as a guess. BlackRock
  publishes its funds' ex-dates a year ahead.
- Changed: dates an issuer has published are recorded and treated as known. Both
  names returned to where they were.

**11. A futures position that was never valued (12 November).**
- Found by: building the hedge stage, when a total did not reconcile.
- Cause: the exchange file labels the index future TX60, not SXF, and the sample
  book had the last trading day where the file has the expiry. The book report
  skipped the position and did not say so.
- Changed: the label and the date, and a position that cannot be found is now an
  error, not a silence.

**12. Sample books struck where nothing trades (12 November).**
- Found by: the first book report, in which few positions could be checked.
- Cause: the books were written before the option files were on disk. Most
  strikes were 20 to 40% from the money and two were never listed.
- Changed: both books re-struck near the money.

## The data

**13. A stale last trade on 29 May 2026.**
- Flagged by: the implied share price against the file's last trade.
- Observed: 104 basis points apart, and 73 from the file's own midpoint.
- Cause: the file's last trade that day predates the close.
- Changed: nothing in the model. The day is flagged, and the comparison is
  reported with and without it.

## What this record does not show

- Time saved. Nobody timed a review with and without the queue. That needs a
  parallel trial against a desk's own process.
- Detection rates on real data. Only the planted benchmarks have a known truth.
- Every case here was investigated by the person who built the checks. Whether
  the queue's "next check" is the right first step for someone else is untested.
