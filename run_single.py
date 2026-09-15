#!/usr/bin/env python
"""MARVEL 单股分析入口。

用法:
  python run_single.py 600519                  # 分析贵州茅台（当天）
  python run_single.py 600519 2026-06-16       # 指定日期
  python run_single.py "贵州茅台"               # 支持中文名称
"""

import io
import os
import sys
from pathlib import Path
from datetime import date, datetime

# ── Windows UTF-8 ──────────────────────────────────────────────────
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 项目路径 ───────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 加载 .env ──────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

# ── 预设配置 ───────────────────────────────────────────────────────
from marvel.dataflows.config import set_config

CONFIG_OVERRIDES = {
    "llm_provider": "deepseek",
    "deep_think_llm": "deepseek-v4-pro",
    "quick_think_llm": "deepseek-flash",
    "output_language": "Chinese",
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
}
set_config(CONFIG_OVERRIDES)


def parse_date(date_str: str) -> str:
    """解析日期字符串，返回 YYYY-MM-DD 格式。"""
    if not date_str or not date_str.strip():
        return date.today().strftime("%Y-%m-%d")

    date_str = date_str.strip()
    # 尝试多种格式
    formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%m-%d", "%m/%d"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    print(f"[WARN] 无法解析日期 '{date_str}'，使用今天日期")
    return date.today().strftime("%Y-%m-%d")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("用法: python run_single.py <股票代码> [日期]")
        sys.exit(1)

    ticker = sys.argv[1].strip()
    analysis_date = parse_date(sys.argv[2]) if len(sys.argv) > 2 else date.today().strftime("%Y-%m-%d")

    # ── 验证 API Key ───────────────────────────────────────────────
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key or not api_key.startswith("sk-"):
        print("=" * 60)
        print("❌ DEEPSEEK_API_KEY 未正确配置！")
        print("   请编辑项目根目录的 .env 文件，填入: DEEPSEEK_API_KEY=sk-你的key")
        print("=" * 60)
        sys.exit(1)

    print("=" * 60)
    print("  MARVEL — 单股分析")
    print("=" * 60)
    print(f"  股票:      {ticker}")
    print(f"  分析日期:  {analysis_date}")
    print(f"  深度思考:  {CONFIG_OVERRIDES['deep_think_llm']}")
    print(f"  快速思考:  {CONFIG_OVERRIDES['quick_think_llm']}")
    print(f"  API Key:   {api_key[:12]}...{api_key[-4:]}")
    print("=" * 60)
    print()

    # ── 执行分析 ───────────────────────────────────────────────────
    from marvel.graph.trading_graph import MarvelGraph
    from marvel.dataflows.config import get_config

    config = get_config()

    print(f"🚀 开始分析: {ticker} ({analysis_date})")
    print()

    try:
        graph = MarvelGraph(debug=True, config=config)

        init_state = graph.propagator.create_initial_state(ticker, analysis_date)
        args = graph.propagator.get_graph_args()

        last_chunk = {}
        for chunk in graph.graph.stream(init_state, **args):
            last_chunk = chunk
            # 打印阶段完成提示
            for key in chunk:
                if key == "final_trade_decision" and chunk[key]:
                    print("\n✅ 分析完成！最终决策已生成。")
                    break

        # 打印最终结果
        final_decision = last_chunk.get("final_trade_decision", "")
        if final_decision:
            print()
            print("=" * 60)
            print("  最终交易决策")
            print("=" * 60)
            print(final_decision[:2000])

        signal = graph.process_signal(final_decision)
        graph._log_state(analysis_date, last_chunk)

        print(f"\n📊 评级: {signal}")
        print(f"📁 日志目录: {config['results_dir']}/{ticker}/{analysis_date}/")
        print()

    except Exception as e:
        print(f"\n❌ 分析过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
