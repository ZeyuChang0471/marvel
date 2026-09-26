"""A-stock (China mainland) data vendor for MARVEL.

Zero third-party data dependency (no akshare). All sources are direct HTTP APIs
or mootdx TCP.

Data sources:
- mootdx (TCP 7709): OHLCV K-lines, financial snapshots, F10 text
- Tencent Finance (HTTP GBK): PE/PB/market cap/turnover
- 东方财富 push2 / datacenter-web (direct HTTP): stock info, dragon-tiger, lockup
- 新浪财经 (direct HTTP): K-line fallback, financial statements
- 同花顺 (direct HTTP): consensus EPS, hot stocks, northbound capital flow
- 财联社 (direct HTTP): global news wire
"""

from __future__ import annotations

from typing import Annotated
from datetime import date, datetime, time as _dtime, timedelta, timezone
from dateutil.relativedelta import relativedelta
import contextlib
import json as _json
import os
import logging
import math
import random
import re as _re
import socket
import threading
import time
import uuid
import urllib.request

import pandas as pd
import requests as _requests

from .utils import atomic_write_text, safe_ticker_component

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers: ticker format & market detection
# ---------------------------------------------------------------------------

def _get_prefix(code: str) -> str:
    """6-digit A-stock code -> market prefix for Tencent/Sina.

    The 92 prefix must be checked before the leading-9 rule: the Beijing Stock
    Exchange started issuing 920xxx codes for new listings in October 2024, and
    a bare ``startswith("9")`` routes them to Shanghai, where the Tencent quote
    endpoint returns an empty payload (issue #85).  Only 900xxx (Shanghai B
    shares) legitimately belongs to ``sh``.

    The leading **4** rule is the same mistake one digit over.  The BSE also
    issues 43xxxx/40xxxx codes (430047 诺思兰德, 430139 华岭股份 …); without an
    explicit branch they fell through to ``sz``, and the Tencent endpoint
    answers with an empty ``v_pv_none_match`` line that the parser skips — so
    PE/PB/市值/涨跌停 simply went missing with no error anywhere.
    """
    if code.startswith("92"):
        return "bj"
    if code.startswith("4"):        # BSE 43xxxx / 40xxxx
        return "bj"
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    return "sz"


def _reject_non_a_share(original: str, code: str) -> None:
    """港股/美股代码走到 A 股数据层时当场报错，而不是拿去查 A 股（#43）。

    A 股代码恒为 6 位数字。港股是 4~5 位（`00700`）或带 `.HK` 后缀，美股是字母。
    这些代码此前会被**原样放行**，然后拿去问 mootdx / 腾讯 / 东财——而这些源对
    不存在的代码往往不报错，只返回空值或僵尸报价（北交所 920 号段就踩过，见
    `_normalize_ticker` 上游的 `_get_prefix`）。于是模型会拿着一份看起来正常、
    实际属于别的市场或根本不存在的数据写完整篇报告，报告里完全看不出来。
    """
    if code.isdigit() and len(code) == 6:
        return
    upper = original.strip().upper()
    if upper.endswith(".HK") or (code.isdigit() and len(code) in (4, 5)):
        raise ValueError(
            f"'{original}' 是港股代码。本数据层只支持 A 股（6 位数字代码，"
            f"如 600519 / 000001）。港股数据请用姊妹项目 global-stock-data，"
            f"多 Agent 港股分析仍在 roadmap（issue #43）。"
        )
    if code and not code.isdigit():
        raise ValueError(
            f"'{original}' 不是 A 股代码。本数据层只支持 A 股 6 位数字代码"
            f"（如 600519）；美股/港股请用姊妹项目 global-stock-data。"
        )
    raise ValueError(
        f"'{original}' 不是有效的 A 股代码：A 股代码恒为 6 位数字（如 600519），"
        f"这里解析出的是 '{code}'。"
    )


