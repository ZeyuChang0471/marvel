"""Provenance helpers: keep the "unverified" label alive across every hand-off.

Companion to ``DEBATE_POLARIZATION_AUDIT.md``. The news tool marks every item it
returns as either 「多源印证」 (several independent feeds carried the story) or
「单一来源，未经交叉验证」 (single-source, unverified).

Nothing in this module judges whether a claim is *true* — no code can do that, and
pretending otherwise would just add a second source of hallucination. What these
helpers do is mechanical: make sure the uncertainty label survives each hand-off.

* :func:`provenance_census` counts the labels inside one report (quality gate);
* :func:`build_evidence_digest` lists the unverified sentences so the Research
  Manager is not blind to what the debaters may be amplifying;
* :func:`append_unverified_appendix` keeps unverified claims out of the decision
  prose and inside a clearly labelled appendix;
* :func:`summarise_for_memory` keeps the appendix out of the "past lessons" text,
  so an unverified rumour cannot be re-injected into later runs as a lesson.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

#: Label printed by ``get_news`` for a story only one feed carried.
UNVERIFIED_LABEL = "单一来源，未经交叉验证"

#: Label printed by ``get_news`` for a story several feeds carried.
CORROBORATED_LABEL = "多源印证"

#: Header of the appendix that keeps unverified claims out of the decision prose.
#: Deliberately emoji-free: the PDF font this project ships drops ⚠️ silently
#: (visible in ``web_ui.log``), and this header is printed to the user.
UNVERIFIED_HEADER = "## 未经证实的信息（仅供参考，不作为决策依据）"

_APPENDIX_RE = re.compile(r"\n*#{1,6}\s*未经证实的信息.*\Z", re.DOTALL)

#: The instruction the debaters carry. Kept here (one copy) so the A/B experiment
#: can turn it off in exactly one place.
EVIDENCE_DISCIPLINE = (
    "Evidence discipline: news items arrive tagged 「多源印证」(carried by several "
    "independent feeds) or 「单一来源，未经交叉验证」(single-source, unverified). When you "
    "cite an unverified item, keep its label and treat it as a claim, not a fact — never "
    "restate it as confirmed, and never let a single unverified rumour be the sole basis "
    "for changing the investment conclusion."
)


def provenance_discipline_enabled() -> bool:
    """实验开关：``MARVEL_ABLATE_PROVENANCE_DISCIPLINE=1`` 时关闭下游纪律。

    This is the *baseline arm* of the A/B experiment (``scripts/compare_polarization.py``).
    It changes prompts only — the news tool still marks every item, so the claim is
    available in both arms and the only difference is whether the agents were told
    to keep the label. Default (unset) = discipline on.
    """
    import os

    return os.environ.get("MARVEL_ABLATE_PROVENANCE_DISCIPLINE", "").strip().lower() not in (
        "1", "true", "yes", "on",
    )

#: 新闻报告在「工具确实没返回消息」时必须显式说明，否则门控会把「没标注」
#: 当成「标注在传抄中丢了」。这两个标记是仓库既有的约定。
NO_NEWS_HINTS = ("No news found", "[数据缺失")


def provenance_census(text: str) -> dict:
    """Count provenance labels in ``text``. Pure string counting — no judgement."""
    body = str(text or "")
    corroborated = body.count(CORROBORATED_LABEL)
    unverified = body.count(UNVERIFIED_LABEL)
    return {
        "corroborated": corroborated,
        "unverified": unverified,
        "total": corroborated + unverified,
    }


def extract_unverified_lines(
    text: str, *, limit: int = 8, max_chars: int = 200
) -> list:
    """Lines carrying the unverified label, cleaned, deduplicated and capped."""
    seen: set = set()
    out: list = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if UNVERIFIED_LABEL not in line:
            continue
        line = re.sub(r"^[#>\-\*\s\d\.、]+", "", line).strip()
        if len(line) > max_chars:
            line = line[:max_chars].rstrip() + "…"
        if not line or line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def build_evidence_digest(
    news_report: str, *, limit: int = 8, max_chars: int = 200
) -> str:
    """Machine-generated provenance digest for the debate judge (Research Manager).

    The audit's second finding was that the manager only ever sees the debate text,
    so it cannot tell an amplified claim from a verified one. This digest is the
    cheapest fix that does not change the graph topology: it lists what the news
    layer labelled, and asks the manager to declare any reliance on it.

    Returns '' when there is no news report at all (news analyst not selected).
    """
    if not provenance_discipline_enabled():
        return ""
    report = str(news_report or "")
    if not report.strip():
        return ""

    census = provenance_census(report)
    parts = ["**来源印证摘要（机器生成，不是模型判断）**"]

    if census["total"] == 0:
        parts.append(
            "- 新闻报告里**没有任何**来源印证标注，而消息的来源状态本应随每条内容"
            "一起传下来。不得把该报告里的任何说法当作已确认事实。"
        )
        return "\n".join(parts)

    parts.append(
        f"- 标注统计：多源印证 {census['corroborated']} 处；"
        f"单一来源（未经交叉验证）{census['unverified']} 处。"
    )
    lines = extract_unverified_lines(report, limit=limit, max_chars=max_chars)
    if lines:
        parts.append("- 未经交叉验证的说法（原文摘录）：")
        parts.extend(f"  {i}. {line}" for i, line in enumerate(lines, 1))
    parts.append(
        "- 纪律：不得把上述说法改写成事实；若你的建议依赖其中任何一条，"
        "必须在 `unverified_reliance` 字段里逐条列出。"
    )
    return "\n".join(parts)


def split_appendix(text: str) -> tuple:
    """Return ``(body, appendix)``; ``appendix`` is empty when there is none."""
    value = str(text or "")
    match = _APPENDIX_RE.search(value)
    if not match:
        return value, ""
    return value[: match.start()].rstrip(), value[match.start():].strip()


def append_unverified_appendix(
    decision_text: str, claims: Iterable, *, max_chars: int = 300, limit: int = 12
) -> str:
    """Move unverified claims into a labelled appendix — never delete them.

    Deleting a sentence is worse than keeping it: dropping 「据传」 turns the
    remainder into a statement the model never made. So the claims stay readable,
    they just stop being part of the decision prose.
    """
    body, existing = split_appendix(decision_text)

    items: list = []
    seen: set = set()

    def _add(value: str) -> None:
        text = " ".join(str(value or "").split())
        if not text:
            return
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        if text in seen:
            return
        seen.add(text)
        items.append(text)

    for claim in list(claims or []):
        _add(claim)
    for line in extract_unverified_lines(body, limit=limit, max_chars=max_chars):
        _add(line)
    for line in extract_unverified_lines(existing, limit=limit, max_chars=max_chars):
        _add(line)

    if not items:
        return body if not existing else body + "\n\n" + existing

    lines = [
        UNVERIFIED_HEADER,
        "",
        "> 下列说法的来源为单一来源、未经交叉验证。它们**不作为决策依据**，"
        "下游也不得把它们当作事实引用。",
        "",
    ]
    lines.extend(f"- {item}" for item in items)
    return body.rstrip() + "\n\n" + "\n".join(lines) + "\n"


def summarise_for_memory(text: str) -> str:
    """Strip the unverified appendix before a decision becomes a "past lesson".

    The memory log re-injects past decisions into later runs. An unverified rumour
    that rode along in an appendix would otherwise be replayed forever as if it
    were an established fact about the company.
    """
    body, appendix = split_appendix(text)
    if not appendix:
        return str(text or "")
    count = len(extract_unverified_lines(appendix, limit=50, max_chars=200))
    note = (
        f"（本条记忆另有 {count} 条未经证实的说法，已剔除；"
        "它们不作为后续运行的教训。）"
    )
    return (body.rstrip() + "\n\n" + note).strip()
