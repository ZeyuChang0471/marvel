# 时间点审计：分析日 与 取数窗口

> 结论先说：**改分析日会改变取到数据的时间窗口，而且改动之前这个依赖是不受控的。**
> 复盘 2026-05-12 时，模型只要在工具调用里写一个更晚的 `end_date`，报告就会拿到
> 2026 年 9 月的新闻——而报告里看不出任何异常。本文记录审计范围、已修的部分、
> 以及**仍然存在**的残余风险。

审计日期：2026-09-26 ｜ 代码：`main` @ `d869a47` 之后的本次改动 ｜
回归用例：`tests/test_point_in_time.py`

---

## 1. 为什么这会影响回测

MARVEL 的取数工具，日期参数是**模型写的**：

```python
get_news(ticker, start_date, end_date)      # 数据层只在这段窗口内过滤
get_stock_data(symbol, start_date, end_date)
get_indicators(symbol, indicator, curr_date, look_back_days)
get_balance_sheet(ticker, curr_date, freq)  # 以及另外十几个
```

提示词里确实告诉模型「current date is {trade_date}」，但**没有任何东西强制它照做**。
于是「分析日」对取数只是建议：模型写今天的窗口，数据层就按今天的窗口过滤。
数据层过去只做了两件事——在给定窗口内过滤、以及把 `curr_date` 用于自己的裁剪逻辑——
两件事都以「模型填对了」为前提。

修复前的具体漏洞：

| 漏洞 | 表现 |
|---|---|
| `get_news` **根本没有 `curr_date` 参数** | 窗口完全由模型决定，数据层无从校对。回测 5 月可拿到 9 月新闻 |
| `get_stock_data` 只把 `end_date` 收敛到**市场当天** | 不是收敛到分析日。历史复盘同样能拿到分析日之后的 K 线 |
| 记忆日志 `get_past_context(ticker)` **没有日期过滤** | 独立于行情/新闻的第二条通道：先跑过 9 月，再回测 5 月，5 月的提示词里会出现 9 月的结论**和它的已实现收益** |
| `get_concept_blocks` 无日期参数也无标注 | 板块归属基本静态，但每个板块带的「当日涨跌幅」是此刻的值 |
| 三个 vendor 函数的 `@clamp_arguments` 写成了 `curr_date`，而它们的参数叫 `trade_date` | 装饰器静默不生效（由本次新增的结构守卫抓出） |

---

## 2. 修复：两头都堵

核心思想：**分析日是运行（run）的属性，不是某次调用的属性**，所以它由运行注入，
不由模型提供。实现见 `marvel/dataflows/as_of.py`。

### 2.1 工具层（模型连试都试不了）

* 每个「以某个日期为窗口上界」的工具，在函数体开头用 `anchored_date()` 取值：
  绑定了运行日期就用运行日期，**忽略模型写的内容**；模型没写也能正确锚定。
  共 15 个工具（`news_data_tools` / `core_stock_tools` / `technical_indicators_tools` /
  `fundamental_data_tools` / `signal_data_tools`）。
* `ToolNode` 外面套一层 `_guard_tool_node`，在执行时用
  `analysis_date_as_of(state["trade_date"])` 绑定。工具实际是在 ToolNode 里跑的，
  所以这一层才是真正决定工具层语义的地方。
* 锚定行插在 docstring **之后**——插在之前会把 docstring 顶掉，而 LangChain 正是用
  docstring 当工具描述，工具选择会跟着退化。有用例守着。

### 2.2 数据层（兜底，且不依赖调用方）

`a_stock.py` 里**每一个收模型日期的函数**都加了 `@clamp_arguments("curr_date"/"end_date"/"trade_date")`：
上界晚于分析日 → 收敛到分析日并**在输出里写明**；解析不出来的上界 → 按分析日处理
（「读不出来」不等于「没有上限」）。未绑定分析日时**完全不变**，所以直接调用数据层
或跑测试的行为与过去一致。

### 2.3 记忆日志

`get_past_context(ticker, ..., as_of=trade_date)`：只注入**日期 ≤ 分析日**的已结算决策。
`trading_graph.py` 的调用点已经传 `as_of=trade_date`，并有源码级用例守着（防止有人
把参数去掉）。

---

## 3. 逐条取数路径的现状

