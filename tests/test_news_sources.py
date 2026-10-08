"""The news analyst must get enough material to be worth its tokens.

Before this, `get_news` called the East Money search API as its primary source and
silently fell back to Sina when it returned nothing. It *always* returned nothing
— every parameter variant answers with a `passportWeb` stub and no articles
(verified live 2026-09-27) — so the only feed that ever reached the prompt was
Sina's headline list, truncated to 20 rows, with **no body text at all**.

Now three feeds are queried: company announcements (公告), broker research (研报)
and the press list (新闻), de-duplicated across sources and ranked.
"""

from __future__ import annotations

import pytest

from marvel.dataflows import a_stock


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTextNormalisation:
    def test_html_is_stripped(self):
        assert a_stock._strip_html("<em>亨通光电</em>&nbsp;公告") == "亨通光电 公告"

    def test_entities_and_full_width_spaces_are_cleaned(self):
        assert a_stock._strip_html("A&amp;B\u3000C") == "A&B C"

    def test_empty_input_is_safe(self):
        assert a_stock._strip_html("") == ""
        assert a_stock._strip_html(None) == ""

    @pytest.mark.parametrize(
        "left,right",
        [
            # the two real shapes the same announcement arrives in
            (
                "江苏亨通光电股份有限公司 关于控股股东部分股权解除质押公告",
                "亨通光电:亨通光电关于控股股东部分股权解除质押公告",
            ),
            (
                "亨通光电(600487)关于向特定对象发行A股股票的公告",
                "亨通光电:关于向特定对象发行A股股票的公告",
            ),
        ],
    )
    def test_the_same_story_from_two_sources_shares_a_key(self, left, right):
        assert a_stock._news_dedup_key(left) == a_stock._news_dedup_key(right)

    def test_different_stories_do_not_collide(self):
        a = a_stock._news_dedup_key("亨通光电:关于控股股东部分股权解除质押公告")
        b = a_stock._news_dedup_key("亨通光电:关于回购注销部分限制性股票的公告")
        assert a != b


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.mark.unit
class TestSourceAdapters:
    def test_announcements_are_parsed_with_links(self, monkeypatch):
        monkeypatch.setattr(
            a_stock, "_em_get",
            lambda *a, **k: _FakeResponse({
                "data": {"list": [
                    {
                        "title": "<em>亨通光电</em>:关于回购的公告",
                        "notice_date": "2026-09-25 00:00:00",
                        "art_code": "AN123",
                    },
                ]}
            }),
        )

        items = a_stock._fetch_news_announcements("600487")

        assert len(items) == 1
        item = items[0]
        assert item["kind"] == "公告"
        assert item["title"] == "亨通光电:关于回购的公告"
        assert item["time"].startswith("2026-09-25")
        assert "AN123" in item["url"]

    def test_research_requests_the_window_so_it_cannot_look_ahead(self, monkeypatch):
        seen: dict = {}

        def fake_get(url, params=None, **kwargs):
            seen.update(params or {})
            return _FakeResponse({"data": [
                {"title": "业绩弹性释放", "publishDate": "2026-08-04",
                 "orgSName": "某券商", "summary": "摘要", "infoCode": "R1"},
            ]})

        monkeypatch.setattr(a_stock, "_em_get", fake_get)

        items = a_stock._fetch_news_research(
            "600487", start_date="2026-08-01", end_date="2026-09-27"
        )

        assert seen["beginTime"] == "2026-08-01"
        assert seen["endTime"] == "2026-09-27", (
            "研报接口支持按日期取数，必须把窗口传下去，否则历史复盘会拿到之后的报告"
        )
        assert items[0]["kind"] == "研报"
        assert "某券商" in items[0]["title"]

    def test_research_without_an_end_date_does_nothing(self):
        assert a_stock._fetch_news_research("600487") == []

    def test_sina_reads_more_than_one_page(self, monkeypatch):
        pages: list[str] = []

        class FakeSina:
            encoding = "gb2312"

            def __init__(self, text):
                self.text = text

            def raise_for_status(self):
                return None

        def fake_get(url, **kwargs):
            page = url.rsplit("Page=", 1)[-1]
            pages.append(page)
            rows = "".join(
                f"2026-09-{20 + int(page):02d}&nbsp;10:0{i}&nbsp;"
                f"<a href='http://x/{page}{i}'>第{page}页第{i}条</a>\n"
                for i in range(3)
            )
            return FakeSina(rows)

        monkeypatch.setattr(a_stock._requests, "get", fake_get)

        items = a_stock._fetch_news_sina("600487", pages=3)

        assert pages == ["1", "2", "3"], "只读了第一页——这正是新闻窗口经常为空的原因之一"
        assert len(items) == 9
        assert all(i["content"] == "" for i in items)  # press feed has no bodies

    def test_a_page_with_no_rows_stops_the_walk(self, monkeypatch):
        calls: list[str] = []

        class FakeSina:
            encoding = "gb2312"
            text = ""

            def raise_for_status(self):
                return None

        def fake_get(url, **kwargs):
            calls.append(url.rsplit("Page=", 1)[-1])
            return FakeSina()

        monkeypatch.setattr(a_stock._requests, "get", fake_get)

        a_stock._fetch_news_sina("600487", pages=5)

        assert calls == ["1"], "空页之后还在继续请求"

    def test_the_dead_search_source_is_off_by_default(self, monkeypatch):
        assert a_stock._news_search_enabled() is False

        monkeypatch.setenv("MARVEL_ENABLE_EM_NEWS_SEARCH", "1")
        assert a_stock._news_search_enabled() is True