def _normalize_ticker(symbol: str) -> str:
    """Strip exchange prefix/suffix, return pure 6-digit code.

    Handles: '688017', 'SH688017', '688017.SH', 'sh688017'

    非 A 股代码（港股 `00700` / `0700.HK`、美股 `AAPL`）会直接报错，不再原样
    放行去查 A 股数据源（#43）。
    """
    s = symbol.strip().upper()
    # Remove .SH / .SZ / .BJ suffix
    for suffix in (".SH", ".SZ", ".BJ"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    # Remove SH / SZ / BJ prefix
    for prefix in ("SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    code = safe_ticker_component(s)
    _reject_non_a_share(symbol, code)
    return code


# ---------------------------------------------------------------------------
# Stock name <-> code mapping (cached)
# ---------------------------------------------------------------------------

_name_to_code: dict[str, str] | None = None
_code_to_name: dict[str, str] | None = None


_NAME_MAP_CACHE_FILE = "name_code_map.json"
_NAME_MAP_CACHE_TTL_S = 24 * 3600


def _name_map_cache_path() -> str:
    """Path to the on-disk full-market name↔code map."""
    from .config import get_config

    config = get_config()
    cache_dir = config.get(
        "data_cache_dir", os.path.expanduser("~/.marvel/cache")
    )
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, _NAME_MAP_CACHE_FILE)


def _load_name_map_from_disk():
    """Return (name→code, code→name) from a fresh cache file, else None."""
    path = _name_map_cache_path()
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = _json.load(f)

        age = time.time() - float(payload.get("built_at", 0))
        if age > _NAME_MAP_CACHE_TTL_S:
            logger.info("名称映射缓存已过期（%.1f 小时），将重建", age / 3600)
            return None

        n2c = payload.get("name_to_code")
        c2n = payload.get("code_to_name")
        if not isinstance(n2c, dict) or not isinstance(c2n, dict) or not n2c:
            return None
        return n2c, c2n
    except Exception as exc:  # 缓存坏掉只是少一次优化，绝不能影响查询
        logger.debug("读取名称映射缓存失败，将重建：%s", exc)
        return None


def _save_name_map_to_disk(n2c: dict, c2n: dict) -> None:
    """Persist the map so a later process start does not rebuild it.

    Best-effort by design: a read-only or sandboxed cache directory must never
    turn a successful lookup into a failure.
    """
    try:
        payload = {
            "built_at": time.time(),
            "name_to_code": n2c,
            "code_to_name": c2n,
        }
        _atomic_write_text(
            _name_map_cache_path(), _json.dumps(payload, ensure_ascii=False)
        )
        logger.info("名称映射已写入磁盘缓存：%d 条", len(n2c))
    except Exception as exc:
        logger.debug("写入名称映射缓存失败（不影响本次结果）：%s", exc)


def _build_name_code_map() -> tuple[dict[str, str], dict[str, str]]:
    """Build name→code and code→name maps (both SH & SZ markets).

    Served from the on-disk cache while it is fresh: building it needs mootdx,
    and when the Tongdaxin TCP port is unreachable that costs roughly 80
    seconds of serial server probing (see ``_get_mootdx_client``).
    """
    global _name_to_code, _code_to_name
    if _name_to_code is not None:
        return _name_to_code, _code_to_name

    cached = _load_name_map_from_disk()
    if cached is not None:
        _name_to_code, _code_to_name = cached
        logger.info("Loaded stock name-code map from cache: %d entries", len(cached[0]))
        return _name_to_code, _code_to_name

    n2c: dict[str, str] = {}
    c2n: dict[str, str] = {}

    try:
        for market in (0, 1):  # 0=SZ, 1=SH
            stocks = _mootdx_call("stocks", market=market)
            if stocks is None or stocks.empty:
                continue
            for _, row in stocks.iterrows():
                code = str(row["code"]).strip()
                name = str(row["name"]).strip()
                if not _re.match(r"^[036]\d{5}$", code):
                    continue
                clean_name = name.replace(" ", "").replace("　", "")
                n2c[clean_name] = code
                c2n[code] = clean_name
    except Exception as e:
        # 网络抖动/通达信不可达时给出明确提示，而非冒泡成风马牛不相及的报错（#46/#66）
        raise ValueError(
            "无法通过 mootdx 解析股票名称（通达信服务暂时不可达）：%s。"
            "请稍后重试，或直接输入 6 位股票代码。" % e
        ) from e

    _name_to_code = n2c
    _code_to_name = c2n
    _save_name_map_to_disk(n2c, c2n)
    logger.info("Built stock name-code map: %d entries", len(n2c))
    return _name_to_code, _code_to_name


def resolve_ticker(user_input: str) -> str:
    """Resolve user input (code or Chinese name) to a 6-digit A-stock code.

    Accepts: '600379', 'SH600379', '600379.SH', '宝光股份'
    Returns: '600379'
    Raises: ValueError if not resolvable.
    """
    s = user_input.strip()
    if not s:
        raise ValueError("输入不能为空")

    has_chinese = any("一" <= ch <= "鿿" for ch in s)

    if not has_chinese:
        return _normalize_ticker(s)

    clean = s.replace(" ", "").replace("　", "")
    n2c, _ = _build_name_code_map()

    if clean in n2c:
        return n2c[clean]

    matches = {name: code for name, code in n2c.items() if clean in name}
    if len(matches) == 1:
        return next(iter(matches.values()))
    if len(matches) > 1:
        examples = ", ".join(f"{n}({c})" for n, c in list(matches.items())[:5])
        raise ValueError(f"'{s}' 匹配到多只股票: {examples}，请输入完整名称或代码")

    # LLM 有时会把行业/概念名（如 '游戏'、'白酒'）当 ticker 传进来（#76）。
    # 报错必须写明原因和正确用法，让模型能在下一次工具调用中自我纠正。
    raise ValueError(
        f"找不到股票 '{s}'。ticker 参数只接受 6 位股票代码（如 '600519'）"
        f"或完整股票名称（如 '贵州茅台'）；行业/概念/板块名（如 '游戏'）不是"
        f"有效的股票标识。请改用目标个股的 6 位股票代码重试。"
    )


# ---------------------------------------------------------------------------
# 未来函数防护（point-in-time）
# ---------------------------------------------------------------------------


# A 股市场时区。判"今天"必须按市场所在地算，不能用主机本地时区——
# 主机在 UTC+9 以东（如新西兰 UTC+13）时，当地已过零点而上海还在前一天，
# 当天的分析会被判成"复盘历史"：实时资金流被略去、快照工具打出莫须有的未来函数
# 警告。反过来主机在西半球也会把已经过去的交易日当成"今天"。
_MARKET_TZ = timezone(timedelta(hours=8))


def _market_today() -> "date":
    """A 股市场当前日期（Asia/Shanghai），与主机时区无关。"""
    return datetime.now(_MARKET_TZ).date()


def _is_historical(curr_date) -> bool:
    """分析日期是否早于市场当天。早于 = 这次是在复盘历史，不能拿实时数据当事实。"""
    if not curr_date:
        return False
    try:
        return (
            datetime.strptime(str(curr_date)[:10], "%Y-%m-%d").date()
            < _market_today()
        )
    except ValueError:
        return False


def _snapshot_notice(curr_date: str, what: str) -> str:
    """实时快照被用在历史日期上时，在正文顶部明说。

    有些数据源只提供"此刻"的值（腾讯实时行情、同花顺当前一致预期），拿不到
    某个历史日的原值。既然补不上，就必须**说出来**——否则模型会把今天的估值
    当成分析日当天的事实写进报告，而这种污染在报告里完全看不出来。
    """
    return (
        f"⚠️ 未来函数警告：以下{what}是**此刻的实时快照**，不是 {curr_date} 当天的值。"
        f"本数据源不提供历史时点数据。在复盘历史日期时，**不得**把这些数字当作"
        f"{curr_date} 当天已知的事实，也不要据此推断当时的判断。\n"
    )


def _atomic_write_text(path: str, text: str, *, newline: str | None = None) -> None:
    """本地别名——实现放在 `dataflows/utils.py`，供整个数据层共用。"""
    return atomic_write_text(path, text, newline=newline)


# ---------------------------------------------------------------------------
# mootdx client (singleton)
# ---------------------------------------------------------------------------

_mootdx_client = None

# mootdx/通达信走**单条 TCP 连接**，协议是有状态的请求-响应，不是线程安全的：
# 两个线程同时用同一个 client 会让响应错位（拿到的可能是另一个请求的 K 线）。
# 而 Web UI 里 tracker 是 per-session 的，两个浏览器会话可以各跑一轮分析，名称
# 映射解析（`_build_name_code_map`）也走同一条连接。用 RLock 而非 Lock：
# `_mootdx_call` 持锁期间会调用 `reset_mootdx_client()`，后者要取同一把锁。
_MOOTDX_LOCK = threading.RLock()

# mootdx（通达信协议）单次 bars 请求能取回的日线根数上限，约 3 年交易日。
# 请求更早的 start_date 时必须说清楚被截断了，不能只写 "# Total records: N"。
_MOOTDX_BAR_LIMIT = 800

# 实测可用的通达信备选服务器（按延迟排序，2026-06 验证）。用于规避 mootdx
# 0.11.x 全新安装时 BESTIP.HQ 为空串导致的 `ValueError: not enough values to unpack`。
_TDX_SERVERS = [
    ("119.97.185.59", 7709), ("124.70.133.119", 7709), ("116.205.183.150", 7709),
    ("123.60.73.44", 7709), ("116.205.163.254", 7709), ("121.36.225.169", 7709),
    ("123.60.70.228", 7709), ("124.71.9.153", 7709), ("110.41.147.114", 7709),
    ("124.71.187.122", 7709),
]


# 探测用的探针股票：主板老票，任何通达信服务器都应能返回它的日线。
_TDX_CANARY_SYMBOL = "600519"

# 全部服务器都验不过之后，隔多久才允许再探一轮（秒）。没有这个负缓存，
# 每一次取数都会把整张服务器表重探一遍（10 台 × TCP 超时），把"取不到数"
# 放大成"每个请求卡几十秒"。
_MOOTDX_RETRY_AFTER_S = 300.0
_mootdx_unavailable_until = 0.0

# ⚠️ 曾经加过「连续 N 台协议失败就停手」的提前退出，已移除：三台远端拒绝**证明不了**
# 本地网络封了协议，而列表里靠后的服务器完全可能是好的。提前收手会让那台可用服务器
# 永远试不到，还顺手记下 5 分钟负缓存。省下的十几秒不值得换这个风险——真正的耗时
# 大头是 bestip 全表测速，那个已经单独规避了。


def _candidate_tdx_servers() -> list[tuple[str, int]]:
    """待试的通达信服务器：先用实测精选的 `_TDX_SERVERS`，再补 mootdx 自带的完整主机表。

    只试精选的那 10 台是不够的——它们要是恰好都不可用，而 mootdx 自带表里还有活着的
    主机，就会被判成"全网不可达"并记 5 分钟负缓存。这里把两张表合起来去重后逐台验证，
    覆盖面等同 `bestip`，但不做它那套要跑几分钟的全表测速。
    """
    servers = list(_TDX_SERVERS)
    seen = set(servers)
    try:
        from mootdx.consts import HQ_HOSTS
        for entry in HQ_HOSTS:
            # 形如 ("深圳双线主站1", "110.41.147.114", 7709)
            host = (entry[1], entry[2]) if len(entry) >= 3 else None
            if host and host not in seen:
                seen.add(host)
                servers.append(host)
    except Exception as e:  # mootdx 版本变动导致取不到就只用精选表，不影响主流程
        logger.debug("读取 mootdx HQ_HOSTS 失败，仅使用内置精选表：%s", e)
    return servers


def _reachable_tdx_servers(servers, timeout: float = 2.0):
    """并发做 TCP 预筛，返回可连的那些（保持原顺序）。

    只是把"等超时"这件事并行化，不改变优先级：返回顺序仍是候选表顺序，所以实测
    精选的服务器依旧排在前面、依旧第一个被真实验证。
    """
    from concurrent.futures import ThreadPoolExecutor

    if not servers:
        return []
    with ThreadPoolExecutor(max_workers=min(16, len(servers))) as pool:
        flags = list(pool.map(lambda s: _probe_tdx(s[0], s[1], timeout), servers))
    return [srv for srv, ok in zip(servers, flags) if ok]


def _probe_tdx(ip: str, port: int, timeout: float = 2.0) -> bool:
    """TCP 握手探测通达信服务器端口是否开着。

    ⚠️ 只是**廉价预筛**，通过不代表能取到数：实测存在大量"TCP 三次握手成功、
    通达信协议握手立刻被 RST"的服务器。选服务器必须再走 `_tdx_client_works()`
    做一次真实取数验证（#90）。
    """
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _tdx_client_works(client) -> bool:
    """真实拉一根 K 线来验证这个 client 确实能取数。"""
    try:
        df = client.bars(symbol=_TDX_CANARY_SYMBOL, category=4, offset=1)
        return df is not None and not df.empty
    except Exception:
        return False


def reset_mootdx_client() -> None:
    """丢弃缓存的 client，让下一次调用重新选服务器。

    单例一旦钉在一台"当时能用、后来挂了"的服务器上，之后每次取数都失败降级且
    永远不会重选。数据调用发现 mootdx 出错时调它，下一次就能换一台（#90）。
    """
    global _mootdx_client, _mootdx_unavailable_until
    with _MOOTDX_LOCK:
        _mootdx_client = None
        _mootdx_unavailable_until = 0.0


@contextlib.contextmanager
def _preserve_mootdx_bestip():
    """探测期间保护 mootdx 的持久化服务器配置，退出时按需还原。

    `StdQuotes.__init__` 里有 `config.set('BESTIP', {'HQ': self.server})`——**每建一次
    带 server 的 client 都会写进 mootdx 的配置文件**。逐台探测 38 个候选就等于把用户
    原本配好的服务器一路覆写，最后留下的是最后一台**失败的**服务器，还会连累同一台
    机器上其它用 mootdx 的程序。

    🔴 必须先 `setup()` 再快照：新进程里 `config.get("BESTIP")` 返回的是模块默认空值，
    用户持久化的值要等 `BaseQuotes.__init__` 调 `setup()` 才读进来。快照到空值的话，
    "还原"反而会把真实配置抹成空——比不还原更糟。
    实测（mootdx 0.11.7）：setup 前 `{'HQ': ''}`，setup 后 `{'HQ': ['218.6.x.x', 7709]}`。

    用法：`with _preserve_mootdx_bestip() as keep:` —— 选出可用服务器时调 `keep()`
    表示"这次的覆写是我们想要的，别还原"；不调就在退出时还原。

    ⚠️ **做成上下文管理器而不是手动调还原函数**：此前是在两处分别调 `_restore_bestip()`，
    再加一条提前返回就会漏掉一处，而漏掉的后果是静默留下一台死服务器。
    """
    saved = None
    try:
        from mootdx import config as _cfg
        _cfg.setup()
        saved = _cfg.get("BESTIP")
        if isinstance(saved, dict):
            saved = dict(saved)
    except Exception as e:  # 版本差异导致取不到就跳过保护，别影响主流程
        logger.debug("读取 mootdx BESTIP 失败，本次探测不做保护：%s", e)

    keep = {"flag": False}
    try:
        yield lambda: keep.__setitem__("flag", True)
    finally:
        if saved is not None and not keep["flag"]:
            try:
                from mootdx import config as _cfg2
                _cfg2.set("BESTIP", saved)
            except Exception as e:
                logger.debug("恢复 mootdx BESTIP 失败：%s", e)


def _get_mootdx_client():
    """Lazy-init 健壮版 mootdx Quotes client（TCP 连接，可复用）。

    选服务器的顺序：内置服务器表（TCP 预筛 + 真实取数验证）→ bestip 测速 →
    裸 factory（老用户 config 里已有 IP）。每一级都必须真正取到数据才会被采用，
    避免把 client 钉死在一台"端口开着但协议不通"的服务器上（#90）。
    全部失败时抛 RuntimeError，并在 `_MOOTDX_RETRY_AFTER_S` 内直接快速失败，
    不再逐台重探。
    """
    global _mootdx_client, _mootdx_unavailable_until
    if _mootdx_client is not None:
        return _mootdx_client

    now = time.time()
    if now < _mootdx_unavailable_until:
        raise RuntimeError(
            "mootdx 通达信服务器暂不可用（%.0f 秒内不再重试）。"
            "已尝试全部内置服务器：端口能连上的也没能完成通达信协议取数。"
            "请检查网络环境（代理/防火墙/公司网络常拦 TCP 7709），"
            "或改用 6 位股票代码直接查询。" % (_mootdx_unavailable_until - now)
        )

    from mootdx.quotes import Quotes

    tcp_ok_but_dead = 0
    # 探测会覆写 mootdx 的持久化配置——包在这里，只有真选出可用服务器时才 keep()，
    # 其余每条退出路径（含异常）都自动还原。
    with _preserve_mootdx_bestip() as keep_bestip:
        # TCP 预筛并发跑：38 台里多数是"连都连不上"，串行每台要等满超时（实测整轮
        # 73.7s，首次调用像卡死）。预筛纯粹是等 IO，并发不改变选取语义——下面仍按
        # 原顺序、逐台做真实取数验证，精选表依旧优先。
        reachable = _reachable_tdx_servers(_candidate_tdx_servers())

        for ip, port in reachable:
            # 「TCP 通但通达信协议不通」有两种表现：factory 建连时握手就被拒，
            # 或者建出来了但取不到数。**两种都要算**——只统计后者的话，计数永远是 0
            # （实测这批服务器全是在 factory 里抛 ConnectionReset），下面的快速失败
            # 判断就失效了。
            try:
                candidate = Quotes.factory(market="std", server=(ip, port))
            except Exception as e:
                tcp_ok_but_dead += 1
                logger.debug("mootdx %s:%s 握手失败（%s），换下一台", ip, port, type(e).__name__)
            else:
                if _tdx_client_works(candidate):
                    logger.info("mootdx server selected: %s:%s", ip, port)
                    keep_bestip()   # 这次的覆写正是我们想要的，别还原
                    _mootdx_client = candidate
                    return _mootdx_client
                tcp_ok_but_dead += 1
                logger.debug("mootdx %s:%s 建连成功但取不到数，换下一台", ip, port)

    # 走到这里说明逐台探测都没成——上面的 with 已经把 BESTIP 还原成用户原本的配置，
    # 下面的裸 factory 读的正是它，这个兜底才有意义。
    # ⚠️ 刻意**不用** `bestip=True`：它会把整张主机表做一遍测速，实测要几分钟。
    # `_candidate_tdx_servers()` 已经把 mootdx 自带的完整主机表逐台验证过了，
    # 覆盖面不比 bestip 差，而且每台都是"真取到数才算通过"。
    try:
        candidate = Quotes.factory(market="std")
    except Exception as e:
        logger.debug("mootdx 裸 factory 失败 — %s", e)
    else:
        if _tdx_client_works(candidate):
            logger.info("mootdx client from 裸 factory（用户已有配置）")
            _mootdx_client = candidate
            return _mootdx_client

    _mootdx_unavailable_until = time.time() + _MOOTDX_RETRY_AFTER_S
    if tcp_ok_but_dead:
        # 说清楚是"协议被拒"而不是"连不上"——这两者的排查方向完全不同。
        cause = (
            "%d 台服务器端口能连上，但通达信协议握手/取数被拒。"
            "这通常是协议层被拦（代理、防火墙、公司网络对 TCP 7709 的策略），"
            "换服务器解决不了。" % tcp_ok_but_dead
        )
    else:
        cause = "内置服务器表里没有一台的 TCP 7709 能连上，请检查网络连通性。"
    raise RuntimeError(
        "mootdx 通达信服务器不可用：%s"
        "可改用 6 位股票代码直接查询。%.0f 秒内将直接快速失败、不再逐台重探。"
        % (cause, _MOOTDX_RETRY_AFTER_S)
    )


def _mootdx_call(method: str, **kwargs):
    """调用 mootdx 的某个方法，失败就弃用当前服务器。

    选中的服务器随时可能挂掉；不弃用的话单例会一直指着它，之后每次取数都失败降级
    且永不重选（#90 的「反复降级」）。取 client 本身失败时不清缓存——那条路径已经
    在 `_get_mootdx_client` 里做了负缓存，清掉等于取消快速失败。

    整段（选服务器 + 取数）都在 `_MOOTDX_LOCK` 内：单条 TCP 连接不是线程安全的，
    选服务器时还会改写 mootdx 的持久化配置。
    """
    with _MOOTDX_LOCK:
        client = _get_mootdx_client()
        try:
            return getattr(client, method)(**kwargs)
        except Exception:
            reset_mootdx_client()
            raise


# ---------------------------------------------------------------------------
# Tencent Finance API
# ---------------------------------------------------------------------------

def _tencent_quote(codes: list[str]) -> dict[str, dict]:
    """Batch real-time quotes from Tencent Finance (qt.gtimg.cn).

    Returns dict[code] -> {name, price, pe_ttm, pb, mcap_yi, ...}
    """
    prefixed = [f"{_get_prefix(c)}{c}" for c in codes]
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    resp = urllib.request.urlopen(req, timeout=10)
    raw = resp.read().decode("gbk")

    result = {}
    for line in raw.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]  # strip sh/sz/bj prefix
        result[code] = {
            "name": vals[1],
            "price": float(vals[3]) if vals[3] else 0,
            "last_close": float(vals[4]) if vals[4] else 0,
            "open": float(vals[5]) if vals[5] else 0,
            "change_pct": float(vals[32]) if vals[32] else 0,
            "high": float(vals[33]) if vals[33] else 0,
            "low": float(vals[34]) if vals[34] else 0,
            "turnover_pct": float(vals[38]) if vals[38] else 0,
            "pe_ttm": float(vals[39]) if vals[39] else 0,
            "mcap_yi": float(vals[44]) if vals[44] else 0,
            "float_mcap_yi": float(vals[45]) if vals[45] else 0,
            "pb": float(vals[46]) if vals[46] else 0,
            "limit_up": float(vals[47]) if vals[47] else 0,
            "limit_down": float(vals[48]) if vals[48] else 0,
            "pe_static": float(vals[52]) if vals[52] else 0,
        }
    return result


def get_stock_name(code: str) -> str | None:
    """Resolve one 6-digit A-share code to its name with a single Tencent call.

    Deliberately NOT built on ``_build_name_code_map()``. That fetches a
    full-market table over mootdx/TCP — the right tool for a name→code lookup,
    but wildly disproportionate for a code→name display label, and it blocks
    for roughly 80 seconds when the Tongdaxin port is unreachable (14 servers
    pass the TCP pre-screen and each then waits out mootdx's own timeout, one
    after another). Tencent answers the same question in about a second.

    Returns None when the code is not an A-share or the lookup fails, so
    callers can fall back to showing the bare code.
    """
    norm = _re.sub(
        r"^(sh|sz|bj)|\s*\.\s*(sh|sz|bj)$",
        "",
        str(code).strip(),
        flags=_re.IGNORECASE,
    )
    if not _re.match(r"^[03689]\d{5}$", norm):
        return None

    try:
        quote = _tencent_quote([norm])
    except Exception as exc:  # 网络问题只该让名称缺失，不该让调用方崩
        logger.debug("腾讯个股名称查询失败 %s：%s", norm, exc)
        return None

    name = str((quote.get(norm) or {}).get("name", ""))
    # Tencent pads some names ("五 粮 液"); _build_name_code_map normalises the
    # same way, so the label looks the same however the name was resolved.
    name = name.replace(" ", "").replace("　", "").strip()
    return name or None


