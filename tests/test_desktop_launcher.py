"""Guards for the Windows desktop launcher.

The launcher replaces `MARVEL.bat`, which refused to start when port 8501 was busy
— unhelpful when the thing listening *is* MARVEL — and left no log behind, so the
2026-10-08 hang had to be diagnosed without the server's stderr.

It is a small C# program compiled by the .NET Framework compiler that ships with
Windows (`scripts/build_desktop_launcher.ps1`), because the network was down and
PyInstaller could not be installed. Two encoding bugs were found while building it,
and both are guarded here because both are invisible until a user sees mojibake or a
script that will not parse.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_DIR = REPO_ROOT / "scripts" / "desktop_launcher"
SOURCE = LAUNCHER_DIR / "MarvelLauncher.cs"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_desktop_launcher.ps1"
ICON_SCRIPT = LAUNCHER_DIR / "make_icon.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_code(path: Path) -> str:
    """Source with comments removed.

    Structural guards in this repository have twice been fooled by explanatory prose
    (a comment saying "no PyInstaller" satisfied a "must not mention PyInstaller"
    check, and `@st.cache_data` named in a comment looked like a live decorator), so
    everything asserted here runs on comment-stripped text.
    """
    text = _read(path)
    text = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)   # block comments
    lines = [
        line for line in text.splitlines()
        if not line.strip().startswith(("#", "//"))
    ]
    return "\n".join(lines)


@pytest.mark.unit
class TestLauncherSource:
    def test_the_source_and_build_script_exist(self):
        assert SOURCE.exists(), "启动器源码没了"
        assert BUILD_SCRIPT.exists(), "构建脚本没了"
        assert ICON_SCRIPT.exists(), "图标生成脚本没了"

    def test_it_does_not_force_a_utf8_console(self):
        """回归：第一版设了 `Console.OutputEncoding = UTF8`。

        在中文 Windows 上控制台是代码页 936：强制 UTF-8 会把每一行中文变成乱码，
        而 stdout 被重定向时（`>`、日志、`Get-Content`）产生的是 UTF-8 字节、读取端
        按 ANSI 解码，同样是乱码。实测确认过这一点。
        """
        source = _read_code(SOURCE)
        offenders = [
            line.strip() for line in source.splitlines()
            if "Console.OutputEncoding" in line and not line.strip().startswith("//")
        ]
        assert offenders == [], f"又强制控制台编码了: {offenders}"

    def test_the_child_process_output_is_decoded_as_utf8(self):
        """服务端（Python/Streamlit）输出的是 UTF-8，读取端必须照此解码。"""
        source = _read_code(SOURCE)
        assert "StandardOutputEncoding = Encoding.UTF8" in source
        assert "StandardErrorEncoding = Encoding.UTF8" in source

    def test_the_log_file_is_written_as_utf8(self):
        """日志与控制台是两条不同的路：日志显式写 UTF-8，才能被任何工具读。"""
        source = _read_code(SOURCE)
        assert "Encoding.UTF8" in source
        assert "web_ui.log" in source

    def test_it_is_single_instance_aware(self):
        """回归：bat 的做法是「端口被占用就报错退出」，而正确行为是打开浏览器。"""
        source = _read_code(SOURCE)
        assert "_stcore/health" in source, "没有用健康检查区分「自己」和「别人」"
        assert "已经在运行" in source
        assert "PortIsOpen()" in source, "没有区分端口被别的程序占用"

    def test_it_waits_for_readiness_before_opening_the_browser(self):
        """否则用户会先看到一个「无法连接」的页面。"""
        source = _read_code(SOURCE)
        wait_index = source.index("WaitUntilReady(")
        open_index = source.index("OpenBrowser();", wait_index)
        assert wait_index < open_index, "先开浏览器再等服务就绪"

    def test_it_is_c_sharp_5_compatible(self):
        """`csc.exe`（Framework 4.x）不接受 C# 6+ 语法：字符串插值、`?.`、`nameof`。"""
        source = _read_code(SOURCE)
        code_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith("//")
        ]
        code = "\n".join(code_lines)
        assert '$"' not in code, "用了 C# 6 的字符串插值，csc 编译不过"
        assert "?." not in code, "用了 C# 6 的 null 条件运算符"
        assert "nameof(" not in code, "用了 C# 6 的 nameof"


@pytest.mark.unit
class TestBuildScript:
    def test_it_compiles_with_the_bundled_framework_compiler(self):
        script = _read_code(BUILD_SCRIPT)
        assert "csc.exe" in script
        assert "Framework64" in script, "没有优先用 64 位编译器"
        # `pyinstaller` may legitimately appear in the "install .NET Framework 4.x,
        # or package with PyInstaller instead" help text, so what matters is that no
        # line *invokes* it — the build must not need the network.
        invocations = [
            line.strip() for line in script.splitlines()
            if re.match(r"^\s*(&\s*)?(pip\s+install\s+)?pyinstaller\b", line, re.IGNORECASE)
            or re.search(r"Start-Process[^\n]*pyinstaller", line, re.IGNORECASE)
        ]
        assert invocations == [], f"构建脚本调用了需要联网的打包器: {invocations}"

    def test_it_passes_the_utf8_codepage_to_the_compiler(self):
        """源码里有中文；不给 /codepage:65001 就会按 ANSI 读，界面文字变乱码。"""
        script = _read_code(BUILD_SCRIPT)
        assert "/codepage:65001" in script

    def test_it_embeds_the_icon(self):
        script = _read_code(BUILD_SCRIPT)
        assert "/win32icon:" in script
        assert "make_icon.py" in script, "图标没有生成步骤"

    def test_the_old_bat_is_retired_not_deleted(self):
        """用户手写的启动脚本可以退役，但不该被无声删掉。"""
        script = _read_code(BUILD_SCRIPT)
        assert "MARVEL.bat" in script
        assert "Move-Item" in script, "旧的 bat 应改名保留，而不是删除"

    def test_the_script_is_saved_with_a_utf8_bom(self):
        """回归：Windows PowerShell 5.1 只认带 BOM 的 UTF-8。

        没有 BOM 时它按 ANSI(GBK) 读 .ps1，中文全部变成乱码并导致语法错误
        （`编译器` 变成 `缂栬瘧鍣?`），实测踩过。
        """
        raw = BUILD_SCRIPT.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), (
            "build_desktop_launcher.ps1 缺少 UTF-8 BOM——Windows PowerShell 5.1 会把"
            "中文读成乱码并直接语法报错"
        )


@pytest.mark.unit
class TestIconGenerator:
    def test_it_writes_a_real_multi_size_ico(self, tmp_path):
        pillow = pytest.importorskip("PIL", reason="图标生成需要 Pillow")
        assert pillow is not None

        import subprocess
        import sys

        target = tmp_path / "marvel.ico"
        result = subprocess.run(
            [sys.executable, str(ICON_SCRIPT), str(target)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert target.exists()
        assert target.read_bytes()[:4] == b"\x00\x00\x01\x00", "不是 ICO 文件头"

    def test_the_repo_ships_the_generated_icon(self):
        """构建脚本会用到它；缺失时脚本能生成，但仓库里应有一份以便离线构建。"""
        icon = REPO_ROOT / "assets" / "marvel.ico"
        assert icon.exists(), "assets/marvel.ico 缺失"
        assert icon.read_bytes()[:4] == b"\x00\x00\x01\x00"
