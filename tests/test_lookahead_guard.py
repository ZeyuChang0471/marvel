"""未来函数防护（point-in-time）。

在历史日期上跑分析时，数据层不能把"今天"的数据当成"分析日当天"的事实交给模型
——报告里完全看不出来，但结论已经被污染了。上游 TradingAgents 把这类问题统称为
backtesting date fidelity（#475）。

本仓库审出三个函数收了日期参数却完全没用：`get_fund_flow`（今天的分钟资金流 +
从今天回溯 20 日）、`get_fundamentals`（腾讯实时估值）、`get_profit_forecast`
（当前一致预期）。前者能真正做时点截断；后两者的数据源根本不提供历史时点值，
补不上就必须**说出来**，而不是静默把今天的数字当历史事实。
"""

from datetime import datetime, timedelta, timezone

import pytest

from marvel.dataflows import a_stock


# The data layer answers "is this historical?" with the **market** clock
# (`_market_today()`, Asia/Shanghai), never the host clock — that is the whole
# point of that helper. Deriving these dates from `datetime.now()` therefore made
# the suite disagree with the code under test for six hours every day: on a UTC
# runner between 16:00 and 24:00 UTC, Shanghai has already rolled over to
# tomorrow, so a host-derived "today" was one day behind and
# `_is_historical(TODAY)` came back True. CI was red for exactly that window and
# green for the rest of the day. Derive them the way the code does.
TODAY = a_stock._market_today().isoformat()
PAST = (a_stock._market_today() - timedelta(days=90)).isoformat()
FUTURE = (a_stock._market_today() + timedelta(days=5)).isoformat()


# ---------------------------------------------------------------------------
# 判定本身
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (PAST, True),
        (TODAY, False),
        (FUTURE, False),
        ("", False),
        (None, False),
        ("not-a-date", False),          # 解析不了不能当成历史，否则误伤实时分析
        (f"{PAST} 09:30:00", True),     # 带时分秒也要认得
    ],
)
def test_is_historical(value, expected):
    assert a_stock._is_historical(value) is expected


def test_snapshot_notice_names_the_date_and_says_do_not_use():
    notice = a_stock._snapshot_notice(PAST, "估值")

    assert PAST in notice
    assert "实时快照" in notice
    assert "不得" in notice   # 必须给模型明确指令，光提示"这是实时的"不够


# ---------------------------------------------------------------------------
# get_fund_flow：真正的时点截断
# ---------------------------------------------------------------------------


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def fake_em(monkeypatch):
    """假的东财返回：历史段跨越分析日前后，用来验证截断。"""
    calls = []

    def fake_get(url, params=None, timeout=10):
        calls.append(url)
        if "push2his" in url:
            return FakeResp({"data": {"klines": [
                "2026-05-01,1000,0,0,0,0,0",
                f"{PAST},2000,0,0,0,0,0",
                "2099-01-01,999999,0,0,0,0,0",   # 分析日之后 → 必须被剔除
            ]}})
        return FakeResp({"data": {"klines": [
            "2099-01-01 09:31,111,0,0,0,0,0",     # 实时段 → 复盘时整段不该取
        ]}})

    monkeypatch.setattr(a_stock, "_em_get", fake_get)
    return calls


def test_fund_flow_drops_rows_after_analysis_date(fake_em):
    out = a_stock.get_fund_flow("600519", PAST)

    assert "2099-01-01" not in out, "分析日之后的资金流泄漏了（未来函数）"
    assert PAST in out


def test_fund_flow_skips_realtime_when_historical(fake_em):
    a_stock.get_fund_flow("600519", PAST)

    assert not any("push2.eastmoney.com/api/qt/stock/fflow/kline" in u for u in fake_em), (
        "复盘历史日期时不该再去取今天的分钟资金流"
    )


def test_fund_flow_says_why_realtime_is_missing(fake_em):
    """略去实时段要说明原因，否则用户以为接口坏了。"""
    out = a_stock.get_fund_flow("600519", PAST)

    assert "略去实时分钟资金流" in out