# ---------------------------------------------------------------------------
# Eastmoney Datacenter unified helper (龙虎榜/解禁 etc.)
# ---------------------------------------------------------------------------

_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


# ---------------------------------------------------------------------------
# 东财防封：全局节流 + 会话复用 (Eastmoney anti-ban: throttle + Keep-Alive)
# ---------------------------------------------------------------------------
# 东财系 HTTP 接口（push2 / push2his / datacenter-web / search-api / np-weblist）
# 有风控：每秒 >5 次 / 单 IP 并发 ≥10 / 1 分钟 ≥200 次 / 5 分钟 ≥300 次 → 临时封 IP。
# 多 Agent 投研跑批量分析时会高频请求东财，是被封的头号元凶。所有 eastmoney.com
# 请求一律走 _em_get()：串行限流（最小间隔 + 随机抖动）+ 复用 Keep-Alive 会话 + 默认 UA。
# 注意：仅东财接口走此入口；mootdx(TCP) / 腾讯 / 新浪 / 同花顺 / 财联社 / 百度 等
# 不限流（实测不封 IP 或风控极弱）。批量任务可调大 EM_MIN_INTERVAL 进一步降速。
_EM_SESSION = _requests.Session()
_EM_SESSION.headers.update({"User-Agent": _UA})
# 两次东财请求最小间隔(秒)；批量多 Agent 场景可设环境变量 EM_MIN_INTERVAL=1.5~2 降速。
_EM_MIN_INTERVAL = float(os.environ.get("EM_MIN_INTERVAL", "1.0"))
_em_last_call = [0.0]  # 模块级上次东财请求时间戳
# 节流必须真的「串行」。原先只做 check-then-act（读时间戳 → sleep → 请求 → 写时间戳），
# 两个线程会读到同一个陈旧时间戳然后双双放行，EM_MIN_INTERVAL 形同虚设——而
# README / CLAUDE.md 恰恰承诺了「串行限流」。这把锁覆盖 sleep + 请求整段。
_EM_LOCK = threading.Lock()

# 429/5xx/网络抖动重试。东财风控命中时返回 429，偶发 502/503 也很常见；原先只发
# 一次请求，调用方拿到一个 429 响应体后 `.json()` 解析失败（或拿到空 data），最终在
# 报告里显示成「未上龙虎榜」/「行业数据获取为空」——把**接口失败**冒充成**真的没数据**。
# 3 次重试（共 4 次尝试）+ 指数退避 1/2/4s，全部失败最后抛出，让调用方能区分两者。
_EM_MAX_RETRIES = int(os.environ.get("EM_MAX_RETRIES", "3"))
_EM_RETRY_BASE_S = float(os.environ.get("EM_RETRY_BASE_S", "1.0"))
_EM_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def _em_get(url, params=None, headers=None, timeout=15, **kwargs):
    """东财统一请求入口：自动节流 + 复用 session + 默认 UA + 退避重试。

    所有 eastmoney.com 接口都应通过它请求，避免多 Agent 高频拉数据被封 IP。
    串行限流：与上次东财请求间隔 < EM_MIN_INTERVAL 时 sleep 补足 + 0.1~0.5s 随机抖动。
    传入的 headers 会覆盖 session 默认 UA（用于保留各端点自己的 Referer/Origin）。

    429 / 5xx / 超时 / 连接错误会重试 `_EM_MAX_RETRIES` 次（指数退避）；重试仍失败则
    **抛出**，而不是把失败响应交给调用方当空数据用。整段重试都在 `_EM_LOCK` 内，与
    节流共用同一把锁——这里本来就是「串行访问东财」的意思。
    """
    last_exc: Exception | None = None
    with _EM_LOCK:
        for attempt in range(_EM_MAX_RETRIES + 1):
            wait = _EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
            if wait > 0:
                time.sleep(wait + random.uniform(0.1, 0.5))
            try:
                response = _EM_SESSION.get(
                    url, params=params, headers=headers, timeout=timeout, **kwargs
                )
            except (_requests.RequestException, OSError) as exc:
                last_exc = exc
            else:
                if response.status_code not in _EM_RETRY_STATUS:
                    return response
                last_exc = _requests.HTTPError(
                    f"东财返回 {response.status_code} for {url}", response=response
                )
            finally:
                _em_last_call[0] = time.time()

            if attempt < _EM_MAX_RETRIES:
                backoff = _EM_RETRY_BASE_S * (2**attempt)
                logger.warning(
                    "东财请求失败（第 %d/%d 次）：%s；%.1fs 后重试",
                    attempt + 1,
                    _EM_MAX_RETRIES + 1,
                    last_exc,
                    backoff,
                )
                time.sleep(backoff)

    logger.error("东财请求连续 %d 次失败：%s", _EM_MAX_RETRIES + 1, last_exc)
    raise last_exc if last_exc is not None else RuntimeError(f"东财请求失败: {url}")


def _eastmoney_datacenter(
    report_name: str,
    columns: str = "ALL",
    filter_str: str = "",
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "-1",
) -> list[dict]:
    """东财数据中心统一查询 — 龙虎榜/解禁 共用."""
    params = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    r = _em_get(_DATACENTER_URL, params=params, timeout=15)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


# ---------------------------------------------------------------------------
# 同花顺 EPS forecast helper (direct HTTP, no akshare)
# ---------------------------------------------------------------------------


def _ths_eps_forecast(code: str) -> pd.DataFrame:
    """Fetch consensus EPS forecast from 同花顺 (direct HTTP).

    Returns DataFrame with columns roughly: 年度, 预测机构数, 最小值, 均值, 最大值.
    """
    url = f"https://basic.10jqka.com.cn/new/{code}/worth.html"
    headers = {
        "User-Agent": _UA,
        "Referer": "https://basic.10jqka.com.cn/",
    }
    r = _requests.get(url, headers=headers, timeout=15)
    r.encoding = "gbk"
    dfs = pd.read_html(r.text)
    # Find the table containing EPS data
    for df in dfs:
        cols = [str(c) for c in df.columns]
        if any("每股收益" in c or "均值" in c for c in cols):
            return df
    # Fallback: return first table if exists
    return dfs[0] if dfs else pd.DataFrame()


# ---------------------------------------------------------------------------
# Sina K-line fallback helper (direct HTTP, no akshare)
# ---------------------------------------------------------------------------


def _sina_kline_fallback(code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """Fetch daily K-line from Sina HTTP API as mootdx fallback.

    Returns DataFrame with columns: Date, Open, High, Low, Close, Volume.
    """
    prefix = "sh" if code.startswith("6") else "sz"
    url = (
        "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_MarketData.getKLineData"
    )
    params = {
        "symbol": f"{prefix}{code}",
        "scale": "240",  # daily
        "ma": "no",
        "datalen": "800",
    }
    r = _requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = _json.loads(r.text)

    if not data:
        return pd.DataFrame()

    rows = []
    for item in data:
        rows.append({
            "Date": item["day"],
            "Open": float(item["open"]),
            "High": float(item["high"]),
            "Low": float(item["low"]),
            "Close": float(item["close"]),
            "Volume": int(item["volume"]),
        })

    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])

    if start_date:
        df = df[df["Date"] >= pd.to_datetime(start_date)]
    if end_date:
        df = df[df["Date"] <= pd.to_datetime(end_date)]

    return df


def _last_ohlcv_date(df: pd.DataFrame) -> pd.Timestamp | None:
    """Return the latest OHLCV Date in a normalized dataframe."""
    if df is None or df.empty or "Date" not in df.columns:
        return None
    dates = pd.to_datetime(df["Date"], errors="coerce")
    if dates.dropna().empty:
        return None
    return dates.max().normalize()


def _normalize_ohlcv_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize OHLCV Date values to daily granularity."""
    if df is None or df.empty or "Date" not in df.columns:
        return df
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    return df.dropna(subset=["Date"])


def _needs_sina_supplement(df: pd.DataFrame, target_date: str | None) -> bool:
    """True when mootdx/cache data is older than the requested cutoff date."""
    if not target_date:
        return False
    last_date = _last_ohlcv_date(df)
    if last_date is None:
        return True
    target = pd.to_datetime(target_date).normalize()
    return last_date < target


def _merge_ohlcv(primary: pd.DataFrame, supplement: pd.DataFrame) -> pd.DataFrame:
    """Merge OHLCV frames, preferring supplement rows on duplicate dates."""
    frames = [frame for frame in (primary, supplement) if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    combined = pd.concat(frames, ignore_index=True)
    combined = _normalize_ohlcv_dates(combined)
    combined = combined.drop_duplicates(subset=["Date"], keep="last")
    combined = combined.sort_values("Date").reset_index(drop=True)
    return combined


def _supplement_stale_ohlcv_with_sina(
    code: str,
    df: pd.DataFrame,
    target_date: str | None,
    start_date: str | None = None,
) -> tuple[pd.DataFrame, bool]:
    """Use Sina daily K-line to fill dates missing from mootdx/cache data."""
    if not _needs_sina_supplement(df, target_date):
        return df, False
    try:
        sina_df = _sina_kline_fallback(code, start_date, target_date)
    except Exception as e:
        logger.warning("sina K-line supplement failed for %s: %s", code, e)
        return df, False
    if sina_df.empty:
        return df, False
    merged = _merge_ohlcv(df, sina_df)
    return merged, _last_ohlcv_date(merged) != _last_ohlcv_date(df)


# ---------------------------------------------------------------------------
# OHLCV loading with cache (mootdx -> CSV)
# ---------------------------------------------------------------------------

def _no_data_reason(curr_date: str) -> str:
    """Calendar-aware suffix for a "no data" error.

    MARVEL addition (see dataflows/trade_calendar.py). Without it, asking for a
    non-trading day — or for today before the daily bar has closed — surfaces
    as a bare "no OHLCV data", which reads like a data-source outage and sends
    the reader looking for a bug that isn't there.
    """
    try:
        from .trade_calendar import cn_no_data_reason

        return " — " + cn_no_data_reason(curr_date)
    except Exception:  # noqa: BLE001 — a hint must never break data fetching
        return ""


def _ohlcv_cache_is_final(cache_file: str, last_bar_date) -> bool:
    """Is this same-day K-line cache safe to reuse as-is?

    旧判据只有一条：缓存文件的 mtime 是**今天**（而且还是主机本地时区的"今天"）。
    盘中 10:30 跑过一次分析时写下的是当天那根**没走完**的 K 线；同一天晚上再跑，
    缓存依旧命中，于是那根半截 K 线被当成当日最终收盘价喂给模型——报告里看不出
    任何异常，均线/涨跌幅却全偏。

    现在的规则：
    - 不是今天写的 → 重取；
    - 缓存里还没有今天那根 → 只有"今天已收盘"时重取才有意义（能补上今天），
      盘前/盘中/非交易日缓存已经是最新的完整数据，直接复用；
    - 缓存里已经有今天那根 → 只有**收盘之后**写下的才可能是完整的最终日线。
    """
    today = _market_today()
    mtime = datetime.fromtimestamp(os.path.getmtime(cache_file), tz=_MARKET_TZ)
    if mtime.date() != today:
        return False

    try:
        from .trade_calendar import cn_market_phase

        phase = cn_market_phase()
    except Exception:  # noqa: BLE001 — 日历不可用时退化成"按钟点判断"
        phase = "post_close" if datetime.now(_MARKET_TZ).hour >= 15 else "in_session"

    # `last_bar_date` 来自 `DataFrame["Date"].max()`，是 pd.Timestamp，直接和 date
    # 比较会抛 TypeError；统一降到 date 再比。
    last_bar_day = None
    if last_bar_date is not None:
        stamp = pd.to_datetime(last_bar_date, errors="coerce")
        if not pd.isna(stamp):
            last_bar_day = stamp.date()

    has_today_bar = last_bar_day is not None and last_bar_day >= today
    if not has_today_bar:
        return phase != "post_close"

    close_dt = datetime.combine(today, _dtime(15, 0), tzinfo=_MARKET_TZ)
    return mtime >= close_dt


def _load_ohlcv_astock(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch OHLCV via mootdx, cache to CSV, filter by curr_date.

    Mirrors stockstats_utils.load_ohlcv but uses mootdx instead of yfinance.
    Returns DataFrame with columns: Date, Open, High, Low, Close, Volume
    """
    from .config import get_config

    code = _normalize_ticker(symbol)
    config = get_config()
    cache_dir = config.get(
        "data_cache_dir", os.path.expanduser("~/.marvel/cache")
    )
    os.makedirs(cache_dir, exist_ok=True)

    cache_file = os.path.join(cache_dir, f"{code}-astock-daily.csv")

    if os.path.exists(cache_file):
        cached = None
        try:
            cached = _normalize_ohlcv_dates(
                pd.read_csv(cache_file, on_bad_lines="skip", encoding="utf-8")
            )
        except Exception as exc:  # 缓存坏掉只是多取一次，不能影响主流程
            logger.warning("K 线缓存读取失败，将重新获取：%s", exc)
        if (
            cached is not None
            and not cached.empty
            and "Date" in cached.columns
            and _ohlcv_cache_is_final(cache_file, cached["Date"].max())
        ):
            data, supplemented = _supplement_stale_ohlcv_with_sina(
                code, cached, curr_date, start_date=None
            )
            if supplemented:
                _atomic_write_text(cache_file, data.to_csv(index=False))
            cutoff = pd.to_datetime(curr_date)
            return data[data["Date"] <= cutoff]

    # Fetch from mootdx — `_MOOTDX_BAR_LIMIT` daily bars (~3 years of trading days)
    try:
        df = _mootdx_call("bars", symbol=code, category=4, offset=_MOOTDX_BAR_LIMIT)

        if df is None or df.empty:
            raise ValueError(f"No OHLCV data from mootdx for {code}")

        # mootdx returns index named 'datetime' AND a column named 'datetime'
        # (plus year/month/day/hour/minute/volume). Drop duplicates before reset.
        df = df.drop(columns=["datetime", "year", "month", "day", "hour", "minute"], errors="ignore")
        df = df.reset_index()  # moves index 'datetime' → column 'datetime'
        rename_map = {
            "datetime": "Date",
            "open": "Open",
            "close": "Close",
            "high": "High",
            "low": "Low",
            "volume": "Volume",
        }
        df = df.rename(columns=rename_map)
        df = df[["Date", "Open", "High", "Low", "Close", "Volume"]]
        df = _normalize_ohlcv_dates(df)
        # 指标路径没法在数值里夹带说明，所以至少留下日志：分析日早于数据源窗口时，
        # 这一路算出来的长周期指标其实是在"没有那段行情"的前提下算的。
        if len(df) >= _MOOTDX_BAR_LIMIT:
            earliest = df["Date"].min()
            cutoff_dt = pd.to_datetime(curr_date, errors="coerce")
            if not pd.isna(cutoff_dt) and earliest > cutoff_dt:
                logger.warning(
                    "OHLCV 窗口不足：%s 只取到最近 %d 根（最早 %s），"
                    "早于该日的指标（长周期均线等）不可靠",
                    code,
                    _MOOTDX_BAR_LIMIT,
                    earliest.date(),
                )
    except Exception as e:
        logger.warning("mootdx OHLCV failed for %s: %s, trying sina HTTP fallback", code, e)
        # Fallback: Sina direct HTTP API
        try:
            df = _sina_kline_fallback(code)
            if df.empty:
                raise ValueError(f"No OHLCV data from sina for {code}")
        except Exception:
            raise ValueError(
                f"No OHLCV data from mootdx/sina for {code}{_no_data_reason(curr_date)}"
            )

    df, _ = _supplement_stale_ohlcv_with_sina(code, df, curr_date, start_date=None)

    # Cache to disk
    _atomic_write_text(cache_file, df.to_csv(index=False))

    # Filter by curr_date to prevent look-ahead bias
    cutoff = pd.to_datetime(curr_date)
    return df[df["Date"] <= cutoff]


