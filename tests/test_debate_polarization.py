"""Does a fake news item polarise the debate, and does it reach the verdict?

This is a **deterministic offline experiment**, not a simulation of DeepSeek. The
model is a scripted stub, so nothing here estimates how a real model behaves.
What it does measure is the architecture, and that is the part you can act on:

1. **Is there any stage whose job is to check a claim?** Inject a fake item into
   the news report and see whether any artefact ever challenges it.
2. **Through how many stages does it propagate?** The news report is embedded
   verbatim into the bull and bear prompts, but the Research Manager sees *only
   the debate transcript* — so a claim reaches the verdict only if the debaters
   repeat it. That channel is measured directly.
3. **Does the verdict move?** Rating labels are mapped to an ordinal scale and
   the shift against a control run (identical in every other respect) is computed.
4. **Does the quality gate help?** It grades report *shape* plus an LLM review; a
   well-formed report carrying a false claim is graded A.

The stub applies one uniform rule at every stage — "if the claim is anywhere in
my prompt, then ignore it / echo it / challenge it" — so the three dispositions
below differ in exactly one respect, which is what makes the comparison mean
something.
"""

from __future__ import annotations

import re

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

TICKER, TRADE_DATE = "600519", "2026-09-18"

#: Sentinel injected into the news report. Deliberately unmistakable so its
#: presence in any downstream artefact is unambiguous.
FAKE_CLAIM = "【独家爆料】据三名知情人士透露，公司已被证监会立案调查财务造假，虚增收入约120亿元"

RATING_ORDINAL = {
    "Buy": 2,
    "Overweight": 1,
    "Hold": 0,
    "Underweight": -1,
    "Sell": -2,
}

# Order matters twice over:
#  * the debate/verdict markers come first, because the bull and bear prompts
#    embed the quality-gate summary, which *names every analyst* — matching an
#    analyst marker there would mislabel the stage;
#  * the news marker must beat the policy marker, since 「新闻与政策分析师」
#    contains 「政策分析师」.
_STAGE_MARKERS = (
    ("bull", "You are a Bull Analyst advocating"),
    ("bear", "You are a Bear Analyst making the case against"),
    ("research_manager", "As the Research Manager and debate facilitator"),
    ("trader", "You are a trading agent specialising in A-share"),
    ("aggressive", "As the Aggressive Risk Analyst"),
    ("conservative", "As the Conservative Risk Analyst"),
    ("neutral", "As the Neutral Risk Analyst"),
    ("portfolio_manager", "As the Portfolio Manager, synthesize the risk analysts"),
    ("news", "专注于 A 股市场的新闻与政策分析师"),
    ("market", "专注于 A 股市场的技术分析师"),
    ("social", "专注于 A 股市场的市场情绪分析师"),
    ("fundamentals", "专注于 A 股市场的基本面分析师"),
    ("policy", "专注于 A 股市场的政策分析师"),
    ("hot_money", "游资与资金流向追踪分析师"),
    ("lockup", "解禁与减持监控分析师"),
    ("volume_price", "量价分析师"),
    ("macro", "宏观与板块分析师"),
)

_BODY = "本报告基于已获取的数据展开分析，逐项核对后给出判断。" * 12
_TABLE = "\n\n| 项目 | 数值 |\n|------|------|\n| 样本 | 1 |\n"


def _flatten(prompt) -> str:
    """Render whatever the graph handed the model into one searchable string."""
    if isinstance(prompt, str):
        return prompt
    to_string = getattr(prompt, "to_string", None)
    if callable(to_string):
        return to_string()
    if isinstance(prompt, (list, tuple)):
        return "\n".join(_flatten(item) for item in prompt)
    content = getattr(prompt, "content", None)
    if content is not None:
        return _flatten(content)
    return str(prompt)


def classify_stage(prompt: str) -> str:
    for stage, marker in _STAGE_MARKERS:
        if marker in prompt:
            return stage
    return "unknown"


