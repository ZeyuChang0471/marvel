"""The CLI front end must describe the same product as the graph.

The CLI kept its own hand-maintained copies of the analyst registry and **every
copy listed only the four upstream analysts** while the graph registers nine.
The worst consequence was functional, not cosmetic: ``cli/main.py`` filtered the
user's selection through its own four-entry list before handing it to
``MarvelGraph``, so the five A-share-specific analysts could not be run from the
CLI at all — and their reports were never displayed.

The CLI also still carried upstream's US-market defaults (``SPY`` plus
US/Canada/Japan/HK examples, every one of which ``safe_ticker_component``
rejects) and upstream's ASCII wordmark, so its first screen introduced the
project under the wrong name while the panel title said MARVEL.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cli.models import (
    ANALYST_DISPLAY_NAMES,
    ANALYST_REPORT_KEYS,
    ANALYST_SELECTION_ORDER,
    AnalystType,
    analyst_agent_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _graph_analyst_keys() -> list[str]:
    """Analyst keys the graph can actually run (marvel/graph/setup.py)."""
    src = (REPO_ROOT / "marvel" / "graph" / "setup.py").read_text(encoding="utf-8")
    return re.findall(r'if "(\w+)" in selected_analysts', src)


@pytest.mark.unit
class TestAnalystRegistryMatchesTheGraph:
    def test_cli_knows_every_analyst_the_graph_can_run(self):
        assert sorted(ANALYST_SELECTION_ORDER) == sorted(_graph_analyst_keys()), (
            "CLI 的分析师清单与 marvel/graph/setup.py 不一致——多出来的选不到，"
            "少掉的会被静默丢弃"
        )

    def test_selection_order_has_no_duplicates(self):
        assert len(ANALYST_SELECTION_ORDER) == len(set(ANALYST_SELECTION_ORDER))

    def test_display_names_and_report_keys_cover_every_key(self):
        for key in ANALYST_SELECTION_ORDER:
            assert key in ANALYST_DISPLAY_NAMES, f"{key} 缺少显示名"
            assert key in ANALYST_REPORT_KEYS, f"{key} 缺少 report state key"

    def test_display_names_are_unique(self):
        names = [ANALYST_DISPLAY_NAMES[k] for k in ANALYST_SELECTION_ORDER]
        assert len(names) == len(set(names)), "显示名重复会让状态面板串行"

    def test_analyst_type_enum_covers_every_key(self):
        assert {m.value for m in AnalystType} == set(ANALYST_SELECTION_ORDER)

    def test_fallback_name_matches_the_graphs_node_naming(self):
        """未登记的分析师也要能渲染，不能 KeyError。"""
        assert analyst_agent_name("brand_new") == "Brand_new Analyst"
        assert analyst_agent_name("market") == "Market Analyst"


@pytest.mark.unit
class TestCliSurfacesEveryAnalyst:
    def test_menu_lists_every_analyst(self):
        from cli import utils

        menu_keys = {value.value for _, value in utils.ANALYST_ORDER}
        assert menu_keys == set(ANALYST_SELECTION_ORDER), (
            "选择菜单漏了分析师，用户就选不到"
        )

    def test_message_buffer_represents_every_analyst(self):
        """行为断言：把 9 个都选上，状态面板和报告区必须都能表示。"""
        from cli.main import MessageBuffer

        buffer = MessageBuffer()
        buffer.init_for_analysis(list(ANALYST_SELECTION_ORDER))

        for key in ANALYST_SELECTION_ORDER:
            assert ANALYST_DISPLAY_NAMES[key] in buffer.agent_status, (
                f"{key} 不在状态面板里"
            )
            assert ANALYST_REPORT_KEYS[key] in buffer.report_sections, (
                f"{key} 的报告区没有建出来"
            )

    def test_selection_filter_keeps_every_selected_analyst(self):
        """cli/main.py 曾用只含 4 项的本地列表过滤选择 → A 股角色被丢掉。"""
        from cli.main import ANALYST_ORDER as main_order

        selected = set(ANALYST_SELECTION_ORDER)
        kept = [a for a in main_order if a in selected]
        assert sorted(kept) == sorted(selected)


@pytest.mark.unit
class TestNoUpstreamDefaultsLeakIntoTheCli:
    @pytest.mark.parametrize("pattern", ["SPY", "CNC.TO", "7203.T", "0700.HK"])
    def test_no_us_or_foreign_ticker_examples(self, pattern):
        for name in ("main.py", "utils.py"):
            src = (REPO_ROOT / "cli" / name).read_text(encoding="utf-8")
            # 允许出现在解释「为什么删掉」的注释里
            offenders = [
                line for line in src.splitlines()
                if pattern in line and not line.lstrip().startswith("#")
            ]
            assert not offenders, f"cli/{name} 仍在使用 {pattern}: {offenders}"

    def test_default_ticker_is_an_a_share(self):
        src = (REPO_ROOT / "cli" / "main.py").read_text(encoding="utf-8")
        defaults = re.findall(r'typer\.prompt\([^)]*default="([^"]+)"', src)
        assert defaults, "找不到 ticker 默认值"
        for value in defaults:
            assert re.fullmatch(r"\d{6}", value) or value in ("Y", "N", ""), (
                f"CLI 默认值 {value!r} 不是 6 位 A 股代码"
            )

    def test_no_upstream_copyright_claim(self):
        """曾打印 `© Tauric Research`——那是在主张本项目的版权归属。"""
        src = (REPO_ROOT / "cli" / "main.py").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in src.splitlines() if not line.lstrip().startswith("#")
        )
        assert "©" not in code, "CLI 仍在用 © 主张版权归属"

    def test_the_wordmark_is_not_upstreams(self):
        """ASCII 字标曾拼的是 TradingAgents，而同一屏标题写着 MARVEL。"""
        art = (REPO_ROOT / "cli" / "static" / "welcome.txt").read_text(encoding="utf-8")
        # `/ _  __/________` 是上游字标里 T 的特征片段
        assert "/_  __/________" not in art, "welcome.txt 仍是上游 TradingAgents 字标"
        assert art.strip(), "welcome.txt 不能为空"
        widths = {len(line) for line in art.splitlines() if line.strip()}
        assert len(widths) == 1, f"字标各行宽度不一致，会渲染错位: {widths}"

    def test_upstream_attribution_is_still_present(self):
        """不主张版权 ≠ 抹掉归属：NOTICE 与 CLI 都应保留上游出处。"""
        src = (REPO_ROOT / "cli" / "main.py").read_text(encoding="utf-8")
        assert "TauricResearch/TradingAgents" in src
        notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
        assert "TauricResearch" in notice


@pytest.mark.unit
class TestCliUsesTheSharedDriverPath:
    """CLI 必须走与 Web UI 相同的驱动路径。

    ``cli/main.py::run_analysis`` 是一长串交互式提问（questionary / typer），
    没法在测试里驱动，所以这里只能做**源码级**断言。这不是偷懒的文本匹配，
    而是在行为测试不可行的前提下，钉住「不得再绕开共享驱动」这条不变量——
    被钉住的三件事各自都是真实缺陷：

    * 直接调 ``propagator.create_initial_state`` → 装不上 checkpointer
      （``--checkpoint`` 是空操作）、记忆日志上下文不注入、断点 thread_id 不一致；
    * 直接 ``graph.graph.stream`` → 与 Web UI 走两条不同的流；
    * 直接 ``process_signal`` 收尾 → 状态不落盘（CLI 的分析不出现在 Web 历史里）、
      决策不写记忆日志（反思回路对 CLI 完全失效）、断点不清理。
    """

    @staticmethod
    def _source() -> str:
        import inspect

        from cli import main as cli_main

        return inspect.getsource(cli_main.run_analysis)

    def test_uses_the_shared_driver(self):
        src = self._source()
        assert "prepare_graph_run" in src, "CLI 没有走 prepare_graph_run"
        assert "finalize_graph_run" in src, "CLI 没有走 finalize_graph_run"
        assert "close_graph_run" in src, "CLI 没有释放 checkpointer"

    def test_does_not_reach_into_the_propagator(self):
        """不得自己拼初始状态与图参数。

        `propagator.create_initial_state` / `get_graph_args` 绕开了
        `prepare_graph_run`，于是 checkpointer 装不上（`--checkpoint` 变空操作）、
        记忆日志上下文不注入、断点 thread_id 与落盘用的 ticker 不一致。
        """
        src = self._source()
        assert "graph.propagator." not in src, (
            "CLI 又直接操作 propagator 了——checkpoint 与记忆日志会再次失效"
        )
        assert "graph.process_signal(" not in src, (
            "CLI 又自己收尾了——状态不会落盘、决策不会进记忆日志"
        )

    def test_drives_the_stream_with_the_prepared_state_and_args(self):
        """CLI 自己驱动 stream 是**正确**的：它要按节点渲染进度，Web UI 也一样。

        关键是它必须消费 `prepare_graph_run` 返回的 state/args，而不是自己拼一套。
        """
        src = self._source()
        assert "graph.graph.stream(init_agent_state, **args)" in src, (
            "CLI 应当用 prepare_graph_run 返回的 init_agent_state/args 驱动 stream"
        )
        assert "graph.prepare_graph_run(" in src, (
            "stream 用的 state/args 必须来自 prepare_graph_run"
        )

    def test_reports_a_readable_error_instead_of_a_bare_traceback(self):
        src = self._source()
        assert "except Exception" in src, "分析失败会抛裸 traceback"
        assert "typer.Exit" in src, "失败时应以非零码退出"