# ===========================================================================
# 9 Vendor Methods (matching interface.py VENDOR_METHODS signatures)
# ===========================================================================


# ---- 1. get_stock_data ----


def get_stock_data(
    symbol: Annotated[str, "A-stock code (e.g. 688017, SH688017)"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Get OHLCV stock price data via mootdx."""
    code = _normalize_ticker(symbol)

    data_source = "mootdx (TCP)"
    window_is_capped = False
    try:
        df = _mootdx_call("bars", symbol=code, category=4, offset=_MOOTDX_BAR_LIMIT)

        if df is None or df.empty:
            raise ValueError(f"No data from mootdx for {code}")

        # mootdx 一次只给最近 800 根（约 3 年）。拿到满额就说明"更早的还有，只是没取"，
        # 用它来判断下面的请求区间是否被静默截断。
        window_is_capped = len(df) >= _MOOTDX_BAR_LIMIT

        # Drop duplicate datetime column + extra columns before reset_index
        df = df.drop(
            columns=["datetime", "year", "month", "day", "hour", "minute"],
            errors="ignore",
        )
        df = df.reset_index()  # index 'datetime' → column 'datetime'
        df = df.rename(
            columns={
                "datetime": "Date",
                "open": "Open",
                "close": "Close",
                "high": "High",
                "low": "Low",
                "volume": "Volume",
                "amount": "Amount",
            }
        )
        df = _normalize_ohlcv_dates(df)

    except Exception as e:
        logger.warning("mootdx K-line failed for %s: %s, trying sina HTTP fallback", code, e)
        # Fallback: Sina direct HTTP API
        try:
            df = _sina_kline_fallback(code, start_date, end_date)
            if df.empty:
                return (
                    "K线数据获取失败：mootdx和新浪备用源均不可用，请检查网络连接"
                    + _no_data_reason(end_date)
                )
            data_source = "sina HTTP (fallback)"
        except Exception:
            return (
                "K线数据获取失败：mootdx和新浪备用源均不可用，请检查网络连接"
                + _no_data_reason(end_date)
            )

    df, supplemented = _supplement_stale_ohlcv_with_sina(code, df, end_date, start_date)
    if supplemented:
        data_source = f"{data_source} + sina HTTP supplement"

    # Filter by date range.
    # ``end_date`` comes from the model, so it cannot be trusted: nothing in any
    # prompt asks for the analysis date, and the natural default (today) pulls
    # bars that did not exist on a historical analysis date into the report.
    # Clamp to the market date and say so, in the same spirit as
    # _snapshot_notice() — every other date-aware tool here clamps or warns.
    start_dt = pd.to_datetime(start_date, errors="coerce")
    end_dt = pd.to_datetime(end_date, errors="coerce")
    clamped_note = ""
    market_today = _market_today()
    if pd.isna(start_dt):
        return f"Invalid start_date for A-stock '{code}': {start_date!r}"
    if pd.isna(end_dt) or end_dt.date() > market_today:
        requested_end = end_date
        end_dt = pd.Timestamp(market_today)
        end_date = market_today.strftime("%Y-%m-%d")
        clamped_note = (
            f"# ⚠️ end_date 已从 {requested_end} 收敛到市场当天 {end_date}："
            f"分析日之后不存在已知的 K 线，不能把它们当作事实。\n"
        )
    df = df[(df["Date"] >= start_dt) & (df["Date"] <= end_dt)]

    if df.empty:
        return (
            f"No data found for A-stock '{code}' "
            f"between {start_date} and {end_date}"
            + _no_data_reason(end_date)
        )

    # 请求区间早于数据源窗口时，前面的 K 线**根本没被取回来**（mootdx 单次上限 800 根，
    # 且它一次只给"最近"的那些）。原先只在 header 里写 "# Total records: N"，等于
    # 把"我只取了近三年"说成"这只票三年以前没有数据"——模型据此算长周期均线、
    # 判断历史位置，报告里完全看不出缺了一段。
    truncation_note = ""
    if window_is_capped:
        earliest = df["Date"].min()
        if earliest > start_dt:
            truncation_note = (
                f"# ⚠️ 请求区间从 {start_date} 开始，但该数据源单次只提供最近 "
                f"{_MOOTDX_BAR_LIMIT} 根日线（最早 {earliest:%Y-%m-%d}）。"
                f"{start_date} ~ {earliest:%Y-%m-%d} 之间**不是没有行情，而是没有取回来**，"
                f"不得据此判断该区间无数据或计算跨区间的指标。\n"
            )

    for col in ["Open", "High", "Low", "Close"]:
        if col in df.columns:
            df[col] = df[col].round(2)

    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    csv_out = df[["Date", "Open", "High", "Low", "Close", "Volume"]].to_csv(
        index=False
    )

    header = f"# Stock data for {code} (A-stock) from {start_date} to {end_date}\n"
    header += clamped_note
    header += truncation_note
    header += f"# Total records: {len(df)}\n"
    header += f"# Data source: {data_source}\n"
    header += (
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )

    return header + csv_out


# ---- 2. get_indicators ----

# Supported technical indicators with descriptions
_INDICATOR_DESCRIPTIONS = {
    "close_50_sma": "50 SMA: Medium-term trend indicator.",
    "close_200_sma": "200 SMA: Long-term trend benchmark.",
    "close_10_ema": "10 EMA: Responsive short-term average.",
    "macd": "MACD: Momentum via EMA differences.",
    "macds": "MACD Signal: EMA smoothing of MACD line.",
    "macdh": "MACD Histogram: Gap between MACD and signal.",
    "rsi": "RSI: Momentum overbought/oversold indicator (70/30 thresholds).",
    "boll": "Bollinger Middle: 20 SMA basis for Bollinger Bands.",
    "boll_ub": "Bollinger Upper Band: 2 std devs above middle.",
    "boll_lb": "Bollinger Lower Band: 2 std devs below middle.",
    "atr": "ATR: Average True Range volatility measure.",
    "vwma": "VWMA: Volume-weighted moving average.",
    "mfi": "MFI: Money Flow Index (volume + price momentum).",
}


def get_indicators(
    symbol: Annotated[str, "A-stock code"],
    indicator: Annotated[
        str, "technical indicator (e.g. rsi, macd, close_50_sma)"
    ],
    curr_date: Annotated[str, "Current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    """Get technical indicators using stockstats on mootdx OHLCV data."""
    from stockstats import wrap

    code = _normalize_ticker(symbol)

    if indicator not in _INDICATOR_DESCRIPTIONS:
        raise ValueError(
            f"Indicator {indicator} not supported. "
            f"Choose from: {list(_INDICATOR_DESCRIPTIONS.keys())}"
        )

    try:
        data = _load_ohlcv_astock(code, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

        # Trigger stockstats calculation
        df[indicator]

        # Build date -> value lookup
        ind_dict = {}
        for _, row in df.iterrows():
            d = row["Date"]
            v = row[indicator]
            ind_dict[d] = "N/A" if pd.isna(v) else str(round(float(v), 4))

        # Generate output for look_back window
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        before = curr_dt - relativedelta(days=look_back_days)

        lines = []
        dt = curr_dt
        while dt >= before:
            ds = dt.strftime("%Y-%m-%d")
            val = ind_dict.get(ds, "N/A: Not a trading day (weekend or holiday)")
            lines.append(f"{ds}: {val}")
            dt -= relativedelta(days=1)

        result = (
            f"## {indicator} values for {code} "
            f"from {before.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
            + "\n".join(lines)
            + "\n\n"
            + _INDICATOR_DESCRIPTIONS.get(indicator, "")
        )
        return result

    except Exception as e:
        return f"Error calculating {indicator} for {code}: {str(e)}"


# ---- 3. get_fundamentals ----


def get_fundamentals(
    ticker: Annotated[str, "A-stock code"],
    curr_date: Annotated[str, "current date"] = None,
) -> str:
    """Get company fundamentals from Tencent + mootdx + Eastmoney + 同花顺."""
    code = _normalize_ticker(ticker)

    try:
        lines = []
        # 腾讯行情只有"此刻"的 PE/PB/市值，拿不到历史时点值。复盘历史日期时
        # 必须明说，否则模型会把今天的估值写成分析日当天的事实（未来函数）。
        if _is_historical(curr_date):
            lines.append(_snapshot_notice(curr_date, "估值与行情数据"))

        # --- Tencent: real-time valuation ---
        try:
            tq = _tencent_quote([code])
            if code in tq:
                q = tq[code]
                lines.extend(
                    [
                        f"Name: {q['name']}",
                        f"Price: {q['price']}",
                        f"PE (TTM): {q['pe_ttm']}",
                        f"PE (Static): {q['pe_static']}",
                        f"PB: {q['pb']}",
                        f"Market Cap (100M CNY): {q['mcap_yi']}",
                        f"Float Market Cap (100M CNY): {q['float_mcap_yi']}",
                        f"Turnover Rate: {q['turnover_pct']}%",
                        f"Change: {q['change_pct']}%",
                        f"Limit Up: {q['limit_up']}",
                        f"Limit Down: {q['limit_down']}",
                    ]
                )
        except Exception as e:
            logger.warning("Tencent quote failed for %s: %s", code, e)

        # --- mootdx: financial snapshot (quarterly) ---
        try:
            fin = _mootdx_call("finance", symbol=code)
            if fin is not None and not (
                isinstance(fin, pd.DataFrame) and fin.empty
            ):
                row = fin.iloc[0] if isinstance(fin, pd.DataFrame) else fin
                field_map = {
                    "eps": "EPS (Quarterly)",
                    "bvps": "Book Value Per Share",
                    "roe": "ROE (%)",
                    "profit": "Net Profit",
                    "income": "Revenue",
                    "liutongguben": "Float Shares",
                    "zongguben": "Total Shares",
                }
                idx = row.index if hasattr(row, "index") else []
                for field, label in field_map.items():
                    if field in idx:
                        val = row[field]
                        if val is not None and str(val) != "nan":
                            lines.append(f"{label}: {val}")
        except Exception as e:
            logger.warning("mootdx finance failed for %s: %s", code, e)

        # --- Eastmoney push2: basic stock info (direct HTTP) ---
        try:
            market_code = 1 if code.startswith("6") else 0
            _info_url = "https://push2.eastmoney.com/api/qt/stock/get"
            _info_params = {
                "fltt": "2",
                "invt": "2",
                "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
                "secid": f"{market_code}.{code}",
            }
            r = _em_get(_info_url, params=_info_params, timeout=10)
            d = r.json().get("data", {})
            if d:
                if d.get("f127"):
                    lines.append(f"行业: {d['f127']}")
                if d.get("f84"):
                    lines.append(f"总股本: {d['f84']}")
                if d.get("f85"):
                    lines.append(f"流通股本: {d['f85']}")
                if d.get("f116"):
                    lines.append(f"总市值: {d['f116']}")
                if d.get("f117"):
                    lines.append(f"流通市值: {d['f117']}")
                if d.get("f189"):
                    lines.append(f"上市日期: {d['f189']}")
        except Exception as e:
            logger.warning("eastmoney push2 stock info failed for %s: %s", code, e)

        # --- 同花顺 direct HTTP: consensus EPS forecast ---
        try:
            forecast_df = _ths_eps_forecast(code)
            if forecast_df is not None and not forecast_df.empty:
                lines.append("\n--- Consensus EPS Forecast (同花顺) ---")
                eps_by_year = {}
                for _, row in forecast_df.iterrows():
                    year = str(row.iloc[0]) if len(row) > 0 else ""
                    mean_eps_val = row.iloc[3] if len(row) > 3 else 0
                    count_val = row.iloc[1] if len(row) > 1 else 0
                    min_eps_val = row.iloc[2] if len(row) > 2 else "N/A"
                    max_eps_val = row.iloc[4] if len(row) > 4 else "N/A"
                    try:
                        mean_eps = float(mean_eps_val)
                    except (ValueError, TypeError):
                        mean_eps = 0
                    try:
                        count = int(count_val)
                    except (ValueError, TypeError):
                        count = 0
                    lines.append(
                        f"FY{year}: EPS={mean_eps} "
                        f"(range {min_eps_val}~{max_eps_val}, {count} analysts)"
                    )
                    if count < 3:
                        lines.append("  Warning: low coverage (<3 analysts)")
                    eps_by_year[year] = mean_eps

                # Forward PE / PEG / PE digestion
                try:
                    tq = _tencent_quote([code])
                    if code in tq:
                        price = tq[code]["price"]
                        years_sorted = sorted(eps_by_year.keys())
                        if years_sorted and eps_by_year.get(years_sorted[0], 0) > 0:
                            eps_cur = eps_by_year[years_sorted[0]]
                            fwd_pe = price / eps_cur
                            lines.append(
                                f"\nForward PE (FY{years_sorted[0]}): "
                                f"{fwd_pe:.1f}x (price={price}, EPS={eps_cur})"
                            )
                            if (
                                len(years_sorted) >= 2
                                and eps_by_year.get(years_sorted[1], 0) > 0
                            ):
                                eps_next = eps_by_year[years_sorted[1]]
                                cagr = eps_next / eps_cur - 1
                                if cagr > 0:
                                    peg = fwd_pe / (cagr * 100)
                                    lines.append(
                                        f"PEG: {peg:.2f} "
                                        f"(EPS CAGR={cagr * 100:.0f}%)"
                                    )
                                    if fwd_pe > 30:
                                        digest = math.log(fwd_pe / 30) / math.log(
                                            1 + cagr
                                        )
                                        lines.append(
                                            f"PE Digestion to 30x: {digest:.1f} years"
                                        )
                                    else:
                                        lines.append("PE already below 30x target")
                                else:
                                    lines.append(
                                        f"EPS declining ({cagr * 100:.0f}%), "
                                        f"PEG not applicable"
                                    )
                except Exception as e:
                    logger.warning("Forward PE calc failed for %s: %s", code, e)
        except Exception as e:
            logger.warning("Consensus EPS forecast failed for %s: %s", code, e)

        if not lines:
            return f"No fundamentals data found for A-stock '{code}'"

        header = f"# Company Fundamentals for {code} (A-stock)\n"
        header += (
            f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )

        return header + "\n".join(lines)

    except Exception as e:
        return f"Error retrieving fundamentals for {code}: {str(e)}"


# ---- 4. get_balance_sheet ----


def _sina_stock_code(code: str) -> str:
    """Pure 6-digit code → sina format (sh688017 / sz000001 / bj832000)."""
    return f"{_get_prefix(code)}{code}"


def _get_financial_report_sina(
    code: str, report_type: str, freq: str, curr_date: str = None,
) -> pd.DataFrame:
    """Shared helper: fetch financial report via Sina direct HTTP API.

    report_type: '资产负债表' | '利润表' | '现金流量表'
    """
    _report_type_map = {
        "资产负债表": "fzb",
        "利润表": "lrb",
        "现金流量表": "llb",
    }
    source_type = _report_type_map.get(report_type, "lrb")

    # 用 _sina_stock_code（走 _get_prefix），不要内联 `"sh" if startswith("6") else "sz"`：
    # 那条内联规则把北交所 8xxxxx/4xxxxx 派到 sz，新浪会返回空——而调用方只看到
    # "No balance sheet data found"，与真的没有报表完全分不清。
    paper_code = _sina_stock_code(code)
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    params = {
        "paperCode": paper_code,
        "source": source_type,
        "type": "0",
        "page": "1",
        "num": "20",
    }
    r = _requests.get(url, params=params, headers={"User-Agent": _UA}, timeout=15)
    d = r.json()

    result = d.get("result", {}).get("data", {})
    items = result.get(source_type, [])
    if not isinstance(items, list) or not items:
        return pd.DataFrame()

    df = pd.DataFrame(items)

    # Point-in-time cutoff.  A report period can *end* before the analysis date
    # while only being *published* after it, so the column has to be cut or the
    # model reads quarters that did not exist yet.
    #
    # This used to read ``if curr_date and "报告日" in df.columns`` — with the
    # tools defaulting curr_date to None, the guard silently switched itself off
    # and every statement returned the newest 8 periods.  A default is as good
    # as no guard at all, so: fall back to the market date, and refuse a payload
    # we cannot date rather than returning it unfiltered.
    if "报告日" not in df.columns:
        logger.warning(
            "财务报表明细缺少「报告日」列（%s），无法做时点裁剪，本批数据已丢弃",
            report_type,
        )
        return pd.DataFrame()

    cutoff = pd.to_datetime(curr_date or _market_today(), errors="coerce")
    if pd.isna(cutoff):
        return pd.DataFrame()
    df["报告日"] = pd.to_datetime(df["报告日"], errors="coerce")
    df = df[df["报告日"] <= cutoff]

    # Filter by frequency (annual = month 12 reports only)
    if freq.lower() == "annual":
        months = df["报告日"].dt.month
        df = df[months == 12]

    return df.head(8)


def get_balance_sheet(
    ticker: Annotated[str, "A-stock code"],
    freq: Annotated[str, "frequency: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
) -> str:
    """Get balance sheet via Sina direct HTTP API.

    ``curr_date`` may be omitted by a direct caller; the helper then cuts at the
    market date rather than skipping the point-in-time filter.
    """
    code = _normalize_ticker(ticker)

    try:
        df = _get_financial_report_sina(code, "资产负债表", freq, curr_date)

        if df.empty:
            return f"No balance sheet data found for A-stock '{code}'"

        csv_string = df.to_csv(index=False)

        header = f"# Balance Sheet for {code} (A-stock, {freq})\n"
        header += "# Data source: sina direct HTTP\n"
        header += (
            f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )

        return header + csv_string

    except Exception as e:
        return f"Error retrieving balance sheet for {code}: {str(e)}"


# ---- 5. get_cashflow ----


def get_cashflow(
    ticker: Annotated[str, "A-stock code"],
    freq: Annotated[str, "frequency: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
) -> str:
    """Get cash flow statement via Sina direct HTTP API.

    ``curr_date`` may be omitted by a direct caller; the helper then cuts at the
    market date rather than skipping the point-in-time filter.
    """
    code = _normalize_ticker(ticker)

    try:
        df = _get_financial_report_sina(code, "现金流量表", freq, curr_date)

        if df.empty:
            return f"No cash flow data found for A-stock '{code}'"

        csv_string = df.to_csv(index=False)

        header = f"# Cash Flow for {code} (A-stock, {freq})\n"
        header += "# Data source: sina direct HTTP\n"
        header += (
            f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )

        return header + csv_string

    except Exception as e:
        return f"Error retrieving cash flow for {code}: {str(e)}"


# ---- 6. get_income_statement ----


def get_income_statement(
    ticker: Annotated[str, "A-stock code"],
    freq: Annotated[str, "frequency: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
) -> str:
    """Get income statement via Sina direct HTTP API.

    ``curr_date`` may be omitted by a direct caller; the helper then cuts at the
    market date rather than skipping the point-in-time filter.
    """
    code = _normalize_ticker(ticker)

    try:
        df = _get_financial_report_sina(code, "利润表", freq, curr_date)

        if df.empty:
            return f"No income statement data found for A-stock '{code}'"

        csv_string = df.to_csv(index=False)

        header = f"# Income Statement for {code} (A-stock, {freq})\n"
        header += "# Data source: sina direct HTTP\n"
        header += (
            f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )

        return header + csv_string

    except Exception as e:
        return f"Error retrieving income statement for {code}: {str(e)}"


# ---- 7. get_news ----


def _fetch_news_eastmoney(code: str, page_size: int = 20) -> list[dict]:
    """Direct East Money search API for individual stock news."""
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    inner_param = {
        "uid": "",
        "keyword": code,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": page_size,
                "preTag": "",
                "postTag": "",
            }
        },
    }
    params = {
        "cb": "callback",
        "param": _json.dumps(inner_param, ensure_ascii=False),
        "_": "1",
    }
    headers = {
        "Referer": "https://so.eastmoney.com/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        ),
    }

    resp = _em_get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    text = resp.text
    text = text[text.index("(") + 1 : text.rindex(")")]
    data = _json.loads(text)

    articles: list[dict] = []
    for item in data.get("result", {}).get("cmsArticleWebOld", []):
        articles.append({
            "title": item.get("title", ""),
            "content": item.get("content", ""),
            "time": item.get("date", ""),
            "source": item.get("mediaName", "东方财富"),
            "url": item.get("url", ""),
        })
    return articles


def _fetch_news_sina(code: str, page_size: int = 20) -> list[dict]:
    """Sina Finance stock news API (backup source)."""
    prefix = _get_prefix(code)
    url = (
        f"https://vip.stock.finance.sina.com.cn/corp/view/"
        f"vCB_AllNewsStock.php?symbol={prefix}{code}&Page=1"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        ),
        "Referer": "https://finance.sina.com.cn/",
    }

    resp = _requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    resp.encoding = "gb2312"
    html = resp.text

    articles: list[dict] = []
    rows = _re.findall(
        r"(\d{4}-\d{2}-\d{2})\s*(?:&nbsp;)*(\d{2}:\d{2})\s*(?:&nbsp;)*"
        r"<a[^>]+href='([^']+)'[^>]*>([^<]+)</a>",
        html,
    )
    for date_str, time_str, link, title in rows[:page_size]:
        articles.append({
            "title": title.strip(),
            "content": "",
            "time": f"{date_str} {time_str}",
            "source": "新浪财经",
            "url": link,
        })
    return articles


_NEWS_DATE_RE = _re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def _parse_news_date(value) -> date | None:
    """Best-effort publication date for one news item, or None if unknown.

    Handles the shapes the two feeds actually send: a unix timestamp (CLS
    ``ctime``), ``YYYY-MM-DD[ HH:MM[:SS]]`` and ``YYYY/MM/DD``.  Timestamps are
    converted in the **market** timezone, not the host's, so an article that
    lands near midnight is filed on the same day the A-share reader would file
    it.

    Returning None means "cannot be placed in time".  Callers must treat that
    as *drop it* and never as *in range* — keeping an undated article is exactly
    how present-day news used to end up inside a historical window.
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=_MARKET_TZ).date()
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None

    # CLS serves ctime as a unix timestamp; the JSON decoder normally gives an
    # int, but a numeric string must not silently fall through to the date regex
    # (a 10-digit epoch would otherwise never match, and the item would be
    # dropped as undated even though its time was perfectly readable).
    if text.isdigit() and len(text) >= 10:
        try:
            return datetime.fromtimestamp(int(text), tz=_MARKET_TZ).date()
        except (OverflowError, OSError, ValueError):
            return None

    m = _NEWS_DATE_RE.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def get_news(
    ticker: Annotated[str, "A-stock code"],
    start_date: Annotated[str, "Start date yyyy-mm-dd"],
    end_date: Annotated[str, "End date yyyy-mm-dd"],
) -> str:
    """Get stock-specific news via East Money direct API (Sina as fallback)."""
    code = _normalize_ticker(ticker)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    articles: list[dict] = []
    source_label = ""

    try:
        articles = _fetch_news_eastmoney(code)
        source_label = "东方财富"
    except Exception as e:
        logger.warning("East Money news fetch failed for %s: %s", code, e)

    if not articles:
        try:
            articles = _fetch_news_sina(code)
            source_label = "新浪财经"
        except Exception as e:
            logger.warning("Sina news fetch failed for %s: %s", code, e)

    if not articles:
        return f"No news found for A-stock '{code}'"

    news_str = ""
    count = 0
    undated = 0
    for art in articles:
        pub_date = _parse_news_date(art.get("time", ""))
        if pub_date is None:
            # 发布时间读不出来就丢弃。"读不出来" 不等于 "在窗口内"——旧代码在这里
            # `pass` 掉异常后照常收录，于是今天的新闻会被写进历史窗口的报告里。
            undated += 1
            continue
        if pub_date < start_dt.date() or pub_date > end_dt.date():
            continue

        title = art["title"]
        content = art.get("content", "")
        source = art.get("source", source_label)
        link = art.get("url", "")

        news_str += f"### {title} (source: {source})\n"
        if content:
            snippet = content[:300] + "..." if len(content) > 300 else content
            news_str += f"{snippet}\n"
        if link and link != "nan":
            news_str += f"Link: {link}\n"
        news_str += "\n"
        count += 1

    if count == 0:
        parts = [
            f"No news found for A-stock '{code}' "
            f"between {start_date} and {end_date}."
        ]
        if undated:
            parts.append(
                f"{undated} article(s) were dropped because their publication "
                "date could not be read."
            )
        if _is_historical(end_date):
            # 这两个端点只提供"最新"文章，"查不到" 与 "当时没有" 是两回事。
            parts.append(
                "Note: the East Money / Sina news endpoints only serve the most "
                "recent articles, so this window cannot be reconstructed "
                "faithfully — read this as 'the source cannot look back', not "
                "as 'no news existed on those dates'."
            )
        return " ".join(parts)

    # 有内容返回时同样要说清被丢掉了什么——静默丢弃和多报一条一样糟：
    # 读者无从判断这份列表是不是完整。
    notes = []
    if undated:
        notes.append(
            f"> ⚠️ {undated} article(s) were dropped because their publication "
            "date could not be read."
        )
    if _is_historical(end_date):
        notes.append(
            "> ⚠️ The East Money / Sina news endpoints only serve the most recent "
            "articles; earlier items inside this window cannot be retrieved, so "
            "the list below may be incomplete."
        )

    header = f"## {code} (A-stock) News, from {start_date} to {end_date}:\n"
    blocks = [header]
    if notes:
        blocks.append("\n" + "\n".join(notes) + "\n")
    blocks.append("\n" + news_str)
    return "".join(blocks)


# ---- 8. get_global_news ----


def get_global_news(
    curr_date: Annotated[str, "Current date yyyy-mm-dd"],
    look_back_days: Annotated[int, "Days to look back"] = 7,
    limit: Annotated[int, "Max articles"] = 10,
) -> str:
    """Get China/global financial news via direct HTTP (CLS + Eastmoney).

    Point-in-time discipline: both feeds serve only the *current* wire, so a
    historical ``curr_date`` cannot be reconstructed.  Items are filtered to the
    requested window, anything whose publication date cannot be read is dropped,
    and the caller is told which of the two situations produced an empty result
    ("the source cannot look back" vs "the window really had no wire") instead
    of being shown today's news under a historical header.
    """
    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - relativedelta(days=look_back_days)
    start_date = start_dt.strftime("%Y-%m-%d")

    all_news: list[dict] = []

    # Source 1: CLS wire (财联社快讯) — direct HTTP
    try:
        cls_url = "https://www.cls.cn/nodeapi/telegraphList"
        cls_params = {"rn": str(limit), "page": "1"}
        cls_headers = {"User-Agent": _UA, "Referer": "https://www.cls.cn/"}
        r_cls = _requests.get(cls_url, params=cls_params, headers=cls_headers, timeout=10)
        d_cls = r_cls.json()
        for item in d_cls.get("data", {}).get("roll_data", []):
            title = item.get("title", "") or item.get("brief", "")
            content = item.get("content", "") or item.get("brief", "")
            ctime = item.get("ctime", "")
            # ctime is a unix timestamp; render it in market time so the display
            # matches the date the point-in-time filter compares against.
            pub_time = ""
            if ctime:
                try:
                    pub_time = datetime.fromtimestamp(
                        int(ctime), tz=_MARKET_TZ
                    ).strftime("%Y-%m-%d %H:%M")
                except (ValueError, TypeError, OSError):
                    pub_time = str(ctime)
            all_news.append({
                "title": title,
                "content": content,
                "time": pub_time,
                "pub_date": _parse_news_date(ctime),
                "source": "CLS Wire",
            })
    except Exception as e:
        logger.warning("CLS news fetch failed: %s", e)

    # Source 2: Eastmoney global (东财7x24资讯) — direct HTTP
    try:
        em_url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
        em_params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": str(limit),
            "req_trace": str(uuid.uuid4()),
        }
        em_headers = {"User-Agent": _UA, "Referer": "https://kuaixun.eastmoney.com/"}
        r_em = _em_get(em_url, params=em_params, headers=em_headers, timeout=10)
        d_em = r_em.json()
        for item in d_em.get("data", {}).get("fastNewsList", []):
            title = item.get("title", "")
            summary = item.get("summary", "")[:200]
            pub_time = item.get("showTime", "")
            all_news.append({
                "title": title,
                "content": summary,
                "time": pub_time,
                "pub_date": _parse_news_date(pub_time),
                "source": "Eastmoney Global",
            })
    except Exception as e:
        logger.warning("Eastmoney global news fetch failed: %s", e)

    if not all_news:
        return f"No global news found for {curr_date}"

    # Deduplicate by title
    seen: set[str] = set()
    unique: list[dict] = []
    for n in all_news:
        if n["title"] not in seen:
            seen.add(n["title"])
            unique.append(n)

    # --- Point-in-time filter ------------------------------------------------
    # 这两个源（财联社电报 / 东财 7×24）只提供"此刻最新"的快讯。原实现算出了
    # start_date 却**只把它打进标题字符串**，正文一条都不裁——于是复盘历史日期时，
    # 报告会出现今天的新闻，而标题写着 "from {start_date} to {curr_date}"。
    # 四个分析师（新闻/政策/游资/宏观）都消费这段文本，且从报告里完全看不出来。
    historical = _is_historical(curr_date)
    in_window: list[dict] = []
    out_of_window = 0
    undated = 0
    for n in unique:
        pub_date = n.get("pub_date")
        if pub_date is None:
            undated += 1
            continue
        if start_dt.date() <= pub_date <= curr_dt.date():
            in_window.append(n)
        else:
            out_of_window += 1

    header = f"## China & Global Market News, from {start_date} to {curr_date}:\n"

    if not in_window:
        detail = []
        if out_of_window:
            detail.append(f"{out_of_window} published outside the window")
        if undated:
            detail.append(f"{undated} with an unreadable publication date")
        why = f" ({'; '.join(detail)} dropped)" if detail else ""
        if historical:
            reason = (
                f"N/A: 该数据源只提供最新快讯，无法回溯 {start_date} ~ {curr_date} "
                f"的历史窗口{why}。不要把这理解为「那几天没有新闻」。"
            )
        else:
            reason = f"N/A: {start_date} ~ {curr_date} 窗口内没有快讯{why}。"
        return f"{header}\n{reason}"

    notes = []
    if historical:
        # 窗口内确实有条目，但源只返回最新 N 条，历史窗口注定不完整——必须说清楚。
        notes.append(
            f"> ⚠️ 数据源只返回**最新**快讯：以上条目已裁到 {start_date} ~ {curr_date}，"
            f"该窗口内更早的快讯无法取回（另有 {out_of_window} 条超出窗口被丢弃），"
            f"因此本列表**可能不完整**，不得当作当日的全部资讯。"
        )
    if undated:
        notes.append(f"> ⚠️ {undated} 条快讯发布时间无法识别，已丢弃。")

    news_str = ""
    for n in in_window[:limit]:
        news_str += f"### {n['title']} (source: {n['source']})\n"
        if n.get("content"):
            snippet = (
                n["content"][:300] + "..."
                if len(n["content"]) > 300
                else n["content"]
            )
            news_str += f"{snippet}\n"
        news_str += "\n"

    blocks = [header]
    if notes:
        blocks.append("\n" + "\n".join(notes) + "\n")
    blocks.append("\n" + news_str)
    return "".join(blocks)


