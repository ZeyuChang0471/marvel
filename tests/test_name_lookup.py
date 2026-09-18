"""Tests for stock-name lookup.

The display path used to resolve a code to a name by building the *whole*
full-market name↔code map over mootdx/TCP. When the Tongdaxin port is
unreachable that map takes about 80 seconds to build — 38 candidates are TCP
screened, 14 pass, and each of those then waits out mootdx's own timeout one
after another. Because resolve_stock_name() runs on every report render, the
results page hung for over a minute.

Two things are pinned here:
  * the code→name display path never builds the full market map, and
  * the full map (still needed for name→code) is served from a disk cache.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from marvel.dataflows import a_stock as A


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Point the on-disk caches at a temp dir and reset module state."""
    import marvel.dataflows.config as cfg

    monkeypatch.setattr(cfg, "get_config", lambda: {"data_cache_dir": str(tmp_path)})
    monkeypatch.setattr(A, "_name_to_code", None)
    monkeypatch.setattr(A, "_code_to_name", None)
    yield tmp_path
    A._name_to_code = None
    A._code_to_name = None


def _fake_tencent(names: dict[str, str]):
    def _quote(codes):
        return {c: {"name": names.get(c, "")} for c in codes}

    return _quote


class TestGetStockName:
    @pytest.mark.unit
    def test_resolves_a_code_to_its_name(self, monkeypatch):
        monkeypatch.setattr(A, "_tencent_quote", _fake_tencent({"600519": "贵州茅台"}))
        assert A.get_stock_name("600519") == "贵州茅台"

    @pytest.mark.unit
    def test_strips_the_padding_tencent_sends(self, monkeypatch):
        """Tencent pads some names; the map builder normalises the same way, so
        the label must not change depending on how the name was resolved."""
        monkeypatch.setattr(A, "_tencent_quote", _fake_tencent({"000858": "五 粮 液"}))
        assert A.get_stock_name("000858") == "五粮液"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "raw", ["600519", "sh600519", "SH600519", "600519.SH", "600519.sh", " 600519 "]
    )
    def test_accepts_prefixes_and_suffixes(self, monkeypatch, raw):
        monkeypatch.setattr(A, "_tencent_quote", _fake_tencent({"600519": "贵州茅台"}))
        assert A.get_stock_name(raw) == "贵州茅台"

    @pytest.mark.unit
    @pytest.mark.parametrize("raw", ["AAPL", "00700.HK", "", "60051", "6005199", "abc"])
    def test_rejects_non_a_share_input(self, monkeypatch, raw):
        called = []
        monkeypatch.setattr(
            A, "_tencent_quote", lambda codes: called.append(codes) or {}
        )
        assert A.get_stock_name(raw) is None
        assert not called, "must not hit the network for a non-A-share input"

    @pytest.mark.unit
    def test_returns_none_when_the_quote_fails(self, monkeypatch):
        def _boom(codes):
            raise OSError("network down")

        monkeypatch.setattr(A, "_tencent_quote", _boom)
        assert A.get_stock_name("600519") is None

    @pytest.mark.unit
    def test_returns_none_when_the_name_is_blank(self, monkeypatch):
        monkeypatch.setattr(A, "_tencent_quote", _fake_tencent({"600519": "  "}))
        assert A.get_stock_name("600519") is None


class TestDisplayPathDoesNotBuildTheFullMap:
    """The regression that caused the hang."""

    @pytest.mark.unit
    def test_resolve_stock_name_never_builds_the_market_map(self, monkeypatch):
        import web.stock_display as sd

        calls = []
        monkeypatch.setattr(A, "_build_name_code_map", lambda: calls.append(1) or ({}, {}))
        monkeypatch.setattr(A, "_tencent_quote", _fake_tencent({"600519": "贵州茅台"}))
        sd.resolve_stock_name.cache_clear()

        assert sd.resolve_stock_name("600519") == "贵州茅台"
        assert calls == [], "display path must not build the full market map"

    @pytest.mark.unit
    def test_resolve_stock_name_degrades_to_none(self, monkeypatch):
        import web.stock_display as sd

        def _boom(code):
            raise OSError("network down")

        monkeypatch.setattr(A, "get_stock_name", _boom)
        sd.resolve_stock_name.cache_clear()
        assert sd.resolve_stock_name("600519") is None

    @pytest.mark.unit
    def test_no_longer_imports_the_map_builder(self):
        """The docstring is allowed to mention it; an import is not."""
        import ast

        path = Path(__file__).resolve().parent.parent / "web" / "stock_display.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names
        }
        assert "_build_name_code_map" not in imported


class TestNameMapDiskCache:
    @pytest.mark.unit
    def test_round_trips_through_disk(self, tmp_path):
        A._save_name_map_to_disk({"贵州茅台": "600519"}, {"600519": "贵州茅台"})
        assert A._name_map_cache_path() == str(tmp_path / A._NAME_MAP_CACHE_FILE)

        A._name_to_code = None
        A._code_to_name = None
        n2c, c2n = A._build_name_code_map()
        assert n2c == {"贵州茅台": "600519"}
        assert c2n == {"600519": "贵州茅台"}

    @pytest.mark.unit
    def test_cache_is_used_instead_of_mootdx(self, monkeypatch):
        """mootdx is made to explode; a successful build can only be the cache."""
        A._save_name_map_to_disk({"五粮液": "000858"}, {"000858": "五粮液"})

        def _boom(*args, **kwargs):
            raise RuntimeError("mootdx unreachable")

        monkeypatch.setattr(A, "_mootdx_call", _boom)
        n2c, _ = A._build_name_code_map()
        assert n2c == {"五粮液": "000858"}

    @pytest.mark.unit
    def test_stale_cache_is_ignored(self, tmp_path):
        A._save_name_map_to_disk({"贵州茅台": "600519"}, {"600519": "贵州茅台"})
        path = Path(A._name_map_cache_path())
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["built_at"] = time.time() - (A._NAME_MAP_CACHE_TTL_S + 60)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        assert A._load_name_map_from_disk() is None

    @pytest.mark.unit
    def test_corrupt_cache_is_ignored(self):
        Path(A._name_map_cache_path()).write_text("{not json", encoding="utf-8")
        assert A._load_name_map_from_disk() is None

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "payload",
        [
            {"built_at": time.time(), "name_to_code": {}, "code_to_name": {}},
            {"built_at": time.time(), "name_to_code": "nope", "code_to_name": {}},
            {"built_at": time.time(), "name_to_code": {"a": "1"}},
            {"built_at": "not-a-number", "name_to_code": {"a": "1"}, "code_to_name": {"1": "a"}},
        ],
    )
    def test_malformed_shapes_are_rejected(self, payload):
        Path(A._name_map_cache_path()).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        assert A._load_name_map_from_disk() is None

    @pytest.mark.unit
    def test_a_write_failure_never_raises(self, monkeypatch):
        """A read-only cache directory must not break a successful lookup."""
        monkeypatch.setattr(
            A, "_name_map_cache_path", lambda: str(Path("\0invalid") / "x.json")
        )
        A._save_name_map_to_disk({"a": "1"}, {"1": "a"})  # must not raise

    @pytest.mark.unit
    def test_fresh_cache_expires_after_the_ttl(self):
        A._save_name_map_to_disk({"贵州茅台": "600519"}, {"600519": "贵州茅台"})
        assert A._load_name_map_from_disk() is not None
        assert A._NAME_MAP_CACHE_TTL_S == 24 * 3600
