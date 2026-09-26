# Changelog

> **Attribution note.** This changelog is **inherited from the upstream project**
> (`simonlin1212/TradingAgents-Astock`), so entries below `[0.2.13]` describe that
> project. Two consequences when reading it here:
>
> - Paths and environment variables in older entries use the pre-rename names
>   (`tradingagents/…`, `~/.tradingagents/…`, `TRADINGAGENTS_MEMORY_LOG_PATH`);
>   in MARVEL those are `marvel/…`, `~/.marvel/…` and `MARVEL_MEMORY_LOG_PATH`.
> - The alpha benchmark note "alpha vs SPY" is upstream's; MARVEL uses CSI 300.
>
> MARVEL's own version number tracks upstream's `0.2.13` baseline. See
> `CHANGES_FROM_UPSTREAM.md` and `NOTICE` for what MARVEL changed.

All notable changes to TradingAgents are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Breaking changes within the 0.x line are called out explicitly.

## [Unreleased] — MARVEL

审查驱动的修复，全部由 `tests/` 里的回归用例守着。三个主题：**决策评级的静默出错**、
**未来函数防护的漏洞**、**文档/许可证/元数据与代码脱节**。

### Fixed — 决策评级（静默给出错误结论）

- **评级标签改成按权威性排序，同级取最后一次出现**（`rating.py`）。原先「最左命中就
  返回」，于是一份先复述上游建议、再给出自己裁决的 PM 备忘录
  （`研究经理的投资建议：增持 … 最终评级：卖出`）会被解析成 **Overweight**。报告正文
  完全看不出异常，而错误评级会写进记忆日志、再作为「过往教训」注入该股票之后每一次运行。
  规则：`最终评级 / **Rating** / 评级结论` > `评级 / Rating` > `投资建议 / 建议`。
- **解析不出评级时不再伪装成 Hold**：新增 `parse_rating_explicit()`（返回 `None`），
  记忆日志把读不出评级的情况记为 `unparsed` 而不是编造一个「模型选了 Hold」的判断；
  `parse_rating()` 落回默认值时记 warning。
- **结构化输出回退路径归一化 `content`**（`structured.py`）。OpenAI Responses / Gemini 3
  会返回 typed content block（list），未归一化时 `parse_rating` 会在**整条管线跑完之后**
  因 `.splitlines()` 崩溃。
- **质量门控 prompt 覆盖全部 9 个分析师**（`quality_gate.py`）。「N 位分析师」与输出表格
  原先写死成 7，而循环评 9 个——量价与宏观分析师永远不会被打分，但下游每个辩手都被告知
  「C/D/F 的报告要降低依赖」。
- 评级正则加词边界，`operating margin: 25%` 不再被当成 `rating` 标签。

### Fixed — 未来函数（历史复盘里混进今天的数字）

- **`get_global_news` 真正按发布时间裁剪**。原先算出了窗口却只把它打进标题字符串，正文
  一条不裁——复盘历史日期时报告里出现**今天**的新闻，标题却写着
  `from {start_date} to {curr_date}`。四个分析师消费这段文本。现在：窗口外的条目丢弃、
  无法确定发布时间的条目丢弃、历史窗口下明确标注「源只提供最新快讯，该窗口可能不完整」，
  全空时区分「源不能回溯」与「那几天没有新闻」。
- **三张财报的 `curr_date` 改为必填**，数据层在 `curr_date` 缺失时退到市场当天而不是
  跳过裁剪。此前 `if curr_date and …` 配上工具默认的 `None`，等于没有设防。
- **`get_stock_data` 的 `end_date` 收敛到市场当天**并说明原因（`end_date` 由模型给出，
  没有任何提示词要求它填分析日）。
- **北向资金**：复盘历史时不再取今天的实时分钟流；`hgt/sgt/time` 长度不一致时拒绝输出
  净流入结论（原先直接把两条不同口径序列的末元素相加，方向与量级都会失真并落盘）；
  本地历史缓存裁剪到分析日。
- **解禁历史记录加上界**：`FREE_DATE <= trade_date`，不再把未来的解禁批次算进「历史解禁」。
- **行业板块排名**在历史日期下加未来函数告警；`get_hot_stocks` 默认日期改用市场时区而不是
  宿主时钟，且 `curr_date` 改为必填。
- **`get_news`** 丢弃发布时间无法解析的文章（原先 `except: pass` 后照常收录），并在有内容
  返回时也说明丢了多少条、以及历史窗口可能不完整。

### Fixed — 数据正确性

- **北交所 `4xxxxx` 代码路由**（`_get_prefix`）：430047/400xxx 原先落到 `sz`，腾讯返回空的
  `v_pv_none_match` 行被解析器跳过——PE/PB/市值/涨跌停静默消失。新浪财报/新闻/K线共享的
  前缀规则也统一走 `_get_prefix`（此前内联的 `"6"→sh 否则 sz` 把北交所派到 sz）。
