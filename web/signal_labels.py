"""A 股信号语义：红涨绿跌，以及印章上的两个字。

放在独立模块里，是因为 Web（Streamlit）和 PDF（fpdf）都要用；`web/theme.py`
一 import 就会拉起 Streamlit，PDF 导出不该为了两个字符串把它拖进来。
"""

from __future__ import annotations

_SEAL_LABELS = (
    ("UNDERWEIGHT", "减持"),
    ("OVERWEIGHT", "增持"),
    ("SELL", "卖出"),
    ("BUY", "买入"),
    ("HOLD", "持有"),
)


def signal_tone(signal: str) -> str:
    """A 股约定：红涨绿跌；持有用黄铜色。"""
    s = (signal or "").upper()
    if "SELL" in s or "UNDERWEIGHT" in s:
        return "down"
    if "BUY" in s or "OVERWEIGHT" in s:
        return "up"
    return "flat"


def seal_label(signal: str) -> str:
    s = (signal or "").upper()
    for marker, label in _SEAL_LABELS:
        if marker in s:
            return label
    return "持有"
