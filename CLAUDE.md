# MARVEL

> Multi-Agent Research & Valuation Engine for Long-short

## 项目概述
基于 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的 A 股深度特化 fork。多 Agent 投研框架，**9 个** Analyst 角色通过 Bull/Bear 辩论 + 三方风险辩论生成投资报告。

- **仓库**: https://github.com/ZeyuChang0471/marvel
- **直接上游**: https://github.com/simonlin1212/tradingagents-astock （本仓库基于其 v0.2.13）
- **协议**: 混合许可 — Apache-2.0 + PolyForm-Noncommercial-1.0.0，**仅限非商业用途**。逐组件清单见 [LICENSING.md](./LICENSING.md)；`pyproject.toml` 里是等价的 SPDX 表达式 `Apache-2.0 AND PolyForm-Noncommercial-1.0.0`。注意继承自上游的 Apache-2.0 部分仍可依 Apache 条款商用，非商业约束只对 PolyForm 组件与 MARVEL 自身改动有效。
- **Python**: >=3.10
- **当前版本**: 0.2.13

## 架构

### 数据层（v0.2.5 全部直连 HTTP，零第三方数据库依赖）
| 来源 | 协议 | 数据 |
|------|------|------|
| mootdx | TCP 7709 | OHLCV K线、财务快照、F10 文本 |
| 腾讯财经 | HTTP (qt.gtimg.cn) | PE/PB/市值/换手率 |
| 东方财富 datacenter | HTTP (datacenter-web) | 龙虎榜、限售解禁、板块行情 |
| 东方财富 push2/push2his | HTTP (push2.eastmoney) | 实时行情、个股信息、板块列表、资金流(分钟+日级) |
| 东方财富 np-weblist | HTTP | 滚动新闻 |
| 新浪财经 | HTTP (money.finance.sina) | K线历史、财报三表 |
| 同花顺 10jqka | HTTP | EPS 一致预期、热股题材 |
| 财联社 cls.cn | HTTP | 全球财经快讯 |
| 百度股市通 | HTTP (gushitong.baidu) | 概念板块归属（资金流已迁移至东财push2） |

### Agent 角色（9 个）
原版 4 个（市场/情绪/新闻/基本面）+ A 股特化 5 个（政策分析师/游资追踪/解禁监控/量价分析师/宏观板块分析师）

> 角色数量以代码为准：`marvel/graph/setup.py` 注册的 analyst 分支、`marvel/graph/trading_graph.py` 的
> `selected_analysts` 默认值和 `web/progress.py` 的 `PIPELINE_STAGES` 都必须是 9。改这里时同步改三处。

### 关键路径
- `marvel/dataflows/a_stock.py` — A 股数据 vendor，所有数据获取入口
- `marvel/dataflows/utils.py` — `safe_ticker_component` 路径安全校验 + 中文 ticker 自动解析
- `marvel/agents/` — 9 个 Analyst + Bull/Bear 辩论逻辑
- `web/` — Streamlit Web UI（14 阶段进度：9 分析师 → 质量门控 → 辩论 → 交易 → 风控 → 决策）
- `cli/` — CLI 入口

### 中文股票名解析链路
用户/LLM 输入 → `safe_ticker_component` 检测中文 → `resolve_ticker()` → `_build_name_code_map()`（mootdx 全市场映射，缓存）→ 返回 6 位代码

## 已知问题与注意事项

### 依赖冲突（v0.2.6 起不再声明 `[google]` extra）
mootdx 锁死 `httpx>=0.25,<0.26`（实测 0.11.7 的 METADATA），与 langchain-google-genai 的 `httpx>=0.28.1` 无法在同一依赖集里解析。因此 `pyproject.toml` **刻意不声明 `google` extra**——声明了也装不上，只会误导（issue #87）。
用 Gemini 请按 `marvel/llm_clients/google_client.py` 抛出的 ImportError 里给的命令显式安装：
```
pip install --no-deps "langchain-google-genai>=4.0.0"
pip install "google-genai>=1.53.0" "httpx>=0.28.1"
```
（mootdx 走 TCP、运行时并不 import httpx，抬 httpx 实际安全。）
`tests/test_google_api_key.py` 用 `importorskip`，所以在默认环境里它**永久跳过**；加 `pip check` 才能暴露这类元数据问题。