- **反思/学习回路真正能落地**：`_fetch_returns` 把**裸 6 位代码**传给 yfinance，而基准那
  一条腿用的是正确后缀（`000300.SS`）——`yf.Ticker("600519")` 匹配不到任何东西，于是每条
  待复盘记录都「等下次再试」，记忆日志只进不出，反思功能从未产出过一条结果。新增
  `yahoo_symbol_for_a_stock()`（复用 `_get_prefix`，含北交所），并让空结果留下日志而不是
  被当成「价格还没出现」静默吞掉。
- **`_em_get` 节流真正串行**：原先读时间戳→sleep→请求→写时间戳是无锁的 check-then-act，
  两个线程会同时放行——而 README/CLAUDE.md 承诺的正是「串行限流」。
- **删除 `llm_clients/factory.py` 的 `claude_agent_sdk` 分支**：它 import 一个不存在的
  模块，把「不支持的 provider」报成 `ModuleNotFoundError`。补上工厂测试。

### Fixed — 文档 / 许可证 / 元数据与代码脱节

- **`pyproject.toml` 使用 SPDX 表达式并登记许可证文件**（PEP 639）：
  `license = "Apache-2.0 AND PolyForm-Noncommercial-1.0.0"` +
  `license-files`（LICENSE / LICENSE-TradingAgents-AShare.txt / NOTICE / LICENSING.md），
  `build-system` 提升到 `setuptools>=77`。此前的自由文本字段无法被工具解析，且与
  `LICENSING.md` 自己写明的「非商业约束只对第 3、4 项有效」相矛盾。
- **依赖表按实际 import 重写**：补上被直接 import 却未声明的 `pydantic`、`python-dateutil`；
  删掉 7 个从未 import 的运行时依赖（`backtrader`、`langchain-experimental`、`parsel`、
  `pytz`、`redis`、`setuptools`、`tqdm`）。
- **不再声明 `[google]` extra**：它与 mootdx 的 `httpx<0.26` 无法共存，是装不上的死元数据；
  `google_client.py` 的 ImportError 早已给出正确的显式安装命令，文档现在与它一致。
- **许可证声明统一**：`CLAUDE.md` 的「协议: Apache 2.0」与 `DEV_LOG.md` 的「可商用」与
  README/LICENSING 冲突，已全部改为指向 `LICENSING.md` 的混合许可表述。
- **`NOTICE` 的 Required Notice 补齐 4 个 PolyForm 文件**（此前只列 1 个，而
  `LICENSING.md` 与 `LICENSE-TradingAgents-AShare.txt` 列 4 个）；补上 Apache-2.0 §4(b)
  要求的修改声明。
- **删除含可执行价位的示例产物**：`examples/cases/002594*`、`300750*` 含建仓价/止损位/
  仓位/两个目标价，与 LICENSING.md「该能力被完整移除」的声明直接冲突（`_summary.json`
  里的 `decision_preview` 尤其严重）。`examples/run_cases.py` 现在落盘前强制脱敏。
- **README/CLAUDE.md 的 7→9 个分析师、12→14 个阶段、默认模型、百度数据源描述、
  `streamlit run web/launch.py`、项目结构树**全部对齐代码；`web/components/sidebar.py`
  里对用户显示的「7 个 Analyst」也一并修正。
- **README 删除「支持上游原作者」捐赠段**（微信赞赏码 + 爱发电 / Buy Me a Coffee），
  连同已无人引用的 `assets/wechat-sponsor.jpg`，并把写死的「65K Stars」换成 shields.io
  实时徽章——实测该数字已 **107,495**，写死必然过期。上游**归属**不受影响，仍保留在
  README「致谢与代码血缘」、`NOTICE` 与 `LICENSING.md`（Apache-2.0 §4 要求随分发保留）。
- **`CHANGELOG.md` 标注为继承自上游**并说明旧路径/环境变量；`CHANGES_FROM_UPSTREAM.md`
  与 `DEV_LOG.md` 加上「计数与包名已过时」的说明；`issues/` 标注为上游归档。
- **清掉零引用的图片资产**：`assets/wechat-sponsor.jpg`（随捐赠段移除）、5 张旧流水线的
  角色图（`analyst` / `researcher` / `risk` / `schema` / `trader.png`，画的是 7 分析师时代
  的架构），以及 `assets/cli/` 下 4 张**上游 TradingAgents 的 CLI 截图**——它们显示 4 个
  分析师、美股 SPY、上游字标与工具名，其中一张还写着「trim SPY exposure by ~25-30%」，
  正是 LICENSING.md 声明已移除的仓位建议。
