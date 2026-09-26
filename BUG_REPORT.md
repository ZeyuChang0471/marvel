# BUG_REPORT — 本轮 bug 检查

> 方法：三路**只读**并行审计（数据层 / agents+graph / web+CLI+harness），
> 加上我自己对每个模块的直接阅读。**每一条都在写进本文件之前由我复核过**——
> 复核包括读那段代码、构造触发路径、必要时反向验证（把修复还原，看守卫是否失败）。
> 三路审计共报 19 条，其中 1 条的**影响判断是错的**（见 §3），已修正后才收录；
> 另有若干条被明确标注为「我未能验证」。
>
> 校准：`main` @ `cfead32` + 本轮修复（未提交时为工作树）。测试 **737 passed / 1 skipped**。

---

## 1. 已修复（本轮，每条都有回归守卫）

| # | 严重度 | 问题 | 后果 | 守卫 |
|---|---|---|---|---|
| 1 | **高** | `social` 的 ToolNode 只有 `get_news`，而情绪分析师给模型绑了 4 个工具 | 模型调用 `get_fund_flow`（它自己提示词里的「情绪最硬的证据」）时 LangGraph 只回一句「不是有效工具」，情绪报告静默失去主证据 | `test_tool_node_coverage.py`（9 个分析师全量比对，已反向验证） |
| 2 | **高** | CLI `MessageBuffer.section_titles` 只列了 4 个上游分析师，而 `REPORT_SECTIONS` 已扩到 9 | 选中任一 A 股特化分析师，第一份报告落地即 `KeyError: 'volume_price_report'`，**几分钟后、已经付过 token 才失败** | `test_entrypoint_regressions.py::TestCliRendersEveryAnalystReport` |
| 3 | **高**（安全） | `web/launch.py` 只依赖 `.streamlit/config.toml`，而 Streamlit 按 CWD/脚本目录解析该文件 | 从非仓库根目录执行 `marvel-web`（README 推荐用法）→ 配置读不到 → 退回 Streamlit 默认 `0.0.0.0`，**无登录的 UI 暴露到局域网**，等于把此前 C7 的安全修复绕过去了 | `test_web_security.py`（改为「不得绑定非回环」+ 启动器必须显式写回环并可被环境变量覆盖） |
| 4 | **高** | `marvel/harness/` 里没有任何模块读 `.env`（其它五个入口都读） | `HARNESS.md` 里那条命令照抄即失败：`RuntimeError: no DeepSeek API key found` | `test_entrypoint_regressions.py::TestHarnessEntryPoint` |
| 5 | 中 | `_sina_kline_fallback` 用内联的 `"sh" if startswith("6") else "sz"` | 北交所 `4xxxxx/8xxxxx` 被请求成 `sz430047`，新浪回 `null` → 备用源对全部北交所代码返回空；而它存在的意义正是 mootdx 不可用时兜底。CHANGELOG 声称「新浪财报/新闻/K线统一走 `_get_prefix`」，K 线这条路被漏了 | `test_data_layer_bugfixes.py::TestSinaKlinePrefix`（7 个交易所前缀参数化） |
| 6 | 中 | `stockstats_utils.load_ohlcv` 把裸 6 位代码交给 `yf.download` | `yf.download("600519")` 返回**空表而不报错**，指标路径把「代码写错了」显示成「N/A：今天不是交易日」；空表还会被写进缓存 | `test_data_layer_bugfixes.py::TestYahooSymbolMapping` |
| 7 | 中 | Alpha Vantage 的 `_filter_reports_by_date` 永远走不到过滤分支：`_make_api_request` 返回**文本**，而它 `isinstance(result, dict)` 不成立就返回 | 该守卫 docstring 写着「防未来函数」，实际从未执行，分析日之后的财季原样通过 | `test_data_layer_bugfixes.py::TestAlphaVantageDateFilter` |
| 8 | 中 | `python run.py cli` 把已消费的 `cli` token 透传给 Typer | 以 `Got unexpected extra argument(s) (cli)` 退出，交互式 CLI 根本起不来，`--checkpoint` 也无法经 run.py 触达 | `test_entrypoint_regressions.py::TestRunPyCliDispatch` |
| 9 | 中 | `Reflector.reflect_on_final_decision` 未归一化 `content` | 返回 typed content block 的供应商（OpenAI Responses / Gemini 3）会把 list-of-dict 的 repr 写进 trading_memory.md；文件可读、反思变垃圾，且会被重新注入该标的之后的每一次提示词 | `test_memory_log.py::test_reflect_on_final_decision_normalises_block_content` |
| 10 | 中 | 三处裁决/辩论节点重写嵌套状态时漏掉 `judge_decision`（见 §3 说明） | LangGraph 整块替换嵌套值，该键在风险辩论期间从状态里消失；只有一个直接下标读者，且它在 PM 之后才跑，所以目前不崩 | `test_debate_state_contract.py`（5 个节点 + 与 TypedDict 对齐） |
| 11 | 低-中 | `get_dragon_tiger_board` 用裸 `except: pass` 吞掉席位/机构查询失败，且第一段查询抛错时 `data`/`buy_data`/`sell_data` 未绑定 | 接口失败被呈现成「没有机构席位」（本仓库反复要避免的那种混淆）；未绑定局部变量的 `NameError` 也被同一个 `pass` 吞掉 | `test_data_layer_bugfixes.py::TestDragonTigerFailureHandling` |
| 12 | 低 | `get_stock_name` 的入口正则是 `^[03689]\d{5}$` | 北交所 `4xxxxx` 被当成「非 A 股」，UI 只显示裸代码；而 `_get_prefix` / `_normalize_ticker` / 腾讯都支持它 | `test_data_layer_bugfixes.py::TestGetStockNameAcceptsBse` |
| 13 | 低 | `trading_graph._log_state` 是全仓库最后一处非原子持久化写入（裸 `open(w)` + `json.dump`） | 进程被杀/断电会留下半截 JSON；`web/history.load_analysis` 又是裸 `json.load`，历史页打开该条即抛 `JSONDecodeError` | 未单独加守卫（与 `atomic_write_text` 的其它调用点一致，写法已被 §1 的纪律覆盖） |
| 14 | 低 | harness 默认模型取自 `DEFAULT_CONFIG["quick_think_llm"]`（= `gpt-5.4-mini`），而 provider 硬编码 `deepseek` | 向 api.deepseek.com 请求一个它没有的模型；仓库自己的校验器已经警告 | `test_entrypoint_regressions.py::TestHarnessEntryPoint` |
| 15 | 低 | `web/app.py:304` 欢迎页仍写「7位AI分析师」 | 用户可见的产品信息错误（流水线跑 9 个）；此前的 7→9 修正只覆盖了 README/CLAUDE/sidebar | `test_entrypoint_regressions.py::TestNoStaleAnalystCountOnScreen` |

