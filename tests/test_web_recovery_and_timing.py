"""A wedged run must be recoverable, diagnosable, and cheap to re-render.

Written after the Web UI froze at 99% CPU for 14 minutes and the only recovery was
killing the process. Three separate defects made that worse than it needed to be:

1. nothing recorded when a run last made progress, so "stuck" and "slow" were
   indistinguishable, and the running state offered no way out at all;
2. no data fetch was timed, so there was no way to guess *which* blocking call the
   run was sitting in;
3. the results page re-hashed the entire pipeline state on every rerun to look up
   its cached exports.
"""

from __future__ import annotations

import time

import pytest

from web.progress import ProgressTracker, stall_timeout_s


@pytest.fixture(autouse=True)
def _fast_stall_timeout(monkeypatch):
    monkeypatch.setenv("MARVEL_STALL_TIMEOUT_S", "5")
    yield


@pytest.mark.unit
class TestStallDetection:
    def test_a_fresh_run_is_not_stalled(self):
        tracker = ProgressTracker()
        tracker.is_running = True

        assert tracker.is_stalled() is False
        assert tracker.stalled_for < 1

    def test_no_progress_for_longer_than_the_limit_is_stalled(self):
        tracker = ProgressTracker()
        tracker.is_running = True
        tracker.last_progress_at = time.time() - 30

        assert tracker.is_stalled() is True
        assert tracker.stalled_for >= 29

    def test_progress_resets_the_clock(self):
        tracker = ProgressTracker()
        tracker.is_running = True
        tracker.last_progress_at = time.time() - 30
        assert tracker.is_stalled() is True

        tracker.mark_stage_active("news")

        assert tracker.is_stalled() is False, "有进展之后不该再报卡死"

    def test_stats_updates_count_as_progress(self):
        tracker = ProgressTracker()
        tracker.is_running = True
        tracker.last_progress_at = time.time() - 30

        tracker.update_stats(1, 2, 3, 4)

        assert tracker.is_stalled() is False

    def test_stage_completion_counts_as_progress(self):
        tracker = ProgressTracker()
        tracker.is_running = True
        tracker.mark_stage_active("news")
        tracker.last_progress_at = time.time() - 30

        tracker.mark_stage_done("news", "报告正文")

        assert tracker.is_stalled() is False

    def test_a_finished_run_is_never_stalled(self):
        for finish in ("complete", "error", "idle"):
            tracker = ProgressTracker()
            tracker.last_progress_at = time.time() - 1000
            if finish == "complete":
                tracker.mark_complete({"x": 1}, "Hold")
            elif finish == "error":
                tracker.mark_error("boom")
            else:
                tracker.is_running = False

            assert tracker.is_stalled() is False, f"{finish} 状态不该报卡死"

    def test_a_paused_run_is_not_stalled(self):
        """Pausing means doing nothing on purpose — warning about it trains users
        to ignore the warning."""
        tracker = ProgressTracker()
        tracker.is_running = True
        assert tracker.pause() is True
        tracker.last_progress_at = time.time() - 1000

        assert tracker.is_stalled() is False

    def test_the_threshold_is_configurable(self):
        tracker = ProgressTracker()
        tracker.is_running = True
        tracker.last_progress_at = time.time() - 30

        assert tracker.is_stalled(threshold=10) is True
        assert tracker.is_stalled(threshold=600) is False

    def test_the_hint_reads_in_seconds_then_minutes(self):
        tracker = ProgressTracker()
        tracker.is_running = True
        tracker.last_progress_at = time.time() - 30
        assert "秒前" in tracker.stalled_hint

        tracker.last_progress_at = time.time() - 300
        assert "分钟前" in tracker.stalled_hint


