#!/usr/bin/env python
"""MARVEL 一键运行脚本 —— DeepSeek 配置版。

用法:
  python run.py              # CLI 交互模式（手动选股+确认配置）
  python run.py web          # 启动 Streamlit Web UI
  python run.py test         # 快速测试：用 600519（贵州茅台）跑通主流程
  python run.py --help       # 查看帮助

前置条件:
  - .env 中已填写 DEEPSEEK_API_KEY=sk-xxxx
  - Python 3.10+ 虚拟环境已激活（venv/）
"""

import io
import os
import sys
from pathlib import Path

# ── 0. Windows 控制台 UTF-8 编码修复 ──────────────────────────────────────
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

# ── 0. 确保 .env 已加载 ────────────────────────────────────────────────
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")

# ── 1. 预设配置：DeepSeek V4 Pro + V4 Flash + 中文输出 ──────────────────
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

print("=" * 60)
print("  MARVEL — DeepSeek 配置")
print("=" * 60)
print(f"  供应商:    {CONFIG_OVERRIDES['llm_provider']}")
print(f"  深度思考:  {CONFIG_OVERRIDES['deep_think_llm']}")
print(f"  快速思考:  {CONFIG_OVERRIDES['quick_think_llm']}")
print(f"  输出语言:  {CONFIG_OVERRIDES['output_language']}")
print("=" * 60)

# ── 2. 检查 API Key ────────────────────────────────────────────────────
api_key = os.environ.get("DEEPSEEK_API_KEY", "")
if not api_key:
    print()
    print("❌ 未找到 DEEPSEEK_API_KEY！")
    print("   请在项目根目录的 .env 文件中填入：DEEPSEEK_API_KEY=sk-你的key")
    print("   然后重新运行。")
    sys.exit(1)

print(f"  API Key:    {api_key[:12]}...{api_key[-4:]}")
print()


# ── 3. 命令分发 ─────────────────────────────────────────────────────────
def run_cli():
    """CLI 交互模式：手动选择股票和分析参数。"""
    from cli.main import app
    # typer 会接管交互，我们在前面已经预设了 config
    app()


def run_test():
    """快速测试：用贵州茅台 600519 跑通主流程。"""
    from marvel.graph.trading_graph import MarvelGraph
    from marvel.dataflows.config import get_config
    from datetime import date

    config = get_config()
    ticker = "600519"
    analysis_date = date.today().strftime("%Y-%m-%d")

    print(f"\n🚀 开始测试分析: {ticker} ({analysis_date})")
    print(f"   深度思考模型: {config['deep_think_llm']}")
    print(f"   快速思考模型: {config['quick_think_llm']}")
    print(f"   输出语言: {config['output_language']}")
    print()

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
        print("\n" + "=" * 60)
        print("  最终交易决策")
        print("=" * 60)
        print(final_decision[:2000])

    signal = graph.process_signal(final_decision)
    graph._log_state(analysis_date, last_chunk)
    print(f"\n📊 评级: {signal}")
    print(f"📁 日志目录: {config['results_dir']}/{ticker}/{analysis_date}/")

    return last_chunk


def run_web():
    """启动 Streamlit Web UI。"""
    from streamlit.web import cli as stcli

    app_path = str(_PROJECT_ROOT / "web" / "app.py")
    print("🌐 启动 Web UI... 浏览器打开后即可使用。")
    print("   侧边栏选择 LLM 供应商 → DeepSeek，即可选中 V4 Pro/Flash。")
    print()
    sys.argv = ["streamlit", "run", app_path]
    stcli.main()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
    else:
        cmd = "cli"

    if cmd == "web":
        run_web()
    elif cmd == "test":
        run_test()
    elif cmd == "cli":
        run_cli()
    elif cmd in ("--help", "-h", "help"):
        print(__doc__)
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)
