"""Tests for debate_utils and the round-goal / report injection into the debate.

Two things are pinned here:
  1. ``default_round_goal`` behaviour (adapted from TradingAgents-AShare).
  2. The two new analyst reports actually reach the Bull/Bear researchers and
     the three risk debators — without that the volume-price and macro analysts
     would run but never influence a decision, which is the "hidden gap" this
     fork already had to fix once for policy / hot_money / lockup.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from marvel.agents.researchers.bear_researcher import create_bear_researcher
from marvel.agents.researchers.bull_researcher import create_bull_researcher
from marvel.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
from marvel.agents.risk_mgmt.conservative_debator import create_conservative_debator
from marvel.agents.risk_mgmt.neutral_debator import create_neutral_debator
from marvel.agents.utils.debate_utils import default_round_goal


class _FakeLLM:
    def __init__(self):
        self.prompt: str | None = None

    def invoke(self, prompt):
        self.prompt = prompt
        return AIMessage(content="argument")


REPORTS = {
    "market_report": "MARKET_BODY",
    "sentiment_report": "SENTIMENT_BODY",
    "news_report": "NEWS_BODY",
    "fundamentals_report": "FUNDAMENTALS_BODY",
    "policy_report": "POLICY_BODY",
    "hot_money_report": "HOTMONEY_BODY",
    "lockup_report": "LOCKUP_BODY",
    "volume_price_report": "VOLPRICE_BODY",
    "macro_report": "MACRO_BODY",
    "data_quality_summary": "QUALITY_BODY",
}


def _investment_state(count: int = 0) -> dict:
    return {
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "count": count,
        },
        **REPORTS,
    }


def _risk_state(count: int = 0) -> dict:
    return {
        "risk_debate_state": {
            "history": "",
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "latest_speaker": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "judge_decision": "",
            "count": count,
        },
        "trader_investment_plan": "PLAN_BODY",
        **REPORTS,
    }


class TestDefaultRoundGoal:
    @pytest.mark.unit
    def test_investment_rounds_are_distinct(self):
        goals = [default_round_goal("investment", n) for n in range(1, 6)]
        assert len(set(goals)) == 5, "rounds must not repeat the same objective"
        assert all(goals)

    @pytest.mark.unit
    def test_risk_rounds_are_distinct(self):
        goals = [default_round_goal("risk", n) for n in range(1, 6)]
        assert len(set(goals)) == 5
        assert all(goals)

    @pytest.mark.unit
    def test_domains_differ(self):
        assert default_round_goal("investment", 1) != default_round_goal("risk", 1)

    @pytest.mark.unit
    def test_unknown_domain_falls_back_to_investment(self):
        assert default_round_goal("nonsense", 1) == default_round_goal("investment", 1)

    @pytest.mark.unit
    @pytest.mark.parametrize("count", [0, -5, 99, 1000])
    def test_out_of_range_counts_are_clamped(self, count):
        goal = default_round_goal("investment", count)
        assert isinstance(goal, str) and goal


class TestInvestmentDebateInjection:
    @pytest.mark.unit
    def test_bull_prompt_carries_round_goal(self):
        llm = _FakeLLM()
        create_bull_researcher(llm)(_investment_state(0))
        assert "第 1 轮" in llm.prompt
        assert default_round_goal("investment", 1) in llm.prompt

    @pytest.mark.unit
    def test_bull_prompt_advances_to_round_two(self):
        llm = _FakeLLM()
        create_bull_researcher(llm)(_investment_state(1))
        assert "第 2 轮" in llm.prompt
        assert default_round_goal("investment", 2) in llm.prompt

    @pytest.mark.unit
    def test_bear_prompt_carries_round_goal(self):
        llm = _FakeLLM()
        create_bear_researcher(llm)(_investment_state(0))
        assert "第 1 轮" in llm.prompt
        assert default_round_goal("investment", 1) in llm.prompt

    @pytest.mark.unit
    @pytest.mark.parametrize("factory", [create_bull_researcher, create_bear_researcher])
    def test_new_reports_reach_the_researchers(self, factory):
        llm = _FakeLLM()
        factory(llm)(_investment_state(0))
        assert "VOLPRICE_BODY" in llm.prompt
        assert "MACRO_BODY" in llm.prompt


class TestRiskDebateInjection:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "factory",
        [create_aggressive_debator, create_conservative_debator, create_neutral_debator],
    )
    def test_prompt_carries_round_goal(self, factory):
        llm = _FakeLLM()
        factory(llm)(_risk_state(0))
        assert "第 1 轮" in llm.prompt
        assert default_round_goal("risk", 1) in llm.prompt

    @pytest.mark.unit
    def test_prompt_advances_to_round_two(self):
        llm = _FakeLLM()
        create_aggressive_debator(llm)(_risk_state(1))
        assert "第 2 轮" in llm.prompt
        assert default_round_goal("risk", 2) in llm.prompt

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "factory",
        [create_aggressive_debator, create_conservative_debator, create_neutral_debator],
    )
    def test_new_reports_reach_the_debators(self, factory):
        llm = _FakeLLM()
        factory(llm)(_risk_state(0))
        assert "VOLPRICE_BODY" in llm.prompt
        assert "MACRO_BODY" in llm.prompt
        assert "PLAN_BODY" in llm.prompt
