# `marvel/harness/` — 一个小的 DeepSeek agent harness

> 参考 DeepSeek 自己开源的 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)
> （MIT，TypeScript，定位 **"Everything is a Plugin"**，核心很薄、能力都是可组合的插件）。
> 这里借的是那个**形状**，不是它的实现：一个薄的核心 + 各自独立的可组合部件。

## 为什么不是替换 LangGraph

`marvel/graph/` 是一条**固定的** 14 阶段流水线：9 个分析师 → 质量门控 → 多空辩论 →
交易 → 风控 → 最终决策。要一份可重复的研究报告，这个形状是对的。

但有些问题它回答不了：

- 给模型这个任务、这套工具，它会怎么做？会用几次工具？会不会一直转圈？
- 上次那次跑得很好的运行，能不能**不花钱**再跑一遍，只换一个输入或一套工具？
- 一次运行里，模型到底看到了什么、调了什么、拿到了什么？

`marvel/harness/` 回答的是这一类问题。两者共存：流水线照跑，harness 是第二个入口。

## 组成

| 模块 | 职责 |
|---|---|
| `registry.py` | `ToolRegistry`：工具**只定义一次**——schema 和实现放在一起。可以从普通函数注册（复用 `Annotated[str, "..."]` 说明），也可以 `from_tools()` 直接包住流水线那套 LangChain 工具，schema 完全沿用流水线的定义，不会出现第二份会漂移的定义 |
| `loop.py` | `AgentLoop`：模型回合 → 工具调用 → 工具结果 → 再问，带**显式的步数预算** |
| `recorder.py` | `RunRecorder`：每个回合写 JSONL；`load_recording()` 读回来 |
| `models.py` | 三个适配器：`LangChainToolCallingModel`（真实 DeepSeek）、`ScriptedModel`（测试/确定性）、`ReplayModel`（回放） |
| `__main__.py` | `python -m marvel.harness` 命令行 |

**没有第二个 HTTP 客户端。** 真实模型走 `marvel.llm_clients` 的
`create_llm_client()`（`DeepSeekChatOpenAI`），thinking 模式的往返已经在那里处理好了。

## 用法

```bash
# 真实运行（会花钱，会先把 model / 步数 / 录制路径打印出来）
python -m marvel.harness --ticker 600519 --date 2026-05-12 --record run.jsonl

# 回放——不需要 key，不发任何请求
python -m marvel.harness --replay run.jsonl --task "研究 600519"
```

```python
from marvel.harness import AgentLoop, ToolRegistry, create_deepseek_model, RunRecorder

registry = ToolRegistry.from_tools([get_stock_data, get_news])
loop = AgentLoop(
    create_deepseek_model(),
    registry,
    recorder=RunRecorder("run.jsonl"),
    max_steps=6,
    analysis_date="2026-05-12",
)
result = loop.run("研究 600519 近期的消息面风险")
print(result.final, result.stopped_because, len(result.tool_calls))
```

## 四个刻意的设计决定

1. **步数用光是明说的，不是静默的成功。** 预算耗尽时
   `RunResult.stopped_because == "step_budget"`，`hit_step_budget` 为真，CLI 会往 stderr
   写「答案不完整」。半截的运行不能看起来像跑完的运行。
2. **工具失败是信息，不是结论。** `ToolRegistry.dispatch()` 从不抛给模型，而是把
   `ERROR: ... failed with ... This is a tool failure, not a finding of 'no data'.`
   写回对话——这个仓库反复踩过的坑就是「失败」被读成「没数据」。
3. **思考内容必须原样回传。** DeepSeek 的 thinking 模型要求把
   `reasoning_content` 在下一轮 assistant 消息里原样回传，否则 HTTP 400。
   这个字段活在 SDK 的消息对象里，从文本重建不出来——所以 `ModelReply.raw` 保留原对象，
   循环优先把它放回对话，而不是拼一个 dict。
4. **复用第 2 项的时间点守卫。** 工具执行被包在 `analysis_date_as_of(run_date)` 里，
   所以 harness 不是绕过分析日约束的第二个后门。用例
   `test_a_real_marvel_tool_cannot_reach_past_the_analysis_date` 端到端钉住了这点。

## 回放为什么重要

回放不只是省钱。它是**可重复实验**的前提：同一批模型回合，换一套工具、换一个日期
参数、换一个步数预算跑一遍，差异就只能来自你换掉的那个东西。多 Agent 辩论里
「模型今天换了个说法」这种噪声被固定住之后，「假消息会不会把结论带偏」这类问题
才有可能被测量，而不是被观察。

## 测试

```bash
python -m pytest tests/test_harness.py -q     # 29 例，全部离线
```

测试覆盖：schema 推导（含 `from __future__ import annotations` 下的 `Annotated`）、
未知工具 / 工具抛异常的可读错误、多工具同回合、步数预算、JSONL 往返、录制版本不匹配
与截断文件的拒绝、回放复现（终局答案 / 工具调用 / 工具结果 / 提示词）、
`reasoning_content` 的保留、分析日守卫、以及 CLI 的 `--replay` 离线路径。
