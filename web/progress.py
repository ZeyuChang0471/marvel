"""Thread-safe progress tracker shared between the background runner and Streamlit UI."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


PIPELINE_STAGES: list[dict[str, str]] = [
    # `group` exists so the UI can label the stages without positional slicing:
    # progress_panel.py used `PIPELINE_STAGES[:7]` / `[7:]`, which put the two
    # most recently added analysts (量价分析, 宏观板块) under the "PIPELINE"
    # heading while the code graded them as analysts.
    {"id": "market", "name": "技术分析", "icon": "📊", "report_key": "market_report", "group": "analyst"},
    {"id": "social", "name": "情绪分析", "icon": "💬", "report_key": "sentiment_report", "group": "analyst"},
    {"id": "news", "name": "新闻舆情", "icon": "📰", "report_key": "news_report", "group": "analyst"},
    {"id": "fundamentals", "name": "基本面", "icon": "📋", "report_key": "fundamentals_report", "group": "analyst"},
    {"id": "policy", "name": "政策分析", "icon": "🏛️", "report_key": "policy_report", "group": "analyst"},
    {"id": "hot_money", "name": "游资追踪", "icon": "🔥", "report_key": "hot_money_report", "group": "analyst"},
    {"id": "lockup", "name": "解禁监控", "icon": "🔒", "report_key": "lockup_report", "group": "analyst"},
    {"id": "volume_price", "name": "量价分析", "icon": "📉", "report_key": "volume_price_report", "group": "analyst"},
    {"id": "macro", "name": "宏观板块", "icon": "🌐", "report_key": "macro_report", "group": "analyst"},
    {"id": "quality_gate", "name": "质量门控", "icon": "✅", "report_key": "data_quality_summary", "group": "pipeline"},
    {"id": "debate", "name": "多空辩论", "icon": "⚔️", "report_key": "investment_plan", "group": "pipeline"},
    {"id": "trader", "name": "交易决策", "icon": "💹", "report_key": "trader_investment_plan", "group": "pipeline"},
    {"id": "risk", "name": "风控评估", "icon": "🛡️", "report_key": "risk_debate_state", "group": "pipeline"},
    {"id": "pm", "name": "最终决策", "icon": "👔", "report_key": "final_trade_decision", "group": "pipeline"},
]

ANALYST_STAGES: list[dict[str, str]] = [
    s for s in PIPELINE_STAGES if s.get("group") == "analyst"
]
PIPELINE_ONLY_STAGES: list[dict[str, str]] = [
    s for s in PIPELINE_STAGES if s.get("group") != "analyst"
]

STAGE_IDS = [s["id"] for s in PIPELINE_STAGES]


@dataclass
class ProgressTracker:
    """Mutable state container updated by the runner thread, read by the UI."""

    ticker: str = ""
    trade_date: str = ""
    start_time: float = field(default_factory=time.time)

    is_running: bool = False
    is_complete: bool = False
    is_paused: bool = False
    stop_requested: bool = False
    error: Optional[str] = None

    current_stage: str = ""
    completed_stages: list[str] = field(default_factory=list)
    stage_reports: dict[str, str] = field(default_factory=dict)

    final_state: dict[str, Any] = field(default_factory=dict)
    signal: str = ""

    llm_calls: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _pause_gate: threading.Event = field(
        default_factory=threading.Event,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self._pause_gate.set()

    def pause(self) -> bool:
        """Pause pipeline advancement after the current streamed step finishes."""
        with self._lock:
            if (
                not self.is_running
                or self.is_complete
                or self.error
                or self.is_paused
                or self.stop_requested
            ):
                return False
            self.is_paused = True
            self._pause_gate.clear()
            return True

    def resume(self) -> bool:
        """Allow the runner thread to continue to the next streamed step."""
        with self._lock:
            if not self.is_paused or self.stop_requested:
                return False
            self.is_paused = False
            self._pause_gate.set()
            return True

    def request_stop(self) -> bool:
        """Request cancellation and clear user-visible progress immediately."""
        with self._lock:
            if not self.is_running or self.is_complete or self.error or self.stop_requested:
                return False
            self.stop_requested = True
            self.is_paused = False
            self.current_stage = ""
            self.completed_stages.clear()
            self.stage_reports.clear()
            self.final_state = {}
            self.signal = ""
            self.llm_calls = 0
            self.tool_calls = 0
            self.tokens_in = 0
            self.tokens_out = 0
            self._pause_gate.set()
            return True

    def wait_if_paused(self) -> None:
        self._pause_gate.wait()

    def report_snapshot(self) -> dict[str, str]:
        """Copy of the completed stage reports, taken under the lock.

        The UI must not iterate `stage_reports` directly: the runner thread
        writes it, and both `request_stop` and `mark_stopped` **clear** it. A
        membership test followed by an index outside the lock is a `KeyError`
        waiting for exactly that moment.
        """
        with self._lock:
            return dict(self.stage_reports)

    def stage_snapshot(self) -> dict[str, str]:
        """Copy of stage id -> status, read under a single lock acquisition.

        Deliberately does **not** call `stage_status()`: that acquires the same
        non-reentrant lock, so calling it from inside this one would deadlock.
        """
        with self._lock:
            completed = set(self.completed_stages)
            current = self.current_stage

        return {
            stage["id"]: (
                "done" if stage["id"] in completed
                else "active" if stage["id"] == current
                else "pending"
            )
            for stage in PIPELINE_STAGES
        }

    def mark_stopped(self) -> None:
        with self._lock:
            self.is_running = False
            self.is_complete = False
            self.is_paused = False
            self.stop_requested = False
            self.error = None
            self.current_stage = ""
            self.completed_stages.clear()
            self.stage_reports.clear()
            self.final_state = {}
            self.signal = ""
            self.llm_calls = 0
            self.tool_calls = 0
            self.tokens_in = 0
            self.tokens_out = 0
            self._pause_gate.set()

    def mark_stage_active(self, stage_id: str) -> None:
        with self._lock:
            if self.stop_requested:
                return
            self.current_stage = stage_id

    def mark_stage_done(self, stage_id: str, report: str = "") -> None:
        with self._lock:
            if self.stop_requested:
                return
            if stage_id not in self.completed_stages:
                self.completed_stages.append(stage_id)
            if report:
                self.stage_reports[stage_id] = report
            self.current_stage = ""

    def mark_complete(self, final_state: dict, signal: str) -> None:
        with self._lock:
            self.final_state = final_state
            self.signal = signal
            self.is_running = False
            self.is_complete = True
            self.is_paused = False
            self.stop_requested = False
            self._pause_gate.set()

    def mark_error(self, err: str) -> None:
        with self._lock:
            self.error = err
            self.is_running = False
            self.is_paused = False
            self.stop_requested = False
            self._pause_gate.set()

    def update_stats(self, llm: int, tool: int, tok_in: int, tok_out: int) -> None:
        with self._lock:
            if self.stop_requested:
                return
            self.llm_calls = llm
            self.tool_calls = tool
            self.tokens_in = tok_in
            self.tokens_out = tok_out

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    def stage_status(self, stage_id: str) -> str:
        with self._lock:
            if stage_id in self.completed_stages:
                return "done"
            if stage_id == self.current_stage:
                return "active"
            return "pending"
