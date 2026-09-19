"""Guards against documentation drifting away from the code.

This repository suffered a specific, repeated failure: the documents described a
different product than the code shipped.

  * README.md and CLAUDE.md said **7 analysts / 12 stages**; the code registered
    **9 analysts / 14 stages** (`setup.py`, `web/progress.py`).
  * The licence was stated three contradictory ways: `pyproject.toml` carried a
    free-text field claiming a non-commercial licence over the whole
    distribution, `CLAUDE.md` said "Apache 2.0", and `DEV_LOG.md` promised the
    project was commercially usable.
  * The PolyForm file list named 1 file in `NOTICE` and 4 in `LICENSING.md` and
    `LICENSE-TradingAgents-AShare.txt`.
  * The README's config table advertised default models the code does not use.

None of that is caught by ordinary tests, and every one of those items is read
by a human or an agent as a statement of fact. These checks derive the truth
from the code and fail when the prose disagrees.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

try:  # tomllib is stdlib only from 3.11 (PEP 680)
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(*parts: str) -> str:
    return (REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Derived truth
# ---------------------------------------------------------------------------


def _registered_analysts() -> list[str]:
    """Analyst keys registered in the graph topology."""
    src = _read("marvel", "graph", "setup.py")
    return re.findall(r'if "(\w+)" in selected_analysts', src)


def _default_selected_analysts() -> list[str]:
    src = _read("marvel", "graph", "trading_graph.py")
    match = re.search(r"selected_analysts=\[(.*?)\]", src, re.DOTALL)
    assert match, "trading_graph.py: cannot find the selected_analysts default"
    return re.findall(r'"(\w+)"', match.group(1))


def _pipeline_stage_count() -> int:
    src = _read("web", "progress.py")
    return len(re.findall(r'\{"id":\s*"\w+"', src))


def _polyform_components() -> list[str]:
    """The PolyForm-covered file list, taken from LICENSING.md (the authority)."""
    src = _read("LICENSING.md")
    return sorted(re.findall(r"^(marvel/[\w/]+\.py)\s", src, re.M))


# ---------------------------------------------------------------------------
# Analyst and stage counts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCountsMatchTheCode:
    def test_there_really_are_nine_analysts(self):
        registered = _registered_analysts()
        assert len(registered) == 9, f"setup.py registers {len(registered)}: {registered}"
        # The graph default must list exactly the same set.
        assert sorted(_default_selected_analysts()) == sorted(registered)

    def test_there_really_are_fourteen_stages(self):
        assert _pipeline_stage_count() == 14

    @pytest.mark.parametrize("doc", ["README.md", "CLAUDE.md"])
    def test_docs_state_the_right_analyst_count(self, doc):
        """任何「总数」表述都必须是 9。

        允许出现的是**子集**数字（原版 4 个 + A 股特化 5 个），所以只认
        「跟着 Analyst/分析师/角色 的数字」和「角色（N 个）」两种形态。
        第一版只查了前者，`### Agent 角色（7 个）` 这种写法会漏掉。
        """
        src = _read(doc)
        count = str(len(_registered_analysts()))

        for pattern in (
            r"(\d+)\s*个\s*(?:Analyst|分析师|角色)",   # 「7 个 Analyst」「7 个角色」
            r"角色[（(]\s*(\d+)\s*个",                # 「Agent 角色（7 个）」
            r"\*\*(\d+)\s*个\*\*\s*(?:Analyst|分析师)",  # 「**9 个** Analyst」
        ):
            wrong = sorted({n for n in re.findall(pattern, src) if n != count})
            assert not wrong, (
                f"{doc} 声称有 {wrong} 个分析师/角色，代码里是 {count} 个"
                f"（pattern: {pattern}）。改数量时请同步 docs。"
            )

        # 对比表格里的 `**N 个**`——「原版 4 个 / 本 Fork **9 个**」这种写法，
        # 上面三条 pattern 都抓不到（粗体数字后面跟的是括号，不是角色词）。
        for line in src.splitlines():
            if not re.search(r"Analyst|分析师|角色", line):
                continue
            for n in re.findall(r"\*\*(\d+)\s*个\*\*", line):
                assert n == count, (
                    f"{doc} 的表格里写「**{n} 个**」，代码里是 {count} 个：{line.strip()[:80]}"
                )

    def test_docs_actually_state_the_count_somewhere(self):
        """避免上面那条因为「一处都没提」而空过。"""
        count = len(_registered_analysts())
        for doc in ("README.md", "CLAUDE.md"):
            src = _read(doc)
            assert re.search(rf"{count}\s*个\s*(?:Analyst|分析师|角色)", src) or \
                re.search(rf"角色[（(]\s*{count}\s*个", src), (
                f"{doc} 完全没有说明当前有 {count} 个分析师——文档不该对角色规模保持沉默"
            )

    def test_readme_states_the_right_stage_count(self):
        src = _read("README.md")
        count = _pipeline_stage_count()

        stale = re.findall(r"(\d+)\s*阶段", src)
        wrong = [n for n in stale if n != str(count)]
        assert not wrong, (
            f"README.md 声称 {sorted(set(wrong))} 阶段，web/progress.py 里是 {count}"
        )

    def test_no_document_claims_seven_or_twelve(self):
        """最容易复发的两个具体数字，单独钉住。"""
        for doc in ("README.md", "CLAUDE.md"):
            src = _read(doc)
            assert not re.search(r"\b7\s*(?:个\s*)?(?:Analyst|分析师)", src), doc
            assert not re.search(r"\b12\s*(?:个\s*)?阶段", src), doc


# ---------------------------------------------------------------------------
# Licence metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLicenceMetadata:
    def _pyproject(self) -> dict:
        return tomllib.loads(_read("pyproject.toml"))

    def test_license_is_an_spdx_expression_not_free_text(self):
        license_field = self._pyproject()["project"]["license"]
        assert isinstance(license_field, str), (
            "license 必须是 SPDX 表达式字符串（PEP 639）。"
            "TOML 表形式 `{text = ...}` 已废弃，且无法表达混合许可。"
        )

    def test_license_expression_names_both_components(self):
        expr = self._pyproject()["project"]["license"]
        assert "Apache-2.0" in expr, "表达式漏掉了 Apache-2.0 组件"
        assert "PolyForm-Noncommercial-1.0.0" in expr, (
            "表达式漏掉了 PolyForm 组件——那样会掩盖整仓不可商用的事实"
        )

    def test_license_files_are_declared_and_exist(self):
        files = self._pyproject()["project"]["license-files"]
        assert "LICENSE" in files
        assert "LICENSE-TradingAgents-AShare.txt" in files
        assert "NOTICE" in files
        for name in files:
            assert (REPO_ROOT / name).is_file(), f"license-files 指向了不存在的文件: {name}"

    def test_build_requires_setuptools_77_for_pep639(self):
        requires = self._pyproject()["build-system"]["requires"]
        joined = " ".join(requires)
        match = re.search(r"setuptools>=(\d+)", joined)
        assert match, f"build-system.requires 未固定 setuptools 下限: {requires}"
        assert int(match.group(1)) >= 77, (
            "PEP 639 的 license 表达式与 license-files 需要 setuptools>=77，"
            f"当前是 {match.group(0)}"
        )

    def test_no_unsatisfiable_google_extra_is_advertised(self):
        """不能声明一个 pip 解析不出来的 extra。

        `google = ["langchain-google-genai>=4.0.0"]` 曾是死元数据：mootdx 锁
        `httpx>=0.25,<0.26`，而它要求 `httpx>=0.28.1`，CI 也从没装过这个 extra。
        `google_client.py` 现在直接给出显式安装命令，元数据不应再承诺一个 extra。
        """
        extras = self._pyproject()["project"].get("optional-dependencies", {})
        assert "google" not in extras, (
            "pyproject.toml 又声明了 google extra——它与 mootdx 的 httpx 约束不可共存。"
            "如需恢复，请先解决该冲突并让 CI 真的安装它。"
        )

    def test_google_guidance_in_docs_matches_the_code(self):
        """文档里的 Gemini 安装说明必须与代码抛出的信息一致。

        代码给的是「--no-deps + 显式装 httpx」，文档一度写的是
        `pip install -e ".[google]"`——一个根本装不上的命令。测试里的
        `importorskip` 跳过原因也曾这么写（那边更容易漏掉）。
        """
        client_src = _read("marvel", "llm_clients", "google_client.py")
        assert "httpx>=0.28.1" in client_src, "代码必须给出可用的安装命令"

        for doc in ("README.md", "CLAUDE.md"):
            src = _read(doc)
            assert "httpx>=0.28.1" in src, (
                f"{doc} 提到 Gemini 却没给出代码里那条可用的安装命令"
            )

    def test_nothing_recommends_the_nonexistent_google_extra(self):
        """当前状态的文档与测试，不得再给出「用 .[google] 安装」的可执行推荐。

        这个 extra 已从 pyproject.toml 删除（与 mootdx 的 httpx 约束冲突）。
        只查 `pip install … .[google]` 这种**可执行**写法：
          * 「没有 `[google]` extra」这类说明性文字是允许的；
          * `CHANGELOG.md` 里的历史条目（0.2.6 当时确实有这个 extra）不改写，
            所以不在此检查范围内。
        """
        targets = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "CLAUDE.md",
            REPO_ROOT / "tests" / "test_google_api_key.py",
        ]

        offenders = []
        for path in targets:
            assert path.is_file(), f"检查目标缺失: {path}"
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if ".[google]" in line and "pip install" in line:
                    offenders.append(f"{path.name}:{lineno}: {line.strip()[:100]}")

        assert not offenders, (
            "仍在推荐不存在的 [google] extra：\n  " + "\n  ".join(offenders)
        )

    def test_no_document_claims_plain_apache_2_0_as_the_project_licence(self):
        """CLAUDE.md 曾写「协议: Apache 2.0」，与混合许可直接冲突。"""
        for doc in ("README.md", "CLAUDE.md"):
            src = _read(doc)
            assert not re.search(
                r"协议\**:\s*\**Apache\s*2\.0\b(?!\s*[+＋])", src
            ), f"{doc} 把项目协议写成纯 Apache 2.0，与 LICENSING.md 冲突"

    def test_no_document_promises_commercial_usability(self):
        """DEV_LOG.md 曾承诺「可商用」，自引入 PolyForm 组件后已不成立。"""
        for doc in ("README.md", "CLAUDE.md", "DEV_LOG.md"):
            src = _read(doc)
            for line in src.splitlines():
                if "可商用" not in line:
                    continue
                # 只允许出现在「澄清不再成立」的上下文里
                assert any(
                    marker in line
                    for marker in ("不", "删", "~~", "无效", "并非")
                ), f"{doc} 里仍有无条件「可商用」表述: {line.strip()}"

    def test_polyform_component_list_is_identical_everywhere(self):
        """Required Notice 的文件清单在三个文件里必须完全一致。

        之前 NOTICE 只列了 trade_calendar.py，而 LICENSING.md 与
        LICENSE-TradingAgents-AShare.txt 列了 4 个——分发的许可证正文列了
        NOTICE 没列的受约束文件。
        """
        authority = _polyform_components()
        assert len(authority) == 4, f"LICENSING.md 的 PolyForm 清单异常: {authority}"

        for doc in ("NOTICE", "LICENSE-TradingAgents-AShare.txt"):
            src = _read(doc)
            missing = [c for c in authority if c not in src]
            assert not missing, (
                f"{doc} 的 Required Notice 漏了这些 PolyForm 文件: {missing}"
            )


# ---------------------------------------------------------------------------
# Config table
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadmeConfigTable:
    def test_default_provider_and_models_match_default_config(self):
        """README 的配置表曾写 minimax 默认值，而 default_config.py 是 openai。"""
        defaults = _read("marvel", "default_config.py")
        readme = _read("README.md")

        for key in ("llm_provider", "deep_think_llm", "quick_think_llm"):
            match = re.search(rf'"{key}":\s*"([^"]+)"', defaults)
            assert match, f"default_config.py 找不到 {key}"
            value = match.group(1)
            row = re.search(rf"\|\s*`{key}`\s*\|\s*`\"([^\"]+)\"`", readme)
            assert row, f"README 的配置表缺少 {key} 行"
            assert row.group(1) == value, (
                f"README 写 {key} = {row.group(1)}，代码里是 {value}"
            )


# ---------------------------------------------------------------------------
# Packaging hygiene
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPackagingHygiene:
    def test_declared_dependencies_cover_the_direct_third_party_imports(self):
        """pydantic 与 python-dateutil 曾被直接 import 却没有声明。

        它们只是碰巧由 langchain-core / pandas 传递带入；上游一次重构就会让
        `pip install -e .` 装出一个跑不起来的包。
        """
        declared = " ".join(
            tomllib.loads(_read("pyproject.toml"))["project"]["dependencies"]
        ).lower()
        for dist in ("pydantic", "python-dateutil"):
            assert dist in declared, f"{dist} 被直接 import 但未在 pyproject 中声明"

    def test_no_unused_runtime_dependencies(self):
        """反向检查：声明了却从不 import 的依赖同样是元数据与代码脱节。"""
        import ast

        declared = tomllib.loads(_read("pyproject.toml"))["project"]["dependencies"]
        # 分发名 -> import 名（与这里不同时列出来）
        import_name = {
            "langchain-core": "langchain_core",
            "langchain-anthropic": "langchain_anthropic",
            "langchain-openai": "langchain_openai",
            "langgraph-checkpoint-sqlite": "langgraph",
            "python-dateutil": "dateutil",
            "python-dotenv": "dotenv",
            "fpdf2": "fpdf",
            "typing-extensions": "typing_extensions",
        }

        top_level: set[str] = set()
        for root in ("marvel", "cli", "web", "scripts", "examples", "tests"):
            for path in (REPO_ROOT / root).rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        top_level.update(a.name.split(".")[0] for a in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                        top_level.add(node.module.split(".")[0])

        unused = []
        for spec in declared:
            dist = re.split(r"[<>=!\[; ]", spec, maxsplit=1)[0].strip()
            module = import_name.get(dist, dist.replace("-", "_"))
            if module not in top_level:
                unused.append(dist)

        assert not unused, (
            f"这些依赖被声明为运行时依赖但代码里从未 import: {unused}。"
            "请从 pyproject.toml 删除（构建期依赖应放 build-system）。"
        )


# ---------------------------------------------------------------------------
# External facts that rot
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoStaleExternalFacts:
    def test_no_hardcoded_upstream_star_count(self):
        """上游 star 数不得写死。

        README 曾写「65K ⭐」，而实测已 **107,495**（GitHub API）——一个每天都在
        变、且与本仓库代码毫无关系的数字，写进文档只会固定地过期，用户还得自己
        去核对。改用实时 shields 徽章（它自己会更新）。
        """
        pattern = re.compile(r"\d[\d,]*\s*[Kk]?\s*(?:⭐|Stars?\b)")

        offenders: list[str] = []
        for doc in ("README.md", "CLAUDE.md"):
            for lineno, line in enumerate(_read(doc).splitlines(), 1):
                if "shields.io" in line:
                    continue          # 实时徽章：数字不由仓库维护
                for match in pattern.finditer(line):
                    offenders.append(f"{doc}:{lineno}: {match.group(0).strip()!r}")

        assert not offenders, (
            "文档里写死了 star 数，请改用 shields.io 实时徽章：\n  "
            + "\n  ".join(offenders)
        )

    def test_donation_solicitation_was_removed(self):
        """README 不再向上游作者募捐（对方也未要求，且与免责定位无关）。"""
        src = _read("README.md")
        for marker in ("支持上游原作者", "赞赏码", "Buy Me a Coffee", "爱发电", "ifdian.net"):
            assert marker not in src, f"README 仍包含捐赠相关内容: {marker}"

        # 赞赏码图片本身也随该节删除；否则仓库里会留一个没人引用的捐赠资产。
        sponsor_img = REPO_ROOT / "assets" / "wechat-sponsor.jpg"
        assert not sponsor_img.exists(), (
            f"{sponsor_img.relative_to(REPO_ROOT)} 已不再被任何文档引用，应一并删除"
        )

    def test_upstream_attribution_is_still_present(self):
        """删掉募捐段不等于删掉归属：Apache-2.0 §4 要求随分发保留。"""
        readme = _read("README.md")
        assert "simonlin1212/tradingagents-astock" in readme
        assert "TauricResearch/TradingAgents" in readme

        notice = _read("NOTICE")
        assert "TauricResearch" in notice and "simonlin1212" in notice
        assert "KylinMountain" in notice

        licensing = _read("LICENSING.md")
        assert "TauricResearch" in licensing and "KylinMountain" in licensing


# ---------------------------------------------------------------------------
# Prompt facts must agree with each other
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPromptFactsAgree:
    def test_no_prompt_still_cites_the_old_st_five_percent_band(self):
        """主板 ST/*ST 自 2026-07-06 起是 ±10%，不再是 ±5%。

        `conservative_debator.py` 曾写「ST designation triggers ±5% price
        limits」，而 `portfolio_manager.py` / `trader.py` / `market_analyst.py` /
        `volume_price_analyst.py` 四处都写明了已改为 ±10%。这段陈旧文本每次
        运行都会被原样喂给保守风控分析师——报告里看不出来，但它会据此论证。
        """
        offenders = []
        for path in (REPO_ROOT / "marvel" / "agents").rglob("*.py"):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if "5%" not in line:
                    continue
                if "ST" not in line and "风险警示" not in line:
                    continue
                # 允许出现在「已由 ±5% 调整为 ±10%」这类更正说明中
                if "10%" in line:
                    continue
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()[:110]}"
                )

        assert not offenders, (
            "有提示词仍在单独引用 ST 的旧 ±5% 涨跌幅：\n  " + "\n  ".join(offenders)
        )

    def test_quality_gate_prompt_covers_every_graded_analyst(self):
        """门控 prompt 的「N 位分析师」与输出表格必须覆盖所有被评分的角色。

        它们曾经写死成 7，而循环评 9 个——量价与宏观分析师永远不会被打分，
        但下游每个辩手都被告知「C/D/F 的报告要降低依赖」。
        """
        from marvel.agents.quality_gate import (
            ANALYST_NAMES,
            REPORT_FIELDS,
            _build_review_prompt,
        )

        prompt = _build_review_prompt({}, "2026-05-12", "600519")

        assert f"{len(REPORT_FIELDS)} 位分析师" in prompt, (
            "prompt 里声明的分析师数量与 REPORT_FIELDS 不一致"
        )
        for key, name in ANALYST_NAMES.items():
            assert name in prompt, f"门控 prompt 漏了 {name}（{key}）——它不会被审核"

        # 表格行数也要对得上：只排除表头行与 |---| 分隔行。
        # （不能按「含 分析师 |」过滤——每一行的角色名都以「分析师」结尾。）
        table_lines = [
            line for line in prompt.splitlines()
            if line.startswith("| ")
            and not line.startswith("| 分析师 |")
            and "---" not in line
        ]
        assert len(table_lines) == len(REPORT_FIELDS), (
            f"输出表格只有 {len(table_lines)} 行，应为 {len(REPORT_FIELDS)} 行"
        )


# ---------------------------------------------------------------------------
# Python 3.10 compatibility
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPython310Compatibility:
    """`requires-python = ">=3.10"` 是承诺，CI 的 3.10 那条腿是唯一的执行者。

    `tomllib` 只在 3.11+ 存在（PEP 680）。`tests/test_docs_consistency.py` 一开始
    无条件 import 它，结果是**只有 Python 3.10 的 CI 失败**（pytest 退出码 2，
    收集期报错），3.11/3.12/3.13 全绿——一个本地跑多少次都看不到的失败。
    """

    #: stdlib 模块 -> 引入它的最低 Python 版本
    _MIN_VERSION = {
        "tomllib": (3, 11),
    }

    def _python_files(self) -> list[Path]:
        skip = {"venv", "__pycache__", ".pytest_tmp", ".git"}
        paths: list[Path] = []
        for root in ("marvel", "cli", "web", "scripts", "examples", "tests"):
            paths += list((REPO_ROOT / root).rglob("*.py"))
        paths += [REPO_ROOT / name for name in ("run.py", "run_single.py")]
        return [
            p for p in paths
            if p.exists() and not any(part in skip for part in p.parts)
        ]

    @staticmethod
    def _imports(tree: "ast.Module") -> list[tuple[str, int, bool]]:
        """Yield ``(top_level_module, lineno, guarded_by_ImportError)``."""
        import ast

        guard_names = {"ImportError", "ModuleNotFoundError"}

        def handles_import_error(node: ast.Try) -> bool:
            for handler in node.handlers:
                node_type = handler.type
                if isinstance(node_type, ast.Name) and node_type.id in guard_names:
                    return True
                if isinstance(node_type, ast.Tuple) and any(
                    isinstance(e, ast.Name) and e.id in guard_names
                    for e in node_type.elts
                ):
                    return True
            return False

        found: list[tuple[str, int, bool]] = []

        def visit(node: ast.AST, guarded: bool) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.Try):
                    inner = guarded or handles_import_error(child)
                    for sub in child.body:
                        visit(sub, inner)
                    for sub in child.orelse + child.finalbody:
                        visit(sub, guarded)
                    for handler in child.handlers:
                        for sub in handler.body:
                            visit(sub, guarded)
                    continue
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        found.append(
                            (alias.name.split(".")[0], child.lineno, guarded)
                        )
                elif (
                    isinstance(child, ast.ImportFrom)
                    and child.module
                    and child.level == 0
                ):
                    found.append((child.module.split(".")[0], child.lineno, guarded))
                visit(child, guarded)

        visit(tree, False)
        return found

    def test_no_python_311_only_stdlib_import_without_a_fallback(self):
        """3.11+ 才有的 stdlib 模块必须包在 try/except ImportError 里。"""
        import ast

        offenders = []
        for path in self._python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module, lineno, guarded in self._imports(tree):
                if module in self._MIN_VERSION and not guarded:
                    needed = ".".join(str(v) for v in self._MIN_VERSION[module])
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: "
                        f"import {module}（需要 Python {needed}，未加 try/except 回退）"
                    )

        assert not offenders, (
            "这些 import 在 Python 3.10 上会直接让测试收集失败：\n  "
            + "\n  ".join(offenders)
        )

    def test_all_sources_parse_under_python_310_grammar(self):
        """语法层也不能用 3.10 之后才有的写法。"""
        import ast

        offenders = []
        for path in self._python_files():
            try:
                ast.parse(
                    path.read_text(encoding="utf-8"),
                    filename=str(path),
                    feature_version=(3, 10),
                )
            except SyntaxError as exc:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{exc.lineno}: {exc.msg}"
                )

        assert not offenders, (
            "这些文件不符合 Python 3.10 语法：\n  " + "\n  ".join(offenders)
        )

    def test_the_tomllib_fallback_is_actually_installed_for_310(self):
        """回退分支要真的可装：dev extra 必须带 3.10 的 tomli。"""
        dev = self._pyproject()["project"]["optional-dependencies"]["dev"]
        joined = " ".join(dev)
        assert "tomli" in joined, (
            "tests 依赖 tomllib（3.11+），dev extra 必须为 3.10 提供 tomli 回退"
        )
        assert 'python_version < "3.11"' in joined, (
            "tomli 必须带 python_version 标记，否则会在 3.11+ 上白装一个包"
        )

    def _pyproject(self) -> dict:
        return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRepoLayout:
    def test_root_scripts_do_nothing_expensive_at_import(self):
        """根目录的 .py 在 import 时不得发起真实调用。

        根目录曾同时存在四个这类文件：`test_astock.py`（模块层跑整条管线并打真实
        Kimi 端点）、`test_data_quality.py`（模块层循环打 17 个真实接口）、`test.py`
        （模块层打 yfinance）和 `main.py`（模块层跑一次 NVDA 分析 + 真实 LLM）。
        pytest 收集、IDE 打开文件、`python -c "import ..."` 都会真的花钱。

        这里用 AST 检查「不处于任何函数/类体内、也不在 __main__ 守卫内」的调用，
        而不是 import 这些文件——`run.py` 在模块层重配 stdout，import 它会污染测试进程。
        """
        import ast

        # import 时会真的出去的调用（简名，或「模块.函数」限定名）
        denied = {
            # 管线 / 数据层入口
            "propagate", "MarvelGraph", "TradingAgentsGraph",
            "prepare_graph_run", "finalize_graph_run",
            "get_stock_data", "get_stock_stats_indicators_window",
            "get_news", "get_global_news", "get_fundamentals",
            "get_stock_stats", "get_balance_sheet", "get_cashflow",
            "get_income_statement", "get_insider_transactions",
            "get_profit_forecast", "get_hot_stocks", "get_northbound_flow",
            "get_concept_blocks", "get_fund_flow", "get_dragon_tiger_board",
            "get_lockup_expiry", "get_industry_comparison", "get_indicators",
            # 网络
            "Ticker", "download", "urlopen",
            "requests.get", "requests.post", "requests.request",
            "_requests.get", "_requests.post", "_requests.request",
            "session.get", "session.post", "_EM_SESSION.get",
            # 进程级副作用：import 一个模块不该结束进程或吞掉 stdin
            "sys.exit", "exit", "getpass.getpass", "getpass",
        }

        def call_name(node: ast.Call) -> str | None:
            """Return the call's name, qualified only when the receiver is a plain name.

            Qualifying means `os.environ.get(...)` is not mistaken for a network
            call while `requests.get(...)` still is.
            """
            func = node.func
            if isinstance(func, ast.Name):
                return func.id
            if isinstance(func, ast.Attribute):
                receiver = func.value
                if isinstance(receiver, ast.Name):
                    return f"{receiver.id}.{func.attr}"
            return None

        def is_main_guard(node: ast.If) -> bool:
            test = node.test
            return (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and any(
                    isinstance(c, ast.Constant) and c.value == "__main__"
                    for c in test.comparators
                )
            )

        offenders: list[str] = []

        def walk(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    continue                      # 函数/类体 import 时不执行
                if isinstance(child, ast.If) and is_main_guard(child):
                    continue                      # __main__ 守卫内不执行
                if isinstance(child, ast.Call):
                    name = call_name(child)
                    if name in denied:
                        offenders.append(
                            f"{current.name}:{child.lineno}: {name}(…)"
                        )
                if isinstance(child, (ast.For, ast.While)):
                    # 模块层的循环几乎必然是「import 就干活」。这条补充规则存在
                    # 是因为间接调用骗得过按名字的检查：曾经的
                    # test_data_quality.py 就是 `for name, fn in TESTS: fn()`
                    # ——被调用的是一个变量，静态看不出它其实是 17 个真实接口。
                    offenders.append(
                        f"{current.name}:{child.lineno}: 模块层的循环体"
                    )
                walk(child)

        for path in sorted(REPO_ROOT.glob("*.py")):
            current = path
            with path.open(encoding="utf-8") as handle:
                walk(ast.parse(handle.read(), filename=str(current)))

        assert not offenders, (
            "这些根目录脚本在 import 时就会发起真实调用：\n  "
            + "\n  ".join(offenders)
            + "\n请移到 scripts/ 并放进 __main__ 守卫内。"
        )

    def test_repo_root_has_no_orphan_demo_script(self):
        """根目录只应有带守卫的入口脚本。"""
        allowed = {"run.py", "run_single.py"}
        present = {p.name for p in REPO_ROOT.glob("*.py")}
        unexpected = sorted(present - allowed)
        assert not unexpected, (
            f"根目录出现了预期外的 .py: {unexpected}。"
            "手工脚本请放 scripts/，库代码请放包内。"
        )

    def test_manual_probes_are_inert_on_import(self):
        """scripts/ 下的探针 import 时必须什么都不做（否则 IDE 打开就花钱）。"""
        import importlib.util

        for name in (
            "probe_astock_e2e",
            "probe_data_quality",
            "probe_yfinance_indicators",
        ):
            path = REPO_ROOT / "scripts" / f"{name}.py"
            assert path.is_file(), f"缺少手工探针: {path}"
            src = path.read_text(encoding="utf-8")
            assert 'if __name__ == "__main__":' in src, (
                f"{name}.py 缺少 __main__ 守卫——import 时会执行"
            )
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # 不应发起任何网络/LLM 调用
            assert callable(module.main)

    def test_examples_runner_redacts_before_writing(self):
        src = _read("examples", "run_cases.py")
        assert "redact_executable_levels" in src
        assert "redact_executable_levels(full_decision)" in src, (
            "run_cases.py 必须在落盘前对决策文本脱敏，否则示例产物会重新带上可执行价位"
        )
