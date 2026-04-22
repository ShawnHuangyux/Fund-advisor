"""akshare_client 缓存逻辑单元测试（不走网络；mock akshare）。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """让 akshare_client 使用临时目录做缓存，避免污染真实 data/cache。"""
    from fund_advisor.data import akshare_client

    monkeypatch.setattr(akshare_client, "_CACHE_DIR", tmp_path)
    yield tmp_path


def test_get_basic_info_parses_akshare_frame(isolated_cache):
    from fund_advisor.data import akshare_client as ac

    fake = pd.DataFrame(
        {
            "item": ["基金代码", "基金名称", "基金类型", "成立时间",
                     "基金公司", "基金经理", "最新规模"],
            "value": ["017513", "广发北证50成份指数C", "股票型-标准指数",
                      "2022-12-28", "广发基金管理有限公司", "刘杰", "4.09亿"],
        }
    )
    with patch("akshare.fund_individual_basic_info_xq", return_value=fake) as m:
        info = ac.get_basic_info("017513", use_cache=False)
        assert m.called
    assert info["name"] == "广发北证50成份指数C"
    assert info["fund_type_raw"] == "股票型-标准指数"

    # 第二次应命中缓存，不再调用 akshare
    with patch("akshare.fund_individual_basic_info_xq") as m:
        info2 = ac.get_basic_info("017513")
        assert not m.called
    # 缓存命中会多一个 _fetched_at 字段；业务字段应一致
    for k in ("name", "fund_type_raw", "fund_company", "fund_manager"):
        assert info2[k] == info[k]


def test_get_latest_nav_returns_decimal_and_date(isolated_cache):
    from fund_advisor.data import akshare_client as ac

    fake = pd.DataFrame(
        {
            "净值日期": pd.to_datetime(["2026-04-20", "2026-04-21"]),
            "单位净值": [1.6677, 1.6437],
            "日增长率": [1.06, -1.44],
        }
    )
    with patch("akshare.fund_open_fund_info_em", return_value=fake):
        nav = ac.get_latest_nav("017513", use_cache=False)
    assert nav["nav"] == Decimal("1.6437")
    assert nav["nav_date"].isoformat() == "2026-04-21"
    assert nav["daily_change_pct"] == Decimal("-1.44")


def test_nav_unavailable_raises(isolated_cache):
    from fund_advisor.data import akshare_client as ac

    with patch("akshare.fund_open_fund_info_em", return_value=pd.DataFrame()):
        with pytest.raises(ac.FundDataError):
            ac.get_latest_nav("999999", use_cache=False)


def test_cache_ttl_expiry(isolated_cache):
    """TTL 过期后应重新拉取。"""
    from fund_advisor.data import akshare_client as ac
    from datetime import datetime, timedelta

    # 伪造一个过期的缓存文件
    payload = {
        "code": "000001", "name": "old",
        "fund_type_raw": "股票型", "inception_date": None,
        "fund_company": None, "fund_manager": None, "latest_scale": None,
        "_fetched_at": (datetime.now() - timedelta(days=30)).isoformat(),
    }
    (isolated_cache / "000001_basic.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    fake = pd.DataFrame(
        {"item": ["基金名称", "基金类型"], "value": ["new name", "债券型"]}
    )
    with patch("akshare.fund_individual_basic_info_xq", return_value=fake) as m:
        info = ac.get_basic_info("000001")
        assert m.called  # 过期了应该重新拉
    assert info["name"] == "new name"
