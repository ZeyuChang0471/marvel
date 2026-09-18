"""Sidebar: stock input, LLM config, and history list."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import streamlit as st

from marvel.llm_clients.model_catalog import MODEL_OPTIONS
from web.history import get_history

# Provider display names in recommended order
_PROVIDERS: list[tuple[str, str]] = [
    ("MiniMax（推荐·国内直连）", "minimax"),
    ("DeepSeek", "deepseek"),
    ("通义千问 Qwen", "qwen"),
    ("智谱 GLM", "glm"),
    ("OpenAI", "openai"),
    ("Anthropic", "anthropic"),
    ("Google Gemini", "google"),
    ("xAI Grok", "xai"),
    ("Ollama（本地）", "ollama"),
]

_PROVIDER_DISPLAY = [name for name, _ in _PROVIDERS]
_PROVIDER_KEYS = [key for _, key in _PROVIDERS]

# Map provider key → env var whose presence signals "this provider is configured".
# Ollama is local so it's always considered ready.
_PROVIDER_KEY_ENV: dict[str, str | None] = {
    "minimax": "MINIMAX_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "glm": "ZHIPU_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "ollama": None,
}

_PROVIDER_LABELS: dict[str, str] = {key: name for name, key in _PROVIDERS}

# The project .env lives at the repo root and is listed in .gitignore, so a
# key typed into the UI is applied to this process and persisted locally
# without ever being committed.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _PROJECT_ROOT / ".env"
_API_KEY_STATUS = "_api_key_status"


def _api_key_widget_key(provider_key: str) -> str:
    return f"api_key_input_{provider_key}"


def _mask_key(value: str) -> str:
    """Show only the tail of a key, enough for the user to tell two apart."""
    return f"****{value[-4:]}" if len(value) > 4 else "****"


def _persist_env_var(env_var: str, value: str | None) -> tuple[bool, str]:
    """Write (or blank out) one variable in the project .env file.

    Comments and every other variable are preserved. Returns (ok, error).
    """
    try:
        lines = (
            _ENV_PATH.read_text(encoding="utf-8").splitlines()
            if _ENV_PATH.exists()
            else []
        )
    except OSError as exc:
        return False, f"读取 .env 失败：{exc}"

    assignment = f"{env_var}={value}" if value else f"{env_var}="
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{env_var}=") or line.strip() == env_var:
            lines[i] = assignment
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(assignment)

    try:
        _ENV_PATH.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    except OSError as exc:
        return False, f"写入 .env 失败：{exc}"
    return True, ""


def _apply_api_key(provider_key: str) -> None:
    """Apply the key typed in the sidebar to os.environ and to .env.

    Runs as a widget callback, i.e. before the rerun's script body, so the
    value is already in the environment by the time the graph (and with it
    the LLM client) is built.
    """
    env_var = _PROVIDER_KEY_ENV.get(provider_key)
    if not env_var:
        return

    value = (st.session_state.get(_api_key_widget_key(provider_key)) or "").strip()

    if value:
        os.environ[env_var] = value
        ok, err = _persist_env_var(env_var, value)
        st.session_state[_API_KEY_STATUS] = (
            ("success", f"已保存 `{env_var}`，本次分析立即可用。")
            if ok
            else ("warning", f"本次会话已生效，但写入 .env 失败：{err}")
        )
    else:
        os.environ.pop(env_var, None)
        ok, err = _persist_env_var(env_var, None)
        st.session_state[_API_KEY_STATUS] = (
            ("info", f"已清除 `{env_var}`。")
            if ok
            else ("warning", f"写入 .env 失败：{err}")
        )


def _render_api_key_input(provider_key: str) -> None:
    """Render the API-key field for the currently selected provider."""
    env_var = _PROVIDER_KEY_ENV.get(provider_key)

    if env_var is None:
        st.caption("ℹ️ Ollama 使用本地模型，无需 API Key。")
        return

    configured = (os.environ.get(env_var) or "").strip()
    if configured:
        st.caption(f"✅ `{env_var}` 已配置 · `{_mask_key(configured)}`")
    else:
        st.caption(f"⚠️ `{env_var}` 未配置")

    st.text_input(
        "API Key",
        key=_api_key_widget_key(provider_key),
        type="password",
        placeholder="粘贴 Key 后按回车即生效",
        on_change=_apply_api_key,
        args=(provider_key,),
        help=(
            f"{_PROVIDER_LABELS.get(provider_key, provider_key)} 使用环境变量 "
            f"{env_var}。在此粘贴后按回车：立即对本次会话生效，并写入项目根目录的 "
            ".env（该文件已被 .gitignore 忽略，不会被提交）。"
            "留空后按回车可清除已保存的 Key。"
        ),
    )

    status = st.session_state.pop(_API_KEY_STATUS, None)
    if status:
        level, message = status
        {"success": st.success, "warning": st.warning, "info": st.info}[level](message)


def _detect_default_provider_idx() -> int:
    """Return the index of the first provider whose API key is configured.

    Scans providers in display order.  Ollama (local) never wins this scan
    because it needs no key — when nothing at all is configured we fall back
    to the first (recommended) provider so the API-key field is visible and
    the user can paste a key straight away.
    """
    for i, key in enumerate(_PROVIDER_KEYS):
        env_var = _PROVIDER_KEY_ENV.get(key)
        if env_var is None:
            continue  # Ollama — needs no key
        if (os.getenv(env_var) or "").strip():
            return i

    return 0


def _resolve_user_input(raw: str) -> tuple[str, str | None]:
    """Resolve raw user input to (ticker_code, error_msg).

    Accepts 6-digit codes or Chinese stock names (e.g. '宝光股份').
    Returns (code, None) on success or ("", error_msg) on failure.
    """
    from marvel.dataflows.a_stock import resolve_ticker

    try:
        code = resolve_ticker(raw)
        return code, None
    except ValueError as e:
        return "", str(e)


def _render_model_picker(label: str, provider_key: str, mode: str, help_text: str) -> str:
    """Render one quick/deep model selector and return the resolved model ID.

    The catalog carries a "Custom model ID" placeholder whose value is the
    literal string ``"custom"``. Picking it has to reveal a free-text field:
    the CLI always did this (``cli/utils.py``), but the Web UI used to hand
    ``"custom"`` straight to the API as the model name, so any model outside
    the catalog was unusable from the Web UI.
    """
    options = MODEL_OPTIONS.get(provider_key, {}).get(mode)

    if not options:
        # Provider without a catalog entry (openrouter / azure): free text.
        return st.text_input(
            f"{label} ID", key=f"custom_{provider_key}_{mode}_model"
        ).strip()

    labels = [text for text, _ in options]
    values = [value for _, value in options]

    idx = st.selectbox(
        label,
        range(len(options)),
        format_func=lambda i: labels[i],
        key=f"{mode}_model_idx",
        help=help_text,
    )

    if values[idx] != "custom":
        return values[idx]

    typed = st.text_input(
        f"自定义{label} ID",
        # Keyed per provider: a shared key would carry a model name typed for
        # one provider over to the next one the user switches to.
        key=f"custom_{provider_key}_{mode}_model",
        placeholder="例: deepseek-flash",
        help=(
            "填你后端实际接受的 model 字符串。不在已知列表里也能用，"
            "只会在日志里留一条 RuntimeWarning（§ validate_model）。"
        ),
    ).strip()

    if typed:
        return typed

    fallback = next((v for v in values if v != "custom"), "")
    st.caption(f"⚠️ 未填写自定义模型 ID，暂时沿用 `{fallback}`")
    return fallback


def _render_llm_config() -> None:
    """Render LLM provider and model selection controls."""

    # Auto-detect which provider has an API key on first visit.
    # Once the user makes a choice it persists in session state.
    if "llm_provider_idx" not in st.session_state:
        st.session_state["llm_provider_idx"] = _detect_default_provider_idx()

    provider_idx = st.selectbox(
        "LLM 供应商",
        range(len(_PROVIDERS)),
        format_func=lambda i: _PROVIDER_DISPLAY[i],
        key="llm_provider_idx",
        help="选择你配置了 API Key 的供应商",
    )
    provider_key = _PROVIDER_KEYS[provider_idx]
    st.session_state["llm_provider"] = provider_key

    _render_api_key_input(provider_key)

    st.session_state["quick_think_llm"] = _render_model_picker(
        "快速思考模型",
        provider_key,
        "quick",
        help_text=(
            "承载绝大多数调用：7 个 Analyst、质量门控、Bull/Bear 研究员、Trader、"
            "三方风险辩论、交易反思与评级提取。速度优先。"
        ),
    )
    st.session_state["deep_think_llm"] = _render_model_picker(
        "深度思考模型",
        provider_key,
        "deep",
        help_text=(
            "只用于两个拍板节点：Research Manager（汇总多空辩论）与 "
            "Portfolio Manager（最终决策）。"
        ),
    )

    st.text_input(
        "API Base URL（第三方/代理，可选）",
        key="llm_base_url",
        placeholder="例: https://your-proxy.com/v1",
        help=(
            "通过第三方中转/代理访问 Claude、OpenAI 等模型时填写网关地址；"
            "留空则用所选供应商的官方地址。API Key 可用上方输入框填写，"
            "或写在 .env / 环境变量里，每个供应商用各自的变量名——"
            "OpenAI=OPENAI_API_KEY、DeepSeek=DEEPSEEK_API_KEY、"
            "通义=DASHSCOPE_API_KEY、智谱=ZHIPU_API_KEY、MiniMax=MINIMAX_API_KEY、"
            "Claude=ANTHROPIC_API_KEY、OpenRouter=OPENROUTER_API_KEY、xAI=XAI_API_KEY。"
            "也可在 .env 里设 BACKEND_URL 代替此处。"
        ),
    )


def _resolve_analysis_date() -> tuple[date, str]:
    """Pick the analysis date and explain the choice.

    Defaults to the most recent session whose daily bar already exists. The old
    behaviour (plain ``date.today()``) launched an analysis of a day the market
    never traded on weekends and holidays, and the resulting empty report looked
    like a data-source failure rather than a calendar fact.
    """
    from marvel.dataflows.trade_calendar import (
        cn_market_phase,
        cn_today_str,
        latest_cn_trading_day,
    )

    today = cn_today_str()
    resolved = latest_cn_trading_day()
    if resolved == today:
        note = "今日已收盘"
    else:
        phase = cn_market_phase()
        if phase in ("in_session", "lunch_break"):
            note = "今日盘中、日线未收盘 → 回退到上一交易日"
        elif phase == "pre_open":
            note = "今日尚未开盘 → 回退到上一交易日"
        else:
            note = "今日非交易日（A股休市）→ 回退到最近交易日"
    return date.fromisoformat(resolved), note


def render_sidebar() -> None:
    """Render the sidebar with input controls and history."""

    st.markdown(
        """
        <div style="text-align:center; margin-bottom:1.5rem;">
            <span style="font-size:2rem; font-weight:800; color:#ffffff;">MARVEL</span>
            <div style="font-size:0.85rem; color:#ffffff; margin-top:0.2rem;">
                A股多Agent投研系统
            </div>
            <div style="font-size:0.7rem; color:#ffffff; margin-top:0.3rem;">
                by <a href="https://github.com/ZeyuChang0471" style="color:#ffffff; text-decoration:none;">zzy</a>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("#### 新建分析")

    ticker = st.text_input(
        "股票代码",
        placeholder="例: 300750 或 宁德时代",
        key="input_ticker",
        help="输入6位A股代码或中文股票全称",
    )

    trade_date, date_note = _resolve_analysis_date()
    st.caption(f"📅 分析日期：{trade_date.strftime('%Y-%m-%d')}（{date_note}）")

    with st.expander("⚙️ 模型配置", expanded=False):
        _render_llm_config()

    tracker = st.session_state.get("tracker")
    is_busy = tracker is not None and tracker.is_running

    if st.button(
        "开始分析" if not is_busy else "分析进行中...",
        use_container_width=True,
        disabled=is_busy or not ticker,
        type="primary",
    ):
        resolved_code, err = _resolve_user_input(ticker)
        if err:
            st.error(f"❌ {err}")
        else:
            if resolved_code != ticker.strip():
                st.success(f"✅ {ticker.strip()} → {resolved_code}")
            st.session_state["start_analysis"] = {
                "ticker": resolved_code,
                "trade_date": trade_date.strftime("%Y-%m-%d"),
            }
            st.session_state["viewing_history"] = None

    st.markdown("---")
    st.markdown("#### 历史记录")

    history = get_history()
    if not history:
        st.caption("暂无历史记录")
        return

    for entry in history[:20]:
        t, d = entry["ticker"], entry["date"]
        label = f"{t}  ·  {d}"
        if st.button(label, key=f"hist_{t}_{d}", use_container_width=True):
            st.session_state["viewing_history"] = entry["path"]
            st.session_state["start_analysis"] = None

    st.markdown("---")
    st.caption("⚠️ 仅供学习研究，不构成投资建议")