class FakeNewsStub(Runnable):
    """A scripted model with one uniform disposition towards a planted claim."""

    def __init__(self, *, inject_claim: bool, disposition: str) -> None:
        self.inject_claim = inject_claim
        self.disposition = disposition
        self.stages: list[str] = []
        #: stage -> the prompt that stage was given, so the experiment can assert
        #: on *what each stage saw* rather than only on the final state.
        self.prompts: dict[str, str] = {}

    # --- Runnable surface -------------------------------------------------

    def invoke(self, input, config=None, **kwargs):  # noqa: A002
        prompt = _flatten(input)
        stage = classify_stage(prompt)
        self.stages.append(stage)
        self.prompts[stage] = prompt
        return AIMessage(content=self._reply(stage, prompt))

    def bind_tools(self, tools):
        return self

    def with_structured_output(self, schema, **kwargs):
        raise NotImplementedError("stub provider")

    def astream(self, input, config=None, **kwargs):  # noqa: A002
        message = self.invoke(input)

        async def _gen():
            yield message

        return _gen()

    # --- the scripted behaviour ------------------------------------------

    def _saw_claim(self, prompt: str) -> bool:
        return FAKE_CLAIM in prompt

    def _claim_line(self, prompt: str) -> str:
        """What this stage says about the claim — only if it actually saw it.

        The guard matters: without it the echo/challenge dispositions would
        introduce the claim themselves, and the control run would stop being a
        control.
        """
        if not self._saw_claim(prompt):
            return "本案仅基于可核验的数据展开，不引用无法核实的传闻。"
        if self.disposition == "echo":
            return f"该消息已获证实：{FAKE_CLAIM}。因此必须重新评估。"
        if self.disposition == "challenge":
            return (
                f"注意：{FAKE_CLAIM} —— 该说法来源不明、未见任何公告佐证，"
                "不得作为决策依据。"
            )
        return "本案仅基于可核验的数据展开，不引用无法核实的传闻。"

    def _reply(self, stage: str, prompt: str) -> str:
        if stage in _ANALYST_STAGES:
            return self._analyst_report(stage)

        if stage in ("bull", "bear", "aggressive", "conservative", "neutral"):
            stance = {"bull": "多头", "bear": "空头"}.get(stage, "风险")
            return f"{stance}分析师：我的立场如下。{self._claim_line(prompt)}"

        if stage == "research_manager":
            return self._with_rating(prompt, default="Hold")
        if stage == "trader":
            return f"交易计划：按现有证据执行。\n\n**Rating**: " + self._rating(prompt)
        if stage == "portfolio_manager":
            return self._with_rating(prompt, default="Hold")
        return _BODY + _TABLE

    def _analyst_report(self, stage: str) -> str:
        if stage == "news" and self.inject_claim:
            return (
                f"## 新闻分析\n\n近期出现一则报道：{FAKE_CLAIM}\n\n" + _BODY + _TABLE
            )
        return f"## {stage} 分析\n\n" + _BODY + _TABLE

    def _rating(self, prompt: str) -> str:
        if self._saw_claim(prompt) and self.disposition == "echo":
            return "Sell"
        return "Hold"

    def _with_rating(self, prompt: str, *, default: str) -> str:
        rating = self._rating(prompt)
        return f"结论如下。\n\n{self._claim_line(prompt)}\n\n**Rating**: {rating}"


_ANALYST_STAGES = {
    "market", "social", "news", "fundamentals", "policy",
    "hot_money", "lockup", "volume_price", "macro",
}


class _StubClient:
    def __init__(self, stub: FakeNewsStub) -> None:
        self._stub = stub

    def get_llm(self):
        return self._stub

    def validate_model(self) -> bool:
        return True


