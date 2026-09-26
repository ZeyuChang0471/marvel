"""Guards for the container setup and the environment-driven config.

Two long-standing lies lived here:

* ``docker-compose.yml`` could not run the Streamlit UI it implied. The image's
  ``ENTRYPOINT`` is the CLI, there was no ``ports:`` mapping anywhere, and the
  README sent users to Docker for PDF export — so ``docker compose up`` produced
  a CLI prompt, never a UI.
* The ``ollama`` profile set ``LLM_PROVIDER=ollama``, a variable **nothing read**,
  so that profile quietly ran on the OpenAI default. It also relied on the ollama
  client's own ``http://localhost:11434/v1`` default, which inside a container
  points at the container itself rather than the ``ollama`` service.

And one more: ``BACKEND_URL`` was documented in ``.env.example`` and honoured by
the Web UI, but ``marvel/default_config.py`` never read it — so a CLI or
container run ignored the endpoint the user had configured.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to read compose files")


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _services() -> dict:
    return _compose()["services"]


@pytest.mark.unit
class TestComposeCanServeTheWebUI:
    def test_a_web_service_exists_and_runs_the_web_entrypoint(self):
        services = _services()
        web = [
            name for name, spec in services.items()
            if "marvel-web" in (spec.get("command") or [])
        ]
        assert web, "没有 service 运行 marvel-web —— compose 跑不起 README 宣传的 Web UI"

    def test_the_web_service_publishes_a_port(self):
        services = _services()
        web = next(
            spec for spec in services.values()
            if "marvel-web" in (spec.get("command") or [])
        )
        ports = web.get("ports") or []
        assert ports, "Web UI service 没有端口映射，宿主访问不到"

    def test_the_published_port_is_bound_to_loopback(self):
        """UI 无登录：宿主端口必须只绑回环。"""
        services = _services()
        web = next(
            spec for spec in services.values()
            if "marvel-web" in (spec.get("command") or [])
        )
        for mapping in web.get("ports") or []:
            assert str(mapping).startswith("127.0.0.1:"), (
                f"端口映射 {mapping!r} 不是回环绑定——等于把无鉴权 UI 公开到网络"
            )

    def test_the_web_service_binds_inside_the_container_explicitly(self):
        """容器内必须绑 0.0.0.0，否则 config.toml 的回环默认让端口映射失效。"""
        services = _services()
        web = next(
            spec for spec in services.values()
            if "marvel-web" in (spec.get("command") or [])
        )
        env = web.get("environment") or []
        assert any(
            str(item).startswith("STREAMLIT_SERVER_ADDRESS=0.0.0.0") for item in env
        ), "容器内没有显式绑 0.0.0.0，宿主端口映射会连不上"

    def test_the_ollama_profile_is_actually_wired(self):
        services = _services()
        ollama_svc = services.get("marvel-ollama")
        assert ollama_svc, "缺少 marvel-ollama service"
        env = [str(i) for i in (ollama_svc.get("environment") or [])]
        assert any(e.startswith("LLM_PROVIDER=ollama") for e in env)
        assert any(e.startswith("BACKEND_URL=") and "ollama" in e for e in env), (
            "ollama profile 没把 endpoint 指到 ollama service —— "
            "客户端会去调自己的 localhost 默认值"
        )

    def test_compose_is_valid_yaml(self):
        assert _services(), "docker-compose.yml 解析不出 services"


@pytest.mark.unit
class TestEnvironmentDrivenConfig:
    """环境变量必须真的被 default_config 读到。"""

    def _reload_default_config(self, monkeypatch, **env):
        import importlib

        for key, value in env.items():
            monkeypatch.setenv(key, value)
        import marvel.default_config as dc

        return importlib.reload(dc)

    def test_llm_provider_is_read_from_the_environment(self, monkeypatch):
        """compose 一直设 LLM_PROVIDER，却没有任何代码读它。"""
        dc = self._reload_default_config(monkeypatch, LLM_PROVIDER="ollama")
        try:
            assert dc.DEFAULT_CONFIG["llm_provider"] == "ollama"
        finally:
            import importlib

            monkeypatch.delenv("LLM_PROVIDER", raising=False)
            importlib.reload(dc)

    def test_backend_url_is_read_from_the_environment(self, monkeypatch):
        """`.env.example` 一直记录 BACKEND_URL，但只有 Web UI 读过它。"""
        dc = self._reload_default_config(
            monkeypatch, BACKEND_URL="http://ollama:11434/v1"
        )
        try:
            assert dc.DEFAULT_CONFIG["backend_url"] == "http://ollama:11434/v1"
        finally:
            import importlib

            monkeypatch.delenv("BACKEND_URL", raising=False)
            importlib.reload(dc)

    def test_empty_backend_url_becomes_none(self, monkeypatch):
        """空串必须归一成 None，否则会当成「显式指定了空端点」。"""
        dc = self._reload_default_config(monkeypatch, BACKEND_URL="")
        try:
            assert dc.DEFAULT_CONFIG["backend_url"] is None
        finally:
            import importlib

            monkeypatch.delenv("BACKEND_URL", raising=False)
            importlib.reload(dc)

    def test_env_example_documents_the_variables_we_read(self):
        """文档化的变量必须真的被读，读的也必须被文档化。"""
        example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        for var in ("BACKEND_URL",):
            assert var in example, f".env.example 没有记录 {var}"


@pytest.mark.unit
class TestGraphConfigIsActuallyWired:
    """配置项必须真的生效——`max_recur_limit` 曾经只是躺在配置里。

    `Propagator()` 使用的是它自己写死的 100，用户无论怎么设都改不动步数预算；
    九个分析师的常规运行就可能撞上上限，而 `GraphRecursionError` 会让整轮
    数分钟的分析作废（checkpoint 默认关闭）。
    """

    def _graph(self, monkeypatch, tmp_path, overrides: dict):
        from unittest.mock import MagicMock

        from marvel.default_config import DEFAULT_CONFIG
        from marvel.graph import trading_graph as tg

        config = DEFAULT_CONFIG.copy()
        config["data_cache_dir"] = str(tmp_path / "cache")
        config["results_dir"] = str(tmp_path / "logs")
        config.update(overrides)

        def fake_create_llm_client(provider, model, base_url=None, **kwargs):
            client = MagicMock()
            client.get_llm.return_value = MagicMock()
            return client

        monkeypatch.setattr(tg, "create_llm_client", fake_create_llm_client)
        return tg.MarvelGraph(config=config)

    def test_max_recur_limit_reaches_the_propagator(self, monkeypatch, tmp_path):
        graph = self._graph(monkeypatch, tmp_path, {"max_recur_limit": 321})
        assert graph.propagator.max_recur_limit == 321, (
            "max_recur_limit 没有传到 Propagator —— 配置项又变成摆设了"
        )

    def test_graph_args_use_that_limit(self, monkeypatch, tmp_path):
        graph = self._graph(monkeypatch, tmp_path, {"max_recur_limit": 321})
        args = graph.propagator.get_graph_args()
        assert args["config"]["recursion_limit"] == 321

    def test_the_default_budget_is_not_the_old_hundred(self, monkeypatch, tmp_path):
        """默认值要给九分析师留出余量。"""
        from marvel.default_config import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["max_recur_limit"] > 100, (
            "默认步数预算又回到 100 了——九个分析师很容易撞上"
        )