第 2、3 两条是**我自己在本次评审早期引入/遗留的缺口**：把分析师注册表扩到 9 时漏改了
`section_titles`；把 Web UI 绑回环时只改了配置文件、没让启动器不依赖 CWD。写在这里是为了
说明这类「改一处、另一处默默失配」在本仓库有多容易发生——两条现在都有结构守卫。

---

## 2. 已验证但**未修**（需要你拍板，或属于设计取舍）

| 严重度 | 问题 | 为什么我没动 |
|---|---|---|
| 中 | CLI 的进度表 `all_teams`、`save_report_to_disk`、`display_complete_report` 仍写死 4 个上游分析师 | 生成的 5 份 A 股报告**被静默丢弃**，不写盘也不打印（用户被提示保存的产物缺 5 份）。修法是从 `cli/models.py` 注册表派生，但会改动 CLI 的落盘目录结构与展示顺序——这是产品决定 |
| 中 | `market_analyst` 的提示词要求模型给 `get_stock_data` 传 `look_back_days`，而该工具没有这个参数（已实测：langchain 静默丢弃多余字段）；且 `market_lookback_days` 不在 `DEFAULT_CONFIG` 里、全仓库只有一处引用 | K 线窗口因此完全由模型自拟（`end_date` 已被分析日锚定，`start_date` 没有），而提示词又要求它报「近 30 日累计涨跌幅」。修法涉及**改提示词**（会改变模型看到的内容）与新增配置键，属于产品决定 |
| 中 | 运行中点击侧边栏历史项后，进度面板和暂停/停止按钮消失，且无自动回到运行的路径（`viewing_history` 优先于 tracker 状态；只有「开始分析」按钮会清它，而它在运行中是禁用的） | 这是状态机优先级的产品决定（该优先显示历史还是优先显示运行中的分析），且修法会影响交互语义 |
| 低-中 | 导出的 Markdown/PDF 把最终决策打印两次（`risk_debate_state["judge_decision"]` 与 `final_trade_decision` 是同一个字符串，导出把两块都输出） | 纯展示取舍：删哪一块、还是保留双份（一处是「风控决策」语境）应由你定 |
| 低 | 侧边栏历史按钮的 key 用 `hist_{ticker}_{date}`，而历史扫描会把 legazy 目录与重跑的同一日期算成两条 | 需要「legacy 目录 + 同日重跑」同时存在才触发；修法是按路径做 key 并去重，属于低优先 |
| 低 | `sender` 被 `trader.py` 写入、在 `AgentState` 声明，但全仓库无人读取 | 死状态，无害。删它要改状态 schema（会影响 checkpoint 兼容） |

