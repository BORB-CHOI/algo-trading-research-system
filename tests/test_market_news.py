"""시장 지표·뉴스 수집 (BORB-43).

외부 네트워크에 의존하므로 slow 표시. 응답 형태(계약)만 확인한다.
"""

from __future__ import annotations

import pytest

from src.layer1_data.market import GROUPS, market_snapshot
from src.layer1_data.news import market_news, stock_news

pytestmark = pytest.mark.slow


def test_market_snapshot_shape() -> None:
    groups = market_snapshot(force=True)
    assert [g["group"] for g in groups] == [name for name, _ in GROUPS]
    items = [it for g in groups for it in g["items"]]
    assert len(items) == sum(len(v) for _, v in GROUPS)
    for it in items:
        assert set(it) == {"key", "name", "unit", "price", "chg", "asof"}
    # 전부 실패면 소스가 깨진 것 — 최소 절반은 값이 있어야 한다
    assert sum(it["price"] is not None for it in items) >= len(items) // 2


def test_market_news_shape() -> None:
    items = market_news(limit=5)
    assert len(items) <= 5
    for it in items:
        assert set(it) == {"title", "source", "url", "datetime"}
        assert it["title"]


def test_stock_news_shape() -> None:
    items = stock_news("005930", limit=3)
    for it in items:
        assert set(it) == {"title", "source", "url", "datetime"}


def test_stock_news_bad_code_is_empty() -> None:
    assert stock_news("999999", limit=3) == []