# ---- 9. get_insider_transactions ----


def get_insider_transactions(
    ticker: Annotated[str, "A-stock code"],
) -> str:
    """Get shareholder/insider activity via mootdx F10.

    Note: A-stock insider transaction data differs from US markets.
    Uses mootdx F10 shareholder research as the closest equivalent.
    """
    code = _normalize_ticker(ticker)

    try:
        text = _mootdx_call("F10", symbol=code, name="股东研究")

        if not text or not text.strip():
            return f"No insider/shareholder data found for A-stock '{code}'"

        header = f"# Shareholder Research for {code} (A-stock)\n"
        header += "# Note: A-stock equivalent of insider transactions\n"
        header += "# Data source: mootdx F10\n"
        header += (
            f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )

        import re

        sec4_hits = list(re.finditer(r"\r?\n【4\.股东变化】\r?\n", text))
        if sec4_hits:
            sec4_pos = sec4_hits[-1].start()
            before_sec4 = text[:sec4_pos]
            sec4_text = text[sec4_pos:]
            cut_at = 2000
            if len(sec4_text) > cut_at:
                sec4_text = (
                    sec4_text[:cut_at]
                    + "\n\n(... older shareholder history omitted, "
                    f"{len(text) - sec4_pos - cut_at} chars truncated ...)"
                )
            text = before_sec4 + sec4_text

        return header + text

    except Exception as e:
        return f"Error retrieving insider/shareholder data for {code}: {str(e)}"


