"""Batch runner: analyse A-stock tickers and save example outputs to examples/cases/.

Usage:
    python examples/run_cases.py          # run all cases
    python examples/run_cases.py 688017   # run a single ticker

⚠️ **Published outputs must not contain executable price levels.**

LICENSING.md stakes this project's legal position on not shipping 建仓价 / 止损位 /
仓位 / 目标价 guidance, and `TraderProposal` / `PortfolioDecision` deliberately have
no fields for them. Committed example outputs are part of what the project
publishes, so they must not reintroduce what the schemas removed — two example
reports (002594, 300750) previously did exactly that, which flatly contradicted
the licence rationale, so they were deleted.

Everything written here therefore goes through :func:`redact_executable_levels`
first. The redaction is deliberately over-inclusive: it drops a whole line when
that line mentions a level, rather than trying to keep the surrounding prose.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from marvel.default_config import DEFAULT_CONFIG
from marvel.graph.trading_graph import MarvelGraph

# ── Config ───────────────────────────────────────────────────────────────────

CASES_DIR = Path(__file__).parent / "cases"

TRADE_DATE = "2026-05-12"

# 10 tickers across different sectors
# fmt: off
TICKERS = {
    "688017": "绿的谐波 (科创板·谐波减速器)",
    "300750": "宁德时代 (创业板·动力电池)",
    "600519": "贵州茅台 (主板·白酒龙头)",
    "000858": "五粮液 (主板·白酒)",
    "300059": "东方财富 (创业板·互联网券商)",
    "601012": "隆基绿能 (主板·光伏)",
    "300760": "迈瑞医疗 (创业板·医疗器械)",
    "688981": "中芯国际 (科创板·芯片代工)",
    "002594": "比亚迪 (主板·新能源汽车)",
    "300124": "汇川技术 (创业板·工业自动化)",
}
# fmt: on


# ── Executable-price-level redaction ─────────────────────────────────────────

# 中英双语：这些词一出现就说明该行带可执行价位（或仓位建议）。
_LEVEL_MARKERS = (
    "止损", "止盈", "止蚀", "目标价", "建仓", "加仓", "减仓", "仓位",
    "买入价", "卖出价", "挂单", "轻仓", "重仓", "满仓", "清仓", "抄底",
    "支撑位", "压力位", "回踩", "建底仓",
    "stop-loss", "stop loss", "price target", "target price",
    "position size", "entry price", "take profit",
)

_REDACTION = "［已移除：可执行价位（建仓/止损/仓位/目标价）］"
# 公开别名：校验脚本要把它从待检文本里剔掉——否则脱敏标记本身含有这些词，
# 会让「示例产物不含可执行价位」的检查永远失败。
REDACTION_MARKER = _REDACTION


def redact_executable_levels(text: str) -> tuple[str, int]:
    """Remove every line carrying an executable price level.

    Returns ``(redacted_text, redaction_count)``. Line-granular on purpose:
    a paragraph in these reports is usually one clause per line, and
    over-redacting is the safe direction — under-redacting would publish the
    very thing the licence says was removed.
    """
    if not text:
        return text, 0

    kept: list[str] = []
    redactions = 0
    for line in text.splitlines():
        lowered = line.lower()
        if any(marker.lower() in lowered for marker in _LEVEL_MARKERS):
            kept.append(_REDACTION)
            redactions += 1
        else:
            kept.append(line)
    return "\n".join(kept), redactions


def build_config() -> dict:
    """Build the MARVEL config for case runs."""
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "minimax"
    config["deep_think_llm"] = "MiniMax-M2.7"
    config["quick_think_llm"] = "MiniMax-M2.7-highspeed"
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


def run_single(ticker: str, label: str, config: dict) -> None:
    """Run one ticker and save the (redacted) decision to a markdown file."""
    print(f"\n{'=' * 60}")
    print(f"Analysing {ticker} — {label}")
    print(f"Trade date: {TRADE_DATE}")
    print(f"{'=' * 60}\n")

    CASES_DIR.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    ta = MarvelGraph(debug=True, config=config)

    full_decision = ""
    try:
        final_state, decision = ta.propagate(ticker, TRADE_DATE)
        full_decision = final_state.get("final_trade_decision", "")
    except Exception as e:
        decision = f"ERROR: {e}"

    elapsed = time.time() - start_time

    redacted_decision, redactions = redact_executable_levels(full_decision)

    # Save result — short signal + redacted decision
    out_path = CASES_DIR / f"{ticker}_{label.split('(')[0].strip()}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# {ticker} {label}\n\n")
        f.write(f"- **Trade Date**: {TRADE_DATE}\n")
        f.write(f"- **Run Time**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"- **Duration**: {elapsed:.0f}s ({elapsed / 60:.1f} min)\n")
        f.write(f"- **LLM**: MiniMax-M2.7 / M2.7-highspeed\n")
        f.write(f"- **Executable levels redacted**: {redactions} line(s)\n\n")
        f.write(f"## Signal: {decision}\n\n")
        if redacted_decision and redacted_decision != decision:
            f.write(f"## Full Analysis\n\n{redacted_decision}\n")

    print(f"\n✅ Saved to {out_path} ({elapsed:.0f}s, {redactions} line(s) redacted)")
    if redactions:
        print(
            "   ⚠️ 已移除含可执行价位的行——这些产物会被提交，"
            "不得包含建仓/止损/仓位/目标价（见 LICENSING.md）。"
        )

    # Also save a JSON summary for programmatic use
    summary_path = CASES_DIR / f"{ticker}_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "ticker": ticker,
                "label": label,
                "trade_date": TRADE_DATE,
                "run_time": datetime.now().isoformat(),
                "duration_seconds": round(elapsed),
                "signal": decision,
                "executable_levels_redacted": redactions,
                "decision_preview": redacted_decision[:2000],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


def main() -> None:
    config = build_config()

    # Allow single ticker override from CLI
    if len(sys.argv) > 1:
        ticker = sys.argv[1]
        label = TICKERS.get(ticker, ticker)
        run_single(ticker, label, config)
        return

    # Run all
    print(f"Running {len(TICKERS)} cases...")
    for ticker, label in TICKERS.items():
        run_single(ticker, label, config)
        print(f"\n{'─' * 40}")

    print(f"\n{'=' * 60}")
    print(f"All {len(TICKERS)} cases complete. Results in {CASES_DIR}/")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
