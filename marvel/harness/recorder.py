"""Recording and replaying runs.

Two jobs, both about not spending tokens twice:

* **record** — append every model turn and tool result to a JSONL file, so a run
  that cost real money can be inspected, diffed and re-analysed later;
* **replay** — feed those recorded model turns back into the loop, so the whole
  agent path (prompt assembly, tool dispatch, message threading, step budget) can
  be re-run offline and deterministically.

That second job is what makes the harness a measurement instrument: it is how a
multi-agent debate can be replayed against a fixed set of inputs to see what the
pipeline does with them, without the run drifting because the model sampled a
different sentence.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .types import ModelReply, RunResult, ToolCall, ToolResult

logger = logging.getLogger(__name__)

__all__ = ["RunRecorder", "load_recording", "reply_from_record"]

#: Bumped when the on-disk shape changes, so an old recording is rejected loudly
#: instead of being misread as a current one.
RECORDING_VERSION = 1


def _clean(value: Any) -> Any:
    """Make a value JSON-serialisable without losing the fact that it existed."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return repr(value)


class RunRecorder:
    """Append-only JSONL writer for one run."""

    def __init__(self, path: str | os.PathLike, *, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self._events: List[dict] = []

    # --- lifetime ---

    def start(self, *, task: str, model: str, analysis_date: Optional[str]) -> None:
        self.write(
            {
                "type": "run_start",
                "task": task,
                "model": model,
                "analysis_date": analysis_date,
                "at": time.time(),
            }
        )

    def finish(self, result: RunResult) -> None:
        self.write(
            {
                "type": "run_end",
                "stopped_because": result.stopped_because,
                "final": result.final,
                "steps": len(result.steps),
                "at": time.time(),
            }
        )

    # --- events ---

    def write(self, event: Dict[str, Any]) -> None:
        event = {"version": RECORDING_VERSION, **_clean(event)}
        self._events.append(event)
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def record_reply(self, step: int, reply: ModelReply) -> None:
        self.write(
            {
                "type": "model_reply",
                "step": step,
                "content": reply.content,
                "reasoning": reply.reasoning,
                "tool_calls": [
                    {"name": c.name, "arguments": c.arguments, "id": c.id}
                    for c in reply.tool_calls
                ],
                "usage": reply.usage,
            }
        )

    def record_tool_result(self, step: int, result: ToolResult) -> None:
        self.write(
            {
                "type": "tool_result",
                "step": step,
                "name": result.name,
                "call_id": result.call_id,
                "ok": result.ok,
                "error": result.error,
                "content": result.content,
            }
        )

    def events(self) -> List[dict]:
        """Events written so far, whether or not they reached the disk."""
        return list(self._events)


def load_recording(path: str | os.PathLike) -> List[dict]:
    """Read a recording, rejecting one written by an incompatible version."""
    events: List[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number} is not valid JSON — the recording is "
                    f"truncated or corrupt ({exc})"
                ) from exc
            version = event.get("version")
            if version != RECORDING_VERSION:
                raise ValueError(
                    f"{path}:{line_number} has recording version {version!r}, "
                    f"expected {RECORDING_VERSION}"
                )
            events.append(event)
    return events


def reply_from_record(event: Dict[str, Any]) -> ModelReply:
    """Rebuild the model turn stored in a ``model_reply`` event."""
    return ModelReply(
        content=event.get("content", "") or "",
        reasoning=event.get("reasoning"),
        usage=event.get("usage"),
        tool_calls=[
            ToolCall(
                name=call.get("name", ""),
                arguments=call.get("arguments") or {},
                id=call.get("id", ""),
            )
            for call in event.get("tool_calls") or []
        ],
    )


def recorded_replies(events: Iterable[dict]) -> List[ModelReply]:
    return [
        reply_from_record(event)
        for event in events
        if event.get("type") == "model_reply"
    ]
