"""CLI data models and the canonical analyst registry.

The analyst maps live here rather than being duplicated per module: the CLI used
to keep **four** separate copies (this enum, ``cli/utils.ANALYST_ORDER``,
``main.MessageBuffer.ANALYST_MAPPING`` and ``main.REPORT_SECTIONS``) and all four
listed only the four upstream analysts. The graph registers nine, so:

* CLI users could not select the five A-share-specific analysts at all;
* ``cli/main.py`` filtered the selection through its own four-entry list before
  handing it to ``MarvelGraph``, so even a hand-edited selection was dropped;
* five analyst reports were never displayed.

One registry, validated against ``marvel/graph/setup.py`` by
``tests/test_docs_consistency.py``.
"""

from enum import Enum
from typing import Dict, List


class AnalystType(str, Enum):
    """Every analyst the graph can run.

    Order mirrors ``marvel/graph/setup.py``: the four upstream roles, then the
    A-share-specific ones.
    """

    MARKET = "market"
    VOLUME_PRICE = "volume_price"
    SOCIAL = "social"
    NEWS = "news"
    FUNDAMENTALS = "fundamentals"
    POLICY = "policy"
    HOT_MONEY = "hot_money"
    LOCKUP = "lockup"
    MACRO = "macro"


#: Selection order shown in the CLI menu.
ANALYST_SELECTION_ORDER: List[str] = [
    "market",
    "volume_price",
    "social",
    "news",
    "fundamentals",
    "policy",
    "hot_money",
    "lockup",
    "macro",
]

#: Human-readable name per analyst key, used by the status panel and progress.
ANALYST_DISPLAY_NAMES: Dict[str, str] = {
    "market": "Market Analyst",
    "volume_price": "Volume-Price Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
    "policy": "Policy Analyst",
    "hot_money": "Hot Money Tracker",
    "lockup": "Lockup Watcher",
    "macro": "Macro & Sector Analyst",
}

#: Graph state key holding each analyst's report.
ANALYST_REPORT_KEYS: Dict[str, str] = {
    "market": "market_report",
    "volume_price": "volume_price_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
    "policy": "policy_report",
    "hot_money": "hot_money_report",
    "lockup": "lockup_report",
    "macro": "macro_report",
}


def analyst_agent_name(analyst_key: str) -> str:
    """Display name for an analyst key.

    Falls back to the graph's own node-naming rule (``"<key> Analyst"``) so an
    analyst added to the graph without being registered here still renders
    instead of raising ``KeyError`` mid-run.
    """
    return ANALYST_DISPLAY_NAMES.get(analyst_key, f"{analyst_key.capitalize()} Analyst")