# ---- 10. get_profit_forecast ----


def get_profit_forecast(
    ticker: Annotated[str, "A-stock code"],
    curr_date: Annotated[str, "current date — 用于判断是否在复盘历史"] = None,
) -> str:
    """Get consensus EPS forecasts with forward valuation (同花顺 direct HTTP)."""
    code = _normalize_ticker(ticker)

    try:
        df = _ths_eps_forecast(code)

        if df is None or df.empty:
            return f"No analyst coverage found for A-stock '{code}'"

        lines = [
            f"# Consensus EPS Forecast for {code} (A-stock)",
            f"# Source: 同花顺 analyst consensus (direct HTTP)",
            f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        # 一致预期是"当前"的分析师预测，没有历史时点版本。同上，必须明说。
        if _is_historical(curr_date):
            lines.insert(0, _snapshot_notice(curr_date, "分析师一致预期"))

        eps_by_year = {}
        for _, row in df.iterrows():
            year = str(row.iloc[0]) if len(row) > 0 else ""
            count_val = row.iloc[1] if len(row) > 1 else 0
            mean_eps_val = row.iloc[3] if len(row) > 3 else 0
            min_eps_val = row.iloc[2] if len(row) > 2 else "N/A"
            max_eps_val = row.iloc[4] if len(row) > 4 else "N/A"
            try:
                count = int(count_val)
            except (ValueError, TypeError):
                count = 0
            try:
                mean_eps = float(mean_eps_val)
            except (ValueError, TypeError):
                mean_eps = 0
            lines.append(
                f"FY{year}: EPS={mean_eps} (range {min_eps_val}~{max_eps_val}), "
                f"analysts={count}"
            )
            if count < 3:
                lines.append("  Warning: low coverage (<3 analysts)")
            eps_by_year[year] = mean_eps

        # Forward valuation
        try:
            tq = _tencent_quote([code])
            if code in tq:
                price = tq[code]["price"]
                pe_ttm = tq[code]["pe_ttm"]
                lines.append(f"\nCurrent: price={price}, PE(TTM)={pe_ttm}")

                years_sorted = sorted(eps_by_year.keys())
                if years_sorted and eps_by_year.get(years_sorted[0], 0) > 0:
                    eps_cur = eps_by_year[years_sorted[0]]
                    fwd_pe = price / eps_cur
                    lines.append(
                        f"Forward PE (FY{years_sorted[0]}): {fwd_pe:.1f}x"
                    )
                    if (
                        len(years_sorted) >= 2
                        and eps_by_year.get(years_sorted[1], 0) > 0
                    ):
                        eps_next = eps_by_year[years_sorted[1]]
                        cagr = eps_next / eps_cur - 1
                        if cagr > 0:
                            peg = fwd_pe / (cagr * 100)
                            lines.append(
                                f"PEG: {peg:.2f} (CAGR={cagr * 100:.0f}%)"
                            )
                            if fwd_pe > 30:
                                digest = math.log(fwd_pe / 30) / math.log(
                                    1 + cagr
                                )
                                lines.append(
                                    f"PE Digestion to 30x: {digest:.1f} years"
                                )
                        else:
                            lines.append(
                                f"EPS declining ({cagr * 100:.0f}%), "
                                f"PEG not applicable"
                            )
        except Exception as e:
            logger.warning("Forward PE calc failed for %s: %s", code, e)

        return "\n".join(lines)

    except Exception as e:
        return f"Error retrieving profit forecast for {code}: {str(e)}"


# ---- 11. get_hot_stocks ----


def get_hot_stocks(
    curr_date: Annotated[str, "Date YYYY-MM-DD, empty string for today"] = "",
) -> str:
    """Get strong stocks with topic attribution from 同花顺 editorial team.

    Returns stocks that hit limit-up with human-curated reason tags
    explaining WHY they surged (e.g. '算力租赁+AI政务').
    """
    import requests

    if not curr_date or curr_date.strip() == "":
        # 用市场日期而不是主机日期：主机在别的时区时会请求到不存在（或还没到）
        # 的那一天，接口返回空，用户看到的是"当日没有强势股"。
        curr_date = _market_today().strftime("%Y-%m-%d")

    try:
        url = (
            f"http://zx.10jqka.com.cn/event/api/getharden/"
            f"date/{curr_date}/orderby/date/orderway/desc/charset/GBK/"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "Chrome/117.0.0.0 Safari/537.36"
            )
        }
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()

        if data.get("errocode", 0) != 0:
            return f"同花顺 API error: {data.get('errormsg', 'unknown')}"

        rows = data.get("data") or []
        if not rows:
            return (
                f"No hot stocks data for {curr_date} "
                f"(may be non-trading day or data not yet available)"
            )

        lines = [
            f"# Hot Stocks with Topic Attribution ({curr_date})",
            f"# Source: 同花顺 editorial (human-curated reason tags)",
            f"# Total: {len(rows)} stocks",
            "",
        ]

        from collections import Counter

        all_tags: list[str] = []

        for row in rows:
            code = row.get("code", "")
            name = row.get("name", "")
            reason = row.get("reason", "")
            zhangfu = row.get("zhangfu", "")
            huanshou = row.get("huanshou", "")
            chengjiaoe = row.get("chengjiaoe", "")
            dde = row.get("ddejingliang", "")

            lines.append(
                f"{code} {name}: +{zhangfu}% "
                f"换手{huanshou}% 成交额{chengjiaoe} "
                f"大单净量{dde} | {reason}"
            )

            if reason:
                tags = [t.strip() for t in str(reason).split("+") if t.strip()]
                all_tags.extend(tags)

        if all_tags:
            cnt = Counter(all_tags)
            lines.append(f"\n## Theme Frequency (top 15)")
            for tag, n in cnt.most_common(15):
                lines.append(f"  {tag}: {n} stocks")

        return "\n".join(lines)

    except Exception as e:
        return f"Error fetching hot stocks for {curr_date}: {str(e)}"


