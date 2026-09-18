# 许可说明 / Licensing

**本仓库是混合许可项目，不同来源的代码适用不同许可证。使用前请读这一页。**

MARVEL 不是一个从零开始的项目：它建立在两个上游之上，并提取了第三个项目的部分代码。
这三个来源的许可证不同，所以**不能给整个仓库贴一个统一的许可证标签**。

---

## 一、许可构成

| # | 来源 | 许可证 | 覆盖范围 |
|---|------|--------|---------|
| 1 | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | **Apache License 2.0** | 原版框架，`marvel/` 的大部分基础结构 |
| 2 | [simonlin1212/TradingAgents-Astock](https://github.com/simonlin1212/tradingagents-astock) | **Apache License 2.0** | MARVEL 的直接上游；本仓库基于其 v0.2.13，并移植了其 v0.5.17 的数据层 / Agent / LLM 客户端修复 |
| 3 | [KylinMountain/TradingAgents-AShare](https://github.com/KylinMountain/TradingAgents-AShare) | **PolyForm Noncommercial License 1.0.0** | 从该项目提取的代码（见下方清单） |
| 4 | MARVEL 自身的改动与新增 | 见下方「三、MARVEL 自身贡献」 | — |

- 第 1、2 项的完整条款见 [LICENSE](./LICENSE)（Apache 2.0 全文）。
- 第 3 项的完整条款见 [LICENSE-TradingAgents-AShare.txt](./LICENSE-TradingAgents-AShare.txt)（PolyForm Noncommercial 1.0.0 全文 + 上游通知）。

### 来自 TradingAgents-AShare 的文件清单

当前包括：

```
marvel/dataflows/trade_calendar.py     # A股交易日历与盘中状态机
```

该文件头部带有来源标注。**新增任何来自该项目的文件时，必须同步更新本清单和
`LICENSE-TradingAgents-AShare.txt` 中的 Required Notice 段。**

---

## 二、非商业声明

**MARVEL 仅面向非商业的研究与教学用途发布。**

由于本仓库包含依据 **PolyForm Noncommercial 1.0.0** 授权的组件（第 3 项），
**整个仓库不得用于商业目的**。商业使用需要分别获得相应权利人的授权。

### ⚠️ 一个必须说清的法律边界

上面的「非商业」约束**只对第 3、4 项有效**，对第 1、2 项**无效**：

Apache License 2.0 第 2 条已向**每一个**获得副本的人授予了**不可撤销的、
包含商业使用在内的**免费许可。原作者和我都**无权替这些代码收回该权利**。
因此：

- 任何人拿到本仓库中属于第 1、2 项的代码，**仍可依 Apache 2.0 合法商用**；
- 想要一份可以商用的构建，**删除第 3 项清单里的全部文件**即可——
  那些文件是 Apache 2.0 与 PolyForm NC 的唯一交界处。

把这一页写成「全仓库一律禁止商用」看起来更省事，但那句话对 Apache 部分
自始无效，反而会让整份声明失去可信度。所以这里如实分开写。

### PolyForm Noncommercial 1.0.0 允许什么

- ✅ 个人使用、研究、实验、测试、学习、业余项目
- ✅ 慈善机构、教育机构、公立研究机构、公共安全/卫生机构、环保组织、政府机构使用
- ✅ 修改、创建衍生作品、分发（须随附许可证条款或 URL 及 Required Notice）
- ❌ 任何商业目的

---

## 三、MARVEL 自身贡献

在 Apache 2.0 与 PolyForm NC 之外，MARVEL 自己新增与修改的部分
（例如重命名与打包、Web UI 的 API Key 填写与模型选择、包名 `marvel`、
启动脚本、以及第 2 项之外的工程改动）由仓库作者贡献。

为避免歧义，作者声明：**MARVEL 自身的贡献同样仅授权用于非商业用途**，
与第 3 项保持一致。这意味着本仓库作为一个整体不存在任何可商用的授权路径
（除非使用者自行移除第 3 项文件，并且其使用范围也不落入第 4 项的约束）。

---

## 四、数据来源与免责声明

本项目的数据来自公开 HTTP 接口（mootdx / 腾讯财经 / 东方财富 / 新浪财经 /
同花顺 / 财联社 / 百度股市通等），不附带任何数据授权。使用者需自行确认其
使用方式符合各数据源的服务条款及当地法律。

> **本项目仅供学习研究与技术演示，不构成任何投资建议。**
>
> - 所有分析报告与交易信号均由 AI 自动生成，可能存在错误或偏差
> - 投资决策请咨询持有中国证监会颁发资质的专业机构
> - 作者不对使用本工具产生的任何投资损失承担责任
> - 股市有风险，投资需谨慎

另外，本项目**不提供建仓价、止损位、仓位、目标价**等可执行价位。
这是刻意为之：软件是否被认定为「荐股软件」取决于其是否**具备**该功能，
因此该能力被完整移除，而不是做成一个默认为关闭的开关。
