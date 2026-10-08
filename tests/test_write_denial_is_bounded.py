"""A refused file write must be bounded, and must never delay an analysis.

Root cause of the 2026-10-08 "卡死" reports: in a directory where creating new files
is denied, `tempfile.NamedTemporaryFile` does not raise — it retries with a new random
name up to ``TMP_MAX`` (10000) times, because on Windows its ``PermissionError``
handler only needs the *directory* to look writable to `continue`. Measured: a
directory where ``os.open(..., O_CREAT|O_EXCL)`` failed in 0.000s left
``NamedTemporaryFile`` stuck for over 30 seconds, at 100% of one core.

It hit inside ``record_incomplete_task()``, which ``run_analysis_in_thread`` called
**before** starting the worker — so the UI sat on "分析进行中" with 0 LLM calls
forever, and the analysis thread never existed. `incomplete_tasks.json` stayed ``[]``,
which is what gave the game away.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from marvel.dataflows import utils as data_utils


@pytest.mark.unit
class TestBoundedMkstemp:
    def test_a_permission_refusal_is_not_retried(self, monkeypatch, tmp_path):
        """回归：这是那个「满核空转一万次」的坑。"""
        calls = {"n": 0}

        def denied(path, flags, mode=0o777):
            calls["n"] += 1
            raise PermissionError(13, "Permission denied", path)

        monkeypatch.setattr(data_utils.os, "open", denied)

        started = time.time()
        with pytest.raises(PermissionError):
            data_utils.bounded_mkstemp(str(tmp_path), prefix="x.")
        elapsed = time.time() - started

        assert calls["n"] == 1, f"重试了 {calls['n']} 次——权限问题不会因为换名字而变好"
        assert elapsed < 0.5

    def test_a_real_collision_is_retried(self, monkeypatch, tmp_path):
        """名字撞车是真会好起来的，那种情况必须换名重试。"""
        seen: list[str] = []
        real_open = os.open

        def collide_twice(path, flags, mode=0o777):
            seen.append(path)
            if len(seen) <= 2:
                raise FileExistsError(17, "File exists", path)
            return real_open(path, flags, mode)

        monkeypatch.setattr(data_utils.os, "open", collide_twice)

        fd, path = data_utils.bounded_mkstemp(str(tmp_path), prefix="x.")
        os.close(fd)
        os.unlink(path)

        assert len(seen) == 3, "撞车后应当换个名字再试"

    def test_it_gives_up_after_the_attempt_budget(self, monkeypatch, tmp_path):
        def always_collide(path, flags, mode=0o777):
            raise FileExistsError(17, "File exists", path)

        monkeypatch.setattr(data_utils.os, "open", always_collide)

        with pytest.raises(FileExistsError):
            data_utils.bounded_mkstemp(str(tmp_path), prefix="x.", attempts=4)


@pytest.mark.unit
class TestAtomicWriteDegradesInsteadOfHanging:
    def test_it_falls_back_to_a_direct_write(self, monkeypatch, tmp_path, caplog):
        """目录拒绝「新建」但仍允许写已有文件时，宁可丢掉原子性也不能丢数据。"""
        target = tmp_path / "cache.json"
        target.write_text("old", encoding="utf-8")

        def denied(path, flags, mode=0o777):
            raise PermissionError(13, "Permission denied", path)

        monkeypatch.setattr(data_utils.os, "open", denied)

        with caplog.at_level("WARNING"):
            data_utils.atomic_write_text(str(target), "new")

        assert target.read_text(encoding="utf-8") == "new"
        assert "降级" in caplog.text, "降级必须说出来，否则没人知道原子性没了"

    def test_a_successful_write_is_still_atomic(self, tmp_path):
        target = tmp_path / "cache.json"
        data_utils.atomic_write_text(str(target), "content")

        assert target.read_text(encoding="utf-8") == "content"
        assert list(tmp_path.glob("*.tmp")) == [], "留下了临时文件"


@pytest.mark.unit
class TestTheIndexWriteIsOffTheCriticalPath:
    def test_it_returns_quickly_when_writes_are_denied(self, monkeypatch):
        from web import history

        def denied(*args, **kwargs):
            raise PermissionError(13, "Permission denied", "incomplete_tasks.json")

        monkeypatch.setattr(history, "bounded_mkstemp", denied)
        monkeypatch.setattr(
            history.Path, "write_text", denied, raising=False
        )

        started = time.time()
        history.record_incomplete_task(
            "600487", "2026-09-30", status="running", completed_stages=[]
        )
        elapsed = time.time() - started

        assert elapsed < 3, f"索引写入花了 {elapsed:.1f}s——它不该拖住调用方"
        assert elapsed < 3

    def test_the_worker_starts_even_if_the_index_write_explodes(self, monkeypatch):
        """回归：这一句曾经在 `threading.Thread().start()` **之前**。"""
        from web import runner
        from web.progress import ProgressTracker

        ran = {"worker": False}

        def explode(*args, **kwargs):
            raise PermissionError(13, "Permission denied", "incomplete_tasks.json")

        def fake_run(ticker, trade_date, config, tracker):
            ran["worker"] = True
            tracker.mark_complete({"ok": True}, "Hold")

        monkeypatch.setattr(runner, "record_incomplete_task", explode)
        monkeypatch.setattr(runner, "_run", fake_run)

        tracker = ProgressTracker()
        thread = runner.run_analysis_in_thread("600487", "2026-09-30", {}, tracker)
        thread.join(timeout=5)

        assert ran["worker"], "索引写入失败把分析线程也带走了"
        assert tracker.is_complete, "分析应当正常跑完"

    def test_the_worker_is_already_started_when_the_index_is_written(self, monkeypatch):
        """顺序必须是先起线程、后写盘——反了就又回到「界面卡死」那条路。"""
        from web import runner
        from web.progress import ProgressTracker

        observed = {}

        def check(*args, **kwargs):
            # 记录此刻是否已有存活的 worker 线程
            observed["alive"] = any(
                t.name.startswith("Thread-") and t.is_alive()
                for t in __import__("threading").enumerate()
            )

        def fake_run(ticker, trade_date, config, tracker):
            time.sleep(0.2)
            tracker.mark_complete({}, "Hold")

        monkeypatch.setattr(runner, "record_incomplete_task", check)
        monkeypatch.setattr(runner, "_run", fake_run)

        tracker = ProgressTracker()
        thread = runner.run_analysis_in_thread("600487", "2026-09-30", {}, tracker)
        thread.join(timeout=5)

        assert observed.get("alive") is True, "写索引时分析线程还没起来——顺序反了"
