"""MARVEL 的视觉语言：夜读研报台。

设计取向（见 frontend-design 技能）：把界面当成一份**收盘后的中文研报**来排，而不是
又一个深色 dashboard。

* 墨色纸面，不用纯黑；正文是暖纸色，长时间读不刺眼。
* 宋体只用于研报正文与标题，控件用系统黑体；数字用等宽数字。
* 朱砂只出现在一个地方最重：评级印章。其余红只用于方向与激活态。
* 结构线（发丝线）承载层级；不用卡片、渐变、发光、emoji 装饰。
* 全站不依赖外部字体 CDN（这台机器经常离线，Google Fonts 只会拖慢首屏）。
"""

from __future__ import annotations

import streamlit as st

from web.signal_labels import seal_label, signal_tone  # re-exported for components

#: 4–6 个核心色，全部在这里定义，组件不写裸色值。
PALETTE = {
    "ink": "#0C1216",
    "panel": "#121A1F",
    "raised": "#18242A",
    "paper": "#E9E2D3",
    "muted": "#93A1A3",
    "rule": "#243238",
    "cinnabar": "#C94B3A",   # A 股「涨/买入」，也是唯一强调色
    "jade": "#4F8A74",       # A 股「跌/卖出」
    "brass": "#C2A36B",      # 持有 / 提醒
}

