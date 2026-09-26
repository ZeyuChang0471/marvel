"""Name resolution must not depend on TCP 7709 being reachable.

The mootdx full-market map is the nice path (it supports substring matching), but
it needs a TCP connection to a Tongdaxin server. Corporate networks, proxies and
firewalls block that port routinely — when they do, typing a Chinese stock name
into the Web UI or the CLI used to hard-fail, so the product was unusable for
anyone who did not already know the 6-digit code. The reported symptom was:

    无法通过 mootdx 解析股票名称（通达信服务暂时不可达）：… 或改用 6 位股票代码直接
    查询。。请稍后重试，或直接输入 6 位股票代码。

— blocked analysis, plus a doubled 「。」 from concatenating a message that already
ended with one.

The fallback is Tencent's smartbox endpoint, which answers over plain HTTPS:
``GET https://smartbox.gtimg.cn/s3/?q=亨通光电&t=all`` →
``v_hint="sh~600487~\\u4ea8\\u901a\\u5149\\u7535~htgd~GP-A"``.
"""

from __future__ import annotations

import pytest

from marvel.dataflows import a_stock


def _smartbox(payload: str):
    """A response object shaped like `requests`'s, carrying a smartbox payload."""

    class FakeResponse:
        text = payload
        status_code = 200

        def raise_for_status(self):
            return None

    return FakeResponse()


def _hint(*entries: str) -> str:
    return 'v_hint="' + "^".join(entries) + '"'


HENG = r"sh~600487~\u4ea8\u901a\u5149\u7535~htgd~GP-A"


@pytest.fixture
def mootdx_down(monkeypatch):
    """The user's environment: no Tongdaxin TCP, and no on-disk map cache."""
    def boom(*args, **kwargs):
        raise RuntimeError(
            "mootdx 通达信服务器暂不可用（300 秒内不再重试）。"
            "已尝试全部内置服务器：端口能连上的也没能完成通达信协议取数。"
        )

    monkeypatch.setattr(a_stock, "_mootdx_call", boom)
    monkeypatch.setattr(a_stock, "_load_name_map_from_disk", lambda: None)
    monkeypatch.setattr(a_stock, "_name_to_code", None)
    monkeypatch.setattr(a_stock, "_code_to_name", None)


@pytest.fixture
def map_available(monkeypatch):
    """A working in-memory name map (the mootdx path succeeded)."""
    monkeypatch.setattr(a_stock, "_name_to_code", {"贵州茅台": "600519"})
    monkeypatch.setattr(a_stock, "_code_to_name", {"600519": "贵州茅台"})
    return {"贵州茅台": "600519"}


@pytest.mark.unit
class TestNameResolvesWithoutMootdx:
    def test_exact_name_resolves_over_http(self, monkeypatch, mootdx_down):
        seen: dict = {}

        def fake_get(url, params=None, **kwargs):
            seen["url"] = url
            seen["q"] = (params or {}).get("q")
            return _smartbox(_hint(HENG))

        monkeypatch.setattr(a_stock._requests, "get", fake_get)

        assert a_stock.resolve_ticker("亨通光电") == "600487"
        assert seen["q"] == "亨通光电", "在线查询没有把用户输入传出去"

    def test_ambiguous_name_lists_the_candidates(self, monkeypatch, mootdx_down):
        payload = _hint(
            HENG, r"sh~600226~\u4ea8\u901a\u80a1\u4efd~htgf~GP-A"
        )
        monkeypatch.setattr(a_stock._requests, "get", lambda *a, **k: _smartbox(payload))

        with pytest.raises(ValueError) as excinfo:
            a_stock.resolve_ticker("亨通")

        message = str(excinfo.value)
        assert "600487" in message and "600226" in message

    def test_a_code_never_looks_anything_up(self, monkeypatch, mootdx_down):
        """Codes must keep working with no network at all."""
        def explode(*args, **kwargs):
            raise AssertionError("6 位代码不该触发任何查询")

        monkeypatch.setattr(a_stock._requests, "get", explode)

        assert a_stock.resolve_ticker("600487") == "600487"
        assert a_stock.resolve_ticker("SH600487") == "600487"

    def test_the_map_still_wins_when_it_is_available(
        self, monkeypatch, map_available
    ):
        """The HTTP path is a fallback, not a replacement."""
        def explode(*args, **kwargs):
            raise AssertionError("名称映射可用时不该走在线查询")

        monkeypatch.setattr(a_stock._requests, "get", explode)

        assert a_stock.resolve_ticker("贵州茅台") == "600519"

    def test_hk_and_us_rows_are_ignored(self, monkeypatch, map_available):
        """`t=all` returns other markets; only A-shares may be returned."""
        payload = _hint(
            r"hk~00700~\u817e\u8baf\u63a7\u80a1~txkg~GP-HK",
            r"us~AAPL~\u82f9\u679c~pg~GP-US",
        )
        monkeypatch.setattr(a_stock._requests, "get", lambda *a, **k: _smartbox(payload))

        with pytest.raises(ValueError, match="找不到股票"):
            a_stock.resolve_ticker("腾讯控股")