---

## 3. 一处**被修正的审计结论**（重要）

审计报告的原话是：`judge_decision` 被辩论节点抹掉后，Portfolio Manager 用直接下标读取
（`portfolio_manager.py:103`），**会 KeyError**，属于「高」。

我复核后确认**机制对、影响判断错**：

- 机制成立：三个风险辩手与两个研究员的返回字典确实都没有 `judge_decision`，而 LangGraph
  对嵌套 TypedDict 通道是整块替换，所以该键会消失。
- 影响不成立：`portfolio_manager.py:103` 是**写入**不是读取；PM 的读取（104-112）不含
  `judge_decision`。全仓库唯一的直接下标读者是 `trading_graph._log_state:590`，而它在 PM
  写回该键**之后**才运行；其余读者（cli / web / pdf_export / report_viewer）一律用 `.get`。

所以真实性质是**潜在脆弱性**（任何将来插在辩手与 PM 之间的读者会踩到），不是活 bug。
我仍然修了它（并在同类的研究员节点一并补上），因为「重写嵌套状态必须重新给出所有声明键」
是明确的意图；但你在读审计回执时应该知道这一条被下调了。**这正是我不直接转述子代理结论
的原因。**

---

## 4. 明确检查过、**没有**发现问题的区域

- `conditional_logic.py` 的所有 `Msg Clear *` / `tools_*` 节点名与 `setup.py` 注册一致；
  图能编译（36 个节点），没有断头边或死循环。辩论轮数公式（`2×` / `3×`）精确，无 off-by-one。
- 状态键契约：9 个报告键、`data_quality_summary`、`past_context`、`investment_plan`、
  `trader_investment_plan`、`final_trade_decision` 都在读者之前被写入，没有读先于写。
- `rating.py` 经 31 例标签权威性压力测试，只漏掉「评级值换行写在 `**Rating**:` 下一行」
  这种极窄写法。
- `web/progress.py` 的锁纪律正确（快照不重入、暂停走 Event）；`web/runner.py` 的
  停止/出错/丢弃路径自洽。
- `web/pdf_export.py` 在 45 份真实日志上全部生成成功（0 失败）。
- `cli/stats_handler.py` 不会重复计数（同一个 handler 传两层，LangChain 会去重）。
- `_tencent_quote` 字段下标、`_ths_eps_forecast` 列序、`_fetch_news_sina` 的
  `href='…'` 正则——审计一度怀疑，但**实测都正确**（新浪那条实跑返回 20 条带正确日期与
  链接的文章），所以没有收录为 bug。
- `as_of.py`、`interface.py`、`config.py`、`trade_calendar.py`、`llm_clients/*`、
  `marvel/harness/{loop,registry,recorder,types}.py` 未发现问题。

---

## 5. 我未能验证的部分（不要当作已验证）

- **checkpoint 续跑路径**（`checkpoint_enabled=True` → `prepare_graph_run` 返回
  `initial_state=None`）：只读了代码，没有执行。
- **真实 API 行为**：本轮所有修复的验证都在离线、桩模型或固定数据下完成。
  唯一涉及真实端点的是「新浪北交所前缀」与「腾讯北交所名称」两条，由审计子代理实测
  （我复核了代码路径与请求构造），但**我没有在本机复跑那两次真实请求**。
- **yfinance 路径**：`yf.download("600519")` 返回空表这一步来自仓库自己的
  CHANGELOG/docstring 与子代理的离线推理，本机 yfinance 的 cookie 库不可写，没能实跑。
  我修的是映射逻辑本身（`_yahoo_symbol` 的行为有单测），不是「Yahoo 会拒绝裸代码」这一步。
- **Streamlit 的实际绑定地址**：由子代理在本机测量（`get_config_options` 在仓库根目录返回
  `127.0.0.1`、在 `%TEMP%` 返回 `None`）。我没有重启一次 Streamlit 去复测。

---

## 6. 本轮测试与验证

- 测试：**737 passed / 1 skipped**（新增 59 例：`test_tool_node_coverage.py` 11、
  `test_data_layer_bugfixes.py` 25、`test_entrypoint_regressions.py` 16、
  `test_debate_state_contract.py` 7，另有既有文件内的新增用例）。
- 反向验证：`social` ToolNode 与 `a_stock` 的 `@clamp_arguments` 两处守卫，把修复还原后
  对应用例确实失败；asset hygiene 守卫同理。
- 过程副产物：写 `_filter_reports_by_date` 的修复时我自己引入过一个 bug
  （`curr_date` 为空时返回了解析后的 dict 而不是原字符串），被同批新写的用例当场抓到并修掉。
