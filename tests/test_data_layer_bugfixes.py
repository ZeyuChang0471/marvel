"""Regression guards for the data-layer defects found in this review pass.

Each of these is the repo's recurring failure shape: the code produces a
**plausible** result that is quietly wrong, so nothing downstream can tell.

1. `_sina_kline_fallback` built `sz430047` for Beijing Stock Exchange codes. Sina
   answers `null` for that, so the fallback returned nothing for every BSE code —
   exactly when it is the only source left (mootdx unreachable) and the bars do
   exist at `bj430047`. `CHANGELOG.md` claimed the Sina prefix rule had been
   unified onto `_get_prefix`; the K-line path was missed.
2. `get_stock_name` rejected `4xxxxx` at its entry gate, so the UI showed a bare
   code for stocks Tencent does name.
3. Alpha Vantage's look-ahead filter was dead code: `_make_api_request` returns
   response **text**, and the filter bailed out on anything that is not a dict, so
   fiscal periods ending after the analysis date passed straight through.
4. `get_dragon_tiger_board` swallowed seat/institution failures with a bare
   `except: pass` — an interface failure presented as "no institutional seats" —
   and left `data`/`buy_data`/`sell_data` unbound when the first query raised.
5. `stockstats_utils.load_ohlcv` asked Yahoo for the bare 6-digit code;
   `yf.download("600519")` returns an *empty* frame rather than raising, so the
   indicator path reported "not a trading day" for every date.
"""

from __future__ import annotations

import json

import pytest

from marvel.dataflows import a_stock


# ---------------------------------------------------------------------------
# 1. Beijing exchange codes in the Sina K-line fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSinaKlinePrefix:
    def _capture_symbol(self, monkeypatch) -> dict:
        captured: dict = {}

        class FakeResponse:
            # `_sina_kline_fallback` uses `r.text` + `json.loads`, not `r.json()`
            text = "[]"

            def raise_for_status(self):
                return None

        def fake_get(url, params=None, **kwargs):
            captured["params"] = params or {}
            return FakeResponse()

        monkeypatch.setattr(a_stock._requests, "get", fake_get)
        return captured

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("600519", "sh600519"),
            ("000001", "sz000001"),
            ("300750", "sz300750"),
            ("688017", "sh688017"),
            ("430047", "bj430047"),
            ("830799", "bj830799"),
            ("920002", "bj920002"),
        ],
    )
    def test_exchange_prefix_matches_the_code(self, monkeypatch, code, expected):
        captured = self._capture_symbol(monkeypatch)

        a_stock._sina_kline_fallback(code)

        assert captured["params"]["symbol"] == expected, (
            f"{code} 被请求成 {captured['params']['symbol']!r}——"
            "北交所代码在备用源上会静默拿不到数据"
        )


# ---------------------------------------------------------------------------
# 2. get_stock_name accepts the exchanges the rest of the layer accepts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStockNameAcceptsBse:
    @pytest.mark.parametrize("code", ["600519", "300750", "688017", "430047", "830799"])
    def test_every_supported_exchange_is_looked_up(self, monkeypatch, code):
        seen: list = []

        def fake_quote(codes):
            seen.append(list(codes))
            return {codes[0]: {"name": "某股票"}}

        monkeypatch.setattr(a_stock, "_tencent_quote", fake_quote)

        assert a_stock.get_stock_name(code) == "某股票", f"{code} 没有走查询"
        assert seen == [[code]]

    def test_non_a_share_is_still_rejected(self, monkeypatch):
        monkeypatch.setattr(
            a_stock, "_tencent_quote", lambda codes: pytest.fail("不该发起查询")
        )

        assert a_stock.get_stock_name("AAPL") is None
        assert a_stock.get_stock_name("00700") is None


