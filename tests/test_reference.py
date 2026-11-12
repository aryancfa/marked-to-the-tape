"""test_reference.py: the dividend schedule reader, on a made-up file."""

from datetime import date

import pytest

from loaders.reference import first_projected, load_dividends


def write(tmp_path, body):
    p = tmp_path / "zzz_dividends.csv"
    p.write_text("ex_date,cash,status,source\n" + body, encoding="utf-8")
    return p


def test_reads_dates_cash_and_status(tmp_path):
    p = write(tmp_path, "2026-07-27,0.50,declared,made up\n"
                        "2027-01-25,0.55,projected,made up\n")
    assert [tuple(d)[:3] for d in load_dividends(p)] == [(date(2026, 7, 27), 0.50, "declared"),
                                 (date(2027, 1, 25), 0.55, "projected")]


def test_first_projected_is_the_next_one_after_the_valuation_date(tmp_path):
    divs = load_dividends(write(tmp_path, "2026-01-26,0.50,projected,x\n"
                                          "2026-07-27,0.50,declared,x\n"
                                          "2027-01-25,0.55,projected,x\n"
                                          "2027-04-23,0.55,projected,x\n"))
    assert first_projected(divs, date(2026, 11, 2)) == date(2027, 1, 25)
    assert first_projected(divs[:2], date(2026, 11, 2)) is None


def test_an_unknown_status_raises(tmp_path):
    with pytest.raises(ValueError):
        load_dividends(write(tmp_path, "2026-07-27,0.50,guessed,x\n"))


def test_announcement_date_is_read_when_the_file_has_one(tmp_path):
    p = tmp_path / "zzz_dividends.csv"
    p.write_text("ex_date,cash,status,announced,source\n"
                 "2026-04-23,0.50,declared,,x\n"
                 "2026-07-27,0.55,declared,2026-05-28,x\n", encoding="utf-8")
    divs = load_dividends(p)
    assert divs[0].announced is None and divs[1].announced == date(2026, 5, 28)
    assert divs[1].ex_date == date(2026, 7, 27) and divs[1].cash == 0.55


def test_a_dividend_not_yet_announced_on_the_day_counts_as_projected(tmp_path):
    """On 20 May the July dividend had not been declared. Whatever the file
    says now, an expiry past it rested on a guess that day."""
    p = tmp_path / "zzz_dividends.csv"
    p.write_text("ex_date,cash,status,announced,source\n"
                 "2026-07-27,0.55,declared,2026-05-28,x\n"
                 "2027-01-25,0.60,projected,,x\n", encoding="utf-8")
    divs = load_dividends(p)
    assert first_projected(divs, date(2026, 5, 20)) == date(2026, 7, 27)
    assert first_projected(divs, date(2026, 5, 28)) == date(2027, 1, 25)


# ---------------------------------------------------------------------------
# Point-in-time: the schedule as it could have been written on a given day
# ---------------------------------------------------------------------------

from loaders.reference import Dividend as _D, as_known_on, has_history, project, rule_for


def _quarterly():
    """Made-up company: raises 5 cents each July, announces a month ahead."""
    rows = [(date(2025, 1, 15), 1.00, None), (date(2025, 4, 15), 1.00, None),
            (date(2025, 7, 15), 1.05, None), (date(2025, 10, 15), 1.05, None),
            (date(2026, 1, 15), 1.05, None), (date(2026, 4, 15), 1.05, None),
            (date(2026, 7, 15), 1.12, date(2026, 6, 15)),       # a bigger raise than last year's
            (date(2026, 10, 14), 1.12, date(2026, 9, 14))]      # and a date one day early
    return [_D(d, c, "declared", a) for d, c, a in rows]


def test_the_rule_repeats_last_years_raise_at_the_same_point_of_the_year():
    known = [(x.ex_date, x.cash) for x in _quarterly()[:6]]
    got = project(known, "raise", date(2027, 4, 30))
    assert [c for _, c in got] == pytest.approx([1.10, 1.10, 1.10, 1.10])
    assert got[0][0] == date(2026, 7, 15) and got[1][0] == date(2026, 10, 15)
    same = project(known, "repeat", date(2026, 12, 31))
    assert [c for _, c in same] == pytest.approx([1.05, 1.05])


def test_a_day_before_the_announcement_sees_the_projection_not_the_later_amount():
    """On 1 June 2026 the July dividend had not been announced. The rule gave
    1.10 on 15 July; the company later declared 1.12. Pricing 1 June with
    1.12 would be hindsight."""
    then = {x.ex_date: x for x in as_known_on(_quarterly(), date(2026, 6, 1))}
    assert then[date(2026, 7, 15)].cash == pytest.approx(1.10)
    assert then[date(2026, 7, 15)].status == "projected"
    assert date(2026, 10, 14) not in then and then[date(2026, 10, 15)].status == "projected"


def test_from_the_announcement_day_the_declared_amount_and_date_are_used():
    then = {x.ex_date: x for x in as_known_on(_quarterly(), date(2026, 6, 15))}
    assert then[date(2026, 7, 15)].cash == pytest.approx(1.12)
    assert then[date(2026, 7, 15)].status == "declared"
    # and the next projection now builds on the new amount
    assert then[date(2026, 10, 15)].cash == pytest.approx(1.12)


def test_a_hand_entered_projected_date_is_kept_but_never_its_amount():
    """The file holds a projected row dated from a published schedule, five
    days off last year's date. The date is kept; the amount is the rule's."""
    divs = _quarterly()[:6] + [_D(date(2026, 7, 20), 9.99, "projected", None)]
    then = as_known_on(divs, date(2026, 5, 1))
    july = [x for x in then if x.ex_date.month == 7 and x.ex_date.year == 2026]
    assert len(july) == 1 and july[0].ex_date == date(2026, 7, 20)
    assert july[0].cash == pytest.approx(1.10)


def test_with_too_little_history_the_file_is_returned_and_that_is_reported():
    short = _quarterly()[4:]                # four payments: no way to see last year's raise
    day = date(2026, 6, 1)
    assert not has_history(short, day)
    assert as_known_on(short, day) == short
    assert has_history(_quarterly(), day)


def test_funds_repeat_and_companies_raise():
    assert rule_for("xiu") == "repeat" and rule_for("ZEB") == "repeat"
    assert rule_for("RY") == "raise"
