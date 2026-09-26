"""Real-time progress display for the analysis pipeline."""

from __future__ import annotations

import streamlit as st

from web.progress import ANALYST_STAGES, PIPELINE_ONLY_STAGES, ProgressTracker


def _status_badge(status: str) -> str:
    if status == "done":
        return '<span style="color:#22c55e; font-size:1.3rem;">●</span>'
    if status == "active":
        return '<span style="color:#C8102E; font-size:1.3rem;">◉</span>'
    return '<span style="color:#333; font-size:1.3rem;">○</span>'


def _format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _render_controls(tracker: ProgressTracker) -> None:
    """Pause / resume / stop.

    These call tracker methods that existed from the start but had **no
    callers**, so the entire cancel path was dead code: a run could not be
    stopped, and `web/runner.py`'s `_close_and_discard` / `mark_stopped`
    handling was unreachable. For a pipeline that spends real money on LLM
    calls per run, being unable to stop one is a cost leak, not just a missing
    button.
    """
    c1, c2, _ = st.columns([1, 1, 3])

    if tracker.is_paused:
        if c1.button("▶ 继续", use_container_width=True):
            tracker.resume()
            st.rerun()
    elif c1.button("⏸ 暂停", use_container_width=True):
        tracker.pause()
        st.rerun()

    if c2.button("⏹ 停止分析", use_container_width=True):
        # Set the notice before requesting the stop: mark_stopped() clears the
        # tracker, so afterwards there is no way to tell "you stopped it" from
        # "it never ran".
        st.session_state["analysis_stopped"] = True
        tracker.request_stop()
        st.rerun()

    if tracker.is_paused:
        st.info("已暂停：当前流式步骤结束后会停住，点「继续」恢复。")


def _render_stage_row(stages: list[dict[str, str]], statuses: dict[str, str]) -> None:
    cols = st.columns(len(stages))
    for col, stage in zip(cols, stages):
        status = statuses.get(stage["id"], "pending")
        badge = _status_badge(status)
        label_color = (
            "#f5f1eb" if status == "active"
            else "#888" if status == "pending"
            else "#22c55e"
        )
        col.markdown(
            f"""
            <div style="text-align:center; padding:0.5rem 0;">
                {badge}<br>
                <span style="font-size:0.75rem; color:{label_color};">{stage['name']}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_progress(tracker: ProgressTracker) -> None:
    """Render the pipeline progress panel."""

    st.markdown(
        f"""
        <div style="text-align:center; margin:1rem 0 0.5rem;">
            <span style="font-size:1.6rem; font-weight:700; color:#f5f1eb;">
                {"分析已暂停" if tracker.is_paused else "分析进行中"}
            </span>
            <span style="font-size:1.1rem; color:#888; margin-left:0.8rem;">
                {tracker.ticker}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    completed = len(tracker.completed_stages)
    total = len(ANALYST_STAGES) + len(PIPELINE_ONLY_STAGES)
    pct = completed / total if total else 0
    st.progress(pct, text=f"{completed}/{total} 阶段完成  ·  {_format_time(tracker.elapsed)}")

    statuses = tracker.stage_snapshot()

    st.markdown(
        '<div style="margin:0.5rem 0 0.3rem; font-size:0.85rem; color:#888;">ANALYSTS</div>',
        unsafe_allow_html=True,
    )
    _render_stage_row(ANALYST_STAGES, statuses)

    st.markdown(
        '<div style="margin:0.8rem 0 0.3rem; font-size:0.85rem; color:#888;">PIPELINE</div>',
        unsafe_allow_html=True,
    )
    _render_stage_row(PIPELINE_ONLY_STAGES, statuses)

    st.markdown("---")

    _render_controls(tracker)

    st.markdown("---")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("LLM 调用", tracker.llm_calls)
    c2.metric("工具调用", tracker.tool_calls)
    c3.metric("输入 Tokens", f"{tracker.tokens_in:,}")
    c4.metric("输出 Tokens", f"{tracker.tokens_out:,}")

    if tracker.error:
        st.error(f"错误: {tracker.error}")

    # Snapshot under the tracker's lock: the runner thread writes this dict and
    # both request_stop() and mark_stopped() clear it. Reading it directly meant
    # a membership test followed by an index that could raise KeyError.
    reports = tracker.report_snapshot()
    completed_reports = [
        (stage["name"], stage["icon"], reports[stage["id"]])
        for stage in ANALYST_STAGES + PIPELINE_ONLY_STAGES
        if stage["id"] in reports
    ]

    if completed_reports:
        st.markdown(
            '<div style="margin:0.5rem 0 0.3rem; font-size:0.85rem; color:#888;">'
            f"REPORTS ({len(completed_reports)})</div>",
            unsafe_allow_html=True,
        )
        for name, icon, report in reversed(completed_reports):
            is_latest = (name == completed_reports[-1][0])
            with st.expander(f"{icon} {name}", expanded=is_latest):
                st.markdown(report[:3000])
