"""Regression tests for the graph run lifecycle and the trader-key mismatch.

Two bugs are pinned here:

1. `web/runner.py` drives the pipeline with
   ``prepare_graph_run`` / ``finalize_graph_run`` / ``close_graph_run``. The
   graph lacked all three, so every web analysis died with
   "'MarvelGraph' object has no attribute 'prepare_graph_run'".

2. The live graph state stores the trader's output under
   ``trader_investment_plan``, but ``_log_state`` renames it to
   ``trader_investment_decision`` in the saved JSON. The report viewer, the PDF
   export and the history rating extractor only knew the saved name, so a live
   report showed no Trader section while the same report reopened from history
   did.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

ROOT = Path(__file__).resolve().parent.parent


class _StubLLM(Runnable):
    def invoke(self, input, config=None, **kwargs):  # noqa: A002
        return AIMessage(content="STUB_REPORT " + "x" * 250)

    def bind_tools(self, tools):
        return self

    def with_structured_output(self, schema, **kwargs):
        raise NotImplementedError("stub provider")


class _StubClient:
    def __init__(self, *args, **kwargs):
        pass

    def get_llm(self):
        return _StubLLM()


@pytest.fixture()
def graph(tmp_path, monkeypatch):
    """A real MarvelGraph wired to a stub LLM and workspace-local dirs."""
    import marvel.graph.trading_graph as tg

    monkeypatch.setattr(tg, "create_llm_client", lambda **kwargs: _StubClient())

    from marvel.default_config import DEFAULT_CONFIG

    config = dict(DEFAULT_CONFIG)
    config.update({
        "llm_provider": "deepseek",
        "quick_think_llm": "deepseek-flash",
        "deep_think_llm": "deepseek-v4-pro",
        "results_dir": str(tmp_path / "logs"),
        "data_cache_dir": str(tmp_path / "cache"),
        "memory_log_path": str(tmp_path / "memory.md"),
    })
    return tg.MarvelGraph(debug=False, config=config)


class TestRunnerAPISurface:
    """Exactly what web/runner.py calls."""

    @pytest.mark.unit
    def test_lifecycle_methods_exist(self, graph):
        for name in ("prepare_graph_run", "finalize_graph_run", "close_graph_run"):
            assert callable(getattr(graph, name, None)), f"missing {name}"

    @pytest.mark.unit
    def test_prepare_graph_run_signature(self, graph):
        params = inspect.signature(graph.prepare_graph_run).parameters
        assert list(params) == ["company_name", "trade_date", "callbacks"]

    @pytest.mark.unit
    def test_finalize_graph_run_signature(self, graph):
        params = inspect.signature(graph.finalize_graph_run).parameters
        assert list(params) == ["company_name", "trade_date", "final_state"]

    @pytest.mark.unit
    def test_prepare_returns_state_args_and_step(self, graph):
        init_state, args, step = graph.prepare_graph_run("600519", "2026-09-18")
        assert isinstance(init_state, dict)
        assert init_state["company_of_interest"] == "600519"
        assert args["stream_mode"] == "values"
        assert step is None  # checkpointing off by default
        assert graph.ticker == "600519"

    @pytest.mark.unit
    def test_close_graph_run_is_safe_without_a_checkpointer(self, graph):
        graph.close_graph_run()
        graph.close_graph_run()  # idempotent

    @pytest.mark.unit
    def test_propagate_is_composed_from_the_lifecycle(self, graph):
        source = inspect.getsource(type(graph).propagate)
        assert "_run_graph" in source and "close_graph_run" in source

    @pytest.mark.unit
    def test_runner_module_still_imports(self):
        """Catches an import-time mismatch without running a real analysis."""
        import importlib

        importlib.import_module("web.runner")


class TestTraderKeyCompatibility:
    """Live state says `_plan`, saved JSON says `_decision`; both must render."""

    @pytest.fixture(autouse=True)
    def _no_name_lookup(self, monkeypatch):
        """Rendering resolves a stock *name* from the code, which builds a
        full-market map over the network (tens of seconds on a first call).
        A unit test must not pay that."""
        import web.stock_display as sd

        monkeypatch.setattr(sd, "resolve_stock_name", lambda ticker: None)

    @pytest.mark.unit
    def test_markdown_renders_trader_section_from_live_key(self):
        from web.pdf_export import generate_markdown

        md = generate_markdown(
            {"trader_investment_plan": "LIVE_PLAN_MARKER", "final_trade_decision": "Hold"},
            "600519",
            "2026-09-18",
            "Hold",
        )
        assert "LIVE_PLAN_MARKER" in md

    @pytest.mark.unit
    def test_markdown_renders_trader_section_from_saved_key(self):
        from web.pdf_export import generate_markdown

        md = generate_markdown(
            {"trader_investment_decision": "SAVED_PLAN_MARKER", "final_trade_decision": "Hold"},
            "600519",
            "2026-09-18",
            "Hold",
        )
        assert "SAVED_PLAN_MARKER" in md

    @pytest.mark.unit
    @pytest.mark.parametrize("key", ["trader_investment_plan", "trader_investment_decision"])
    def test_history_extract_signal_reads_both_keys(self, key):
        from web.history import extract_signal

        state = {key: "**Rating**: Buy\n\nStrong momentum."}
        assert extract_signal(state) == "Buy"

    @pytest.mark.unit
    def test_report_viewer_source_accepts_both_keys(self):
        source = (ROOT / "web" / "components" / "report_viewer.py").read_text(encoding="utf-8")
        assert "trader_investment_plan" in source
        assert "trader_investment_decision" in source