def test_fund_flow_keeps_realtime_for_today(fake_em):
    """当天分析仍然要有实时资金流——防护不能误伤正常用法。"""
    a_stock.get_fund_flow("600519", TODAY)

    assert any("fflow/kline" in u for u in fake_em)


# ---------------------------------------------------------------------------
# 只有实时快照的两个：补不上就必须明说
# ---------------------------------------------------------------------------


def test_fundamentals_warns_on_historical_date(monkeypatch):
    monkeypatch.setattr(a_stock, "_tencent_quote", lambda codes: {})
    monkeypatch.setattr(a_stock, "_mootdx_call", lambda *a, **k: None)
    monkeypatch.setattr(a_stock, "_em_get", lambda *a, **k: FakeResp({}))

    out = a_stock.get_fundamentals("600519", PAST)

    assert "未来函数警告" in out
    assert PAST in out


def test_fundamentals_silent_for_today(monkeypatch):
    monkeypatch.setattr(a_stock, "_tencent_quote", lambda codes: {})
    monkeypatch.setattr(a_stock, "_mootdx_call", lambda *a, **k: None)
    monkeypatch.setattr(a_stock, "_em_get", lambda *a, **k: FakeResp({}))

    out = a_stock.get_fundamentals("600519", TODAY)

    assert "未来函数警告" not in out


def test_profit_forecast_warns_on_historical_date(monkeypatch):
    import pandas as pd

    monkeypatch.setattr(
        a_stock, "_ths_eps_forecast",
        lambda code: pd.DataFrame({"年度": ["2026"], "预测每股收益": [1.23]}),
    )

    out = a_stock.get_profit_forecast("600519", PAST)

    assert "未来函数警告" in out


# ---------------------------------------------------------------------------
# codex 复审补：防护要在**生产调用路径**上真的生效
# ---------------------------------------------------------------------------


def test_profit_forecast_tool_exposes_curr_date():
    """工具不把 curr_date 传下去，数据层的未来函数告警就是死代码。

    v0.5.1 给 get_profit_forecast 加了告警，但 @tool 只暴露 ticker，
    curr_date 恒为 None → 告警永远不触发，模型照样把今天的一致预期当历史事实。
    """
    from marvel.agents.utils.agent_utils import get_profit_forecast

    assert "curr_date" in get_profit_forecast.args


def test_every_date_aware_tool_forwards_its_date():
    """凡是数据层按 curr_date 做时点处理的工具，@tool 都必须暴露并转发它。"""
    import inspect

    from marvel.agents.utils import signal_data_tools

    src = inspect.getsource(signal_data_tools)
    for name in ("get_profit_forecast", "get_fund_flow"):
        call = f'route_to_vendor("{name}", '
        idx = src.find(call)
        assert idx != -1, f"找不到 {name} 的路由调用"
        line = src[idx:src.find(")", idx)]
        assert "curr_date" in line, f"{name} 没有把 curr_date 转发给数据层"


def test_fund_flow_widens_window_for_older_dates(fake_em, monkeypatch):
    """分析日早于最近 20 个交易日时，必须放大回溯窗口。

    接口只提供"从今天回溯 lmt 天"，仍只要 20 天的话过滤后一行不剩——
    把"数据不对"变成"没有数据"，比不过滤更糟（codex P2）。
    """
    captured = {}
    orig = a_stock._em_get

    def spy(url, params=None, timeout=10):
        if "push2his" in url:
            captured["lmt"] = params.get("lmt")
        return orig(url, params=params, timeout=timeout)

    monkeypatch.setattr(a_stock, "_em_get", spy)
    a_stock.get_fund_flow("600519", PAST)      # PAST = 90 天前

    assert captured["lmt"] > 20, f"回溯窗口没有放大，仍是 {captured.get('lmt')}"


def test_fund_flow_says_when_history_is_unavailable(monkeypatch):
    """过滤后为空要说明原因，不能让正文凭空少一段。"""
    def empty_hist(url, params=None, timeout=10):
        return FakeResp({"data": {"klines": []}})

    monkeypatch.setattr(a_stock, "_em_get", empty_hist)
    out = a_stock.get_fund_flow("600519", PAST)

    assert "未能取到" in out


