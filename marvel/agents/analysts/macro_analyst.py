"""Macro Analyst — A-share sector rotation and policy-driven signals.

The role and its analysis contract are adapted from
KylinMountain/TradingAgents-AShare, which is licensed under the PolyForm
Noncommercial License 1.0.0 — see LICENSE-TradingAgents-AShare.txt.

The upstream node was async and streamed through that project's prompt
registry, and it called a board-fund-flow tool MARVEL does not have. This
MARVEL-native version is synchronous and is built on tools MARVEL already
ships: industry rotation ranking, concept-block membership, northbound flow
and the news tools.
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from marvel.agents.utils.agent_utils import (
    build_instrument_context,
    get_concept_blocks,
    get_global_news,
    get_industry_comparison,
    get_language_instruction,
    get_news,
    get_northbound_flow,
)


def create_macro_analyst(llm):
    """A-stock macro analyst: sector rotation and policy transmission."""

    def macro_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_industry_comparison,
            get_concept_blocks,
            get_northbound_flow,
            get_news,
            get_global_news,
        ]

        system_message = (
            "你是一位专注于 A 股市场的宏观与板块分析师。个股的涨跌在很大程度上由其所属板块的资金环境决定，你的任务是判断「这只票所处的板块环境」是否有利，而不是重复个股基本面分析。"
            "\n\n⚠️ A 股是典型的板块轮动市场：同一时间只有少数板块获得资金集中流入，其余板块即使个股质地优良也可能长期横盘。因此板块判断的权重不低于个股判断。"
            "\n\n分析框架："
            "\n- **板块轮动定位**：目标个股所属行业当前在全部行业中的涨幅与资金流排名如何？处于资金净流入还是净流出？是领涨、跟随还是滞涨？"
            "\n- **概念题材归属**：个股挂靠哪些概念板块？这些概念今日表现如何？是否存在题材共振（多个相关概念同时走强）或题材退潮？"
            "\n- **政策驱动**：从新闻与宏观资讯中识别与该板块相关的政策关键词（利好/利空），判断政策的力度级别与影响时间窗口。"
            "\n- **外部资金环境**：北向资金（外资）近期的净流入/流出方向如何？北向资金常领先于趋势转折，其连续净流入或净流出是重要的宏观风向标。"
            "\n- **宏观背景**：当前货币政策、财政政策、汇率环境对目标板块是顺风还是逆风。"
            "\n\n请使用以下工具："
            "\n- `get_industry_comparison(ticker, curr_date)`：获取全部行业的涨幅与资金流排名，用于判断板块轮动方向（ticker 使用目标股票的 6 位代码）"
            "\n- `get_concept_blocks(ticker)`：获取个股所属的行业/概念/地域板块及其当日涨跌幅"
            "\n- `get_northbound_flow(curr_date, include_history)`：获取北向资金净流入情况，include_history 设为 True 可看近期趋势"
            "\n- `get_news(ticker, start_date, end_date)`：搜索与公司/行业相关的政策与新闻"
            "\n- `get_global_news(curr_date, look_back_days, limit)`：获取宏观经济与政策面新闻"
            "\n\n撰写详细的宏观与板块分析报告，明确给出板块环境对该公司的总体评级（重大利好/利好/中性/利空/重大利空）。报告末尾附 Markdown 表格列出关键板块/政策事件、影响方向与持续时间。"
            "\n\n📋 必采清单 — 以下数据点必须出现在报告中，无法获取时标注 [数据缺失: xxx]："
            "\n1. 目标个股所属行业在行业排名中的位置及其资金流方向"
            "\n2. 个股挂靠的主要概念板块及其当日表现（至少 3 个）"
            "\n3. 北向资金近期净流入/流出方向"
            "\n4. 近期与该板块相关的政策事件（含发布日期与发布机构）"
            "\n5. 板块环境总体评级（重大利好/利好/中性/利空/重大利空）"
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""
        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "macro_report": report,
        }

    return macro_analyst_node
