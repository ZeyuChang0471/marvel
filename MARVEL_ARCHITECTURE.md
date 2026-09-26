# MARVEL 架构解读（以代码为准）

> 校准：本文写于 2026-09-26，对应 `main` @ `cfead32` 之后的工作树。**所有数字与行为都从
> 代码和测试里读出来**，不是从其它文档转述；凡是我没有验证的，文中会写明「未验证」。
> 复现命令见文末。测试：37 个测试文件、**684 passed / 1 skipped**（Python 3.12，离线）。

---

## 1. 它是什么

一个 **A 股多 Agent 投研引擎**：9 个分析师并行取证 → 质量门控 → 多空辩论 → 交易计划 →
三方风险辩论 → 最终评级，产出中文研究报告。

血缘链条（`NOTICE` / `CHANGES_FROM_UPSTREAM.md`）：

```
TauricResearch/TradingAgents (Apache-2.0)
        └── simonlin1212/tradingagents-astock  v0.2.13   ← 直接上游
                    └── ZeyuChang0471/marvel             ← 本仓库
```

- **许可**：Apache-2.0 **AND** PolyForm-Noncommercial-1.0.0，`pyproject.toml` 里是 PEP 639
  的 SPDX 表达式，**仅限非商业**。继承自上游的 Apache-2.0 部分仍可依 Apache 条款商用，
  非商业约束只对 PolyForm 组件（交易日历、量价分析师、宏观分析师、辩论议题）与 MARVEL
  自身改动有效——逐组件清单在 `LICENSING.md`。
- **Python** `>=3.10`；版本号沿用上游的 `0.2.13`。
- **三个入口**（`pyproject.toml` 的 `[project.scripts]`）：
  `marvel` → `cli.main:app`、`marvel-web` → `web.launch:main`、
  `python -m marvel.harness`（无 console script，走模块）。

### 1.1 一句话概括它的技术选择

| 维度 | 选择 | 代价 / 收益 |
|---|---|---|
| 编排 | LangGraph `StateGraph`，14 个节点固定拓扑 | 可重复的研究报告；牺牲了「让模型自己决定流程」的灵活性 |
| 数据 | **全部直连 HTTP / TCP**，零第三方数据库、零 akshare | 部署轻（没有 MongoDB/Redis）；代价是每个源的时点能力参差不齐 |
| 模型 | 任意 OpenAI 兼容端点 + Anthropic/Google/Azure，双模型（quick/deep） | 便宜模型跑量、贵模型做裁决 |
| 报告语言 | 中文输出，内部辩论英文 | `output_language` 配置项 |

---

## 2. 规模

| 区域 | 文件 | 行数 | 职责 |
|---|---|---|---|
| `marvel/dataflows/` | 16 | 4,333 | 数据层：9 个源、供应商路由、缓存、时点纪律 |
| `marvel/agents/` | 31 | 2,771 | 9 个分析师 + 辩论 + 裁决 + 质量门控 + 工具包装 |
| `marvel/graph/` | 8 | 1,048 | 图装配、条件边、传播、检查点、反思、信号处理 |
| `marvel/llm_clients/` | 10 | 787 | 各厂商客户端、模型目录、能力表、结构化输出 |
| `marvel/harness/` | 7 | 852 | 本轮新增的独立 agent harness（注册表/循环/录制/回放） |
| `web/` | 12 | 2,311 | Streamlit UI、进度、历史、PDF 导出 |
| `cli/` | 7 | 1,605 | Typer + Rich 交互式 CLI |
| **应用合计** | **91** | **13,707** | |
| `tests/` | 37 | ~6,900 | 684 个用例 |
| `scripts/`, `examples/` | 5 | 476 | 联网探针、批量案例（不在 CI 里跑） |

它的体量是「一个人能读完」的量级：应用代码 1.37 万行，测试比应用的一半还多。

---

## 3. 数据层（`marvel/dataflows/`）

### 3.1 九个来源