@pytest.mark.unit
class TestFailureMessageQuality:
    def test_no_doubled_full_stop_when_both_paths_fail(
        self, monkeypatch, mootdx_down
    ):
        """The reported cosmetic bug: '查询。。请稍后重试'."""
        def http_boom(*args, **kwargs):
            raise ConnectionError("proxy refused")

        monkeypatch.setattr(a_stock._requests, "get", http_boom)

        with pytest.raises(ValueError) as excinfo:
            a_stock.resolve_ticker("亨通光电")

        message = str(excinfo.value)
        assert "。。" not in message, f"标点重复：{message}"
        assert ".." not in message
        assert "600487" in message, "报错应当给出可操作的替代方案（6 位代码）"

    def test_the_map_error_itself_has_no_doubled_full_stop(self, mootdx_down):
        with pytest.raises(ValueError) as excinfo:
            a_stock._build_name_code_map()

        assert "。。" not in str(excinfo.value)

    def test_an_unknown_name_says_what_the_parameter_accepts(
        self, monkeypatch, map_available
    ):
        """With a working map, a non-stock word gets the explanatory message."""
        monkeypatch.setattr(
            a_stock._requests, "get", lambda *a, **k: _smartbox(_hint())
        )

        with pytest.raises(ValueError) as excinfo:
            a_stock.resolve_ticker("游戏")

        message = str(excinfo.value)
        assert "6 位股票代码" in message
        assert "行业/概念/板块名" in message

    def test_with_no_map_and_no_online_hit_it_still_says_what_to_do(
        self, monkeypatch, mootdx_down
    ):
        """The user's environment: both paths down. Must stay actionable."""
        monkeypatch.setattr(
            a_stock._requests, "get", lambda *a, **k: _smartbox(_hint())
        )

        with pytest.raises(ValueError) as excinfo:
            a_stock.resolve_ticker("亨通光电")

        message = str(excinfo.value)
        assert "6 位股票代码" in message
        assert "在线名称查询" in message


@pytest.mark.unit
class TestSmartboxParsing:
    def test_decodes_unicode_escapes_and_splits_rows(self):
        payload = _hint(
            HENG, r"sz~000001~\u5e73\u5b89\u94f6\u884c~payh~GP-A"
        )

        assert a_stock._fetch_name_candidates.__doc__  # documented shape
        import json

        text = payload
        start = text.find('v_hint="') + len('v_hint="')
        end = text.find('"', start)
        decoded = json.loads(f'"{text[start:end]}"')
        rows = [r.split("~") for r in decoded.split("^")]

        assert rows[0][1] == "600487" and rows[0][2] == "亨通光电"
        assert rows[1][1] == "000001" and rows[1][2] == "平安银行"

    def test_multiple_results_are_returned_in_order(self, monkeypatch):
        payload = _hint(HENG, r"sz~000001~\u5e73\u5b89\u94f6\u884c~payh~GP-A")
        monkeypatch.setattr(a_stock._requests, "get", lambda *a, **k: _smartbox(payload))

        candidates = a_stock._fetch_name_candidates("x")

        assert candidates == [("亨通光电", "600487"), ("平安银行", "000001")]

    def test_a_non_smartbox_body_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(
            a_stock._requests, "get", lambda *a, **k: _smartbox("<html>nope</html>")
        )

        assert a_stock._fetch_name_candidates("x") == []
