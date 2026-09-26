"""Guards for the Web UI's two credential hazards.

``web/`` has no authentication and Streamlit serves every visitor from one
process. Two things followed from that and are pinned here:

1. **The listening address.** ``.streamlit/config.toml`` never set
   ``server.address``, and Streamlit's default for an unset address is *all
   interfaces* — so ``start.sh`` / ``start.bat`` / ``marvel-web`` / ``run.py web``
   all published an unauthenticated UI to the network, where anyone who could
   reach the port could run paid analyses on the host's keys.

2. **Where the key lives.** The sidebar wrote the typed key into
   ``os.environ`` (process-wide) and into the shared project ``.env``, and an
   empty field *deleted* the operator's key from ``.env`` for every session.
   Keys are now held per browser session and reach the client through the
   per-run config — which the four provider clients already prefer over the
   environment — so one session cannot see or spend another's key.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

try:  # tomllib is stdlib only from 3.11 (PEP 680)
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


def _read(*parts: str) -> str:
    return (REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Source with comments removed.

    These guards explain the hazard they prevent, and the explanation naturally
    quotes the very idiom being banned (``os.environ[env_var] = value``). Without
    stripping comments the test matches its own documentation.
    """
    import io
    import tokenize

    lines = src.splitlines()
    for token in tokenize.generate_tokens(io.StringIO(src).readline):
        if token.type == tokenize.COMMENT:
            row, col = token.start
            lines[row - 1] = lines[row - 1][:col]
    return "\n".join(lines)


@pytest.mark.unit
class TestWebServerBinding:
    def _server_config(self) -> dict:
        return tomllib.loads(_read(".streamlit", "config.toml")).get("server", {})

    def test_server_address_is_set_and_loopback(self):
        address = self._server_config().get("address")
        assert address, (
            "未设置 [server].address —— Streamlit 在未设置时绑定 0.0.0.0，"
            "等于把一个无鉴权的 UI 公开发布到整个网络"
        )
        assert ipaddress.ip_address(address).is_loopback, (
            f"server.address = {address!r} 不是回环地址"
        )

    def test_cors_and_xsrf_are_not_disabled(self):
        server = self._server_config()
        for key in ("enableCORS", "enableXsrfProtection"):
            assert server.get(key, True) is not False, f"{key} 被显式关闭了"

    def test_no_launch_path_binds_beyond_loopback(self):
        """No launch path may bind a non-loopback address on its own.

        `web/launch.py` legitimately *states* the address (Streamlit only reads
        `.streamlit/config.toml` relative to the CWD and the script directory, so
        relying on the file alone meant `marvel-web` from any other directory fell
        back to Streamlit's 0.0.0.0 default). What must never happen is a launch
        path naming a non-loopback address as its own default.
        """
        import re

        for name in ("start.sh", "start.bat", "run.py", "web/launch.py"):
            src = _read(*name.split("/"))
            for line in src.splitlines():
                if "--server.address" not in line:
                    continue
                if line.lstrip().startswith("#"):
                    continue
                assert not re.search(r"--server\.address=(?!\{)[^\"']*", line) or (
                    "127.0.0.1" in line or "STREAMLIT_SERVER_ADDRESS" in line
                ), f"{name} 把绑定地址改成非回环: {line.strip()}"

    def test_the_launcher_pins_loopback_explicitly(self):
        """Relying on the config file alone was the security bug, not the fix."""
        src = _read("web", "launch.py")

        assert "DEFAULT_ADDRESS = \"127.0.0.1\"" in src, (
            "web/launch.py 不再显式绑定回环——从非仓库根目录启动会退回 0.0.0.0"
        )
        assert "STREAMLIT_SERVER_ADDRESS" in src, (
            "容器需要用这个环境变量覆盖成 0.0.0.0，不能把它写死"
        )
        assert "--server.address=" in src


@pytest.mark.unit
class TestApiKeysAreSessionScoped:
    def test_sidebar_never_mutates_the_process_environment(self):
        src = _strip_comments(_read("web", "components", "sidebar.py"))
        assert "os.environ[" not in src, (
            "侧边栏又往 os.environ 写 key 了——那是进程级的，所有会话共享"
        )
        assert "os.environ.pop" not in src, (
            "侧边栏又删除 os.environ 里的 key 了——会影响所有会话"
        )

    def test_sidebar_never_writes_the_env_file(self):
        src = _strip_comments(_read("web", "components", "sidebar.py"))
        for forbidden in ("_ENV_PATH", "_persist_env_var", "write_text"):
            assert forbidden not in src, (
                f"侧边栏又出现了 {forbidden} —— 请求路径不得改共享的 .env"
            )

    def test_there_is_a_session_scoped_accessor(self):
        src = _read("web", "components", "sidebar.py")
        assert "def session_api_key(" in src
        assert "def effective_api_key(" in src

    def test_build_config_carries_the_session_key(self):
        src = _read("web", "app.py")
        assert "session_api_key(" in src, "_build_config 没有取本会话的 key"
        assert 'config["api_key"]' in src, "_build_config 没有把 key 放进 config"


@pytest.mark.unit
class TestGraphHonoursThePerRunKey:
    """config['api_key'] 必须真的传到 create_llm_client。

    否则 Web UI 的「按会话 key」只是个摆设，实际仍会去读环境变量——也就是
    退回共享 key 的老行为。
    """

    def _config(self, tmp_path: Path, api_key) -> dict:
        from marvel.default_config import DEFAULT_CONFIG

        config = DEFAULT_CONFIG.copy()
        config["data_cache_dir"] = str(tmp_path / "cache")
        config["results_dir"] = str(tmp_path / "logs")
        config["api_key"] = api_key
        return config

    def _make_graph(self, monkeypatch, tmp_path, api_key):
        from marvel.graph import trading_graph as tg

        captured: list[dict] = []

        def fake_create_llm_client(provider, model, base_url=None, **kwargs):
            captured.append({"provider": provider, "model": model, **kwargs})
            client = MagicMock()
            client.get_llm.return_value = MagicMock()
            return client

        monkeypatch.setattr(tg, "create_llm_client", fake_create_llm_client)
        tg.MarvelGraph(config=self._config(tmp_path, api_key))
        return captured

    def test_key_from_config_reaches_the_client_factory(self, monkeypatch, tmp_path):
        captured = self._make_graph(monkeypatch, tmp_path, "sk-session-key")

        assert captured, "create_llm_client 没有被调用"
        for call in captured:
            assert call.get("api_key") == "sk-session-key", (
                f"{call['model']} 没收到 per-run api_key：{call}"
            )

    def test_absent_key_does_not_inject_an_empty_one(self, monkeypatch, tmp_path):
        """没有 key 时必须让 client 自己回退到环境变量，不能传空串盖掉它。"""
        captured = self._make_graph(monkeypatch, tmp_path, None)

        for call in captured:
            assert "api_key" not in call, (
                "config 里没有 key 却传了 api_key，会盖掉 .env 里的运维配置"
            )

    def test_blank_key_is_treated_as_absent(self, monkeypatch, tmp_path):
        captured = self._make_graph(monkeypatch, tmp_path, "   ")

        for call in captured:
            assert "api_key" not in call
