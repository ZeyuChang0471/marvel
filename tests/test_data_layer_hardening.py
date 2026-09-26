"""Guards for six data-layer defects found in review.

Each one is the same shape of bug: the code produced a **plausible** result that
was quietly wrong, so nothing downstream could tell the difference.

1. ``get_industry_comparison`` advertised "top/bottom N" but only ever fetched the
   top ``2N`` — there was no bottom half at all.
2. ``_em_get`` sent one request with no retry, so an Eastmoney 429/5xx surfaced in
   the report as "未上龙虎榜" / "行业数据获取为空" (no data) rather than as a failure.
3. the mootdx TCP client is a module-level singleton with no lock, and the Web UI
   can run two sessions at once against one stateful socket.
4. the caches were rewritten in place, so an interrupted write left a truncated
   file that still parses (the northbound CSV rewrites its whole history).
5. ``get_stock_data`` silently truncated at ``offset=800`` (~3 years) and only
   reported "# Total records: N".
6. ``_load_ohlcv_astock`` reused any cache written today, so a partially-formed
   bar written mid-session was later served as the day's final close.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timedelta, time as dtime

import pandas as pd
import pytest

from marvel.dataflows import a_stock, utils as df_utils
from marvel.dataflows.utils import atomic_write_text

PAST = "2026-05-12"
MARKET_TZ = a_stock._MARKET_TZ


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# 1. industry ranking really shows a top and a bottom
# ---------------------------------------------------------------------------


def _industry_payload(changes: list[float]) -> dict:
    """push2 `clist` payload, already sorted descending like the real API."""
    rows = []
    for index, change in enumerate(sorted(changes, reverse=True)):
        rows.append({
            "f14": f"行业{index}",
            "f3": change,
            "f104": 10,
            "f105": 20,
            "f140": f"领涨{index}",
        })
    return {"data": {"diff": rows}}


@pytest.mark.unit
class TestIndustryRanking:
    def _patch(self, monkeypatch, payload) -> None:
        class FakeSession:
            def get(self, *args, **kwargs):
                return _FakeResponse(payload)

        monkeypatch.setattr(a_stock, "_EM_SESSION", FakeSession())
        monkeypatch.setattr(a_stock, "_EM_MIN_INTERVAL", 0.0)
        monkeypatch.setattr(a_stock.random, "uniform", lambda a, b: 0.0)

    def test_bottom_half_is_actually_included(self, monkeypatch):
        """收益最大的修复点：跌幅榜原先一个都没有。"""
        self._patch(
            monkeypatch,
            _industry_payload([5.0, 4.0, 3.0, 2.0, 1.0, -1.0, -2.0, -3.0, -4.0, -5.0]),
        )

        text = a_stock.get_industry_comparison("600519", PAST, top_n=2)

        assert "行业0" in text, "涨幅榜丢了"
        assert "行业9" in text, "跌幅榜没有出现——「top/bottom」仍然是假的"
        assert text.index("行业0") < text.index("行业9"), "榜单没有按涨跌幅排序"

    def test_ranks_are_the_real_market_ranks(self, monkeypatch):
        self._patch(
            monkeypatch,
            _industry_payload([5.0, 4.0, 3.0, 2.0, 1.0, -1.0, -2.0, -3.0, -4.0, -5.0]),
        )

        text = a_stock.get_industry_comparison("600519", PAST, top_n=2)

        assert "  1. 行业0" in text
        assert "  2. 行业1" in text
        # 共 10 个行业，最后一名是第 10 名，而不是第 3 名
        assert "  10. 行业9" in text
        assert "  9. 行业8" in text

    def test_non_numeric_change_is_not_ranked_as_zero(self, monkeypatch):
        """停牌行业的 f3 是 "-"：不能当 0 混进榜单中间。"""
        payload = _industry_payload([3.0, 1.0, -1.0])
        payload["data"]["diff"].append({
            "f14": "停牌行业", "f3": "-", "f104": 0, "f105": 0, "f140": "",
        })
        self._patch(monkeypatch, payload)

        text = a_stock.get_industry_comparison("600519", PAST, top_n=3)

        assert "停牌行业" not in text

    def test_small_universe_does_not_duplicate_rows(self, monkeypatch):
        """行业数少于 2N 时，同一行不能既算涨幅榜又算跌幅榜。"""
        self._patch(monkeypatch, _industry_payload([2.0, 1.0]))

        text = a_stock.get_industry_comparison("600519", PAST, top_n=20)
        body = [
            ln for ln in text.splitlines()
            if ln.strip().startswith(("1.", "2."))
        ]

        assert len(body) == 2, f"行业被重复列出：{body}"
        assert "未另列跌幅榜" in text


# ---------------------------------------------------------------------------
# 2. atomic cache writes
# ---------------------------------------------------------------------------


class _ReplaceBoom:
    """An ``os`` stand-in whose ``replace`` always fails."""

    def __init__(self, real) -> None:
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def replace(self, src, dst):
        raise OSError("simulated crash during replace")


@pytest.mark.unit
class TestAtomicWrites:
    def test_write_replaces_content_and_leaves_no_temp_file(self, tmp_path):
        target = tmp_path / "cache.csv"

        atomic_write_text(str(target), "a,b\n1,2\n")

        assert target.read_text(encoding="utf-8") == "a,b\n1,2\n"
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "cache.csv"]
        assert not leftovers, f"留下了临时文件：{leftovers}"

    def test_failure_keeps_the_previous_file_intact(self, tmp_path, monkeypatch):
        """写入中途失败时旧缓存必须完好——这正是原来会丢掉历史数据的地方。"""
        target = tmp_path / "cache.csv"
        target.write_text("old,complete\n", encoding="utf-8")
        monkeypatch.setattr(df_utils, "os", _ReplaceBoom(os))

        with pytest.raises(OSError):
            atomic_write_text(str(target), "new,partial\n")

        assert target.read_text(encoding="utf-8") == "old,complete\n"
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "cache.csv"]
        assert not leftovers, f"失败了还留下临时文件：{leftovers}"

    def test_creates_missing_parent_directory(self, tmp_path):
        target = tmp_path / "nested" / "deeper" / "x.json"

        atomic_write_text(str(target), "{}")

        assert target.read_text(encoding="utf-8") == "{}"

    def test_northbound_snapshot_keeps_history_and_dedups(self, tmp_path, monkeypatch):
        cache = tmp_path / "northbound_daily.csv"
        monkeypatch.setattr(a_stock, "_northbound_cache_path", lambda: str(cache))

        a_stock._save_northbound_snapshot("2026-05-11", 1.0, 2.0)
        a_stock._save_northbound_snapshot("2026-05-12", 3.0, 4.0)
        # 同一天重复写入必须覆盖，而不是追加第二行
        a_stock._save_northbound_snapshot("2026-05-12", 5.0, 6.0)

        lines = cache.read_text(encoding="utf-8").strip().splitlines()
        assert lines[0].split(",")[0] == "date"
        assert len(lines) == 3, f"按日期去重失效：{lines}"
        assert lines[1] == "2026-05-11,1.00,2.00"
        assert lines[2] == "2026-05-12,5.00,6.00"
        assert not [p for p in tmp_path.iterdir() if p.name != cache.name]

    def test_interrupted_northbound_write_does_not_lose_history(
        self, tmp_path, monkeypatch
    ):
        cache = tmp_path / "northbound_daily.csv"
        monkeypatch.setattr(a_stock, "_northbound_cache_path", lambda: str(cache))
        a_stock._save_northbound_snapshot("2026-05-11", 1.0, 2.0)
        before = cache.read_text(encoding="utf-8")
        monkeypatch.setattr(df_utils, "os", _ReplaceBoom(os))

        with pytest.raises(OSError):
            a_stock._save_northbound_snapshot("2026-05-12", 3.0, 4.0)

        assert cache.read_text(encoding="utf-8") == before, "半截写入毁掉了历史缓存"

    def test_no_cache_path_is_written_in_place(self):
        """回归守卫：这几条路径不得退回 open(w) / to_csv(直接写)。"""
        import re
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent
            / "marvel"
            / "dataflows"
            / "a_stock.py"
        ).read_text(encoding="utf-8")

        assert not re.search(r"to_csv\(\s*cache_file", src), "K 线缓存又变成非原子写入了"
        assert not re.search(r'open\(\s*path,\s*"w"', src), "又出现了就地覆盖写的缓存"


# ---------------------------------------------------------------------------
# 3. mootdx client serialisation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMootdxClientIsSerialised:
    def test_concurrent_calls_do_not_overlap(self, monkeypatch):
        state = {"active": 0, "max_active": 0}
        guard = threading.Lock()

        class FakeClient:
            def bars(self, **kwargs):
                with guard:
                    state["active"] += 1
                    state["max_active"] = max(state["max_active"], state["active"])
                time.sleep(0.02)
                with guard:
                    state["active"] -= 1
                return "bars"

        monkeypatch.setattr(a_stock, "_get_mootdx_client", lambda: FakeClient())

        threads = [
            threading.Thread(
                target=lambda: a_stock._mootdx_call("bars", symbol="600519")
            )
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert state["max_active"] == 1, (
            f"有 {state['max_active']} 个线程同时用同一条 TCP 连接——"
            "通达信协议是有状态的，响应会错位"
        )

    def test_reset_from_inside_the_lock_does_not_deadlock(self):
        """`_mootdx_call` 持锁时会调 reset_mootdx_client：必须是可重入锁。"""
        done = threading.Event()

        def worker():
            with a_stock._MOOTDX_LOCK:
                a_stock.reset_mootdx_client()
            done.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        assert done.wait(timeout=5.0), "reset_mootdx_client 在锁内自锁了（应为 RLock）"


# ---------------------------------------------------------------------------
# 4. the 800-bar window is not silently truncated
# ---------------------------------------------------------------------------


def _mootdx_frame(periods: int) -> pd.DataFrame:
    """A frame shaped like what ``mootdx.Quotes.bars`` returns."""
    dates = pd.bdate_range("2023-01-02", periods=periods)
    frame = pd.DataFrame({
        "datetime": dates,
        "year": dates.year,
        "month": dates.month,
        "day": dates.day,
        "hour": 15,
        "minute": 0,
        "open": 1.0,
        "close": 2.0,
        "high": 2.0,
        "low": 1.0,
        "volume": 100,
        "amount": 1000.0,
    })
    frame.index = pd.Index(dates, name="datetime")
    return frame


@pytest.mark.unit
class TestBarWindowTruncationIsDisclosed:
    def _patch(self, monkeypatch, periods: int) -> None:
        monkeypatch.setattr(
            a_stock, "_mootdx_call", lambda *a, **k: _mootdx_frame(periods).copy()
        )
        monkeypatch.setattr(
            a_stock,
            "_supplement_stale_ohlcv_with_sina",
            lambda code, df, target, start=None: (df, False),
        )

    def test_capped_window_is_disclosed(self, monkeypatch):
        """请求区间早于数据源窗口时必须说出来，否则等于谎报「那几年没有数据」。"""
        self._patch(monkeypatch, a_stock._MOOTDX_BAR_LIMIT)
        today = a_stock._market_today().strftime("%Y-%m-%d")

        out = a_stock.get_stock_data("600519", "2015-01-01", today)

        assert "只提供最近" in out and str(a_stock._MOOTDX_BAR_LIMIT) in out
        assert "不是没有行情，而是没有取回来" in out

    def test_uncapped_window_is_not_flagged(self, monkeypatch):
        """上市晚于请求起点（不足 800 根）不算截断，不能乱报。"""
        self._patch(monkeypatch, 120)
        today = a_stock._market_today().strftime("%Y-%m-%d")

        out = a_stock.get_stock_data("600519", "2015-01-01", today)

        assert "只提供最近" not in out

    def test_window_covering_the_request_is_not_flagged(self, monkeypatch):
        """满 800 根但请求起点落在窗口内时也不该报警。"""
        self._patch(monkeypatch, a_stock._MOOTDX_BAR_LIMIT)
        today = a_stock._market_today().strftime("%Y-%m-%d")

        out = a_stock.get_stock_data("600519", "2025-01-01", today)

        assert "只提供最近" not in out


# ---------------------------------------------------------------------------
# 5. same-day OHLCV cache is only reused once the bar can be final
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOhlcvCacheFinality:
    def _cache_file(
        self, tmp_path, monkeypatch, *, market_today: date, last_bar: date, mtime: datetime
    ) -> str:
        cache = tmp_path / "600519-astock-daily.csv"
        pd.DataFrame({
            "Date": [pd.Timestamp(last_bar)],
            "Open": [1.0], "High": [2.0], "Low": [1.0], "Close": [2.0],
            "Volume": [100],
        }).to_csv(cache, index=False)
        stamp = mtime.timestamp()
        os.utime(cache, (stamp, stamp))
        monkeypatch.setattr(a_stock, "_market_today", lambda: market_today)
        return str(cache)

    def _phase(self, monkeypatch, phase: str) -> None:
        monkeypatch.setattr(
            "marvel.dataflows.trade_calendar.cn_market_phase", lambda *a, **k: phase
        )

    def test_cache_written_intraday_is_refetched_after_the_close(
        self, tmp_path, monkeypatch
    ):
        """10:30 写下的当天 K 线是半截的，晚上不能再当最终收盘价用。"""
        today = a_stock._market_today()
        cache = self._cache_file(
            tmp_path, monkeypatch,
            market_today=today,
            last_bar=today,
            mtime=datetime.combine(today, dtime(10, 30), tzinfo=MARKET_TZ),
        )
        self._phase(monkeypatch, "post_close")

        assert a_stock._ohlcv_cache_is_final(cache, pd.Timestamp(today)) is False

    def test_cache_written_after_the_close_is_reused(self, tmp_path, monkeypatch):
        today = a_stock._market_today()
        cache = self._cache_file(
            tmp_path, monkeypatch,
            market_today=today,
            last_bar=today,
            mtime=datetime.combine(today, dtime(16, 5), tzinfo=MARKET_TZ),
        )
        self._phase(monkeypatch, "post_close")

        assert a_stock._ohlcv_cache_is_final(cache, pd.Timestamp(today)) is True

    def test_cache_with_todays_bar_is_refetched_during_the_session(
        self, tmp_path, monkeypatch
    ):
        """盘中拿着含今日那根的缓存要重取：不重取就一直是首次写入那一刻的价格。

        判据是「写下来的那根能不能算最终值」——盘中任何时候写的都不算，所以盘中
        每次取数都会去源上要一次最新的；收盘后写下的才稳定复用。
        """
        today = a_stock._market_today()
        cache = self._cache_file(
            tmp_path, monkeypatch,
            market_today=today,
            last_bar=today,
            mtime=datetime.combine(today, dtime(10, 30), tzinfo=MARKET_TZ),
        )
        self._phase(monkeypatch, "in_session")

        assert a_stock._ohlcv_cache_is_final(cache, pd.Timestamp(today)) is False

    def test_cache_file_from_an_earlier_day_is_never_reused(
        self, tmp_path, monkeypatch
    ):
        today = a_stock._market_today()
        cache = self._cache_file(
            tmp_path, monkeypatch,
            market_today=today,
            last_bar=today - timedelta(days=1),
            mtime=datetime.combine(
                today - timedelta(days=1), dtime(19, 0), tzinfo=MARKET_TZ
            ),
        )
        self._phase(monkeypatch, "in_session")

        assert a_stock._ohlcv_cache_is_final(cache, pd.Timestamp(today)) is False

    def test_pre_open_cache_without_todays_bar_is_reused(self, tmp_path, monkeypatch):
        """盘前：缓存里没有今天那根很正常，不该每次重取。"""
        today = a_stock._market_today()
        cache = self._cache_file(
            tmp_path, monkeypatch,
            market_today=today,
            last_bar=today - timedelta(days=1),
            mtime=datetime.combine(today, dtime(8, 0), tzinfo=MARKET_TZ),
        )
        self._phase(monkeypatch, "pre_open")

        assert (
            a_stock._ohlcv_cache_is_final(cache, pd.Timestamp(today - timedelta(days=1)))
            is True
        )

    def test_after_close_cache_without_todays_bar_is_refetched(
        self, tmp_path, monkeypatch
    ):
        """收盘后缓存里还没有今天那根 → 必须重取一次才能补上今天的日线。"""
        today = a_stock._market_today()
        cache = self._cache_file(
            tmp_path, monkeypatch,
            market_today=today,
            last_bar=today - timedelta(days=1),
            mtime=datetime.combine(today, dtime(8, 0), tzinfo=MARKET_TZ),
        )
        self._phase(monkeypatch, "post_close")

        assert (
            a_stock._ohlcv_cache_is_final(cache, pd.Timestamp(today - timedelta(days=1)))
            is False
        )
