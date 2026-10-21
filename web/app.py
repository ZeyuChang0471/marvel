"""MARVEL — Multi-Agent Research & Valuation Engine for Long-short（Streamlit Web UI）."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / ".env")

from marvel.default_config import DEFAULT_CONFIG  # noqa: E402

from web.components.progress_panel import render_progress  # noqa: E402
from web.components.report_viewer import render_report  # noqa: E402
from web.components.sidebar import render_sidebar, session_api_key  # noqa: E402
from web.diagnostics import arm_stack_dumps  # noqa: E402
from web.history import clear_incomplete_task, extract_signal, load_analysis  # noqa: E402
from web.progress import ProgressTracker  # noqa: E402
from web.runner import run_analysis_in_thread  # noqa: E402
from web.theme import inject_theme  # noqa: E402

# Armed here rather than in web/launch.py: that module spawns this file as a *child*
# process, and a dump armed in the parent would never show the child's frames.
# Off unless MARVEL_WEB_DUMP_STACKS is set — see web/diagnostics.py.
arm_stack_dumps()

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="MARVEL · A股投研分析",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ───────────────────────────────────────────────────────────────

inject_theme()


# ── Build config ─────────────────────────────────────────────────────────────

def _build_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    provider = st.session_state.get("llm_provider", "minimax")
    config["llm_provider"] = provider
    config["deep_think_llm"] = st.session_state.get("deep_think_llm", "MiniMax-M2.7")
    config["quick_think_llm"] = st.session_state.get("quick_think_llm", "MiniMax-M2.7-highspeed")
    # Optional third-party / proxy endpoint. Sidebar input wins, else .env BACKEND_URL.
    backend_url = (st.session_state.get("llm_base_url") or os.getenv("BACKEND_URL") or "").strip()
    config["backend_url"] = backend_url or None
    # The key this browser session typed, if any. It is passed to the client
    # through the per-run config rather than copied into os.environ, so two
    # sessions analysing different stocks cannot see or spend each other's key.
    # When absent the client falls back to the operator's .env variable.
    config["api_key"] = session_api_key(provider)
    config["data_vendors"] = {
        "core_stock_apis": "a_stock",
        "technical_indicators": "a_stock",
        "fundamental_data": "a_stock",
        "news_data": "a_stock",
        "signal_data": "a_stock",
    }
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["output_language"] = "Chinese"
    return config


# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    render_sidebar()


# ── Handle "Start Analysis" trigger ──────────────────────────────────────────

start_req = st.session_state.pop("start_analysis", None)
if start_req:
    tracker = ProgressTracker(
        ticker=start_req["ticker"],
        trade_date=start_req["trade_date"],
    )
    st.session_state["tracker"] = tracker
    run_analysis_in_thread(
        ticker=start_req["ticker"],
        trade_date=start_req["trade_date"],
        config=_build_config(),
        tracker=tracker,
    )


# ── Main area state machine ─────────────────────────────────────────────────

tracker: ProgressTracker | None = st.session_state.get("tracker")
viewing_history: str | None = st.session_state.get("viewing_history")

# State 1: Viewing a historical analysis
if viewing_history:
    try:
        state = load_analysis(viewing_history)
        signal = extract_signal(state)
        ticker = Path(viewing_history).parent.parent.name
        trade_date = Path(viewing_history).stem.replace("full_states_log_", "")
        render_report(
            state, ticker, trade_date, signal,
            cache_key=f"history:{viewing_history}",
        )
    except Exception as exc:
        st.error(f"加载失败: {exc}")

# State 2: Analysis running
elif tracker and tracker.is_running:
    render_progress(tracker)

    # A wedged run must not be a dead end. Before this, the only way out of the
    # running state was restarting the whole app: the start button is disabled
    # while a run is in flight, and nothing here ever offered to abandon one.
    if tracker.is_stalled():
        st.warning(
            f"⚠️ 分析已经 {int(tracker.stalled_for)} 秒没有任何进展"
            "（可能卡在某个数据源或模型调用上）。"
        )
        st.caption(
            "可以先看下面的「数据源耗时」，判断是哪个源慢；确认无望再强制复位。"
        )
        if st.button("🛑 强制复位（放弃本次分析并清理断点）", type="primary"):
            ticker_stuck, date_stuck = tracker.ticker, tracker.trade_date
            tracker.force_reset("用户在界面强制复位")
            try:
                clear_incomplete_task(ticker_stuck, date_stuck)
            except Exception as exc:  # noqa: BLE001 — 复位本身不能失败
                st.caption(f"（清理断点记录时出错，可忽略：{exc}）")
            st.session_state.pop("tracker", None)
            st.session_state["analysis_reset"] = True
            st.rerun()
        st.caption(tracker.stalled_hint)

    time.sleep(2)
    st.rerun()

# State 3: Analysis complete
elif tracker and tracker.is_complete:
    render_report(
        tracker.final_state,
        tracker.ticker,
        tracker.trade_date,
        tracker.signal,
        elapsed=tracker.elapsed,
        # start_time is unique per run, so a re-run of the same ticker/date in one
        # session cannot collide with the previous report's cached exports.
        cache_key=f"run:{tracker.ticker}:{tracker.trade_date}:{tracker.start_time}",
    )

# State 4: Analysis errored
elif tracker and tracker.error:
    st.error(f"分析失败: {tracker.error}")
    if st.button("重试"):
        st.session_state.pop("tracker", None)
        st.rerun()

# State 0: Idle — welcome screen
else:
    # A stopped run clears the tracker (mark_stopped), so without this the user
    # would just see the welcome screen again and wonder where the report went.
    if st.session_state.pop("analysis_stopped", None):
        st.info("上一次分析已被你停止，未完成的产物已清理。")
    if st.session_state.pop("analysis_reset", None):
        st.warning(
            "上一次分析因为长时间无进展被你强制复位：断点记录已清理，可以重新开始。"
            "如果它反复卡在同一阶段，请看「数据源耗时」，那能指出是哪个源。"
        )
    st.markdown(
        """
        <div class="mv-cover">
            <div class="mv-cover-rule"></div>
            <div class="mv-cover-word">MARVEL</div>
            <div class="mv-cover-sub">A股多Agent投研系统</div>
            <p class="mv-cover-lede">
                九位分析师分工取证，经质量门控、多空辩论与三方风控，
                收束成一份可读、可复核的研究报告。
            </p>
            <div class="mv-cover-hint">在左侧输入股票代码，开始一份新的研究。</div>
            <div class="mv-cover-foot">
                MARVEL · 项目维护 · fork of simonlin1212/TradingAgents-Astock<br>
                仅供学习研究，不构成投资建议。
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