# ---- 12. get_northbound_flow ----


def _northbound_cache_path() -> str:
    """Path to local CSV cache for northbound daily close snapshots."""
    from .config import get_config

    config = get_config()
    cache_dir = config.get(
        "data_cache_dir", os.path.expanduser("~/.marvel/cache")
    )
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "northbound_daily.csv")


def _save_northbound_snapshot(date_str: str, hgt: float, sgt: float) -> None:
    """Append today's northbound close to local CSV cache (dedup by date).

    整份重写（去重后按日期排序），因此必须原子落盘：这份缓存是在**累积历史**，
    半截文件会把之前攒下的所有交易日一起丢掉，而不是只丢今天这一行。
    """
    import csv
    import io

    path = _northbound_cache_path()
    existing: dict[str, tuple[str, str]] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 3:
                    existing[row[0]] = (row[1], row[2])
    existing[date_str] = (f"{hgt:.2f}", f"{sgt:.2f}")
    sorted_dates = sorted(existing.keys())
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "hgt", "sgt"])
    for d in sorted_dates:
        writer.writerow([d, existing[d][0], existing[d][1]])
    _atomic_write_text(path, buffer.getvalue(), newline="")


def _load_northbound_history(n: int = 20) -> list[tuple[str, float, float]]:
    """Load last N days of northbound close data from local cache."""
    import csv

    path = _northbound_cache_path()
    if not os.path.exists(path):
        return []
    rows: list[tuple[str, float, float]] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) >= 3:
                try:
                    rows.append((row[0], float(row[1]), float(row[2])))
                except ValueError:
                    continue
    return rows[-n:]


def get_northbound_flow(
    curr_date: Annotated[str, "Date YYYY-MM-DD"],
    include_history: Annotated[
        bool, "Include historical daily data (last 20 trading days)"
    ] = False,
) -> str:
    """Get northbound capital flow (沪深股通) from 同花顺 hsgtApi.

    Realtime: minute-level cumulative net buying for HGT(沪股通) + SGT(深股通).
    History: self-cached daily close snapshots (upstream APIs stopped updating
    northbound history since 2024-08).
    """
    import requests

    hsgt_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Chrome/117.0.0.0 Safari/537.36"
        ),
        "Host": "data.hexin.cn",
        "Referer": "https://data.hexin.cn/",
    }

    lines = [
        f"# Northbound Capital Flow ({curr_date})",
        "# Source: 同花顺 hsgtApi (沪深股通) + local cache",
        "",
    ]

    hgt_close = 0.0
    sgt_close = 0.0
    got_realtime = False

    historical = _is_historical(curr_date)
    if historical:
        # 分钟级北向只有"今天"的。复盘历史日期时它整段都是未来数据，直接不取——
        # 与 get_fund_flow 的处理保持一致（那边早就这么做了，这里漏了）。
        lines.append(
            f"（分析日期 {curr_date} 早于今天，已略去实时分钟北向——"
            f"那是今天的盘中数据，不是 {curr_date} 当天的。）\n"
        )

    try:
        url_rt = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
        d = {}
        if not historical:
            r = requests.get(url_rt, headers=hsgt_headers, timeout=10)
            d = r.json()

        times = d.get("time", [])
        hgt = d.get("hgt", [])
        sgt = d.get("sgt", [])

        if historical:
            pass          # 略去原因已在上面写明
        elif times:
            lines.append("## Realtime (cumulative net buying, 亿元)")
            n = len(times)
            start_idx = max(0, n - 10)
            for i in range(start_idx, n):
                t = times[i]
                h = hgt[i] if i < len(hgt) else "N/A"
                s = sgt[i] if i < len(sgt) else "N/A"
                lines.append(f"  {t}: HGT={h} SGT={s}")

            # 长度必须一致才敢把末元素当成"收盘累计净买入"。上游 hgt/sgt 与 time
            # 的长度并不总是相同（上面的打印段已经防御了这一点，取值段却没有），
            # 而两条不同口径、不同长度的序列相加会得出方向与量级都失真的"净流入"，
            # 还会被写进本地缓存、均进 N 日均值——一路污染后续所有判断。
            if not (len(hgt) == len(sgt) == n):
                lines.append(
                    f"\n⚠️ 沪股通/深股通序列长度不一致（time={n}, HGT={len(hgt)}, "
                    f"SGT={len(sgt)}），本次不输出净流入结论，也不写入本地缓存。"
                )
            elif not hgt or not sgt:
                lines.append("\n⚠️ 北向序列为空，本次不输出净流入结论。")
            else:
                try:
                    hgt_close = float(hgt[-1])
                    sgt_close = float(sgt[-1])
                except (TypeError, ValueError) as conv_err:
                    lines.append(
                        f"\n⚠️ 北向收盘值无法解析（{conv_err}），"
                        f"本次不输出净流入结论。"
                    )
                else:
                    total = hgt_close + sgt_close
                    lines.append(
                        f"\nClose: HGT(沪股通)={hgt_close:.2f}亿 "
                        f"SGT(深股通)={sgt_close:.2f}亿 "
                        f"Total={total:.2f}亿"
                    )
                    if total > 0:
                        lines.append("Signal: Net northbound INFLOW (bullish)")
                    elif total < 0:
                        lines.append("Signal: Net northbound OUTFLOW (bearish)")
                    got_realtime = True
        else:
            lines.append("No realtime data (non-trading hours or holiday)")

        if got_realtime:
            # 快照键用市场日期：主机在别的时区时不能让一份"今天的"快照落到别的日子。
            _save_northbound_snapshot(
                _market_today().strftime("%Y-%m-%d"), hgt_close, sgt_close
            )

        if include_history:
            history = _load_northbound_history(20)
            # 历史同样要裁到分析日：本地缓存里存的是"跑分析那天"的收盘快照，
            # 复盘历史日期时不裁就会把分析日之后的收盘值列进趋势里。
            history = [row for row in history if str(row[0]) <= curr_date]
            if history:
                lines.append("\n## Historical Daily Close (local cache, 亿元)")
                lines.append("Date       | HGT(沪股通) | SGT(深股通) | Total")
                for date, h, s in history:
                    lines.append(f"  {date}: HGT={h:.2f} SGT={s:.2f} Total={h + s:.2f}")
                avg_total = sum(h + s for _, h, s in history) / len(history)
                lines.append(
                    f"\n{len(history)}-day avg net flow: {avg_total:.2f}亿"
                )
                if got_realtime:
                    today_total = hgt_close + sgt_close
                    diff = today_total - avg_total
                    lines.append(
                        f"Today vs avg: {'+' if diff >= 0 else ''}{diff:.2f}亿 "
                        f"({'above' if diff >= 0 else 'below'} average)"
                    )
            else:
                lines.append(
                    "\n## Historical Daily: No cached data yet. "
                    "History accumulates automatically with each call."
                )

        return "\n".join(lines)

    except Exception as e:
        return f"Error fetching northbound flow: {str(e)}"


# ---------------------------------------------------------------------------
# Baidu PAE (百度股市通) helpers
# ---------------------------------------------------------------------------

_BAIDU_PAE_HEADERS = {
    "Host": "finance.pae.baidu.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) "
        "Gecko/20100101 Firefox/110.0"
    ),
    "Accept": "application/vnd.finance-web.v1+json",
    "Origin": "https://gushitong.baidu.com",
    "Referer": "https://gushitong.baidu.com/",
}


# ---- 13. get_concept_blocks ----


def get_concept_blocks(
    ticker: Annotated[str, "A-stock code (e.g. 688017)"],
) -> str:
    """Get concept/sector/region blocks that a stock belongs to (百度股市通).

    Returns industry classification (申万), concept themes, and region.
    Each block includes current day's change percentage.
    """
    import requests

    code = _normalize_ticker(ticker)

    try:
        url = (
            "https://finance.pae.baidu.com/api/getrelatedblock"
            f'?stock=[{{"code":"{code}","market":"ab","type":"stock"}}]'
            "&finClientType=pc"
        )
        r = requests.get(url, headers=_BAIDU_PAE_HEADERS, timeout=10)
        d = r.json()

        if str(d.get("ResultCode", -1)) != "0":
            return (
                f"Baidu PAE error: ResultCode={d.get('ResultCode')} "
                f"{d.get('ResultMsg', '')}"
            )

        result = d.get("Result", {})
        categories = result.get(code, [])
        if not categories:
            return f"No concept/block data for {code}"

        lines = [
            f"# Concept & Sector Blocks for {code} (A-stock)",
            f"# Source: 百度股市通 (Baidu PAE)",
            f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]

        concept_names: list[str] = []

        for cat in categories:
            cat_name = cat.get("name", "")
            items = cat.get("list", [])
            if not items:
                continue
            lines.append(f"## {cat_name}")
            for item in items:
                name = item.get("name", "")
                ratio = item.get("ratio", "")
                desc = item.get("describe", "")
                suffix = f" ({desc})" if desc else ""
                lines.append(f"  {name}{suffix}: {ratio}")
                if cat_name == "概念":
                    concept_names.append(name)

        if concept_names:
            lines.append(f"\nConcept tags: {' / '.join(concept_names)}")

        return "\n".join(lines)

    except Exception as e:
        return f"Error fetching concept blocks for {code}: {str(e)}"


# ---- 14. get_fund_flow ----


