"""Guards for the LLM recorder used by scripts/record_debate.py.

The recorder is how a real (paid) run becomes evidence: without it, every
statement about "how the model handles an unverified claim" stays a guess about a
scripted stub. It is a LangChain callback handler, so the tests drive the
callbacks directly — no network, no provider.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, Generation, LLMResult

from marvel.harness.llm_recorder import JsonlLlmRecorder, iter_records


def _chat_result(text: str, reasoning: str = "") -> LLMResult:
    message = AIMessage(content=text, additional_kwargs={"reasoning_content": reasoning})
    return LLMResult(generations=[[ChatGeneration(message=message)]], llm_output={"model_name": "deepseek-v4-pro"})


def _plain_result(text: str) -> LLMResult:
    return LLMResult(generations=[[Generation(text=text)]], llm_output={"model_name": "some-model"})


@pytest.mark.unit
class TestRecorder:
    def test_chat_calls_round_trip(self, tmp_path):
        path = tmp_path / "run.jsonl"
        recorder = JsonlLlmRecorder(path)

        recorder.on_chat_model_start({}, [[HumanMessage(content="问题")]], run_id="r1")
        recorder.on_llm_end(_chat_result("答案", reasoning="思考"), run_id="r1")

        records = list(iter_records(path))
        assert len(records) == 1
        assert records[0]["prompt"] == "[human] 问题"
        assert records[0]["response"] == "答案"
        assert records[0]["reasoning_content"] == "思考"
        assert records[0]["model"] == "deepseek-v4-pro"
        assert records[0]["kind"] == "chat"
        assert records[0]["truncated"] is False

    def test_plain_llm_calls_are_recorded_too(self, tmp_path):
        path = tmp_path / "run.jsonl"
        recorder = JsonlLlmRecorder(path)

        recorder.on_llm_start({}, ["prompt-a"], run_id="r1")
        recorder.on_llm_end(_plain_result("response-a"), run_id="r1")

        record = list(iter_records(path))[0]
        assert record["kind"] == "llm"
        assert record["prompt"] == "prompt-a"
        assert record["response"] == "response-a"

    def test_long_payloads_are_clipped_and_flagged(self, tmp_path):
        path = tmp_path / "run.jsonl"
        recorder = JsonlLlmRecorder(path, max_chars=10)

        recorder.on_llm_start({}, ["x" * 500], run_id="r1")
        recorder.on_llm_end(_plain_result("y" * 500), run_id="r1")

        record = list(iter_records(path))[0]
        assert record["prompt"] == "x" * 10
        assert record["response"] == "y" * 10
        assert record["truncated"] is True

    def test_a_failed_call_is_recorded_as_evidence(self, tmp_path):
        path = tmp_path / "run.jsonl"
        recorder = JsonlLlmRecorder(path)

        recorder.on_chat_model_start({}, [[HumanMessage(content="问题")]], run_id="r1")
        recorder.on_llm_error(RuntimeError("402 Insufficient Balance"), run_id="r1")

        record = list(iter_records(path))[0]
        assert "402" in record["error"]
        assert record["response"] == "", "错误记录也保持同一形状，--report 才不用分叉"

    def test_sequential_calls_get_increasing_sequence_numbers(self, tmp_path):
        path = tmp_path / "run.jsonl"
        recorder = JsonlLlmRecorder(path)
        for i in range(3):
            recorder.on_llm_start({}, [f"p{i}"], run_id=f"r{i}")
            recorder.on_llm_end(_plain_result(f"a{i}"), run_id=f"r{i}")

        seqs = [r["seq"] for r in iter_records(path)]
        assert seqs == [1, 2, 3]

    def test_malformed_lines_are_skipped(self, tmp_path):
        path = tmp_path / "run.jsonl"
        path.write_text('{"seq": 1}\nnot json\n{"seq": 2}\n', encoding="utf-8")

        assert [r["seq"] for r in iter_records(path)] == [1, 2]

    def test_the_recording_is_valid_jsonl(self, tmp_path):
        path = tmp_path / "run.jsonl"
        recorder = JsonlLlmRecorder(path)
        for i in range(3):
            recorder.on_llm_start({}, [f"p{i}"], run_id=f"r{i}")
            recorder.on_llm_end(_plain_result(f"a{i}"), run_id=f"r{i}")

        for line in path.read_text(encoding="utf-8").splitlines():
            json.loads(line)  # must not raise


@pytest.mark.unit
class TestToolCallCapture:
    """结构化输出（RM / Trader / PM）在 DeepSeek 上走 tool_calls，content 是空的。

    第一版录制只记 content，于是那三个阶段的记录看起来全是「空响应」——
    等于把最关键的决策正文录丢了。
    """

    def test_structured_payload_is_recorded(self, tmp_path):
        path = tmp_path / "run.jsonl"
        recorder = JsonlLlmRecorder(path)
        message = AIMessage(
            content="",
            tool_calls=[{
                "name": "ResearchPlan",
                "args": {"recommendation": "Sell", "rationale": "未证实的传闻被放大"},
                "id": "call_1",
                "type": "tool_call",
            }],
        )
        result = LLMResult(
            generations=[[ChatGeneration(message=message)]],
            llm_output={"model_name": "deepseek-v4-pro"},
        )

        recorder.on_chat_model_start({}, [[HumanMessage(content="q")]], run_id="r1")
        recorder.on_llm_end(result, run_id="r1")

        record = list(iter_records(path))[0]
        assert record["tool_calls"][0]["name"] == "ResearchPlan"
        assert "Sell" in record["tool_calls"][0]["args"]

    def test_plain_answers_carry_an_empty_list(self, tmp_path):
        path = tmp_path / "run.jsonl"
        recorder = JsonlLlmRecorder(path)
        recorder.on_llm_start({}, ["p"], run_id="r1")
        recorder.on_llm_end(_plain_result("答案"), run_id="r1")

        assert list(iter_records(path))[0]["tool_calls"] == []


def _load_record_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "record_debate.py"
    spec = importlib.util.spec_from_file_location("record_debate_for_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestReportsFromRecording:
    """「便宜地重跑辩论」的离线部分：从录制里还原 9 份分析师报告。"""

    def test_it_takes_the_last_long_response_per_analyst(self):
        rd = _load_record_script()
        records = [
            {"seq": 1, "prompt": "你是一位专注于 A 股市场的技术分析师。你的任务是", "response": "短文"},
            {"seq": 2, "prompt": "你是一位专注于 A 股市场的技术分析师。你的任务是",
             "response": "报告正文" * 100},
            {"seq": 3, "prompt": "你是数据质量审核员。以下是 9 位分析师", "response": "## 数据质量审核报告"},
            # 辩手提示词里嵌着所有分析师的名字，不能被误判成分析师报告
            {"seq": 4, "prompt": "You are a Bull Analyst advocating … 技术分析师 政策分析师 游资",
             "response": "多头论证" * 100},
        ]

        reports, quality = rd.reports_from_recording(records)

        assert set(reports) == {"market_report"}
        assert reports["market_report"].startswith("报告正文")
        assert quality.startswith("## 数据质量")

    def test_stage_markers_use_role_sentences_not_bare_keywords(self):
        """量价分析师的提示词里也有「游资」；裸关键词会把它的报告记到游资头上。"""
        rd = _load_record_script()
        cases = {
            "你是一位量价分析师（Volume Price Analysis），严格基于 Anna Coulling《量价分析》": "volume_price",
            "你是一位专注于 A 股市场的游资与资金流向追踪分析师。": "hot_money",
            "你是一位专注于 A 股市场的解禁与减持监控分析师。": "lockup",
            "你是一位专注于 A 股市场的技术分析师。你的任务是": "market",
            "你是一位专注于 A 股市场的新闻与政策分析师。": "news",
            "你是一位专注于 A 股市场的政策分析师。": "policy",
            "你是一位专注于 A 股市场的宏观与板块分析师。": "macro",
        }
        for prompt, expected in cases.items():
            assert rd.classify_stage(prompt) == expected, prompt

    def test_it_maps_every_analyst_stage_to_its_state_field(self):
        rd = _load_record_script()
        assert set(rd._ANALYST_FIELDS.values()) == {
            "market_report", "sentiment_report", "news_report", "fundamentals_report",
            "policy_report", "hot_money_report", "lockup_report",
            "volume_price_report", "macro_report",
        }


@pytest.mark.unit
class TestLlmBudgetEnv:
    def test_defaults_are_bounded(self, monkeypatch):
        rd = _load_record_script()
        monkeypatch.delenv("MARVEL_LLM_TIMEOUT_S", raising=False)
        monkeypatch.delenv("MARVEL_LLM_MAX_RETRIES", raising=False)

        rd._apply_llm_budget_env()

        import os
        assert os.environ["MARVEL_LLM_TIMEOUT_S"] == rd.DEFAULT_LLM_TIMEOUT_S
        assert os.environ["MARVEL_LLM_MAX_RETRIES"] == rd.DEFAULT_LLM_MAX_RETRIES

    def test_existing_values_win(self, monkeypatch):
        rd = _load_record_script()
        monkeypatch.setenv("MARVEL_LLM_TIMEOUT_S", "45")

        rd._apply_llm_budget_env()

        import os
        assert os.environ["MARVEL_LLM_TIMEOUT_S"] == "45"


@pytest.mark.unit
class TestGraphForwardsTheLlmBudget:
    """`MARVEL_LLM_TIMEOUT_S` / `MARVEL_LLM_MAX_RETRIES` 必须真的到客户端手里。

    实测背景：一次真实录制的最后一次调用挂住不动（TCP CLOSE_WAIT 堆积、CPU 冻结），
    SDK 默认 600s×3 次会把整条流水线拖住半小时。
    """

    def _kwargs(self, monkeypatch, **env):
        from marvel.graph.trading_graph import MarvelGraph

        for key, value in env.items():
            monkeypatch.setenv(key, value)
        graph = MarvelGraph.__new__(MarvelGraph)
        graph.config = {"llm_provider": "deepseek"}
        return graph._get_provider_kwargs()

    def test_values_are_forwarded(self, monkeypatch):
        kwargs = self._kwargs(monkeypatch, MARVEL_LLM_TIMEOUT_S="120", MARVEL_LLM_MAX_RETRIES="2")

        assert kwargs["timeout"] == 120.0
        assert kwargs["max_retries"] == 2

    def test_absent_env_changes_nothing(self, monkeypatch):
        monkeypatch.delenv("MARVEL_LLM_TIMEOUT_S", raising=False)
        monkeypatch.delenv("MARVEL_LLM_MAX_RETRIES", raising=False)

        kwargs = self._kwargs(monkeypatch)

        assert "timeout" not in kwargs and "max_retries" not in kwargs

    def test_garbage_is_ignored_not_fatal(self, monkeypatch):
        kwargs = self._kwargs(monkeypatch, MARVEL_LLM_TIMEOUT_S="abc", MARVEL_LLM_MAX_RETRIES="x")

        assert "timeout" not in kwargs and "max_retries" not in kwargs


def _load_ab_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "compare_polarization.py"
    spec = importlib.util.spec_from_file_location("compare_polarization_for_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestAblationSwitch:
    def test_discipline_is_on_by_default(self, monkeypatch):
        from marvel.agents.utils.evidence import provenance_discipline_enabled

        monkeypatch.delenv("MARVEL_ABLATE_PROVENANCE_DISCIPLINE", raising=False)
        assert provenance_discipline_enabled() is True

    def test_ablation_disables_the_digest(self, monkeypatch):
        from marvel.agents.utils import evidence

        monkeypatch.setenv("MARVEL_ABLATE_PROVENANCE_DISCIPLINE", "1")

        assert evidence.provenance_discipline_enabled() is False
        assert evidence.build_evidence_digest(
            f"某条（{evidence.UNVERIFIED_LABEL}）"
        ) == ""


@pytest.mark.unit
class TestPolarizationAbHelpers:
    def test_inject_claim_marks_it_and_does_not_mutate_the_input(self):
        ab = _load_ab_script()
        reports = {"news_report": "原有内容"}

        injected = ab.inject_claim(reports, "某爆料", "单一来源，未经交叉验证")

        assert "某爆料（单一来源，未经交叉验证）" in injected["news_report"]
        assert "原有内容" in injected["news_report"]
        assert reports["news_report"] == "原有内容"

    def test_summarise_detects_repetition_and_label_retention(self):
        ab = _load_ab_script()
        records = [
            {"prompt": "You are a Bull Analyst advocating", "response": "某爆料（单一来源，未经交叉验证）"},
            {"prompt": "You are a Bear Analyst making", "response": "某爆料应当被当真"},
        ]

        table = ab.summarise(
            records,
            lambda prompt: "bull" if "Bull Analyst" in prompt else "bear",
            "单一来源，未经交叉验证",
            "某爆料",
        )

        assert table["bull"]["repeats_claim"] is True
        assert table["bull"]["keeps_label"] is True
        assert table["bear"]["repeats_claim"] is True
        assert table["bear"]["keeps_label"] is False

    def test_the_script_is_inert_on_import(self):
        ab = _load_ab_script()
        assert callable(ab.main)