def test_profit_forecast_curr_date_is_required():
    """给默认值等于没设防：模型按 {"ticker": "600519"} 调用时 curr_date 为空串，
    判定为"非历史"，告警永远不触发（codex 终轮指出）。"""
    from marvel.agents.utils.agent_utils import get_profit_forecast

    required = get_profit_forecast.args_schema.model_json_schema().get("required", [])
    assert "curr_date" in required


def test_fundamentals_prompt_tells_the_model_to_pass_the_date():
    """工具签名要求了还不够——提示词不提，模型也不会主动传。"""
    import inspect

    from marvel.agents.analysts import fundamentals_analyst

    src = inspect.getsource(fundamentals_analyst.create_fundamentals_analyst)
    assert "get_profit_forecast(ticker, curr_date)" in src
    assert "curr_date 必须传" in src


def test_fund_flow_history_trimmed_to_twenty_rows(monkeypatch):
    """窗口为够回溯才放大，过滤后要裁回承诺的 20 个交易日（codex 终轮指出）。

    不裁的话复盘 90 天前会返回约 40 行，既改变了请求的趋势窗口，又把返回体撑大一倍。
    """
    rows = [f"2026-{m:02d}-{d:02d},1000,0,0,0,0,0"
            for m in (3, 4, 5) for d in range(1, 16)]   # 45 行，全部早于 PAST

    def fake_get(url, params=None, timeout=10):
        if "push2his" in url:
            return FakeResp({"data": {"klines": rows}})
        return FakeResp({"data": {"klines": []}})

    monkeypatch.setattr(a_stock, "_em_get", fake_get)
    out = a_stock.get_fund_flow("600519", PAST)

    kept = [ln for ln in out.splitlines() if ln.strip().startswith("2026-")]
    assert len(kept) == 20, f"应裁到 20 行，实际 {len(kept)} 行"


def test_is_historical_uses_market_timezone_not_host(monkeypatch):
    """"今天"必须按 A 股市场时区算，不能用主机本地时区（codex 第四轮）。

    主机在 UTC+9 以东（如新西兰 UTC+13）时，当地已过零点而上海还在前一天——
    当天的分析会被判成"复盘历史"，实时资金流被略去、快照工具打出莫须有的
    未来函数警告。
    """
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    class FakeDatetime(_dt):
        @classmethod
        def now(cls, tz=None):
            # 奥克兰已是 8-10 凌晨，上海仍是 8-09 晚间
            aware = _dt(2026, 8, 9, 23, 30, tzinfo=_tz(_td(hours=8)))
            return aware.astimezone(tz) if tz else aware.astimezone(_tz(_td(hours=13))).replace(tzinfo=None)

    monkeypatch.setattr(a_stock, "datetime", FakeDatetime)

    assert a_stock._market_today().isoformat() == "2026-08-09"
    # 市场当天不该被判成历史，哪怕主机日历已经翻页
    assert a_stock._is_historical("2026-08-09") is False
    assert a_stock._is_historical("2026-08-08") is True


# ---------------------------------------------------------------------------
# 快讯：窗口算出来就必须真的裁，不能只打进标题
# ---------------------------------------------------------------------------

_MARKET_TZ = timezone(timedelta(hours=8))


def _cn_ts(date_str: str) -> int:
    """该日 09:00（市场时区）的 unix 时间戳，模拟财联社的 ctime。"""
    return int(
        datetime.strptime(date_str, "%Y-%m-%d")
        .replace(hour=9, tzinfo=_MARKET_TZ)
        .timestamp()
    )


def _fake_sources(monkeypatch, cls_items, em_items):
    """同时假造财联社（_requests.get）与东财 7×24（_em_get）两个源。"""

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(
        a_stock._requests,
        "get",
        lambda url, **kw: _Resp({"data": {"roll_data": cls_items}}),
    )
    monkeypatch.setattr(
        a_stock,
        "_em_get",
        lambda url, **kw: _Resp({"data": {"fastNewsList": em_items}}),
    )