# ---------------------------------------------------------------------------
# Collection: de-duplication, ranking, resilience
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCollection:
    def _stub(self, monkeypatch, *, announcements=(), research=(), press=()):
        def item(title, kind, when, content=""):
            return {"title": title, "kind": kind, "time": when,
                    "content": content, "source": kind, "url": ""}

        monkeypatch.setattr(a_stock, "_fetch_news_announcements",
                            lambda code, **k: [item(*a) for a in announcements])
        monkeypatch.setattr(a_stock, "_fetch_news_research",
                            lambda code, **k: [item(*a) for a in research])
        monkeypatch.setattr(a_stock, "_fetch_news_sina",
                            lambda code, **k: [item(*a) for a in press])

    def test_announcements_outrank_press_and_are_kept_once(self, monkeypatch):
        self._stub(
            monkeypatch,
            announcements=[("亨通光电:关于控股股东部分股权解除质押公告", "公告", "2026-09-20")],
            press=[("江苏亨通光电股份有限公司 关于控股股东部分股权解除质押公告", "新闻", "2026-09-20")],
        )

        articles, counts = a_stock._collect_stock_news("600487", "2026-08-01", "2026-09-27")

        assert len(articles) == 1, "同一件事在公告和新闻里各出现了一次"
        assert articles[0]["kind"] == "公告", "去重后应保留信号更强的来源"
        assert counts["公告"] == 1 and counts["新闻"] == 1, "计数要如实反映各源返回量"

    def test_a_body_is_kept_when_the_winner_lacks_one(self, monkeypatch):
        self._stub(
            monkeypatch,
            announcements=[("亨通光电:关于回购的公告", "公告", "2026-09-20")],
            research=[("亨通光电:关于回购的公告", "研报", "2026-09-20", "摘要正文")],
        )

        articles, _ = a_stock._collect_stock_news("600487", "2026-08-01", "2026-09-27")

        assert articles[0]["kind"] == "公告"
        assert articles[0]["content"] == "摘要正文", "合并时丢掉了另一来源的正文"

    def test_ranking_is_announcement_then_research_then_press(self, monkeypatch):
        self._stub(
            monkeypatch,
            announcements=[("甲公告", "公告", "2026-09-01")],
            research=[("乙研报", "研报", "2026-09-02")],
            press=[("丙新闻", "新闻", "2026-09-03")],
        )

        articles, _ = a_stock._collect_stock_news("600487", "2026-08-01", "2026-09-27")

        assert [a["kind"] for a in articles] == ["公告", "研报", "新闻"]

    def test_newest_first_within_a_kind(self, monkeypatch):
        self._stub(
            monkeypatch,
            announcements=[("旧", "公告", "2026-09-01"), ("新", "公告", "2026-09-20")],
        )

        articles, _ = a_stock._collect_stock_news("600487", "2026-08-01", "2026-09-27")

        assert [a["title"] for a in articles] == ["新", "旧"]

    def test_one_dead_feed_does_not_empty_the_list(self, monkeypatch):
        self._stub(monkeypatch, press=[("丙新闻", "新闻", "2026-09-03")])

        def boom(*args, **kwargs):
            raise RuntimeError("公告接口挂了")

        monkeypatch.setattr(a_stock, "_fetch_news_announcements", boom)

        articles, counts = a_stock._collect_stock_news("600487", "2026-08-01", "2026-09-27")

        assert [a["title"] for a in articles] == ["丙新闻"]
        assert counts["公告"] == 0

    def test_publication_dates_are_attached_and_unparseable_ones_flagged(self, monkeypatch):
        self._stub(
            monkeypatch,
            announcements=[("有日期", "公告", "2026-09-20"), ("没日期", "公告", "")],
        )

        articles, _ = a_stock._collect_stock_news("600487", "2026-08-01", "2026-09-27")
        by_title = {a["title"]: a for a in articles}

        assert by_title["有日期"]["pub_date"] is not None
        assert by_title["没日期"]["pub_date"] is None


