#!/usr/bin/env python
"""MARVEL 单股分析入口。

用法:
  python run_single.py 600519                  # 分析贵州茅台（当天）
  python run_single.py 600519 2026-06-16       # 指定日期
  python run_single.py "贵州茅台"               # 支持中文名称

注意：本文件在 import 时**不做任何事**——此前它在模块层重配 stdio 并调用
`set_config()`，import 它会污染调用方的 stdout 与全局配置。
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent

CONFIG_OVERRIDES = {
    "llm_provider": "deepseek",
    "deep_think_llm": "deepseek-v4-pro",
    "quick_think_llm": "deepseek-flash",
    "output_language": "Chinese",
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
}


def _configure_stdio() -> None:
    """Windows UTF-8 修复。``sys.stdin`` 可能是 None，必须判空。"""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — 编码修复失败不该阻断运行
            pass


def _ensure_project_on_path() -> None:
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))


def parse_date(date_str: str) -> str:
    """解析日期字符串，返回 YYYY-MM-DD 格式。"""
    if not date_str or not date_str.strip():
        return date.today().strftime("%Y-%m-%d")

    date_str = date_str.strip()
    # 尝试多种格式
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%m-%d", "%m/%d"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    print(f"[WARN] 无法解析日期 '{date_str}'，使用今天日期")
    return date.today().strftime("%Y-%m-%d")


def _require_api_key() -> None:
    """确认 DEEPSEEK_API_KEY 存在；缺失时报错并退出。

    只报变量名，**不打印 key 片段**：此前这里打印
    ``api_key[:12]}...{api_key[-4:]}``，十几个真实密钥字符会留在终端 scrollback
    与被重定向的日志里。项目自己的惯例是只显示末四位。
    """
    import os

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key or not api_key.startswith("sk-"):
        print("=" * 60)
        print("❌ DEEPSEEK_API_KEY 未正确配置！")
        print("   请编辑项目根目录的 .env 文件，填入: DEEPSEEK_API_KEY=sk-你的key")
        print("=" * 60)
        raise SystemExit(1)


def main() -> None:
    _configure_stdio()
    _ensure_project_on_path()

    if len(sys.argv) < 2:
        print(__doc__)
        print("用法: python run_single.py <股票代码> [日期]")
        raise SystemExit(1)

    ticker = sys.argv[1].strip()
    analysis_date = (
        parse_date(sys.argv[2]) if len(sys.argv) > 2
        else date.today().strftime("%Y-%m-%d")
    )

    from dotenv import load_dotenv

    load_dotenv(_PROJECT_ROOT / ".env")

    _require_api_key()

    print("=" * 60)
    print("  MARVEL — 单股分析")
    print("=" * 60)
    print(f"  股票:      {ticker}")
    print(f"  分析日期:  {analysis_date}")
    print(f"  深度思考:  {CONFIG_OVERRIDES['deep_think_llm']}")
    print(f"  快速思考:  {CONFIG_OVERRIDES['quick_think_llm']}")
    print("  API Key:   已配置（来自 .env 或环境变量）")
    print("=" * 60)
    print()

    from marvel.dataflows.config import get_config, set_config
    from marvel.graph.trading_graph import MarvelGraph

    set_config(CONFIG_OVERRIDES)
    config = get_config()

    print(f"🚀 开始分析: {ticker} ({analysis_date})")
    print()

    try:
        graph = MarvelGraph(debug=True, config=config)
        try:
            # 与 Web UI 相同的驱动路径：checkpoint / 状态落盘 / 记忆日志 / 断点清理
            # 都在 prepare_graph_run + finalize_graph_run 里完成。此前这里直接调
            # propagator.* 和 graph._log_state，把记忆日志与断点续跑整个绕过了。
            init_state, args, _ = graph.prepare_graph_run(ticker, analysis_date)

            last_chunk: dict = {}
            for chunk in graph.graph.stream(init_state, **args):
                last_chunk = chunk
                if chunk.get("final_trade_decision"):
                    print("\n✅ 分析完成！最终决策已生成。")

            if not last_chunk:
                raise RuntimeError("分析没有返回任何结果")

            signal = graph.finalize_graph_run(ticker, analysis_date, last_chunk)
        finally:
            graph.close_graph_run()

        final_decision = last_chunk.get("final_trade_decision", "")
        if final_decision:
            print()
            print("=" * 60)
            print("  最终交易决策")
            print("=" * 60)
            print(final_decision[:2000])

        print(f"\n📊 评级: {signal}")
        print(f"📁 日志目录: {config['results_dir']}/{ticker}/marvel_strategy_logs/")
        print()

    except Exception as e:  # noqa: BLE001 — CLI 入口要给出可读错误而不是裸栈
        print(f"\n❌ 分析过程中出现错误: {e}")
        import traceback

        traceback.print_exc()
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
