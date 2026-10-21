"""Guards for MARVEL's visual language (``web/theme.py``).

The UI used to be near-black + a full-red sidebar + a gradient hero card + emoji
section titles — the generic generated-dashboard look. It is now a "night research
desk": ink/paper, a cinnabar rating seal, Songti for report prose, hairline rules.
These tests pin the decisions that would otherwise drift back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from web.theme import PALETTE, seal_label, signal_tone

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.unit
class TestPalette:
    def test_core_tokens_are_named(self):
        assert {"ink", "panel", "paper", "muted", "cinnabar", "jade", "brass"} <= set(PALETTE)

    def test_no_external_font_cdn(self):
        """离线机器上 Google Fonts 只会拖慢首屏，还会把排版交给网络。"""
        from web import theme

        assert "@import" not in theme._CSS
        assert "fonts.googleapis" not in theme._CSS

    def test_accessibility_guards_survive(self):
        from web import theme

        assert ":focus-visible" in theme._CSS
        assert "prefers-reduced-motion" in theme._CSS

    def test_sidebar_is_quiet_not_a_red_block(self):
        from web import theme

        assert "#C8102E" not in theme._CSS, "又回到整块红侧栏了"
        assert "background: var(--mv-panel)" in theme._CSS

    def test_the_streamlit_sidebar_toggle_stays_visible(self):
        """#36 的教训：藏掉 header/toolbar 会让折叠后的侧栏无法再打开。"""
        from web import theme

        assert "stExpandSidebarButton" in theme._CSS
        assert "stSidebarCollapseButton" in theme._CSS


@pytest.mark.unit
class TestSignalLanguage:
    @pytest.mark.parametrize(
        "signal,tone",
        [("Buy", "up"), ("Overweight", "up"), ("Hold", "flat"),
         ("Underweight", "down"), ("Sell", "down"), ("BUY", "up")],
    )
    def test_a_share_convention(self, signal, tone):
        assert signal_tone(signal) == tone

    @pytest.mark.parametrize(
        "signal,label",
        [("Buy", "买入"), ("Overweight", "增持"), ("Hold", "持有"),
         ("Underweight", "减持"), ("Sell", "卖出"), ("", "持有")],
    )
    def test_seal_labels_are_two_characters(self, signal, label):
        assert seal_label(signal) == label


@pytest.mark.unit
class TestAppUsesTheTheme:
    def test_app_injects_the_shared_theme_once(self):
        src = (REPO_ROOT / "web" / "app.py").read_text(encoding="utf-8")

        assert src.count("inject_theme()") == 1
        assert "<style>" not in src, "app.py 里又出现了第二份内联样式表"

    def test_report_sections_have_no_emoji(self):
        from web.components.report_viewer import _ANALYST_SECTIONS

        for _key, title in _ANALYST_SECTIONS:
            assert not any(ord(ch) > 0xFFFF for ch in title), f"章节标题带 emoji: {title}"


@pytest.mark.unit
class TestPdfTypography:
    """PDF 与 Web 共用同一套信号语义；A 股的红涨绿跌不能画反。"""

    def test_a_share_signal_colours(self):
        from web.pdf_export import _signal_color

        assert _signal_color("Overweight") == (201, 75, 58)   # 朱砂：涨/增持
        assert _signal_color("Buy") == (201, 75, 58)
        assert _signal_color("Underweight") == (79, 138, 116)  # 青玉：跌/减持
        assert _signal_color("Sell") == (79, 138, 116)
        assert _signal_color("Hold") == (194, 163, 107)        # 黄铜：持有

    def test_emoji_are_stripped_before_rendering(self):
        from web.pdf_export import _strip_emoji

        cleaned = _strip_emoji("⚠️ 风险提示 🔴 立案")

        assert "⚠" not in cleaned and "🔴" not in cleaned
        assert "风险提示" in cleaned and "立案" in cleaned
