"""Guards for the DeepSeek agent harness (`marvel/harness/`).

The harness is a second way into the data layer and a second way to drive a
model, so the things worth pinning are the ones that go wrong quietly:

* a tool failure being read as "no data";
* a run that ran out of steps being reported as a finished run;
* the thinking-mode ``reasoning_content`` field being dropped between turns
  (DeepSeek then rejects the next request with HTTP 400);
* a replay that does not actually reproduce the recorded run;
* tool calls escaping the analysis-date guard that the pipeline enforces.
"""

from __future__ import annotations

import json
from typing import Annotated

import pytest

from marvel.harness import (
    RECORDING_VERSION,
    AgentLoop,
    ModelReply,
    ReplayModel,
    RunRecorder,
    ScriptedModel,
    ToolCall,
    ToolRegistry,
    load_recording,
)
from marvel.harness.loop import _assistant_message


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register
    def get_quote(
        code: Annotated[str, "6-digit A-share code"],
        days: Annotated[int, "look-back window"] = 30,
    ) -> str:
        """Return a fake quote."""
        return f"quote {code} {days}d"

    @registry.register(name="explode")
    def _explode() -> str:
        raise RuntimeError("upstream is down")

    return registry


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToolRegistry:
    def test_schema_follows_the_annotated_convention(self):
        schema = _registry().get("get_quote").parameters

        assert schema["properties"]["code"]["description"] == "6-digit A-share code"
        assert schema["properties"]["code"]["type"] == "string"
        assert schema["properties"]["days"]["type"] == "integer"
        assert schema["properties"]["days"]["default"] == 30
        assert schema["required"] == ["code"], "有默认值的参数不应当是必填"

    def test_duplicate_registration_is_an_error(self):
        registry = _registry()

        with pytest.raises(ValueError, match="already registered"):
            registry.register(lambda: "x", name="get_quote")

    def test_unknown_tool_becomes_a_readable_error(self):
        """The model must be told, not crashed: it can still recover."""
        result = _registry().dispatch(ToolCall(name="nope", arguments={}))

        assert result.is_error
        assert "unknown tool" in result.content
        assert "get_quote" in result.content, "错误信息里要列出可用工具"

    def test_a_failing_tool_is_not_reported_as_missing_data(self):
        """The distinction this whole repo keeps re-learning: failure ≠ empty."""
        result = _registry().dispatch(ToolCall(name="explode", arguments={}))

        assert result.is_error
        assert "RuntimeError" in result.error
        assert "not a finding of 'no data'" in result.content

    def test_from_tools_reuses_the_pipeline_schema(self):
        """A LangChain tool must keep the exact schema the pipeline shows the model."""
        from marvel.agents.utils.agent_utils import get_balance_sheet, get_news

        registry = ToolRegistry.from_tools([get_news, get_balance_sheet])

        assert registry.names() == ["get_balance_sheet", "get_news"]
        news = registry.get("get_news").parameters
        assert set(news["properties"]) == {"ticker", "start_date", "end_date"}
        assert registry.get("get_balance_sheet").parameters["required"], (
            "财报工具的 curr_date 是必填，schema 丢了就等于放宽了调用"
        )

    def test_none_renders_as_empty_text(self):
        registry = ToolRegistry()

        @registry.register
        def nothing() -> None:
            return None

        assert registry.dispatch(ToolCall(name="nothing")).content == ""


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentLoop:
    def test_terminates_on_a_final_answer(self):
        model = ScriptedModel([ModelReply(content="done")])
        result = AgentLoop(model, _registry(), max_steps=3).run("task")

        assert result.stopped_because == "final_answer"
        assert result.final == "done"
        assert not result.hit_step_budget
        assert result.tool_calls == []

    def test_tool_round_trip_threads_the_result_back_to_the_model(self):
        model = ScriptedModel([
            ModelReply(tool_calls=[ToolCall(name="get_quote", arguments={"code": "600519"}, id="c1")]),
            ModelReply(content="answer"),
        ])
        result = AgentLoop(model, _registry(), max_steps=3).run("task")

        assert result.stopped_because == "final_answer"
        assert [call.name for call in result.tool_calls] == ["get_quote"]
        # the second prompt must contain the tool output, addressed to the call
        second = model.seen[1]["messages"]
        tool_messages = [m for m in second if isinstance(m, dict) and m.get("role") == "tool"]
        assert tool_messages and tool_messages[0]["tool_call_id"] == "c1"
        assert "quote 600519 30d" in tool_messages[0]["content"]

    def test_step_budget_exhaustion_is_not_reported_as_success(self):
        """A truncated run must not look finished — that is the whole point."""
        replies = [
            ModelReply(tool_calls=[ToolCall(name="get_quote", arguments={"code": "600519"}, id=f"c{i}")])
            for i in range(3)
        ]
        result = AgentLoop(
            ScriptedModel(replies), _registry(), max_steps=2
        ).run("task")

        assert result.hit_step_budget
        assert result.stopped_because == "step_budget"
        assert len(result.steps) == 2

    def test_step_budget_must_be_positive(self):
        with pytest.raises(ValueError, match="max_steps"):
            AgentLoop(ScriptedModel([]), _registry(), max_steps=0)

    def test_tool_errors_do_not_abort_the_run(self):
        model = ScriptedModel([
            ModelReply(tool_calls=[ToolCall(name="explode", arguments={}, id="c1")]),
            ModelReply(content="recovered"),
        ])
        result = AgentLoop(model, _registry(), max_steps=3).run("task")

        assert result.final == "recovered"
        assert len(result.tool_errors) == 1

    def test_multiple_tool_calls_in_one_turn_all_run(self):
        model = ScriptedModel([
            ModelReply(tool_calls=[
                ToolCall(name="get_quote", arguments={"code": "600519", "days": 5}, id="a"),
                ToolCall(name="get_quote", arguments={"code": "300750", "days": 10}, id="b"),
            ]),
            ModelReply(content="both"),
        ])
        result = AgentLoop(model, _registry(), max_steps=3).run("task")

        assert [r.content for r in result.steps[0].results] == [
            "quote 600519 5d", "quote 300750 10d",
        ]

    def test_raw_message_is_preferred_over_a_rebuilt_dict(self):
        """DeepSeek thinking mode survives only if the SDK message is threaded back."""
        sentinel = object()
        reply = ModelReply(
            tool_calls=[ToolCall(name="get_quote", arguments={}, id="c1")], raw=sentinel
        )

        assert _assistant_message(reply) is sentinel

    def test_rebuilt_dict_is_used_when_there_is_no_raw_message(self):
        reply = ModelReply(
            content="thinking", tool_calls=[ToolCall(name="get_quote", arguments={"code": "1"}, id="c1")]
        )

        message = _assistant_message(reply)

        assert message["role"] == "assistant"
        assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"code": "1"}


