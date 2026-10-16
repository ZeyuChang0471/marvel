"""Guards for the provenance helpers (see marvel/agents/utils/evidence.py).

These helpers are the mechanical half of the de-polarization work: they never
judge whether a claim is true, they only keep the "unverified" label attached to
it as it travels through the gate, the debate and the final report. The audit's
own wording is the contract — 「驳斥」不等于「剔除」 — so every hand-off is pinned
here rather than left to the prompt.
"""

from __future__ import annotations

import pytest

from marvel.agents.utils.evidence import (
    CORROBORATED_LABEL,
    UNVERIFIED_HEADER,
    UNVERIFIED_LABEL,
    append_unverified_appendix,
    build_evidence_digest,
    extract_unverified_lines,
    provenance_census,
    split_appendix,
    summarise_for_memory,
)


@pytest.mark.unit
class TestCensus:
    def test_counts_both_labels(self):
        text = f"a {CORROBORATED_LABEL} b {UNVERIFIED_LABEL} c {UNVERIFIED_LABEL}"
        census = provenance_census(text)

        assert census == {"corroborated": 1, "unverified": 2, "total": 3}

    def test_empty_text_is_zero(self):
        assert provenance_census("") == {"corroborated": 0, "unverified": 0, "total": 0}

    def test_extraction_cleans_markdown_noise_and_dedupes(self):
        text = (
            f"### [公告] 亨通光电:关于回购的公告 (source: 东方财富公告; {UNVERIFIED_LABEL})\n"
            f"- 同一条 (source: 新闻; {UNVERIFIED_LABEL})\n"
            f"1. 同一条 (source: 新闻; {UNVERIFIED_LABEL})\n"
        )
        lines = extract_unverified_lines(text)

        assert len(lines) == 2, lines
        assert lines[0].startswith("[公告] 亨通光电"), "Markdown 前缀没有被清掉"
        assert lines[1].startswith("同一条")

    def test_extraction_caps_length_and_count(self):
        text = "\n".join(
            f"第{i}条 {UNVERIFIED_LABEL} " + "长" * 500 for i in range(20)
        )
        lines = extract_unverified_lines(text, limit=3, max_chars=50)

        assert len(lines) == 3
        assert all(len(line) <= 51 for line in lines)  # 51 = 50 + ellipsis


@pytest.mark.unit
class TestEvidenceDigest:
    def test_it_lists_the_unverified_sentences(self):
        news = (
            "## 新闻分析\n"
            f"### [新闻] 某独家爆料 (source: 某媒体; {UNVERIFIED_LABEL})\n"
            f"### [公告] 分红公告 (source: 东方财富公告; {CORROBORATED_LABEL}：公告、新闻)\n"
        )

        digest = build_evidence_digest(news)

        assert "来源印证摘要" in digest
        assert "多源印证 1 处" in digest
        assert "单一来源（未经交叉验证）1 处" in digest
        assert "某独家爆料" in digest
        assert "unverified_reliance" in digest

    def test_a_report_without_labels_is_itself_the_finding(self):
        digest = build_evidence_digest("## 新闻分析\n消息很多，但没有一条带标注。")

        assert "没有任何" in digest
        assert "不得把该报告里的任何说法当作已确认事实" in digest

    def test_no_news_report_no_digest(self):
        assert build_evidence_digest("") == ""
        assert build_evidence_digest(None) == ""


@pytest.mark.unit
class TestUnverifiedAppendix:
    def test_claims_move_into_the_appendix(self):
        rendered = append_unverified_appendix(
            "**Rating**: Hold\n\n**Investment Thesis**: 论点。",
            ["某独家爆料：公司被立案调查（单一来源，未经交叉验证）"],
        )

        body, appendix = split_appendix(rendered)

        assert UNVERIFIED_HEADER in rendered
        assert "某独家爆料" in appendix
        assert "某独家爆料" not in body, "未证实的说法仍留在决策正文里"
        assert "**Rating**: Hold" in body

    def test_nothing_to_move_leaves_the_text_alone(self):
        text = "**Rating**: Hold\n\n**Investment Thesis**: 论点。"

        assert append_unverified_appendix(text, []) == text
        assert append_unverified_appendix(text, ["  ", ""]) == text

    def test_inline_labelled_sentences_are_also_listed(self):
        text = (
            "**Rating**: Hold\n\n"
            f"**Investment Thesis**: 该说法（{UNVERIFIED_LABEL}）尚待确认。"
        )

        rendered = append_unverified_appendix(text, [])
        body, appendix = split_appendix(rendered)

        assert UNVERIFIED_HEADER in rendered
        assert UNVERIFIED_LABEL in appendix
        assert "尚待确认" in appendix

    def test_duplicates_are_not_listed_twice(self):
        claim = "某独家爆料：公司被立案调查（单一来源，未经交叉验证）"
        rendered = append_unverified_appendix(f"正文\n\n{claim}", [claim])
        _, appendix = split_appendix(rendered)

        assert appendix.count("某独家爆料") == 1

    def test_memory_drops_the_appendix_content(self):
        decision = (
            "**Rating**: Hold\n\n**Investment Thesis**: 论点。\n\n"
            + append_unverified_appendix("", ["某独家爆料：公司被立案调查（单一来源，未经交叉验证）"])
        )

        memory_text = summarise_for_memory(decision)

        assert "**Rating**: Hold" in memory_text
        assert "某独家爆料" not in memory_text, "未证实的说法会作为「过往教训」被再次注入"
        assert "已剔除" in memory_text

    def test_memory_without_appendix_is_untouched(self):
        text = "**Rating**: Hold\n\n**Investment Thesis**: 论点。"
        assert summarise_for_memory(text) == text