def test_global_news_filters_by_publication_date(monkeypatch):
    """Regression: get_global_news 算了窗口却只把它打进标题，正文一条没裁。

    于是复盘历史日期时，报告里出现**今天**的快讯，而标题写着
    "from {start_date} to {curr_date}"——新闻/政策/游资/宏观四个分析师都消费它，
    而报告正文完全看不出这件事。
    """
    _fake_sources(
        monkeypatch,
        cls_items=[
            {"title": "今天的最新快讯", "brief": "今天", "ctime": _cn_ts(TODAY)},
            {"title": "分析日当天的快讯", "brief": "当天", "ctime": _cn_ts(PAST)},
        ],
        em_items=[
            {"title": "窗口外的旧闻", "summary": "旧", "showTime": "2019-01-01 08:00:00"},
        ],
    )

    out = a_stock.get_global_news(PAST)

    assert "分析日当天的快讯" in out
    assert "今天的最新快讯" not in out, "分析日之后的快讯泄漏了（未来函数）"
    assert "窗口外的旧闻" not in out


def test_global_news_flags_that_a_historical_window_is_incomplete(monkeypatch):
    """窗口内确实有条目时也要说清楚：源只返回最新 N 条，历史窗口注定不全。"""
    _fake_sources(
        monkeypatch,
        cls_items=[
            {"title": "窗口内的快讯", "brief": "x", "ctime": _cn_ts(PAST)},
            {"title": "今天的快讯", "brief": "y", "ctime": _cn_ts(TODAY)},
        ],
        em_items=[],
    )

    out = a_stock.get_global_news(PAST)

    assert "窗口内的快讯" in out
    assert "可能不完整" in out, "历史窗口不完整必须明说，否则会被当成当日全部资讯"
    assert "1 条超出窗口被丢弃" in out


def test_global_news_says_the_source_cannot_look_back(monkeypatch):
    """全被裁掉时要区分「源不能回溯」和「那几天真的没新闻」。"""
    _fake_sources(
        monkeypatch,
        cls_items=[{"title": "今天的快讯", "brief": "x", "ctime": _cn_ts(TODAY)}],
        em_items=[],
    )

    out = a_stock.get_global_news(PAST)

    assert "今天的快讯" not in out
    assert "无法回溯" in out
    assert "没有新闻" in out, "必须明确否定「那几天没有新闻」这个误读"


def test_global_news_drops_undated_articles(monkeypatch):
    """发布时间读不出来 → 丢弃。旧行为是把读不出来的当成"在窗口内"。"""
    _fake_sources(
        monkeypatch,
        cls_items=[
            {"title": "无时间戳的条目", "brief": "x", "ctime": ""},
            {"title": "有时间的条目", "brief": "y", "ctime": _cn_ts(PAST)},
        ],
        em_items=[],
    )

    out = a_stock.get_global_news(PAST)

    assert "无时间戳的条目" not in out
    assert "有时间的条目" in out
    assert "无法识别" in out


def test_global_news_keeps_today_for_today(monkeypatch):
    """当天分析不能被误伤：窗口内快讯照常返回，且不加历史警示。"""
    today = a_stock._market_today().isoformat()
    _fake_sources(
        monkeypatch,
        cls_items=[{"title": "今日快讯", "brief": "x", "ctime": _cn_ts(today)}],
        em_items=[],
    )

    out = a_stock.get_global_news(today)

    assert "今日快讯" in out
    assert "可能不完整" not in out


def test_news_drops_undated_articles(monkeypatch):
    """get_news 的同类缺陷：日期解析失败时旧代码 `pass` 后照常收录。"""
    monkeypatch.setattr(
        a_stock,
        "_fetch_news_eastmoney",
        lambda code: [
            {"title": "无日期新闻", "content": "x", "time": "", "source": "东方财富"},
            {"title": "窗口内新闻", "content": "y", "time": f"{PAST} 10:00:00",
             "source": "东方财富"},
        ],
    )

    out = a_stock.get_news("600519", PAST, PAST)

    assert "窗口内新闻" in out
    assert "无日期新闻" not in out
    assert "publication date could not be read" in out


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("not-a-date", None),
        ("2026-05-08", "2026-05-08"),
        ("2026-05-08 09:31:00", "2026-05-08"),
        ("2026/5/8 09:31", "2026-05-08"),
        ("2019-13-45", None),        # 非法月日：返回 None，不能抛
    ],
)
def test_parse_news_date_shapes(value, expected):
    got = a_stock._parse_news_date(value)
    assert (got.isoformat() if got else None) == expected


