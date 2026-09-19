"""Tests for the LLM client factory — the provider dispatch table.

This module had **no test at all**, which is how a branch importing a
nonexistent module (`marvel.llm_clients.claude_agent_sdk_client`) survived: the
only way to discover it was to pass that provider and get a
`ModuleNotFoundError` instead of the factory's own `ValueError`.

The tests construct clients but never call `get_llm()`, so they need no API keys
and make no network calls (the suite-wide guard in ``tests/conftest.py`` would
reject one anyway).
"""

from __future__ import annotations

import pytest

from marvel.llm_clients.base_client import BaseLLMClient
from marvel.llm_clients.factory import _OPENAI_COMPATIBLE, create_llm_client

# Every provider the factory claims to support, with a representative model.
# Kept as an explicit list (rather than derived) so that *adding* a provider
# without testing it here is a visible omission.
ADVERTISED_PROVIDERS = (
    "openai",
    "xai",
    "deepseek",
    "qwen",
    "glm",
    "ollama",
    "openrouter",
    "minimax",
    "openai_compatible",
    "anthropic",
    "google",
    "azure",
)


# Providers that work with the core dependency set.
CORE_PROVIDERS = tuple(p for p in ADVERTISED_PROVIDERS if p != "google")


@pytest.mark.unit
class TestFactoryDispatch:
    @pytest.mark.parametrize("provider", CORE_PROVIDERS)
    def test_every_advertised_provider_returns_a_client(self, provider):
        client = create_llm_client(provider, "test-model")
        assert isinstance(client, BaseLLMClient), (
            f"{provider} 没有返回 BaseLLMClient：{type(client)}"
        )
        assert callable(getattr(client, "get_llm", None))
        assert callable(getattr(client, "validate_model", None))

    def test_google_is_either_available_or_fails_with_an_actionable_message(self):
        """Gemini 是可选的，且与 mootdx 的 httpx 约束冲突（issue #87）。

        所以这里接受两种结果，但**不接受**一个光秃秃的 ModuleNotFoundError：
        用户必须能看到该装什么。
        """
        try:
            client = create_llm_client("google", "gemini-2.5-flash")
        except ImportError as exc:
            message = str(exc)
            assert "httpx" in message, "错误信息没说清冲突在 httpx 上"
            assert "pip install" in message, "错误信息没给出可执行的安装命令"
        else:
            assert isinstance(client, BaseLLMClient)

    def test_provider_names_are_case_insensitive(self):
        assert isinstance(create_llm_client("DeepSeek", "m"), BaseLLMClient)
        assert isinstance(create_llm_client("ANTHROPIC", "m"), BaseLLMClient)

    def test_unknown_provider_raises_value_error_not_import_error(self):
        """未知 provider 必须是 ValueError。

        漏掉的分支曾让它变成 ModuleNotFoundError——一个「不支持的 provider」
        被报成了「本仓库坏了」，排查方向完全不同。
        """
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            create_llm_client("claude_agent_sdk", "m")

        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            create_llm_client("definitely-not-a-provider", "m")

    def test_every_module_in_llm_clients_is_reachable_from_the_factory(self):
        """反向检查：包里的 client 模块都应该有对应 provider，避免留死代码。

        `claude_agent_sdk_client.py` 曾经只在 factory 里被引用而文件根本不存在；
        反过来，一个存在却没人能选到的 client 模块同样是状态不一致。
        """
        from pathlib import Path

        import marvel.llm_clients as pkg

        pkg_dir = Path(pkg.__file__).parent
        client_modules = {
            p.stem.removesuffix("_client")
            for p in pkg_dir.glob("*_client.py")
            if p.stem not in {"base_client"}
        }
        known = set(_OPENAI_COMPATIBLE) | {"anthropic", "google", "azure"}

        orphan_modules = sorted(client_modules - known)
        assert not orphan_modules, (
            f"这些 client 模块没有任何 provider 能路由到它们: {orphan_modules}"
        )
