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
            return "response"

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
            return "response"

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
