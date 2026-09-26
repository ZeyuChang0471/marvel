"""CLI entry point: ``python -m marvel.harness ...``.

Two modes, both deliberately explicit about spending money:

* ``--replay FILE`` re-runs a recording with no model calls at all;
* otherwise a real DeepSeek run happens, and it says so (model, steps) before
  starting, and writes a recording you can replay later.

Nothing runs at import time — the module only acts under ``__main__``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .loop import AgentLoop
from .models import ReplayModel, create_deepseek_model
from .recorder import RunRecorder
from .registry import ToolRegistry

#: The tool surface a harness run starts with: enough to fetch prices, news and
#: the A-share signal layer, without dragging in the whole analyst tool set.
_DEFAULT_TOOL_SETS = ("core", "news", "signal")


def build_registry(tool_sets=_DEFAULT_TOOL_SETS) -> ToolRegistry:
    """Build a registry from the MARVEL pipeline's own tool objects."""
    from marvel.agents.utils import agent_utils

    tools = []
    if "core" in tool_sets:
        tools += [agent_utils.get_stock_data, agent_utils.get_indicators]
    if "news" in tool_sets:
        tools += [agent_utils.get_news, agent_utils.get_global_news]
    if "signal" in tool_sets:
        tools += [
            agent_utils.get_hot_stocks,
            agent_utils.get_northbound_flow,
            agent_utils.get_industry_comparison,
            agent_utils.get_dragon_tiger_board,
            agent_utils.get_lockup_expiry,
        ]
    return ToolRegistry.from_tools(tools)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m marvel.harness",
        description="Run a task against the MARVEL tool surface with a step budget.",
    )
    parser.add_argument("--task", help="the task text to give the agent")
    parser.add_argument("--ticker", help="A-share code, substituted into the task")
    parser.add_argument(
        "--date",
        dest="analysis_date",
        help="analysis date (YYYY-MM-DD); anchors every tool window",
    )
    parser.add_argument("--model", help="override the DeepSeek model name")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument(
        "--record",
        help="write a JSONL recording here (default: <tmp>/marvel-harness-<ticker>.jsonl)",
    )
    parser.add_argument(
        "--replay",
        help="replay this recording instead of calling the model (no API key needed)",
    )
    parser.add_argument(
        "--tools",
        default=",".join(_DEFAULT_TOOL_SETS),
        help="comma-separated tool sets: core,news,signal",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if not args.replay and not (args.task or args.ticker):
        print("nothing to do: pass --task or --ticker (or --replay FILE)", file=sys.stderr)
        return 2

    task = args.task or (
        f"研究 A 股 {args.ticker}：先取行情与新闻，再给出你的判断与依据。"
    )
    if args.ticker and args.task and args.ticker not in args.task:
        task = f"{args.task}\n标的：{args.ticker}"

    registry = build_registry(
        tuple(s.strip() for s in args.tools.split(",") if s.strip())
    )

    if args.replay:
        model = ReplayModel.from_recording(args.replay)
        print(f"replaying {args.replay} ({model.remaining} recorded model turns)")
        recorder = None
    else:
        record_path = Path(
            args.record
            or Path.cwd() / f"marvel-harness-{args.ticker or 'run'}.jsonl"
        )
        model = create_deepseek_model(args.model)
        recorder = RunRecorder(record_path)
        print(f"model={model.name} steps<={args.max_steps} recording->{record_path}")

    loop = AgentLoop(
        model,
        registry,
        recorder=recorder,
        max_steps=args.max_steps,
        analysis_date=args.analysis_date,
    )
    result = loop.run(task, analysis_date=args.analysis_date)

    if result.hit_step_budget:
        print(
            f"\n[step budget exhausted after {len(result.steps)} steps — the answer "
            f"below is incomplete]",
            file=sys.stderr,
        )
    for error in result.tool_errors:
        print(f"[tool error] {error.name}: {error.error}", file=sys.stderr)

    print("\n" + (result.final or "(no final answer)"))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    raise SystemExit(main())
