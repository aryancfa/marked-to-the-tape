# Hermes project assessment — 15 November 2026

Review baseline: commit `49ac726`. This is the assessment requested by the user, handed to Claude before further project work.

## Mandatory handling before continuing

Read this entire assessment before project work. In the first response after reading it, acknowledge the review and summarise the highest-priority concerns. Treat the findings as claims to verify against the current code, not as unquestionable instructions to change it. If the code has moved since the baseline, identify what has changed.

Before implementing fixes or adding features, return a short triage of the findings: confirmed, disputed (with evidence), or needs reproduction; affected results; and whether remediation touches the frozen baseline. This handoff authorises reading and triage, not blanket implementation or unfreezing. Preserve the existing project restrictions. Do not silently fix the frozen baseline or overwrite its published results. The frozen plan requires before-and-after reporting if a bug is found during the test. Obtain the user's decision on changes that affect frozen behaviour or scope.

## Overall assessment

This is now a substantial offline research and risk-analysis system, not merely a pricing-engine project. The two defining charts exist, the analysis produces useful negative results as well as positive ones, and there is a serious reproducibility framework.

But implementation is ahead of validation. It is an advanced research prototype suitable for a technical demonstration—not yet an operational daily risk system or independently validated pricing platform.

Scope: roadmap, methodology, committed reports, workflows and selected implementation paths were reviewed, with separate engineering and quant reviews. Hermes checked the main code findings itself. This was read-only: no raw/private data, no test runs or numerical reproduction. The reported 534 passing tests is not a fresh verification. No current deployment check was performed.

## 1. Where progress actually stands

### Front chart and mark diagnostics: substantially built

The project has moved beyond RY into fifteen names, with rejection accounting, evidence tiers, call-put checks, smile comparisons and an independent-engine comparison. The strongest feature is not simply producing implied vols; it is showing when a measurement is unreliable and distinguishing an input problem from a suspicious quote.

### Book, hedge and risk analysis: substantially built, with important control gaps

There are two sample books, marked P&L, repricing attribution, a review queue, delta hedging, an actual-SXF comparison, an XIU vega-hedge experiment, stress episodes, component ES, VaR backtesting and limits. These are real modules with committed reports, not roadmap placeholders.

### Delivery: largely built

The static-site generator supports charts, a methodology PDF, investigations and day/expiry navigation. The walkthrough remains unfinished. The local implementation was checked, not the currently deployed website.

### Live operation: unfinished

The IBKR capture script and loader exist, but the gateway path has not been exercised, and the main reporting pipeline still discovers MX files rather than IBKR captures. Signing into IBKR alone will not make the existing charts refresh automatically.

### Prospective validation: pending

The frozen next-month test is the right milestone. The current six-month option sample helped shape the rules, so it cannot independently validate them.

## 2. What is strongest about the project

- **Coherent finance questions.** “Can I believe these marks?” followed by “What risk remains after hedging?” is a stronger structure than unrelated pricing and risk features.
- **Results are not forced to look successful.** Reports show that the XIU vega hedge did not usefully improve these books, bank-heavy VaR underestimated losses, and several dividend/funding discrepancies remain unresolved. These are useful findings when their scope is stated correctly.
- **Improved methodological discipline.** Earlier-day hedge estimates, point-in-time dividend handling, dependence-adjusted standard errors, multiple-testing corrections, synthetic fault benchmarks and a prospective frozen plan address specific ways a quant project can mislead itself.
- **Sensible architecture.** Pricing is separated from loading, reports are persisted, the site reads derived artifacts rather than market-data files, and caches use content fingerprints.

## 3. Highest-priority weaknesses

### R1. Whole-book coverage is not enforced strongly enough

Priority: highest correctness concern.

The hedge script emits a position-day only when both valuations succeed, then sums surviving rows. VaR and limits subsequently check completeness at the underlying level, not the individual-position level. If one option on RY is missing but another RY position succeeds, the aggregate can still look complete.