### akshare 已移除（v0.2.5）
v0.2.5 起完全移除 akshare 依赖，所有数据通过直连 HTTP API 获取。

### 百度 PAE 资金流接口已下线（v0.2.7 已修复）
`fundsortlist` 和 `fundflow` 两个接口返回空（2026-05-19 确认）。v0.2.7 已替换为东财 push2 资金流 API。同时修复了 `RPT_ORGANIZATION_BUSSINESS`（改用席位筛选机构）和东财全球资讯 `req_trace` 参数。

### 东财接口防封限流（v0.2.11 新增，移植自 a-stock-data v3.2）
`a_stock.py` 里所有指向 `eastmoney.com` 的请求（push2 / push2his / datacenter-web / search-api / np-weblist 共 7 个调用点）统一走节流入口 `_em_get()`：模块级时间戳串行限流（默认间隔 `EM_MIN_INTERVAL=1.0s`，可用同名环境变量覆盖）+ 0.1~0.5s 随机抖动 + 复用 `requests.Session`（Keep-Alive）+ 默认 UA。多 Agent 跑批量分析不再触发东财临时封 IP。**仅东财限流**——mootdx(TCP) / 腾讯 / 新浪 / 同花顺 / 财联社 / 百度 等非东财源不受影响。批量场景可设 `EM_MIN_INTERVAL=1.5~2` 进一步降速。新增东财端点时务必走 `_em_get` 而非裸 `requests.get`。

### 模型兼容性
deepseek-v4-flash 等模型在 tool call 时可能返回中文股票名而非 6 位代码。`safe_ticker_component` 已加兜底自动转码，但不同模型表现仍有差异。

### 待处理 PR
- PR #18（hejingchi）：start_date 功能 + 主题切换 + Windows 字体。不建议直接 merge（与 v0.2.6 冲突），start_date 功能值得后续自行实现。

## Issue 归档
`issues/` 收录的是**直接上游**（simonlin1212/TradingAgents-Astock）的 Issue 记录，包含问题描述、根因分析、修复方案和当前状态，作为移植时的参考保留。本仓库自己的 Issue 在 GitHub 上，不在此目录。

## 开发规范
- 改动前先跑 `python -m pytest tests/ -v` 确保不破坏现有测试
- `safe_ticker_component` 是安全边界，任何绕过路径校验的改动必须慎重评估
- 数据层新增接口遵循 `marvel/dataflows/interface.py` 的 vendor 路由模式
- 数据层收 `curr_date` 的函数必须真的用它做时点裁剪：**不要给 `curr_date` 默认值**，
  否则模型省略该参数时守卫会静默失效（见 `tests/test_lookahead_guard.py`，以及
  `get_global_news` / 三张财报的修复）。数据源取不到历史时点值时，用
  `_snapshot_notice()` 明说，不要默认静默。
- Web UI 改动在 `web/` 目录，用 `streamlit run web/app.py` 本地测试
  （`web/launch.py` 是 `marvel-web` 的启动包装器；在它外面再套一层
  `streamlit run` 会起两个服务、页面空白）
- 解析决策评级统一用 `marvel/agents/utils/rating.py`；需要区分「模型选了 Hold」和
  「读不出评级」时用 `parse_rating_explicit()`（返回 `None`），不要用默认值兜底

## 相关项目
- [a-stock-data](https://github.com/simonlin1212/a-stock-data) — A 股 MCP 数据服务（Claude Code 用的 skill）
- 上游 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) — 原版框架