def test_parse_news_date_reads_unix_timestamps():
    ts = _cn_ts("2026-05-08")
    assert a_stock._parse_news_date(ts).isoformat() == "2026-05-08"
    # 数字字符串形式的 epoch 也必须认（不能掉进日期正则）
    assert a_stock._parse_news_date(str(ts)).isoformat() == "2026-05-08"


def test_parse_news_date_uses_market_timezone():
    """CN 23:30 的稿件属于 CN 当天，不随主机时区漂到别的一天。"""
    aware = datetime(2026, 5, 8, 23, 30, tzinfo=_MARKET_TZ)
    assert a_stock._parse_news_date(int(aware.timestamp())).isoformat() == "2026-05-08"


# ---------------------------------------------------------------------------
# 三张财报：curr_date 必须真的生效，不能被默认值关掉
# ---------------------------------------------------------------------------


def _fake_sina_report(monkeypatch, periods):
    """假造新浪财报三表：periods 是「报告日」字符串列表。"""
    import pandas as pd

    class _Resp:
        def json(self):
            return {
                "result": {
                    "data": {
                        "lrb": [
                            {"报告日": p, "净利润": 100 + i}
                            for i, p in enumerate(periods)
                        ]
                    }
                }
            }

    monkeypatch.setattr(a_stock._requests, "get", lambda url, **kw: _Resp())
    return pd.DataFrame


def test_financial_report_drops_periods_after_the_analysis_date(monkeypatch):
    """报告期结束于分析日之前、但尚未公布时，不能被当成已知事实。

    Regression: 裁剪写成 ``if curr_date and "报告日" in df.columns``，而三个工具
    都把 curr_date 默认成 None —— 于是模型按 {"ticker": ...} 调用时守卫**静默失效**，
    直接返回最新 8 期。
    """
    _fake_sina_report(
        monkeypatch,
        ["2026-03-31", "2026-06-30", "2026-09-30"],   # PAST = 90 天前
    )

    df = a_stock._get_financial_report_sina("600519", "利润表", "quarterly", PAST)

    assert list(df["报告日"].dt.strftime("%Y-%m-%d")) == ["2026-03-31"]


def test_financial_report_cuts_at_market_date_when_curr_date_missing(monkeypatch):
    """curr_date 缺失时要退到"市场当天"，而不是干脆不裁。"""
    future = (a_stock._market_today() + timedelta(days=400)).isoformat()
    _fake_sina_report(monkeypatch, ["2026-03-31", future])

    df = a_stock._get_financial_report_sina("600519", "利润表", "quarterly", None)

    dates = list(df["报告日"].dt.strftime("%Y-%m-%d"))
    assert future not in dates, "curr_date 缺失不应等于放弃时点裁剪"
    assert "2026-03-31" in dates


def test_financial_report_refuses_payload_without_a_date_column(monkeypatch):
    """没有「报告日」列就无法裁剪 → 丢弃，而不是原样返回。"""
    class _Resp:
        def json(self):
            return {"result": {"data": {"lrb": [{"净利润": 100}]}}}

    monkeypatch.setattr(a_stock._requests, "get", lambda url, **kw: _Resp())

    df = a_stock._get_financial_report_sina("600519", "利润表", "quarterly", PAST)

    assert df.empty, "无法确定报告期的数据必须丢弃，不能原样交给模型"


@pytest.mark.parametrize(
    "tool_name",
    ["get_balance_sheet", "get_cashflow", "get_income_statement"],
)
def test_statements_require_curr_date(tool_name):
    """给默认值等于没设防：三个财报工具必须把 curr_date 标成必填。"""
    from marvel.agents.utils import fundamental_data_tools as fdt

    tool = getattr(fdt, tool_name)
    required = tool.args_schema.model_json_schema().get("required", [])
    assert "curr_date" in required, f"{tool_name} 的 curr_date 必须是必填项"


