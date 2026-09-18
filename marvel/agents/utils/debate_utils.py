"""Debate helpers.

Adapted from KylinMountain/TradingAgents-AShare, which is licensed under the
PolyForm Noncommercial License 1.0.0 — see LICENSE-TradingAgents-AShare.txt.

Scope note: upstream's ``debate_utils`` is not a generic utility module — most
of it (``update_debate_state_with_payload``, ``format_claims_for_prompt``,
``extract_risk_judge_result``, …) implements that project's *claim-registry*
debate protocol, in which researchers register machine-readable claims, the
risk judge returns a tagged approve/revise verdict, and conditional logic
routes on claim state. MARVEL's debate layer is prose-based and comes from the
TradingAgents-Astock lineage, so adopting those functions would mean replacing
the debate architecture rather than extracting code from it.

``default_round_goal`` is the genuinely framework-independent part: it gives
each debate round an explicit objective, which stops later rounds from simply
restating round one. Only that is carried over, and it is used.
"""

from __future__ import annotations

# Upstream frames these as "claims"; MARVEL's debate is prose, so the wording
# here says 论点 instead. The objectives themselves are unchanged.
_ROUND_GOALS: dict[str, list[str]] = {
    "investment": [
        "建立最核心的正反两方论点，并明确为何是现在。",
        "优先攻击对手最脆弱的假设，不要扩散议题。",
        "围绕时间窗口与触发条件，判断交易时机是否成立。",
        "围绕失败路径与失效条件，判断谁低估了回撤风险。",
        "检查剩余分歧是否仍有信息增量，否则准备收口。",
    ],
    "risk": [
        "建立最关键的执行风险论点，明确风险预算冲突点。",
        "围绕仓位、止损、流动性约束，攻击对手最薄弱一环。",
        "判断哪些风险是可接受波动，哪些风险是硬性红线。",
        "逼迫双方给出可执行替代方案，而不是抽象立场。",
        "检查是否还存在未解决的高影响执行风险，否则准备收口。",
    ],
}


def default_round_goal(domain: str, next_count: int) -> str:
    """Return the objective for the upcoming debate round.

    Args:
        domain: ``"investment"`` for the Bull/Bear debate, ``"risk"`` for the
            three-way risk debate. Unknown values fall back to ``investment``.
        next_count: the 1-based number of the round about to be spoken.
    """
    goal_list = _ROUND_GOALS.get(domain, _ROUND_GOALS["investment"])
    index = min(max(next_count - 1, 0), len(goal_list) - 1)
    return goal_list[index]
