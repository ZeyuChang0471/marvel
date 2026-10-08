"""Output must be in the configured language, and every agent must be told so.

Three separate causes made Chinese runs produce English text:

1. the bull and bear researchers were the only prompt-producing agents with **no
   language instruction at all** — and the investment debate they generate is what
   the Research Manager summarises into the plan the trader executes;
2. the structured-output schemas' field descriptions *are* the model's output
   instructions (their own docstring says so) and were written entirely in English,
   so the three decision stages received English instructions that competed with
   the single Chinese sentence appended to the prompt body;
3. the markdown renderers emit English section headers.

(3) is deliberately left alone: `**Rating**:` is a machine-parsed keyword —
`agents/utils/rating.py` keys on it, and so do the CLI and the Web UI sections.
Changing it would break rating extraction to fix a cosmetic word.
"""

from __future__ import annotations

import pytest

from marvel.dataflows.config import set_config
from marvel.dataflows.config import set_config as _set_config


@pytest.fixture(autouse=True)
def _english_by_default():
    """Keep the process-global config from leaking between tests."""
    set_config({"output_language": "English"})
    yield
    set_config({"output_language": "English"})


# ---------------------------------------------------------------------------
# 1. Every prompt-producing agent gets the instruction
# ---------------------------------------------------------------------------

_PROMPT_AGENTS = [
    "analysts/market_analyst.py", "analysts/social_media_analyst.py",
    "analysts/news_analyst.py", "analysts/fundamentals_analyst.py",
    "analysts/policy_analyst.py", "analysts/hot_money_tracker.py",
    "analysts/lockup_watcher.py", "analysts/volume_price_analyst.py",
    "analysts/macro_analyst.py",
    "researchers/bull_researcher.py", "researchers/bear_researcher.py",
    "managers/research_manager.py", "managers/portfolio_manager.py",
    "trader/trader.py",
    "risk_mgmt/aggressive_debator.py", "risk_mgmt/conservative_debator.py",
    "risk_mgmt/neutral_debator.py",
]


@pytest.mark.unit
class TestEveryAgentAsksForTheLanguage:
    @pytest.mark.parametrize("relative", _PROMPT_AGENTS)
    def test_the_agent_uses_the_language_instruction(self, relative):
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "marvel" / "agents" / relative
        source = path.read_text(encoding="utf-8")

        assert "get_language_instruction" in source, (
            f"{relative} 从不要求输出语言——配置成中文时它仍会输出英文"
        )

    def test_the_debate_is_no_longer_an_exception(self):
        """The comment used to justify English debate; the code must not."""
        from marvel.agents.utils import agent_utils

        doc = agent_utils.get_language_instruction.__doc__ or ""
        assert "Internal debate agents stay in English" not in doc, (
            "注释还在为「辩论保持英文」辩护，而多空辩手现在已经要求中文"
        )


# ---------------------------------------------------------------------------
# 2. get_language_instruction itself
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLanguageInstruction:
    def test_english_costs_nothing(self):
        from marvel.agents.utils.agent_utils import get_language_instruction

        set_config({"output_language": "English"})
        assert get_language_instruction() == ""

    def test_chinese_is_explicit_and_forbids_english(self):
        from marvel.agents.utils.agent_utils import get_language_instruction

        set_config({"output_language": "Chinese"})
        instruction = get_language_instruction()

        assert "Chinese" in instruction
        assert "English" in instruction, "没有明确禁止英文，弱模型仍可能整段用英文回答"
        assert "heading" in instruction, "表头/标题最容易漏掉"

    def test_a_missing_key_does_not_silently_mean_english(self):
        """`get_config()` always carries the key; assert the fallback is safe."""
        from marvel.agents.utils.agent_utils import get_language_instruction

        set_config({"output_language": "Japanese"})
        assert "Japanese" in get_language_instruction()


# ---------------------------------------------------------------------------
# 3. Structured-output schemas carry the language too
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSchemaLocalisation:
    def test_the_clause_is_appended_to_every_field(self):
        from marvel.agents.schemas import PortfolioDecision
        from marvel.agents.utils.structured import localize_schema

        set_config({"output_language": "Chinese"})
        localize_schema(PortfolioDecision)

        for name, field in PortfolioDecision.model_fields.items():
            assert "Write this field in Chinese." in (field.description or ""), (
                f"{name} 的描述里没有语言要求——而描述就是模型的输出指令"
            )

    def test_applying_twice_does_not_stack(self):
        from marvel.agents.schemas import ResearchPlan
        from marvel.agents.utils.structured import localize_schema

        set_config({"output_language": "Chinese"})
        localize_schema(ResearchPlan)
        once = ResearchPlan.model_fields["rationale"].description
        localize_schema(ResearchPlan)

        assert ResearchPlan.model_fields["rationale"].description == once

    def test_switching_back_to_english_removes_the_clause(self):
        """The CLI lets the user pick a language per run; clauses must not persist."""
        from marvel.agents.schemas import ResearchPlan
        from marvel.agents.utils.structured import (
            _BASE_DESCRIPTIONS,
            localize_schema,
        )

        set_config({"output_language": "Chinese"})
        localize_schema(ResearchPlan)
        set_config({"output_language": "English"})
        localize_schema(ResearchPlan)

        base = _BASE_DESCRIPTIONS[ResearchPlan]["rationale"]
        assert ResearchPlan.model_fields["rationale"].description == base
        assert "Write this field in" not in ResearchPlan.model_fields["rationale"].description

    def test_binding_a_schema_localises_it(self):
        from marvel.agents.schemas import PortfolioDecision
        from marvel.agents.utils import structured as structured_mod

        class FakeLLM:
            def with_structured_output(self, schema):
                return schema

        set_config({"output_language": "Chinese"})
        structured_mod.bind_structured(FakeLLM(), PortfolioDecision, "Portfolio Manager")

        assert "Write this field in Chinese." in (
            PortfolioDecision.model_fields["executive_summary"].description or ""
        )


# ---------------------------------------------------------------------------
# 4. The parse-stable header stays English on purpose
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRatingHeaderStaysParseable:
    def test_the_rendered_decision_still_parses(self):
        """Localising `**Rating**:` would break rating extraction everywhere."""
        from marvel.agents.schemas import PortfolioDecision, render_pm_decision
        from marvel.agents.utils.rating import parse_rating

        set_config({"output_language": "Chinese"})
        decision = PortfolioDecision(
            rating="Sell", executive_summary="摘要", investment_thesis="论据",
        )
        rendered = render_pm_decision(decision)

        assert "**Rating**: Sell" in rendered
        assert parse_rating(rendered) == "Sell"