| 来源 | 协议 | 拿到什么 | 有历史时点值吗 |
|---|---|---|---|
| mootdx（通达信） | TCP 7709 | 日线 OHLCV、财务快照、F10 | 有（K 线） |
| 腾讯财经 | HTTP | PE/PB/市值/换手率/涨跌停 | **无**（实时快照） |
| 东财 datacenter | HTTP | 龙虎榜、限售解禁、板块行情 | 有（带日期过滤） |
| 东财 push2 / push2his | HTTP | 实时行情、个股信息、资金流（分钟+日） | 部分（日级可回溯） |
| 东财 np-weblist | HTTP | 7×24 快讯 | 只有「最新」 |
| 新浪财经 | HTTP | K 线兜底、财报三表 | 有（按报告日） |
| 同花顺 10jqka | HTTP | 一致预期 EPS、强势股题材 | EPS 只有当前版本 |
| 财联社 cls.cn | HTTP | 全球快讯 | 只有「最新」 |
| 百度股市通 | HTTP | 概念板块归属 | **无**（含当日涨跌幅） |

### 3.2 路由：`interface.py`

工具名 → 类别 → 供应商，`route_to_vendor(method, *args, **kwargs)` 是**唯一漏斗**：

- 15 个工具、5 个类别、3 个供应商实现（`a_stock` / `yfinance` / `alpha_vantage`）。
- 回退链：主供应商失败后依次尝试其余实现，**但只对 `AlphaVantageRateLimitError` 回退**。
  其它异常不回退——对 A 股来说这是对的（回退目标是雅虎的美股数据），代价是一个瞬时
  网络故障会直接变成工具错误，模型看到的是失败而不是「换了数据源」。
- `data_vendors` 全部默认 `a_stock`；`tool_vendors` 可按工具覆盖（默认空）。

### 3.3 东财防封：一把锁 + 重试

`_em_get()` 是东财 7 个调用点的统一入口：

- `_EM_LOCK` 覆盖「sleep 节流 + 请求」**整段**（默认最小间隔 `EM_MIN_INTERVAL=1.0s` +
  0.1~0.5s 抖动）。早先只是 check-then-act，两个线程会双双放行。
- 429/5xx/超时/连接错误 → 1/2/4s 退避重试 3 次（`EM_MAX_RETRIES` / `EM_RETRY_BASE_S`），
  仍失败则**抛出**。这是刻意的：失败响应如果交给调用方，最终会显示成「没有数据」。
- 复用 `requests.Session`（Keep-Alive）+ 默认 UA。

### 3.4 mootdx：单例 TCP + 服务器选择

- 38 台候选服务器 → 并发 TCP 预筛 → 逐台**真实取数验证**（`_tdx_client_works`）→ 命中即缓存。
- 全失败时负缓存 `_MOOTDX_RETRY_AFTER_S`（300s）内直接快速失败，不再逐台重探。
- 探测会改写 mootdx 的持久化配置，所以包在 `_preserve_mootdx_bestip()` 上下文里，
  只有真的选到可用服务器才 keep。
- **`_MOOTDX_LOCK`（RLock）覆盖「选服务器 + 取数」整段**：通达信协议是有状态的
  请求-响应，两个线程共用一条连接会让响应错位。

### 3.5 缓存与原子写

| 缓存 | 位置 | 复用判据 |
|---|---|---|
| 名称→代码映射 | `~/.marvel/cache/` | 带 `built_at` 的 TTL |
| 个股日线 | `{code}-astock-daily.csv` | **`_ohlcv_cache_is_final`**：不是今天写的→重取；没有今天那根→盘前/盘中/非交易日复用、已收盘重取一次；有今天那根→只有**收盘后**写下的才算最终日线 |
| 北向历史 | `northbound_daily.csv` | 按日期去重，整份重写 |

三者都经 `dataflows/utils.py:atomic_write_text`（同目录临时文件 + `os.replace`）。
北向那份每次重写**全部历史**，半截写入会把攒下的所有交易日一起丢掉——这就是它必须原子
的原因。

### 3.6 时点纪律（本轮第 2 项工作的成果）

**问题**：取数工具的日期参数是模型写的，数据层只负责在给定窗口内过滤——于是「分析日」
对取数只是建议。修复前有四处漏洞（新闻窗口无锚、K 线只收到市场当天、记忆日志无日期过滤、
概念板块无标注）和三个静默失效的装饰器。

**现在的机制**（`dataflows/as_of.py`）：