# ---------------------------------------------------------------------------
# get_news: the window filter and the disclosure still hold
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNewsContract:
    def _collector(self, monkeypatch, items):
        def fake(code, start, end, **kwargs):
            for item in items:
                item["pub_date"] = a_stock._parse_news_date(item.get("time", ""))
            return items, {"公告": len(items)}
        monkeypatch.setattr(a_stock, "_collect_stock_news", fake)

    def test_out_of_window_items_are_still_dropped(self, monkeypatch):
        """The point-in-time filter must survive the richer source set."""
        self._collector(monkeypatch, [
            {"title": "窗口内", "kind": "公告", "time": "2026-09-20", "content": "", "source": "x", "url": ""},
            {"title": "窗口后", "kind": "公告", "time": "2026-10-08", "content": "", "source": "x", "url": ""},
        ])

        out = a_stock.get_news("600487", "2026-09-01", "2026-09-27")

        assert "窗口内" in out
        assert "窗口后" not in out, "分析日之后的公告进入了报告"

    def test_undated_items_are_dropped_and_counted(self, monkeypatch):
        self._collector(monkeypatch, [
            {"title": "有日期", "kind": "公告", "time": "2026-09-20", "content": "", "source": "x", "url": ""},
            {"title": "没日期", "kind": "公告", "time": "", "content": "", "source": "x", "url": ""},
        ])

        out = a_stock.get_news("600487", "2026-09-01", "2026-09-27")

        assert "没日期" not in out
        assert "could not be read" in out

    def test_the_kind_is_visible_in_the_output(self, monkeypatch):
        self._collector(monkeypatch, [
            {"title": "一条公告", "kind": "公告", "time": "2026-09-20", "content": "正文", "source": "东方财富公告", "url": ""},
        ])

        out = a_stock.get_news("600487", "2026-09-01", "2026-09-27")

        assert "### [公告] 一条公告" in out, "来源类型没标出来，模型无法区分硬信息和新闻稿"
        assert "来源：" in out, "没有说明每个源返回了多少条"

    def test_an_empty_result_names_the_sources_it_asked(self, monkeypatch):
        monkeypatch.setattr(
            a_stock, "_collect_stock_news",
            lambda code, start, end, **k: ([], {"公告": 0, "研报": 0, "新闻": 0}),
        )

        out = a_stock.get_news("600487", "2026-09-01", "2026-09-27")

        assert "No news found" in out
        assert "Sources queried" in out, "空结果也要说清查过哪些源"
