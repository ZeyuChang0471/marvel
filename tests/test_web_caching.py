"""Guards for caching the Web UI's expensive render work.

Streamlit re-executes the whole script on every widget interaction, and while an
analysis is in flight `web/app.py` reruns on a two-second timer. `web/` had **no**
`@st.cache_data` anywhere, so each of those reruns:

* rebuilt the report's PDF — the most expensive thing on the page, since it
  re-runs the mention-normalisation regexes over every report and embeds a CJK
  font — and re-serialised the bytes to the browser;
* re-ran `get_history()`, a full walk of the saved-log tree.

The results are cached, so the behaviour is unchanged and only the repeated work
disappears.

**The export cache was later rebuilt on a cheaper key.** `@st.cache_data` hashes
every argument on every rerun, and the argument here was `final_state` — the entire
pipeline state, several hundred KB of nested reports plus LangChain message objects
in `messages`. So the lookup itself became the expensive part on a page that reruns
on every interaction, and `max_entries=8` held up to eight multi-hundred-KB PDFs
pickled besides. It now keys on a cheap report identity (saved path, or run
identity) and holds exactly one report; a PDF failure is remembered rather than
retried on every rerun. These tests check that intent, not the decorator.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(*parts: str) -> str:
    return (REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


@pytest.mark.unit
class TestExportsAreCached:
    def test_the_export_cache_is_keyed_on_a_cheap_report_identity(self):
        src = _read("web", "components", "report_viewer.py")
        assert "def _exports(" in src
        assert "cache_key" in src
        assert "_EXPORT_CACHE" in src, "导出没有缓存"

    def test_the_whole_final_state_is_not_a_cache_argument(self):
        """回归：`final_state` 曾被当作 `@st.cache_data` 的参数，每次 rerun 都要
        哈希整个流水线状态——缓存本身成了页面上最贵的一步。

        按行首匹配装饰器，而不是查子串：本文件上面的说明里就写着
        `@st.cache_data`，子串匹配会把注释当成代码（这类误报在这个仓库里已经
        出现过一次）。
        """
        src = _read("web", "components", "report_viewer.py")
        decorators = [
            line.strip() for line in src.splitlines()
            if line.strip().startswith("@st.cache_data")
        ]
        assert decorators == [], f"导出又用回了整状态做缓存键: {decorators}"
        assert "_cached_pdf(final_state" not in src
        assert "_cached_markdown(final_state" not in src

    def test_render_report_uses_the_cache(self):
        src = _read("web", "components", "report_viewer.py")
        assert "exports = _exports(" in src, "结果页没有走缓存"
        assert 'exports["markdown"]' in src
        assert 'exports["pdf"]' in src

    def test_the_raw_generators_are_only_called_inside_the_cache(self):
        src = _read("web", "components", "report_viewer.py")
        assert src.count("generate_pdf(") == 1
        assert src.count("generate_markdown(") == 1

    def test_cached_markdown_matches_the_generator(self):
        """缓存不得改变输出——只是省掉重复计算。"""
        from web.components.report_viewer import _exports
        from web.pdf_export import generate_markdown

        state = {"market_report": "正文", "final_trade_decision": "**Rating**: Hold"}
        cached = _exports(state, "600519", "2026-05-12", "Hold", "unit:1")["markdown"]
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
        """回归：`web/` 曾经一处缓存都没有。

        结果页后来改用按「报告标识」为键的自建缓存（`_EXPORT_CACHE`），所以这里
        数的是两种缓存之和，而不是只数装饰器。
        """
        hits = 0
        for path in (REPO_ROOT / "web").rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            hits += src.count("@st.cache_data") + src.count("_EXPORT_CACHE")
        assert hits >= 3, f"web/ 里的缓存只有 {hits} 处"