@pytest.mark.unit
class TestForceReset:
    def test_reset_ends_the_running_state(self):
        tracker = ProgressTracker()
        tracker.is_running = True
        tracker.mark_stage_active("debate")

        assert tracker.force_reset("测试") is True

        assert tracker.is_running is False
        assert tracker.reset_forced is True
        assert tracker.reset_reason == "测试"
        assert tracker.error, "复位后应给出可见的原因"

    def test_reset_clears_partial_results(self):
        tracker = ProgressTracker()
        tracker.is_running = True
        tracker.final_state = {"news_report": "半截"}
        tracker.signal = "Hold"

        tracker.force_reset("测试")

        assert tracker.final_state == {}
        assert tracker.signal == ""

    def test_reset_is_idempotent_and_refuses_when_not_running(self):
        tracker = ProgressTracker()

        assert tracker.force_reset("测试") is False, "没在跑就不该报复位成功"

    def test_reset_unblocks_a_paused_run(self):
        """A paused run holds the pause gate; reset must not leave it waiting."""
        tracker = ProgressTracker()
        tracker.is_running = True
        tracker.pause()

        tracker.force_reset("测试")

        assert tracker.is_paused is False
        assert tracker._pause_gate.is_set(), "复位后 wait_if_paused 会永久阻塞"

    def test_reset_marks_stop_requested_so_the_worker_bails_out(self):
        tracker = ProgressTracker()
        tracker.is_running = True

        tracker.force_reset("测试")

        assert tracker.stop_requested is True


@pytest.mark.unit
class TestStallTimeoutConfig:
    def test_default_is_five_minutes(self, monkeypatch):
        monkeypatch.delenv("MARVEL_STALL_TIMEOUT_S", raising=False)
        assert stall_timeout_s() == 300.0

    def test_a_bad_value_falls_back_instead_of_crashing(self, monkeypatch):
        monkeypatch.setenv("MARVEL_STALL_TIMEOUT_S", "not-a-number")
        assert stall_timeout_s() == 300.0


@pytest.mark.unit
class TestSourceTiming:
    def setup_method(self):
        from marvel.dataflows import timing

        timing.reset()

    def test_a_call_is_recorded_with_its_name_and_duration(self):
        from marvel.dataflows import timing

        with timing.timed("unit-test-source"):
            time.sleep(0.02)

        samples = timing.recent()
        assert samples[-1][0] == "unit-test-source"
        assert samples[-1][1] >= 0.02

    def test_a_failing_call_is_still_recorded_and_the_error_propagates(self):
        """A source that *times out* is exactly the one you need to see."""
        from marvel.dataflows import timing

        with pytest.raises(RuntimeError):
            with timing.timed("broken-source"):
                raise RuntimeError("boom")

        assert any(name == "broken-source" for name, _, _ in timing.recent())

    def test_slowest_sorts_descending(self):
        from marvel.dataflows import timing

        timing.record("fast", 0.1)
        timing.record("slow", 9.0)
        timing.record("medium", 3.0)

        assert [name for name, _, _ in timing.slowest(2)] == ["slow", "medium"]

    def test_the_ring_is_bounded(self):
        from marvel.dataflows import timing

        for i in range(timing._MAX_SAMPLES * 3):
            timing.record(f"s{i}", 0.01)

        assert len(timing.recent(limit=1000)) == timing._MAX_SAMPLES

    def test_slow_calls_log_a_warning(self, caplog):
        from marvel.dataflows import timing

        with caplog.at_level("WARNING"):
            timing.record("explicit", 0.5)
            with timing.timed("slow-unit-source", slow_over=0.0):
                pass

        assert "slow-unit-source" in caplog.text

    def test_summary_is_empty_without_samples_and_names_the_slowest(self):
        from marvel.dataflows import timing

        assert timing.summary() == ""
        timing.record("a", 1.0)
        timing.record("b", 8.0)
        assert "b 8.0s" in timing.summary()