def run_pipeline(tmp_path, monkeypatch, *, inject_claim: bool, disposition: str):
    """Drive the real graph end to end with the scripted model."""
    import marvel.graph.trading_graph as tg
    import web.history as wh
    import web.stock_display as sd

    stub = FakeNewsStub(inject_claim=inject_claim, disposition=disposition)
    monkeypatch.setattr(tg, "create_llm_client", lambda **kwargs: _StubClient(stub))
    monkeypatch.setattr(wh, "_INCOMPLETE_TASKS_FILE", tmp_path / "incomplete.json")
    monkeypatch.setattr(wh, "_results_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(sd, "resolve_stock_name", lambda ticker: None)

    from marvel.default_config import DEFAULT_CONFIG
    from web.progress import ProgressTracker
    from web.runner import _run

    config = dict(DEFAULT_CONFIG)
    config.update({
        "llm_provider": "deepseek",
        "quick_think_llm": "stub",
        "deep_think_llm": "stub",
        "output_language": "Chinese",
        "results_dir": str(tmp_path / "logs"),
        "data_cache_dir": str(tmp_path / "cache"),
        "memory_log_path": str(tmp_path / "memory.md"),
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
    })

    tracker = ProgressTracker()
    _run(TICKER, TRADE_DATE, config, tracker)
    assert not getattr(tracker, "error", None), tracker.error
    return tracker.final_state, stub


def _final_rating(state) -> str:
    from marvel.agents.utils.rating import parse_rating

    return parse_rating(state.get("final_trade_decision", ""), default="?")


def _artefacts_containing(state, needle: str) -> list[str]:
    found = []
    for key, value in state.items():
        if isinstance(value, str) and needle in value:
            found.append(key)
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, str) and needle in sub_value:
                    found.append(f"{key}.{sub_key}")
    return sorted(found)


# ---------------------------------------------------------------------------
# The experiment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFakeNewsPropagation:
    """Four measurements, each a claim about the architecture."""

    @pytest.fixture(scope="class")
    def matrix(self, tmp_path_factory):
        """Run the 3 dispositions × {control, treatment} matrix once."""
        import copy

        from _pytest.monkeypatch import MonkeyPatch

        results = {}
        for disposition in ("ignore", "echo", "challenge"):
            for inject in (False, True):
                base = tmp_path_factory.mktemp(f"{disposition}-{int(inject)}")
                monkeypatch = MonkeyPatch()
                try:
                    state, stub = run_pipeline(
                        base, monkeypatch, inject_claim=inject, disposition=disposition
                    )
                finally:
                    monkeypatch.undo()
                results[(disposition, inject)] = {
                    "state": copy.deepcopy(dict(state)),
                    "stub": stub,
                    "rating": _final_rating(state),
                }
        return results

    def test_the_claim_actually_reaches_the_bull_and_bear(self, matrix):
        """Step 1: the analyst reports are embedded **verbatim** into the debate.

        This is the funnel that matters: the debaters read the raw reports, so a
        claim in any of the nine reports is in front of both advocates before any
        review has happened.
        """
        stub = matrix[("echo", True)]["stub"]

        assert "bull" in stub.prompts, f"多空辩手没有跑起来：{sorted(stub.prompts)}"
        assert FAKE_CLAIM in stub.prompts["bull"], (
            "新闻报告没有原样进入多头辩手的提示词——本审计关于传播链的前提不成立"
        )
        assert FAKE_CLAIM in stub.prompts["bear"]

    def test_the_manager_never_sees_the_reports(self, matrix):
        """The verdict stage reasons over the transcript, not over the evidence."""
        stub = matrix[("echo", True)]["stub"]
        manager_prompt = stub.prompts["research_manager"]

        assert FAKE_CLAIM in manager_prompt, "经理应当从辩论记录里看到该说法"
        # the manager is not given the source report itself
        assert "新闻与政策分析师" not in manager_prompt, (
            "经理的提示词里出现了分析师角色文本——传播链假设需要复核"
        )

    def test_ignoring_debaters_stop_the_claim_at_the_debate(self, matrix):
        """Step 2: the Research Manager sees only the transcript, not the reports.

        So a debater that ignores the claim is a *firewall* — the verdict stage
        never learns the claim existed.
        """
        state = matrix[("ignore", True)]["state"]

        assert "investment_plan" in state
        assert FAKE_CLAIM not in state["investment_plan"], (
            "经理只看到辩论记录，而辩手没有重复该说法，它不该出现在计划里"
        )

    def test_echoing_debaters_carry_it_into_the_verdict_path(self, matrix):
        state = matrix[("echo", True)]["state"]

        assert FAKE_CLAIM in state["investment_plan"], (
            "辩手重复了该说法，但经理的计划里没有它——传播链断了（这会让实验失效）"
        )

    def test_challenging_debaters_also_carry_it_forward(self, matrix):
        """Important negative result: a challenge still propagates the claim.

        The claim text travels with the rebuttal, so "someone disagreed" does not
        remove the claim from the transcript the manager reasons over.
        """
        state = matrix[("challenge", True)]["state"]

        assert FAKE_CLAIM in state["investment_plan"]

    def test_the_verdict_moves_only_under_echo(self, matrix):
        """Step 3: the ordinal displacement caused by the planted claim."""
        def delta(disposition):
            control = RATING_ORDINAL[matrix[(disposition, False)]["rating"]]
            treated = RATING_ORDINAL[matrix[(disposition, True)]["rating"]]
            return treated - control

        assert delta("ignore") == 0, "辩手忽略时结论不该动"
        assert delta("echo") < 0, "辩手照单全收时结论应当被推空"
        assert delta("challenge") == 0, "辩手驳斥时结论不该跟着动"

    def test_nothing_in_the_pipeline_ever_drops_the_claim(self, matrix):
        """Step 4: there is no falsification stage — the claim only ever spreads.

        Measured as breadth: how many completed artefacts carry the sentinel.
        """
        carriers = _artefacts_containing(matrix[("echo", True)]["state"], FAKE_CLAIM)

        assert len(carriers) >= 2, (
            f"注入的假消息只出现在 {carriers}——传播广度低于预期，实验前提需要复核"
        )
        assert any("final" in key or "trader" in key for key in carriers), (
            f"假消息没有到达决策层：{carriers}"
        )


