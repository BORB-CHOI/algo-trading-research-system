"""시장맵 API (/api/heatmap) — 업종(네이버, 표시 전용) 트리맵 계약.

실제 marcap 데이터가 필요해 slow, 없으면 skip (test_api.py 와 동일 관례).
업종 매핑은 conftest 의 autouse fixture 가 빈 맵(ready=True)으로 막아 두므로,
그룹핑·정렬은 monkeypatch 로 원하는 매핑을 주입해 검증한다.
"""

from __future__ import annotations

import pytest
from api.main import app
from fastapi.testclient import TestClient

from src.layer1_data.marcap_loader import available_years

pytestmark = pytest.mark.slow

client = TestClient(app)


@pytest.fixture(autouse=True)
def _require_data() -> None:
    if not available_years():
        pytest.skip("marcap 데이터 없음")


def _codes(j: dict) -> set[str]:
    return {it["code"] for s in j["sectors"] for it in s["items"]}


def test_heatmap_schema_and_defaults() -> None:
    r = client.get("/api/heatmap", params={"top": 10})
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"date", "market", "sectors_ready", "sectors"}
    assert j["market"] == "KOSPI"  # 기본 시장
    assert j["sectors_ready"] is True  # conftest 가 (빈 맵, ready=True) 로 막는다
    for sec in j["sectors"]:
        assert set(sec) == {"name", "items"}
        for it in sec["items"]:
            assert set(it) == {"code", "name", "marcap", "chg"}
    assert sum(len(s["items"]) for s in j["sectors"]) == 10


def test_heatmap_unmapped_goes_to_etc() -> None:
    """매핑 없는 종목은 "기타" — 빈 맵이면 전 종목이 "기타" 한 그룹이다."""
    j = client.get("/api/heatmap", params={"top": 10}).json()
    assert [s["name"] for s in j["sectors"]] == ["기타"]


def test_heatmap_market_filter() -> None:
    kospi = client.get("/api/heatmap", params={"market": "KOSPI", "top": 50}).json()
    kosdaq = client.get("/api/heatmap", params={"market": "KOSDAQ", "top": 50}).json()
    assert kosdaq["market"] == "KOSDAQ"
    assert "005930" in _codes(kospi)  # 삼성전자는 KOSPI 시총 상위
    assert _codes(kospi) and _codes(kosdaq)
    assert _codes(kospi).isdisjoint(_codes(kosdaq))


def test_heatmap_invalid_market_422() -> None:
    r = client.get("/api/heatmap", params={"market": "NASDAQ"})
    assert r.status_code == 422


def test_heatmap_grouping_and_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    """주입한 업종 매핑대로 묶이고, 업종=시총합·종목=시총 내림차순으로 정렬된다."""
    import api.main

    base = client.get("/api/heatmap", params={"top": 30}).json()
    codes = [it["code"] for it in base["sectors"][0]["items"]]  # 빈 맵 → "기타" 하나
    mapping = {c: ("A업종" if i % 2 == 0 else "B업종") for i, c in enumerate(codes[:10])}
    monkeypatch.setattr(api.main, "industry_map", lambda: (mapping, True))

    j = client.get("/api/heatmap", params={"top": 30}).json()
    assert {s["name"] for s in j["sectors"]} == {"A업종", "B업종", "기타"}
    sums = [sum(it["marcap"] for it in s["items"]) for s in j["sectors"]]
    assert sums == sorted(sums, reverse=True)  # 업종 시총합 내림차순
    by_sector = {s["name"]: {it["code"] for it in s["items"]} for s in j["sectors"]}
    for code, name in mapping.items():
        assert code in by_sector[name]
    for s in j["sectors"]:
        marcaps = [it["marcap"] for it in s["items"]]
        assert marcaps == sorted(marcaps, reverse=True)  # 종목 시총 내림차순


def test_heatmap_sectors_ready_false_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """수집이 아직이면 sectors_ready=False + 전 종목 "기타" — 프런트 로딩 표시용."""
    import api.main

    monkeypatch.setattr(api.main, "industry_map", lambda: ({}, False))
    j = client.get("/api/heatmap", params={"top": 10}).json()
    assert j["sectors_ready"] is False
    assert [s["name"] for s in j["sectors"]] == ["기타"]
