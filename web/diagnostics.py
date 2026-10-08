"""Optional self-diagnostics for the Web UI process.

Twice now, diagnosing a wedged UI was blocked by the same wall: a running Python
process's stack could not be read. `py-spy` is not installed and cannot be installed
(the machine's network is down), WMI/`Get-CimInstance` is unavailable, and the
process's own log — with no stack in it — could only say *that* it was spinning at
100% CPU, not *where*.

Python ships the answer: :func:`faulthandler.dump_traceback_later` prints every
thread's stack on a timer. It has to be armed **inside the process that might freeze**
— the Streamlit server — which is `web/app.py`, not the `marvel-web` launcher (that
one only spawns it as a child).

Off by default: it is noisy. Turn it on when chasing a hang, then read the log.
"""

from __future__ import annotations

import os
from typing import Callable, Mapping

#: Environment variable holding the dump interval in seconds (unset = disabled).
ENV_VAR = "MARVEL_WEB_DUMP_STACKS"

#: Armed-once guard. Streamlit re-executes `web/app.py` top-to-bottom on **every
#: rerun** — and the running state reruns every two seconds — so arming from module
#: scope without this reset the timer on each pass and it never fired. The first
#: version of this module produced hundreds of "已开启" lines and not a single stack,
#: which is worse than useless: it looked like diagnostics were on.
_ARMED = False


def arm_stack_dumps(
    env: Mapping[str, str] | None = None,
    printer: Callable[[str], None] | None = None,
) -> float | None:
    """Arm periodic stack dumps when ``MARVEL_WEB_DUMP_STACKS`` is set.

    Returns the interval in seconds when armed, ``None`` otherwise. Never raises:
    a diagnostic must not be able to break the app it is meant to diagnose.
    """
    global _ARMED

    if printer is None:
        # stderr, not stdout: Streamlit captures `print` from inside a script run and
        # routes it to the browser instead of the terminal, so the "armed" notice sent
        # via stdout never reached the log — while faulthandler's dumps (written
        # straight to fd 2) did. Same destination as the dumps, same place to look.
        def printer(line: str) -> None:  # type: ignore[misc]
            import sys

            print(line, file=sys.stderr, flush=True)

    env = os.environ if env is None else env
    raw = str(env.get(ENV_VAR, "") or "").strip()
    if not raw:
        return None

    if _ARMED:
        return None

    try:
        interval = float(raw)
    except ValueError:
        printer(f"[diagnostics] {ENV_VAR}={raw!r} 不是数字，已忽略")
        return None

    if interval <= 0:
        printer(f"[diagnostics] {ENV_VAR}={raw!r} 必须为正数，已忽略")
        return None

    try:
        import faulthandler

        faulthandler.dump_traceback_later(interval, repeat=True, exit=False)
    except Exception as exc:  # noqa: BLE001 — diagnostics never break the app
        printer(f"[diagnostics] 无法开启线程栈转储：{exc}")
        return None

    _ARMED = True
    printer(
        f"[diagnostics] 已开启线程栈定时转储：每 {interval:g} 秒把所有线程的栈写进本日志。"
        "排查卡死时把这段日志发出来即可定位到具体那一行。"
    )
    return interval


def reset_armed_state() -> None:
    """Forget that dumps were armed (tests only; the process keeps its timer)."""
    global _ARMED
    _ARMED = False