1. **运行级分析日**存在 ContextVar 里，由 `_guard_tool_node` 在执行时用
   `analysis_date_as_of(state["trade_date"])` 绑定——工具实际是在 ToolNode 里跑的，
   所以这一层决定工具层语义。
2. **工具层**：15 个以日期为窗口上界的工具用 `anchored_date()` 取值，**忽略模型写的内容**。
3. **数据层兜底**：`a_stock.py` 里每个收模型日期的函数都带 `@clamp_arguments(...)`，
   上界晚于分析日就收敛到分析日**并在输出里写明**；解析不出来的上界按分析日处理。
4. **记忆日志**：`get_past_context(..., as_of=trade_date)` 只注入日期 ≤ 分析日的已结算决策。

未绑定分析日时**完全不变**——守卫只会收紧窗口，绝不会凭空造一个日期。所以直接调用数据层、
写脚本、跑测试的行为与过去一致。逐条路径的状态与残余风险见 `POINT_IN_TIME_AUDIT.md`。

---

## 4. 九个分析师

每个分析师是一个节点工厂，节点内部**现场组装工具列表**（所以 `state["trade_date"]` 在
作用域内），并用 `prompt | llm.bind_tools(tools)` 调用模型。报告写入各自的 state key。

| 角色 | 报告键 | 绑定工具 | A 股特化点 |
|---|---|---|---|
| 技术分析师 | `market_report` | `get_stock_data`, `get_indicators` | 涨跌停制度、T+1、北向、换手率；最多选 8 个指标 |
| 量价分析师 | `volume_price_report` | `get_stock_data`, `get_indicators` | Anna Coulling《量价分析》框架（Wyckoff） |
| 情绪分析师 | `sentiment_report` | `get_news`, `get_fund_flow`, `get_hot_stocks`, `get_stock_data` | 「嘴上说什么不如钱往哪走」，以资金流为主证据 |
| 新闻分析师 | `news_report` | `get_news`, `get_global_news` | 个股 + 宏观 |
| 基本面分析师 | `fundamentals_report` | `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement`, `get_profit_forecast`, `get_industry_comparison` | 三表按报告日截断 |
| 政策分析师 | `policy_report` | `get_news`, `get_global_news` | 政策/监管导向 |
| 游资追踪 | `hot_money_report` | 9 个（含龙虎榜、席位、北向、概念） | 主力/游资资金博弈 |
| 解禁监控 | `lockup_report` | `get_insider_transactions`, `get_news`, `get_fundamentals`, `get_lockup_expiry` | 供给端压力 |
| 宏观板块 | `macro_report` | `get_industry_comparison`, `get_concept_blocks`, `get_northbound_flow`, `get_news`, `get_global_news` | 判断「板块环境」而非重复个股分析 |

**每个分析师绑定的工具必须能被对应的 ToolNode 执行**（模型能调 ≠ 图能跑）。本轮在
`social` 上找到一个反例：分析师绑了 4 个工具，ToolNode 只有 `get_news`——模型调用
`get_fund_flow` 时 LangGraph 只回一句「不是有效工具」，情绪报告静默失去主证据。
现已修复，并由 `tests/test_tool_node_coverage.py` 对全部 9 个分析师做结构守卫。

---

## 5. 图拓扑（`graph/setup.py`）

节点名由 `f"{analyst_type.capitalize()} Analyst"` 生成，另有 `Msg Clear X` 与 `tools_x`。
以默认全选 9 个分析师为例：

```
START
 └─ Market Analyst ⇄ tools_market ─► Msg Clear Market
     └─ Volume_price Analyst ⇄ tools_volume_price ─► Msg Clear Volume_price
         └─ Social Analyst ⇄ tools_social ─► Msg Clear Social
             └─ News → Fundamentals → Policy → Hot_money → Lockup → Macro（同构）
                 └─ Msg Clear Macro
                     └─ Quality Gate
                         └─ Bull Researcher ⇄ Bear Researcher   （条件边）
                             └─ Research Manager               （deep 模型）
                                 └─ Trader                     （quick 模型）
                                     └─ Aggressive → Conservative → Neutral（条件边，三方循环）
                                         └─ Portfolio Manager  （deep 模型）
                                             └─ END
```

条件边（`conditional_logic.py`）：

