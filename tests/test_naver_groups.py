"""네이버 그룹 수집 공용기(naver_groups) + 업종 매핑(industry) 단위 테스트.

네트워크는 _fetch 를 통째로 바꿔 막는다 — 실데이터·실요청 불필요, 항상 돈다.
_build 를 직접 불러 스레드 없이 결정적으로 검증한다.
"""

from __future__ import annotations

import pytest
import requests

from src.layer1_data import naver_groups as ng
from src.layer1_data.industry import industry_map


def make_collector(monkeypatch: pytest.MonkeyPatch, pages: dict[str, dict]) -> ng.GroupCollector:
    """URL → 응답 dict 테이블로 _fetch 를 대체한 수집기를 만든다."""
    monkeypatch.setattr(ng, "_fetch", lambda url: pages[url])
    monkeypatch.setattr(ng.time, "sleep", lambda s: None)  # 그룹 간 대기 생략
    return ng.GroupCollector("BASE")


def test_build_maps_codes_to_group_names(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        "BASE?page=1&pageSize=100": {
            "groups": [
                {"no": 1, "name": "반도체", "totalCount": 2},
                {"no": 2, "name": "은행", "totalCount": 1},
            ],
            "totalCount": 2,
        },
        # itemCode 앞자리 0 누락은 zfill 로 정규화된다.
        "BASE/1?page=1&pageSize=100": {"stocks": [{"itemCode": "5930"}, {"itemCode": "000660"}]},
        "BASE/2?page=1&pageSize=100": {"stocks": [{"itemCode": "005930"}]},
    }
    c = make_collector(monkeypatch, pages)
    c._build()
    m, ready = c.map()
    assert ready is True
    assert m == {"005930": ["반도체", "은행"], "000660": ["반도체"]}


def test_build_paginates_members(monkeypatch: pytest.MonkeyPatch) -> None:
    # totalCount 101 → 멤버 2페이지를 다 돈다.
    pages = {
        "BASE?page=1&pageSize=100": {
            "groups": [{"no": 7, "name": "화학", "totalCount": 101}],
            "totalCount": 1,
        },
        "BASE/7?page=1&pageSize=100": {
            "stocks": [{"itemCode": str(i).zfill(6)} for i in range(100)]
        },
        "BASE/7?page=2&pageSize=100": {"stocks": [{"itemCode": "000100"}]},
    }
    c = make_collector(monkeypatch, pages)
    c._build()
    m, _ = c.map()
    assert len(m) == 101


def test_build_skips_failed_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """그룹 하나 실패는 건너뛴다 — 전체를 버리지 않는다(부분 실패 허용)."""

    def fetch(url: str) -> dict:
        if url.startswith("BASE/1?"):
            raise requests.ConnectionError("멤버 조회 실패")
        return {
            "BASE?page=1&pageSize=100": {
                "groups": [
                    {"no": 1, "name": "실패그룹", "totalCount": 1},
                    {"no": 2, "name": "생존그룹", "totalCount": 1},
                ],
                "totalCount": 2,
            },
            "BASE/2?page=1&pageSize=100": {"stocks": [{"itemCode": "035420"}]},
        }[url]

    monkeypatch.setattr(ng, "_fetch", fetch)
    monkeypatch.setattr(ng.time, "sleep", lambda s: None)
    c = ng.GroupCollector("BASE")
    c._build()
    m, ready = c.map()
    assert ready is True
    assert m == {"035420": ["생존그룹"]}


def test_build_total_failure_keeps_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """그룹 목록부터 실패하면 맵을 덮지 않고 ready=False 를 유지한다."""

    def fetch(url: str) -> dict:
        raise requests.ConnectionError("전체 실패")

    monkeypatch.setattr(ng, "_fetch", fetch)
    c = ng.GroupCollector("BASE")
    c._build()
    assert c._state["building"] is False
    assert (c._state["map"], c._state["at"]) == ({}, 0.0)


def test_industry_map_keeps_first_industry(monkeypatch: pytest.MonkeyPatch) -> None:
    """한 종목이 여러 업종에 나오면 처음 것 하나만 쓴다(시장맵 타일은 업종 1개)."""
    from src.layer1_data import industry

    monkeypatch.setattr(
        industry._collector, "map", lambda: ({"005930": ["반도체", "전자"], "000001": []}, True)
    )
    m, ready = industry_map()
    assert ready is True
    assert m == {"005930": "반도체"}  # 빈 목록 종목은 매핑에서 빠진다
