"""Guards for release metadata: version, notes, dist name, workflows."""

from __future__ import annotations

from pathlib import Path

try:  # tomllib is stdlib only from 3.11 (PEP 680); the project supports 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


@pytest.mark.unit
class TestReleaseMetadata:
    def test_pypi_name_is_distinct_from_import_name(self):
        """PyPI 上的 `marvel` 已可能被占用；分发名与导入名分开。"""
        assert _project()["name"] == "marvel-astock"
        assert (ROOT / "marvel" / "__init__.py").is_file()

    def test_current_version_has_release_notes_and_changelog_section(self):
        version = _project()["version"]
        assert (ROOT / "docs" / "releases" / f"v{version}.md").is_file(), (
            f"缺少 v{version} 的发布说明"
        )
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert f"## [{version}]" in changelog

    def test_release_workflow_matches_tag_to_version(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        assert "GITHUB_REF_NAME" in workflow
        assert "twine check" in workflow
        assert "gh release create" in workflow
        assert "pypa/gh-action-pypi-publish" in workflow

    def test_dev_extra_has_lint_and_coverage(self):
        dev = " ".join(_project()["optional-dependencies"]["dev"])
        assert "ruff" in dev
        assert "pytest-cov" in dev

    def test_community_files_exist(self):
        for name in ("SECURITY.md", "CONTRIBUTING.md", "RELEASING.md"):
            assert (ROOT / name).is_file(), name
        for name in ("bug_report.yml", "feature_request.yml", "config.yml"):
            assert (ROOT / ".github" / "ISSUE_TEMPLATE" / name).is_file(), name
        assert (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").is_file()

    def test_issue_templates_are_valid_yaml(self):
        yaml = pytest.importorskip("yaml")
        for path in sorted((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml")):
            yaml.safe_load(path.read_text(encoding="utf-8"))