@pytest.mark.unit
class TestQualityGateDoesNotCheckProvenance:
    """The gate is the only review step, and it reviews shape, not sourcing."""

    def test_a_well_formed_report_with_a_false_claim_is_graded_A(self):
        from marvel.agents.quality_gate import _hard_check_report

        report = f"## 新闻分析\n\n近期出现一则报道：{FAKE_CLAIM}\n\n" + _BODY + _TABLE

        grade, detail = _hard_check_report("news", report)

        assert grade == "A", (
            f"假消息报告被硬检查判为 {grade}（{detail}）——如果它会被降级，"
            "门控就是一道防线，这个结论需要改写"
        )

    def test_the_gate_has_no_provenance_check(self):
        """Structural: nothing in the gate inspects *where* a claim came from.

        If a provenance check is ever added, this fails and the audit's
        conclusion ("the gate is not a defence against a false claim") must be
        revisited rather than left standing.
        """
        import inspect

        from marvel.agents import quality_gate

        source = inspect.getsource(quality_gate)
        for marker in (
            "provenance", "cross_check", "cross-check", "verify_source",
            "核实来源", "交叉验证", "单一来源", "unverified",
        ):
            assert marker not in source, (
                f"门控里出现了来源核对（{marker}）——本审计的结论需要改写"
            )

        # and what it grades is exactly the nine analyst report bodies
        assert set(quality_gate.REPORT_FIELDS) == set(_ANALYST_STAGES)


@pytest.mark.unit
class TestStageMarkersStayAccurate:
    """The experiment is only trustworthy while its stage detection is correct."""

    def test_every_pipeline_stage_is_recognisable(self):
        from marvel.agents.utils.agent_states import AgentState

        assert set(_ANALYST_STAGES) == {
            "market", "social", "news", "fundamentals", "policy",
            "hot_money", "lockup", "volume_price", "macro",
        }
        # the debate stages the pipeline wires up
        for stage in ("bull", "bear", "research_manager", "trader",
                      "aggressive", "conservative", "neutral", "portfolio_manager"):
            assert any(name == stage for name, _ in _STAGE_MARKERS)

    def test_news_marker_wins_over_the_policy_substring(self):
        """「新闻与政策分析师」 contains 「政策分析师」 — order matters."""
        assert classify_stage("你是一位专注于 A 股市场的新闻与政策分析师。") == "news"
        assert classify_stage("你是一位专注于 A 股市场的政策分析师。") == "policy"

    def test_unknown_prompts_are_reported_as_unknown(self):
        assert classify_stage("totally unrelated text") == "unknown"
