"""Guards for the Web UI's pause / stop controls.

`ProgressTracker` grew a complete cancellation API — `pause`, `resume`,
`request_stop`, `wait_if_paused`, `mark_stopped` — and `web/runner.py` grew the
matching handling (`_close_and_discard`, `clear_checkpoint`, `mark_stopped`).
**Nothing ever called any of it**, so the whole path was unreachable: a run could
not be stopped, and pressing a history entry or closing the tab left the
background thread streaming paid LLM calls to completion with nobody watching.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from web.progress import (
    ANALYST_STAGES,
    PIPELINE_ONLY_STAGES,
    PIPELINE_STAGES,
    ProgressTracker,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _running_tracker() -> ProgressTracker:
    tracker = ProgressTracker(ticker="600519", trade_date="2026-05-12")
    tracker.is_running = True
    return tracker


@pytest.mark.unit
class TestPauseResume:
    def test_pause_then_resume(self):
        tracker = _running_tracker()

        assert tracker.pause() is True
        assert tracker.is_paused is True
        assert tracker.resume() is True
        assert tracker.is_paused is False

    def test_pause_is_refused_when_not_running(self):
        tracker = ProgressTracker(ticker="x")
        assert tracker.pause() is False
        assert tracker.is_paused is False

    def test_pause_is_refused_when_already_paused(self):
        tracker = _running_tracker()
        tracker.pause()
        assert tracker.pause() is False

    def test_resume_is_refused_when_not_paused(self):
        tracker = _running_tracker()
        assert tracker.resume() is False

    def test_wait_if_paused_blocks_then_releases(self):
        """暂停必须真的把 runner 挡住，恢复后继续。"""
        tracker = _running_tracker()
        tracker.pause()

        released = threading.Event()

        def waiter():
            tracker.wait_if_paused()
            released.set()

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()

        # 暂停期间必须仍然阻塞
        assert released.wait(timeout=0.3) is False, "暂停没有挡住 runner 线程"

        tracker.resume()
        assert released.wait(timeout=2.0) is True, "恢复后 runner 没有被放行"


@pytest.mark.unit
class TestRequestStop:
    def test_stop_sets_the_flag_and_clears_progress(self):
        tracker = _running_tracker()
        tracker.mark_stage_done("market", "report")
        assert tracker.stage_status("market") == "done"

        assert tracker.request_stop() is True
        assert tracker.stop_requested is True
        assert tracker.is_paused is False
        # 停止后立刻清掉用户可见的进度
        assert tracker.completed_stages == []
        assert tracker.report_snapshot() == {}

    def test_progress_writes_are_ignored_after_a_stop_request(self):
        """停止后 runner 仍可能走完当前步骤，这些写入必须被丢弃。"""
        tracker = _running_tracker()
        tracker.request_stop()

        tracker.mark_stage_done("market", "late report")
        tracker.mark_stage_active("news")
        tracker.update_stats(5, 5, 5, 5)

        assert tracker.stage_status("market") == "pending"
        assert tracker.report_snapshot() == {}
        assert tracker.llm_calls == 0

    def test_stop_is_refused_when_not_running(self):
        tracker = ProgressTracker(ticker="x")
        assert tracker.request_stop() is False

    def test_stop_is_refused_after_completion(self):
        tracker = _running_tracker()
        tracker.mark_complete({"final_trade_decision": "x"}, "Hold")
        assert tracker.request_stop() is False

    def test_paused_runner_is_released_by_a_stop_request(self):
        """暂停中按停止也必须能走完退出路径，否则线程永远挂着。"""
        tracker = _running_tracker()
        tracker.pause()
        tracker.request_stop()

        # wait_if_paused 不该再把 runner 挡住
        done = threading.Event()
        threading.Thread(
            target=lambda: (tracker.wait_if_paused(), done.set()), daemon=True
        ).start()
        assert done.wait(timeout=2.0) is True


@pytest.mark.unit
class TestSnapshots:
    def test_report_snapshot_is_a_copy(self):
        tracker = _running_tracker()
        tracker.mark_stage_done("market", "report")

        snapshot = tracker.report_snapshot()
        snapshot["market"] = "tampered"

        assert tracker.report_snapshot()["market"] == "report"

    def test_stage_snapshot_does_not_deadlock(self):
        """`stage_snapshot` 曾在锁内调用 `stage_status`——同一把**非重入**锁。

        那会当场死锁。用带超时的线程跑，避免真把测试套件挂住。
        """
        tracker = _running_tracker()
        tracker.mark_stage_done("market", "r")
        tracker.mark_stage_active("news")

        result: dict = {}

        def run():
            result["value"] = tracker.stage_snapshot()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(timeout=5)

        assert not thread.is_alive(), "stage_snapshot 死锁了（锁内重复获取同一把锁）"
        assert result["value"]["market"] == "done"
        assert result["value"]["news"] == "active"
        assert result["value"]["pm"] == "pending"
        assert set(result["value"]) == {s["id"] for s in PIPELINE_STAGES}

    def test_stage_snapshot_reflects_a_stop(self):
        tracker = _running_tracker()
        tracker.mark_stage_done("market", "r")
        tracker.request_stop()

        assert set(tracker.stage_snapshot().values()) == {"pending"}


@pytest.mark.unit
class TestStageGrouping:
    """`PIPELINE_STAGES[:7]` / `[7:]` 把两个分析师归到了 PIPELINE 标题下。"""

    def test_nine_analysts_and_five_pipeline_stages(self):
        assert len(ANALYST_STAGES) == 9, f"分析师阶段数不对: {len(ANALYST_STAGES)}"
        assert len(PIPELINE_ONLY_STAGES) == 5
        assert len(ANALYST_STAGES) + len(PIPELINE_ONLY_STAGES) == len(PIPELINE_STAGES)

    def test_the_two_newest_analysts_are_grouped_as_analysts(self):
        ids = {s["id"] for s in ANALYST_STAGES}
        assert {"volume_price", "macro"} <= ids, (
            "量价/宏观分析师被归到 PIPELINE 组了（旧代码用位置切片）"
        )

    def test_no_stage_is_missing_a_group(self):
        for stage in PIPELINE_STAGES:
            assert stage.get("group") in {"analyst", "pipeline"}, stage

    def test_the_panel_does_not_slice_positionally(self):
        src = (REPO_ROOT / "web" / "components" / "progress_panel.py").read_text(
            encoding="utf-8"
        )
        assert "PIPELINE_STAGES[:" not in src, "又用位置切片分组了"
        assert "stage_reports[" not in src, (
            "面板又直接索引 stage_reports —— 那是锁外的检查后索引，会 KeyError"
        )


@pytest.mark.unit
class TestThePanelActuallyOffersTheControls:
    """面板必须真的接上那三个方法。

    面板需要 Streamlit 运行时才能渲染，所以这里做源码级断言——被钉住的是
    「UI 有没有接线」，而没有别的办法检查一个纯渲染函数。
    """

    @staticmethod
    def _source() -> str:
        return (REPO_ROOT / "web" / "components" / "progress_panel.py").read_text(
            encoding="utf-8"
        )

    def test_controls_call_the_tracker(self):
        src = self._source()
        for call in ("tracker.pause()", "tracker.resume()", "tracker.request_stop()"):
            assert call in src, f"面板没有调用 {call} —— 按钮形同虚设"

    def test_stopping_sets_a_notice_for_the_idle_screen(self):
        """停止会清空 tracker，不提示的话用户不知道报告去哪了。"""
        assert 'st.session_state["analysis_stopped"] = True' in self._source()
        app = (REPO_ROOT / "web" / "app.py").read_text(encoding="utf-8")
        assert 'pop("analysis_stopped", None)' in app

    def test_controls_are_rendered_while_running(self):
        src = self._source()
        assert "def _render_controls(" in src
        assert "_render_controls(tracker)" in src
