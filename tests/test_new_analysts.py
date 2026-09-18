"""Tests for the Volume Price Analyst and the Macro Analyst.

These two analysts were extracted from KylinMountain/TradingAgents-AShare; the
framework text is carried over, the node plumbing is MARVEL's. The tests pin
both: the analytical content must survive, and the node must follow MARVEL's
conventions (report key, one message, empty report while tools are still being
called).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from marvel.agents.analysts.macro_analyst import create_macro_analyst
from marvel.agents.analysts.volume_price_analyst import create_volume_price_analyst
from marvel.graph.conditional_logic import ConditionalLogic


class _FakeLLM:
    """Captures the rendered prompt and the bound tools, then replies."""

    def __init__(self, reply: AIMessage | None = None):
        self.reply = reply or AIMessage(content="REPORT BODY")
        self.prompt: list | None = None
        self.tools: list | None = None

    def bind_tools(self, tools):
        self.tools = list(tools)

        def _call(messages):
            self.prompt = messages
            return self.reply

        return RunnableLambda(_call)


def _state(ticker: str = "600519") -> dict:
    return {
        "trade_date": "2026-09-18",
        "company_of_interest": ticker,
        "messages": [HumanMessage(content=f"分析 {ticker}")],
    }


def _system_text(llm: _FakeLLM) -> str:
    assert llm.prompt is not None, "the LLM was never invoked"
    prompt = llm.prompt
    # `prompt | RunnableLambda` hands the lambda a ChatPromptValue, not a list.
    if hasattr(prompt, "to_messages"):
        prompt = prompt.to_messages()
    return "\n".join(str(m.content) for m in prompt)


def _tool_names(llm: _FakeLLM) -> set[str]:
    assert llm.tools is not None, "no tools were bound"
    return {t.name for t in llm.tools}


class TestVolumePriceAnalyst:
    @pytest.mark.unit
    def test_returns_the_report_key(self):
        llm = _FakeLLM()
        node = create_volume_price_analyst(llm)
        result = node(_state())
        assert result["volume_price_report"] == "REPORT BODY"
        assert len(result["messages"]) == 1

    @pytest.mark.unit
    def test_binds_the_kline_and_indicator_tools(self):
        llm = _FakeLLM()
        create_volume_price_analyst(llm)(_state())
        assert _tool_names(llm) == {"get_stock_data", "get_indicators"}

    @pytest.mark.unit
    def test_empty_report_while_tools_are_still_being_called(self):
        """MARVEL's convention: no report until the agent stops calling tools."""
        reply = AIMessage(
            content="",
            tool_calls=[{"name": "get_stock_data", "args": {}, "id": "call_1"}],
        )
        node = create_volume_price_analyst(_FakeLLM(reply))
        result = node(_state())
        assert result["volume_price_report"] == ""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "phrase",
        [
            "Anna Coulling",
            "威科夫三大定律",
            "供求定律",
            "因果定律",
            "投入产出定律",
            "吸筹阶段",
            "派筹阶段",
            "抛售高峰",
            "买入高峰",
            "射击十字星",
            "锤头线",
            "吊人线",
            "长腿十字线",
            "房屋法则",
            "成交量是唯一不能被掩盖的真相",
        ],
    )
    def test_carries_the_volume_price_framework(self, phrase):
        llm = _FakeLLM()
        create_volume_price_analyst(llm)(_state())
        assert phrase in _system_text(llm)

    @pytest.mark.unit
    def test_carries_the_a_share_specifics(self):
        llm = _FakeLLM()
        create_volume_price_analyst(llm)(_state())
        text = _system_text(llm)
        for phrase in ("T+1 制度", "涨跌停制度", "量在价先", "换手率"):
            assert phrase in text, phrase

    @pytest.mark.unit
    def test_renders_the_ticker_and_date(self):
        llm = _FakeLLM()
        create_volume_price_analyst(llm)(_state("000858"))
        text = _system_text(llm)
        assert "000858" in text
        assert "2026-09-18" in text


class TestMacroAnalyst:
    @pytest.mark.unit
    def test_returns_the_report_key(self):
        llm = _FakeLLM()
        node = create_macro_analyst(llm)
        result = node(_state())
        assert result["macro_report"] == "REPORT BODY"
        assert len(result["messages"]) == 1

    @pytest.mark.unit
    def test_binds_the_sector_and_flow_tools(self):
        llm = _FakeLLM()
        create_macro_analyst(llm)(_state())
        assert _tool_names(llm) == {
            "get_industry_comparison",
            "get_concept_blocks",
            "get_northbound_flow",
            "get_news",
            "get_global_news",
        }

    @pytest.mark.unit
    def test_empty_report_while_tools_are_still_being_called(self):
        reply = AIMessage(
            content="",
            tool_calls=[{"name": "get_industry_comparison", "args": {}, "id": "call_1"}],
        )
        node = create_macro_analyst(_FakeLLM(reply))
        assert node(_state())["macro_report"] == ""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "phrase",
        ["板块轮动", "概念题材", "政策驱动", "北向资金", "宏观背景", "行业排名"],
    )
    def test_carries_the_sector_rotation_framework(self, phrase):
        llm = _FakeLLM()
        create_macro_analyst(llm)(_state())
        assert phrase in _system_text(llm)

    @pytest.mark.unit
    def test_renders_the_ticker_and_date(self):
        llm = _FakeLLM()
        create_macro_analyst(llm)(_state("300750"))
        text = _system_text(llm)
        assert "300750" in text
        assert "2026-09-18" in text


class TestConditionalLogic:
    @pytest.mark.unit
    def test_volume_price_routes_to_its_tool_node(self):
        cl = ConditionalLogic()
        state = {"messages": [AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1"}])]}
        assert cl.should_continue_volume_price(state) == "tools_volume_price"

    @pytest.mark.unit
    def test_volume_price_finishes_to_its_clear_node(self):
        cl = ConditionalLogic()
        assert cl.should_continue_volume_price({"messages": [AIMessage(content="done")]}) == (
            "Msg Clear Volume_price"
        )

    @pytest.mark.unit
    def test_macro_routes_to_its_tool_node(self):
        cl = ConditionalLogic()
        state = {"messages": [AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1"}])]}
        assert cl.should_continue_macro(state) == "tools_macro"

    @pytest.mark.unit
    def test_macro_finishes_to_its_clear_node(self):
        cl = ConditionalLogic()
        assert cl.should_continue_macro({"messages": [AIMessage(content="done")]}) == (
            "Msg Clear Macro"
        )
