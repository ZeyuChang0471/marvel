"""Market-prefix routing for 6-digit A-share codes.

Regression cover for issue #85: the Beijing Stock Exchange started issuing
920xxx codes for new listings in October 2024.  A bare ``startswith("9")``
routed them to Shanghai, where the Tencent quote endpoint answers with an
empty payload instead of an error — so the failure was silent.

Same failure mode one digit over: the BSE also issues 43xxxx/40xxxx codes.
Those fell through to ``sz``, and the Tencent endpoint again answered with an
empty ``v_pv_none_match`` line that the parser skips — PE/PB/市值/涨跌停 just
went missing, with no error to trace it back to.
"""

import unittest

import pytest

from marvel.dataflows.a_stock import _get_prefix, _sina_stock_code


@pytest.mark.unit
class MarketPrefixRoutingTests(unittest.TestCase):

    def test_shanghai_main_board_and_star(self):
        self.assertEqual(_get_prefix("600519"), "sh")
        self.assertEqual(_get_prefix("601398"), "sh")
        self.assertEqual(_get_prefix("688017"), "sh")

    def test_shenzhen_main_board_and_chinext(self):
        self.assertEqual(_get_prefix("000001"), "sz")
        self.assertEqual(_get_prefix("002463"), "sz")
        self.assertEqual(_get_prefix("300476"), "sz")

    def test_beijing_legacy_8_prefix(self):
        self.assertEqual(_get_prefix("830799"), "bj")
        self.assertEqual(_get_prefix("871981"), "bj")

    def test_beijing_920_series_routes_to_bj_not_sh(self):
        """920xxx is the Beijing Stock Exchange's new-listing range, not Shanghai."""
        for code in ("920002", "920008", "920098", "920111"):
            self.assertEqual(_get_prefix(code), "bj", f"{code} must route to bj")

    def test_shanghai_b_shares_still_route_to_sh(self):
        """900xxx (Shanghai B shares) is the only leading-9 range that really is Shanghai."""
        self.assertEqual(_get_prefix("900901"), "sh")
        self.assertEqual(_get_prefix("900932"), "sh")

    def test_beijing_4_prefix_routes_to_bj_not_sz(self):
        """430xxx / 400xxx 也是北交所，不能落到 sz。"""
        for code in ("430047", "430139", "400001", "400008"):
            self.assertEqual(_get_prefix(code), "bj", f"{code} must route to bj")

    def test_sina_code_uses_the_shared_prefix_rule(self):
        """新浪 paperCode 必须走 _get_prefix，不能内联 "6→sh 否则 sz"。"""
        self.assertEqual(_sina_stock_code("600519"), "sh600519")
        self.assertEqual(_sina_stock_code("000001"), "sz000001")
        self.assertEqual(_sina_stock_code("430047"), "bj430047")
        self.assertEqual(_sina_stock_code("830799"), "bj830799")


if __name__ == "__main__":
    unittest.main()
