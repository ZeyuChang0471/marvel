"""MANUAL PROBE — per-source health of the news layer.

⚠️ **This is not a test.** It calls the live news endpoints (East Money
announcements + research + bodies, Sina press, CLS / East Money / Sina wires),
so it needs a mainland-China network position and it is subject to the upstream
rate limits. It is inert on import: nothing happens until you run it.

Why it exists: the news layer has repeatedly *silently degraded* — the East
Money search feed answered a passportWeb stub for weeks, and the CLS wire was
404-ing behind a swallowed JSON error while the report read like "there was no
news that week". A per-source count is the fastest way to see which feed is
lying by omission.

Run it explicitly:

    python scripts/probe_news_sources.py
    python scripts/probe_news_sources.py 600487 300750 --start 2026-09-16 --end 2026-10-16

Exit code is 1 when every source failed for some ticker (network-level outage),
0 otherwise — a source that is merely empty is reported, not failed.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta

DEFAULT_TICKERS = ["600487", "300750", "000001"]


def _window(days: int = 30) -> tuple:
    end = date.today()
    return (end - timedelta(days=days)).isoformat(), end.isoformat()


def build_checks(start_date: str, end_date: str):
    from marvel.dataflows.a_stock import (
        _fetch_news_announcements,
        _fetch_news_research,
        _fetch_news_sina,
        get_global_news,
        get_news,
    )

    return [
        ("公告", lambda code: _fetch_news_announcements(
            code, start_date=start_date, end_date=end_date)),
        ("研报", lambda code: _fetch_news_research(
            code, start_date=start_date, end_date=end_date)),
        ("新闻稿", lambda code: _fetch_news_sina(code)),
    ]


def probe_ticker(code, checks, start_date, end_date):
    from marvel.dataflows.a_stock import get_news

    print("=" * 72)
    print(f"{code}  ({start_date} .. {end_date})")
    failed = 0
    for label, fetch in checks:
        t0 = time.time()
        try:
            items = fetch(code) or []
            first = (items[0].get("title") if items else "") or ""
            print(f"  [OK]   {label:6s} {len(items):3d} 条  {time.time() - t0:5.1f}s  {first[:44]}")
        except Exception as exc:  # noqa: BLE001 — a probe reports, it does not raise
            failed += 1
            print(f"  [FAIL] {label:6s} {type(exc).__name__}: {exc}")

    t0 = time.time()
    try:
        text = get_news(code, start_date, end_date)
        head = text.splitlines()[0] if text else ""
        print(f"  [get_news] {len(text)} chars {time.time() - t0:5.1f}s | {head[:80]}")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        print(f"  [get_news] FAIL {type(exc).__name__}: {exc}")
    return failed, len(checks) + 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MARVEL 新闻源逐源体检（联网）")
    parser.add_argument("tickers", nargs="*", default=None,
                        help="6 位 A 股代码，默认 " + " ".join(DEFAULT_TICKERS))
    parser.add_argument("--start", default=None, help="窗口开始 yyyy-mm-dd")
    parser.add_argument("--end", default=None, help="窗口结束 yyyy-mm-dd")
    args = parser.parse_args(argv)

    default_start, default_end = _window()
    start_date = args.start or default_start
    end_date = args.end or default_end
    tickers = args.tickers or DEFAULT_TICKERS

    checks = build_checks(start_date, end_date)

    total_failed = 0
    total = 0
    for code in tickers:
        failed, count = probe_ticker(code, checks, start_date, end_date)
        total_failed += failed
        total += count

    print("=" * 72)
    print("全球快讯（CLS / 东财 / 新浪三源）")
    t0 = time.time()
    try:
        from marvel.dataflows.a_stock import get_global_news

        text = get_global_news(end_date, 7, 10)
        counts = {name: text.count("source: " + name)
                  for name in ("CLS Wire", "Eastmoney Global", "Sina 7x24")}
        print(f"  [OK] {len(text)} chars {time.time() - t0:5.1f}s | " +
              "、".join(f"{k} {v} 条" for k, v in counts.items()))
    except Exception as exc:  # noqa: BLE001
        total_failed += 1
        total += 1
        print(f"  [FAIL] {type(exc).__name__}: {exc}")

    print("=" * 72)
    print(f"失败 {total_failed} / {total} 次调用（失败 = 抛异常；源返回 0 条只是空，不算失败）")
    return 1 if total_failed and total_failed == total else 0


if __name__ == "__main__":
    sys.exit(main())