def get_fund_flow(
    ticker: Annotated[str, "A-stock code"],
    curr_date: Annotated[str, "Date YYYY-MM-DD"],
    include_history: Annotated[
        bool, "Include historical daily fund flow (last 20 days)"
    ] = True,
) -> str:
    """Get individual stock fund flow from 东财 push2.

    Realtime: minute-level main/large/medium/small/super order net inflow.
    History: daily net inflow for 20 trading days (push2his).

    V0.2.7: replaced 百度 PAE (fundflow/fundsortlist, offline since 2026-05)
    with 东财 push2 fund flow API.
    """
    code = _normalize_ticker(ticker)
    secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
    lines = [
        f"# Fund Flow for {code} (A-stock)",
        f"# Source: 东财 push2 (Eastmoney)",
        f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    historical = _is_historical(curr_date)
    if historical:
        # 分钟级资金流只有"今天"的，复盘历史日期时整段都是未来数据，直接不取。
        lines.append(
            f"（分析日期 {curr_date} 早于今天，已略去实时分钟资金流——"
            f"那是今天的盘中数据，不是 {curr_date} 当天的。）\n"
        )

    try:
        # Realtime minute-level fund flow
        url_rt = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
        params_rt = {
            "secid": secid, "klt": 1,
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
        }
        klines = []
        if not historical:
            r = _em_get(url_rt, params=params_rt, timeout=10)
            d = r.json()
            klines = d.get("data", {}).get("klines", [])

        if klines:
            lines.append(
                "## Realtime Minute Flow "
                "(主力/小单/中单/大单/超大单 净流入, 元)"
            )
            for line in klines[-10:]:
                parts = line.split(",")
                if len(parts) >= 6:
                    lines.append(
                        f"  {parts[0]}: "
                        f"主力={float(parts[1])/1e4:.0f}万 "
                        f"大单={float(parts[4])/1e4:.0f}万 "
                        f"超大单={float(parts[5])/1e4:.0f}万"
                    )

            last_parts = klines[-1].split(",")
            if len(last_parts) >= 2:
                main_net = float(last_parts[1])
                lines.append(
                    f"\nClose: 主力净流入={main_net/1e4:.0f}万元"
                )
                if main_net > 0:
                    lines.append(
                        "Signal: Net main force INFLOW (bullish)"
                    )
                elif main_net < 0:
                    lines.append(
                        "Signal: Net main force OUTFLOW (bearish)"
                    )
        else:
            lines.append(
                "No realtime fund flow (non-trading hours or holiday)"
            )

        # Historical daily fund flow (push2his)
        if include_history:
            url_hist = (
                "https://push2his.eastmoney.com"
                "/api/qt/stock/fflow/daykline/get"
            )
            # 接口返回的是"从今天回溯 lmt 个交易日"，没有 end_date 参数。复盘一个
            # 较早的日期时，若仍只要 20 天，过滤后会**一行不剩**——把"数据不对"
            # 变成"没有数据"，比不过滤更糟。按分析日与今天的间隔把窗口放大到能
            # 覆盖到那一段（上限 500，够回溯约两年）。
            hist_limit = 20
            if historical:
                gap_days = (_market_today() - datetime.strptime(
                    str(curr_date)[:10], "%Y-%m-%d").date()).days
                # 日历日 → 交易日约 ×0.7，再多留 20 天余量
                hist_limit = min(500, 20 + int(gap_days * 0.7) + 20)
            params_hist = {
                "secid": secid, "lmt": hist_limit, "klt": 101,
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
            }
            rh = _em_get(url_hist, params=params_hist, timeout=10)
            dh = rh.json()
            hist_klines = dh.get("data", {}).get("klines", [])

            # 逐行按分析日截断：接口返回的是"从今天回溯 20 个交易日"，
            # 在历史日期上直接打印等于把未来的资金流喂给模型（未来函数）。
            if historical:
                cutoff = str(curr_date)[:10]
                hist_klines = [
                    k for k in hist_klines if k.split(",")[0][:10] <= cutoff
                ]
                # 窗口是为了"够回溯到分析日"才放大的，过滤完要裁回承诺的 20 个交易日。
                # 不裁的话，复盘 90 天前会返回约 40 行——既改变了请求的趋势窗口，
                # 又把每次情绪工具的返回体撑大一倍。
                hist_klines = hist_klines[-20:]

            if historical and not hist_klines:
                # 说清楚是"这个日期取不到"，而不是让正文里凭空少一段
                lines.append(
                    f"\n## Historical Daily Fund Flow\n"
                    f"（{str(curr_date)[:10]} 及之前的资金流未能取到：该接口只提供"
                    f"从今天回溯的窗口，分析日过早时可能已超出可回溯范围。）"
                )
            elif hist_klines:
                lines.append(
                    f"\n## Historical Daily Fund Flow "
                    f"(last {len(hist_klines)} trading days"
                    + (f", 截至 {str(curr_date)[:10]}" if historical else "")
                    + ")"
                )
                lines.append(
                    "Date | 主力净流入(万) | 大单(万) "
                    "| 中单(万) | 小单(万) | 超大单(万)"
                )
                for line in hist_klines:
                    parts = line.split(",")
                    if len(parts) >= 6:
                        lines.append(
                            f"  {parts[0]} "
                            f"| main={float(parts[1])/1e4:.0f} "
                            f"| large={float(parts[4])/1e4:.0f} "
                            f"| mid={float(parts[3])/1e4:.0f} "
                            f"| small={float(parts[2])/1e4:.0f} "
                            f"| super={float(parts[5])/1e4:.0f}"
                        )

        return "\n".join(lines)

    except Exception as e:
        return f"Error fetching fund flow for {code}: {str(e)}"


# ---------------------------------------------------------------------------
# 15. Dragon Tiger Board (龙虎榜)
# ---------------------------------------------------------------------------

def get_dragon_tiger_board(
    ticker: str,
    trade_date: str,
    look_back_days: int = 30,
) -> str:
    """Get dragon-tiger board (龙虎榜) appearances and seat details.

    Args:
        ticker: 6-digit A-share code, e.g. '000858'
        trade_date: YYYY-MM-DD
        look_back_days: how many days back to search (default 30)

    Returns:
        Formatted text with LHB appearances, top buyer/seller seats,
        and institutional activity.
    """
    code = _normalize_ticker(ticker)
    end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
    start_dt = end_dt - pd.Timedelta(days=look_back_days)
    start_date_str = start_dt.strftime("%Y-%m-%d")
    lines = [f"# 龙虎榜数据 | {code} | {trade_date} (近{look_back_days}日)"]

    # 1. 上榜记录 — eastmoney datacenter direct HTTP
    try:
        data = _eastmoney_datacenter(
            "RPT_DAILYBILLBOARD_DETAILSNEW",
            filter_str=(
                f"(TRADE_DATE>='{start_date_str}')"
                f"(TRADE_DATE<='{trade_date}')"
                f"(SECURITY_CODE=\"{code}\")"
            ),
            page_size=50,
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
        if not data:
            lines.append(f"\n近{look_back_days}日未上龙虎榜。")
        else:
            lines.append(f"\n## 上榜记录 ({len(data)} 次)")
            lines.append("日期 | 原因 | 净买入(万) | 换手率")
            for row in data:
                net_buy = round((row.get("BILLBOARD_NET_AMT") or 0) / 10000, 1)
                turnover = round(float(row.get("TURNOVERRATE") or 0), 2)
                lines.append(
                    f"  {str(row.get('TRADE_DATE', ''))[:10]} "
                    f"| {row.get('EXPLANATION', '')} "
                    f"| {net_buy:.0f} "
                    f"| {turnover:.2f}%"
                )
    except Exception as e:
        lines.append(f"龙虎榜列表查询失败: {e}")

    # 2. 最近上榜的买卖席位 — eastmoney datacenter direct HTTP
    try:
        if data:
            latest_date = str(data[0].get("TRADE_DATE", ""))[:10]
            lines.append(f"\n## 最近上榜席位明细 ({latest_date})")

            # 买入席位
            buy_data = _eastmoney_datacenter(
                "RPT_BILLBOARD_DAILYDETAILSBUY",
                filter_str=f"(TRADE_DATE='{latest_date}')(SECURITY_CODE=\"{code}\")",
                page_size=10,
                sort_columns="BUY",
                sort_types="-1",
            )
            if buy_data:
                lines.append("\n### 买入席位 TOP5")
                lines.append("营业部 | 买入(万) | 卖出(万) | 净额(万)")
                for row in buy_data[:5]:
                    buy_amt = round((row.get("BUY") or 0) / 10000, 1)
                    sell_amt = round((row.get("SELL") or 0) / 10000, 1)
                    net = round((row.get("NET") or 0) / 10000, 1)
                    lines.append(
                        f"  {row.get('OPERATEDEPT_NAME', '')} "
                        f"| {buy_amt:.0f} | {sell_amt:.0f} | {net:.0f}"
                    )

            # 卖出席位
            sell_data = _eastmoney_datacenter(
                "RPT_BILLBOARD_DAILYDETAILSSELL",
                filter_str=f"(TRADE_DATE='{latest_date}')(SECURITY_CODE=\"{code}\")",
                page_size=10,
                sort_columns="SELL",
                sort_types="-1",
            )
            if sell_data:
                lines.append("\n### 卖出席位 TOP5")
                lines.append("营业部 | 买入(万) | 卖出(万) | 净额(万)")
                for row in sell_data[:5]:
                    buy_amt = round((row.get("BUY") or 0) / 10000, 1)
                    sell_amt = round((row.get("SELL") or 0) / 10000, 1)
                    net = round((row.get("NET") or 0) / 10000, 1)
                    lines.append(
                        f"  {row.get('OPERATEDEPT_NAME', '')} "
                        f"| {buy_amt:.0f} | {sell_amt:.0f} | {net:.0f}"
                    )
    except Exception:
        pass

    # 3. 机构动向 — 从买卖席位明细筛选机构专用席位 (OPERATEDEPT_CODE="0")
    try:
        inst_buy = 0.0
        inst_sell = 0.0
        for detail, side in [(buy_data, "buy"), (sell_data, "sell")]:
            for row in (detail or []):
                if str(row.get("OPERATEDEPT_CODE", "")) == "0":
                    if side == "buy":
                        inst_buy += (row.get("BUY") or 0)
                    else:
                        inst_sell += (row.get("SELL") or 0)
        if inst_buy > 0 or inst_sell > 0:
            lines.append("\n## 机构动向")
            lines.append(
                f"  机构买入 {inst_buy/1e4:.0f} 万 "
                f"| 卖出 {inst_sell/1e4:.0f} 万 "
                f"| 净额 {(inst_buy - inst_sell)/1e4:.0f} 万"
            )
    except Exception:
        pass

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 16. Lockup Expiry Calendar (限售解禁日历)
# ---------------------------------------------------------------------------

def get_lockup_expiry(
    ticker: str,
    trade_date: str,
    forward_days: int = 90,
) -> str:
    """Get lockup expiry schedule for a stock.

    Args:
        ticker: 6-digit A-share code
        trade_date: YYYY-MM-DD
        forward_days: how many days forward to check (default 90)

    Returns:
        Formatted text with historical unlock records and upcoming
        expiry calendar with impact metrics.
    """
    code = _normalize_ticker(ticker)
    lines = [f"# 限售解禁日历 | {code} | {trade_date}"]

    # 1. 历史解禁记录 — eastmoney datacenter direct HTTP
    try:
        history_data = _eastmoney_datacenter(
            "RPT_LIFT_STAGE",
            # 上界必须加：只按 SECURITY_CODE 过滤时，复盘历史日期会把**分析日之后**
            # 才发生的解禁批次也算进"历史解禁记录"（同一批还会重复出现在下面的
            # "未来待解禁"里），等于把未来的筹码事件当成已知事实。
            filter_str=(
                f"(SECURITY_CODE=\"{code}\")"
                f"(FREE_DATE<='{trade_date}')"
            ),
            page_size=15,
            sort_columns="FREE_DATE",
            sort_types="-1",
        )
        if history_data:
            lines.append(f"\n## 截至 {trade_date} 的解禁记录 (共 {len(history_data)} 批)")
            lines.append("解禁时间 | 类型 | 解禁数量 | 占比")
            for row in history_data:
                lines.append(
                    f"  {str(row.get('FREE_DATE', ''))[:10]} "
                    f"| {row.get('LIMITED_STOCK_TYPE', '')} "
                    f"| {row.get('FREE_SHARES_NUM', '')} "
                    f"| {row.get('FREE_RATIO', '')}"
                )
        else:
            lines.append("\n无历史解禁记录。")
    except Exception as e:
        lines.append(f"个股解禁查询失败: {e}")

    # 2. 未来待解禁 — eastmoney datacenter direct HTTP
    try:
        end_dt = datetime.strptime(trade_date, "%Y-%m-%d") + pd.Timedelta(
            days=forward_days
        )
        end_str = end_dt.strftime("%Y-%m-%d")
        upcoming_data = _eastmoney_datacenter(
            "RPT_LIFT_STAGE",
            filter_str=(
                f"(SECURITY_CODE=\"{code}\")"
                f"(FREE_DATE>='{trade_date}')"
                f"(FREE_DATE<='{end_str}')"
            ),
            page_size=20,
            sort_columns="FREE_DATE",
            sort_types="1",
        )
        if upcoming_data:
            lines.append(f"\n## 未来 {forward_days} 天待解禁")
            for row in upcoming_data:
                lines.append(
                    f"  {str(row.get('FREE_DATE', ''))[:10]} "
                    f"| {row.get('LIMITED_STOCK_TYPE', '')} "
                    f"| 数量 {row.get('FREE_SHARES_NUM', '')} "
                    f"| 占比 {row.get('FREE_RATIO', '')}"
                )
        else:
            lines.append(f"\n未来 {forward_days} 天无待解禁。")
    except Exception as e:
        lines.append(f"解禁日历查询失败: {e}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 17. Industry Comparison (行业横向对比)
# ---------------------------------------------------------------------------

def _to_float(value) -> float | None:
    """Parse a push2 numeric field, or None when it is not a number.

    东财在停牌/无数据的行里把 ``f3`` 之类的字段写成 ``"-"``（字符串），直接
    ``float()`` 会抛，直接当 0 又会把它排进榜单中间冒充"不涨不跌"。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().rstrip("%")
    if not text or text in {"-", "--", "null", "None"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _industry_rank_row(rank: int, row: dict) -> str:
    return (
        f"  {rank}. {row['name']} "
        f"| {row['change_pct']:+.2f}% "
        f"| {row['up']} "
        f"| {row['down']} "
        f"| {row['leader']}"
    )


def get_industry_comparison(
    ticker: str,
    trade_date: str,
    top_n: int = 20,
) -> str:
    """Get industry sector performance comparison.

    Args:
        ticker: 6-digit A-share code (used to identify relevant sector)
        trade_date: YYYY-MM-DD
        top_n: number of top/bottom industries to show (default 20)

    Returns:
        Formatted text with sector performance ranking, highlighting
        the sector the target stock belongs to.
    """
    code = _normalize_ticker(ticker)
    lines = [f"# 行业横向对比 | {code} | {trade_date}"]
    # 板块排名来自东财 push2 的**当前**快照，没有历史时点版本。trade_date 原先只
    # 出现在标题里，正文却是实时数据——复盘时等于把今天的板块涨跌当成分析日的事实。
    if _is_historical(trade_date):
        lines.insert(1, _snapshot_notice(trade_date, "行业板块排名"))

    # 东财 push2 行业板块排名 (direct HTTP, replaces 同花顺 which has 401)
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": "100",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fs": "m:90+t:2",
            "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
        }
        r = _em_get(url, params=params, timeout=15)
        d = r.json()
        items = d.get("data", {}).get("diff", [])

        if items:
            # 东财按 po=1 返回的是**降序**列表，但"top/bottom N"不能靠切片实现：
            # 原实现打到 2*top_n 就 break，拿到的其实只有涨幅前 40 个行业，跌幅榜
            # 一个都没有，而标题写着 "showing top/bottom 20"（pz=100 本来就够取回
            # 全部约 86 个行业）。这里本地解析 + 排序，头尾各取 N，标题与内容一致。
            parsed: list[dict] = []
            for item in items:
                change_pct = _to_float(item.get("f3"))
                if change_pct is None:
                    # 停牌/无成交的行业没有涨跌幅，不该混进任何一张榜单。
                    continue
                parsed.append(
                    {
                        "name": item.get("f14", ""),
                        "change_pct": change_pct,
                        "up": item.get("f104", 0),
                        "down": item.get("f105", 0),
                        "leader": item.get("f140", ""),
                    }
                )
            parsed.sort(key=lambda row: row["change_pct"], reverse=True)

            if parsed:
                n = max(1, min(int(top_n), len(parsed)))
                top = parsed[:n]
                # 只有在自己不重叠时才给"跌幅榜"，避免行业数少于 2N 时同一行出现两次。
                bottom = parsed[len(parsed) - n :] if len(parsed) - n >= n else []

                lines.append(
                    f"\n## 行业涨跌幅榜 (东财 {len(parsed)} 个行业，按涨跌幅排序)"
                )
                lines.append("排名 | 行业 | 涨跌幅 | 上涨 | 下跌 | 领涨股")
                for rank, row in enumerate(top, start=1):
                    lines.append(_industry_rank_row(rank, row))
                if bottom:
                    lines.append(f"  ... (共 {len(parsed)} 个行业)")
                    for offset, row in enumerate(bottom):
                        rank = len(parsed) - len(bottom) + offset + 1
                        lines.append(_industry_rank_row(rank, row))
                if not bottom:
                    lines.append(f"  (仅 {len(parsed)} 个行业，未另列跌幅榜)")
            else:
                lines.append("行业数据获取成功，但没有一个行业带有效涨跌幅。")
        else:
            lines.append("行业数据获取为空。")
    except Exception as e:
        lines.append(f"行业对比查询失败: {e}")

    return "\n".join(lines)
