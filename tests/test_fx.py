"""test_fx.py: the exchange-rate reader, on a made-up file."""

from datetime import date

import pytest

from fx import load_usdcad, rate_on

BODY = ('"TERMS AND CONDITIONS"\n"made up"\n\n"OBSERVATIONS"\n"date","FXUSDCAD"\n'
        '"2026-06-04","1.3700"\n"2026-06-05","1.3750"\n"2026-06-08",""\n"2026-06-09","1.3800"\n')


def series(tmp_path):
    p = tmp_path / "FXUSDCAD.csv"
    p.write_text(BODY, encoding="utf-8")
    return load_usdcad(p)


def test_rates_are_read_as_canadian_dollars_per_us_dollar(tmp_path):
    s = series(tmp_path)
    assert s[date(2026, 6, 5)] == pytest.approx(1.375)
    assert date(2026, 6, 8) not in s                    # an empty observation is not filled


def test_the_latest_rate_on_or_before_the_day_is_used(tmp_path):
    s = series(tmp_path)
    assert rate_on(s, date(2026, 6, 5)) == pytest.approx(1.375)
    assert rate_on(s, date(2026, 6, 8)) == pytest.approx(1.375)     # weekend and a gap
    assert rate_on(s, date(2026, 6, 9)) == pytest.approx(1.38)


def test_a_stale_rate_raises_instead_of_being_used(tmp_path):
    s = series(tmp_path)
    with pytest.raises(ValueError):
        rate_on(s, date(2026, 6, 20))
    with pytest.raises(ValueError):
        rate_on(s, date(2026, 6, 1))
