"""The analysis date must be judged on the **market** clock, never the host clock.

`tests/test_lookahead_guard.py` derived its "today" from `datetime.now()` while
the data layer decides historicity with `_market_today()` (Asia/Shanghai). On a UTC
runner between 16:00 and 24:00 UTC the market has already rolled over, so
`_is_historical(TODAY)` came back True and two look-ahead tests failed — CI was red
for six hours of every day and green for the rest, which is the worst kind of
failure to diagnose (and exactly what happened here).

The CLI had the same defect in production code: it offered the market's *yesterday*
as the default analysis date and rejected the market's actual current day as "in the
future". The Web UI already used the market-aware helpers.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from marvel.dataflows import a_stock

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(*parts: str) -> str:
    return REPO_ROOT.joinpath(*parts).read_text(encoding="utf-8")


@pytest.mark.unit
class TestCliUsesTheMarketClock:
    def test_today_is_the_market_date(self):
        from cli.main import market_today_str

        assert market_today_str() == a_stock._market_today().isoformat()

    def test_the_default_is_the_latest_trading_day(self):
        from cli.main import default_analysis_date
        from marvel.dataflows.trade_calendar import latest_cn_trading_day

        assert default_analysis_date() == latest_cn_trading_day()

    def test_the_markets_today_is_not_rejected_as_future(self, monkeypatch):
        """The exact production bug: market-today was 'in the future' on a UTC host."""
        import cli.main as cli_main

        monkeypatch.setattr(cli_main, "market_today_str", lambda: "2026-09-27")

        assert not cli_main.is_future_analysis_date("2026-09-27")
        assert not cli_main.is_future_analysis_date("2026-09-20")
        assert cli_main.is_future_analysis_date("2026-09-28")

    def test_a_timestamp_is_compared_on_its_date_part(self, monkeypatch):
        import cli.main as cli_main

        monkeypatch.setattr(cli_main, "market_today_str", lambda: "2026-09-27")

        assert not cli_main.is_future_analysis_date("2026-09-27 09:30:00")

    def test_the_module_no_longer_reads_the_host_clock_for_dates(self):
        """Structural: a future edit must not reintroduce `datetime.now()` here."""
        src = _read("cli", "main.py")
        start = src.index("def market_today_str")
        end = src.index("def save_report_to_disk")
        block = src[start:end]

        assert "datetime.datetime.now()" not in block, (
            "分析日的默认值/校验又用回了主机时钟——UTC 主机上会把当天判成未来"
        )
        assert "cn_today_str" in block and "latest_cn_trading_day" in block


@pytest.mark.unit
class TestLookaheadFixtureUsesTheMarketClock:
    def test_the_suite_does_not_derive_today_from_the_host_clock(self):
        src = _read("tests", "test_lookahead_guard.py")

        assert "TODAY = a_stock._market_today().isoformat()" in src
        assert "TODAY = datetime.now()" not in src

    def test_a_host_behind_the_market_would_have_failed_the_old_fixture(self):
        """Keep the reason on the record, so nobody 'simplifies' it back."""
        src = _read("tests", "test_lookahead_guard.py")

        assert "_market_today()" in src
        # the comment explains the six-hour window
        assert "16:00" in src and "24:00" in src


@pytest.mark.unit
class TestMarketClockIsTheReferenceAcrossTheRepo:
    def test_the_market_date_is_not_derived_from_the_host_anywhere_in_tests(self):
        """One host-clock 'today' is enough to make the suite time-of-day flaky."""
        import re

        # Only *assignments* of a "today" variable from the host clock count.
        # Matching the bare substring would also hit this file's own prose and
        # its `assert "TODAY = datetime.now()" not in src` line — the repository
        # already learned this lesson once with its comment-matching guards.
        pattern = re.compile(
            r"\b(today|TODAY|today_str)\s*=\s*(datetime\.now\(\)|date\.today\(\))"
        )
        offenders = []
        for path in (REPO_ROOT / "tests").glob("test_*.py"):
            if path.name == Path(__file__).name:
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                stripped = line.strip()
                if stripped.startswith(("#", "assert")):
                    continue
                if pattern.search(line):
                    offenders.append(f"{path.name}:{number}: {stripped}")

        assert not offenders, (
            "这些地方用主机时钟算『今天』，而数据层用市场时钟；"
            f"UTC 主机在 16:00-24:00 UTC 会因此失败：{offenders}"
        )

    def test_the_market_today_helper_is_available_for_that(self):
        assert callable(a_stock._market_today)
        assert isinstance(a_stock._market_today(), date)
        assert a_stock._market_today() <= date.today() + timedelta(days=1)