The headline hedge report uses 127 days, while the tail-risk reports acknowledge fewer complete-underlying days. Separately, the full-revaluation ES path replaces missing scenario values with zero after collecting explanatory notes.

This does not prove that the published hedge percentages or ES are materially wrong. It establishes that the controls permit incomplete books to enter calculations. Require position-by-position coverage and identical fully valued dates across comparisons before relying on the headlines.

Evidence at review baseline:
- `scripts/book_hedge.py:158–176,441–442`: conditional row emission and aggregation.
- `scripts/tail_risk.py:118–138`: completeness based on underlying aggregate delta.
- `scripts/tail_risk.py:167–170`: missing scenario values filled with zero.
- `reports/bank_heavy_hedge.txt:4–15`: 127-day headline comparison.

The quant review additionally identified per-name latest cached valuation dates in scenario construction (`scripts/scenarios.py:108–125`), so a common book as-of date needs checking too. No numerical effect on currently published ES was established.

### R2. Vega hedge does not exactly implement its stated specification

The expiry-selection code weights positions by absolute quantity times multiplier—not by option vega—despite documentation describing a vega-weighted expiry.

The vega stage builds on the index-proxy delta hedge, not the actual-SXF stage. These are separate comparisons, not one fully executable SXF-plus-options hedge chain.

The negative result remains informative for the rule actually implemented. It is not yet a clean rejection of the specified maturity-matched hedge.

Evidence:
- `scripts/book_hedge.py:421–429`: contract-exposure weighting in expiry selection.
- `scripts/book_hedge.py:461–465`: separate SXF stage and index-proxy-based vega stage.
- `reports/bank_heavy_hedge.txt:51–54`: vol-component standard deviation 1,724 before versus 1,841 with hedge P&L added.
- `reports/diversified_hedge.txt:56–59`: corresponding 841 versus 847.

Reconcile specification and implementation explicitly, subject to the freeze; do not silently retune the hedge.

### R3. Limits have an exposure-timestamp mismatch

Rows dated on a given day contain previous-valuation Greeks. The limits report uses those fields but labels output as usage on that day.

That can be a valid “risk carried into today” report, but it is not closing exposure. The distinction matters when explaining a breach.

Evidence:
- `scripts/book_hedge.py:160–174`: D0, V0 and G0 come from previous valuation.
- `scripts/limits.py:163–173,203–212`: those fields are consumed and labelled as latest-day usage.

Also distinguish Greek-based ES used in limits from the separate fully revalued scenario ES. Historical vega-limit usage uses full-sample move calibration; that hindsight is already acknowledged in `reports/bank_heavy_limits.txt:43–48`.

### R4. Independent pricing validation is narrower than the headline

Agreement with OptionMetrics is excellent for options without an intervening dividend. Dividend-spanning options remain substantially different, with vendor assumptions unavailable.

That supports the basic implementation, but does not independently settle discrete-dividend and early-exercise treatment. Do not say dividends alone explain the discrepancy without a matched-input comparison that establishes it.

There is an overstatement in findings text: a positive remaining gap across names is treated as sufficient evidence that it is real. RY's report shows a converted call-put gap with t = 0.911—not individually significant by the project's usual yardstick.

Evidence:
- `reports/ry_us_engine_numbers.txt:15–16`: dividend-spanning mean absolute difference 1.022 vol points versus 0.006 without an intervening dividend.
- `reports/ry_us_engine_numbers.txt:33–39`: converted RY gap 0.106, standard error 0.116, t = 0.911.
- `docs/findings.md:139–146`: statistical wording and unavailable vendor dividend assumptions.

### R5. Reproducibility claims exceed current provenance

The committed manifest establishes a successful 92-step run of an earlier commit. Current source defines 93 steps after day tables were added. That is an expected pending refresh, not evidence of a failed run.

However, the site turns the presence of a manifest into a blanket statement that every number came from that run. Partial runs can also replace the global manifest. The framework is good; publication wording needs narrowing or stronger artifact-level verification.