- 分析师：`last_message.tool_calls` 非空 → `tools_x`，否则 → `Msg Clear X`。
- 多空辩论：`count >= 2 * max_debate_rounds` → Research Manager；否则按
  `current_response` 是否以 `"Bull"` 开头决定下一个是 Bear 还是 Bull。
  **注意**：防守端是「按上一句发言者交替」，没有独立的轮次状态。
- 三方风险：`count >= 3 * max_risk_discuss_rounds` → Portfolio Manager；否则按
  `latest_speaker` 前缀轮转 Aggressive → Conservative → Neutral → Aggressive。

默认 `max_debate_rounds = 1`、`max_risk_discuss_rounds = 1`，即多空各说一次、风险三方各说
一次就收敛。步数预算 `max_recur_limit = 250`（`Propagator` 从配置读取，不再是写死的 100）。

ToolNode 由 `_guard_tool_node` 包一层（绑定分析日），并把原 ToolNode 挂在
`guarded.tool_node` 上供测试内省。

---

## 6. 质量门控

两层，写 `data_quality_summary` 给下游：

1. **硬检查**（`_hard_check_report`，纯代码）：查长度、表格结构 → 评级 A–F + 说明。
2. **LLM 复审**：只有 `fail_count < 4` 才跑；把各报告拼成一个提示词让 quick 模型逐份打分。

两个刻意的设计：

- **尊重本次选择的分析师**：未运行的分析师标 `—` / 「未运行（本次分析未选择）」，
  且**不计入 `fail_count`**。此前「没选它」会被判成「报告为空」的 F，选少于 5 位分析师时
  直接跳过 LLM 复审，下游辩手还被要求降低对它的依赖。
- **下游确实会消费它**：多空辩手的提示词里明确写着「若某报告被标 C/D/F，降低对它的依赖」。

**它不做的**：核实来源。硬检查看形状、复审看质量，一条格式完好但内含捏造消息的报告会
被判 A（已实测）。这是 `DEBATE_POLARIZATION_AUDIT.md` 的核心结论之一。

---

## 7. 辩论

### 7.1 多空

- 辩手看到 **9 份报告全文**（逐字嵌入提示词）、质量门控摘要、**完整辩论历史**、
  对方上一轮发言。
- 每轮有 `default_round_goal("investment", round_no)` 指定的议题目标。
- 产出写入 `investment_debate_state`（`history` / `bull_history` / `bear_history` /
  `current_response` / `count`）。

### 7.2 裁判

Research Manager 的输入**只有辩论记录**——9 份原始报告一份都没给它——然后要求它按
Buy/Overweight/Hold/Underweight/Sell 五档产出投资计划。这意味着**它无法回到证据复核辩手
有没有放大**，也无法独立验证一句话的真伪。这不是实现疏漏，是这一层的输入就长这样。

### 7.3 三方风险

`risk_debate_state` 结构同多空：共享 `history`，各自 `*_history`，外加
`latest_speaker` 供轮转判断。三方各说一次后交给 PM。

### 7.4 极化审计的实测结论

详见 `DEBATE_POLARIZATION_AUDIT.md`。要点：注入一条假消息后，

| 辩手处置 | 终局评级 | 携带该说法的产物 |
|---|---|---|
| 忽略 | 不动（Hold） | 1 个（仅新闻报告） |
| 重复升级 | **Sell（位移 2 档）** | **16 个**（含最终决策） |
| 引用但驳斥 | 不动（Hold） | **16 个**（含最终决策） |

即：**「驳斥」不等于「剔除」**——结论可以被保住，但捏造的说法仍然印在最终交付物里；
而唯一确定性的防线是「辩手没有重复它」。实验用桩模型驱动真实图，测的是**架构**，
不是 DeepSeek 的极化倾向。

---

## 8. 决策链与评级解析

```
Research Manager（deep）→ investment_plan
      └─ Trader（quick）→ trader_investment_plan
            └─ 三方风险 → risk_debate_state
                  └─ Portfolio Manager（deep）→ final_trade_decision（终局评级）
```

