"""Render the completed analysis report with expandable sections and PDF download."""

from __future__ import annotations

import html
import re
import threading
from typing import Any

import streamlit as st

from web.pdf_export import generate_markdown, generate_pdf
from web.stock_display import normalize_stock_mentions, stock_display_parts
from web.theme import seal_label, signal_tone


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


# Export generation, cached per *report* rather than per identical final state.
#
# These were @st.cache_data functions taking `final_state` as an argument. Streamlit
# hashes every argument on every rerun, and this dict is the whole pipeline state —
# several hundred KB of nested reports, plus LangChain message objects in
# `messages`. The results page re-executes on every widget interaction, so that
# hashing ran constantly, and `max_entries=8` kept up to eight multi-hundred-KB PDFs
# pickled in the cache besides.
#
# Keyed on a cheap identity instead (path for a saved report, run identity for a live
# one), the lookup is O(1) and only the report being viewed is held. A PDF failure is
# remembered too — previously the failing generation was retried on every single
# rerun.
_EXPORT_CACHE: dict[str, dict[str, Any]] = {}
_EXPORT_CACHE_LOCK = threading.Lock()


def _exports(
    final_state: dict,
    ticker: str,
    trade_date: str,
    signal: str,
    cache_key: str | None = None,
) -> dict[str, Any]:
    """Return ``{"markdown": str, "pdf": bytes|None, "pdf_error": str|None}``."""
    key = cache_key or f"{ticker}:{trade_date}:{signal}"

    with _EXPORT_CACHE_LOCK:
        entry = _EXPORT_CACHE.get(key)
    if entry is not None:
        return entry

    entry = {
        "markdown": generate_markdown(final_state, ticker, trade_date, signal),
        "pdf": None,
        "pdf_error": None,
    }
    try:
        entry["pdf"] = generate_pdf(final_state, ticker, trade_date, signal)
    except Exception as exc:  # noqa: BLE001 — a PDF failure must not break the page
        entry["pdf_error"] = str(exc)

    with _EXPORT_CACHE_LOCK:
        _EXPORT_CACHE.clear()   # one report at a time keeps memory bounded
        _EXPORT_CACHE[key] = entry
    return entry


_ANALYST_SECTIONS = [
    ("market_report", "技术分析"),
    ("sentiment_report", "市场情绪"),
    ("news_report", "新闻舆情"),
    ("fundamentals_report", "基本面"),
    ("policy_report", "政策分析"),
    ("hot_money_report", "游资追踪"),
    ("lockup_report", "解禁与减持"),
    ("volume_price_report", "量价分析"),
    ("macro_report", "宏观与板块"),
]

_CN_SIGNAL = {
    "BUY": "买入",
    "OVERWEIGHT": "增持",
    "HOLD": "持有",
    "UNDERWEIGHT": "减持",
    "SELL": "卖出",
}


def _signal_name(signal: str) -> str:
    s = (signal or "").upper()
    for key, cn in _CN_SIGNAL.items():
        if key in s:
            return f"{cn}（{key.title()}）"
    return "持有（Hold）"


