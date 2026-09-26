from typing import Annotated

REPORT_FIELDS = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
    "policy": "policy_report",
    "hot_money": "hot_money_report",
    "lockup": "lockup_report",
    "volume_price": "volume_price_report",
    "macro": "macro_report",
}

ANALYST_NAMES = {
    "market": "技术分析师",
    "social": "情绪分析师",
    "news": "新闻分析师",
    "fundamentals": "基本面分析师",
    "policy": "政策分析师",
    "hot_money": "游资追踪师",
    "lockup": "解禁监控师",
    "volume_price": "量价分析师",
    "macro": "宏观板块分析师",
}

MIN_REPORT_LENGTH = 200

FAILURE_MARKERS = [
    "无法获取",
    "I cannot retrieve",
    "I don't have access",
    "unable to fetch",
    "工具调用失败",
]


def _hard_check_report(analyst_type: str, report: str) -> tuple:
    """Run hard checks on a single report. Returns (grade, detail)."""
    if not report or not report.strip():
        return ("F", "报告为空")

    length = len(report.strip())
    if length < MIN_REPORT_LENGTH:
        return ("D", f"报告过短 ({length} chars < {MIN_REPORT_LENGTH})")

    failure_count = sum(1 for m in FAILURE_MARKERS if m in report)
    stripped = report
    for m in FAILURE_MARKERS:
        stripped = stripped.replace(m, "")
    if failure_count > 0 and len(stripped.strip()) < MIN_REPORT_LENGTH:
        return ("D", f"报告主要由失败信息构成 ({failure_count} 处)")

    has_table = "|" in report and "---" in report
    missing_count = report.count("[数据缺失")

    issues = []
    if not has_table:
        issues.append("缺少汇总表格")
    if missing_count > 0:
        issues.append(f"{missing_count} 处数据缺失")

    if missing_count >= 3:
        return ("C", "；".join(issues))
    if not has_table or missing_count > 0:
        return ("B", "；".join(issues) if issues else "基本合格")

    return ("A", f"完整 ({length} chars)")


def _graded_fields(selected=None) -> list[str]:
    """Analyst keys the gate should grade, in registry order.

    ``None`` means "grade everything" (the behaviour when the caller did not say
    which analysts it built the graph with).
    """
    keys = list(REPORT_FIELDS)
    if selected is None:
        return keys
    return [key for key in keys if key in selected]


def _build_review_prompt(
    reports: dict, trade_date: str, ticker: str, selected=None
) -> str:
    """Build the LLM review prompt.

    The analyst count and the mandated output table are derived from
    ``REPORT_FIELDS`` / ``ANALYST_NAMES`` rather than written out by hand. Both
    used to be hardcoded to seven while the loop graded nine, so the two newest
    analysts (量价分析师, 宏观板块分析师) were never reviewed: a missing or
    fabricated report could not be flagged, and every downstream debater is told
    to lower its reliance on any report graded C/D/F.

    ``selected`` restricts the prompt to the analysts this run actually built, so
    the review is not asked to grade reports that were never requested.
    """
    graded = _graded_fields(selected)

    report_sections = []
    for analyst_type in graded:
        field = REPORT_FIELDS[analyst_type]
        name = ANALYST_NAMES[analyst_type]
        content = reports.get(field, "（未运行）")
        if not content:
            content = "（报告为空）"
        if len(content) > 3000:
            content = content[:3000] + "\n... (truncated for review)"
        report_sections.append(f"### {name} ({analyst_type})\n{content}")

    all_reports = "\n\n".join(report_sections)

    # 表格行同样从 ANALYST_NAMES 生成，避免漏掉后加的角色。
    table_rows = []
    for index, analyst_type in enumerate(graded):
        name = ANALYST_NAMES[analyst_type]
        if index == 0:
            table_rows.append(
                f"| {name} | A/B/C/D/F | 是否匹配交易日 | 列出缺失的必采项 | 简要说明 |"
            )
        else:
            table_rows.append(f"| {name} | ... | ... | ... | ... |")
    review_table = "\n".join(table_rows)

    return f"""你是数据质量审核员。以下是 {len(graded)} 位分析师对 {ticker} 在 {trade_date} 的研究报告。请逐一审核。

{all_reports}

---

请按以下格式输出审核结果（不要输出其他内容）：

## 数据质量审核报告

**标的**: {ticker} | **日期**: {trade_date}

| 分析师 | 评级 | 数据时效 | 缺失项 | 备注 |
|--------|------|----------|--------|------|
{review_table}

**整体评级**: A/B/C/D/F
**数据可信度**: 高/中/低
**建议**: （如有数据缺失，提醒辩论阶段谨慎使用该报告）

评级标准：
- A: 必采清单全部覆盖，数据时效匹配，有汇总表格
- B: 缺少 1-2 项非关键数据，整体可用
- C: 缺少 3+ 项或有数据时效问题，需谨慎使用
- D: 大量缺失或主要为失败信息，可信度低
- F: 报告为空或完全无效
"""


