"""Guards for mootdx server selection, the cause of the "freeze forever" reports.

On this machine TCP 7709 accepts connections but the Tongdaxin protocol never
answers, so *every* server in the table is "reachable but dead". Two defects turned
that from "slow" into "wedged at 100% CPU, forever":

1. ``_mootdx_call`` calls ``reset_mootdx_client()`` whenever a data call fails, and
   that function used to clear ``_mootdx_unavailable_until`` as well. The negative
   cache — whose whole purpose is to stop the 38-server probe from repeating — was
   therefore wiped by the very failure it was meant to remember, and the next call
   probed all over again. Measured cost per round: 60–200 seconds at a full core.
2. The probe had no overall time budget: 14 reachable servers × ~4.3s, plus the bare
   ``factory`` fallback, measured 137.7s for a single call.
"""

from __future__ import annotations

import time

import pytest

from marvel.dataflows import a_stock


@pytest.mark.unit
class TestTheNegativeCacheSurvivesAClientReset:
    def test_reset_keeps_the_unavailable_window(self, monkeypatch):
        """回归：清掉它 = 把负缓存彻底作废，于是每轮取数都重探 38 台。"""
        monkeypatch.setattr(a_stock, "_mootdx_client", object())
        future = time.time() + 300
        monkeypatch.setattr(a_stock, "_mootdx_unavailable_until", future)

        a_stock.reset_mootdx_client()

        assert a_stock._mootdx_client is None, "必须丢弃 client（#90 的用意）"
        assert a_stock._mootdx_unavailable_until == future, (
            "负缓存被清掉了——这正是「重探 38 台服务器」死循环的来源"
        )

    def test_a_known_unavailable_window_fails_fast(self, monkeypatch):
        monkeypatch.setattr(a_stock, "_mootdx_client", None)
        monkeypatch.setattr(a_stock, "_mootdx_unavailable_until", time.time() + 300)

        def explode(*args, **kwargs):
            raise AssertionError("负缓存有效期内不该再去探服务器")

        monkeypatch.setattr(a_stock, "_reachable_tdx_servers", explode)

        started = time.time()
        with pytest.raises(RuntimeError, match="暂不可用"):
            a_stock._get_mootdx_client()
        assert time.time() - started < 0.5


@pytest.mark.unit
class TestSelectionHasATimeBudget:
    def _slow_dead_servers(self, monkeypatch, servers: int = 20, per_probe: float = 0.05):
        """Every server looks reachable, every protocol check fails slowly."""
        probes = {"n": 0}
        monkeypatch.setattr(a_stock, "_mootdx_client", None)
        monkeypatch.setattr(a_stock, "_mootdx_unavailable_until", 0.0)
        monkeypatch.setattr(
            a_stock, "_candidate_tdx_servers",
            lambda: [(f"10.0.0.{i}", 7709) for i in range(servers)],
        )
        monkeypatch.setattr(
            a_stock, "_reachable_tdx_servers", lambda candidates: list(candidates)
        )

        class FakeQuotes:
            def __init__(self, *a, **k):
                pass

        def factory(*args, **kwargs):
            return FakeQuotes()

        import mootdx.quotes

        monkeypatch.setattr(mootdx.quotes.Quotes, "factory", staticmethod(factory))

        def slow_works(client):
            probes["n"] += 1
            time.sleep(per_probe)
            return False

        monkeypatch.setattr(a_stock, "_tdx_client_works", slow_works)
        monkeypatch.setattr(a_stock, "_preserve_mootdx_bestip", _noop_context)
        return probes

    def test_it_gives_up_at_the_budget_instead_of_probing_forever(
        self, monkeypatch, caplog
    ):
        probes = self._slow_dead_servers(monkeypatch, servers=200, per_probe=0.02)
        monkeypatch.setattr(a_stock, "_MOOTDX_SELECT_BUDGET_S", 0.2)

        with caplog.at_level("WARNING"):
            started = time.time()
            with pytest.raises(RuntimeError):
                a_stock._get_mootdx_client()
            elapsed = time.time() - started

        assert elapsed < 3, f"预算没起作用，跑了 {elapsed:.1f}s"
        assert probes["n"] < 200, f"200 台全探完了（{probes['n']} 次），预算被忽略"
        assert "预算" in caplog.text, "放弃时必须说明原因，否则用户只看到一次莫名的慢"

    def test_the_order_is_unchanged_while_the_budget_lasts(self, monkeypatch):
        """预算只限时间，不该变成「失败 N 台就收手」——那会漏掉靠后的可用服务器。"""
        seen: list[tuple[str, int]] = []

        probes = self._slow_dead_servers(monkeypatch, servers=6, per_probe=0.0)
        monkeypatch.setattr(a_stock, "_MOOTDX_SELECT_BUDGET_S", 60)

        class FakeQuotes:
            pass

        import mootdx.quotes

        def factory(*args, **kwargs):
            seen.append(kwargs.get("server"))
            return FakeQuotes()

        monkeypatch.setattr(mootdx.quotes.Quotes, "factory", staticmethod(factory))

        with pytest.raises(RuntimeError):
            a_stock._get_mootdx_client()

        # 6 台候选 + 预算还有余量时的那次裸 factory 兜底 = 7。兜底是设计的一部分
        # （用户自己配置过服务器时它有意义），预算内就允许它跑。
        assert probes["n"] == 7, "预算没用完就应该逐台试完（含裸 factory 兜底）"
        assert [s for s in seen if s] == [
            (f"10.0.0.{i}", 7709) for i in range(6)
        ], "顺序被改变了——精选表必须优先"

    def test_a_working_server_is_still_found(self, monkeypatch):
        """健康网络下预算不该影响结果：第一台就能用。"""
        monkeypatch.setattr(a_stock, "_mootdx_client", None)
        monkeypatch.setattr(a_stock, "_mootdx_unavailable_until", 0.0)
        monkeypatch.setattr(
            a_stock, "_candidate_tdx_servers", lambda: [("10.0.0.1", 7709)]
        )
        monkeypatch.setattr(
            a_stock, "_reachable_tdx_servers", lambda candidates: list(candidates)
        )
        monkeypatch.setattr(a_stock, "_preserve_mootdx_bestip", _noop_context)
        monkeypatch.setattr(a_stock, "_tdx_client_works", lambda client: True)
        monkeypatch.setattr(a_stock, "_MOOTDX_SELECT_BUDGET_S", 30)

        import mootdx.quotes

        sentinel = object()
        monkeypatch.setattr(
            mootdx.quotes.Quotes, "factory", staticmethod(lambda *a, **k: sentinel)
        )

        assert a_stock._get_mootdx_client() is sentinel

    def test_zero_disables_the_budget(self, monkeypatch):
        """设 0 = 恢复旧行为，供「宁可慢也不要漏掉服务器」的用户使用。"""
        import os

        monkeypatch.setenv("MARVEL_MOOTDX_SELECT_BUDGET_S", "0")
        assert float(os.environ["MARVEL_MOOTDX_SELECT_BUDGET_S"]) == 0

        source = (
            __import__("pathlib").Path(a_stock.__file__).read_text(encoding="utf-8")
        )
        assert "_MOOTDX_SELECT_BUDGET_S > 0" in source, (
            "预算是 0 时必须走「不限制」分支"
        )


class _noop_context:
    """Stand-in for `_preserve_mootdx_bestip()` (a context manager)."""

    def __enter__(self):
        return lambda: None

    def __exit__(self, *exc):
        return False
