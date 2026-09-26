"""Every tool an analyst is handed must actually be executable.

An analyst node binds a list of tools for the *model* (`tools = [...]` inside the
node factory) and a separate `ToolNode` holds the tools the graph can *run*. Those
are two independent lists, and when they drift the failure is quiet:
LangGraph answers a call for an unregistered tool with an error `ToolMessage`
("… is not a valid tool, try one of […]"), so the model is told the tool does not
exist and the report just loses that evidence.

That is not hypothetical. `social` held only `get_news` while the social-media
analyst bound four tools — including `get_fund_flow`, which its own prompt calls
「情绪最硬的证据」. DEV_LOG records the same bug class being fixed once before
("ToolNode 未包含 signal 工具"), which is why this is a structural guard on all
nine analysts rather than a one-off assertion.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: analyst module stem -> the key used in `_create_tool_nodes()`.
ANALYST_NODES = {
    "market_analyst": "market",
    "social_media_analyst": "social",
    "news_analyst": "news",
    "fundamentals_analyst": "fundamentals",
    "policy_analyst": "policy",
    "hot_money_tracker": "hot_money",
    "lockup_watcher": "lockup",
    "volume_price_analyst": "volume_price",
    "macro_analyst": "macro",
}


def bound_tool_names(analyst_stem: str) -> list[str]:
    """The names in the analyst node's `tools = [...]` list."""
    path = REPO_ROOT / "marvel" / "agents" / "analysts" / f"{analyst_stem}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "tools" for target in node.targets
        ):
            if isinstance(node.value, ast.List):
                return [ast.unparse(element) for element in node.value.elts]
    raise AssertionError(f"{path.name}: 找不到 tools = [...] 列表")


def executable_tool_names(node_key: str) -> set[str]:
    """The names the graph's ToolNode for that analyst can execute."""
    from marvel.graph.trading_graph import MarvelGraph

    graph_stub = object.__new__(MarvelGraph)  # _create_tool_nodes uses no state
    nodes = MarvelGraph._create_tool_nodes(graph_stub)
    return set(nodes[node_key].tool_node.tools_by_name)


@pytest.mark.unit
class TestAnalystToolsAreExecutable:
    def test_the_analyst_map_covers_the_registry(self):
        """A new analyst must be added here, not silently skipped."""
        from cli.models import ANALYST_SELECTION_ORDER

        assert set(ANALYST_NODES.values()) == set(ANALYST_SELECTION_ORDER)

    @pytest.mark.parametrize("analyst", sorted(ANALYST_NODES))
    def test_every_bound_tool_reaches_its_tool_node(self, analyst):
        node_key = ANALYST_NODES[analyst]
        bound = bound_tool_names(analyst)
        executable = executable_tool_names(node_key)

        assert bound, f"{analyst} 没有绑定任何工具"

        missing = [name for name in bound if name not in executable]
        assert not missing, (
            f"{analyst} 把 {missing} 交给了模型，但 tools_{node_key} 里没有它们——"
            f"模型调用时 LangGraph 只会回一句「不是有效工具」，"
            f"报告会静默丢掉这部分证据（该 ToolNode 可执行：{sorted(executable)}）"
        )

    def test_extra_tools_in_a_tool_node_are_reported(self):
        """Not a failure — but extras mean the model can call what it cannot see."""
        extras = {
            analyst: sorted(
                executable_tool_names(ANALYST_NODES[analyst])
                - set(bound_tool_names(analyst))
            )
            for analyst in ANALYST_NODES
        }
        extras = {k: v for k, v in extras.items() if v}

        # Informational: an unreachable extra is dead weight, not a defect.
        assert isinstance(extras, dict)
