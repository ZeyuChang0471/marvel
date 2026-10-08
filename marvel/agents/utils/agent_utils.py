from functools import lru_cache

from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from marvel.agents.utils.core_stock_tools import (
    get_stock_data
)
from marvel.agents.utils.technical_indicators_tools import (
    get_indicators
)
from marvel.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from marvel.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)
from marvel.agents.utils.signal_data_tools import (
    get_profit_forecast,
    get_hot_stocks,
    get_northbound_flow,
    get_concept_blocks,
    get_fund_flow,
    get_dragon_tiger_board,
    get_lockup_expiry,
    get_industry_comparison,
)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English, so no extra tokens are used.

    Applied to **every** agent that produces text a user reads, the debate
    included. The bull and bear researchers used to be the exception, so the
    investment debate — which the Research Manager then summarises into the plan
    the trader executes — was generated in English regardless of the configured
    language.
    """
    from marvel.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return (
        f" Write your entire response in {lang}."
        f" Every heading, sentence and table cell must be in {lang};"
        f" do not answer in English even if the material you were given is in English."
        f" Keep ticker codes, indicator names and figures as they are."
    )


@lru_cache(maxsize=512)
def _cached_stock_name(code: str) -> str:
    """Chinese name for a 6-digit code, cached, never raising.

    One Tencent request per code per process. The agents are built per node and
    again per run, so without the cache this would be a request per agent.
    """
    try:
        from marvel.dataflows.a_stock import get_stock_name

        return get_stock_name(code) or ""
    except Exception:  # noqa: BLE001 — a missing label must not break a run
        return ""


def build_instrument_context(ticker: str) -> str:
    """Describe the exact instrument, and say which form to use where.

    This used to be the cause of reports that named the stock only by its code:
    it supplied the code and nothing else, then instructed the model to use
    "this exact ticker in every tool call, report, and recommendation". The model
    complied — the company name never appeared. The instruction now separates the
    two audiences: **prose gets the name, tool arguments get the code**.
    """
    name = ""
    text = str(ticker).strip()
    if text.isdigit() and len(text) == 6:
        try:
            name = _cached_stock_name(text)
        except Exception:  # noqa: BLE001 — a prompt nicety must never break a run
            name = ""

    if name:
        return (
            f"The instrument to analyze is `{text}` — {name}. "
            f"Write the company name **{name}** (with the code in brackets on first "
            f"mention, e.g. {name}（{text}）) in every report heading, analysis passage "
            f"and recommendation; a reader must never have to look up which company "
            f"a bare code refers to. "
            f"Pass only `{text}` as a tool argument named `ticker` or `symbol`; never a "
            f"company name, sector, concept or search keyword. "
            f"Preserve any exchange suffix that is already part of the value."
        )
    return (
        f"The instrument to analyze is `{text}`. "
        f"Use this exact value in every tool call, and state the company or "
        f"instrument name alongside it wherever you write about it, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`). "
        "When a tool argument is named `ticker`, pass only this ticker value; "
        "do not pass company names, sectors, concepts, or search keywords."
    )

def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages


        
