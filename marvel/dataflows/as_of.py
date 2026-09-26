"""The run's analysis date, and the clamps that depend on it.

MARVEL retrieves data through tools whose date arguments are written by the
**model**. Most of them take a ``curr_date`` (or a ``start_date``/``end_date``
window), and the data layer trusted whatever arrived. That made the analysis
date advisory rather than authoritative:

* ``get_news(ticker, start_date, end_date)`` did not even take a ``curr_date``.
  The window was entirely model-chosen, and the data layer only filtered *inside*
  it — so back-testing 2026-05-12 could return September news, and the report
  would present it as known at the time.
* ``get_stock_data`` clamped ``end_date`` to **market today**, not to the
  analysis date, so the same leak existed for bars.

The fix has two ends, and this module is the lower one:

1. **Tool layer** — :func:`marvel.harness.tools.pin_analysis_date` rewrites each
   tool so the model cannot see or set the anchoring date at all, and the
   ToolNode is wrapped in :func:`analysis_date_as_of` so every tool call executes
   inside a known analysis date.
2. **Data layer (here)** — every date-taking vendor function clamps its upper
   bound to :func:`analysis_date` and says so in its output. This is the
   load-bearing half: it holds even for callers that never went through the
   pinned tools (direct vendor calls, new tools added later, scripts, tests).

A context variable is the right carrier because the analysis date is a property
of the *run*, not of any single call, and LangGraph executes a run's nodes and
tools synchronously in the calling context. It is deliberately **not** given a
default-in-the-future or a "guess today" fallback: when nothing set it, the
clamps below do nothing and the previous behaviour is preserved exactly, so this
guard can never silently invent a date.
"""

from __future__ import annotations

import functools
import inspect
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from typing import Iterator, Optional

__all__ = [
    "analysis_date",
    "analysis_date_as_of",
    "anchored_date",
    "clamp_arguments",
    "clamp_upper",
    "parse_date",
    "set_analysis_date",
    "reset_analysis_date",
    "FORECAST_WARNING",
]

_ANALYSIS_DATE: ContextVar[Optional[str]] = ContextVar(
    "marvel_analysis_date", default=None
)

#: Prepended to a tool's output when its window had to be clamped. Worded like the
#: other point-in-time notices in ``dataflows.a_stock`` so a reader (or a model)
#: recognises it as the same class of statement.
FORECAST_WARNING = (
    "⚠️ 未来函数防护：本次取数的上界已被收敛到分析日，"
    "分析日之后发布的内容不会出现在下面。\n"
)


def parse_date(value) -> Optional[date]:
    """Parse the several date shapes that reach the data layer, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    try:  # last resort: let pandas-style ISO strings through
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def set_analysis_date(value) -> object:
    """Bind the analysis date for the current context; returns a reset token."""
    parsed = parse_date(value)
    return _ANALYSIS_DATE.set(parsed.isoformat() if parsed else None)


def reset_analysis_date(token: object) -> None:
    _ANALYSIS_DATE.reset(token)


def analysis_date() -> Optional[date]:
    """The analysis date bound to this context, or None when unbound."""
    return parse_date(_ANALYSIS_DATE.get())


@contextmanager
def analysis_date_as_of(value) -> Iterator[Optional[date]]:
    """Run a block with the analysis date bound, restoring it afterwards."""
    token = set_analysis_date(value)
    try:
        yield analysis_date()
    finally:
        reset_analysis_date(token)


def anchored_date(value=None) -> Optional[str]:
    """The authoritative date for a tool call.

    This is the **tool-layer** half of the guard. A tool that anchors its window
    on a date (``curr_date``, ``end_date``) ignores whatever the model wrote and
    uses the run's analysis date instead; the model cannot widen its own window,
    and a model that omits the argument is still correctly anchored.

    Falls back to the caller's value when no analysis date is bound, so direct
    library use and the existing tests behave exactly as before.
    """
    cutoff = analysis_date()
    if cutoff is not None:
        return cutoff.isoformat()
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else (value if value not in ("", None) else None)


def clamp_upper(value, *, label: str = "end_date") -> tuple[Optional[str], str]:
    """Clamp an upper-bound date to the analysis date.

    Returns ``(iso_date_or_original, note)``. ``note`` is empty unless a clamp
    actually happened, so callers can concatenate it unconditionally.

    ``value`` is returned unchanged when no analysis date is bound — this guard
    only ever *tightens* a window, never invents one.
    """
    cutoff = analysis_date()
    if cutoff is None or value is None or value == "":
        return (value, "")

    requested = parse_date(value)
    if requested is None:
        # Unparseable upper bound: treat it as absent rather than as "no limit".
        return (cutoff.isoformat(), _clamp_note(label, str(value), cutoff, unparseable=True))
    if requested <= cutoff:
        return (value, "")
    return (cutoff.isoformat(), _clamp_note(label, requested.isoformat(), cutoff))


def _clamp_note(label: str, requested: str, cutoff: date, *, unparseable: bool = False) -> str:
    why = "无法解析" if unparseable else "晚于分析日"
    return (
        f"⚠️ 未来函数防护：{label}={requested} {why}，已收敛到 {cutoff.isoformat()}。"
        f"分析日之后发布的内容不属于当时已知的事实。\n"
    )


def _attach_note(text: str, note: str) -> str:
    """Put the notice after a leading ``# ...`` header block, otherwise on top.

    Only level-1/2 headings count as the header block: a news body opens with
    ``### <title>`` per article, and treating that as a header would bury the
    notice inside the first article.
    """
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines) and re.match(r"#{1,2} \S", lines[index]):
        index += 1
    return "".join(lines[:index] + [note] + lines[index:])


def clamp_arguments(*param_names: str):
    """Decorator: clamp the named date arguments to the analysis date.

    Applied to the vendor functions in ``dataflows.a_stock``, this is the half of
    the point-in-time guard that holds **regardless of the caller**: a model
    passing ``end_date="2026-12-31"`` for a 2026-05-12 run, a script calling the
    vendor directly, or a tool added later that forgot to bind its dates.

    A string result gets a visible note whenever a clamp fired, so the clamping
    is never silent. When no analysis date is bound the arguments pass through
    untouched (see the module docstring).
    """

    def decorator(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = signature.bind_partial(*args, **kwargs)
            notes: list[str] = []
            for name in param_names:
                if name not in bound.arguments:
                    continue
                clamped, note = clamp_upper(bound.arguments[name], label=name)
                if note:
                    bound.arguments[name] = clamped
                    notes.append(note)

            result = fn(*bound.args, **bound.kwargs)
            if notes and isinstance(result, str):
                return _attach_note(result, "".join(notes))
            return result

        return wrapper

    return decorator
