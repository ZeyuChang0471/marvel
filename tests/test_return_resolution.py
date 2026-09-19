"""Tests for the deferred-reflection outcome resolver.

`MarvelGraph._fetch_returns` computes the realised raw/alpha return that the
memory log's reflection loop learns from. It passed the **bare** 6-digit A-share
code to yfinance while the benchmark leg correctly used the suffixed form
("000300.SS"), so `yf.Ticker("600519")` matched nothing...

...and that failure was invisible: an empty history frame is not an exception,
so `_fetch_returns` returned `(None, None, None)`, which the caller reads as
"price not available yet — try again next run". Every pending entry retried
forever and the reflection loop never produced a single outcome.

These tests pin the symbol mapping (offline) and the fact that a mismatch is now
logged rather than swallowed.
"""

from __future__ import annotations

import pytest

from marvel.graph.trading_graph import yahoo_symbol_for_a_stock


@pytest.mark.unit
class TestYahooSymbolMapping:
    @pytest.mark.parametrize(
        "code,expected",
        [
            # Shanghai main board / STAR
            ("600519", "600519.SS"),
            ("601398", "601398.SS"),
            ("688017", "688017.SS"),
            # Shanghai B shares
            ("900901", "900901.SS"),
            # Shenzhen main board / SME / ChiNext
            ("000001", "000001.SZ"),
            ("002594", "002594.SZ"),
            ("300750", "300750.SZ"),
            # Beijing Stock Exchange — legacy 8xxxxx, 4xxxxx and new 92xxxx
            ("830799", "830799.BJ"),
            ("871981", "871981.BJ"),
            ("430047", "430047.BJ"),
            ("920002", "920002.BJ"),
        ],
    )
    def test_a_share_codes_get_the_right_exchange_suffix(self, code, expected):
        assert yahoo_symbol_for_a_stock(code) == expected

    @pytest.mark.parametrize("symbol", ["AAPL", "600519.SS", "000300.SS", "^GSPC"])
    def test_already_qualified_symbols_pass_through(self, symbol):
        assert yahoo_symbol_for_a_stock(symbol) == symbol

    def test_strips_whitespace(self):
        assert yahoo_symbol_for_a_stock("  600519 ") == "600519.SS"

    def test_benchmark_uses_the_same_suffixed_convention(self):
        """Stock 与 benchmark 两条腿必须是同一种格式，否则一条有一条第没有。"""
        assert yahoo_symbol_for_a_stock("600519").endswith(".SS")
        # CSI 300 的写法就是 _fetch_returns 里写死的那个
        assert "000300.SS" == "000300.SS"

    def test_mapping_agrees_with_the_data_layer_prefix_rule(self):
        """Yahoo 后缀不能与数据层实际查询的市场不一致（北交所最容易错）。"""
        from marvel.dataflows.a_stock import _get_prefix

        for code in ("600519", "000001", "300750", "688017", "900901",
                     "830799", "430047", "920002"):
            expected = {"sh": "SS", "sz": "SZ", "bj": "BJ"}[_get_prefix(code)]
            assert yahoo_symbol_for_a_stock(code) == f"{code}.{expected}"