- **CLI 补齐到 9 个分析师**：`cli/models.py`、`cli/utils.py:ANALYST_ORDER`、
  `MessageBuffer.ANALYST_MAPPING` 与 `REPORT_SECTIONS`、`main.ANALYST_ORDER` 共 **5 处**各自
  维护着一份只有 4 个分析师的清单。最严重的是 `cli/main.py` 用本地那份过滤要传给
  `MarvelGraph` 的选择——**A 股特化的 5 个分析师在 CLI 里根本跑不起来**，报告也不显示。
  现在统一到 `cli/models.py` 的单一注册表，并由 `tests/test_cli_consistency.py` 对齐
  `marvel/graph/setup.py`。
- **CLI 去掉上游遗留**：welcome 字标曾拼的是 `TradingAgents`（同一屏标题却是 MARVEL）；
  打印过 `© Tauric Research`（等于主张本项目的版权归属，已改为归属表述）；默认股票与
  示例仍是 `SPY` / `CNC.TO` / `7203.T` / `0700.HK`（**每一个都会被 `safe_ticker_component`
  拒绝**），现改为 `600519` 与 A 股示例。
- **CI 的 action 升到 Node 24 版本**：`actions/checkout@v4 → @v5`、
  `actions/setup-python@v5 → @v6`，清掉每次运行都出现的 Node 20 弃用告警。
- **CLI 改走与 Web UI 相同的驱动路径**：`cli/main.py` 原先直接调
  `propagator.create_initial_state` / `get_graph_args` 并自己 `process_signal` 收尾，
  于是 **`--checkpoint` 是空操作**（checkpointer 从未安装）、记忆日志上下文不注入、
  状态不落盘（CLI 跑的分析不出现在 Web 历史里）、决策不写记忆日志（反思回路对 CLI
  完全失效）。现在走 `prepare_graph_run` → stream → `finalize_graph_run`，并用
  `try/except/finally` 释放 checkpointer、把裸 traceback 换成可读错误 + 非零退出码。
- **根目录不再有 import 即执行的脚本**：`test_astock.py` / `test_data_quality.py` /
  `test.py` 移为 `scripts/probe_*.py`（加 `__main__` 守卫），`main.py`（上游美股 demo，
  模块层跑一次 NVDA 分析）删除，`run.py` / `run_single.py` 的全部模块层副作用
  （重配 stdio、`set_config`、`sys.exit`、打印 key 片段）移入 `main()`。
- **`conftest.py` 真正隔离**：key 占位符不再保留开发者本机的真实 key；新增出站连接守卫，
  让「测试套件离线」这句声明变成强制事实。
- **CI**：加 `pip check`（能暴露不可解析的 extra）、Python 3.13 一条腿、以及根目录
  `test_*.py` 残留检查。
- **`web/history.py` 读取配置里的结果目录**（原先硬编码 `~/.marvel/logs`，设了
  `MARVEL_RESULTS_DIR` 的部署侧边栏历史永远空）。
- **`_log_state` 对 `trade_date` 做路径组件校验**（ticker 早就有，date 没有）。

### Tests

- 371 → 495 个通过用例。新增 `test_docs_consistency.py`（30+ 条把文档与代码钉在一起的
  检查，含「导入即执行」的 AST 扫描与 Python 3.10 兼容检查）、`test_example_artifacts.py`、
  `test_llm_factory.py`、`test_em_throttle.py`、`test_return_resolution.py`，
  以及 look-ahead / rating 的回归矩阵。
- CI 的 **Python 3.10** 一条腿抓到 `tests/test_docs_consistency.py` 无条件 `import tomllib`
  （PEP 680，仅 3.11+ 有）——本地跑 3.13 永远看不到，3.11/3.12/3.13 也全绿。已加 `tomli`
  回退（dev extra 带 `python_version < "3.11"` 标记），并补两条守卫：3.11+ stdlib 必须包在
  `try/except ImportError` 里、全部源码须符合 3.10 语法。

## [0.2.13] — 2026-06-04

### Security

- **CLI 路径穿越加固（#51，感谢 @mituxunzhi 报告并给出修复方向）**：CLI 是唯一未对 ticker 做
  路径组件校验的入口（Web UI / `a_stock.py` / `checkpointer.py` / `stockstats_utils.py` 早已统一走
  `safe_ticker_component`）。ticker 会被拼进 `results_dir / <ticker> / <date>` 和报告保存路径，
  形如 `../../tmp/evil` 的输入可写到目标目录之外。三处加固：
  - `cli/utils.py:normalize_ticker_symbol()` — 现在委托 `safe_ticker_component()` 校验（拒绝
    `/`、`..`、`~`、`\0`、绝对路径、纯点等），并返回校验/解析后的安全值（中文名自动解析为 6 位代码）；
  - `cli/main.py:get_ticker()` — 输入后即校验，非法则提示并**重新询问**（而非崩溃），返回安全值；
  - `cli/main.py` 报告保存 — 保存路径先 `.resolve()`，若落在当前目录之外则**提示并要求确认**，
    拒绝则取消保存。
  - 实测：`../../tmp/evil`、`/etc/passwd`、`~/secret`、`a/../../b`、`\x00evil`、`.` 等 11 个穿越载荷
    全部被拒；`SPY` / `600519` / `0700.HK` / `^GSPC` / `BRK.B` 等正常代码全部通过且保留交易所后缀。