def _safe_filename_label(label: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", label).strip("_")
    return cleaned or "report"


def _display_report_text(text: Any, ticker: str, final_state: dict[str, Any]) -> str:
    """Strip thinking blocks, then normalise bare stock codes to code+name."""
    cleaned = _strip_think(str(text))
    return normalize_stock_mentions(cleaned, ticker, final_state)


def render_report(
    final_state: dict[str, Any],
    ticker: str,
    trade_date: str,
    signal: str,
    elapsed: float | None = None,
    cache_key: str | None = None,
) -> None:
    """Render the full analysis report.

    ``cache_key`` identifies *this* report cheaply (saved path, or run identity) so
    the exports are built once instead of re-hashing the whole state on every rerun.
    """
    exports = _exports(final_state, ticker, trade_date, signal, cache_key)

    tone = signal_tone(signal)
    seal = seal_label(signal)
    code, name = stock_display_parts(ticker, final_state)
    title = name or code
    ticker_label = f"{code} {name}".strip() if name else code
    code_html = (
        f'<span class="mv-code">{html.escape(code)}</span>' if name else ""
    )

    meta_bits = [f"分析日 {trade_date}"]
    if elapsed is not None:
        m, s = divmod(int(elapsed), 60)
        meta_bits.append(f"耗时 {m}:{s:02d}")
    meta_bits.append("AI 生成，仅供研究")
    meta_html = "".join(f"<span>{html.escape(bit)}</span>" for bit in meta_bits)

    st.markdown(
        f"""
        <header class="mv-masthead">
            <div>
                <div class="mv-kicker">
                    <span>沪深 A 股</span>
                    <span class="sep"></span>
                    <span>{html.escape(trade_date)}</span>
                </div>
                <h1 class="mv-title">{html.escape(title)}{code_html}</h1>
                <div class="mv-dek">研究结论：{html.escape(_signal_name(signal))}</div>
                <div class="mv-meta">{meta_html}</div>
            </div>
            <div class="mv-stamp" data-tone="{tone}" title="{html.escape(_signal_name(signal))}">
                {seal}
            </div>
        </header>
        """,
        unsafe_allow_html=True,
    )

    st.caption("本报告由 AI 自动生成，仅供学习研究，不构成投资建议。")

    # Markdown export always works (no font dependency); PDF is generated
    # lazily and guarded so a PDF/font failure never crashes the results page.
    col_md, col_pdf, col_spacer = st.columns([1, 1, 2])
    with col_md:
        md_text = exports["markdown"]
        st.download_button(
            "下载 Markdown",
            data=md_text.encode("utf-8"),
            file_name=f"MARVEL_{_safe_filename_label(ticker_label)}_{trade_date}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with col_pdf:
        if exports["pdf"] is not None:
            st.download_button(
                "下载 PDF",
                data=exports["pdf"],
                file_name=f"MARVEL_{_safe_filename_label(ticker_label)}_{trade_date}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        else:
            # The failure is remembered with the report: retrying it on every rerun
            # was wasted work and doubled as a way to make a slow page slower.
            st.button(
                "PDF 不可用",
                disabled=True,
                use_container_width=True,
                help=(
                    "PDF 生成失败，请改用 Markdown 导出。原因："
                    f"{exports['pdf_error']}"
                ),
            )

    st.markdown("---")

    inv_plan = final_state.get("investment_plan", "")
    if inv_plan:
        st.markdown("## 最终投资建议")
        st.markdown(_display_report_text(inv_plan, ticker, final_state))
        st.markdown("---")

    st.markdown("## 分析师报告")

    for key, title in _ANALYST_SECTIONS:
        content = final_state.get(key, "")
        if not content:
            continue
        with st.expander(title, expanded=False):
            st.markdown(_display_report_text(content, ticker, final_state))

    debate = final_state.get("investment_debate_state")
    if debate and isinstance(debate, dict):
        st.markdown("## 多空辩论")
        tab_bull, tab_bear, tab_judge = st.tabs(["多方", "空方", "研究经理"])
        with tab_bull:
            st.markdown(_display_report_text(debate.get("bull_history", "") or "无数据", ticker, final_state))
        with tab_bear:
            st.markdown(_display_report_text(debate.get("bear_history", "") or "无数据", ticker, final_state))
        with tab_judge:
            st.markdown(_display_report_text(debate.get("judge_decision", "") or "无数据", ticker, final_state))

    # The live graph state carries `trader_investment_plan`; the JSON written by
    # TradingAgentsGraph._log_state renames it to `trader_investment_decision`.
    # Accept either, otherwise the live report renders this section empty while
    # the same report reopened from history shows it.
    trader_decision = (
        final_state.get("trader_investment_plan")
        or final_state.get("trader_investment_decision", "")
    )
    if trader_decision:
        with st.expander("交易员决策", expanded=False):
            st.markdown(_display_report_text(trader_decision, ticker, final_state))

    risk = final_state.get("risk_debate_state")
    if risk and isinstance(risk, dict):
        st.markdown("## 风控评估")
        tab_agg, tab_con, tab_neu, tab_rj = st.tabs(["激进", "保守", "中性", "风控决策"])
        with tab_agg:
            st.markdown(_display_report_text(risk.get("aggressive_history", "") or "无数据", ticker, final_state))
        with tab_con:
            st.markdown(_display_report_text(risk.get("conservative_history", "") or "无数据", ticker, final_state))
        with tab_neu:
            st.markdown(_display_report_text(risk.get("neutral_history", "") or "无数据", ticker, final_state))
        with tab_rj:
            st.markdown(_display_report_text(risk.get("judge_decision", "") or "无数据", ticker, final_state))

    dqs = final_state.get("data_quality_summary", "")
    if dqs:
        with st.expander("数据质量", expanded=False):
            st.markdown(_display_report_text(dqs, ticker, final_state))
