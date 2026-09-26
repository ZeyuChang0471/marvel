"""The agent loop: model turn → tool calls → tool results → repeat.

Small on purpose. What it owns is the part that was previously implicit and
therefore untestable: **how many turns a run may take, what the model is allowed
to call, what happens when a tool fails, and what gets written down.** Those four
questions are where agent runs usually go wrong quietly, so each one is explicit
here rather than left to a framework default.

The analysis date is bound around tool execution (see ``dataflows/as_of.py``), so
a harness run inherits the same point-in-time guarantee as the LangGraph pipeline
instead of being a second, unguarded way into the data layer.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence

from marvel.dataflows.as_of import analysis_date_as_of

from .recorder import RunRecorder
from .registry import ToolRegistry
from .types import ModelReply, RunResult, Step

logger = logging.getLogger(__name__)

__all__ = ["AgentLoop", "DEFAULT_SYSTEM_PROMPT"]

DEFAULT_SYSTEM_PROMPT = (
    "You are a research agent working on A-share (China mainland) equities.\n"
    "Use the provided tools to gather evidence before answering; do not state "
    "numbers you did not retrieve.\n"
    "If a tool fails, say so explicitly instead of treating the failure as an "
    "absence of data.\n"
    "When you have enough evidence, answer in prose and stop calling tools."
)


class AgentLoop:
    """Drives one task to completion within a step budget."""

    def __init__(
        self,
        model: Any,
        registry: ToolRegistry,
        *,
        recorder: Optional[RunRecorder] = None,
        max_steps: int = 8,
        system_prompt: Optional[str] = None,
        analysis_date: Optional[str] = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.model = model
        self.registry = registry
        self.recorder = recorder
        self.max_steps = max_steps
        self.system_prompt = (
            DEFAULT_SYSTEM_PROMPT if system_prompt is None else system_prompt
        )
        self.analysis_date = analysis_date

    # --- public API ---

    def run(self, task: str, *, analysis_date: Optional[str] = None) -> RunResult:
        """Run one task. Never raises for a tool failure — only for a broken model."""
        run_date = analysis_date or self.analysis_date
        messages: List[Any] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": task})

        tools = self.registry.to_openai_tools()
        steps: List[Step] = []
        final = ""
        stopped_because = "step_budget"

        if self.recorder is not None:
            self.recorder.start(
                task=task, model=getattr(self.model, "name", "unknown"),
                analysis_date=run_date,
            )

        for index in range(self.max_steps):
            reply = self.model.complete(messages, tools)
            step = Step(index=index, reply=reply)

            if self.recorder is not None:
                self.recorder.record_reply(index, reply)

            if not reply.wants_tools:
                final = reply.content
                stopped_because = "final_answer"
                steps.append(step)
                break

            messages.append(_assistant_message(reply))
            for call in reply.tool_calls:
                # Tool execution is where retrieved data enters the run, so the
                # analysis date is authoritative for exactly this window.
                with analysis_date_as_of(run_date):
                    result = self.registry.dispatch(call)
                step.results.append(result)
                if self.recorder is not None:
                    self.recorder.record_tool_result(index, result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id or call.name,
                        "name": call.name,
                        "content": result.content,
                    }
                )
            steps.append(step)
        else:
            # Budget exhausted with the model still asking for tools. Saying so
            # is the point: a truncated run must not look like a finished one.
            logger.warning(
                "harness run hit the step budget (%d) while the model still "
                "wanted tools",
                self.max_steps,
            )
            final = steps[-1].reply.content if steps else ""

        result = RunResult(
            task=task,
            steps=steps,
            final=final,
            stopped_because=stopped_because,
            analysis_date=run_date,
        )
        if self.recorder is not None:
            self.recorder.finish(result)
        return result


def _assistant_message(reply: ModelReply) -> Any:
    """Thread the assistant turn back into the conversation.

    The provider's own message object is preferred over a reconstructed dict: for
    DeepSeek thinking models the ``reasoning_content`` field must survive into the
    next request, and only the SDK object carries it.
    """
    if reply.raw is not None:
        return reply.raw
    return {
        "role": "assistant",
        "content": reply.content,
        "tool_calls": [call.to_openai() for call in reply.tool_calls],
    }
