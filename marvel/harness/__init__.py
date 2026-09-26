"""A small DeepSeek-oriented agent harness for MARVEL.

Why this exists alongside the LangGraph pipeline rather than replacing it: the
pipeline is a *fixed* 14-stage graph, which is what you want for a repeatable
research report, and the wrong shape for asking "what would the model do with
this task and these tools?". This package answers the second question.

Shape (see https://github.com/deepseek-ai/deepseek-harness for the reference
design this borrows from — "everything is a plugin", thin core, capabilities as
composable units):

* :class:`~marvel.harness.registry.ToolRegistry` — tools defined once, schema and
  implementation together;
* :class:`~marvel.harness.loop.AgentLoop` — the model/tool round trip with an
  explicit step budget;
* :class:`~marvel.harness.recorder.RunRecorder` — every turn written to JSONL;
* :class:`~marvel.harness.models.ReplayModel` — feed a recording back through the
  loop, so the agent path can be re-run offline and deterministically.

The real DeepSeek client is not reimplemented: :func:`create_deepseek_model`
builds one through ``marvel.llm_clients``, which already handles the
thinking-mode round-trip.
"""

from __future__ import annotations

from .loop import DEFAULT_SYSTEM_PROMPT, AgentLoop
from .models import (
    LangChainToolCallingModel,
    ReplayModel,
    ScriptedModel,
    create_deepseek_model,
)
from .recorder import RECORDING_VERSION, RunRecorder, load_recording, recorded_replies
from .registry import ToolRegistry, ToolSpec
from .types import ModelReply, RunResult, Step, ToolCall, ToolResult

__all__ = [
    "AgentLoop",
    "DEFAULT_SYSTEM_PROMPT",
    "LangChainToolCallingModel",
    "ModelReply",
    "RECORDING_VERSION",
    "ReplayModel",
    "RunRecorder",
    "RunResult",
    "ScriptedModel",
    "Step",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "create_deepseek_model",
    "load_recording",
    "recorded_replies",
]
