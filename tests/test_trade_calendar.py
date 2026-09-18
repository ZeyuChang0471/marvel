"""Tests for the A-share trading calendar and intraday market phase.

The calendar is never fetched from the network here: tests inject a fixed
trading-date set through the module cache so the assertions are deterministic.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import pytest

from marvel.dataflows import trade_calendar as tc


def _inject(dates: list[date]) -> None:
    """Install a fixed calendar, bypassing the HTTP fetch."""
    ordered = sorted(dates)
    tc._cache = (time.time(), ordered, set(ordered))


@pytest.fixture(autouse=True)
def _clean_cache():
    tc.reset_cache()
    yield
    tc.reset_cache()


# A small calendar with an obvious holiday gap:
#   Mon 2026-09-14 .. Fri 2026-09-18 trading, then Mon 2026-09-21.
_CAL = [
    date(2026, 9, 14),
    date(2026, 9, 15),
    date(2026, 9, 16),
    date(2026, 9, 17),
    date(2026, 9, 18),
    date(2026, 9, 21),
]


class TestIsCnSymbol:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "symbol,expected",
        [
            ("600519", True),
            ("000001", True),
            ("300750", True),
            ("920819", True),
            ("600519.SH", True),
            ("000001.SZ", True),
            ("600519.sh", True),
            ("AAPL", False),
            ("00700.HK", False),
            ("", False),
            ("60051", False),
            ("6005199", False),
        ],
    )
    def test_symbol_recognition(self, symbol, expected):
        assert tc.is_cn_symbol(symbol) is expected


class TestTradingDay:
    @pytest.mark.unit
    def test_trading_day_inside_window(self):
        _inject(_CAL)
        assert tc.is_cn_trading_day("2026-09-16") is True

    @pytest.mark.unit
    def test_holiday_inside_window_is_not_a_trading_day(self):
        """The weekend rule alone would call this a trading day — the fetched
        calendar is what makes the difference."""
        _inject(_CAL)
        assert date(2026, 9, 19).weekday() == 5  # Saturday, trivially closed
        # 2026-09-20 is a Sunday; pick a weekday the calendar omits instead.
        gap = date(2026, 9, 15)
        _inject([d for d in _CAL if d != gap])
        assert gap.weekday() < 5
        assert tc.is_cn_trading_day("2026-09-15") is False

    @pytest.mark.unit
    def test_beyond_window_falls_back_to_weekday_rule(self):
        _inject(_CAL)
        # 2026-09-22 is a Tuesday, past the last known date.
        assert tc.is_cn_trading_day("2026-09-22") is True
        # 2026-09-26 is a Saturday.
        assert tc.is_cn_trading_day("2026-09-26") is False

    @pytest.mark.unit
    def test_before_window_is_not_trading(self):
        _inject(_CAL)
        assert tc.is_cn_trading_day("2020-01-02") is False

    @pytest.mark.unit
    def test_empty_calendar_degrades_to_weekend_rule(self):
        _inject([])
        assert tc.is_cn_trading_day("2026-09-16") is True   # Wednesday
        assert tc.is_cn_trading_day("2026-09-19") is False  # Saturday

    @pytest.mark.unit
    def test_malformed_date_is_not_a_trading_day(self):
        _inject(_CAL)
        assert tc.is_cn_trading_day("not-a-date") is False
        assert tc.is_cn_trading_day("") is False


class TestPreviousTradingDay:
    @pytest.mark.unit
    def test_skips_the_weekend(self):
        _inject(_CAL)
        assert tc.previous_cn_trading_day("2026-09-21") == "2026-09-18"

    @pytest.mark.unit
    def test_is_strictly_before(self):
        _inject(_CAL)
        assert tc.previous_cn_trading_day("2026-09-18") == "2026-09-17"

    @pytest.mark.unit
    def test_falls_back_to_weekday_rollback_without_calendar(self):
        _inject([])
        assert tc.previous_cn_trading_day("2026-09-21") == "2026-09-18"


class TestMarketPhase:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "moment,expected",
        [
            (datetime(2026, 9, 16, 9, 0), "pre_open"),
            (datetime(2026, 9, 16, 10, 0), "in_session"),
            (datetime(2026, 9, 16, 12, 0), "lunch_break"),
            (datetime(2026, 9, 16, 14, 0), "in_session"),
            (datetime(2026, 9, 16, 16, 0), "post_close"),
        ],
    )
    def test_phases_on_a_trading_day(self, moment, expected):
        _inject(_CAL)
        assert tc.cn_market_phase(moment.replace(tzinfo=tc.CN_TZ)) == expected

    @pytest.mark.unit
    def test_closed_on_a_non_trading_day(self):
        _inject(_CAL)
        saturday = datetime(2026, 9, 19, 10, 0, tzinfo=tc.CN_TZ)
        assert tc.cn_market_phase(saturday) == "closed"


class TestLatestTradingDay:
    @pytest.mark.unit
    def test_post_close_returns_today(self):
        _inject(_CAL)
        after_close = datetime(2026, 9, 16, 16, 0, tzinfo=tc.CN_TZ)
        assert tc.latest_cn_trading_day(after_close) == "2026-09-16"

    @pytest.mark.unit
    def test_during_session_returns_the_last_completed_day(self):
        _inject(_CAL)
        mid_session = datetime(2026, 9, 16, 10, 0, tzinfo=tc.CN_TZ)
        assert tc.latest_cn_trading_day(mid_session) == "2026-09-15"

    @pytest.mark.unit
    def test_weekend_returns_friday(self):
        _inject(_CAL)
        saturday = datetime(2026, 9, 19, 10, 0, tzinfo=tc.CN_TZ)
        assert tc.latest_cn_trading_day(saturday) == "2026-09-18"

    @pytest.mark.unit
    def test_pre_open_returns_previous_day(self):
        _inject(_CAL)
        before_open = datetime(2026, 9, 16, 8, 0, tzinfo=tc.CN_TZ)
        assert tc.latest_cn_trading_day(before_open) == "2026-09-15"


class TestNoDataReason:
    @pytest.mark.unit
    def test_non_trading_day_message(self):
        _inject(_CAL)
        assert "非交易日" in tc.cn_no_data_reason("2026-09-19")

    @pytest.mark.unit
    def test_unknown_trading_day_message(self):
        _inject(_CAL)
        # A Wednesday inside the window that the calendar omits would be a
        # holiday; anything else is "may be suspended or delayed".
        assert "暂无数据" in tc.cn_no_data_reason("2026-09-18")

    @pytest.mark.unit
    def test_today_pre_open_message(self):
        _inject(_CAL)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(tc, "cn_today_str", lambda: "2026-09-16")
            mp.setattr(tc, "cn_market_phase", lambda now=None: "pre_open")
            assert "尚未开盘" in tc.cn_no_data_reason("2026-09-16")

    @pytest.mark.unit
    def test_today_in_session_message(self):
        _inject(_CAL)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(tc, "cn_today_str", lambda: "2026-09-16")
            mp.setattr(tc, "cn_market_phase", lambda now=None: "in_session")
            assert "盘中" in tc.cn_no_data_reason("2026-09-16")


class TestDateHelpers:
    @pytest.mark.unit
    def test_parse_date_accepts_iso_datetime(self):
        assert tc._parse_date("2026-09-16 15:00:00") == date(2026, 9, 16)

    @pytest.mark.unit
    def test_now_cn_is_shanghai_time(self):
        assert tc.now_cn().tzinfo is not None
        assert str(tc.now_cn().tzinfo) == "Asia/Shanghai"

    @pytest.mark.unit
    def test_today_str_shape(self):
        value = tc.cn_today_str()
        assert len(value) == 10 and value[4] == "-" and value[7] == "-"
