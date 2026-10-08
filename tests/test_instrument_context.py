"""Reports must name the company, not just its code.

`build_instrument_context` supplied the code and nothing else, then instructed the
model to use "this exact ticker in **every tool call, report, and
recommendation**". The model complied: headings and prose carried `600487`, and a
reader had to look up which company that is. The instruction now separates the two
audiences — **prose gets the name, tool arguments get the code** — and the name is
resolved once per process.
"""

from __future__ import annotations

import pytest

from marvel.agents.utils import agent_utils


@pytest.fixture(autouse=True)
def _clear_name_cache():
    def clear():
        # monkeypatch may have replaced the cached function with a plain lambda
        getattr(agent_utils._cached_stock_name, "cache_clear", lambda: None)()

    clear()
    yield
    clear()


@pytest.mark.unit
class TestInstrumentContextNamesTheCompany:
    def test_the_name_is_included_when_it_resolves(self, monkeypatch):
        monkeypatch.setattr(
            agent_utils, "_cached_stock_name", lambda code: "亨通光电"
        )

        context = agent_utils.build_instrument_context("600487")

        assert "亨通光电" in context
        assert "600487" in context

    def test_the_old_harmful_instruction_is_gone(self, monkeypatch):
        """'use this ticker in every report' is what produced code-only prose."""
        monkeypatch.setattr(agent_utils, "_cached_stock_name", lambda code: "亨通光电")

        context = agent_utils.build_instrument_context("600487")

        assert "in every tool call, report, and recommendation" not in context

    def test_prose_and_tool_arguments_are_told_apart(self, monkeypatch):
        monkeypatch.setattr(agent_utils, "_cached_stock_name", lambda code: "亨通光电")

        context = agent_utils.build_instrument_context("600487")

        assert "company name" in context.lower()
        assert "tool argument" in context.lower()
        assert "ticker" in context, "没有说明工具参数该传什么"

    def test_it_degrades_to_code_only_without_a_name(self, monkeypatch):
        """An unreachable name lookup must not break the run."""
        monkeypatch.setattr(agent_utils, "_cached_stock_name", lambda code: "")

        context = agent_utils.build_instrument_context("600487")

        assert "600487" in context
        assert "state the company or instrument name" in context

    def test_it_never_raises_when_the_lookup_explodes(self, monkeypatch):
        def boom(code):
            raise RuntimeError("网络不通")

        monkeypatch.setattr(agent_utils, "_cached_stock_name", boom)

        context = agent_utils.build_instrument_context("600487")

        assert "600487" in context

    def test_a_non_a_share_is_not_looked_up(self, monkeypatch):
        def explode(code):
            raise AssertionError("非 A 股不该触发名称查询")

        monkeypatch.setattr(agent_utils, "_cached_stock_name", explode)

        context = agent_utils.build_instrument_context("7203.T")

        assert "7203.T" in context, "外部代码的原文必须保留"
        assert "state the company or instrument name" in context


@pytest.mark.unit
class TestNameLookupIsCached:
    def test_repeated_calls_hit_the_network_once(self, monkeypatch):
        calls: list[str] = []

        def fake_get_stock_name(code):
            calls.append(code)
            return "亨通光电"

        import marvel.dataflows.a_stock as a_stock

        monkeypatch.setattr(a_stock, "get_stock_name", fake_get_stock_name)

        for _ in range(5):
            agent_utils.build_instrument_context("600487")

        assert calls == ["600487"], (
            f"每个 agent 节点都会组装一次提示词，名称查询必须缓存（实际查询 {len(calls)} 次）"
        )

    def test_the_cache_is_keyed_by_code(self, monkeypatch):
        import marvel.dataflows.a_stock as a_stock

        monkeypatch.setattr(
            a_stock, "get_stock_name",
            lambda code: {"600487": "亨通光电", "600519": "贵州茅台"}.get(code, ""),
        )

        assert "亨通光电" in agent_utils.build_instrument_context("600487")
        assert "贵州茅台" in agent_utils.build_instrument_context("600519")

    def test_a_failed_lookup_is_also_cached(self, monkeypatch):
        """Otherwise every node re-attempts a failing HTTP call."""
        calls: list[str] = []

        def failing(code):
            calls.append(code)
            raise RuntimeError("不通")

        import marvel.dataflows.a_stock as a_stock

        monkeypatch.setattr(a_stock, "get_stock_name", failing)

        agent_utils.build_instrument_context("600487")
        agent_utils.build_instrument_context("600487")

        assert len(calls) <= 1, "失败结果没有缓存，每个节点都会再打一次网络"
