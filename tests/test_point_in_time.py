"""Guards that the analysis date — not the model — decides how far data reaches.

The defect these tests exist for: MARVEL retrieves data through tools whose date
arguments are written by the **model**, and the data layer trusted them.

* ``get_news(ticker, start_date, end_date)`` did not take a ``curr_date`` at all.
  Its window was entirely model-chosen, and the data layer only filtered *inside*
  it. Re-running a 2026-05-12 analysis therefore returned whatever window the
  model typed — including news published months after the analysis date, which
  the report then presented as known at the time. Back-test results inherited it.
* ``get_stock_data`` clamped ``end_date`` to *market today*, not to the analysis
  date, so the same leak existed for bars.

The fix has two ends, and both are tested here:

1. **tool layer** — every tool that anchors a window resolves the date from the
   run context (``anchored_date``), so the model cannot widen it, and the
   ToolNode executes inside ``analysis_date_as_of(state["trade_date"])``;
2. **data layer** — every date-taking vendor function is wrapped in
   ``@clamp_arguments``, which holds even for callers that never went through a
   tool.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from marvel.dataflows import as_of
from marvel.dataflows.as_of import (
    anchored_date,
    analysis_date,
    analysis_date_as_of,
    clamp_upper,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DATE = "2026-05-12"


@pytest.fixture
def bound():
    """Run inside a 2026-05-12 analysis context, like a real back-test."""
    with analysis_date_as_of(ANALYSIS_DATE):
        yield ANALYSIS_DATE


# ---------------------------------------------------------------------------
# The clamps themselves
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClampUpper:
    def test_future_date_is_pulled_back_to_the_analysis_date(self, bound):
        value, note = clamp_upper("2026-12-31", label="end_date")

        assert value == ANALYSIS_DATE
        assert "未来函数防护" in note

    def test_past_date_is_left_alone(self, bound):
        value, note = clamp_upper("2026-01-01", label="end_date")

        assert value == "2026-01-01"
        assert note == ""

    def test_the_analysis_date_itself_is_not_clamped(self, bound):
        value, note = clamp_upper(ANALYSIS_DATE)

        assert value == ANALYSIS_DATE
        assert note == ""

    def test_unparseable_date_becomes_the_analysis_date(self, bound):
        """An unreadable upper bound must not be treated as "no limit"."""
        value, note = clamp_upper("next friday", label="end_date")

        assert value == ANALYSIS_DATE
        assert "无法解析" in note

    def test_nothing_is_bound_means_nothing_changes(self):
        """The guard must never invent a date: no run context, no clamp."""
        assert clamp_upper("2026-12-31") == ("2026-12-31", "")
        assert clamp_upper(None) == (None, "")

    def test_empty_value_is_untouched(self, bound):
        assert clamp_upper("") == ("", "")


@pytest.mark.unit
class TestAnchoredDate:
    def test_ignores_what_the_model_wrote(self, bound):
        assert anchored_date("2026-12-31") == ANALYSIS_DATE
        assert anchored_date("2019-01-01") == ANALYSIS_DATE

    def test_fills_in_a_missing_argument(self, bound):
        """A model that omits curr_date is still correctly anchored."""
        assert anchored_date(None) == ANALYSIS_DATE

    def test_falls_back_to_the_model_value_when_unbound(self):
        assert anchored_date("2026-12-31") == "2026-12-31"
        assert anchored_date(None) is None


@pytest.mark.unit
class TestAnalysisDateContext:
    def test_unbound_by_default(self):
        assert analysis_date() is None

    def test_context_is_restored_after_the_block(self):
        with analysis_date_as_of(ANALYSIS_DATE):
            assert analysis_date() is not None
        assert analysis_date() is None

    def test_accepts_date_objects_and_slash_format(self):
        with analysis_date_as_of("2026/05/12"):
            assert analysis_date().isoformat() == ANALYSIS_DATE


# ---------------------------------------------------------------------------
# End 1 — the data layer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDataLayerClamp:
    def _stub_news(self, monkeypatch):
        import marvel.dataflows.a_stock as a_stock

        monkeypatch.setattr(
            a_stock,
            "_fetch_news_eastmoney",
            lambda code: [
                {"title": "BEFORE", "time": "2026-05-10", "content": "known then"},
                {"title": "AFTER", "time": "2026-08-01", "content": "the future"},
            ],
        )

    def test_news_cannot_reach_past_the_analysis_date(self, monkeypatch, bound):
        """This is the exact back-test leak: the model asks for a window that
        ends months after the analysis date."""
        self._stub_news(monkeypatch)
        import marvel.dataflows.a_stock as a_stock

        out = a_stock.get_news("600519", "2026-01-01", "2026-12-31")

        assert "BEFORE" in out
        assert "AFTER" not in out, "分析日之后的新闻进入了报告"
        assert "未来函数防护" in out, "裁剪发生了却没说"

    def test_without_a_run_context_the_model_window_is_honoured(
        self, monkeypatch
    ):
        """Backwards compatibility: library use outside a run is unchanged."""
        self._stub_news(monkeypatch)
        import marvel.dataflows.a_stock as a_stock

        out = a_stock.get_news("600519", "2026-01-01", "2026-12-31")

        assert "AFTER" in out
        assert "未来函数防护" not in out


# ---------------------------------------------------------------------------
# End 2 — the tool layer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToolLayerAnchor:
    def test_news_tool_anchors_the_window(self, monkeypatch, bound):
        import marvel.dataflows.a_stock as a_stock
        from marvel.agents.utils.agent_utils import get_news

        monkeypatch.setattr(
            a_stock,
            "_fetch_news_eastmoney",
            lambda code: [
                {"title": "AFTER", "time": "2026-08-01", "content": "the future"},
            ],
        )

        out = get_news.invoke(
            {"ticker": "600519", "start_date": "2026-01-01", "end_date": "2026-12-31"}
        )

        assert "AFTER" not in out, "工具层没有把窗口钉到分析日"

    def test_curr_date_tool_takes_the_analysis_date(self, monkeypatch, bound):
        """A tool whose anchor is curr_date must pass the analysis date down."""
        import marvel.agents.utils.fundamental_data_tools as fdt

        seen: dict = {}

        def capture(method, *args, **kwargs):
            seen["method"] = method
            seen["kwargs"] = kwargs
            return "table"

        monkeypatch.setattr(fdt, "route_to_vendor", capture)

        fdt.get_balance_sheet.invoke(
            {"ticker": "600519", "curr_date": "2026-12-31", "freq": "quarterly"}
        )

        assert seen["kwargs"]["curr_date"] == ANALYSIS_DATE, (
            "财报工具用的仍是模型填的 curr_date"
        )

    def test_tools_keep_their_descriptions(self):
        """The anchor line is inserted after the docstring, not before it.

        LangChain reads the docstring as the tool description; inserting ahead of
        it would silently strip every tool's description and degrade tool choice.
        """
        from marvel.agents.utils.agent_utils import (
            get_balance_sheet,
            get_global_news,
            get_hot_stocks,
            get_news,
            get_stock_data,
        )

        for tool in (
            get_stock_data,
            get_news,
            get_global_news,
            get_balance_sheet,
            get_hot_stocks,
        ):
            assert tool.description and len(tool.description.strip()) > 40, (
                f"{tool.name} 的 description 丢了"
            )

    def test_tool_signatures_are_unchanged(self):
        """Anchoring is an internal detail: models still see the same parameters."""
        from marvel.agents.utils.agent_utils import get_balance_sheet, get_news

        assert set(get_news.args_schema.model_fields) == {
            "ticker", "start_date", "end_date",
        }
        assert "curr_date" in get_balance_sheet.args_schema.model_fields


@pytest.mark.unit
class TestToolNodeGuard:
    def test_tool_node_executes_inside_the_run_date(self):
        """The ToolNode is where tools actually run, so the date must be bound there."""
        from marvel.graph.trading_graph import MarvelGraph

        observed: dict = {}

        class FakeToolNode:
            name = "fake"

            def invoke(self, state):
                observed["date"] = analysis_date()
                return {"messages": []}

        guarded = MarvelGraph._guard_tool_node(FakeToolNode())
        guarded({"trade_date": ANALYSIS_DATE, "messages": []})

        assert observed["date"] is not None
        assert observed["date"].isoformat() == ANALYSIS_DATE
        # and it must not leak out of the node
        assert analysis_date() is None

    def test_guard_tolerates_a_missing_trade_date(self):
        from marvel.graph.trading_graph import MarvelGraph

        observed: dict = {}

        class FakeToolNode:
            name = "fake"

            def invoke(self, state):
                observed["date"] = analysis_date()
                return {}

        MarvelGraph._guard_tool_node(FakeToolNode())({"messages": []})

        assert observed["date"] is None


@pytest.mark.unit
class TestSourcesWithNoHistoricalVersionAreLabelled:
    """A source that only knows "now" must say so when the run is a back-test.

    `get_concept_blocks` takes no date at all and returns each block's *current*
    change percentage. Nothing marked it, so a historical run presented today's
    board performance as the analysis day's.
    """

    def _stub_pae(self, monkeypatch):
        import marvel.dataflows.a_stock as a_stock

        payload = {
            "ResultCode": "0",
            "Result": {
                "600519": [
                    {"name": "概念", "list": [{"name": "白酒", "ratio": "+1.2%"}]},
                ]
            },
        }

        class FakeResponse:
            def json(self):
                return payload

        monkeypatch.setattr(
            "requests.get", lambda *a, **k: FakeResponse(), raising=False
        )
        return a_stock

    def test_historical_run_gets_the_snapshot_notice(self, monkeypatch, bound):
        a_stock = self._stub_pae(monkeypatch)

        out = a_stock.get_concept_blocks("600519")

        assert "未来函数警告" in out, "实时快照没有标注，回测会把它当成当天的事实"

    def test_live_run_is_not_labelled(self, monkeypatch):
        a_stock = self._stub_pae(monkeypatch)
        today = a_stock._market_today().isoformat()

        with analysis_date_as_of(today):
            out = a_stock.get_concept_blocks("600519")

        assert "未来函数警告" not in out


# ---------------------------------------------------------------------------
# The memory log is a second, independent look-ahead channel
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMemoryLogRespectsTheAnalysisDate:
    """The log is append-only and shared across runs.

    Re-running an old date after a newer run used to feed the old run the newer
    run's decision — and its realised alpha, which is future information by
    definition.
    """

    def _log(self, tmp_path):
        from marvel.agents.utils.memory import TradingMemoryLog

        return TradingMemoryLog(
            {"memory_log_path": str(tmp_path / "trading_memory.md")}
        )

    def _seed(self, log):
        log.store_decision(
            "600519", "2026-04-01", "**Rating**: Hold\n四月：按兵不动。"
        )
        log.store_decision(
            "600519", "2026-09-01", "**Rating**: Sell\n九月：已经卖出了。"
        )
        # both must be resolved (non-pending) to be eligible for injection
        log.update_with_outcome("600519", "2026-04-01", 0.01, 0.0, 5, "四月复盘。")
        log.update_with_outcome("600519", "2026-09-01", -0.2, -0.1, 5, "九月复盘。")

    def test_future_decisions_are_not_injected(self, tmp_path):
        log = self._log(tmp_path)
        self._seed(log)

        ctx = log.get_past_context("600519", as_of="2026-05-12")

        assert "四月" in ctx, "分析日之前的决策应当注入"
        assert "九月" not in ctx, "分析日之后的决策泄漏进了历史复盘"
        assert "九月复盘" not in ctx

    def test_same_day_decisions_are_allowed(self, tmp_path):
        """A decision made on the analysis date is known by then."""
        log = self._log(tmp_path)
        self._seed(log)

        ctx = log.get_past_context("600519", as_of="2026-09-01")

        assert "九月" in ctx

    def test_without_as_of_behaviour_is_unchanged(self, tmp_path):
        log = self._log(tmp_path)
        self._seed(log)

        ctx = log.get_past_context("600519")

        assert "四月" in ctx and "九月" in ctx

    def test_the_graph_passes_the_trade_date(self):
        """Regression: the call site must supply the bound, not just support it."""
        src = (
            REPO_ROOT / "marvel" / "graph" / "trading_graph.py"
        ).read_text(encoding="utf-8")

        assert "get_past_context(" in src
        assert "as_of=trade_date" in src, (
            "记忆日志没有按分析日过滤，回测会读到之后才做出的决策"
        )


# ---------------------------------------------------------------------------
# Structural guard: a new date-taking vendor function must not skip the clamp
# ---------------------------------------------------------------------------

_ANCHOR_PARAMS = {"curr_date", "end_date", "trade_date"}


def _vendor_functions() -> list[tuple[str, set[str], list[str]]]:
    """(name, date params, decorator exprs) for every function in a_stock.py."""
    tree = ast.parse(
        (REPO_ROOT / "marvel" / "dataflows" / "a_stock.py").read_text(encoding="utf-8")
    )
    found = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        params = {arg.arg for arg in node.args.args}
        dates = params & _ANCHOR_PARAMS
        if not dates:
            continue
        decorators = [ast.unparse(d) for d in node.decorator_list]
        found.append((node.name, dates, decorators))
    return found


class TestEveryDateTakingVendorFunctionIsClamped:
    def test_the_scan_finds_the_expected_surface(self):
        """If this drops, the guard below stopped looking at anything."""
        names = {name for name, _, _ in _vendor_functions()}

        for expected in (
            "get_news", "get_global_news", "get_stock_data", "get_indicators",
            "get_fundamentals", "get_balance_sheet", "get_cashflow",
            "get_income_statement", "get_profit_forecast", "get_hot_stocks",
            "get_northbound_flow", "get_fund_flow", "get_dragon_tiger_board",
            "get_lockup_expiry", "get_industry_comparison",
        ):
            assert expected in names, f"{expected} 不再被扫描到，守卫失效了"

    def test_each_one_declares_the_clamp(self):
        missing = []
        for name, dates, decorators in _vendor_functions():
            if name.startswith("_"):
                # Private helpers take an already-clamped date from the public
                # wrapper that calls them (`_get_financial_report_sina` from the
                # three statements, `_load_ohlcv_astock` from get_indicators).
                continue
            declared = {
                param
                for dec in decorators
                if "clamp_arguments" in dec
                for param in dates
                # ast.unparse renders the literal with single quotes
                if f"'{param}'" in dec or f'"{param}"' in dec
            }
            if declared != dates:
                missing.append(
                    f"{name}: 日期参数 {sorted(dates - declared)} 没有 @clamp_arguments"
                )

        assert not missing, (
            "这些函数收模型给的日期却没有做分析日裁剪，未来函数会从这里进来：\n  "
            + "\n  ".join(missing)
        )
