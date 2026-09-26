"""Model adapters.

The loop talks to a three-method protocol — ``complete(messages, tools)`` — so it
can be driven by a real DeepSeek model, by a scripted model in a test, or by a
recording from an earlier run. That last one is what makes counterfactual
questions answerable: replay the same model turns against a different tool
surface, a different set of inputs, or a different step budget.

The real client is **reused, not reimplemented**: ``marvel.llm_clients`` already
builds a DeepSeek model with the thinking-mode round-trip
(``DeepSeekChatOpenAI``), and a second HTTP client here would drift from it.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, List, Optional, Sequence

from .recorder import load_recording, recorded_replies
from .types import ModelReply, ToolCall

logger = logging.getLogger(__name__)

__all__ = [
    "LangChainToolCallingModel",
    "ReplayModel",
    "ScriptedModel",
    "create_deepseek_model",
]


class ScriptedModel:
    """Returns pre-set replies in order. For tests and deterministic runs."""

    name = "scripted"

    def __init__(self, replies: Sequence[ModelReply], *, name: str = "scripted") -> None:
        self._pending: List[ModelReply] = list(replies)
        self.name = name
        #: Every prompt the loop sent, so a test can assert on what the model saw.
        self.seen: List[dict] = []

    def complete(self, messages: Sequence[Any], tools: Optional[list] = None) -> ModelReply:
        self.seen.append({"messages": list(messages), "tools": tools})
        if not self._pending:
            raise AssertionError(
                "the scripted model ran out of replies — the loop asked for more "
                "turns than the script provides (check max_steps)"
            )
        return self._pending.pop(0)


class ReplayModel:
    """Replays the model turns captured in a recording."""

    def __init__(self, replies: Iterable[ModelReply], *, name: str = "replay") -> None:
        self._pending: List[ModelReply] = list(replies)
        self.name = name
        self.seen: List[dict] = []

    @classmethod
    def from_recording(cls, path) -> "ReplayModel":
        return cls(recorded_replies(load_recording(path)))

    @property
    def remaining(self) -> int:
        return len(self._pending)

    def complete(self, messages: Sequence[Any], tools: Optional[list] = None) -> ModelReply:
        self.seen.append({"messages": list(messages), "tools": tools})
        if not self._pending:
            raise AssertionError(
                "the recording ran out of model turns — the replayed run took "
                "more steps than the recorded one"
            )
        return self._pending.pop(0)


class LangChainToolCallingModel:
    """Adapts a LangChain chat model (DeepSeek included) to the loop protocol."""

    def __init__(self, llm: Any, *, name: Optional[str] = None) -> None:
        self._llm = llm
        self.name = name or getattr(llm, "model_name", None) or "langchain"

    def complete(self, messages: Sequence[Any], tools: Optional[list] = None) -> ModelReply:
        from marvel.llm_clients.base_client import normalize_content

        runner = self._llm.bind_tools(tools) if tools else self._llm
        response = runner.invoke(list(messages))

        reasoning = None
        additional = getattr(response, "additional_kwargs", None) or {}
        if isinstance(additional, dict):
            reasoning = additional.get("reasoning_content")

        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            metadata = getattr(response, "response_metadata", None) or {}
            usage = metadata.get("token_usage") if isinstance(metadata, dict) else None

        return ModelReply(
            content=normalize_content(response).content or "",
            tool_calls=[
                ToolCall(
                    name=call.get("name", ""),
                    arguments=call.get("args") or {},
                    id=call.get("id", ""),
                )
                for call in (getattr(response, "tool_calls", None) or [])
            ],
            reasoning=reasoning,
            usage=dict(usage) if isinstance(usage, dict) else usage,
            raw=response,
        )


def create_deepseek_model(
    model: Optional[str] = None,
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: Optional[float] = None,
    **kwargs,
) -> LangChainToolCallingModel:
    """Build a DeepSeek-backed model through the repo's existing client factory.

    Falls back to ``DEFAULT_CONFIG`` and the environment so a harness run uses
    the same provider settings as the pipeline.
    """
    from marvel.default_config import DEFAULT_CONFIG
    from marvel.llm_clients.factory import create_llm_client

    resolved_model = model or DEFAULT_CONFIG.get("quick_think_llm")
    resolved_base_url = base_url or os.getenv("BACKEND_URL") or None
    resolved_key = (
        api_key
        or os.getenv("DEEPSEEK_API_KEY")
        or DEFAULT_CONFIG.get("api_key")
    )
    if not resolved_key:
        raise RuntimeError(
            "no DeepSeek API key found: pass api_key=..., or set DEEPSEEK_API_KEY "
            "in the environment or .env"
        )

    client = create_llm_client(
        "deepseek",
        resolved_model,
        resolved_base_url,
        api_key=resolved_key,
        temperature=temperature,
        **kwargs,
    )
    return LangChainToolCallingModel(
        client.get_llm(), name=f"deepseek:{resolved_model}"
    )