# ---------------------------------------------------------------------------
# Recorder and replay
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecordingAndReplay:
    def _run(self, tmp_path, *, model=None):
        from marvel.dataflows import as_of

        path = tmp_path / "run.jsonl"
        recorder = RunRecorder(path)
        model = model or ScriptedModel([
            ModelReply(
                tool_calls=[ToolCall(name="get_quote", arguments={"code": "600519"}, id="c1")],
                reasoning="先看行情",
            ),
            ModelReply(content="结论", reasoning="再下判断"),
        ])
        result = AgentLoop(
            model, _registry(), recorder=recorder, max_steps=3,
            analysis_date="2026-05-12",
        ).run("任务")
        assert as_of.analysis_date() is None, "运行结束后分析日不能留在上下文里"
        return path, result

    def test_recording_is_jsonl_with_reasoning_preserved(self, tmp_path):
        path, _ = self._run(tmp_path)
        events = load_recording(path)

        assert [e["type"] for e in events] == [
            "run_start", "model_reply", "tool_result", "model_reply", "run_end",
        ]
        assert all(e["version"] == RECORDING_VERSION for e in events)
        assert events[1]["reasoning"] == "先看行情", "思考内容没有落盘"
        assert events[2]["content"] == "quote 600519 30d"
        assert events[0]["analysis_date"] == "2026-05-12"
        assert events[-1]["final"] == "结论"

    def test_replay_reproduces_the_recorded_run(self, tmp_path):
        path, original = self._run(tmp_path)

        replayed = AgentLoop(
            ReplayModel.from_recording(path), _registry(), max_steps=3
        ).run("任务")

        assert replayed.final == original.final
        assert replayed.stopped_because == original.stopped_because
        assert [c.name for c in replayed.tool_calls] == [c.name for c in original.tool_calls]
        assert [r.content for r in replayed.steps[0].results] == [
            r.content for r in original.steps[0].results
        ]

    def test_replay_sees_the_same_prompts(self, tmp_path):
        """Replay is only meaningful if the prompts are reproduced too."""
        path, _ = self._run(tmp_path)
        model = ReplayModel.from_recording(path)

        AgentLoop(model, _registry(), max_steps=3).run("任务")

        assert len(model.seen) == 2
        second = [m for m in model.seen[1]["messages"] if isinstance(m, dict) and m.get("role") == "tool"]
        assert second and second[0]["content"] == "quote 600519 30d"

    def test_running_out_of_recorded_turns_is_loud(self, tmp_path):
        path, _ = self._run(tmp_path)
        model = ReplayModel.from_recording(path)
        # consume both recorded turns, then the loop asks for a third
        model.complete([], None)
        model.complete([], None)

        with pytest.raises(AssertionError, match="ran out of model turns"):
            AgentLoop(model, _registry(), max_steps=3).run("任务")

    def test_a_recording_from_another_version_is_rejected(self, tmp_path):
        path = tmp_path / "old.jsonl"
        path.write_text(
            json.dumps({"version": 999, "type": "run_start"}) + "\n", encoding="utf-8"
        )

        with pytest.raises(ValueError, match="recording version"):
            load_recording(path)

    def test_a_truncated_recording_is_rejected(self, tmp_path):
        path = tmp_path / "broken.jsonl"
        path.write_text('{"version": 1, "type": "run_start"}\n{"version": 1, "ty',
                        encoding="utf-8")

        with pytest.raises(ValueError, match="truncated or corrupt"):
            load_recording(path)

    def test_recorder_can_be_disabled_without_losing_events(self, tmp_path):
        recorder = RunRecorder(tmp_path / "unused.jsonl", enabled=False)

        recorder.write({"type": "x"})

        assert recorder.events() == [{"version": RECORDING_VERSION, "type": "x"}]
        assert not (tmp_path / "unused.jsonl").exists()


