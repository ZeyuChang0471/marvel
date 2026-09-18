"""A-share trading calendar and intraday market phase.

Adapted from KylinMountain/TradingAgents-AShare, which is licensed under the
PolyForm Noncommercial License 1.0.0 — see LICENSE-TradingAgents-AShare.txt.
The upstream version pulled the calendar from akshare; this version derives it
from the Shanghai Composite's daily bars over MARVEL's direct-HTTP Sina path,
so no akshare dependency is introduced.

Everything degrades gracefully: if the calendar cannot be fetched (offline,
data source down), the weekend rule is used and the callers still work.

Why this exists: without it the UI defaults to ``date.today()``, which on a
weekend or public holiday launches an analysis of a day the market never
traded, and the resulting "no data" looks like a data-source failure rather
than a calendar fact.
"""

from __future__ import annotations

import json as _json
import re
import time
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import requests as _requests

CN_TZ = ZoneInfo("Asia/Shanghai")

# Sina's daily K-line endpoint — the same one _sina_kline_fallback() uses in
# a_stock.py, pointed at the Shanghai Composite instead of a single stock.
_SINA_KLINE_URL = (
    "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)
_SINA_INDEX_SYMBOL = "sh000001"
_KLINE_LEN = 800  # ~3.2 years of trading days

_CACHE_TTL_SECONDS = 6 * 3600
_cache: tuple[float, list[date], set[date]] | None = None

_CN_SYMBOL_RE = re.compile(r"^\d{6}(\.(SH|SZ|SS|BJ))?$", re.IGNORECASE)


def now_cn() -> datetime:
    """Current time in the Asia/Shanghai timezone."""
    return datetime.now(CN_TZ)


def cn_today_str() -> str:
    return now_cn().date().strftime("%Y-%m-%d")


def _parse_date(date_str: str) -> date:
    return datetime.strptime(str(date_str).strip()[:10], "%Y-%m-%d").date()


def reset_cache() -> None:
    """Drop the cached calendar. Used by tests and by long-running processes."""
    global _cache
    _cache = None


def _load_cn_trade_dates() -> tuple[list[date], set[date]]:
    """Return (sorted trading dates, same as a set), or ([], set()) on failure."""
    global _cache

    fresh = time.time()
    if _cache is not None and fresh - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1], _cache[2]

    dates: list[date] = []
    try:
        response = _requests.get(
            _SINA_KLINE_URL,
            params={
                "symbol": _SINA_INDEX_SYMBOL,
                "scale": "240",
                "ma": "no",
                "datalen": str(_KLINE_LEN),
            },
            timeout=8,
        )
        response.raise_for_status()
        payload = _json.loads(response.text)
        if isinstance(payload, list):
            parsed = {
                _parse_date(item["day"])
                for item in payload
                if isinstance(item, dict) and item.get("day")
            }
            dates = sorted(parsed)
    except Exception:  # noqa: BLE001 — any failure falls back to the weekend rule
        dates = []

    _cache = (fresh, dates, set(dates))
    return dates, set(dates)


def is_cn_symbol(symbol: str) -> bool:
    """True for a 6-digit A-share code, with or without an exchange suffix."""
    return bool(_CN_SYMBOL_RE.match(str(symbol).strip().upper()))


def is_cn_trading_day(date_str: str) -> bool:
    """True when the A-share market is open on that date."""
    try:
        target = _parse_date(date_str)
    except (ValueError, TypeError):
        return False

    dates, dates_set = _load_cn_trade_dates()
    if dates:
        # Only trust the fetched calendar for dates it can actually cover.
        if dates[0] <= target <= dates[-1]:
            return target in dates_set
        if target > dates[-1]:
            return target.weekday() < 5  # beyond the window: weekday rule
        return False  # before the window: treat as unknown/non-trading
    return target.weekday() < 5


def previous_cn_trading_day(date_str: str) -> str:
    """The last trading day strictly before ``date_str``."""
    target = _parse_date(date_str)
    dates, _ = _load_cn_trade_dates()
    if dates:
        lo, hi = 0, len(dates)
        while lo < hi:
            mid = (lo + hi) // 2
            if dates[mid] < target:
                lo = mid + 1
            else:
                hi = mid
        if lo - 1 >= 0:
            return dates[lo - 1].strftime("%Y-%m-%d")

    cur = target
    for _ in range(30):
        cur = date.fromordinal(cur.toordinal() - 1)
        if cur.weekday() < 5:
            return cur.strftime("%Y-%m-%d")
    return target.strftime("%Y-%m-%d")


def cn_market_phase(now: datetime | None = None) -> str:
    """One of: closed / pre_open / in_session / lunch_break / post_close."""
    now_dt = now or now_cn()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=CN_TZ)
    else:
        now_dt = now_dt.astimezone(CN_TZ)

    today = now_dt.date().strftime("%Y-%m-%d")
    if not is_cn_trading_day(today):
        return "closed"

    t = now_dt.time()
    if t < dtime(9, 30):
        return "pre_open"
    if t < dtime(11, 30):
        return "in_session"
    if t < dtime(13, 0):
        return "lunch_break"
    if t < dtime(15, 0):
        return "in_session"
    return "post_close"


def latest_cn_trading_day(now: datetime | None = None) -> str:
    """The most recent trading day whose daily bar should exist.

    Today counts only once the session has closed; during the trading day the
    last *completed* session is yesterday's, which is what an analysis should
    default to.
    """
    now_dt = now or now_cn()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=CN_TZ)
    else:
        now_dt = now_dt.astimezone(CN_TZ)

    today = now_dt.date().strftime("%Y-%m-%d")
    if is_cn_trading_day(today) and cn_market_phase(now_dt) == "post_close":
        return today
    return previous_cn_trading_day(today)


def cn_no_data_reason(date_str: str) -> str:
    """A human-readable reason why that date has no A-share data."""
    if not is_cn_trading_day(date_str):
        return "N/A：非交易日（A股休市）"

    if date_str == cn_today_str():
        phase = cn_market_phase()
        if phase == "pre_open":
            return "N/A：今日尚未开盘"
        if phase in ("in_session", "lunch_break"):
            return "N/A：今日盘中，日线未收盘（可参考实时价）"
        if phase == "post_close":
            return "N/A：今日已收盘，数据源尚未更新"

    return "N/A：该交易日暂无数据（可能停牌或数据延迟）"
