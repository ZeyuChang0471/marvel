"""Tool registry: what the model may call, and what happens when it does.

The registry exists so a tool is defined **once** — with its schema and its
implementation together — instead of a JSON schema in one place and a dispatch
table in another, which is how tool surfaces drift out of sync with the code.

Schema derivation reuses the ``Annotated[str, "description"]`` style the MARVEL
tools already use, so the same callables serve both the LangGraph pipeline and
this harness without being rewritten.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, get_type_hints

from .types import ToolCall, ToolResult

logger = logging.getLogger(__name__)

__all__ = ["ToolRegistry", "ToolSpec"]

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


class ToolSpec:
    """A registered tool: name, description, JSON schema, implementation."""

    def __init__(
        self,
        name: str,
        func: Callable[..., Any],
        description: str = "",
        parameters: Optional[dict] = None,
    ) -> None:
        self.name = name
        self.func = func
        self.description = description or (inspect.getdoc(func) or "").strip()
        self.parameters = parameters or _schema_from_signature(func)

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def call(self, arguments: Dict[str, Any]) -> str:
        """Invoke the tool and render the result as text.

        LangChain tools are invoked through ``.invoke()`` rather than by calling
        them: a ``StructuredTool`` is a Runnable, not a function, and going
        through the Runnable also validates the arguments against the schema
        instead of letting a wrong call reach the implementation.
        """
        target = self.func
        invoke = getattr(target, "invoke", None)
        if callable(invoke) and not inspect.isfunction(target):
            result = invoke(arguments)
        else:
            result = target(**arguments)
        if result is None:
            return ""
        return result if isinstance(result, str) else str(result)


def _schema_from_signature(func: Callable[..., Any]) -> dict:
    """Build a JSON schema from a signature, honouring ``Annotated`` hints.

    Annotations are resolved with ``get_type_hints`` rather than read off the
    signature: in a module that uses ``from __future__ import annotations`` every
    annotation is a *string*, so ``Annotated[str, "…"]`` would arrive as text and
    the parameter would silently lose both its type and its description.
    """
    try:
        hints = get_type_hints(func, include_extras=True)
    except Exception:  # noqa: BLE001 — an unresolvable hint must not kill the tool
        logger.debug("could not resolve type hints for %s", getattr(func, "__name__", func))
        hints = {}

    signature = inspect.signature(func)
    properties: Dict[str, dict] = {}
    required: List[str] = []

    for name, parameter in signature.parameters.items():
        if name in ("self", "cls") or parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        properties[name] = _property_schema(
            hints.get(name, parameter.annotation), parameter
        )
        if parameter.default is inspect.Parameter.empty:
            required.append(name)

    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _property_schema(annotation: Any, parameter: inspect.Parameter) -> dict:
    description = None

    # Annotated[str, "what it means"] — the convention already used by the tools.
    if getattr(annotation, "__metadata__", None):
        metadata = annotation.__metadata__
        annotation = annotation.__origin__
        for entry in metadata:
            if isinstance(entry, str):
                description = entry
                break

    json_type = _TYPE_MAP.get(annotation, "string")
    schema: Dict[str, Any] = {"type": json_type}
    if description:
        schema["description"] = description
    if parameter.default is not inspect.Parameter.empty and parameter.default is not None:
        schema["default"] = parameter.default
    return schema


class ToolRegistry:
    """Name → tool, plus the dispatch that never raises at the model."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    # --- registration ---

    def register(
        self,
        func: Optional[Callable[..., Any]] = None,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        parameters: Optional[dict] = None,
    ):
        """Register a callable. Usable directly or as a decorator."""

        def _register(target: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or target.__name__
            if tool_name in self._tools:
                raise ValueError(f"tool {tool_name!r} is already registered")
            self._tools[tool_name] = ToolSpec(
                tool_name, target, description or "", parameters
            )
            return target

        return _register(func) if func is not None else _register

    @classmethod
    def from_tools(cls, tools: Iterable[Any]) -> "ToolRegistry":
        """Build a registry from LangChain tools (the MARVEL tool modules).

        ``args_schema`` is reused when present so the harness sees exactly the
        schema the pipeline shows the model — no second, drifting definition.
        """
        registry = cls()
        for tool in tools:
            tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", "")
            if not tool_name:
                raise ValueError(f"cannot determine a name for {tool!r}")
            schema = None
            args_schema = getattr(tool, "args_schema", None)
            if args_schema is not None and hasattr(args_schema, "model_json_schema"):
                schema = args_schema.model_json_schema()
            registry._tools[tool_name] = ToolSpec(
                tool_name,
                tool,
                (getattr(tool, "description", "") or "").strip(),
                schema,
            )
        return registry

    # --- use ---

    def names(self) -> List[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def to_openai_tools(self) -> List[dict]:
        return [self._tools[name].to_openai_tool() for name in self.names()]

    def dispatch(self, call: ToolCall) -> ToolResult:
        """Run one tool call, converting every failure into readable text.

        A tool error is information the model can act on; raising here would
        abort the run and lose the rest of the plan. The failure text is
        deliberately explicit about it being an error so the model does not read
        an empty string as "the data says nothing".
        """
        spec = self._tools.get(call.name)
        if spec is None:
            return ToolResult(
                name=call.name,
                call_id=call.id,
                ok=False,
                error=f"unknown tool {call.name!r}",
                content=(
                    f"ERROR: unknown tool {call.name!r}. "
                    f"Available tools: {', '.join(self.names()) or '(none)'}."
                ),
            )
        try:
            return ToolResult(
                name=call.name,
                call_id=call.id,
                content=spec.call(call.arguments),
            )
        except Exception as exc:  # noqa: BLE001 — the model must see the failure
            logger.warning("harness tool %s failed: %s", call.name, exc)
            return ToolResult(
                name=call.name,
                call_id=call.id,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                content=(
                    f"ERROR: {call.name} failed with {type(exc).__name__}: {exc}. "
                    "This is a tool failure, not a finding of 'no data'."
                ),
            )
