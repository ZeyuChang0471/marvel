from langchain_core.tools import tool
from typing import Annotated
from marvel.dataflows.interface import route_to_vendor


# 三张财报的 `curr_date` 都是**必填**，刻意不给默认值。
# 财报期可能"结束于分析日之前、却公布于分析日之后"，数据层必须把它裁掉才不会
# 泄漏未来。而只要这里给一个 `= None`，模型按 {"ticker": "600519"} 调用就会让
# 数据层的裁剪静默失效（`if curr_date and ...`）——给默认值等于没设防。
# 同 get_profit_forecast（issues #94 / test_lookahead_guard.py）。
_CURR_DATE_DOC = (
    "current date you are trading at, yyyy-mm-dd. REQUIRED: the data layer uses "
    "it to drop financial periods published after this date. Never omit it."
)

_FREQ_DOC = "reporting frequency: annual/quarterly"


@tool
def get_fundamentals(
    ticker: Annotated[str, "6-digit A-stock code (e.g. 600379). Must be numeric, NOT company name"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing comprehensive fundamental data
    """
    return route_to_vendor("get_fundamentals", ticker, curr_date)


@tool
def get_balance_sheet(
    ticker: Annotated[str, "6-digit A-stock code (e.g. 600379). Must be numeric, NOT company name"],
    curr_date: Annotated[str, _CURR_DATE_DOC],
    freq: Annotated[str, _FREQ_DOC] = "quarterly",
) -> str:
    """
    Retrieve balance sheet data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd (required)
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
    Returns:
        str: A formatted report containing balance sheet data
    """
    return route_to_vendor(
        "get_balance_sheet", ticker=ticker, freq=freq, curr_date=curr_date
    )


@tool
def get_cashflow(
    ticker: Annotated[str, "6-digit A-stock code (e.g. 600379). Must be numeric, NOT company name"],
    curr_date: Annotated[str, _CURR_DATE_DOC],
    freq: Annotated[str, _FREQ_DOC] = "quarterly",
) -> str:
    """
    Retrieve cash flow statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd (required)
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
    Returns:
        str: A formatted report containing cash flow statement data
    """
    return route_to_vendor(
        "get_cashflow", ticker=ticker, freq=freq, curr_date=curr_date
    )


@tool
def get_income_statement(
    ticker: Annotated[str, "6-digit A-stock code (e.g. 600379). Must be numeric, NOT company name"],
    curr_date: Annotated[str, _CURR_DATE_DOC],
    freq: Annotated[str, _FREQ_DOC] = "quarterly",
) -> str:
    """
    Retrieve income statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd (required)
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
    Returns:
        str: A formatted report containing income statement data
    """
    return route_to_vendor(
        "get_income_statement", ticker=ticker, freq=freq, curr_date=curr_date
    )