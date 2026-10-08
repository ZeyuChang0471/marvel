"""The optional self-diagnostics switch.

`arm_stack_dumps` exists because a frozen Web UI could not be diagnosed: `py-spy` is
absent and cannot be installed (no network), WMI is unavailable, and the server's log
said only that it was spinning at 100% CPU, not where. This arms Python's own
`faulthandler` timer inside the server process so the next hang writes its own stack.
"""

from __future__ import annotations

import pytest

from web import diagnostics


@pytest.fixture(autouse=True)
def _disarm():
    """Make sure an armed dump from one test cannot fire during another."""
    import faulthandler

    diagnostics.reset_armed_state()
    faulthandler.cancel_dump_traceback_later()
    yield
    diagnostics.reset_armed_state()
    faulthandler.cancel_dump_traceback_later()


@pytest.mark.unit
class TestItIsArmedExactlyOnce:
    """回归：`app.py` 每次 rerun 都会重新执行，重新武装等于不断重置计时器。

    第一版就是这样：日志里几百行「已开启线程栈定时转储」，**一条栈都没有**——
    比没有诊断更糟，因为它看起来是开着的。
    """

    def test_the_second_call_does_not_rearm(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "faulthandler.dump_traceback_later",
            lambda interval, **kw: calls.append(interval),
        )

        first = diagnostics.arm_stack_dumps(
            env={diagnostics.ENV_VAR: "30"}, printer=lambda _: None
        )
        second = diagnostics.arm_stack_dumps(
            env={diagnostics.ENV_VAR: "30"}, printer=lambda _: None
        )

        assert first == 30
        assert second is None
        assert calls == [30.0], f"重复武装了 {len(calls)} 次——计时器会被不断重置"

    def test_the_message_is_printed_only_once(self, monkeypatch):
        monkeypatch.setattr("faulthandler.dump_traceback_later", lambda *a, **k: None)
        printed = []

        for _ in range(20):   # 模拟一次分析里几十次 rerun
            diagnostics.arm_stack_dumps(
                env={diagnostics.ENV_VAR: "30"}, printer=printed.append
            )

        assert len(printed) == 1, f"日志被刷了 {len(printed)} 行"

    def test_a_bad_value_does_not_block_a_later_good_one(self, monkeypatch):
        """值不合法时不该把 _ARMED 置上，否则改对了也不再生效。"""
        monkeypatch.setattr("faulthandler.dump_traceback_later", lambda *a, **k: None)

        assert diagnostics.arm_stack_dumps(
            env={diagnostics.ENV_VAR: "abc"}, printer=lambda _: None
        ) is None
        assert diagnostics.arm_stack_dumps(
            env={diagnostics.ENV_VAR: "30"}, printer=lambda _: None
        ) == 30


@pytest.mark.unit
class TestStackDumpSwitch:
    def test_it_is_off_by_default(self):
        assert diagnostics.arm_stack_dumps(env={}) is None

    def test_an_empty_or_whitespace_value_means_off(self):
        assert diagnostics.arm_stack_dumps(env={diagnostics.ENV_VAR: "   "}) is None

    def test_setting_an_interval_arms_the_dump(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "faulthandler.dump_traceback_later",
            lambda interval, **kw: calls.append((interval, kw)),
        )

        armed = diagnostics.arm_stack_dumps(
            env={diagnostics.ENV_VAR: "30"}, printer=lambda _: None
        )

        assert armed == 30
        assert calls == [(30.0, {"repeat": True, "exit": False})], (
            "必须是重复转储（repeat=True）——只打一次的话，卡死发生在两次之间就白开了"
        )

    def test_the_message_tells_the_user_where_to_look(self):
        printed = []
        diagnostics.arm_stack_dumps(
            env={diagnostics.ENV_VAR: "15"}, printer=printed.append
        )

        text = " ".join(printed)
        assert "日志" in text and "15" in text

    @pytest.mark.parametrize("value", ["abc", "0", "-5", "1e999"])
    def test_a_bad_value_is_ignored_not_fatal(self, value):
        printed = []
        result = diagnostics.arm_stack_dumps(
            env={diagnostics.ENV_VAR: value}, printer=printed.append
        )

        assert result is None
        assert printed, "值不合法时应当说一声，而不是静默忽略"

    def test_a_failure_inside_faulthandler_cannot_break_the_app(self, monkeypatch):
        """诊断功能坏了也不能影响应用本身。"""
        def boom(*args, **kwargs):
            raise RuntimeError("faulthandler 不可用")

        monkeypatch.setattr("faulthandler.dump_traceback_later", boom)
        printed = []

        assert diagnostics.arm_stack_dumps(
            env={diagnostics.ENV_VAR: "30"}, printer=printed.append
        ) is None
        assert any("无法开启" in line for line in printed)

    def test_the_app_arms_it_at_import(self):
        """要挂在 app.py：`marvel-web` 只是把 app.py 当子进程起，父进程里挂没用。"""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent / "web" / "app.py"
        ).read_text(encoding="utf-8")

        assert "arm_stack_dumps()" in source
        assert "web/launch.py" in source, "没有说明为什么挂在 app.py 而不是 launch.py"