# ---------------------------------------------------------------------------
# The harness inherits the point-in-time guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHarnessHonoursTheAnalysisDate:
    def test_tools_execute_inside_the_analysis_date(self):
        from marvel.dataflows.as_of import analysis_date

        seen: dict = {}
        registry = ToolRegistry()

        @registry.register
        def probe() -> str:
            seen["date"] = analysis_date()
            return "ok"

        model = ScriptedModel([
            ModelReply(tool_calls=[ToolCall(name="probe", arguments={}, id="c1")]),
            ModelReply(content="done"),
        ])
        AgentLoop(model, registry, max_steps=2).run("t", analysis_date="2026-05-12")

        assert seen["date"] is not None
        assert seen["date"].isoformat() == "2026-05-12"

    def test_a_real_marvel_tool_cannot_reach_past_the_analysis_date(
        self, monkeypatch
    ):
        """End to end: harness → real tool → vendor → clamped window."""
        import marvel.dataflows.a_stock as a_stock
        from marvel.agents.utils.agent_utils import get_news

        monkeypatch.setattr(
            a_stock,
            "_fetch_news_eastmoney",
            lambda code: [
                {"title": "BEFORE", "time": "2026-05-10", "content": "known"},
                {"title": "AFTER", "time": "2026-08-01", "content": "future"},
            ],
        )
        registry = ToolRegistry.from_tools([get_news])
        model = ScriptedModel([
            ModelReply(tool_calls=[ToolCall(
                name="get_news",
                arguments={
                    "ticker": "600519",
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                },
                id="c1",
            )]),
            ModelReply(content="done"),
        ])

        result = AgentLoop(model, registry, max_steps=2).run(
            "新闻", analysis_date="2026-05-12"
        )

        content = result.steps[0].results[0].content
        assert "BEFORE" in content
        assert "AFTER" not in content, "harness 绕过了分析日守卫"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCli:
    def test_nothing_to_do_is_a_usage_error(self, capsys):
        from marvel.harness.__main__ import main

        assert main([]) == 2
        assert "nothing to do" in capsys.readouterr().err

    def test_registry_exposes_the_pipeline_tools(self):
        from marvel.harness.__main__ import build_registry

        names = build_registry().names()

        for expected in (
            "get_stock_data", "get_indicators", "get_news", "get_global_news",
            "get_hot_stocks", "get_northbound_flow", "get_dragon_tiger_board",
        ):
            assert expected in names, f"{expected} 没有进入 harness 的工具面"

    def test_tool_sets_can_be_narrowed(self):
        from marvel.harness.__main__ import build_registry

        assert build_registry(("news",)).names() == ["get_global_news", "get_news"]

    def test_replay_mode_needs_no_api_key(self, tmp_path, monkeypatch, capsys):
        """`--replay` is the offline path: it must not touch the model factory."""
        from marvel.harness.__main__ import main

        path, _ = TestRecordingAndReplay()._run(tmp_path)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setattr(
            "marvel.harness.__main__.create_deepseek_model",
            lambda *a, **k: pytest.fail("replay 模式不应构造真实模型"),
        )

        code = main(["--replay", str(path), "--task", "任务"])

        assert code == 0
        assert "结论" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# DeepSeek wiring
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeepSeekWiring:
    def test_missing_key_is_a_clear_error(self, monkeypatch):
        from marvel.harness.models import create_deepseek_model

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "")
        monkeypatch.setattr(
            "marvel.default_config.DEFAULT_CONFIG", {"quick_think_llm": "deepseek-chat"}
        )

        with pytest.raises(RuntimeError, match="no DeepSeek API key"):
            create_deepseek_model()

    def test_model_is_built_through_the_repo_factory(self, monkeypatch):
        """The harness must not grow a second HTTP client."""
        from marvel.harness.models import LangChainToolCallingModel, create_deepseek_model
        from marvel.llm_clients.model_catalog import get_known_models

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-placeholder")
        configured = get_known_models()["deepseek"][0]
        model = create_deepseek_model(configured)

        assert isinstance(model, LangChainToolCallingModel)
        assert model.name == f"deepseek:{configured}"
