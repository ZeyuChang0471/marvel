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

注意：本文件在 import 时**不做任何事**。此前的版本在模块层就重配 stdio、
  `set_config()`、打印 banner、检查 key 并在缺失时 `sys.exit(1)`——import 它
  （IDE 打开、pytest 收集、被别的脚本引用）会直接终止进程。全部逻辑现在都在
  `main()` 里，只有 `python run.py` 才会执行。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent

# ── 预设配置：DeepSeek V4 Pro + V4 Flash + 中文输出 ──────────────────────
CONFIG_OVERRIDES = {
    "llm_provider": "deepseek",
    "deep_think_llm": "deepseek-v4-pro",
    "quick_think_llm": "deepseek-flash",
    "output_language": "Chinese",
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
}


def _configure_stdio() -> None:
    """Windows 控制台 UTF-8 编码修复。

    ``sys.stdin`` 在 ``pythonw.exe`` / 管道关闭时可能是 None，所以三路都要判空。
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — 编码修复失败不该阻断运行
            pass


def _load_config() -> dict:
    """加载 .env、应用预设配置，返回当前 config。"""
    from dotenv import load_dotenv

    from marvel.dataflows.config import get_config, set_config

    load_dotenv(_PROJECT_ROOT / ".env")
    set_config(CONFIG_OVERRIDES)
    return get_config()


def _print_banner(config: dict) -> None:
    print("=" * 60)
    print("  MARVEL — DeepSeek 配置")
    print("=" * 60)
    print(f"  供应商:    {config['llm_provider']}")
    print(f"  深度思考:  {config['deep_think_llm']}")
    print(f"  快速思考:  {config['quick_think_llm']}")
    print(f"  输出语言:  {config['output_language']}")
    print("=" * 60)


def _require_api_key() -> str:
    """确认 DEEPSEEK_API_KEY 存在；缺失时给出可执行的提示并退出。

    只用变量名报警，**绝不打印 key 片段**——此前这里会打印
    ``api_key[:12]...{api_key[-4:]}``，十几个真实密钥字符会留在终端 scrollback、
    启动脚本日志与 CI 记录里。项目自己的惯例是只显示末四位（见
    ``web/components/sidebar.py::_mask_key``）。
    """
    import os

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print()
        print("❌ 未找到 DEEPSEEK_API_KEY！")
        print("   请在项目根目录的 .env 文件中填入：DEEPSEEK_API_KEY=sk-你的key")
        print("   然后重新运行。")
        raise SystemExit(1)
    print("  API Key:   已配置（来自 .env 或环境变量）")
    return api_key


# ── 命令实现 ────────────────────────────────────────────────────────────


def run_cli() -> None:
    """CLI 交互模式：手动选择股票和分析参数。"""
    from cli.main import app

    # Typer 只认识 `analyze`，而 main() 已经把 "cli" 这个 token 消费掉了：不摘掉
    # 它，Typer 会把它当成多余的位置参数并以 "Got unexpected extra argument(s)"
    # 退出，交互式 CLI 根本起不来。
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    # typer 会接管交互，我们在前面已经预设了 config
    app()


def run_test() -> dict:
    """快速测试：用贵州茅台 600519 跑通主流程。

    走与 Web UI 相同的驱动路径（``prepare_graph_run`` → stream →
    ``finalize_graph_run``）。此前这里直接调 ``propagator.create_initial_state``
    和 ``graph._log_state``，绕过了 checkpoint / 记忆日志 / 断点清理——即 CLI
    那条路径的老问题，只是没人报错。
    """
    from datetime import date

    from marvel.graph.trading_graph import MarvelGraph

    config = _load_config()
    ticker = "600519"
    analysis_date = date.today().strftime("%Y-%m-%d")

    print(f"\n🚀 开始测试分析: {ticker} ({analysis_date})")
    print(f"   深度思考模型: {config['deep_think_llm']}")
    print(f"   快速思考模型: {config['quick_think_llm']}")
    print(f"   输出语言:    {config['output_language']}")
    print()

    graph = MarvelGraph(debug=True, config=config)
    try:
        init_state, args, _ = graph.prepare_graph_run(ticker, analysis_date)

        last_chunk: dict = {}
        for chunk in graph.graph.stream(init_state, **args):
            last_chunk = chunk
            if chunk.get("final_trade_decision"):
                print("\n✅ 分析完成！最终决策已生成。")

        if not last_chunk:
            raise RuntimeError("分析没有返回任何结果")

        # 落盘 + 写入记忆日志 + 清理断点，都在这一步里完成
        signal = graph.finalize_graph_run(ticker, analysis_date, last_chunk)
    finally:
        graph.close_graph_run()

    final_decision = last_chunk.get("final_trade_decision", "")
    if final_decision:
        print("\n" + "=" * 60)
        print("  最终交易决策")
        print("=" * 60)
        print(final_decision[:2000])

    print(f"\n📊 评级: {signal}")
    print(f"📁 日志目录: {config['results_dir']}/{ticker}/marvel_strategy_logs/")
    return last_chunk


def run_web() -> None:
    """启动 Streamlit Web UI。"""
    from streamlit.web import cli as stcli

    app_path = str(_PROJECT_ROOT / "web" / "app.py")
    print("🌐 启动 Web UI... 浏览器打开后即可使用。")
    print("   侧边栏选择 LLM 供应商 → DeepSeek，即可选中 V4 Pro/Flash。")
    print()
    sys.argv = ["streamlit", "run", app_path]
    stcli.main()


def main() -> None:
    """入口：解析命令、准备配置、分发。"""
    _configure_stdio()
    config = _load_config()

    command = sys.argv[1].lower() if len(sys.argv) > 1 else "cli"

    if command in ("--help", "-h", "help"):
        print(__doc__)
        return

    _print_banner(config)

    if command == "web":
        # Web UI 在侧边栏自己管 key，这里不拦
        run_web()
    elif command == "test":
        _require_api_key()
        print()
        run_test()
    elif command == "cli":
        _require_api_key()
        print()
        run_cli()
    else:
        print(f"未知命令: {command}")
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
