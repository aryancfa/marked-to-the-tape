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
    assert load_dividends(p) == [(date(2026, 7, 27), 0.50, "declared"),
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
