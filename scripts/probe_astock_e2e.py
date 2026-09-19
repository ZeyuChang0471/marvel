"""MANUAL PROBE — end-to-end run of the MARVEL pipeline on an A-stock.

⚠️ **This is not a test.** It makes real, billed LLM calls and real network
requests against live data sources. It used to live at the repository root as
``test_astock.py`` with everything at module level, so merely *importing* it —
which is what pytest does during collection, and what an IDE does when opening
the file — fired a full multi-agent analysis against a paid endpoint.

Run it explicitly:

    python scripts/probe_astock_e2e.py
"""

from __future__ import annotations


def main() -> None:
    from dotenv import load_dotenv

    from marvel.default_config import DEFAULT_CONFIG
    from marvel.graph.trading_graph import MarvelGraph

    load_dotenv()

    config = DEFAULT_CONFIG.copy()

    # --- LLM: Kimi 2.6 via Anthropic-compatible API ---
    config["llm_provider"] = "anthropic"
    config["deep_think_llm"] = "claude-sonnet-4-6"   # Kimi maps internally
    config["quick_think_llm"] = "claude-sonnet-4-6"
    config["backend_url"] = "https://api.kimi.com/coding/"

    # --- Data: A-stock vendor (mootdx + tencent + eastmoney + sina) ---
    config["data_vendors"] = {
        "core_stock_apis": "a_stock",
        "technical_indicators": "a_stock",
        "fundamental_data": "a_stock",
        "news_data": "a_stock",
        "signal_data": "a_stock",
    }

    # --- Debate settings: minimal for a first run ---
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["output_language"] = "Chinese"

    print("=" * 60)
    print("MARVEL E2E probe")
    print("Ticker: 688017")
    print("Trade date: 2026-04-30")
    print("LLM: Kimi 2.6 via Anthropic API")
    print("Data: a_stock (mootdx + tencent + eastmoney + sina)")
    print("=" * 60)

    ta = MarvelGraph(debug=True, config=config)

    _, decision = ta.propagate("688017", "2026-04-30")
    print("\n" + "=" * 60)
    print("FINAL DECISION:")
    print("=" * 60)
    print(decision)


if __name__ == "__main__":
    main()
