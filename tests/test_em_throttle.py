"""Tests for the Eastmoney request throttle.

`README.md` and `CLAUDE.md` both promise that every ``eastmoney.com`` request
goes through `_em_get()` and is **serialised** (a minimum interval plus jitter)
so that a multi-agent batch run does not trip Eastmoney's per-IP rate limit and
get temporarily banned.

The implementation was a bare check-then-act: read the timestamp, sleep, make
the request, then write the timestamp. Two threads read the same stale value and
both proceeded, so the documented guarantee held only while nothing else was
running — exactly when it was not needed.
"""

from __future__ import annotations

import threading
import time

import pytest

from marvel.dataflows import a_stock


class _FakeResponse:
    """Minimal stand-in for a ``requests.Response``.

    `_em_get` inspects ``status_code`` to decide whether to retry, so the fake has
    to carry one — returning a bare sentinel would let a real bug (or a missing
    status check) pass unnoticed.
    """

    def __init__(self, status_code: int = 200, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}

    def json(self):
        return self._payload


@pytest.mark.unit
def test_em_get_serialises_concurrent_calls(monkeypatch):
    """并发调用不得同时进入请求段。"""
    state = {"active": 0, "max_active": 0}
    guard = threading.Lock()

    class FakeSession:
        def get(self, *args, **kwargs):
            with guard:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            time.sleep(0.05)          # 模拟网络往返，放大并发窗口
            with guard:
                state["active"] -= 1
            return _FakeResponse()

    monkeypatch.setattr(a_stock, "_EM_SESSION", FakeSession())
    monkeypatch.setattr(a_stock, "_EM_MIN_INTERVAL", 0.0)  # 隔离 sleep，只验锁

    threads = [
        threading.Thread(target=lambda: a_stock._em_get("https://push2.eastmoney.com/x"))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state["max_active"] == 1, (
        f"有 {state['max_active']} 个东财请求同时进行——节流没有串行，"
        "README/CLAUDE.md 承诺的防封保证不成立"
    )


@pytest.mark.unit
def test_em_get_enforces_the_minimum_interval(monkeypatch):
    """连续调用之间必须真的间隔 EM_MIN_INTERVAL。"""
    stamps: list[float] = []

    class FakeSession:
        def get(self, *args, **kwargs):
            stamps.append(time.monotonic())
            return _FakeResponse()

    monkeypatch.setattr(a_stock, "_EM_SESSION", FakeSession())
    monkeypatch.setattr(a_stock, "_EM_MIN_INTERVAL", 0.3)
    # 去掉随机抖动，让断言可判定
    monkeypatch.setattr(a_stock.random, "uniform", lambda a, b: 0.0)

    a_stock._em_get("https://push2.eastmoney.com/a")
    a_stock._em_get("https://push2.eastmoney.com/b")

    assert len(stamps) == 2
    assert stamps[1] - stamps[0] >= 0.3, (
        f"两次东财请求只隔了 {stamps[1] - stamps[0]:.3f}s，小于 EM_MIN_INTERVAL=0.3"
    )


class _FakeClock:
    """Stand-in for the ``time`` module inside ``a_stock``.

    Patches only the attribute on the module under test — monkeypatching
    ``time.sleep`` itself would stall the whole test process.
    """

    def __init__(self) -> None:
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def time(self) -> float:
        return time.time()


@pytest.mark.unit
class TestRetriesOnTransientFailures:
    """429/5xx/网络抖动要重试，重试仍失败要**抛出**。

    原先只发一次请求：调用方拿到 429 的响应体后 `.json()` 解析失败（或拿到空 data），
    最终在报告里显示成「未上龙虎榜」「行业数据获取为空」——把**接口失败**说成了
    **真的没有数据**，而这两种情况对模型的含义完全相反。
    """

    def _quiet(self, monkeypatch) -> _FakeClock:
        clock = _FakeClock()
        monkeypatch.setattr(a_stock, "time", clock)
        monkeypatch.setattr(a_stock, "_EM_MIN_INTERVAL", 0.0)
        monkeypatch.setattr(a_stock.random, "uniform", lambda a, b: 0.0)
        return clock

    def test_retries_429_then_succeeds(self, monkeypatch):
        self._quiet(monkeypatch)
        calls = []

        class FakeSession:
            def get(self, *args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    return _FakeResponse(429)
                return _FakeResponse(200, {"data": "ok"})

        monkeypatch.setattr(a_stock, "_EM_SESSION", FakeSession())

        response = a_stock._em_get("https://push2.eastmoney.com/x")

        assert response.status_code == 200
        assert len(calls) == 2, "429 没有被重试"

    def test_retries_5xx(self, monkeypatch):
        self._quiet(monkeypatch)
        calls = []

        class FakeSession:
            def get(self, *args, **kwargs):
                calls.append(1)
                return _FakeResponse(503)

        monkeypatch.setattr(a_stock, "_EM_SESSION", FakeSession())

        with pytest.raises(a_stock._requests.HTTPError):
            a_stock._em_get("https://push2.eastmoney.com/x")

        assert len(calls) == a_stock._EM_MAX_RETRIES + 1, (
            f"5xx 只尝试了 {len(calls)} 次，应为 {a_stock._EM_MAX_RETRIES + 1} 次"
        )

    def test_4xx_other_than_429_is_not_retried(self, monkeypatch):
        """404 之类的错误重试没有意义，也不该被当成"没有数据"。"""
        self._quiet(monkeypatch)
        calls = []

        class FakeSession:
            def get(self, *args, **kwargs):
                calls.append(1)
                return _FakeResponse(404)

        monkeypatch.setattr(a_stock, "_EM_SESSION", FakeSession())

        response = a_stock._em_get("https://push2.eastmoney.com/x")

        assert response.status_code == 404
        assert len(calls) == 1, "404 不该重试"

    def test_network_errors_are_retried_then_raised(self, monkeypatch):
        self._quiet(monkeypatch)
        calls = []

        class FakeSession:
            def get(self, *args, **kwargs):
                calls.append(1)
                raise a_stock._requests.ConnectionError("connection reset")

        monkeypatch.setattr(a_stock, "_EM_SESSION", FakeSession())

        with pytest.raises(a_stock._requests.ConnectionError):
            a_stock._em_get("https://push2.eastmoney.com/x")

        assert len(calls) == a_stock._EM_MAX_RETRIES + 1

    def test_backoff_grows_exponentially(self, monkeypatch):
        clock = self._quiet(monkeypatch)

        class FakeSession:
            def get(self, *args, **kwargs):
                return _FakeResponse(502)

        monkeypatch.setattr(a_stock, "_EM_SESSION", FakeSession())

        with pytest.raises(a_stock._requests.HTTPError):
            a_stock._em_get("https://push2.eastmoney.com/x")

        # EM_MIN_INTERVAL=0 时不会有节流等待，所以 clock.slept 里只该有退避。
        assert clock.slept == [
            a_stock._EM_RETRY_BASE_S * (2**n)
            for n in range(a_stock._EM_MAX_RETRIES)
        ], f"退避序列不对：{clock.slept}"


@pytest.mark.unit
def test_em_get_callers_report_failures_as_failures(monkeypatch):
    """接口失败必须走到调用方的 except 分支（输出「查询失败」而不是「没有数据」）。"""
    clock = _FakeClock()
    monkeypatch.setattr(a_stock, "time", clock)
    monkeypatch.setattr(a_stock, "_EM_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(a_stock.random, "uniform", lambda a, b: 0.0)

    class FakeSession:
        def get(self, *args, **kwargs):
            return _FakeResponse(503)

    monkeypatch.setattr(a_stock, "_EM_SESSION", FakeSession())

    today = a_stock._market_today().strftime("%Y-%m-%d")
    text = a_stock.get_industry_comparison("600519", today)

    assert "行业对比查询失败" in text
    assert "行业数据获取为空" not in text