_CSS = """
<style>
:root {
  --mv-ink: #0C1216;
  --mv-panel: #121A1F;
  --mv-raised: #18242A;
  --mv-paper: #E9E2D3;
  --mv-muted: #93A1A3;
  --mv-rule: #243238;
  --mv-faint: #7E8C8F;
  --mv-cinnabar: #C94B3A;
  --mv-jade: #4F8A74;
  --mv-brass: #C2A36B;
  --mv-serif: "Noto Serif SC", "Source Han Serif SC", "Songti SC", "STSong", "SimSun", serif;
  --mv-sans: "Noto Sans SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei",
             "Segoe UI", system-ui, sans-serif;
}

html, body, .stApp, [class*="css"] {
  font-family: var(--mv-sans);
  color: var(--mv-paper);
  -webkit-font-smoothing: antialiased;
}
.stApp { background: var(--mv-ink); }
[data-testid="stAppViewContainer"] > .main .block-container {
  max-width: 1180px;
  padding-top: 2.2rem;
  padding-bottom: 4.5rem;
}

/* Streamlit chrome：保留侧栏展开/收起按钮（#36 的教训），只藏掉不需要的。 */
#MainMenu, footer, div[data-testid="stDecoration"], div[data-testid="stStatusWidget"],
div[data-testid="stToolbarActions"], div[data-testid="stAppDeployButton"],
span[data-testid="stMainMenu"] { display: none !important; }
header[data-testid="stHeader"] { background: transparent !important; box-shadow: none !important; }
button[data-testid="stExpandSidebarButton"], button[data-testid="stSidebarCollapseButton"],
button[data-testid="collapsedControl"], [data-testid="stSidebarCollapsedControl"] {
  display: flex !important; visibility: visible !important; opacity: 1 !important;
}

/* ── 侧栏：安静的索引，不是一块红色广告牌 ───────────────────────── */
section[data-testid="stSidebar"] {
  background: var(--mv-panel);
  border-right: 1px solid var(--mv-rule);
}
section[data-testid="stSidebar"] > div { padding-top: .4rem; }
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stCaption { color: var(--mv-muted); }
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h4 {
  font-family: var(--mv-sans);
  font-size: .74rem;
  font-weight: 600;
  letter-spacing: .05em;
  color: var(--mv-muted);
  margin: 1.35rem 0 .45rem;
  padding-bottom: .35rem;
  border-bottom: 1px solid var(--mv-rule);
}
section[data-testid="stSidebar"] hr { border-color: var(--mv-rule); margin: .9rem 0; }

/* ── 正文排版 ───────────────────────────────────────────────────── */
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li { line-height: 1.78; }
.main [data-testid="stMarkdownContainer"] { max-width: 78ch; }
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3 {
  font-family: var(--mv-serif);
  font-weight: 600;
  color: var(--mv-paper);
  letter-spacing: .01em;
}
[data-testid="stMarkdownContainer"] h2 {
  font-size: 1.42rem;
  margin: 2.3rem 0 1rem;
  padding-bottom: .5rem;
  border-bottom: 1px solid var(--mv-rule);
}
[data-testid="stMarkdownContainer"] h3 { font-size: 1.16rem; margin: 1.7rem 0 .65rem; }
[data-testid="stMarkdownContainer"] table { border-collapse: collapse; font-size: .86rem; }
[data-testid="stMarkdownContainer"] th {
  font-weight: 600; color: var(--mv-paper); border-bottom: 1px solid var(--mv-muted) !important;
}
[data-testid="stMarkdownContainer"] td { border-color: var(--mv-rule) !important; }
[data-testid="stMarkdownContainer"] a { color: var(--mv-cinnabar); text-underline-offset: 3px; }
[data-testid="stMarkdownContainer"] code {
  background: var(--mv-raised); color: var(--mv-paper); border-radius: 2px;
}
[data-testid="stMarkdownContainer"] blockquote {
  border-left: 2px solid var(--mv-cinnabar);
  color: var(--mv-muted);
  padding-left: 1rem;
}

/* ── 报头：研报的刊头 + 朱砂印章（全站唯一的强元素）──────────────── */
.mv-masthead {
  display: flex; justify-content: space-between; align-items: flex-start; gap: 2rem;
  padding: 1.1rem 0 1.3rem;
  border-top: 2px solid var(--mv-paper);
  border-bottom: 1px solid var(--mv-rule);
  margin: .4rem 0 1.5rem;
}
.mv-kicker {
  font-size: .72rem; letter-spacing: .09em; color: var(--mv-muted);
  display: flex; flex-wrap: wrap; gap: .55rem; align-items: baseline;
}
.mv-kicker .sep { width: 18px; height: 1px; background: var(--mv-rule); display: inline-block; }
.mv-title {
  font-family: var(--mv-serif); font-weight: 600; line-height: 1.12;
  font-size: clamp(2rem, 3.4vw, 2.9rem); margin: .45rem 0 .35rem; color: var(--mv-paper);
}
.mv-title .mv-code {
  font-family: var(--mv-sans); font-size: .82rem; font-weight: 400;
  letter-spacing: .08em; color: var(--mv-muted); margin-left: .7rem;
  font-variant-numeric: tabular-nums;
}
.mv-dek { color: var(--mv-muted); font-size: .95rem; max-width: 56ch; }
.mv-meta {
  display: flex; flex-wrap: wrap; gap: 1.4rem; margin-top: .95rem;
  font-size: .76rem; color: var(--mv-muted); font-variant-numeric: tabular-nums;
}
.mv-stamp {
  flex: 0 0 auto; width: 96px; height: 96px; display: grid; place-items: center;
  font-family: var(--mv-serif); font-size: 1.75rem; letter-spacing: .1em;
  color: var(--mv-cinnabar); border: 1px solid currentColor;
  box-shadow: inset 0 0 0 4px var(--mv-ink), inset 0 0 0 5px currentColor;
  margin-top: .3rem;
}
.mv-stamp[data-tone="down"] { color: var(--mv-jade); }
.mv-stamp[data-tone="flat"] { color: var(--mv-brass); }

/* ── 封面（空态）────────────────────────────────────────────────── */
.mv-cover {
  min-height: 62vh; display: flex; flex-direction: column; justify-content: center;
  align-items: flex-start; padding: 3rem 0 2rem;
}
.mv-cover-rule { width: 64px; height: 2px; background: var(--mv-cinnabar); margin-bottom: 1.5rem; }
.mv-cover-word {
  font-family: var(--mv-serif); font-size: clamp(3rem, 6vw, 4.4rem);
  letter-spacing: .1em; line-height: 1; color: var(--mv-paper);
}
.mv-cover-sub { margin: .85rem 0 1.5rem; color: var(--mv-muted); font-size: .95rem; letter-spacing: .06em; }
.mv-cover-lede {
  max-width: 44ch; color: var(--mv-paper); font-family: var(--mv-serif);
  font-size: 1.08rem; line-height: 1.95;
}
.mv-cover-hint {
  margin-top: 2.2rem; padding-top: .85rem; border-top: 1px solid var(--mv-rule);
  color: var(--mv-muted); font-size: .85rem;
}
.mv-cover-foot { margin-top: 3rem; color: var(--mv-faint); font-size: .74rem; line-height: 1.8; }

/* ── 品牌（侧栏顶部）────────────────────────────────────────────── */
.mv-brand { padding: .2rem 0 1.05rem; border-bottom: 1px solid var(--mv-rule); }
.mv-brand-word { font-family: var(--mv-serif); font-size: 1.4rem; letter-spacing: .14em; color: var(--mv-paper); }
.mv-brand-sub { color: var(--mv-muted); font-size: .75rem; margin-top: .3rem; letter-spacing: .04em; }
.mv-brand-by { color: var(--mv-faint); font-size: .7rem; margin-top: .45rem; }
.mv-brand-by a { color: inherit; text-decoration: none; border-bottom: 1px solid var(--mv-rule); }
.mv-cover-foot a {
  color: var(--mv-muted); text-decoration: none;
  border-bottom: 1px solid var(--mv-rule);
}
.mv-cover-foot a:hover { color: var(--mv-paper); border-color: var(--mv-cinnabar); }

/* ── 流程轨道：14 个阶段本来就是序列，编号在这里才有意义 ─────────── */
.mv-rail { display: grid; gap: .55rem; list-style: none; padding: 0; margin: .3rem 0 1.15rem; }
.mv-rail--analysts { grid-template-columns: repeat(9, minmax(0, 1fr)); }
.mv-rail--pipeline { grid-template-columns: repeat(5, minmax(0, 1fr)); }
.mv-rail li { position: relative; padding-top: .85rem; border-top: 1px solid var(--mv-rule); min-width: 0; }
.mv-rail li::before {
  content: ""; position: absolute; top: -3px; left: 0; width: 5px; height: 5px; border-radius: 50%;
  background: var(--mv-rule);
}
.mv-rail li.done::before { background: var(--mv-jade); }
.mv-rail li.active::before { background: var(--mv-cinnabar); }
.mv-rail .n {
  display: block; font-size: .62rem; color: #6C7A7D; font-variant-numeric: tabular-nums;
  letter-spacing: .06em;
}
.mv-rail .name { display: block; font-size: .76rem; color: var(--mv-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mv-rail li.done .name { color: var(--mv-paper); }
.mv-rail li.active .name { color: var(--mv-paper); }
.mv-rail li.active .n { color: var(--mv-cinnabar); }

.mv-rail-label {
  font-size: .7rem; color: var(--mv-muted); letter-spacing: .05em; margin: .9rem 0 .2rem;
}

/* ── 控件：细线、直角，不用卡片阴影 ─────────────────────────────── */
.stButton > button, .stDownloadButton > button {
  border-radius: 0 !important; font-family: var(--mv-sans); font-size: .84rem;
  transition: border-color .15s ease, color .15s ease, background-color .15s ease;
}
button[kind="primary"], button[data-testid="stBaseButton-primary"] {
  background: var(--mv-cinnabar) !important; color: var(--mv-ink) !important;
  border: 1px solid var(--mv-cinnabar) !important; font-weight: 600 !important;
}
button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {
  background: #D75A47 !important; border-color: #D75A47 !important; color: var(--mv-ink) !important;
}
button[kind="secondary"], button[data-testid="stBaseButton-secondary"] {
  background: transparent !important; color: var(--mv-muted) !important;
  border: 1px solid var(--mv-rule) !important;
}
button[kind="secondary"]:hover, button[data-testid="stBaseButton-secondary"]:hover {
  color: var(--mv-paper) !important; border-color: var(--mv-cinnabar) !important;
}
button[kind="secondary"] p, button[data-testid="stBaseButton-secondary"] p { color: inherit !important; }
.stDownloadButton button { background: transparent !important; color: var(--mv-paper) !important; border: 1px solid var(--mv-rule) !important; }
.stDownloadButton button:hover { border-color: var(--mv-cinnabar) !important; color: var(--mv-cinnabar) !important; }
section[data-testid="stSidebar"] button[kind="secondary"] {
  border: 0 !important; border-bottom: 1px solid var(--mv-rule) !important;
  justify-content: flex-start !important; text-align: left; padding: .4rem .1rem !important;
  color: var(--mv-muted) !important;
}
section[data-testid="stSidebar"] button[kind="secondary"]:hover { color: var(--mv-paper) !important; }

div[data-testid="stTextInput"] input,
div[data-testid="stDateInput"] input,
div[data-testid="stNumberInput"] input,
div[data-testid="stTextArea"] textarea {
  background: var(--mv-raised) !important; color: var(--mv-paper) !important;
  border: 1px solid var(--mv-rule) !important; border-radius: 0 !important;
}
div[data-testid="stTextInput"] input:focus,
div[data-testid="stDateInput"] input:focus,
div[data-testid="stNumberInput"] input:focus,
div[data-testid="stTextArea"] textarea:focus {
  border-color: var(--mv-cinnabar) !important; box-shadow: 0 0 0 1px var(--mv-cinnabar) !important;
}
div[data-testid="stTextInput"] input::placeholder { color: var(--mv-faint) !important; }
div[data-baseweb="select"] > div {
  background: var(--mv-raised) !important; border-radius: 0 !important;
  border-color: var(--mv-rule) !important; color: var(--mv-paper) !important;
}

/* 折叠面板与标签页：当成研报的章节，不是 SaaS 卡片 */
div[data-testid="stExpander"] {
  border: 0 !important; border-top: 1px solid var(--mv-rule) !important;
  border-radius: 0 !important; background: transparent !important;
}
div[data-testid="stExpander"] details { border: 0 !important; }
div[data-testid="stExpander"] summary { padding: .72rem .1rem !important; }
div[data-testid="stExpander"] summary p {
  font-family: var(--mv-serif); font-size: 1.02rem; color: var(--mv-paper);
}
div[data-testid="stExpander"] details[open] summary p { color: var(--mv-cinnabar); }
.stTabs [data-baseweb="tab-list"] { gap: 1.5rem; border-bottom: 1px solid var(--mv-rule); }
.stTabs [data-baseweb="tab"] {
  background: transparent !important; color: var(--mv-muted) !important;
  padding: .35rem 0 .55rem !important; font-size: .86rem;
}
.stTabs [aria-selected="true"] { color: var(--mv-paper) !important; }
.stTabs [data-baseweb="tab-highlight"] { background: var(--mv-cinnabar) !important; height: 1px !important; }

/* 指标：一条账本，不是四张卡 */
div[data-testid="stMetric"] {
  border-left: 1px solid var(--mv-rule); padding: .15rem 0 .15rem .9rem; background: transparent;
}
div[data-testid="stMetricLabel"] p { color: var(--mv-muted) !important; font-size: .72rem !important; letter-spacing: .03em; }
div[data-testid="stMetricValue"] {
  font-family: var(--mv-sans) !important; font-weight: 500 !important;
  color: var(--mv-paper) !important; font-variant-numeric: tabular-nums;
}

/* 进度条：2px 朱砂，不要渐变 */
div[data-testid="stProgress"] > div { background: var(--mv-rule) !important; border-radius: 0 !important; height: 3px; }
div[data-testid="stProgress"] > div > div { background: var(--mv-cinnabar) !important; border-radius: 0 !important; }
.stProgress > div > div > div { background: var(--mv-cinnabar) !important; }

/* 提示块：细线，安静 */
div[data-testid="stAlert"] {
  border-radius: 0 !important; border: 1px solid var(--mv-rule) !important;
  background: var(--mv-panel) !important;
}
div[data-testid="stAlert"] p { color: var(--mv-paper); }

/* 可访问性 */
:focus-visible { outline: 2px solid var(--mv-cinnabar) !important; outline-offset: 2px !important; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation: none !important; }
}

/* 窄屏：轨道退成三列，报头竖排 */
@media (max-width: 900px) {
  .mv-rail--analysts { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .mv-rail--pipeline { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .mv-masthead { flex-direction: column-reverse; gap: 1.2rem; }
  .mv-stamp { width: 76px; height: 76px; font-size: 1.4rem; }
  [data-testid="stMarkdownContainer"] h2 { font-size: 1.28rem; }
}
</style>
"""


def inject_theme() -> None:
    """Inject the single stylesheet. Call once, right after ``set_page_config``."""
    st.markdown(_CSS, unsafe_allow_html=True)
