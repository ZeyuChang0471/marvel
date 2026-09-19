"""MANUAL PROBE — data-quality sweep over every ``a_stock`` vendor endpoint.

⚠️ **This is not a test.** It calls all 17 live data sources (mootdx TCP,
Tencent, Eastmoney, Sina, 同花顺, 财联社, 百度), so it needs a mainland-China
network position and it is subject to the upstream rate limits. It used to live
at the repository root as ``test_data_quality.py`` with the sweep at module
level, so importing it (pytest collection, an IDE opening the file) fired every
request immediately and then reported "no tests ran".

Run it explicitly:

    python scripts/probe_data_quality.py

Checks per endpoint: return type, non-empty, length, and a short preview.
"""

from __future__ import annotations

import time
import traceback

TICKER = "300750"          # 宁德时代
TICKER_LABEL = "宁德时代"
TRADE_DATE = "2026-05-12"
START_DATE = "2026-04-01"
END_DATE = "2026-05-12"


def build_tests():
    from marvel.dataflows.a_stock import (
        get_balance_sheet,
        get_cashflow,
        get_concept_blocks,
        get_dragon_tiger_board,
        get_fund_flow,
        get_fundamentals,
        get_global_news,
        get_hot_stocks,
        get_income_statement,
        get_indicators,
        get_industry_comparison,
        get_insider_transactions,
        get_lockup_expiry,
        get_news,
        get_northbound_flow,
        get_profit_forecast,
        get_stock_data,
    )

    return [
        ("1. get_stock_data", lambda: get_stock_data(TICKER, START_DATE, END_DATE)),
        ("2. get_indicators", lambda: get_indicators(TICKER, "rsi", END_DATE, 30)),
        ("3. get_fundamentals", lambda: get_fundamentals(TICKER, TRADE_DATE)),
        ("4. get_balance_sheet",
         lambda: get_balance_sheet(TICKER, "quarterly", TRADE_DATE)),
        ("5. get_cashflow", lambda: get_cashflow(TICKER, "quarterly", TRADE_DATE)),
        ("6. get_income_statement",
         lambda: get_income_statement(TICKER, "quarterly", TRADE_DATE)),
        ("7. get_news", lambda: get_news(TICKER, START_DATE, END_DATE)),
        ("8. get_global_news", lambda: get_global_news(TRADE_DATE, 7, 10)),
        ("9. get_insider_transactions", lambda: get_insider_transactions(TICKER)),
        ("10. get_profit_forecast", lambda: get_profit_forecast(TICKER, TRADE_DATE)),
        ("11. get_hot_stocks", lambda: get_hot_stocks(TRADE_DATE)),
        ("12. get_northbound_flow", lambda: get_northbound_flow(TRADE_DATE, True)),
        ("13. get_concept_blocks", lambda: get_concept_blocks(TICKER)),
        ("14. get_fund_flow", lambda: get_fund_flow(TICKER, TRADE_DATE, True)),
        ("15. get_dragon_tiger_board",
         lambda: get_dragon_tiger_board(TICKER, TRADE_DATE)),
        ("16. get_lockup_expiry", lambda: get_lockup_expiry(TICKER, TRADE_DATE)),
        ("17. get_industry_comparison",
         lambda: get_industry_comparison(TICKER, TRADE_DATE)),
    ]


def main() -> None:
    tests = build_tests()

    print("=" * 70)
    print(f"数据质量实测 | {TICKER} {TICKER_LABEL} | {TRADE_DATE}")
    print(f"（共 {len(tests)} 个端点）")
    print("=" * 70 + "\n")

    results = []
    for name, fn in tests:
        print(f"--- {name} ---")
        t0 = time.time()
        try:
            out = fn()
            elapsed = time.time() - t0
            length = len(out) if isinstance(out, str) else len(str(out))
            preview = out[:800] if isinstance(out, str) else str(out)[:800]
            print(f"  OK | {elapsed:.1f}s | {length} chars")
            print(f"  >>> {preview}")
            if length < 50:
                results.append((name, "WARN", f"too short ({length} chars)"))
            else:
                results.append((name, "OK", f"{length} chars, {elapsed:.1f}s"))
        except Exception as e:  # noqa: BLE001 — a probe reports, it does not raise
            elapsed = time.time() - t0
            print(f"  FAIL | {elapsed:.1f}s | {type(e).__name__}: {e}")
            traceback.print_exc()
            results.append((name, "FAIL", str(e)[:100]))
        print()

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)
    ok = sum(1 for _, s, _ in results if s == "OK")
    warn = sum(1 for _, s, _ in results if s == "WARN")
    fail = sum(1 for _, s, _ in results if s == "FAIL")
    print(f"OK: {ok} | WARN: {warn} | FAIL: {fail} / {len(results)} total\n")
    for name, status, detail in results:
        marker = "✓" if status == "OK" else ("⚠" if status == "WARN" else "✗")
        print(f"  {marker} {name}: {detail}")


if __name__ == "__main__":
    main()