@pytest.mark.parametrize(
    "tool_name",
    ["get_balance_sheet", "get_cashflow", "get_income_statement"],
)
def test_statements_forward_curr_date_to_the_data_layer(monkeypatch, tool_name):
    """签名要求了还不够——工具必须真的把它转发下去。

    行为断言（spy 住 route_to_vendor），不查源码文本：改名/重排都不该影响它。
    """
    from marvel.agents.utils import fundamental_data_tools as fdt

    captured = {}

    def spy(method, *args, **kwargs):
        captured["method"] = method
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(fdt, "route_to_vendor", spy)
    out = getattr(fdt, tool_name).invoke({"ticker": "600519", "curr_date": PAST})

    assert out == "ok"
    assert captured["method"] == tool_name
    assert captured["kwargs"].get("curr_date") == PAST, "curr_date 没有转发到数据层"


def test_fundamentals_prompt_tells_the_model_to_pass_the_date_to_statements():
    """提示词不提，模型也不会主动传 curr_date。"""
    import inspect

    from marvel.agents.analysts import fundamentals_analyst

    src = inspect.getsource(fundamentals_analyst.create_fundamentals_analyst)
    for name in ("get_balance_sheet", "get_cashflow", "get_income_statement"):
        assert f"{name}(ticker, curr_date)" in src, f"提示词没有要求给 {name} 传 curr_date"


# ---------------------------------------------------------------------------
# get_stock_data：end_date 由模型给出，不能盲信
# ---------------------------------------------------------------------------


def test_stock_data_clamps_end_date_to_market_date(monkeypatch):
    """end_date 是模型填的，而没有任何提示词要求它填分析日。

    自然默认（今天）会把分析日之后才出现的 K 线拉进历史复盘，报告里看不出异常。
    其他日期敏感工具都会 clamp 或告警，只有这个原先完全信任入参。
    """
    import pandas as pd

    future = (a_stock._market_today() + timedelta(days=30)).isoformat()
    frame = pd.DataFrame({
        "Date": pd.to_datetime([PAST, future]),
        "Open": [1.0, 2.0], "High": [1.0, 2.0], "Low": [1.0, 2.0],
        "Close": [1.0, 2.0], "Volume": [100, 200],
    })

    def no_tcp(*a, **k):
        raise ValueError("no tcp in test")

    monkeypatch.setattr(a_stock, "_mootdx_call", no_tcp)
    monkeypatch.setattr(
        a_stock, "_sina_kline_fallback", lambda code, s=None, e=None: frame.copy()
    )
    monkeypatch.setattr(
        a_stock,
        "_supplement_stale_ohlcv_with_sina",
        lambda code, df, target, start=None: (df, False),
    )

    out = a_stock.get_stock_data("600519", PAST, future)

    # 只看 CSV 正文（# 开头是表头/说明行）
    body = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    assert not any(ln.startswith(future) for ln in body), (
        f"分析日之后的 K 线泄漏到了正文: {body}"
    )
    assert any(ln.startswith(PAST) for ln in body)
    assert "收敛" in out, "收敛了 end_date 就必须说明，否则读者不知道窗口被改过"


# ---------------------------------------------------------------------------
# 北向资金：实时段与历史都必须裁到分析日
# ---------------------------------------------------------------------------


