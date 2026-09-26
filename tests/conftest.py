"""Shared pytest fixtures.

Two guarantees the suite relies on, and which nothing used to enforce:

1. **No real API keys.** Every provider client is stubbed by the tests
   themselves; the placeholders below exist only to stop an accidentally
   constructed client from blocking on an interactive prompt. They used to be
   written as ``monkeypatch.setenv(var, os.environ.get(var, "placeholder"))``,
   which *preserved* a developer's real key — so the "dummy" fixture did the
   opposite of what its name promised, on exactly the machines where a mistake
   would cost money.

2. **No outbound network.** The suite is documented as offline. The repository
   used to ship root-level ``test_*.py`` files with live billed LLM calls at
   module level, which pytest executed during *collection*. A test that quietly
   starts calling an API is a bug, so any connection to a non-loopback address
   now fails loudly with an explanation.
"""

import ipaddress
import socket
import sys

import pytest


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


def pytest_runtest_logreport(report):
    """Emit a GitHub Actions annotation for each failure.

    A red build used to be opaque from the outside: downloading a job log needs
    repository admin rights, so all anyone could see was "Process completed with
    exit code 1". ``::error file=…,line=…::message`` lines are rendered by the
    runner as **annotations**, and annotations are readable with the public
    checks API — which is the difference between "CI is red" and "this test
    failed, on this line, for this reason".

    Written to ``sys.__stdout__`` on purpose: pytest captures stdout, and a
    captured annotation line never reaches the runner.
    """
    if report.when != "call" or not report.failed:
        return

    path, lineno, _ = report.location
    path = str(path).replace("\\", "/")
    lines = [line for line in (report.longreprtext or "").splitlines() if line.strip()]
    detail = lines[-1].strip().replace("`", "'")[:350] if lines else "failed"
    stream = sys.__stdout__ or sys.stdout
    print(
        f"::error file={path},line={lineno + 1}::{report.nodeid} — {detail}",
        file=stream,
        flush=True,
    )


_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "ZHIPU_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "MINIMAX_API_KEY",
)

_OFFLINE_MESSAGE = (
    "测试套件必须离线运行，但有一个测试尝试建立出站连接。"
    "请用 monkeypatch 打桩数据层（参考 tests/test_name_lookup.py、"
    "tests/test_lookahead_guard.py 的写法），不要依赖真实网络。"
)


def _is_loopback(address) -> bool:
    """True only for loopback TCP/UDP targets."""
    if not isinstance(address, tuple) or not address:
        return False          # AF_UNIX and friends: treat as non-local
    host = address[0]
    if not isinstance(host, str):
        return False
    if host in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _offline_network(monkeypatch):
    """Fail loudly on any outbound (non-loopback) connection attempt."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guard_connect(self, address):
        if not _is_loopback(address):
            raise AssertionError(f"{_OFFLINE_MESSAGE}（目标: {address!r}）")
        return real_connect(self, address)

    def guard_connect_ex(self, address):
        if not _is_loopback(address):
            raise AssertionError(f"{_OFFLINE_MESSAGE}（目标: {address!r}）")
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guard_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guard_connect_ex)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch):
    """Set every provider key to a placeholder, unconditionally."""
    for env_var in _API_KEY_ENV_VARS:
        monkeypatch.setenv(env_var, "placeholder")
