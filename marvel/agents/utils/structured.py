"""Shared helpers for invoking an agent with structured output and a graceful fallback.

The Portfolio Manager, Trader, and Research Manager all follow the same
canonical pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. If the structured call itself fails for any reason
   (malformed JSON from a weak model, transient provider issue), fall
   back to a plain ``llm.invoke`` so the pipeline never blocks.

Centralising the pattern here keeps the agent factories small and ensures
all three agents log the same warnings when fallback fires.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel

from marvel.llm_clients.base_client import normalize_content

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

#: Field descriptions as written in the schema module, captured on first use so
#: localization can be applied and *undone* — the schema classes are module-level
#: singletons, and the CLI lets the user pick a language per run.
_BASE_DESCRIPTIONS: dict[type, dict[str, str]] = {}


def localize_schema(schema: type[T], language: Optional[str] = None) -> type[T]:
    """Make a schema's field descriptions ask for the configured output language.

    The schema docstring in ``agents/schemas.py`` says it outright: *"Field
    descriptions double as the model's output instructions"*. They were written
    entirely in English, so a provider that supports structured output (OpenAI,
    DeepSeek, …) received English instructions for the three stages that produce
    the decision, and the single ``Write your entire response in Chinese``
    sentence appended to the prompt body was competing with them. That is why the
    plan, the trader note and the final decision came back in English.

    The English text is preserved as the base and the language clause is
    appended, so switching languages between runs cannot stack clauses.
    """
    descriptions = _BASE_DESCRIPTIONS.setdefault(
        schema, {name: (field.description or "") for name, field in schema.model_fields.items()}
    )

    if language is None:
        try:
            from marvel.dataflows.config import get_config

            language = get_config().get("output_language", "Chinese")
        except Exception:  # noqa: BLE001 — never block a run over a prompt nicety
            language = "Chinese"

    lang = str(language or "").strip()
    clause = "" if not lang or lang.lower() == "english" else f" Write this field in {lang}."

    for name, field in schema.model_fields.items():
        base = descriptions.get(name, field.description or "")
        field.description = base + clause
    return schema


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Optional[Any]:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    Logs a warning when the binding fails so the user understands the agent
    will use free-text generation for every call instead of one-shot fallback.
    """
    localize_schema(schema)
    try:
        return llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back to free-text generation",
            agent_name, exc,
        )
        return None


def invoke_structured_or_freetext(
    structured_llm: Optional[Any],
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
) -> str:
    """Run the structured call and render to markdown; fall back to free-text on any failure.

    ``prompt`` is whatever the underlying LLM accepts (a string for chat
    invocations, a list of message dicts for chat models that take that
    shape). The same value is forwarded to the free-text path so the
    fallback sees the same input the structured call did.
    """
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
            return render(result)
        except Exception as exc:
            logger.warning(
                "%s: structured-output invocation failed (%s); retrying once as free text",
                agent_name, exc,
            )

    response = plain_llm.invoke(prompt)
    # Providers that answer with typed content blocks (OpenAI Responses API,
    # Gemini 3) return ``content`` as a *list* here.  The four provider clients
    # already normalise this on their own invoke path, but this fallback builds
    # the string that becomes ``final_trade_decision`` — and ``parse_rating``
    # calls ``.splitlines()`` on it, so an unnormalised list crashed the run
    # only after the whole multi-agent pipeline had finished.
    return normalize_content(response).content