**结构化输出**：三个裁决角色先 `bind_structured(llm, Schema, name)` 尝试
`with_structured_output`；供应商不支持（或抛错）时回退**自由文本**生成，并由
`schema` 的渲染函数或 `rating.py` 解析。这个回退路径曾经会崩：OpenAI Responses /
Gemini 3 返回 typed content block（list），未归一化时 `parse_rating` 会在**整条管线跑完
之后**因 `.splitlines()` 崩溃——现在统一走 `normalize_content()`。

**评级解析**（`agents/utils/rating.py`）是权威性排序，不是「最左命中」：

```
rank 0（裁决）  最终评级 / 最终投资评级 / 最终投资建议 / 评级结论 / **Rating**:
rank 1（评级）  投资评级 / 推荐评级 / 评级
rank 2（建议）  投资建议 / 操作建议 / 建议 / 推荐
```

同一等级取**最后一次**出现，低等级不覆盖高等级。这条规则修的是一个很隐蔽的错：
一份先复述上游建议、再给出自己裁决的备忘录（`研究经理的投资建议：增持 … 最终评级：卖出`）
以前会被解析成 **Overweight**，并且错误评级会写进记忆日志、作为「过往教训」注入该股票
之后每一次运行。

需要区分「模型选了 Hold」和「读不出评级」时用 `parse_rating_explicit()`（返回 `None`），
记忆日志把读不出评级记成 `unparsed` 而不是编造一个 Hold。

---

## 9. 记忆与反思回路

- `store_decision(ticker, trade_date, decision)` 写一条 `[... | pending]`。
- `update_with_outcome(...)` 事后回填已实现收益、alpha、持仓天数与**反思文本**。
- `get_past_context(ticker, n_same, n_cross, as_of)` 注入提示词：同标的的历史决策 +
  跨标的的教训。**`as_of` 是本轮加的**——此前它取「最近的 N 条」，于是一次 9 月的运行
  会把它的结论**和已实现收益**注入到 5 月的回测里。
- `_fetch_returns` 用 yfinance 从 `trade_date` 起算持有期收益（基准沪深 300）。
  这是**故意向后看**的：衡量结果本身就是反思回路的目的，它写进「事后复盘」字段，
  不是喂给分析师的当期事实。别把它当未来函数「修掉」。
- 这里修过一个致命错：`_fetch_returns` 把**裸 6 位代码**传给 yfinance，而基准那条腿用的是
  正确后缀（`000300.SS`）——`yf.Ticker("600519")` 匹配不到任何东西，于是每条待复盘记录
  都「等下次再试」，记忆日志只进不出，反思功能从未产出过一条结果。现在
  `yahoo_symbol_for_a_stock()` 复用 `_get_prefix`（含北交所）。

---

## 10. 三个入口

### 10.1 CLI（`cli/`）

- `marvel` → 交互式流程；子命令 `analyze`（`--checkpoint` / `--clear-checkpoints`）。
- 9 个分析师的清单**统一来自** `cli/models.py` 的单一注册表，不再有 4 份各自维护的副本。
- 现在走**共享驱动**：`prepare_graph_run` → 流式 → `finalize_graph_run`，外层
  `try/except/finally` 释放 checkpointer 并把裸 traceback 换成可读错误 + 非零退出码。
  此前 CLI 自己拼 state 并自己收尾，于是 `--checkpoint` 是空操作、记忆日志不注入、
  状态不落盘（CLI 跑的分析不出现在 Web 历史里）、决策不写记忆日志。

### 10.2 Web（`web/`）

4 个状态（`web/app.py`）：历史查看 / 运行中 / 完成 / 出错，外加空转欢迎页。

- **默认只绑 `127.0.0.1`**（`.streamlit/config.toml`）。此前没设，而 Streamlit 未设置时
  绑 `0.0.0.0`——等于把一个**没有登录**的 UI 发布到整个网络。
- **API Key 按浏览器会话隔离**：只存在本会话，经 `config["api_key"]` 传给客户端；
  输入框为空回退 `.env`，清除需要点显式按钮。此前它写进进程级 `os.environ` 与共享
  `.env`，于是别的访问者能用你的 Key，而**清空输入框回车会删掉 `.env` 里运维配的那把**。
- 进度面板有暂停/继续/停止（`ProgressTracker.pause/resume/request_stop` 此前**没有任何
  调用方**，取消路径整体是死代码）。
