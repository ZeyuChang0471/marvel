"""MANUAL PROBE — one real (paid) analysis, every LLM call recorded to JSONL.

⚠️ **This is not a test and it is not free.** It runs the full pipeline (9 analysts
with tool loops, quality gate, bull/bear debate, two judges, risk debate) against
the configured provider — typically 20–40 model calls. It prints the models, the
ticker, the analysis date and the output path first, and refuses to run without
``--yes``.

Why it exists: ``DEBATE_POLARIZATION_AUDIT.md`` measures the *architecture* with
scripted models and says so. Whether the real model restates an unverified rumour,
or drops the label while keeping the claim, can only be answered from a real run
whose prompts and responses are kept. Once recorded, those questions are answered
offline and for free, as many times as you like:

    python scripts/record_debate.py --ticker 600487 --date 2026-10-16 --yes
    python scripts/record_debate.py --report .marvel_state/recordings/debate-600487-2026-10-16.jsonl

The recorder is a LangChain callback handler (``marvel/harness/llm_recorder.py``),
attached through ``MarvelGraph(callbacks=[...])`` — no proxy, no second HTTP
client, and structured-output calls are captured too.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

#: 本机预设，与 run.py / run_single.py 保持一致（可用命令行参数覆盖）。
DEFAULT_PROVIDER = "deepseek"
DEFAULT_DEEP_MODEL = "deepseek-v4-pro"
DEFAULT_QUICK_MODEL = "deepseek-flash"

#: 提示词 → 阶段 的启发式识别。顺序有意义：
#:   * 质量门控 / 辩手 / 裁判的提示词里**嵌着所有分析师的名字**，必须先匹配它们；
#:   * 「新闻与政策分析师」包含「政策分析师」，新闻在前。
_STAGE_MARKERS = (
    ("你是数据质量审核员", "quality_gate"),
    ("You are a Bull Analyst advocating", "bull"),
    ("You are a Bear Analyst making the case against", "bear"),
    ("As the Research Manager and debate facilitator", "research_manager"),
    ("As the Portfolio Manager", "portfolio_manager"),
    ("As the Aggressive Risk Analyst", "aggressive"),
    ("As the Conservative Risk Analyst", "conservative"),
    ("As the Neutral Risk Analyst", "neutral"),
    ("trading agent specialising in A-share", "trader"),
    ("新闻与政策分析师", "news"),
    ("政策分析师", "policy"),
    ("市场情绪分析师", "social"),
    ("基本面分析师", "fundamentals"),
    ("技术分析师。你的任务是", "market"),
    # 量价分析师的提示词里同样出现「游资」二字，必须排在 hot_money 之前；
    # 所有分析师标记都用**角色句**而不是关键词，避免下游提示词里嵌入的报告串台。
    ("量价分析师（Volume Price Analysis）", "volume_price"),
    ("游资与资金流向追踪分析师", "hot_money"),
    ("解禁与减持监控分析师", "lockup"),
    ("宏观与板块分析师", "macro"),
)

#: 分析师阶段 → AgentState 字段
_ANALYST_FIELDS = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
    "policy": "policy_report",
    "hot_money": "hot_money_report",
    "lockup": "lockup_report",
    "volume_price": "volume_price_report",
    "macro": "macro_report",
}

#: 录制/复测时的有界请求预算（SDK 默认 600s × 3 次会把一次挂住的调用拖半小时）。
DEFAULT_LLM_TIMEOUT_S = "240"
DEFAULT_LLM_MAX_RETRIES = "1"


def _apply_llm_budget_env() -> None:
    os.environ.setdefault("MARVEL_LLM_TIMEOUT_S", DEFAULT_LLM_TIMEOUT_S)
    os.environ.setdefault("MARVEL_LLM_MAX_RETRIES", DEFAULT_LLM_MAX_RETRIES)


def classify_stage(prompt: str) -> str:
    for marker, stage in _STAGE_MARKERS:
        if marker in prompt:
            return stage
    return "other"


def reports_from_recording(records) -> tuple:
    """从一次完整运行的录制里取回 9 份分析师报告 + 门控复审。

    「便宜地重跑辩论」的关键：分析师阶段（工具循环 + 联网取数）占了绝大部分调用与
    时间，而改辩论/裁决的 prompt 时想复测的恰恰是后半段。每个分析师取**最后一条**
    长响应（它停止调用工具时给出的就是报告正文）。
    """
    reports: dict = {}
    quality = ""
    for rec in records:
        stage = classify_stage(rec.get("prompt", "") or "")
        response = rec.get("response", "") or ""
        if stage == "quality_gate":
            if response.strip():
                quality = response
            continue
        field = _ANALYST_FIELDS.get(stage)
        if field and len(response.strip()) > 200:
            reports[field] = response
    return reports, quality


def run_debate_chain(config, ticker: str, trade_date: str, reports: dict,
                     quality: str = "", *, recorder=None) -> dict:
    """在给定报告上跑完整辩论链（bull→bear→RM→trader→risk×3→PM），返回 state。

    与图里的节点顺序一致（`graph/setup.py`）：多空各一轮、RM 用 deep、Trader 用
    quick、三方风险各一轮、PM 用 deep。A/B 对照与 `--from-recording` 共用这一份。
    """
    from marvel.agents.managers.portfolio_manager import create_portfolio_manager
    from marvel.agents.managers.research_manager import create_research_manager
    from marvel.agents.researchers.bear_researcher import create_bear_researcher
    from marvel.agents.researchers.bull_researcher import create_bull_researcher
    from marvel.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
    from marvel.agents.risk_mgmt.conservative_debator import create_conservative_debator
    from marvel.agents.risk_mgmt.neutral_debator import create_neutral_debator
    from marvel.agents.trader.trader import create_trader
    from marvel.graph.propagation import Propagator
    from marvel.graph.trading_graph import MarvelGraph

    graph = MarvelGraph(config=config, callbacks=[recorder] if recorder else None)
    quick, deep = graph.quick_thinking_llm, graph.deep_thinking_llm

    state = Propagator().create_initial_state(ticker, trade_date)
    for field in _ANALYST_FIELDS.values():
        state[field] = reports.get(field, "（本次复测未包含该报告）")
    if quality:
        state["data_quality_summary"] = (
            "## 数据质量门控结果（录制回放：硬检查未重算）\n\n" + quality
        )

    bull = create_bull_researcher(quick)
    bear = create_bear_researcher(quick)
    research_manager = create_research_manager(deep)
    trader = create_trader(quick)
    aggressive = create_aggressive_debator(quick)
    conservative = create_conservative_debator(quick)
    neutral = create_neutral_debator(quick)
    portfolio_manager = create_portfolio_manager(deep)

    for _ in range(max(1, int(config.get("max_debate_rounds", 1) or 1))):
        state.update(bull(state))
        state.update(bear(state))
    state.update(research_manager(state))
    state.update(trader(state))
    for _ in range(max(1, int(config.get("max_risk_discuss_rounds", 1) or 1))):
        state.update(aggressive(state))
        state.update(conservative(state))
        state.update(neutral(state))
    state.update(portfolio_manager(state))
    return state


def run_debate_only(source, ticker: str, trade_date: str, out: str, max_chars: int,
                    yes: bool, provider: str = "", deep_model: str = "",
                    quick_model: str = "") -> int:
    """只重跑辩论 + 裁决（复用录制里的分析师报告），成本约为一次全流程的 1/3。"""
    from dotenv import load_dotenv

    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")

    from marvel.agents.managers.portfolio_manager import create_portfolio_manager
    from marvel.agents.managers.research_manager import create_research_manager
    from marvel.agents.researchers.bear_researcher import create_bear_researcher
    from marvel.agents.researchers.bull_researcher import create_bull_researcher
    from marvel.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
    from marvel.agents.risk_mgmt.conservative_debator import create_conservative_debator
    from marvel.agents.risk_mgmt.neutral_debator import create_neutral_debator
    from marvel.agents.trader.trader import create_trader
    from marvel.agents.utils.rating import parse_rating
    from marvel.dataflows.config import get_config
    from marvel.graph.propagation import Propagator
    from marvel.graph.trading_graph import MarvelGraph
    from marvel.harness.llm_recorder import JsonlLlmRecorder, iter_records

    records = list(iter_records(source))
    if not records:
        print(f"没读到记录：{source}")
        return 1
    reports, quality = reports_from_recording(records)
    missing = [field for field in _ANALYST_FIELDS.values() if field not in reports]

    config = dict(get_config())

    def _pick(cli_value: str, env_name: str, preset: str) -> str:
        return (cli_value or os.environ.get(env_name, "") or preset).strip()

    config["llm_provider"] = _pick(provider, "LLM_PROVIDER", DEFAULT_PROVIDER)
    config["deep_think_llm"] = _pick(deep_model, "DEEP_THINK_LLM", DEFAULT_DEEP_MODEL)
    config["quick_think_llm"] = _pick(quick_model, "QUICK_THINK_LLM", DEFAULT_QUICK_MODEL)

    out_path = Path(out) if out else (
        Path(config["data_cache_dir"]).parent / "recordings"
        / f"debate-only-{ticker}-{trade_date}.jsonl"
    )

    print("=" * 72)
    print("复用录制里的分析师报告，只重跑「辩论 + 裁决」（会计费，约 8–12 次调用）：")
    print(f"  来源录制   : {source}")
    print(f"  报告       : 找到 {len(reports)}/9" + (f"，缺 {missing}" if missing else "（齐全）"))
    print(f"  门控复审   : {'有' if quality else '没有（data_quality_summary 会留空）'}")
    print(f"  provider   : {config.get('llm_provider')}")
    print(f"  模型       : {config.get('deep_think_llm')} / {config.get('quick_think_llm')}")
    print(f"  录制输出   : {out_path}")
    print("=" * 72)
    if not yes:
        print("确认要花钱后加 --yes 再跑。")
        return 2

    _apply_llm_budget_env()
    recorder = JsonlLlmRecorder(out_path, max_chars=max_chars)

    started = time.time()
    state = run_debate_chain(
        config, ticker, trade_date, reports, quality, recorder=recorder
    )

    print("=" * 72)
    print(f"完成：{time.time() - started:.1f}s；录制写入 {out_path}")
    print(f"最终评级：{parse_rating(state.get('final_trade_decision', ''))}")
    print(f"复盘：python scripts/record_debate.py --report {out_path}")
    print("=" * 72)
    return 0


def report(path) -> int:
    """Offline read-back: which stages saw unverified claims, and did they keep them?"""
    from marvel.agents.utils.evidence import provenance_census
    from marvel.harness.llm_recorder import iter_records

    records = list(iter_records(path))
    if not records:
        print(f"没读到记录：{path}")
        return 1

    errors = [r for r in records if r.get("error")]
    print(f"录制：{path}")
    print(f"调用 {len(records)} 次；失败 {len(errors)} 次；"
          f"截断 {sum(1 for r in records if r.get('truncated'))} 次")
    print("=" * 72)

    structured = [r for r in records if r.get("tool_calls")]
    if structured:
        print(f"带工具/结构化调用的记录：{len(structured)} 次"
              "（RM / Trader / PM 的决策正文在 args 里，用 --dump 看全文）")

    carriers = []
    for rec in records:
        prompt = rec.get("prompt", "") or ""
        # 结构化输出的正文在 tool_calls，不在 response
        response = (rec.get("response", "") or "") + "".join(
            str(call.get("args") or "") for call in (rec.get("tool_calls") or [])
        )
        seen = provenance_census(prompt)
        if not seen["unverified"]:
            continue
        kept = provenance_census(response)["total"]
        carriers.append((rec.get("seq"), classify_stage(prompt), seen["unverified"],
                         kept, len(response), rec.get("model") or ""))

    print(f"提示词里带「未经交叉验证」的调用：{len(carriers)} 次")
    for seq, stage, seen, kept, length, model in carriers:
        verdict = (
            "响应里保留了来源标注" if kept
            else "响应里没有出现任何来源标注 —— 需人工确认它有没有把未证实说法当成事实"
        )
        print(f"  #{seq:<3} {stage:<18} prompt {seen:>2} 处 / response {kept:>2} 处"
              f"  resp_len={length:<6} {verdict}")

    print("=" * 72)
    print("下一步：人工读上面列出的响应（`--dump <seq>` 可打印原文），"
          "确认「复述但丢标注」的比例。")
    return 0


def dump(path, seq) -> int:
    from marvel.harness.llm_recorder import iter_records

    for rec in iter_records(path):
        if rec.get("seq") == seq:
            print(f"=== #{seq} {classify_stage(rec.get('prompt', ''))} ===")
            print(rec.get("prompt", ""))
            print("--- response ---")
            print(rec.get("response") or rec.get("error", ""))
            for call in rec.get("tool_calls") or []:
                print(f"--- tool_call: {call.get('name')} ---")
                print(call.get("args") or "")
            reasoning = rec.get("reasoning_content") or ""
            if reasoning:
                print("--- reasoning_content ---")
                print(reasoning)
            return 0
    print(f"没有 #{seq}")
    return 1


def run(ticker: str, trade_date: str, out: str, max_chars: int, yes: bool,
        provider: str = "", deep_model: str = "", quick_model: str = "") -> int:
    from dotenv import load_dotenv

    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")

    from marvel.dataflows.config import get_config
    from marvel.harness.llm_recorder import JsonlLlmRecorder
    from marvel.graph.trading_graph import MarvelGraph

    config = dict(get_config())

    # 优先级：命令行 > 环境变量（LLM_PROVIDER / DEEP_THINK_LLM / QUICK_THINK_LLM）
    # > 本机预设。default_config 的兜底值（openai/gpt-5.4）在这里没有意义——
    # 这台机器没有 OpenAI key，真正被使用的一直是 run.py 的 DeepSeek 预设。
    def _pick(cli_value: str, env_name: str, preset: str) -> str:
        return (cli_value or os.environ.get(env_name, "") or preset).strip()

    config["llm_provider"] = _pick(provider, "LLM_PROVIDER", DEFAULT_PROVIDER)
    config["deep_think_llm"] = _pick(deep_model, "DEEP_THINK_LLM", DEFAULT_DEEP_MODEL)
    config["quick_think_llm"] = _pick(quick_model, "QUICK_THINK_LLM", DEFAULT_QUICK_MODEL)
    out_path = Path(out) if out else (
        Path(config["data_cache_dir"]).parent / "recordings"
        / f"debate-{ticker}-{trade_date}.jsonl"
    )

    print("=" * 72)
    print("即将跑一次真实分析（会计费）：")
    print(f"  provider    : {config.get('llm_provider')}"
          "  ← 命令行 > 环境变量 > 本机预设(DeepSeek)")
    print(f"  deep model  : {config.get('deep_think_llm')}")
    print(f"  quick model : {config.get('quick_think_llm')}")
    print(f"  ticker/date : {ticker} / {trade_date}")
    print(f"  录制输出    : {out_path}")
    print("  调用规模    : 9 位分析师（含工具循环）+ 门控 + 多空辩论 + 裁决，约 20–40 次")
    print("=" * 72)
    if not yes:
        print("确认要花钱后加 --yes 再跑。")
        return 2

    _apply_llm_budget_env()
    recorder = JsonlLlmRecorder(out_path, max_chars=max_chars)
    graph = MarvelGraph(config=config, callbacks=[recorder])

    started = time.time()
    final_state, decision = graph.propagate(ticker, trade_date)
    elapsed = time.time() - started

    print("=" * 72)
    print(f"完成：{elapsed:.1f}s；录制写入 {out_path}")
    print(f"最终信号：{decision}")
    print(f"下一次离线分析：python scripts/record_debate.py --report {out_path}")
    print("=" * 72)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="录一次真实分析的全部 LLM 调用（会计费），或离线分析已有录制"
    )
    parser.add_argument("--ticker", help="6 位 A 股代码")
    parser.add_argument("--date", help="分析日 yyyy-mm-dd")
    parser.add_argument("--out", default="", help="录制输出路径（默认放到状态目录 recordings/）")
    parser.add_argument("--provider", default="", help=f"LLM 供应商（默认本机预设 {DEFAULT_PROVIDER}）")
    parser.add_argument("--deep-model", default="", help=f"深度模型（默认 {DEFAULT_DEEP_MODEL}）")
    parser.add_argument("--quick-model", default="", help=f"快速模型（默认 {DEFAULT_QUICK_MODEL}）")
    parser.add_argument("--max-chars", type=int, default=20000,
                        help="单条 prompt/response 记录的最大字符数")
    parser.add_argument("--yes", action="store_true", help="确认这次真实运行会花钱")
    parser.add_argument("--report", default="", help="离线分析已有录制")
    parser.add_argument("--from-recording", default="",
                        help="复用这份录制里的分析师报告，只重跑辩论+裁决（约 8–12 次调用）")
    parser.add_argument("--dump", type=int, default=0, help="配合 --report 打印某一条的全文")
    args = parser.parse_args(argv)

    if args.report:
        if args.dump:
            return dump(args.report, args.dump)
        return report(args.report)

    if args.from_recording:
        if not args.ticker or not args.date:
            parser.error("--from-recording 需要 --ticker 与 --date（与录制时一致）")
        return run_debate_only(args.from_recording, args.ticker, args.date, args.out,
                               args.max_chars, args.yes,
                               args.provider, args.deep_model, args.quick_model)

    if not args.ticker or not args.date:
        parser.error("需要 --ticker 与 --date（或用 --report 做离线分析）")
    return run(args.ticker, args.date, args.out, args.max_chars, args.yes,
               args.provider, args.deep_model, args.quick_model)


if __name__ == "__main__":
    sys.exit(main())
