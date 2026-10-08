"""Catch "used but never imported" globals — the class of bug that broke the UI.

`web/components/sidebar.py` referenced `DEFAULT_CONFIG` without importing it, so the
whole app rendered a red ``NameError: name 'DEFAULT_CONFIG' is not defined`` instead of
the sidebar. Every existing test passed: nothing renders Streamlit, and `compileall`
only proves the *syntax* is valid, not that a name resolves. The failure was found by
the user, in production, which is the wrong place.

`symtable` (stdlib) reports, per scope, which names are looked up as globals; comparing
those against the module's own symbols and the builtins finds this statically. Modules
with a star import are skipped rather than guessed at — `from marvel.agents import *`
genuinely can supply names no static tool can enumerate, and a noisy guard gets deleted.
"""

from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNED_ROOTS = ("web", "marvel", "cli", "scripts")


def _has_star_import(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                return True
    return False


def find_undefined_globals(path: Path) -> list[str]:
    """Names read as globals that no import, assignment or builtin can provide."""
    source = path.read_text(encoding="utf-8")
    if _has_star_import(source):
        return []          # unverifiable by construction; see the module docstring

    table = symtable.symtable(source, str(path), "exec")
    module_names = {symbol.get_name() for symbol in table.get_symbols()}
    problems: list[str] = []

    def walk(scope) -> None:
        for child in scope.get_children():
            if child.get_type() in ("function", "class"):
                for symbol in child.get_symbols():
                    name = symbol.get_name()
                    if name.startswith("__") and name.endswith("__"):
                        continue          # __file__, __name__, ... come from the runtime
                    if not symbol.is_global() or symbol.is_assigned():
                        continue
                    if name not in module_names and not hasattr(builtins, name):
                        problems.append(f"{path}:{child.get_name()}:{name}")
            walk(child)

    walk(table)
    return problems


@pytest.mark.unit
class TestTheDetectorItself:
    """Guard the guard: a checker that misses the real bug is worse than none."""

    def test_it_catches_the_bug_that_broke_the_sidebar(self, tmp_path):
        module = tmp_path / "fake_sidebar.py"
        module.write_text(
            "import os\n"
            "\n"
            "def render():\n"
            "    return DEFAULT_CONFIG.get('results_dir')\n",
            encoding="utf-8",
        )

        assert find_undefined_globals(module) == [
            f"{module}:render:DEFAULT_CONFIG"
        ]

    def test_it_accepts_an_imported_name(self, tmp_path):
        module = tmp_path / "ok.py"
        module.write_text(
            "from marvel.default_config import DEFAULT_CONFIG\n"
            "\n"
            "def render():\n"
            "    return DEFAULT_CONFIG.get('results_dir')\n",
            encoding="utf-8",
        )

        assert find_undefined_globals(module) == []

    def test_it_accepts_a_module_level_assignment(self, tmp_path):
        module = tmp_path / "ok2.py"
        module.write_text(
            "CACHE = {}\n\ndef get():\n    return CACHE\n", encoding="utf-8"
        )

        assert find_undefined_globals(module) == []

    def test_it_accepts_builtins_and_local_imports(self, tmp_path):
        module = tmp_path / "ok3.py"
        module.write_text(
            "def run(path):\n"
            "    import json\n"
            "    return json.dumps(sorted([len(path)]))\n",
            encoding="utf-8",
        )

        assert find_undefined_globals(module) == []

    def test_a_star_import_makes_the_module_unverifiable_not_guilty(self, tmp_path):
        module = tmp_path / "starred.py"
        module.write_text(
            "from somewhere import *\n\ndef go():\n    return mystery_name()\n",
            encoding="utf-8",
        )

        assert find_undefined_globals(module) == []


@pytest.mark.unit
class TestTheRepositoryIsClean:
    def test_no_module_reads_an_undefined_global(self):
        files = [
            path
            for root in SCANNED_ROOTS
            for path in (REPO_ROOT / root).rglob("*.py")
            if (REPO_ROOT / root).exists()
        ]
        assert files, "没有扫到任何文件——路径写错了？"

        problems = [p for path in sorted(files) for p in find_undefined_globals(path)]

        assert not problems, (
            "这些名字在使用处没有定义，运行到那一行就会 NameError：\n  "
            + "\n  ".join(problems)
        )

    def test_the_sidebar_still_uses_the_flag_it_broke_on(self):
        """回归的具体形状：`render_sidebar` 读 `results_dir_fell_back`。"""
        source = (REPO_ROOT / "web" / "components" / "sidebar.py").read_text(
            encoding="utf-8"
        )

        assert "results_dir_fell_back" in source
        assert "from marvel.default_config import DEFAULT_CONFIG" in source, (
            "又用了 DEFAULT_CONFIG 却没导入——这正是当初让整个界面报 NameError 的原因"
        )