### 说明

- 纯 CLI 入口安全加固，复用既有 `safe_ticker_component` 校验器，数据层 / Agent 逻辑零改动。

## [0.2.12] — 2026-06-03

### Fixed

- **PDF 导出中文崩溃（#54）**：项目依赖 `fpdf2`，但它和早已废弃的 `pyfpdf`（1.x）**都以 `fpdf`
  名称导入**，二者共存时谁后装谁生效。用户环境里若残留 pyfpdf，导出中文报告会在库内部抛出晦涩的
  `UnicodeEncodeError: 'latin-1' codec can't encode`（pyfpdf 用 latin-1 编码每一页）。
  `web/pdf_export.py` 新增 `_ensure_fpdf2()`：导出前检测 fpdf 版本，若是旧库则抛出**可操作**的中文
  提示（`pip uninstall -y fpdf && pip install "fpdf2>=2.8.0"`），不再让 PDF 渲染到一半崩溃。
- **Docker 内无法导出 PDF（#48）**：运行镜像基于 `python:3.12-slim`，不含任何中文字体，
  `_find_cjk_font()` 返回 None → 抛「未找到中文字体」。Dockerfile 运行阶段新增
  `apt-get install fonts-noto-cjk`，容器内 PDF 导出开箱即用。
- **DeepSeek/通义/智谱等报 `OPENAI_API_KEY must be set`（#42）**：这些 OpenAI 兼容供应商各自需要
  **专属环境变量**（DeepSeek=`DEEPSEEK_API_KEY`、通义=`DASHSCOPE_API_KEY`、智谱=`ZHIPU_API_KEY`、
  MiniMax=`MINIMAX_API_KEY` 等），但 key 缺失时 ChatOpenAI 只会抛出令人误解的 `OPENAI_API_KEY` 错误。
  `openai_client.py` 现在在缺 key 时**明确指出该供应商对应的环境变量名**；Web 侧边栏 help 文案也补齐了
  每个供应商的 key 变量对照，避免用户设错。

### 说明

- 三项均为环境/配置类问题的健壮性修复，数据层与 Agent 逻辑无改动。PDF 修复经 fpdf2 实测生成
  中文报告通过 + 旧库检测单测通过；#42 经 api_key 解析分支单测全用例通过。

## [0.2.11] — 2026-05-30

### Changed

- **东财接口统一限流防封（移植自 a-stock-data v3.2）**：数据层 `a_stock.py` 里所有指向
  `eastmoney.com` 的请求（push2 / push2his / datacenter-web / search-api / np-weblist
  共 7 个调用点）统一收口到新的节流入口 `_em_get()`，多 Agent 投研跑批量分析时不再触发
  临时封 IP（社区实测东财风控：每秒 >5 / 并发 ≥10 / 1 分钟 ≥200 / 5 分钟 ≥300 触发封禁，
  多位用户反馈过）。具体：
  - 模块级 last-call 时间戳 + 最小间隔 `EM_MIN_INTERVAL`（默认 1.0s，可用同名环境变量覆盖）
    + 0.1~0.5s 随机抖动，串行限流，QPS ≤ 1；
  - 复用 `requests.Session`（Keep-Alive）+ 默认 UA；各端点保留自己的 Referer/Origin header；
  - **仅东财接口限流**——mootdx(TCP) / 腾讯 / 新浪 / 同花顺 / 财联社 / 百度 等非东财源
    不受影响（实测不封 IP）。批量场景可设 `EM_MIN_INTERVAL=1.5~2` 进一步降速。

### Tested

- 实测 4 次连续 `_em_get` 请求东财 push2（600519 = 贵州茅台），HTTP 200 返回真实数据；
  相邻调用间隔 1.47 / 1.18 / 1.42s 均 ≥1.0s，限流生效。
- `get_industry_comparison` / `get_fund_flow` / `get_dragon_tiger_board` 三个东财公共函数
  端到端跑通（走同一已验证的 `_em_get` 通道）；`py_compile` 通过；grep 复核：7 个 `_em_get`
  调用点 + 0 个残留 `_req.` + 8 个非东财源（mootdx/腾讯/新浪/同花顺/财联社/百度）未被误伤。

---

## [0.2.10] — 2026-05-30

### Added

