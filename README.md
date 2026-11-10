# Marked to the Tape

A risk system for a small book of Canadian listed options. It prices positions with its own engine for American options with discrete dividends. It compares the implied volatilities with Montreal Exchange settlements and with an independent source. Then it replays real historical days and shows what risk is left after hedging with SXF, ZEB and XIU.

**Status:** in progress. The pricing and inversion core (`src/voleng.py`) is done and tested.

## Data

This repository contains no exchange or vendor data. Montreal Exchange, Interactive Brokers and OptionMetrics data are licensed for personal use and can't be redistributed. Raw files stay on the author's machine under `data/`, which is excluded from version control. Everything published here is computed from them: errors in vol points, residuals and summary statistics.

## Run the tests

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -v
```

On macOS or Linux, use `.venv/bin/python` instead.
