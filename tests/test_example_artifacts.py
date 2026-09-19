"""Guards for the committed example outputs.

``LICENSING.md`` stakes the project's legal position on executable price levels
(建仓价 / 止损位 / 仓位 / 目标价) having been removed from the product — the
schemas carry no fields for them on purpose. Committed example reports are part
of what the project publishes, so they must not reintroduce what the schemas
removed. Two example cases (002594, 300750) did exactly that, and were deleted.

These tests keep the contradiction from coming back, and they check the
redaction helper that ``examples/run_cases.py`` applies before writing.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "examples" / "cases"

# Kept as an independent copy of the word list on purpose: if someone narrows
# the implementation's markers, these tests must still catch the term.
_LEVEL_MARKERS = (
    "止损", "止盈", "目标价", "建仓", "加仓", "减仓", "仓位",
    "买入价", "卖出价", "挂单", "轻仓", "重仓", "满仓", "抄底",
    "支撑位", "压力位", "回踩",
    "stop-loss", "stop loss", "price target", "target price",
    "position size", "entry price", "take profit",
)


def _load_run_cases():
    spec = importlib.util.spec_from_file_location(
        "marvel_examples_run_cases", REPO_ROOT / "examples" / "run_cases.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestRedaction:
    def test_removes_lines_that_carry_levels(self):
        rc = _load_run_cases()
        text = "估值合理。\n建议建仓，止损设在 420 元。\n风险可控。"
        out, n = rc.redact_executable_levels(text)

        assert n == 1
        assert "420" not in out, "带价位的原文必须整行移除"
        assert "建议建仓" not in out
        assert rc.REDACTION_MARKER in out, "要留下可见的脱敏标记，不能静默删除"
        assert "估值合理。" in out
        assert "风险可控。" in out

    def test_keeps_clean_prose_untouched(self):
        rc = _load_run_cases()
        text = "**Rating**: Hold\n\n盈利质量稳健，估值处于历史中枢。"
        out, n = rc.redact_executable_levels(text)

        assert n == 0
        assert out == text

    def test_handles_empty_input(self):
        rc = _load_run_cases()
        assert rc.redact_executable_levels("") == ("", 0)

    def test_every_marker_is_actually_redacted(self):
        """测试自己的词表里每个词都必须被实现拦下。"""
        rc = _load_run_cases()
        for marker in _LEVEL_MARKERS:
            _, n = rc.redact_executable_levels(f"示例：{marker} 示例。")
            assert n == 1, f"{marker!r} 没有被脱敏——示例产物会重新带上可执行价位"


@pytest.mark.unit
class TestCommittedExamples:
    def test_no_committed_case_artifact_contains_price_levels(self):
        assert CASES_DIR.is_dir(), f"示例目录不存在: {CASES_DIR}"
        marker = _load_run_cases().REDACTION_MARKER

        offenders = []
        for path in sorted(CASES_DIR.iterdir()):
            if path.suffix not in {".md", ".json"}:
                continue
            raw = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                try:
                    raw = json.dumps(json.loads(raw), ensure_ascii=False)
                except json.JSONDecodeError:
                    pass
            # 脱敏标记本身含有这些词，先剔除再扫——否则一个已正确脱敏的文件
            # 会被这份检查判为违规。
            raw = raw.replace(marker, "")
            hit = [m for m in _LEVEL_MARKERS if m in raw]
            if hit:
                offenders.append(f"{path.name}: {hit}")

        assert not offenders, (
            "示例产物含可执行价位，与 LICENSING.md 的声明冲突"
            "（应删除或先经 redact_executable_levels）：\n  "
            + "\n  ".join(offenders)
        )
