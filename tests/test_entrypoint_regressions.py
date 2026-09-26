"""Regression guards for the CLI / Web / harness defects found in this review pass.

Four of these broke documented entry points outright, and two of them were gaps in
code written earlier in this same review: extending the analyst registry to nine
left `MessageBuffer.section_titles` behind (the CLI died minutes into every A-share
run), and binding the Web UI to loopback via `.streamlit/config.toml` did not hold
for `marvel-web` started from any other directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(*parts: str) -> str:
    return REPO_ROOT.joinpath(*parts).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI: every analyst report the pipeline can produce must be renderable
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCliRendersEveryAnalystReport:
    def test_section_titles_come_from_the_registry(self):
        """`section_titles` listed only the 4 upstream analysts.

        `report_sections` had grown to nine, so the first A-share report to arrive
        raised `KeyError: 'volume_price_report'` and the whole run failed minutes
        in — after the analyst LLM calls had already been paid for.
        """
        src = _read("cli", "main.py")

        assert "ANALYST_REPORT_KEYS.items()" in src, (
            "section_titles 又变回手写清单了；它必须由 cli/models.py 的注册表派生"
        )

    @pytest.mark.parametrize(
        "report_key",
        [
            "market_report", "sentiment_report", "news_report", "fundamentals_report",
            "policy_report", "hot_money_report", "lockup_report",
            "volume_price_report", "macro_report",
        ],
    )
    def test_every_report_key_survives_the_titles_lookup(self, report_key):
        """Behavioural: drive the real MessageBuffer with each report key."""
        from cli.main import MessageBuffer
        from cli.models import ANALYST_SELECTION_ORDER

        buffer = MessageBuffer()
        buffer.init_for_analysis(list(ANALYST_SELECTION_ORDER))

        assert report_key in buffer.report_sections, f"{report_key} 没有报告区"

        # must not raise KeyError
        buffer.update_report_section(report_key, "报告正文")

        assert "报告正文" in (buffer.current_report or "")

    def test_unknown_section_does_not_crash_the_run(self):
        """`.get(...)` keeps a future section from taking the run down with it."""
        from cli.main import MessageBuffer

        buffer = MessageBuffer()
        buffer.init_for_analysis(["market"])
        buffer.report_sections["some_future_report"] = "正文"

        buffer.update_report_section("some_future_report", "正文")

        assert "正文" in (buffer.current_report or "")


# ---------------------------------------------------------------------------
# Web: the launcher must not depend on the working directory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebLauncherBindsLoopback:
    def test_address_is_passed_explicitly(self):
        src = _read("web", "launch.py")

        assert "--server.address=" in src
        assert "127.0.0.1" in src

    def test_env_var_still_wins_for_containers(self):
        """docker-compose sets STREAMLIT_SERVER_ADDRESS=0.0.0.0 for the container."""
        src = _read("web", "launch.py")

        assert "STREAMLIT_SERVER_ADDRESS" in src
        assert "os.environ.get" in src

    def test_a_real_subprocess_call_is_constructed(self, monkeypatch):
        """Behavioural: capture the command without starting a server."""
        captured: dict = {}

        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            return None

        import web.launch as launch

        monkeypatch.setattr(launch.subprocess, "run", fake_run)
        monkeypatch.delenv("STREAMLIT_SERVER_ADDRESS", raising=False)

        launch.main()

        assert any("--server.address=127.0.0.1" in part for part in captured["cmd"])


# ---------------------------------------------------------------------------
# Harness: documented entry points must actually work
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHarnessEntryPoint:
    def test_env_file_is_loaded(self):
        """No module under marvel/harness/ read .env, so HARNESS.md's example failed."""
        assert "load_dotenv" in _read("marvel", "harness", "models.py")

    def test_default_model_belongs_to_the_deepseek_provider(self, monkeypatch):
        """It used to default to `quick_think_llm` — an OpenAI model id."""
        from marvel.harness.models import _default_deepseek_model
        from marvel.llm_clients.model_catalog import get_known_models

        known = get_known_models().get("deepseek") or []
        assert _default_deepseek_model() in known, (
            "harness 默认模型不在 deepseek 的已知模型列表里，"
            "请求会被 api.deepseek.com 拒绝"
        )


# ---------------------------------------------------------------------------
# run.py dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunPyCliDispatch:
    def test_the_command_token_is_stripped_before_typer(self):
        """`python run.py cli` died with "Got unexpected extra argument(s) (cli)"."""
        src = _read("run.py")

        assert "sys.argv = [sys.argv[0], *sys.argv[2:]]" in src, (
            "run_cli 没有摘掉已被消费的 'cli' token，Typer 会把它当成多余参数"
        )


# ---------------------------------------------------------------------------
# User-visible analyst counts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoStaleAnalystCountOnScreen:
    def test_welcome_screen_says_nine(self):
        src = _read("web", "app.py")

        assert "7位AI分析师" not in src, "欢迎页仍写着 7 位分析师，而流水线跑 9 个"
        assert "9位AI分析师" in src

    def test_no_web_or_cli_source_advertises_seven(self):
        import re

        offenders = []
        for path in list((REPO_ROOT / "web").rglob("*.py")) + list(
            (REPO_ROOT / "cli").rglob("*.py")
        ):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"\b7\s*(位|个)\s*(AI)?\s*(分析师|Analyst)", text):
                offenders.append(f"{path.name}: {match.group(0)}")

        assert not offenders, f"用户可见的「7 个分析师」残留：{offenders}"
