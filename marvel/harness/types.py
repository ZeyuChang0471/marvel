"""Value types shared by the harness.

Deliberately plain dataclasses over provider SDK objects: the agent loop, the
recorder and the replay path must all be exercisable without a network call, and
a recording has to be readable years later without the SDK that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "ModelReply",
    "RunResult",
    "Step",
    "ToolCall",
    "ToolResult",
]


@dataclass
class ToolCall:
    """One tool invocation requested by the model."""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    id: str = ""

    def to_openai(self) -> dict:
        import json

        return {
            "id": self.id or self.name,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }

    @classmethod
    def from_openai(cls, raw: dict) -> "ToolCall":
        import json

        function = raw.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                # A model that emits malformed JSON must surface as a tool error,
                # not as a crash: the loop turns this into a message the model can
                # read and correct.
                arguments = {"__raw_arguments__": arguments}
        return cls(
            name=function.get("name", raw.get("name", "")),
            arguments=arguments or {},
            id=raw.get("id", ""),
        )


@dataclass
class ToolResult:
    """The outcome of one tool call, always as text the model can consume."""

    name: str
    content: str
    call_id: str = ""
    ok: bool = True
    error: Optional[str] = None

    @property
    def is_error(self) -> bool:
        return not self.ok


@dataclass
class ModelReply:
    """One model turn, including the reasoning text when the model exposes it."""

    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    reasoning: Optional[str] = None
    usage: Optional[dict] = None
    #: The provider's native assistant message, when there is one.
    #:
    #: It must be threaded back into the conversation **unchanged**: DeepSeek's
    #: thinking models reject the next request with HTTP 400 unless the
    #: ``reasoning_content`` field is echoed back on the assistant turn, and that
    #: field lives in the SDK message object, not in anything reconstructible
    #: from the text. Flattening the reply to a plain dict silently breaks
    #: thinking mode.
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class Step:
    """One model turn plus everything it caused."""

    index: int
    reply: ModelReply
    results: List[ToolResult] = field(default_factory=list)


@dataclass
class RunResult:
    """Everything a caller needs to judge a run without re-reading the log."""

    task: str
    steps: List[Step]
    final: str
    stopped_because: str  # "final_answer" | "step_budget"
    analysis_date: Optional[str] = None

    @property
    def tool_calls(self) -> List[ToolCall]:
        return [call for step in self.steps for call in step.reply.tool_calls]

    @property
    def tool_errors(self) -> List[ToolResult]:
        return [r for step in self.steps for r in step.results if r.is_error]

    @property
    def hit_step_budget(self) -> bool:
        return self.stopped_because == "step_budget"
