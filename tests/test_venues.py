"""Venue shares and days to liquidate (C6): made-up volumes only."""

import numpy as np
import pandas as pd
import pytest

from loaders.compustat import load_volume_pair
from venues import (adv, bucket, common_step, days_to_liquidate, one_for_one, same_count,
                    share_over, single_venue_days, toronto_share)


def pair(tor, ny, start="2026-01-05", tor_px=13.0, ny_px=10.0):
    d = pd.bdate_range(start, periods=len(tor))
    return pd.DataFrame({"tor": tor, "ny": ny, "tor_px": tor_px, "ny_px": ny_px},
                        index=pd.DatetimeIndex(d, name="day"))


def test_share_is_volume_weighted_on_days_both_traded():
    p = pair([300.0, 100.0, 50.0], [100.0, 100.0, np.nan])
    s = toronto_share(p, "YE")
    assert s["share"].iloc[0] == pytest.approx(400 / 600)     # the NY holiday is left out
    assert int(s["days"].iloc[0]) == 2


def test_a_twelve_month_share_spans_new_year_in_one_piece():
    p = pair([100.0] * 10, [100.0] * 5 + [300.0] * 5, start="2025-12-24")
    assert p.index.year.nunique() == 2
    assert share_over(p) == pytest.approx(1000 / (1000 + 2000))


def test_two_sources_counting_the_same_trades_agree_day_by_day():
    d = pd.bdate_range("2026-01-05", periods=4)
    a = pd.Series([100.0, 200.0, 0.0, 400.0], index=d)
    out = same_count(a, pd.Series([100.0, 201.0, 50.0, 300.0], index=d))
    assert out["days"] == 3                                   # the zero day is left out
    assert out["agree"] == pytest.approx(2 / 3)


def test_a_change_in_what_one_source_counts_shows_in_every_name_at_once():
    days = pd.bdate_range("2025-06-02", "2025-11-28")
    def name(seed):
        rng = np.random.default_rng(seed)
        tor = pd.Series(rng.uniform(90, 110, len(days)), index=days)
        tor[days >= "2025-09-01"] *= 1.5                      # Toronto's figure starts counting more
        return pd.DataFrame({"tor": tor, "ny": rng.uniform(90, 110, len(days))})
    step, typical = common_step({f"N{i}": name(i) for i in range(5)}, "2025-09")
    assert step == pytest.approx(np.log(1.5), abs=0.05)
    assert step >= typical


def test_a_one_for_one_listing_trades_at_the_exchange_rate():
    assert one_for_one(pair([1.0] * 3, [1.0] * 3, tor_px=13.5, ny_px=10.0)) == (1.35, True)
    m, ok = one_for_one(pair([1.0] * 3, [1.0] * 3, tor_px=13.5, ny_px=5.0))  # two shares per receipt
    assert not ok and m == pytest.approx(2.7)


def test_a_holiday_day_is_compared_with_that_venue_s_own_normal():
    tor = [100.0] * 20 + [60.0]
    ny = [50.0] * 20 + [np.nan]                               # US holiday on the last day
    out = single_venue_days(pair(tor, ny))
    assert list(out["venue"]) == ["Toronto alone"]
    assert out["ratio"].iloc[0] == pytest.approx(0.6)


def test_a_holiday_needs_a_full_window_before_it():
    assert single_venue_days(pair([100.0] * 5 + [60.0], [50.0] * 5 + [np.nan])).empty


def test_average_volume_uses_earlier_days_only():
    v = pd.Series([10.0] * 20 + [1000.0], index=pd.bdate_range("2026-01-05", periods=21))
    assert adv(v, v.index[-1]) == pytest.approx(10.0)
    assert np.isnan(adv(v, v.index[5]))


def test_days_to_liquidate_at_a_fifth_of_volume():
    assert days_to_liquidate(-40, 100.0) == pytest.approx(2.0)
    assert np.isinf(days_to_liquidate(10, 0.0))
    assert bucket(0.5) == "within a day" and bucket(2.0) == "1 to 5 days"
    assert bucket(9.0) == "6 to 20 days" and bucket(45.0) == "21 to 60 days"
    assert bucket(200.0) == "over 60 days" and bucket(float("inf")).startswith("no volume")
    assert bucket(float("nan")) == "no volume history"


def test_loader_keeps_only_the_exchange_listed_common_share(tmp_path):
    (tmp_path / "t.csv").write_text("datadate,cshtrd,prccd\n2026-01-05,500,13\n2026-01-06,400,13\n")
    (tmp_path / "u.csv").write_text(
        "datadate,tic,iid,cshtrd,prccd,curcdd,exchg\n"
        "2026-01-05,AA,01,100,10,USD,11\n"
        "2026-01-05,AAPF,31,7,20,USD,19\n"                    # an over-the-counter preferred
        "2026-01-07,AA,01,90,10,USD,11\n")                     # a Canadian holiday
    p = load_volume_pair(tmp_path / "t.csv", tmp_path / "u.csv", today=pd.Timestamp("2026-02-01").date())
    assert list(p["ny"].fillna(-1)) == [100, -1, 90]
    assert list(p["tor"].fillna(-1)) == [500, 400, -1]