- 结果页导出与历史列表有 `@st.cache_data`（此前 `web/` 一处都没有，每次 rerun 都重新
  生成 PDF 并全量扫描历史目录）。

### 10.3 harness（`marvel/harness/`，本轮新增）

参考 `deepseek-ai/deepseek-harness` 的形状（薄核心 + 可组合部件），与流水线**共存**：

- `ToolRegistry`（工具只定义一次；`from_tools()` 复用流水线的 `args_schema`）
- `AgentLoop`（模型回合 → 工具调用 → 结果，带**显式步数预算**；预算耗尽会**明说**）
- `RunRecorder`（每回合写 JSONL）/ `ReplayModel`（离线回放）
- 真实模型走 `llm_clients.create_llm_client`，**没有第二个 HTTP 客户端**；
  `ModelReply.raw` 保留 SDK 消息对象，让 DeepSeek 的 `reasoning_content` 能原样回传
  （丢了下一轮会 HTTP 400）。
- 工具执行同样包在 `analysis_date_as_of` 里——它不是绕过时点守卫的第二个后门。

详见 `HARNESS.md`。

---

## 11. 工程保障

### 11.1 测试

37 个文件 / **684 passed / 1 skipped**。跳过的是 `test_google_api_key.py`（依赖
`langchain-google-genai`，与 mootdx 的 `httpx<0.26` 无法共存，所以没有 `[google]` extra，
它在默认环境里永久跳过）。

测试的价值取向是「守卫会静默漂移的事实」，例如：

| 守卫 | 防的是什么 |
|---|---|
| `test_docs_consistency.py` | README/CLAUDE/NOTICE 与代码不一致（分析师数、阶段数、许可、资产引用、PEP 639 元数据） |
| `test_point_in_time.py` | 日期窗口不被分析日约束；新增取数函数漏加 `@clamp_arguments` |
| `test_tool_node_coverage.py` | 分析师绑定的工具跑不起来（本轮新增） |
| `test_progress_controls.py` | 暂停/停止路径无人调用 |
| `test_web_security.py` | 绑定地址被改回 0.0.0.0、Key 被写进环境或 `.env` |
| `test_container_and_config.py` | compose 起不了 Web UI、`max_recur_limit` 又变成摆设 |
| `test_pipeline_end_to_end.py` | web runner 与图之间的接口错位（曾导致每次分析都失败） |
| `test_debate_polarization.py` | 假消息传播链的结论被静默改写 |

### 11.2 CI（`.github/workflows/`）

Python **3.10 / 3.11 / 3.12 / 3.13** 四条腿，每条都跑：安装 → `pip check`（暴露不可解析的
extra）→ `pytest` → 根目录 `test_*.py` 残留检查。240 个 commit 的仓库里这套是 green 的。

### 11.3 并发：三把锁，各防一件事

| 锁 | 保护 | 不保护的 |
|---|---|---|
| `_EM_LOCK` | 东财请求的节流 + 重试整段（串行访问） | 非东财源 |
| `_MOOTDX_LOCK`（RLock） | 「选服务器 + 取数」整段（单条 TCP 有状态） | yfinance/新浪兜底路径 |
| `ProgressTracker._lock` | runner 线程写 / UI 线程读的进度与报告 | —（**非重入**，所以 `stage_snapshot()` 刻意不调用 `stage_status()`） |

### 11.4 其它

- 三个入口共用同一驱动与同一份 state 契约，所以 CLI/Web 的分析都会落盘、进历史、写记忆。
- 日志与结果目录：`~/.marvel/{logs,cache,memory}`，可用 `MARVEL_*` 环境变量覆盖。

---

## 12. 现在真正的弱点（诚实清单）

### 架构性（读代码即可确认，与模型无关）

1. **没有来源核实工序。** 没有任何 agent 的职责是判断一条说法是否可信，也没有字段记录
   「未经证实」。质量门控查形状与质量，不查来源。
2. **裁判看不到证据。** Research Manager 只在辩论文本上做判断，无法复核放大。
3. **单条未证实的说法可以走完全程**：实测传播到 16 个产物、把评级推动 2 档；
   即使结论没被带偏，捏造内容仍可能出现在最终交付物里。