@pytest.mark.unit
class TestEastmoneyAndNewsAreInstrumented:
    def test_em_get_records_the_endpoint(self, monkeypatch):
        """Every Eastmoney endpoint funnels through _em_get, so one wrapper covers
        all of them — and the label names the endpoint, not just 'eastmoney'."""
        from marvel.dataflows import a_stock, timing

        timing.reset()

        class _Resp:
            status_code = 200

            def json(self):
                return {}

        monkeypatch.setattr(a_stock._EM_SESSION, "get", lambda *a, **k: _Resp())

        a_stock._em_get("https://push2.eastmoney.com/api/qt/stock/get", params={})

        assert any("push2" in name for name, _, _ in timing.recent()), timing.recent()

    def test_each_news_source_is_timed_separately(self, monkeypatch):
        from marvel.dataflows import a_stock, timing

        timing.reset()
        monkeypatch.setattr(a_stock, "_fetch_news_announcements", lambda code, **k: [])
        monkeypatch.setattr(a_stock, "_fetch_news_research", lambda code, **k: [])
        monkeypatch.setattr(a_stock, "_fetch_news_sina", lambda code, **k: [])

        a_stock._collect_stock_news("600487", "2026-09-01", "2026-09-27")

        names = [name for name, _, _ in timing.recent()]
        assert "新闻源 公告" in names
        assert "新闻源 研报" in names
        assert "新闻源 新闻" in names


@pytest.mark.unit
class TestExportCacheIsKeyedCheaply:
    def setup_method(self):
        from web.components import report_viewer

        report_viewer._EXPORT_CACHE.clear()

    def test_exports_are_generated_once_per_key(self, monkeypatch):
        from web.components import report_viewer

        calls = {"md": 0, "pdf": 0}

        def fake_md(*a, **k):
            calls["md"] += 1
            return "# 报告"

        def fake_pdf(*a, **k):
            calls["pdf"] += 1
            return b"%PDF-1.4"

        monkeypatch.setattr(report_viewer, "generate_markdown", fake_md)
        monkeypatch.setattr(report_viewer, "generate_pdf", fake_pdf)

        state = {"news_report": "x" * 1000}
        for _ in range(5):
            entry = report_viewer._exports(state, "600487", "2026-09-27", "Hold", "run:1")

        assert calls == {"md": 1, "pdf": 1}, "每次重跑都重新生成导出，结果页会越来越慢"
        assert entry["markdown"] == "# 报告"
        assert entry["pdf"] == b"%PDF-1.4"

    def test_a_different_key_regenerates(self, monkeypatch):
        from web.components import report_viewer

        monkeypatch.setattr(report_viewer, "generate_markdown", lambda *a, **k: "md")
        monkeypatch.setattr(report_viewer, "generate_pdf", lambda *a, **k: b"pdf")

        report_viewer._exports({}, "600487", "2026-09-27", "Hold", "run:1")
        report_viewer._exports({}, "600519", "2026-09-27", "Hold", "run:2")

        assert len(report_viewer._EXPORT_CACHE) == 1, "只保留当前这一份报告"

    def test_a_pdf_failure_is_remembered_instead_of_retried_every_rerun(
        self, monkeypatch
    ):
        from web.components import report_viewer

        attempts = {"n": 0}

        def broken_pdf(*a, **k):
            attempts["n"] += 1
            raise RuntimeError("字体缺失")

        monkeypatch.setattr(report_viewer, "generate_markdown", lambda *a, **k: "md")
        monkeypatch.setattr(report_viewer, "generate_pdf", broken_pdf)

        for _ in range(4):
            entry = report_viewer._exports({}, "600487", "2026-09-27", "Hold", "run:1")

        assert attempts["n"] == 1, "PDF 失败被反复重试"
        assert entry["pdf"] is None
        assert "字体缺失" in entry["pdf_error"]

    def test_the_cache_key_never_hashes_the_state(self):
        """The defect was hashing `final_state`; assert the signature takes a key."""
        import inspect

        from web.components import report_viewer

        params = list(inspect.signature(report_viewer._exports).parameters)
        assert params[:4] == ["final_state", "ticker", "trade_date", "signal"]
        assert "cache_key" in params
