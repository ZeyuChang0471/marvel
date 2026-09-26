"""A node that rewrites a nested debate state must re-emit every declared key.

LangGraph has no per-key reducer for a `TypedDict`-valued channel: returning
`{"risk_debate_state": {...}}` **replaces** the whole value, so any key the node
does not re-emit silently disappears from the state.

That happened: the three risk debators and the two researchers all returned
partial dicts that omitted `judge_decision`, so from the first risk-debator step
onward the declared key was absent. Nothing crashed only because the Portfolio
Manager happens to rewrite that same key before the single direct reader
(`trading_graph._log_state`) runs — ordering luck, not design, and a landmine for
any new reader placed between the debators and the PM.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage


class _StubLLM:
    """Minimal `invoke`-only model: the debate nodes build plain-string prompts."""

    def invoke(self, prompt, config=None, **kwargs):
        return AIMessage(content="桩发言")


def _investment_state(**overrides) -> dict:
    state = {
        "bull_history": "", "bear_history": "", "history": "",
        "current_response": "", "judge_decision": "上一轮的裁决",
        "count": 0,
    }
    state.update(overrides)
    return state


def _risk_state(**overrides) -> dict:
    state = {
        "aggressive_history": "", "conservative_history": "", "neutral_history": "",
        "history": "", "latest_speaker": "",
        "current_aggressive_response": "", "current_conservative_response": "",
        "current_neutral_response": "", "judge_decision": "上一轮的裁决",
        "count": 0,
    }
    state.update(overrides)
    return state


def _base_state(**overrides) -> dict:
    state = {
        "trade_date": "2026-05-12",
        "company_of_interest": "600519",
        "messages": [],
        "market_report": "报告", "sentiment_report": "报告", "news_report": "报告",
        "fundamentals_report": "报告", "policy_report": "报告",
        "hot_money_report": "报告", "lockup_report": "报告",
        "volume_price_report": "报告", "macro_report": "报告",
        "data_quality_summary": "",
        "investment_plan": "", "trader_investment_plan": "",
        "investment_debate_state": _investment_state(),
        "risk_debate_state": _risk_state(),
    }
    state.update(overrides)
    return state


INVESTMENT_KEYS = {
    "bull_history", "bear_history", "history", "current_response",
    "judge_decision", "count",
}
RISK_KEYS = {
    "aggressive_history", "conservative_history", "neutral_history", "history",
    "latest_speaker", "current_aggressive_response",
    "current_conservative_response", "current_neutral_response",
    "judge_decision", "count",
}


def _assert_contract(returned: dict, expected: set[str], node_name: str) -> None:
    missing = expected - set(returned)
    assert not missing, (
        f"{node_name} 重写了嵌套状态却没有重新给出 {sorted(missing)}——"
        "LangGraph 会整块替换，这些键会从状态里消失"
    )


@pytest.mark.unit
class TestDebateNodesPreserveDeclaredKeys:
    def test_bull_researcher(self):
        from marvel.agents.researchers.bull_researcher import create_bull_researcher

        result = create_bull_researcher(_StubLLM())(
            _base_state(investment_debate_state=_investment_state())
        )

        _assert_contract(result["investment_debate_state"], INVESTMENT_KEYS, "Bull Researcher")

    def test_bear_researcher(self):
        from marvel.agents.researchers.bear_researcher import create_bear_researcher

        result = create_bear_researcher(_StubLLM())(
            _base_state(investment_debate_state=_investment_state())
        )

        _assert_contract(result["investment_debate_state"], INVESTMENT_KEYS, "Bear Researcher")

    def test_aggressive_debator(self):
        from marvel.agents.risk_mgmt.aggressive_debator import create_aggressive_debator

        result = create_aggressive_debator(_StubLLM())(
            _base_state(risk_debate_state=_risk_state())
        )

        _assert_contract(result["risk_debate_state"], RISK_KEYS, "Aggressive Analyst")

    def test_conservative_debator(self):
        from marvel.agents.risk_mgmt.conservative_debator import create_conservative_debator

        result = create_conservative_debator(_StubLLM())(
            _base_state(risk_debate_state=_risk_state())
        )

        _assert_contract(result["risk_debate_state"], RISK_KEYS, "Conservative Analyst")

    def test_neutral_debator(self):
        from marvel.agents.risk_mgmt.neutral_debator import create_neutral_debator

        result = create_neutral_debator(_StubLLM())(
            _base_state(risk_debate_state=_risk_state())
        )

        _assert_contract(result["risk_debate_state"], RISK_KEYS, "Neutral Analyst")

    def test_a_prior_verdict_is_carried_through_not_replaced_by_empty(self):
        """Preserving the key must preserve its value, not reset it."""
        from marvel.agents.risk_mgmt.aggressive_debator import create_aggressive_debator

        result = create_aggressive_debator(_StubLLM())(
            _base_state(risk_debate_state=_risk_state(judge_decision="先前的裁决"))
        )

        assert result["risk_debate_state"]["judge_decision"] == "先前的裁决"

    def test_the_declared_key_sets_match_agent_states(self):
        """The expected sets above must track the TypedDicts, not drift from them."""
        from marvel.agents.utils.agent_states import InvestDebateState, RiskDebateState

        assert set(InvestDebateState.__annotations__) == INVESTMENT_KEYS
        assert set(RiskDebateState.__annotations__) == RISK_KEYS
