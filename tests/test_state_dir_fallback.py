"""MARVEL must still work when its configured state directory refuses writes.

On this machine `~/.marvel` stopped accepting **new files** (and appends) for every
process, the owner included: `os.open(..., O_CREAT|O_EXCL)` returned WinError 5 while
the SDDL showed an inherited FullControl for the user and no mandatory label, and
Windows Defender's controlled-folder access was off — i.e. the refusal comes from
outside the ACL (a security product's file protection). The app therefore died with
``分析失败: [WinError 5] 拒绝访问: 'C:\\Users\\zangk\\.marvel\\logs\\600577'``.

`default_config` now resolves each state path with a real create probe and falls back
to a directory beside the code, announcing where the reports went. These tests pin
that behaviour, because "silently wrote somewhere else" would be indistinguishable
from "my history was deleted".
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from marvel import default_config


@pytest.mark.unit
class TestTheProbeIsHonest:
    def test_a_normal_directory_is_writable(self, tmp_path):
        assert default_config._can_create_files(str(tmp_path / "fresh")) is True

    def test_the_probe_leaves_nothing_behind(self, tmp_path):
        default_config._can_create_files(str(tmp_path))
        assert list(tmp_path.iterdir()) == []

    def test_it_says_no_when_creation_is_refused(self, monkeypatch, tmp_path):
        """`os.access` says yes here — that is exactly why it is not used."""
        def denied(path, flags, mode=0o777):
            raise PermissionError(13, "Permission denied", path)

        monkeypatch.setattr(default_config.os, "open", denied)

        assert default_config._can_create_files(str(tmp_path)) is False

    def test_an_existing_file_is_appended_not_recreated(self, tmp_path):
        """Regressing this would move a working memory log for no reason."""
        existing = tmp_path / "trading_memory.md"
        existing.write_text("已积累的记忆", encoding="utf-8")

        assert default_config._can_append(str(existing)) is True
        assert existing.read_text(encoding="utf-8") == "已积累的记忆"

    def test_a_missing_file_needs_a_writable_parent(self, monkeypatch, tmp_path):
        def denied(path, flags, mode=0o777):
            raise PermissionError(13, "Permission denied", path)

        monkeypatch.setattr(default_config.os, "open", denied)

        assert default_config._can_append(str(tmp_path / "missing.md")) is False


@pytest.mark.unit
class TestDirectoryResolution:
    def test_a_writable_preferred_dir_is_kept(self, tmp_path, capsys):
        preferred = str(tmp_path / "preferred")
        chosen = default_config._resolve_state_dir(
            preferred, str(tmp_path / "fallback"), "测试"
        )

        assert chosen == preferred
        assert capsys.readouterr().err == "", "没发生回退就不该打扰用户"

    def test_a_refused_dir_falls_back_and_says_so(self, monkeypatch, tmp_path, capsys):
        real_open = default_config.os.open

        def denied(path, flags, mode=0o777):
            if "preferred" in str(path):
                raise PermissionError(13, "Permission denied", path)
            return real_open(path, flags, mode)

        monkeypatch.setattr(default_config.os, "open", denied)

        fallback = str(tmp_path / "fallback")
        chosen = default_config._resolve_state_dir(
            str(tmp_path / "preferred"), fallback, "报告输出"
        )

        assert chosen == fallback
        err = capsys.readouterr().err
        assert "报告输出" in err and fallback in err
        assert "白名单" in err, "必须告诉用户怎么修，否则只能猜"

    def test_when_nothing_is_writable_it_keeps_the_configured_path(
        self, monkeypatch, tmp_path
    ):
        """这样才能让报错指向用户期望的路径，而不是一个陌生的回退目录。"""
        def denied(path, flags, mode=0o777):
            raise PermissionError(13, "Permission denied", path)

        monkeypatch.setattr(default_config.os, "open", denied)
        preferred = str(tmp_path / "preferred")

        assert default_config._resolve_state_dir(
            preferred, str(tmp_path / "fallback"), "测试"
        ) == preferred


@pytest.mark.unit
class TestTheShippedConfigExposesTheFallback:
    def test_it_carries_the_legacy_and_the_flag(self):
        cfg = default_config.DEFAULT_CONFIG

        assert "legacy_results_dir" in cfg
        assert isinstance(cfg["results_dir_fell_back"], bool)

    def test_the_three_state_paths_are_always_set(self):
        cfg = default_config.DEFAULT_CONFIG
        for key in ("results_dir", "data_cache_dir", "memory_log_path"):
            assert cfg[key], f"{key} 为空——配置没解析出来"


@pytest.mark.unit
class TestHistoryReadsBothDirectories:
    def test_reports_from_the_legacy_directory_stay_visible(
        self, monkeypatch, tmp_path
    ):
        """回归：只读回退目录 = 用户以为历史被删了。"""
        import web.history as history

        legacy = tmp_path / "legacy"
        fallback = tmp_path / "fallback"
        for root, ticker in ((legacy, "600519"), (fallback, "600577")):
            d = root / ticker / "marvel_strategy_logs"
            d.mkdir(parents=True)
            (d / "full_states_log_2026-09-30.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(
            history, "DEFAULT_CONFIG",
            {"results_dir": str(fallback), "legacy_results_dir": str(legacy)},
        )

        entries = history.get_history()

        assert {e["ticker"] for e in entries} == {"600519", "600577"}

    def test_a_shared_directory_is_not_listed_twice(self, monkeypatch, tmp_path):
        import web.history as history

        d = tmp_path / "only" / "600519" / "marvel_strategy_logs"
        d.mkdir(parents=True)
        (d / "full_states_log_2026-09-30.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(
            history, "DEFAULT_CONFIG",
            {"results_dir": str(tmp_path / "only"), "legacy_results_dir": str(tmp_path / "only")},
        )

        assert len(history.get_history()) == 1


@pytest.mark.unit
class TestTheFailingWritePathsNowSucceed:
    """The three writes that produced `分析失败: [WinError 5]`."""

    def test_the_ticker_log_directory_and_state_file(self, tmp_path, monkeypatch):
        from marvel.dataflows.utils import atomic_write_text

        directory = tmp_path / "logs" / "600577" / "marvel_strategy_logs"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "full_states_log_2026-09-30.json"

        atomic_write_text(str(target), '{"ok": true}')

        assert target.read_text(encoding="utf-8") == '{"ok": true}'

    def test_the_incomplete_task_index(self, monkeypatch, tmp_path):
        import web.history as history
        from marvel.dataflows.utils import atomic_write_text  # noqa: F401

        target = tmp_path / "incomplete_tasks.json"
        monkeypatch.setattr(history, "_INCOMPLETE_TASKS_FILE", target)

        history.record_incomplete_task(
            "600577", "2026-09-30", status="running", completed_stages=[]
        )

        assert target.exists()
        assert "600577" in target.read_text(encoding="utf-8")


def test_module_survives_a_reload_with_a_refused_home(monkeypatch, tmp_path):
    """Import-time resolution must not raise, whatever the filesystem says."""
    def denied(path, flags, mode=0o777):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(default_config.os, "open", denied)
    monkeypatch.setenv("MARVEL_RESULTS_DIR", str(tmp_path / "denied"))

    reloaded = importlib.reload(default_config)
    try:
        assert reloaded.DEFAULT_CONFIG["results_dir"] == str(tmp_path / "denied")
    finally:
        monkeypatch.undo()
        importlib.reload(default_config)
