"""Guards for the data-quality gate.

The gate used to grade **every** analyst in its registry, whether or not the run
had been asked to build that analyst. An analyst that never ran has an empty
report, and the hard check grades an empty report **F**. Consequences:

* a user who deliberately ran a subset got a gate report asserting that several
  analysts "produced nothing", which is not a data problem at all;
* four or more unselected analysts pushed ``fail_count`` to 4, which **skips the
  LLM review entirely** — so the more analysts you left out, the less reviewing
  happened;
* every downstream debater is instructed to lower its reliance on reports graded
  C/D/F, so the fabricated F's propagated into the decision.
"""

from __future__ import annotations

import pytest

from marvel.agents.quality_gate import (
    ANALYST_NAMES,
    REPORT_FIELDS,
    _build_review_prompt,
    create_quality_gate,
)


def _state(reports: dict | None = None) -> dict:
    state = {"trade_date": "2026-05-12", "company_of_interest": "600519"}
    state.update(reports or {})
    return state


def _good_report(name: str) -> str:
    """A report long enough, with a table, to pass the hard checks."""
    return (
        f"## {name}\n\n"
        + "正文。" * 120
        + "\n\n| 项目 | 值 |\n|------|----|\n| PE | 21 |\n"
    )


class _StubLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)

        class _Resp:
            content = "## 数据质量审核报告\n\n整体评级: A"

        return _Resp()


@pytest.mark.unit
class TestUnselectedAnalystsAreNotGraded:
    def test_unselected_analysts_are_marked_not_run(self):
        llm = _StubLLM()
        node = create_quality_gate(llm, selected_analysts=["market", "news"])

        summary = node(_state({
            "market_report": _good_report("market"),
            "news_report": _good_report("news"),
        }))["data_quality_summary"]

        assert "未运行（本次分析未选择）" in summary
        # 没有被判成失败
        assert "[F] 报告为空" not in summary
        for key, name in ANALYST_NAMES.items():
            if key in ("market", "news"):
                continue
            assert f"- {name}: [—]" in summary, f"{name} 不该被评分"

    def test_skipping_the_llm_review_requires_real_failures(self):
        """只有 market + news 选中时，未运行的 7 位不得触发「跳过 LLM 复审」。"""
        llm = _StubLLM()
        node = create_quality_gate(llm, selected_analysts=["market", "news"])

        node(_state({
            "market_report": _good_report("market"),
            "news_report": _good_report("news"),
        }))

        assert llm.prompts, "全部报告正常，却跳过了 LLM 复审"
        assert "跳过" not in llm.prompts[0]

    def test_really_empty_selected_reports_still_skip_the_review(self):
        """真正的失败仍要能触发跳过——修复不能把门控变成摆设。"""
        llm = _StubLLM()
        # 选 5 个，全部空报告 → 5 个 F
        selected = ["market", "news", "fundamentals", "policy", "lockup"]
        node = create_quality_gate(llm, selected_analysts=selected)

        summary = node(_state())["data_quality_summary"]

        assert not llm.prompts, "5 份空报告应当跳过 LLM 复审"
        assert "跳过" in summary

    def test_without_a_selection_it_grades_everything(self):
        """不传选择时保持旧行为（grade 全部），避免悄悄改变语义。"""
        llm = _StubLLM()
        node = create_quality_gate(llm)

        summary = node(_state())["data_quality_summary"]

        for name in ANALYST_NAMES.values():
            assert f"- {name}: [F]" in summary

    def test_selection_is_case_insensitive_and_trimmed(self):
        llm = _StubLLM()
        node = create_quality_gate(llm, selected_analysts=["MARKET", " News "])

        summary = node(_state({
            "market_report": _good_report("market"),
            "news_report": _good_report("news"),
        }))["data_quality_summary"]

        assert "未运行（本次分析未选择）" in summary
        # 大小写/空白归一化后，" News " 仍然是被选中的那个，不该标成未运行
        assert f"- {ANALYST_NAMES['news']}: [—]" not in summary
        assert f"- {ANALYST_NAMES['market']}: [—]" not in summary
        assert f"- {ANALYST_NAMES['policy']}: [—]" in summary


@pytest.mark.unit
class TestReviewPromptMatchesTheSelection:
    def test_prompt_covers_exactly_the_selected_analysts(self):
        prompt = _build_review_prompt(
            {}, "2026-05-12", "600519", selected={"market", "news"}
        )

        assert "以下是 2 位分析师" in prompt
        assert ANALYST_NAMES["market"] in prompt
        assert ANALYST_NAMES["news"] in prompt
        assert ANALYST_NAMES["policy"] not in prompt

    def test_prompt_table_rows_match_the_selection(self):
        prompt = _build_review_prompt(
            {}, "2026-05-12", "600519", selected={"market", "news", "macro"}
        )
        rows = [
            line for line in prompt.splitlines()
            if line.startswith("| ")
            and not line.startswith("| 分析师 |")
            and "---" not in line
        ]
        assert len(rows) == 3

    def test_prompt_without_a_selection_covers_every_analyst(self):
        prompt = _build_review_prompt({}, "2026-05-12", "600519")
        assert f"以下是 {len(REPORT_FIELDS)} 位分析师" in prompt
        for name in ANALYST_NAMES.values():
            assert name in prompt