def create_quality_gate(llm, selected_analysts=None):
    """Factory for the data quality gate node.

    Sits between the last analyst Msg Clear and Bull Researcher.
    Layer 1: hard checks (code). Layer 2: LLM review (one call).
    Writes data_quality_summary to state for downstream consumers.

    ``selected_analysts`` is the set the graph was built with. Without it the
    gate cannot tell "this analyst was not selected" from "this analyst ran and
    produced nothing": an unselected analyst has an empty report, which the hard
    check grades **F**. Four or more unselected analysts then tripped the
    `fail_count >= 4` branch and skipped the LLM review entirely, so a user who
    deliberately ran a subset got a gate report asserting that several analysts
    had produced nothing — and downstream debaters were told to discount it.
    """
    selected = (
        {str(a).strip().lower() for a in selected_analysts}
        if selected_analysts is not None
        else None
    )

    def quality_gate_node(state) -> dict:
        trade_date = state["trade_date"]
        ticker = state["company_of_interest"]

        reports = {}
        for analyst_type, field in REPORT_FIELDS.items():
            reports[field] = state.get(field, "")

        hard_results = {}
        for analyst_type, field in REPORT_FIELDS.items():
            if selected is not None and analyst_type not in selected:
                hard_results[analyst_type] = ("—", "未运行（本次分析未选择）")
                continue
            grade, detail = _hard_check_report(analyst_type, reports[field])
            hard_results[analyst_type] = (grade, detail)

        hard_summary_lines = []
        for analyst_type, (grade, detail) in hard_results.items():
            name = ANALYST_NAMES[analyst_type]
            hard_summary_lines.append(f"- {name}: [{grade}] {detail}")
        hard_summary = "\n".join(hard_summary_lines)

        # Only graded analysts count towards "too many failures"; "—" means the
        # analyst never ran and is not evidence of a data problem.
        fail_count = sum(
            1 for _, (g, _) in hard_results.items() if g in ("F", "D")
        )

        llm_review = ""
        if fail_count < 4:
            try:
                review_prompt = _build_review_prompt(
                    reports, trade_date, ticker, selected=selected
                )
                response = llm.invoke(review_prompt)
                llm_review = response.content
            except Exception as e:
                llm_review = f"（LLM 复审失败: {type(e).__name__}: {e}）"

        skipped = sum(1 for _, (g, _) in hard_results.items() if g == "—")
        skipped_note = (
            f"\n> 本次分析只选了 {len(hard_results) - skipped} 位分析师，"
            f"另有 {skipped} 位未运行（不计入失败）。\n"
            if skipped
            else ""
        )

        summary = (
            f"## 数据质量门控结果\n\n"
            f"**标的**: {ticker} | **交易日**: {trade_date}\n"
            f"{skipped_note}\n"
            f"### 硬检查结果\n{hard_summary}\n\n"
            f"### LLM 复审\n"
            f"{llm_review if llm_review else '（跳过 — 多数**已运行**的报告未通过硬检查）'}\n"
        )

        return {"data_quality_summary": summary}

    return quality_gate_node
