"""Record every LLM call of a real run to JSONL — the missing half of the audit.

``DEBATE_POLARIZATION_AUDIT.md`` is explicit about what its experiment does *not*
answer: it drives the real graph with **scripted** models, so it measures the
architecture, never "how often does DeepSeek restate an unverified rumour?".
Answering that needs one paid run whose prompt/response pairs are kept.

``MarvelGraph(callbacks=[...])`` already forwards callback handlers to the LLM
constructors (``marvel/graph/trading_graph.py``), so a LangChain callback handler
is the injection point here: no proxy object, no second HTTP client, no change to
any node, and structured-output calls are captured too (they go through the same
chat model).

Offline replay of a recording is a separate concern — ``ReplayModel`` in
``marvel/harness/models.py`` handles that. This module is only the recorder.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from langchain_core.callbacks import BaseCallbackHandler

#: 单条 prompt / response 记录的最大字符数。九位分析师的提示词包含整份报告，
#: 不设上限的话一份录制就是几十 MB。
DEFAULT_MAX_CHARS = 20000


def _text_of(content: Any) -> str:
    """Flatten a LangChain message content (str, or a list of typed blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def _model_name(serialized: Any, llm_output: Any) -> str:
    for source in (llm_output or {}), (serialized or {}).get("kwargs") or {}:
        for key in ("model_name", "model", "model_id"):
            value = source.get(key)
            if value:
                return str(value)
    return ""


class JsonlLlmRecorder(BaseCallbackHandler):
    """Append one JSON object per LLM call to ``path``.

    Record shape::

        {"seq": 3, "ts": 1770000000.0, "kind": "chat", "model": "deepseek-v4-pro",
         "prompt": "...", "response": "...", "reasoning_content": "...",
         "truncated": false}

    Failures are recorded too (``"error"`` instead of ``"response"``): a run that
    dies halfway is exactly the run you want evidence for.
    """

    def __init__(self, path: Any, *, max_chars: int = DEFAULT_MAX_CHARS) -> None:
        super().__init__()
        self.path = Path(path)
        self.max_chars = max(0, int(max_chars))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0
        self._pending: dict = {}

    # -- internals ---------------------------------------------------------

    def _clip(self, text: str) -> tuple:
        value = str(text or "")
        if self.max_chars and len(value) > self.max_chars:
            return value[: self.max_chars], True
        return value, False

    def _write(self, record: dict) -> None:
        with self._lock:
            self._seq += 1
            record["seq"] = self._seq
            record.setdefault("ts", time.time())
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _format_messages(messages: Any) -> str:
        parts = []
        for message in messages or []:
            kind = getattr(message, "type", message.__class__.__name__)
            parts.append(f"[{kind}] {_text_of(getattr(message, 'content', message))}")
        return "\n\n".join(parts)

    # -- callbacks ---------------------------------------------------------

    def on_chat_model_start(self, serialized, messages, **kwargs):
        batch = messages[0] if messages else []
        self._pending[str(kwargs.get("run_id"))] = (
            self._format_messages(batch), "chat", _model_name(serialized, None)
        )

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._pending[str(kwargs.get("run_id"))] = (
            "\n\n".join(str(p) for p in (prompts or [])), "llm",
            _model_name(serialized, None),
        )

    def on_llm_end(self, response, **kwargs):
        prompt, kind, model = self._pending.pop(
            str(kwargs.get("run_id")), ("", "chat", "")
        )
        text, reasoning = "", ""
        tool_calls: list = []
        try:
            generation = response.generations[0][0]
            message = getattr(generation, "message", None)
            if message is not None:
                text = _text_of(getattr(message, "content", ""))
                extra = getattr(message, "additional_kwargs", None) or {}
                reasoning = _text_of(extra.get("reasoning_content"))
                # 结构化输出（RM / Trader / PM）与工具调用走的是 tool_calls：
                # DeepSeek 的 thinking 模型在这里 content 是空的，payload 全在 args。
                # 不记这一项，录制里那几个阶段就只剩「空响应」，等于白录。
                for call in getattr(message, "tool_calls", None) or []:
                    args = call.get("args") if isinstance(call, dict) else getattr(call, "args", None)
                    if not isinstance(args, str):
                        try:
                            args = json.dumps(args, ensure_ascii=False)
                        except (TypeError, ValueError):
                            args = str(args)
                    clipped_args, _ = self._clip(args)
                    tool_calls.append({
                        "name": (call.get("name") if isinstance(call, dict)
                                 else getattr(call, "name", "")),
                        "args": clipped_args,
                    })
            else:
                text = str(getattr(generation, "text", "") or "")
        except (AttributeError, IndexError, TypeError):
            text = ""

        clipped_prompt, truncated_prompt = self._clip(prompt)
        clipped_text, truncated_text = self._clip(text)
        clipped_reasoning, truncated_reasoning = self._clip(reasoning)
        self._write({
            "kind": kind,
            "model": model or _model_name(None, getattr(response, "llm_output", None)),
            "prompt": clipped_prompt,
            "response": clipped_text,
            "reasoning_content": clipped_reasoning,
            "tool_calls": tool_calls,
            "truncated": bool(truncated_prompt or truncated_text or truncated_reasoning),
        })

    def on_llm_error(self, error, **kwargs):
        prompt, kind, model = self._pending.pop(
            str(kwargs.get("run_id")), ("", "chat", "")
        )
        clipped_prompt, truncated = self._clip(prompt)
        self._write({
            "kind": kind,
            "model": model,
            "prompt": clipped_prompt,
            "response": "",
            "error": f"{type(error).__name__}: {error}",
            "truncated": truncated,
        })


def iter_records(path: Any) -> Iterator[dict]:
    """Yield the records of a recording, skipping malformed lines."""
    file_path = Path(path)
    if not file_path.exists():
        return
    with open(file_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
