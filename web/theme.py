"""MARVEL 的视觉语言：夜读研报台。

设计取向（见 frontend-design 技能）：把界面当成一份**收盘后的中文研报**来排，而不是
又一个深色 dashboard。

* 墨色纸面，不用纯黑；正文是暖纸色，长时间读不刺眼。
* 宋体只用于研报正文与标题，控件用系统黑体；数字用等宽数字。
* 朱砂只出现在一个地方最重：评级印章。其余红只用于方向与激活态。
* 结构线（发丝线）承载层级；控件借鉴 Apple 的克制质感：小圆角、半透明层、
  极轻内高光与按压反馈，但不做卡片堆叠、渐变装饰或发光。
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
  --mv-control: #0F171B;
  --mv-radius: 9px;
  --mv-radius-sm: 6px;
  --mv-line-soft: rgba(233, 226, 211, .075);
  --mv-highlight: rgba(255, 255, 255, .105);
  --mv-ease: cubic-bezier(.2, .75, .25, 1);
  --mv-serif: "Noto Serif SC", "Source Han Serif SC", "Songti SC", "STSong", "SimSun", serif;
  --mv-sans: "Noto Sans SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei",
             "Segoe UI", system-ui, sans-serif;
}

html, body, .stApp, [class*="css"] {
  font-family: var(--mv-sans);
  color: var(--mv-paper);
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
.stApp ::selection { background: rgba(201, 75, 58, .34); color: var(--mv-paper); }
.stApp { background: var(--mv-ink); }
[data-testid="stAppViewContainer"] > .main .block-container {
  max-width: 1160px;
  padding-top: 2.35rem;
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
  border-right: 1px solid var(--mv-line-soft);
  box-shadow: inset -1px 0 0 rgba(0, 0, 0, .18);
}
section[data-testid="stSidebar"] > div { padding-top: .55rem; }
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stCaption { color: var(--mv-muted); line-height: 1.65; }
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h4 {
  font-family: var(--mv-sans);
  font-size: .72rem;
  font-weight: 600;
  letter-spacing: .055em;
  color: var(--mv-muted);
  margin: 1.4rem 0 .55rem;
  padding-bottom: .42rem;
  border-bottom: 1px solid var(--mv-line-soft);
}
section[data-testid="stSidebar"] hr { border-color: var(--mv-line-soft); margin: .95rem 0; }

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
  padding: 1.15rem 0 1.45rem;
  border-top: 1px solid var(--mv-paper);
  border-bottom: 1px solid var(--mv-line-soft);
  margin: .25rem 0 1.55rem;
}
.mv-kicker {
  font-size: .7rem; letter-spacing: .075em; color: var(--mv-muted);
  display: flex; flex-wrap: wrap; gap: .6rem; align-items: center;
}
.mv-kicker .sep { width: 16px; height: 1px; background: var(--mv-line-soft); display: inline-block; }
.mv-title {
  font-family: var(--mv-serif); font-weight: 600; line-height: 1.14;
  font-size: clamp(2.05rem, 3.25vw, 2.78rem); margin: .5rem 0 .38rem; color: var(--mv-paper);
  letter-spacing: .006em; text-wrap: balance;
}
.mv-title .mv-code {
  font-family: var(--mv-sans); font-size: .82rem; font-weight: 400;
  letter-spacing: .08em; color: var(--mv-muted); margin-left: .7rem;
  font-variant-numeric: tabular-nums;
}
.mv-dek { color: var(--mv-muted); font-size: .95rem; max-width: 56ch; }
.mv-meta {
  display: flex; flex-wrap: wrap; gap: 1.15rem; margin-top: 1rem;
  font-size: .72rem; color: var(--mv-muted); font-variant-numeric: tabular-nums;
  letter-spacing: .015em;
}
.mv-stamp {
  flex: 0 0 auto; width: 96px; height: 96px; display: grid; place-items: center;
  font-family: var(--mv-serif); font-size: 1.75rem; letter-spacing: .1em;
  color: var(--mv-cinnabar); border: 1px solid currentColor;
  box-shadow: inset 0 0 0 4px var(--mv-ink), inset 0 0 0 5px currentColor;
  margin-top: .3rem; border-radius: 3px;
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

/* ── 流程轨道：阶段是序列，用编号与状态灯表达，不做九宫格卡片 ─────── */
[data-testid="stMarkdownContainer"] .mv-rail {
  display: grid !important; gap: 1px; list-style: none !important;
  padding: 1px !important; margin: .35rem 0 1.35rem !important;
  width: 100% !important; box-sizing: border-box;
  background: var(--mv-line-soft); border: 1px solid var(--mv-line-soft);
  border-radius: var(--mv-radius); overflow: hidden;
}
[data-testid="stMarkdownContainer"] .mv-rail--analysts {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
[data-testid="stMarkdownContainer"] .mv-rail--pipeline {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
[data-testid="stMarkdownContainer"] .mv-rail > li {
  display: grid; grid-template-columns: 6px 1.35rem minmax(0, 1fr) auto;
  align-items: center; column-gap: .56rem; min-width: 0;
  padding: .78rem .82rem; margin: 0 !important; background: rgba(18, 26, 31, .94);
  transition: background-color .18s var(--mv-ease), box-shadow .18s var(--mv-ease);
}
[data-testid="stMarkdownContainer"] .mv-rail .mark {
  width: 6px; height: 6px; border-radius: 50%; background: #344248;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, .035);
}
[data-testid="stMarkdownContainer"] .mv-rail li.done .mark { background: var(--mv-jade); }
[data-testid="stMarkdownContainer"] .mv-rail li.active {
  background: rgba(201, 75, 58, .055); box-shadow: inset 2px 0 0 var(--mv-cinnabar);
}
[data-testid="stMarkdownContainer"] .mv-rail li.active .mark {
  background: var(--mv-cinnabar); box-shadow: 0 0 0 3px rgba(201, 75, 58, .16);
}
[data-testid="stMarkdownContainer"] .mv-rail .n {
  font-size: .61rem; color: #6C7A7D; font-variant-numeric: tabular-nums;
  letter-spacing: .055em;
}
[data-testid="stMarkdownContainer"] .mv-rail .name {
  min-width: 0; font-size: .75rem; color: var(--mv-muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
[data-testid="stMarkdownContainer"] .mv-rail .state {
  font-size: .59rem; color: var(--mv-faint); letter-spacing: .035em;
  white-space: nowrap;
}
[data-testid="stMarkdownContainer"] .mv-rail li.done .name { color: var(--mv-paper); }
[data-testid="stMarkdownContainer"] .mv-rail li.done .state { color: var(--mv-jade); }
[data-testid="stMarkdownContainer"] .mv-rail li.active .name,
[data-testid="stMarkdownContainer"] .mv-rail li.active .n { color: var(--mv-paper); }
[data-testid="stMarkdownContainer"] .mv-rail li.active .n,
[data-testid="stMarkdownContainer"] .mv-rail li.active .state { color: var(--mv-cinnabar); }

.mv-rail-label {
  font-size: .69rem; color: var(--mv-muted); letter-spacing: .045em;
  margin: 1.25rem 0 .45rem;
}

/* ── 控件：Apple 式小圆角、半透明表面与极轻内高光 ───────────────── */
.stButton > button, .stDownloadButton > button,
button[data-testid^="stBaseButton-"] {
  min-height: 2.45rem; border-radius: var(--mv-radius) !important;
  font-family: var(--mv-sans); font-size: .82rem; font-weight: 500;
  letter-spacing: .012em; box-shadow: inset 0 1px 0 var(--mv-highlight);
  transition: transform .16s var(--mv-ease), border-color .16s var(--mv-ease),
              color .16s var(--mv-ease), background-color .16s var(--mv-ease),
              box-shadow .16s var(--mv-ease);
}
.stButton > button:active, .stDownloadButton > button:active,
button[data-testid^="stBaseButton-"]:active { transform: translateY(1px); }
button[kind="primary"], button[data-testid="stBaseButton-primary"] {
  background: var(--mv-cinnabar) !important; color: var(--mv-paper) !important;
  border: 1px solid rgba(255, 255, 255, .13) !important; font-weight: 600 !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .18), 0 5px 16px rgba(0, 0, 0, .16);
}
button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {
  background: #D25846 !important; border-color: rgba(255, 255, 255, .19) !important;
  color: var(--mv-paper) !important; transform: translateY(-1px);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .2), 0 7px 20px rgba(0, 0, 0, .2);
}
button[kind="secondary"], button[data-testid="stBaseButton-secondary"] {
  background: rgba(255, 255, 255, .025) !important; color: var(--mv-paper) !important;
  border: 1px solid var(--mv-line-soft) !important;
}
button[kind="secondary"]:hover, button[data-testid="stBaseButton-secondary"]:hover {
  background: rgba(255, 255, 255, .055) !important;
  color: var(--mv-paper) !important; border-color: rgba(233, 226, 211, .16) !important;
}
button[kind="secondary"] p, button[data-testid="stBaseButton-secondary"] p,
button[kind="primary"] p, button[data-testid="stBaseButton-primary"] p { color: inherit !important; }
button:disabled, button[aria-disabled="true"] {
  cursor: not-allowed !important; opacity: 1 !important;
  transform: none !important; box-shadow: none !important;
}
button[kind="primary"]:disabled, button[data-testid="stBaseButton-primary"]:disabled {
  background: rgba(201, 75, 58, .2) !important;
  border-color: rgba(201, 75, 58, .34) !important;
  color: rgba(233, 226, 211, .48) !important;
}
button[kind="secondary"]:disabled, button[data-testid="stBaseButton-secondary"]:disabled {
  background: rgba(255, 255, 255, .018) !important;
  border-color: var(--mv-line-soft) !important;
  color: var(--mv-faint) !important;
}
.stDownloadButton button {
  background: rgba(255, 255, 255, .025) !important; color: var(--mv-paper) !important;
  border: 1px solid var(--mv-line-soft) !important;
}
.stDownloadButton button:hover {
  border-color: rgba(233, 226, 211, .17) !important; color: var(--mv-paper) !important;
  background: rgba(255, 255, 255, .055) !important;
}
section[data-testid="stSidebar"] button[kind="secondary"] {
  min-height: 2.2rem; border: 0 !important; border-radius: var(--mv-radius-sm) !important;
  justify-content: flex-start !important; text-align: left; padding: .42rem .58rem !important;
  color: var(--mv-muted) !important; box-shadow: inset 0 -1px 0 var(--mv-line-soft);
  background: transparent !important;
}
section[data-testid="stSidebar"] button[kind="secondary"]:hover {
  color: var(--mv-paper) !important; background: rgba(255, 255, 255, .04) !important;
  box-shadow: inset 0 -1px 0 var(--mv-line-soft), inset 0 1px 0 var(--mv-highlight);
}

div[data-testid="stTextInput"] input,
div[data-testid="stDateInput"] input,
div[data-testid="stNumberInput"] input,
div[data-testid="stTextArea"] textarea {
  min-height: 2.65rem; background: rgba(15, 23, 27, .78) !important;
  color: var(--mv-paper) !important; border: 1px solid var(--mv-line-soft) !important;
  border-radius: var(--mv-radius) !important; padding: .58rem .72rem !important;
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, .22);
  transition: border-color .18s var(--mv-ease), box-shadow .18s var(--mv-ease),
              background-color .18s var(--mv-ease);
}
div[data-testid="stTextInput"] input:focus,
div[data-testid="stDateInput"] input:focus,
div[data-testid="stNumberInput"] input:focus,
div[data-testid="stTextArea"] textarea:focus {
  border-color: rgba(201, 75, 58, .78) !important;
  box-shadow: 0 0 0 3px rgba(201, 75, 58, .12), inset 0 1px 2px rgba(0, 0, 0, .2) !important;
  background: rgba(15, 23, 27, .95) !important;
}
div[data-testid="stTextInput"] input::placeholder { color: var(--mv-faint) !important; }
div[data-baseweb="input"], div[data-baseweb="textarea"] {
  border-radius: var(--mv-radius) !important; background: transparent !important;
}
div[data-baseweb="select"] > div {
  min-height: 2.55rem; background: rgba(15, 23, 27, .78) !important;
  border-radius: var(--mv-radius) !important; border-color: var(--mv-line-soft) !important;
  color: var(--mv-paper) !important; box-shadow: inset 0 1px 0 var(--mv-highlight);
}

/* 折叠面板与标签页：当成研报的章节，不是 SaaS 卡片 */
div[data-testid="stExpander"] {
  border: 1px solid var(--mv-line-soft) !important;
  border-radius: var(--mv-radius) !important; background: rgba(18, 26, 31, .48) !important;
  overflow: hidden; margin: .35rem 0 .65rem;
}
div[data-testid="stExpander"] details { border: 0 !important; }
div[data-testid="stExpander"] summary {
  padding: .78rem .9rem !important; border-radius: var(--mv-radius) !important;
  transition: background-color .16s var(--mv-ease);
}
div[data-testid="stExpander"] summary:hover { background: rgba(255, 255, 255, .025); }
div[data-testid="stExpander"] summary p {
  font-family: var(--mv-serif); font-size: .98rem; color: var(--mv-paper);
}
div[data-testid="stExpander"] details[open] summary p { color: var(--mv-cinnabar); }
.stTabs [data-baseweb="tab-list"] {
  width: fit-content; max-width: 100%; gap: 2px; padding: 3px;
  border: 1px solid var(--mv-line-soft); border-radius: var(--mv-radius);
  background: rgba(15, 23, 27, .64);
}
.stTabs [data-baseweb="tab"] {
  background: transparent !important; color: var(--mv-muted) !important;
  min-height: 2rem; padding: .35rem .8rem !important; font-size: .8rem;
  border-radius: var(--mv-radius-sm) !important;
}
.stTabs [aria-selected="true"] {
  color: var(--mv-paper) !important; background: rgba(255, 255, 255, .055) !important;
  box-shadow: inset 0 1px 0 var(--mv-highlight);
}
.stTabs [data-baseweb="tab-highlight"] { background: transparent !important; height: 0 !important; }

/* 指标：一条账本，不是四张卡 */
div[data-testid="stMetric"] {
  border-left: 0; border-top: 1px solid var(--mv-line-soft);
  padding: .72rem .1rem .1rem; background: transparent;
}
div[data-testid="stMetricLabel"] p { color: var(--mv-muted) !important; font-size: .72rem !important; letter-spacing: .03em; }
div[data-testid="stMetricValue"] {
  font-family: var(--mv-sans) !important; font-weight: 500 !important;
  color: var(--mv-paper) !important; font-variant-numeric: tabular-nums;
}

/* 自定义进度：文字有独立行高，不再被原生 st.progress 的细条裁切。 */
.mv-progress {
  margin: .1rem 0 1.4rem; padding: 1rem 1.05rem 1.05rem;
  border: 1px solid var(--mv-line-soft); border-radius: var(--mv-radius);
  background: rgba(18, 26, 31, .62);
  box-shadow: inset 0 1px 0 var(--mv-highlight), 0 12px 32px rgba(0, 0, 0, .1);
}
.mv-progress__head, .mv-progress__foot {
  display: flex; justify-content: space-between; align-items: baseline; gap: 1rem;
}
.mv-progress__head { margin-bottom: .72rem; font-size: .74rem; }
.mv-progress__label { color: var(--mv-muted); letter-spacing: .035em; }
.mv-progress__value {
  color: var(--mv-muted); font-variant-numeric: tabular-nums; white-space: nowrap;
}
.mv-progress__value strong { color: var(--mv-paper); font-weight: 600; }
.mv-progress__track {
  position: relative; height: 6px; overflow: hidden; border-radius: 999px;
  background: #091014; box-shadow: inset 0 1px 2px rgba(0, 0, 0, .58),
              0 0 0 1px rgba(255, 255, 255, .035);
}
.mv-progress__fill {
  position: absolute; inset: 0 auto 0 0; display: block; border-radius: inherit;
  background: var(--mv-cinnabar); box-shadow: inset 0 1px 0 rgba(255, 255, 255, .22);
  transition: width .42s var(--mv-ease);
}
.mv-progress__ticks { position: absolute; inset: 0; display: flex; pointer-events: none; }
.mv-progress__ticks i { flex: 1; border-right: 1px solid rgba(233, 226, 211, .14); }
.mv-progress__foot {
  margin-top: .65rem; color: var(--mv-faint); font-size: .66rem;
  letter-spacing: .02em; font-variant-numeric: tabular-nums;
}
.mv-progress__current { color: var(--mv-muted); }
.mv-progress[data-paused="true"] .mv-progress__fill { background: var(--mv-brass); }

/* 侧栏日期说明：把日期与口径拆成可扫读的两层。 */
.mv-date {
  display: grid; grid-template-columns: auto 1fr; gap: .16rem .55rem;
  margin: .1rem 0 .8rem; padding: .65rem .72rem .68rem;
  border: 1px solid var(--mv-line-soft); border-radius: var(--mv-radius-sm);
  background: rgba(255, 255, 255, .018);
}
.mv-date__label { color: var(--mv-faint); font-size: .66rem; letter-spacing: .035em; }
.mv-date__value {
  color: var(--mv-paper); font-size: .75rem; text-align: right;
  font-variant-numeric: tabular-nums;
}
.mv-date__note { grid-column: 1 / -1; color: var(--mv-muted); font-size: .67rem; line-height: 1.55; }

/* 提示块：细线，安静 */
div[data-testid="stAlert"] {
  border-radius: var(--mv-radius) !important; border: 1px solid var(--mv-line-soft) !important;
  background: var(--mv-panel) !important;
  box-shadow: inset 0 1px 0 var(--mv-highlight);
}
div[data-testid="stAlert"] [data-testid="stAlertContainer"] {
  border-radius: calc(var(--mv-radius) - 1px) !important;
  background: rgba(194, 163, 107, .055) !important;
  box-shadow: inset 2px 0 0 var(--mv-brass);
}
div[data-testid="stAlert"] [data-testid="stAlertContentError"] {
  box-shadow: inset 2px 0 0 var(--mv-cinnabar);
}
div[data-testid="stAlert"] p { color: var(--mv-paper); }

/* 细窄滚动条，避免浏览器默认控件破坏整体精度。 */
* { scrollbar-width: thin; scrollbar-color: #344248 transparent; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: #344248; border-radius: 999px; border: 2px solid var(--mv-ink); }
::-webkit-scrollbar-track { background: transparent; }

/* 可访问性 */
:focus-visible { outline: 2px solid var(--mv-cinnabar) !important; outline-offset: 2px !important; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation: none !important; }
}

/* 窄屏：轨道降列，报头竖排 */
@media (max-width: 900px) {
  [data-testid="stMarkdownContainer"] .mv-rail--analysts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  [data-testid="stMarkdownContainer"] .mv-rail--pipeline {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .mv-masthead { flex-direction: column-reverse; gap: 1.2rem; }
  .mv-stamp { width: 76px; height: 76px; font-size: 1.4rem; }
  [data-testid="stMarkdownContainer"] h2 { font-size: 1.28rem; }
}

@media (max-width: 620px) {
  [data-testid="stAppViewContainer"] > .main .block-container { padding-top: 1.4rem; }
  [data-testid="stMarkdownContainer"] .mv-rail--analysts,
  [data-testid="stMarkdownContainer"] .mv-rail--pipeline { grid-template-columns: 1fr; }
  .mv-progress__head, .mv-progress__foot { align-items: flex-start; }
  .mv-progress__foot { flex-direction: column; gap: .22rem; }
  .mv-title { font-size: 2rem; }
}
</style>
"""


def inject_theme() -> None:
    """Inject the single stylesheet. Call once, right after ``set_page_config``."""
    st.markdown(_CSS, unsafe_allow_html=True)
