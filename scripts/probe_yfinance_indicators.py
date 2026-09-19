"""MANUAL PROBE — yfinance indicator path (non-A-share vendor).

⚠️ **This is not a test.** It makes live network requests. It used to live at
the repository root as ``test.py`` with the calls at module level, so importing
it (pytest collection, an IDE opening the file) hit the network immediately.

Run it explicitly:

    python scripts/probe_yfinance_indicators.py
"""

from __future__ import annotations

import time


def main() -> None:
    from marvel.dataflows.y_finance import get_stock_stats_indicators_window

    print("Testing optimized implementation with 30-day lookback:")
    start_time = time.time()
    result = get_stock_stats_indicators_window("AAPL", "macd", "2024-11-01", 30)
    end_time = time.time()

    print(f"Execution time: {end_time - start_time:.2f} seconds")
    print(f"Result length: {len(result)} characters")
    print(result)


if __name__ == "__main__":
    main()
