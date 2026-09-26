import os
import re
import json
import logging
import tempfile
import contextlib
import pandas as pd
from datetime import date, timedelta, datetime
from typing import Annotated

logger = logging.getLogger(__name__)

SavePathType = Annotated[str, "File path to save data. If None, data is not saved."]


def atomic_write_text(path: str, text: str, *, newline: str | None = None) -> None:
    """Atomically write ``text`` to ``path`` (temp file in the same dir + replace).

    The data layer's caches (name map JSON, K-line CSV, northbound history CSV,
    yfinance OHLCV CSV) are all rewritten **in full**. With a plain
    ``open(path, "w")`` a crash, kill or power loss halfway through leaves a
    truncated file that still *parses*, so the next run silently reads a cache
    that is missing an arbitrary suffix of its rows — and the northbound file
    rewrites its accumulated history every time, so a partial write there
    destroys every previously collected trading day.

    ``os.replace`` is atomic within one filesystem: a reader sees either the old
    complete file or the new complete file, never an intermediate state.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=os.path.basename(path) + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Never leave a .tmp behind; the original file was not touched.
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise

# Tickers can contain letters, digits, dot, dash, underscore, and caret
# (for index symbols like ^GSPC). Anything else is rejected so the value
# never escapes a containing directory when interpolated into a path.
_TICKER_PATH_RE = re.compile(r"^[A-Za-z0-9._\-\^]+$")
_HAS_CHINESE_RE = re.compile(r"[一-鿿]")


def safe_ticker_component(value: str, *, max_len: int = 32) -> str:
    """Validate ``value`` is safe to interpolate into a filesystem path.

    If the value contains Chinese characters (common when LLMs return stock
    names instead of codes), automatically resolve it to a 6-digit A-stock
    code via ``resolve_ticker`` before validation.

    Returns ``value`` unchanged when it matches the allowed pattern; raises
    ``ValueError`` otherwise.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"ticker must be a non-empty string, got {value!r}")

    if _HAS_CHINESE_RE.search(value):
        from marvel.dataflows.a_stock import resolve_ticker
        resolved = resolve_ticker(value)
        logger.info("Auto-resolved Chinese ticker %r -> %s", value, resolved)
        value = resolved

    if len(value) > max_len:
        raise ValueError(f"ticker exceeds {max_len} chars: {value!r}")
    if not _TICKER_PATH_RE.fullmatch(value):
        raise ValueError(
            f"ticker contains characters not allowed in a filesystem path: {value!r}"
        )
    if set(value) == {"."}:
        raise ValueError(f"ticker cannot consist solely of dots: {value!r}")
    return value


def save_output(data: pd.DataFrame, tag: str, save_path: SavePathType = None) -> None:
    if save_path:
        data.to_csv(save_path, encoding="utf-8")
        print(f"{tag} saved to {save_path}")


def get_current_date():
    return date.today().strftime("%Y-%m-%d")


def decorate_all_methods(decorator):
    def class_decorator(cls):
        for attr_name, attr_value in cls.__dict__.items():
            if callable(attr_value):
                setattr(cls, attr_name, decorator(attr_value))
        return cls

    return class_decorator


def get_next_weekday(date):

    if not isinstance(date, datetime):
        date = datetime.strptime(date, "%Y-%m-%d")

    if date.weekday() >= 5:
        days_to_add = 7 - date.weekday()
        next_weekday = date + timedelta(days=days_to_add)
        return next_weekday
    else:
        return date