- **Web UI 支持第三方 / 代理 API 网关（#35）**：侧边栏新增「API Base URL」输入框，
  也可在 `.env` 设 `BACKEND_URL`。方便国内用户通过中转网关访问 Claude / OpenAI 等模型
  （API Key 仍从 `.env` 读取，如 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`）。
  侧边栏输入优先于环境变量，留空则用所选供应商官方地址。

---

## [0.2.9] — 2026-05-30

### Added

- **Markdown 报告导出**：分析结果页新增「下载 Markdown」按钮。MD 导出零字体依赖、
  跨平台永远可用，是 PDF 之外的稳妥兜底（#17 多位用户请求）。

### Fixed

- **PDF 中文字体跨平台崩溃（#22 / #30 / #31）**：原 `_FONT_CANDIDATES` 只列了
  macOS/Linux 字体，Windows 用户找不到中文字体 → fpdf 回退 Helvetica → 渲染中文时
  抛 `FPDFUnicodeEncodingException` / `Character "股" ... outside the range`。
  现改为**按操作系统排序的字体候选**（Windows 微软雅黑/黑体/宋体、macOS 苹方、
  Linux Noto/文泉驿）+ 递归扫描字体目录兜底。
- **PDF 失败拖垮整个结果页**：`generate_pdf` 原先在结果页渲染时被 eager 调用，一旦
  报错整页崩成 traceback，用户连分析结果都看不到。现改为 **try/except 包裹 + 懒生成**，
  PDF 失败只禁用 PDF 按钮并提示改用 Markdown，分析报告照常显示。
- **长串中文表格/段落渲染报错（#31）**：`multi_cell` 遇到无空格的长中文串抛
  `Not enough horizontal space to render a single character`。已为内容 `multi_cell`
  加 `wrapmode="CHAR"` 并复位左边距，中文按字符正确换行。
- **缺字体时优雅降级**：系统无任何中文字体时，`generate_pdf` 抛出清晰中文报错
  （指引安装字体或改用 Markdown），不再是深层 fpdf traceback。

### Tested

- Streamlit 1.50 环境用 fpdf2 2.8.4 实测：含中文标题、表格、列表、200 字无空格长串的
  报告成功生成 7 页 PDF（目视确认中文渲染无乱码、长串正确换行）；Markdown 导出正常；
  无字体路径正确抛 RuntimeError。

---

## [0.2.8] — 2026-05-30

### Fixed

- **Web UI 侧边栏收起后无法展开（#36）**：为录视频清爽化界面的自定义 CSS 把整个
  顶栏 `stHeader` 和工具栏 `stToolbar` 都 `display:none` 掉了。但 Streamlit ≥1.36 的
  「展开侧边栏」按钮 `stExpandSidebarButton` 正好嵌在工具栏内部，于是侧边栏一旦收起
  ——无论是手动点收起箭头，还是**页面缩放 / 窄屏时 Streamlit 自动收起**——展开按钮
  跟着被隐藏，再也调不出来，刷新、重启都没用。原先那行兜底的 `collapsedControl`
  选择器是旧版 DOM，在 1.45+ 已不存在，等于没写。
  修复：不再整个隐藏顶栏/工具栏，改为**保留二者、将 header 透明化、只精准隐藏
  Deploy 按钮 / 主菜单 / 状态条 / 装饰条**，侧边栏展开按钮恢复可见可点，录屏依旧干净。
  已用 Streamlit 1.50 + headless Chrome 在收起/展开两种状态下实测验证。

---

## [0.2.7] — 2026-05-19

### Fixed

- **百度 PAE 资金流下线**：`fundflow` + `fundsortlist` 接口已返回空，
  `get_fund_flow()` 全部替换为东财 push2 资金流 API（分钟级 + 日级 20 天）
- **龙虎榜机构动向**：`RPT_ORGANIZATION_BUSSINESS` 报表配置已下线，
  改用 BUY/SELL 席位明细筛选 `OPERATEDEPT_CODE="0"`（机构专用席位）
- **东财全球资讯**：新增必填参数 `req_trace`（UUID），否则返回 403

---

## [0.2.6] — 2026-05-19

### Fixed

- **依赖冲突**：`langchain-google-genai` 移至可选依赖组 `[google]`，
  消除与 mootdx 的 httpx 版本冲突。`pip install -e .` 开箱即用，
  需要 Google Gemini 时 `pip install -e ".[google]"`。
- **WebUI 模型写死 minimax**：侧边栏新增 LLM 供应商和模型选择器，
  支持 9 个供应商（MiniMax/DeepSeek/Qwen/GLM/OpenAI/Anthropic/Google/xAI/Ollama），
  默认仍为 MiniMax 但用户可自由切换。
- **阶段分析内容消失**：进度面板现在展示所有已完成阶段的报告（按时间倒序），
  不再只显示最新的一个。最新阶段自动展开，历史阶段可点击展开。

### Changed

- `.env.example` 补充 `MINIMAX_API_KEY=` 条目
- README 快速开始增加 Google 可选依赖安装说明
- README Web UI 功能列表更新

## [0.2.5] — 2026-05-17

### Breaking Changes

- **移除 akshare 依赖** — `akshare>=1.18.0` 从 `pyproject.toml` 中删除。
  所有原 akshare 调用已替换为直接 HTTP API（东财 datacenter、新浪财经、
  同花顺 10jqka、财联社 cls.cn、百度股市通）。

### Changed

- `tradingagents/dataflows/a_stock.py` 全面重构数据获取层：
  - `get_stock_data()` → 新浪 JSON K线 API + push2.eastmoney 实时行情
  - `get_stock_info()` → push2.eastmoney 个股基本信息
  - `get_stock_news()` → 东财 np-weblist 滚动新闻（已有，无变化）
  - `get_financial_data()` → 新浪财经财报三表 API
  - `get_market_news()` → 财联社 cls.cn 快讯 + 东财 np-weblist
  - `get_analyst_forecast()` → 同花顺 10jqka EPS 一致预期
  - `get_dragon_tiger_board()` → 东财 datacenter RPT_DAILYBILLBOARD
  - `get_restricted_release()` → 东财 datacenter RPT_LIFT_STAGE
  - `get_industry_overview()` → push2.eastmoney 板块行情
- 新增内部 helper：`_eastmoney_datacenter()`、`_ths_eps_forecast()`、`_sina_kline_fallback()`
- 所有函数签名和返回格式保持不变，对上层 Agent 透明

### Fixed

- 彻底消除 akshare + pandas 3.0 + pyarrow 的 `ArrowInvalid` 崩溃问题
- 消除 akshare 与 mootdx 的 httpx 版本冲突

## [0.2.4] — 2026-04-25

### Added

- **Structured-output decision agents.** Research Manager, Trader, and Portfolio
  Manager now use `llm.with_structured_output(Schema)` on their primary call
  and return typed Pydantic instances. Each provider's native structured-output
  mode is used (`json_schema` for OpenAI / xAI, `response_schema` for Gemini,
  tool-use for Anthropic, function-calling for OpenAI-compatible providers).
  Render helpers preserve the existing markdown shape so memory log, CLI
  display, and saved reports keep working unchanged. (#434)
- **LangGraph checkpoint resume** — opt-in via `--checkpoint`. State is saved
  after each node so crashed or interrupted runs resume from the last
  successful step. Per-ticker SQLite databases under
  `~/.tradingagents/cache/checkpoints/`. `--clear-checkpoints` resets them. (#594)
- **Persistent decision log** replacing the per-agent BM25 memory. Decisions
  are stored automatically at the end of `propagate()`; the next same-ticker
  run resolves prior pending entries with realised return, alpha vs SPY, and
  a one-paragraph reflection. Override path with `TRADINGAGENTS_MEMORY_LOG_PATH`.
  Optional `memory_log_max_entries` config caps resolved entries; pending
  entries are never pruned. (#578, #563, #564, #579)
- **DeepSeek, Qwen (Alibaba DashScope), GLM (Zhipu), and Azure OpenAI**
  providers, plus dynamic OpenRouter model selection.
- **Docker support** — multi-stage build with separate dev and runtime images.
- **`scripts/smoke_structured_output.py`** — diagnostic that exercises the
  three structured-output agents against any provider so contributors can
  verify their setup with one command.
- **5-tier rating scale** (Buy / Overweight / Hold / Underweight / Sell) used
  consistently by Research Manager, Portfolio Manager, signal processor, and
  the memory log; Trader keeps 3-tier (Buy / Hold / Sell) since transaction
  direction is naturally ternary.
- **Pytest fixtures** — lazy LLM client imports plus placeholder API keys so
  the test suite runs cleanly without credentials. (#588)

### Changed

- **`backend_url` default is now `None`** rather than the OpenAI URL. Each
  provider client falls back to its native default. The previous default
  leaked the OpenAI URL into non-OpenAI clients (e.g. Gemini), producing
  malformed request URLs for Python users who switched providers without
  overriding `backend_url`. The CLI flow is unaffected.
- All file I/O passes explicit `encoding="utf-8"` so Windows users no longer
  hit `UnicodeEncodeError` with the cp1252 default. (#543, #550, #576)
- Cache and log directories moved to `~/.tradingagents/` to resolve Docker
  permission issues. (#519)
- `SignalProcessor` reads the rating from the Portfolio Manager's rendered
  markdown via a deterministic heuristic — no extra LLM call.
- OpenAI structured-output calls default to `method="function_calling"` to
  avoid noisy `PydanticSerializationUnexpectedValue` warnings emitted by
  langchain-openai's Responses-API parse path. Same typed result, no warnings.

### Fixed

- Empty memory no longer triggers fabricated past-lessons in agent prompts;
  the memory-log redesign makes this structurally impossible since only the
  Portfolio Manager consults memory and only when entries exist. (#572)
- Tool-call logging processes every chunk message, not just the last one, and
  memory score normalization handles empty score arrays. (#534, #531)

### Removed

- `FinancialSituationMemory` (the per-agent BM25 system) and the dead
  `reflect_and_remember()` plumbing; subsumed by the persistent decision log.
- Hardcoded Google endpoint that caused 404 when `langchain-google-genai`
  changed its API path. (#493, #496)

### Contributors

Thanks to everyone who shaped this release through code, design, and reports:

- [@claytonbrown](https://github.com/claytonbrown) — checkpoint resume (#594), test fixtures (#588), design feedback on cost tracking (#582) and structured validation (#583)
- [@Bcardo](https://github.com/Bcardo) — memory-log redesign (#579), empty-memory hallucination report (#572), encoding fix proposal (#570)
- [@voidborne-d](https://github.com/voidborne-d) — memory persistence design (#564), portfolio manager state fix (#503)
- [@mannubaveja007](https://github.com/mannubaveja007) — structured-output feature request (#434)
- [@kelder66](https://github.com/kelder66) — RAM-only memory issue (#563)
- [@Gujiassh](https://github.com/Gujiassh) — tool-call logging fix (#534), test stub PR (#533)
- [@iuyup](https://github.com/iuyup) — memory score normalization fix (#531)
- [@kaihg](https://github.com/kaihg) — Google base_url fix (#496)
- [@32ryh98yfe](https://github.com/32ryh98yfe) — Gemini 404 report (#493)
- [@uppb](https://github.com/uppb) — OpenRouter dynamic model selection (#482)
- [@guoz14](https://github.com/guoz14) — OpenRouter limited-model report (#337)
- [@samchenku](https://github.com/samchenku) — indicator name normalization (#490)
- [@JasonOA888](https://github.com/JasonOA888) — y_finance pandas import fix (#488)
- [@tiffanychum](https://github.com/tiffanychum) — stale import cleanup (#499)
- [@zaizou](https://github.com/zaizou) — Docker permission issue (#519)
- [@Stosman123](https://github.com/Stosman123), [@mauropuga](https://github.com/mauropuga), [@hotwind2015](https://github.com/hotwind2015) — Windows encoding bug reports (#543, #550, #576)
- [@nnishad](https://github.com/nnishad), [@atharvajoshi01](https://github.com/atharvajoshi01) — encoding fix proposals (#568, #549)

## [0.2.3] — 2026-03-29

### Added

- **Multi-language output** for analyst reports and final decisions, with a
  CLI selector. Internal agent debate stays in English for reasoning quality. (#472)
- **GPT-5.4 family models** in the default catalog, with deep/quick model split.
- **Unified model catalog** as a single source of truth for CLI options and
  provider validation.

### Changed

- `base_url` is forwarded to Google and Anthropic clients so corporate proxies
  work consistently across providers. (#427)
- Standardised the Google `api_key` parameter to the unified `api_key` form.

### Fixed

- Backtesting fetchers no longer leak look-ahead data when `curr_date` is in
  the middle of a fetched window. (#475)
- Invalid indicator names from the LLM are caught at the tool boundary instead
  of crashing the run. (#429)
- yfinance news fetchers respect the same exponential-backoff retry as price
  fetchers. (#445)

### Contributors

- [@ahmedk20](https://github.com/ahmedk20) — multi-language output (#472)
- [@CadeYu](https://github.com/CadeYu) — model catalog typing (#464)
- [@javierdejesusda](https://github.com/javierdejesusda) — unified Google API key parameter (#453)
- [@voidborne-d](https://github.com/voidborne-d) — yfinance news retry (#445)
- [@kostakost2](https://github.com/kostakost2) — look-ahead bias report (#475)
- [@lu-zhengda](https://github.com/lu-zhengda) — proxy/base_url support request (#427)
- [@VamsiKrishna2021](https://github.com/VamsiKrishna2021) — invalid indicator crash report (#429)

## [0.2.2] — 2026-03-22

### Added

- **Five-tier rating scale** (Buy / Overweight / Hold / Underweight / Sell)
  introduced for the Portfolio Manager.
- **Anthropic effort level** support for Claude models.
- **OpenAI Responses API** path for native OpenAI models.

### Changed

- `risk_manager` renamed to `portfolio_manager` to match the role description
  shown in the CLI display.
- Exchange-qualified tickers (e.g. `7203.T`, `BRK.B`) preserved across all
  agent prompts and tool calls.
- Process-level UTF-8 default attempted for cross-platform consistency
  (note: this approach did not actually take effect; replaced in v0.2.4 with
  explicit per-call `encoding="utf-8"` arguments).

### Fixed

- yfinance rate-limit errors are retried with exponential backoff. (#426)
- HTTP client SSL customisation is supported for environments that need
  custom certificate bundles. (#379)
- Report-section writes handle list-of-string content gracefully.

### Contributors

- [@CadeYu](https://github.com/CadeYu) — exchange-qualified ticker preservation (#413)
- [@yang1002378395-cmyk](https://github.com/yang1002378395-cmyk) — HTTP client SSL customisation (#379)

## [0.2.1] — 2026-03-15

### Security

- Patched `langchain-core` vulnerability (LangGrinch). (#335)
- Removed `chainlit` dependency affected by CVE-2026-22218.

### Added

- `pyproject.toml` build-system configuration; the project now installs via
  modern packaging tooling.

### Removed

- `setup.py` — dependencies consolidated to `pyproject.toml`.

### Fixed

- Risk manager reads the correct fundamental report source. (#341)
- All `open()` calls receive an explicit UTF-8 encoding (initial pass).
- `get_indicators` tool handles comma-separated indicator names from the LLM. (#368)
- `Propagation` initialises every debate-state field so risk debaters never
  see missing keys.
- Stock data parsing tolerates malformed CSVs and NaN values.
- Conditional debate logic respects the configured round count. (#361)

### Contributors

- [@RinZ27](https://github.com/RinZ27) — `langchain-core` security patch (#335)
- [@Ljx-007](https://github.com/Ljx-007) — risk manager fundamental-report fix (#341)
- [@makk9](https://github.com/makk9) — debate-rounds config issue (#361)

## [0.2.0] — 2026-02-04

This is the largest release since the initial public version. The framework
moved from single-provider to a multi-provider architecture and grew several
production-ready surfaces.

### Added

- **Multi-provider LLM support** (OpenAI, Google, Anthropic, xAI, OpenRouter,
  Ollama) via a factory pattern, with provider-specific thinking configurations.
- **Alpha Vantage** integration as a configurable primary data provider, with
  yfinance as a community-stability fallback.
- **Footer statistics** in the CLI: real-time tracking of LLM calls, tool
  calls, and token usage via LangChain callbacks.
- **Post-analysis report saving** — the framework writes per-section markdown
  files (analyst reports, debate transcripts, final decision) when a run
  completes.
- **Announcements panel** — fetches updates from `api.tauric.ai/v1/announcements`
  for the CLI welcome screen.
- **Tool fallbacks** so a single vendor outage does not stop the pipeline.

### Changed

- Risky / Safe risk debaters renamed to **Aggressive / Conservative** for
  consistency with the displayed agent labels.
- Default data vendor switched to balance reliability and quota across
  community deployments.
- Ollama and OpenRouter model lists updated; default endpoints clarified.

### Fixed

- Analyst status tracking and message deduplication in the live display.
- Infinite-loop guard in the agent loop; reflection and logging hardened.
- Various data-vendor implementation bugs and tool-signature mismatches.

### Contributors

This release is the first with substantial outside contributions; many community
PRs from late 2025 also landed here.

- [@luohy15](https://github.com/luohy15) — Alpha Vantage data-vendor integration (#235)
- [@EdwardoSunny](https://github.com/EdwardoSunny) — yfinance fetching optimisations (#245)
- [@Mirza-Samad-Ahmed-Baig](https://github.com/Mirza-Samad-Ahmed-Baig) — infinite-loop guard, reflection, and logging fixes (#89)
- [@ZeroAct](https://github.com/ZeroAct) — saved results path support (#29)
- [@Zhongyi-Lu](https://github.com/Zhongyi-Lu) — `.env` gitignore (#49)
- [@csoboy](https://github.com/csoboy) — local Ollama setup (#53)
- [@chauhang](https://github.com/chauhang) — initial Docker support attempt (#47, later reverted; the merged Docker support shipped in v0.2.4)

## [0.1.1] — 2025-06-07

### Removed

- Static site assets that had been bundled with v0.1.0; the public site now
  lives separately.

## [0.1.0] — 2025-06-05

### Added

- **Initial public release** of the TradingAgents multi-agent trading
  framework: market / sentiment / news / fundamentals analysts; bull and bear
  researchers; trader; aggressive, conservative, and neutral risk debaters;
  portfolio manager. LangGraph orchestration, yfinance data, per-agent
  BM25 memory, single-provider OpenAI integration, interactive CLI.

[0.2.4]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/TauricResearch/TradingAgents/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/TauricResearch/TradingAgents/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/TauricResearch/TradingAgents/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/TauricResearch/TradingAgents/releases/tag/v0.1.0