Evidence:
- `reports/manifest.json:3`: run commit `7b445f5` rather than review HEAD `49ac726`.
- `scripts/build_site.py:496–501`: blanket provenance text from manifest presence.
- `scripts/reproduce_all.py:135–152,186–187`: partial-run scope and global manifest replacement.

Additional engineering review observations, not observed run failures:
- Output artifacts are not individually hashed; the PDF is generated separately from the reproduction steps.
- “All or nothing” restoration handles nonzero subprocess exit codes, but exceptions or interruption can bypass restoration because the loop and finalisation are not covered by exception-safe recovery (`scripts/reproduce_all.py:143–188`).
- Tests and Pages deployment are independent workflows. A failed test run does not itself block successful publication (`.github/workflows/tests.yml:13–23`; `.github/workflows/pages.yml:29–46`).

### R6. Documentation drift undermines the presentation

The findings document still says option-book layers are unbuilt, later says C6/C8 are missing, and retains an obsolete description of dividend hindsight. The site publishes this document directly.

This makes completed work appear unfinished and obscures which limitations still apply. It is more than cosmetic.

Evidence:
- `docs/findings.md:8–10`: outdated opening project status.
- `docs/findings.md:935–939`: outdated dividend-hindsight description.
- `docs/findings.md:991–992`: C6/C8 described as absent.
- `scripts/build_site.py:672`: findings published directly.

### R7. Live capture needs integration and history safeguards

Capture and a compatible loader exist, but the day runner discovers MX inputs and uses MX loaders and fingerprints (`scripts/run_days.py:73–107,303–317`). Gateway capture alone is not an operational report pipeline.

Before routine use, check these static-code behaviours:
- `--date` accepts a trading date while the API requests current/frozen snapshots, permitting current data to be filed under a historical date (`scripts/capture_ibkr.py:122–143`).
- Repeating a name/day overwrites its CSV while appending another log entry (`:154–170`).
- One acquisition timestamp is assigned before all quote batches (`:97–109`).

Live impact has not been tested. The gateway connection and Montreal exchange code remain unverified. Capture integration and scheduling must follow the project's data-access rules and require the user to sign in.

## 4. How to interpret the current results

- Delta-hedge results are promising, but provisional pending the coverage audit.
- The vega-hedge result is useful for this implementation and sample, not evidence that index options generally cannot hedge single-name volatility.
- The funding estimate is a conditional option-implied carry adjustment, not an independently measured funding cost.
- A small Greek-explain residual shows reconciliation within the system. It does not independently prove the original marks or assumptions correct.
- Earlier-day estimation prevents a particular look-ahead error; it does not make a research process adapted on the same sample out-of-sample.
- The frozen month can test whether rules survive new observations. A pass would not establish crisis performance or trading profitability.
- The book-level hedge stage statistics lack the block-bootstrap uncertainty broadly promised by the methodology (`scripts/book_hedge.py:394–400`; `docs/methodology.md:219–224`). Small differences between stages should not be presented as established improvements.

## 5. Recommended next sequence, subject to user-approved freeze handling

1. Resolve position coverage and valuation timestamps before strengthening claims.
2. Reconcile the implemented vega hedge with the frozen specification.
3. Correct stale findings and separate verified results from unresolved explanations.
4. Complete a clean current-version reproduction and tighten provenance wording.
5. Finish the ninety-second walkthrough.
6. Exercise and connect IBKR capture when access is ready.
7. Run and report the untouched month exactly as predeclared.

Because rules are frozen, document correctness findings before changing behaviour. The frozen plan requires before-and-after reporting if a bug is found during the test. Do not silently replace the baseline or blend the new sample into in-sample figures.

## Bottom line

The project has enough features. Its next improvement should be confidence, not breadth.

The strongest version is a focused, inspectable research system that can explain its marks, expose its own failures, and show what hedging achieved. The remaining work is principally coverage controls, precise claims, reproducibility and unseen-data evidence—not another risk module.
