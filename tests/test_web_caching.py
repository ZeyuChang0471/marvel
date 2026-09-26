"""Guards for caching the Web UI's expensive render work.

Streamlit re-executes the whole script on every widget interaction, and while an
analysis is in flight `web/app.py` reruns on a two-second timer. `web/` had **no**
`@st.cache_data` anywhere, so each of those reruns:

* rebuilt the report's PDF — the most expensive thing on the page, since it
  re-runs the mention-normalisation regexes over every report and embeds a CJK
  font — and re-serialised the bytes to the browser;
* re-ran `get_history()`, a full walk of the saved-log tree.

The results are cached on identical inputs, so the behaviour is unchanged and
only the repeated work disappears.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(*parts: str) -> str:
    return (REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


@pytest.mark.unit
class TestExportsAreCached:
    def test_the_cached_wrappers_exist_and_are_decorated(self):
        src = _read("web", "components", "report_viewer.py")
        assert src.count("@st.cache_data") >= 2, "导出没有缓存装饰器"
        assert "def _cached_pdf(" in src
        assert "def _cached_markdown(" in src

    def test_render_report_calls_the_cached_wrappers(self):
        src = _read("web", "components", "report_viewer.py")
        assert "_cached_pdf(final_state" in src, "结果页仍在直接生成 PDF"
        assert "_cached_markdown(final_state" in src, "结果页仍在直接生成 Markdown"

    def test_the_raw_generators_are_only_called_inside_the_wrappers(self):
        src = _read("web", "components", "report_viewer.py")
        assert src.count("return generate_pdf(") == 1
        assert src.count("return generate_markdown(") == 1

    def test_cached_markdown_matches_the_generator(self):
        """缓存不得改变输出——只是省掉重复计算。"""
        from web.components.report_viewer import _cached_markdown
        from web.pdf_export import generate_markdown

        state = {"market_report": "正文", "final_trade_decision": "**Rating**: Hold"}
        cached = _cached_markdown(state, "600519", "2026-05-12", "Hold")
        direct = generate_markdown(state, "600519", "2026-05-12", "Hold")

        assert cached == direct


@pytest.mark.unit
class TestHistoryScanIsCached:
    def test_sidebar_caches_the_history_scan(self):
        src = _read("web", "components", "sidebar.py")
        assert "@st.cache_data" in src, "侧边栏没有缓存任何东西"
        assert "def _cached_history(" in src
        assert "_cached_history()" in src, "侧边栏仍在每次 rerun 全量扫描历史目录"

    def test_the_ttl_is_short_enough_to_stay_useful(self):
        """TTL 太长的话，刚跑完的分析会在侧边栏里“消失”一段时间。"""
        import re

        src = _read("web", "components", "sidebar.py")
        match = re.search(r"@st\.cache_data\(ttl=(\d+)", src)
        assert match, "历史缓存没有设置 TTL"
        assert int(match.group(1)) <= 30, (
            f"历史缓存的 TTL 是 {match.group(1)}s，太长了——新完成的报告会被隐藏"
        )


@pytest.mark.unit
class TestCachingIsAppliedWhereItWasMissing:
    def test_web_has_caching_at_all(self):
        """回归：`web/` 曾经一处 `@st.cache_data` 都没有。"""
        hits = 0
        for path in (REPO_ROOT / "web").rglob("*.py"):
            hits += path.read_text(encoding="utf-8").count("@st.cache_data")
        assert hits >= 3, f"web/ 里的缓存装饰器只有 {hits} 处"