4. **回退供应商没有时点守卫。** 裁剪只加在 `a_stock.py`；`route_to_vendor` 的回退链还会
   走到 `y_finance.py` / `yfinance_news.py` / `alpha_vantage*.py`，这些实现没有
   `@clamp_arguments`。A 股默认配置下 `a_stock` 是主供应商，但回退路径仍可能漏。
5. **时点标注是散文，不是强制。** `_snapshot_notice` 说明「这是此刻的快照」，
   但没有任何机制阻止模型忽略它；源本身没有历史时点值的，也补不出来。
6. **记忆日志的跨轮影响仍在**（已加日期上界，但同一天内的多次运行仍会互相注入）。
7. **辩论轮数是交替制而非轮次状态**：`should_continue_debate` 靠
   `current_response.startswith("Bull")` 判断下一个发言者。它现在能工作，但这个判据依赖
   辩手输出的固定前缀（`f"Bull Analyst: {...}"`）——改一句文案就会改变图的走向。

### 运维/产品性

8. CLI 只有一个子命令（`analyze`）+ 交互式流程，没有非交互式的「跑一批标的」入口
   （`examples/run_cases.py` 是脚本，不在 CI 里）。
9. Web UI 无鉴权。缓解手段是回环绑定 + 按会话隔离 Key；一旦要对外提供服务，
   需要真正的登录层。
10. 数据源的时点能力参差不齐（见 §3.1 的表），这不是代码能补齐的，只能标注。

---

## 13. 本次会话做了什么

| commit | 内容 |
|---|---|
| `ae2e8b9` | CLI 补齐 9 个分析师（此前 A 股特化的 5 个在 CLI 里根本跑不起来），清掉上游默认值 |
| `7019e4a` | CLI 改走共享驱动：checkpoint / 记忆日志 / 状态落盘 / 决策写日志一次修好 |
| `6341cbb` | Web UI 安全：回环绑定 + API Key 按会话隔离（此前后者会把运维配的 Key 删掉） |
| `35754fe` | 进度面板暂停/停止（死代码）、界面共享状态改快照、导出与历史加缓存、质量门控尊重分析师子集 |
| `d869a47` | 数据层六处「静默给出貌似合理的错误结果」：行业榜真排序、东财失败重试、mootdx 串行、缓存原子写、800 根窗口披露、当日缓存最终性 |
| `cd5d2c9` | 给「删掉的上游截图与旧架构图」补上回归守卫（此前只删了、没守住） |
| `9299bff` | **第 2 项**：分析日成为取数窗口的唯一权威（工具层锚定 + 数据层兜底 + 记忆日志日期上界）+ `POINT_IN_TIME_AUDIT.md` |
| `6eddd40` | **第 1 项**：`marvel/harness/`（注册表 / 循环 / 录制 / 回放）+ `HARNESS.md` |
| `cfead32` | **第 3 项**：多 Agent 辩论的极化审计 + 离线确定性实验 + `DEBATE_POLARIZATION_AUDIT.md` |

更早还有一轮（文档/许可/元数据与代码脱节、决策评级与未来函数防护的静默出错），
见 `CHANGELOG.md` 的 `[Unreleased] — MARVEL` 分节。

---

## 14. 怎么跑 / 怎么验证

```bash
# 测试（离线，需要 --basetemp 指向工作区内）
python -m pytest tests/ -q -p no:cacheprovider --basetemp=.pytest_tmp\bt

# CLI / Web
marvel                                  # 交互式
marvel analyze --checkpoint             # 带断点续跑
marvel-web                              # Streamlit，默认 127.0.0.1:8501

# 独立 harness（会花钱；先打印 model / 步数 / 录制路径）
python -m marvel.harness --ticker 600519 --date 2026-05-12 --record run.jsonl
python -m marvel.harness --replay run.jsonl   # 离线回放，不需要 key

# 只读审计入口（不是测试）
python scripts/probe_astock_e2e.py      # 联网探针，会真打数据源
```

**测试与真实调用的边界**：`tests/conftest.py` 有出站 socket 守卫，所以测试套件是**强制
离线**的——任何真的联网的代码在测试里都会失败而不是悄悄通过。要看真实数据请用
`scripts/probe_*.py` 或 CLI。
