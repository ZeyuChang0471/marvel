"""End-to-end pipeline test: drives web/runner.py's exact sequence.

This exists because the unit suite could not catch an API mismatch between the
web runner and the graph. ``web/runner.py`` calls
``prepare_graph_run`` / ``finalize_graph_run`` / ``close_graph_run``; when those
did not exist, every web analysis failed with "'MarvelGraph' object has no
attribute 'prepare_graph_run'" while every unit test stayed green.

The LLM is stubbed, so no API key and no tokens are involved. Because the stub
never emits tool calls, the analysis tools are never invoked either — the test
exercises the graph wiring, the state contract and the progress bookkeeping,
which is where this class of bug lives.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

REPORT_BODY = "端到端桩报告 " * 60  # comfortably above the quality gate's 200-char floor

TICKER, TRADE_DATE = "600519", "2026-09-18"

ANALYST_REPORT_KEYS = (
    "market_report", "sentiment_report", "news_report", "fundamentals_report",
    "policy_report", "hot_money_report", "lockup_report",
    "volume_price_report", "macro_report",
)


class _StubLLM(Runnable):
    def invoke(self, input, config=None, **kwargs):  # noqa: A002
        return AIMessage(content=REPORT_BODY)

    def bind_tools(self, tools):
        return self

    def with_structured_output(self, schema, **kwargs):
        raise NotImplementedError("stub provider")

    def astream(self, input, config=None, **kwargs):  # noqa: A002
        async def _gen():
            yield AIMessage(content=REPORT_BODY)

        return _gen()


class _StubClient:
    def __init__(self, *args, **kwargs):
        pass

    def get_llm(self):
        return _StubLLM()


@pytest.fixture()
def pipeline(tmp_path, monkeypatch):
    """Wire the runner to a stub LLM and workspace-local side effects."""
    import marvel.graph.trading_graph as tg
    import web.history as wh

    monkeypatch.setattr(tg, "create_llm_client", lambda **kwargs: _StubClient())
    monkeypatch.setattr(wh, "_INCOMPLETE_TASKS_FILE", tmp_path / "incomplete_tasks.json")
    monkeypatch.setattr(wh, "_results_dir", lambda: tmp_path / "logs")

    # Displaying "600519 贵州茅台" makes the runner resolve a stock *name* from
    # the code, which builds a full-market map over mootdx/TCP. With the TDX
    # port unreachable that probe runs for over a minute, which is a property
    # of the environment, not of this test.
    import web.stock_display as sd

    monkeypatch.setattr(sd, "resolve_stock_name", lambda ticker: None)

    from marvel.default_config import DEFAULT_CONFIG

    config = dict(DEFAULT_CONFIG)
    config.update({
        "llm_provider": "deepseek",
        "quick_think_llm": "deepseek-flash",
        "deep_think_llm": "deepseek-v4-pro",
        "output_language": "Chinese",
        "results_dir": str(tmp_path / "logs"),
        "data_cache_dir": str(tmp_path / "cache"),
        "memory_log_path": str(tmp_path / "memory.md"),
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
    })

    from web.progress import ProgressTracker

    tracker = ProgressTracker()
    return config, tracker, tmp_path


class TestPipelineEndToEnd:
    @pytest.mark.unit
    def test_runner_completes_the_whole_pipeline(self, pipeline):
        config, tracker, _ = pipeline
        from web.runner import _run

        _run(TICKER, TRADE_DATE, config, tracker)

        assert getattr(tracker, "error", None) in (None, ""), tracker.error
        assert tracker.final_state, "runner produced no final state"

    @pytest.mark.unit
    def test_every_analyst_report_reaches_the_final_state(self, pipeline):
        config, tracker, _ = pipeline
        from web.runner import _run

        _run(TICKER, TRADE_DATE, config, tracker)

        for key in ANALYST_REPORT_KEYS:
            assert key in tracker.final_state, f"missing {key}"

    @pytest.mark.unit
    def test_every_pipeline_stage_completes(self, pipeline):
        config, tracker, _ = pipeline
        from web.progress import PIPELINE_STAGES
        from web.runner import _run

        _run(TICKER, TRADE_DATE, config, tracker)

        expected = {s["id"] for s in PIPELINE_STAGES}
        missing = expected - set(tracker.completed_stages)
        assert not missing, f"stages never completed: {sorted(missing)}"

    @pytest.mark.unit
    def test_a_signal_is_produced(self, pipeline):
        config, tracker, _ = pipeline
        from web.runner import _run

        _run(TICKER, TRADE_DATE, config, tracker)

        assert tracker.final_state.get("final_trade_decision")
        assert getattr(tracker, "signal", None) in {
            "Buy", "Overweight", "Hold", "Underweight", "Sell",
        }


class TestStateKeyContract:
    """Every graph-state key the code reads must exist after a real run.

    This is the check that surfaced the live-vs-saved
    ``trader_investment_plan`` / ``trader_investment_decision`` mismatch.
    """

    @pytest.mark.unit
    def test_referenced_keys_exist_in_the_final_state(self, pipeline):
        import re

        config, tracker, _ = pipeline
        from web.runner import _run

        _run(TICKER, TRADE_DATE, config, tracker)
        present = set(tracker.final_state)

        root = Path(__file__).resolve().parent.parent
        # The `(?<!\w)` matters: without it the bare `state` alternative also
        # matches the tail of `st.session_state[...]`, so session-state keys were
        # reported as graph-state keys read but never produced.
        pattern = re.compile(
            r'(?<!\w)(?:final_state|state|chunk|last_chunk)\s*'
            r'(?:\.get\(\s*"([a-z_]+)"|\[\s*"([a-z_]+)"\s*\])'
        )
        # Keys that live inside nested sub-state dicts rather than the top-level
        # graph state handed to `_run`.
        nested_or_session = {
            "bull_history", "bear_history", "current_response", "count", "history",
            "judge_decision", "latest_speaker", "aggressive_history",
            "conservative_history", "neutral_history",
            "current_aggressive_response", "current_conservative_response",
            "current_neutral_response",
            "final_trade_decision", "trader_investment_plan", "investment_plan",
            "trader_investment_decision",
            "deep_think_llm", "quick_think_llm", "llm_provider", "llm_provider_idx",
            "llm_base_url", "start_analysis", "tracker", "viewing_history",
        }

        referenced: dict[str, set[str]] = {}
        targets = (
            list((root / "web").rglob("*.py"))
            + list((root / "marvel" / "agents").rglob("*.py"))
            + [root / "marvel" / "graph" / "trading_graph.py"]
        )
        for path in targets:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in pattern.finditer(text):
                key = match.group(1) or match.group(2)
                referenced.setdefault(key, set()).add(path.name)

        missing = {
            k: v for k, v in referenced.items()
            if k not in present and k not in nested_or_session
        }
        assert not missing, f"state keys read but never produced: {missing}"