| 工具 | 锚定方式 | 时点状态 |
|---|---|---|
| `get_stock_data` | `end_date` | 已锚定 + 裁剪；另收敛到市场当天；800 根窗口不足时会说明 |
| `get_indicators` | `curr_date` | 已锚定 + 裁剪；OHLCV 按 `curr_date` 截断 |
| `get_fundamentals` | `curr_date` | 已锚定 + 裁剪；估值是实时快照 → 历史复盘时打标注 |
| `get_balance_sheet` / `cashflow` / `income_statement` | `curr_date`（必填） | 已锚定 + 裁剪；按报告日截断；无报告日的载荷直接拒绝 |
| `get_news` | `end_date` | 已锚定 + 裁剪；发布时间读不出来的条目丢弃；历史窗口可能不完整会说明 |
| `get_global_news` | `curr_date` | 已锚定 + 裁剪；窗口由数据层自己算；只提供最新快讯会说明 |
| `get_profit_forecast` | `curr_date` | 已锚定 + 裁剪；只有当前版本 → 历史复盘时打标注 |
| `get_hot_stocks` | `curr_date` | 已锚定 + 裁剪；同花顺端点本身按日期取数 |
| `get_northbound_flow` | `curr_date` | 已锚定 + 裁剪；历史时略去实时分钟段；历史裁剪；长度不一致拒绝给结论 |
| `get_fund_flow` | `curr_date` | 已锚定 + 裁剪；历史时略去实时分钟段；历史裁剪 |
| `get_dragon_tiger_board` | `trade_date` | 已锚定 + 裁剪（此前装饰器是空操作） |
| `get_lockup_expiry` | `trade_date` | 已锚定 + 裁剪（同上）；历史解禁加 `FREE_DATE <= 分析日` |
| `get_industry_comparison` | `trade_date` | 已锚定 + 裁剪（同上）；排名源只有实时版 → 历史复盘时打标注 |
| `get_concept_blocks` | 无日期参数 | **本次新增**：运行日期是历史时打快照标注 |
| `get_insider_transactions` | 无日期参数 | A 股实现不返回带日期的内容 |

---

## 4. 故意「向后看」的地方（不要当成 bug 修掉）

* `_fetch_returns` / `update_with_outcome`：在 `trade_date` **之后**取价格来算已实现
  收益与 alpha。这是反思回路的目的本身，不是未来函数——它写进的是「事后复盘」字段，
  而不是喂给分析师的当期事实。
* `get_lockup_expiry` 的 `forward_days`：未来待解禁是**已知的日程安排**
  （交易所公告），不是被观测到的行情，输出里也明确标成「未来待解禁」。

---

## 5. 仍然存在的残余风险

1. **回退供应商没有同样的守卫。** 裁剪只加在 `a_stock.py`。`VENDOR_METHODS` 的回退链
   还会走到 `y_finance.py` / `yfinance_news.py` / `alpha_vantage*.py`，这些实现**没有**
   `@clamp_arguments`。A 股默认配置下 a_stock 是主供应商，但回退路径仍可能漏。
   结构守卫目前只扫 `a_stock.py`。
2. **标注是给模型看的散文，不是强制。** `_snapshot_notice` 说明了「这是此刻的快照」，
   但没有任何机制阻止模型忽略它。
3. **补不出历史时点值的源，仍然补不出来。** 本次修复保证的是「未来数据不会**不被察觉**
   地进来」，它无法凭空造出数据源没有保留的历史值。行业排名、概念板块、一致预期、
   实时资金流这几条属于此类：历史复盘时它们要么被略去，要么带标注。
4. **记忆日志的日期比较是字符串比较。** 对 `YYYY-MM-DD` 正确；若将来写入别的日期格式，
   过滤会失效。
5. `get_news` 仍然没有 `curr_date` 形参（改用运行上下文锚定）。这样避免改动
   `route_to_vendor` 的三家供应商签名，但读代码的人需要知道锚定发生在哪一层。

---

## 6. 怎么验证

```bash
python -m pytest tests/test_point_in_time.py -q     # 28 例
python -m pytest tests/ -q                          # 全套
```

用例覆盖三类断言：

* **行为**：绑定分析日后，晚于分析日的新闻/条目被排除且输出带说明；未绑定时行为不变。
* **结构**：`a_stock.py` 里任何收 `curr_date` / `end_date` / `trade_date` 的函数都必须带
  对应的 `@clamp_arguments`——新增取数函数忘了加会被直接拦下（此守卫已反向验证：
  删掉一个装饰器即失败）。
* **调用点**：`trading_graph.py` 必须把 `as_of=trade_date` 传给记忆日志。