# ---------------------------------------------------------------------------
# 3. The Alpha Vantage look-ahead filter is reachable
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAlphaVantageDateFilter:
    def _payload(self) -> str:
        return json.dumps({
            "annualReports": [
                {"fiscalDateEnding": "2019-12-31"},
                {"fiscalDateEnding": "2099-12-31"},
            ],
            "quarterlyReports": [
                {"fiscalDateEnding": "2020-03-31"},
                {"fiscalDateEnding": "2020-06-30"},
            ],
        })

    def test_future_fiscal_periods_are_removed(self):
        from marvel.dataflows.alpha_vantage_fundamentals import _filter_reports_by_date

        result = _filter_reports_by_date(self._payload(), "2020-04-15")

        assert isinstance(result, str), "输出形状被改成了 dict，调用方拿到的东西变了"
        parsed = json.loads(result)
        assert [r["fiscalDateEnding"] for r in parsed["annualReports"]] == ["2019-12-31"]
        assert [r["fiscalDateEnding"] for r in parsed["quarterlyReports"]] == ["2020-03-31"]

    def test_without_curr_date_nothing_is_filtered(self):
        from marvel.dataflows.alpha_vantage_fundamentals import _filter_reports_by_date

        payload = self._payload()

        assert json.loads(_filter_reports_by_date(payload, None)) == json.loads(payload)

    def test_non_json_payload_passes_through(self):
        from marvel.dataflows.alpha_vantage_fundamentals import _filter_reports_by_date

        assert _filter_reports_by_date("not json at all", "2020-01-01") == "not json at all"


# ---------------------------------------------------------------------------
# 4. Dragon-tiger seat/institution failures are reported, not swallowed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDragonTigerFailureHandling:
    def _patch(self, monkeypatch, *, list_fails=False, seat_fails=False):
        calls = {"n": 0}

        def fake_datacenter(report_name, **kwargs):
            if report_name == "RPT_DAILYBILLBOARD_DETAILSNEW":
                if list_fails:
                    raise RuntimeError("东财挂了")
                return [{"TRADE_DATE": "2026-05-12", "EXPLANATION": "日涨幅偏离值达7%"}]
            calls["n"] += 1
            if seat_fails:
                raise RuntimeError("席位接口挂了")
            return []

        monkeypatch.setattr(a_stock, "_eastmoney_datacenter", fake_datacenter)

    def test_first_query_failure_does_not_raise_name_error(self, monkeypatch):
        """`data` used to be unbound, so `if data:` raised NameError into a bare pass."""
        self._patch(monkeypatch, list_fails=True)

        text = a_stock.get_dragon_tiger_board("600519", "2026-05-12")

        assert "龙虎榜列表查询失败" in text
        assert isinstance(text, str)

    def test_seat_query_failure_is_reported(self, monkeypatch):
        """A failed seat query must not read as 'there were no seats'."""
        self._patch(monkeypatch, seat_fails=True)

        text = a_stock.get_dragon_tiger_board("600519", "2026-05-12")

        assert "席位明细查询失败" in text, (
            "席位接口失败被静默吞掉，读者会以为这只票没有机构席位"
        )
        assert "机构动向" not in text or "机构动向统计失败" in text


# ---------------------------------------------------------------------------
# 5. Yahoo gets a symbol it can actually resolve
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestYahooSymbolMapping:
    @pytest.mark.parametrize(
        "code,expected",
        [
            ("600519", "600519.SS"),
            ("688017", "688017.SS"),
            ("000001", "000001.SZ"),
            ("300750", "300750.SZ"),
            ("430047", "430047.BJ"),
            ("830799", "830799.BJ"),
        ],
    )
    def test_a_share_codes_get_an_exchange_suffix(self, code, expected):
        from marvel.dataflows.stockstats_utils import _yahoo_symbol

        assert _yahoo_symbol(code) == expected

    def test_non_a_share_input_is_left_alone(self):
        """An index symbol like 000300.SS must not be mangled."""
        from marvel.dataflows.stockstats_utils import _yahoo_symbol

        assert _yahoo_symbol("000300.SS") == "000300.SS"
        assert _yahoo_symbol("^GSPC") == "^GSPC"