def test_northbound_skips_realtime_for_historical(monkeypatch):
    """分钟级北向只有"今天"的，复盘时不该去取。"""
    monkeypatch.setattr(a_stock, "_save_northbound_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(a_stock, "_load_northbound_history", lambda n: [])

    import requests as _rq

    def must_not_fetch(*a, **k):
        raise AssertionError("复盘历史日期时不该再去取今天的实时北向")

    monkeypatch.setattr(_rq, "get", must_not_fetch)

    out = a_stock.get_northbound_flow(PAST)

    assert "略去实时分钟北向" in out


def test_northbound_refuses_signal_when_series_lengths_differ(monkeypatch):
    """hgt/sgt 与 time 长度不一致时，不能把两条序列的末元素相加当净流入。

    打印段早就防御了长度不一致，取值段却直接 hgt[-1] + sgt[-1]——方向与量级都会
    失真，还会写进本地缓存、均进 N 日均值。宁可不给结论。
    """
    monkeypatch.setattr(a_stock, "_save_northbound_snapshot", lambda *a, **k: None)

    class _Resp:
        def json(self):
            return {
                "time": ["09:30", "09:31", "09:32"],
                "hgt": [-9.28, -9.0, -8.5],
                "sgt": [379.75],                      # 长度不一致
            }

    import requests as _rq

    monkeypatch.setattr(_rq, "get", lambda *a, **k: _Resp())

    out = a_stock.get_northbound_flow(a_stock._market_today().isoformat())

    assert "INFLOW" not in out and "OUTFLOW" not in out, "序列不齐还给出了方向性结论"
    assert "长度不一致" in out


def test_northbound_history_is_cut_at_the_analysis_date(monkeypatch):
    """本地缓存里存的是跑分析那天的收盘快照，复盘时要裁掉分析日之后的行。"""
    monkeypatch.setattr(a_stock, "_is_historical", lambda d: True)   # 跳过实时段
    monkeypatch.setattr(a_stock, "_save_northbound_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(
        a_stock,
        "_load_northbound_history",
        lambda n: [("2026-01-01", 1.0, 2.0), ("2099-01-01", 9.0, 9.0)],
    )

    out = a_stock.get_northbound_flow("2026-06-01", include_history=True)

    assert "2026-01-01" in out
    assert "2099-01-01" not in out, "分析日之后的北向收盘快照泄漏了（未来函数）"


# ---------------------------------------------------------------------------
# 解禁 / 行业对比 / 强势股
# ---------------------------------------------------------------------------


def test_lockup_history_query_bounds_the_upper_date(monkeypatch):
    """只按 SECURITY_CODE 过滤时，历史解禁会把未来的批次也算进来。"""
    captured = {}

    def spy(report_name, **kwargs):
        captured.setdefault(report_name, []).append(kwargs.get("filter_str", ""))
        return []

    monkeypatch.setattr(a_stock, "_eastmoney_datacenter", spy)

    a_stock.get_lockup_expiry("600519", PAST)

    history_filter = captured["RPT_LIFT_STAGE"][0]
    assert "FREE_DATE<=" in history_filter, f"历史解禁没有上界: {history_filter}"
    assert PAST in history_filter


def test_industry_comparison_warns_on_historical_date(monkeypatch):
    """板块排名是东财"当前"快照，trade_date 原先只出现在标题里。"""

    class _Resp:
        def json(self):
            return {"data": {"diff": []}}

    monkeypatch.setattr(a_stock, "_em_get", lambda *a, **k: _Resp())

    out = a_stock.get_industry_comparison("600519", PAST)

    assert "未来函数警告" in out
    assert PAST in out


def test_industry_comparison_silent_for_today(monkeypatch):
    """当天分析不能被误伤。"""
    today = a_stock._market_today().isoformat()

    class _Resp:
        def json(self):
            return {"data": {"diff": []}}

    monkeypatch.setattr(a_stock, "_em_get", lambda *a, **k: _Resp())

    out = a_stock.get_industry_comparison("600519", today)

    assert "未来函数警告" not in out


def test_hot_stocks_requires_curr_date():
    """强势股接口按日期取数，省略就等于取"今天" → 复盘时是未来函数。"""
    from marvel.agents.utils.agent_utils import get_hot_stocks

    required = get_hot_stocks.args_schema.model_json_schema().get("required", [])
    assert "curr_date" in required


def test_hot_stocks_defaults_to_market_date_not_host_date(monkeypatch):
    """空 curr_date 要退到市场日期，否则主机在别的时区会请求到不存在的那一天。"""
    seen = {}

    class _Resp:
        def json(self):
            return {"errocode": 0, "data": []}

    import requests as _rq

    def spy(url, **kwargs):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(_rq, "get", spy)

    a_stock.get_hot_stocks("")

    assert a_stock._market_today().isoformat() in seen["url"]
