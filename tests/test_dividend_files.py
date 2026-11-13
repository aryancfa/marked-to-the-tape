"""
test_dividend_files.py: every dividend schedule under reference/ (build sheet A4).

These files are in git: ex-dates and amounts are public facts or this
project's own projections. One ex-date a day out puts the early-exercise
point in the wrong place, so each file is checked for the mistakes that are
easy to make by hand: a date on a weekend or holiday, rows out of order, a
dividend announced after it was paid, a projection sitting before a declared
row.
"""

from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

from loaders.reference import first_projected, load_dividends
from tradingcal import TSX, trading_days

REF = Path(__file__).resolve().parents[1] / "reference"
FILES = sorted(REF.glob("*_dividends.csv"))


def test_there_are_schedules_to_check():
    assert len(FILES) >= 2


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_schedule_is_well_formed(path):
    divs = load_dividends(path)             # raises on an unknown status
    raw = pd.read_csv(path)
    name = path.stem.replace("_dividends", "").upper()
    assert set(raw["underlying"]) <= {name}
    if not divs:                            # a name that pays nothing: header only
        return
    dates = [x.ex_date for x in divs]
    assert dates == sorted(set(dates)), "ex-dates must be in order with none repeated"
    sessions = set(trading_days(TSX, dates[0], dates[-1]))
    closed = [d for d in dates if d not in sessions]
    assert not closed, f"ex-dates on days the exchange is closed: {closed}"
    assert all(x.cash > 0 for x in divs)
    assert raw["source"].notna().all() and (raw["source"].str.len() > 10).all()


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_declared_rows_come_before_projected_ones(path):
    status = [x.status for x in load_dividends(path)]
    if "projected" in status:
        first = status.index("projected")
        assert all(s == "projected" for s in status[first:])


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_a_dividend_is_announced_before_it_goes_ex(path):
    for x in load_dividends(path):
        if x.announced is not None:
            assert x.status == "declared"
            # equal means the announcement date was not found: the dividend
            # counts as unknown until the day it goes ex
            assert x.announced <= x.ex_date


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_a_dividend_counts_as_unknown_until_the_day_it_is_announced(path):
    """The day before an announcement the dividend is the first unknown one;
    on the day itself it no longer is."""
    divs = load_dividends(path)
    for x in divs:
        if x.announced is None:
            continue
        before = x.announced - timedelta(days=1)
        assert first_projected(divs, before) is not None
        assert first_projected(divs, before) <= x.ex_date
        if x.announced < x.ex_date:
            after = first_projected(divs, x.announced)
            assert after is None or after != x.ex_date


def test_every_announcement_matches_a_dividend_in_its_schedule():
    ann = pd.read_csv(REF / "dividend_announcements.csv", parse_dates=["ex_date", "announced"])
    assert ann["announced"].notna().all()
    for name, g in ann.groupby("underlying"):
        divs = {x.ex_date: x for x in load_dividends(REF / f"{name.lower()}_dividends.csv")}
        for row in g.itertuples():
            x = divs.get(row.ex_date.date())
            assert x is not None, f"{name} {row.ex_date.date()} is not in the schedule"
            assert x.status == "declared" and x.announced == row.announced.date()
            if not pd.isna(row.cash):
                assert x.cash == pytest.approx(row.cash)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_every_schedule_can_be_rebuilt_as_it_was_known_on_the_first_day_of_data(path):
    """Point-in-time needs a year of history plus one payment before the
    option data starts (8 May 2026), to see last year's raise."""
    from datetime import date
    from loaders.reference import as_known_on, has_history, rule_for
    divs = load_dividends(path)
    if not divs:
        return
    name = path.stem.replace("_dividends", "")
    day = date(2026, 5, 8)
    assert has_history(divs, day, rule_for(name))
    # the rate's size does not matter here: only what was known and what was projected
    then = as_known_on(divs, day, rule_for(name), fx=lambda d: 1.0)
    known = {x.ex_date for x in then if x.status == "declared"}
    # nothing announced after that day is in the schedule as a declared row
    assert all(x.announced is None or x.announced <= day for x in divs if x.ex_date in known)
    assert any(x.status == "projected" and x.ex_date > day for x in then)


def test_published_ex_dates_are_public_before_they_are_announced():
    """reference/published_dates.csv: each row matches a schedule row (the
    reader raises otherwise), was public before the dividend went ex, and
    names where the date was read."""
    raw = pd.read_csv(REF / "published_dates.csv", parse_dates=["ex_date", "known_from"])
    assert (raw["known_from"] < raw["ex_date"]).all()
    assert (raw["source"].str.len() > 10).all()
    for name in sorted(set(raw["underlying"])):
        divs = load_dividends(REF / f"{name.lower()}_dividends.csv")
        got = sorted(x.ex_date for x in divs if x.date_known is not None)
        want = sorted(d.date() for d in raw.loc[raw["underlying"] == name, "ex_date"])
        assert got == want
