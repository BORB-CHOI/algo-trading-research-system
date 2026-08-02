"""케이스 검사기 API (ADR-0005).

실제 marcap 데이터가 있어야 도는 통합 테스트라 slow 로 표시한다.
데이터가 없으면 skip — 데이터 경로(요청→marcap→JSON)가 살아있는지 확인하는 용도.
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


def test_health_returns_years() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["years"]) > 0


def test_candles_samsung_shape() -> None:
    r = client.get(
        "/api/candles", params={"code": "005930", "start": "2026-01-01", "end": "2026-07-16"}
    )
    assert r.status_code == 200
    j = r.json()
    assert j["name"] == "삼성전자"
    assert j["count"] > 0
    assert set(j["candles"][0]) == {"time", "open", "high", "low", "close", "volume", "amount"}
    # 날짜 오름차순이어야 차트가 제대로 그려진다.
    times = [c["time"] for c in j["candles"]]
    assert times == sorted(times)


def test_code_zfill_normalizes() -> None:
    """'5930' 처럼 앞자리 0 이 빠진 코드도 삼성전자로 정규화된다(ADR-0003)."""
    r = client.get(
        "/api/candles", params={"code": "5930", "start": "2026-07-01", "end": "2026-07-16"}
    )
    assert r.status_code == 200
    assert r.json()["code"] == "005930"


def test_unknown_code_returns_404() -> None:
    r = client.get(
        "/api/candles", params={"code": "000000", "start": "2026-01-01", "end": "2026-02-01"}
    )
    assert r.status_code == 404


def _min_overnight_ratio(candles: list[dict]) -> float:
    closes = [c["close"] for c in candles]
    return min(b / a for a, b in zip(closes[:-1], closes[1:], strict=False))


def test_split_adjustment_removes_cliff() -> None:
    """삼성전자 2018-05-04 50:1 분할이 보정으로 연속이 된다(ADR-0006)."""
    params = {"code": "005930", "start": "2018-04-30", "end": "2018-05-08"}
    raw = client.get("/api/candles", params={**params, "adjust": "false"}).json()
    adj = client.get("/api/candles", params={**params, "adjust": "true"}).json()

    # 원주가엔 분할 절벽(하루 -98%), 보정하면 연속.
    assert _min_overnight_ratio(raw["candles"]) < 0.1
    assert _min_overnight_ratio(adj["candles"]) > 0.8
    # 분할 전 보정가 ≈ 원주가 / 50
    assert abs(adj["candles"][0]["close"] - raw["candles"][0]["close"] / 50) < 1.0


def test_recent_window_unchanged_by_adjust() -> None:
    """분할 없는 최근 구간은 보정해도 원주가와 같다."""
    params = {"code": "005930", "start": "2026-07-01", "end": "2026-07-16"}
    raw = client.get("/api/candles", params={**params, "adjust": "false"}).json()
    adj = client.get("/api/candles", params={**params, "adjust": "true"}).json()
    raw_c = [round(c["close"], 2) for c in raw["candles"]]
    adj_c = [round(c["close"], 2) for c in adj["candles"]]
    assert raw_c == adj_c


# ─────────────────────────────────────────────────────────────
# 전략 카탈로그·신호·오버레이 계약 스모크 (ADR-0009)
# 단위 검증(베이스 탐지·레벨·라운드)은 test_fibonacci.py — 여기는 3 엔드포인트 계약만.
# ─────────────────────────────────────────────────────────────


def test_strategies_catalog_contract() -> None:
    """GET /api/strategies — 조건검색과 같은 param 스키마 형식(프런트가 폼 코드 재사용)."""
    r = client.get("/api/strategies")
    assert r.status_code == 200
    by_key = {s["key"]: s for s in r.json()["strategies"]}
    assert set(by_key) >= {"ma_cross", "fib_retrace"}

    ma = by_key["ma_cross"]
    assert (ma["signals"], ma["overlay"]) == (True, False)
    assert [p["key"] for p in ma["params"]] == ["short", "long"]

    fib = by_key["fib_retrace"]
    assert (fib["signals"], fib["overlay"]) == (False, True)
    assert [p["key"] for p in fib["params"]] == ["lookback", "base_window", "base_range", "near"]
    for s in by_key.values():
        assert set(s) == {"key", "name", "desc", "signals", "overlay", "params"}
        for p in s["params"]:
            assert set(p) == {"key", "label", "type", "unit", "required"}


def test_signals_post_ma_cross() -> None:
    """POST /api/signals — 이평 기간은 요청 params 로만 전달한다(하드코딩 5/20 폐기)."""
    r = client.post(
        "/api/signals",
        json={
            "code": "005930",
            "strategy": "ma_cross",
            "params": {"short": 5, "long": 20},
            "start": "2025-01-01",
            "end": "2026-07-16",
        },
    )
    assert r.status_code == 200
    j = r.json()
    assert j["code"] == "005930"
    assert j["strategy"] == "ma_cross"
    assert len(j["signals"]) > 0  # 1년 반 구간이면 5/20 교차가 최소 1개는 있다
    for s in j["signals"]:
        assert set(s) == {"time", "side", "price"}
        assert s["side"] in ("buy", "sell")


def test_signals_get_removed() -> None:
    """기존 GET /api/signals 는 제거 — 파라미터를 숨기지 않기 위해 항상 POST 명시 전달."""
    r = client.get("/api/signals", params={"code": "005930", "strategy": "ma_cross"})
    assert r.status_code == 405  # Method Not Allowed (라우트는 POST 전용)


def test_signals_missing_param_400() -> None:
    """필수 파라미터 누락은 400 — 서버가 기본값으로 조용히 메꾸지 않는다(ADR-0009)."""
    r = client.post(
        "/api/signals",
        json={"code": "005930", "strategy": "ma_cross", "params": {"short": 5}},
    )
    assert r.status_code == 400
    assert "long" in r.json()["detail"]


def test_signals_unknown_strategy_404() -> None:
    r = client.post("/api/signals", json={"code": "005930", "strategy": "없는전략", "params": {}})
    assert r.status_code == 404


def test_overlay_contract() -> None:
    """POST /api/overlay — 피보나치 되돌림. 실데이터라 베이스 존재를 보장할 수 없으므로
    200(계약 형태) 또는 규정된 400 메시지 중 하나면 계약 준수다."""
    r = client.post(
        "/api/overlay",
        json={
            "code": "005930",
            "strategy": "fib_retrace",
            "params": {"lookback": 250, "base_window": 10, "base_range": 6, "near": 2},
            "end": "2026-07-16",
        },
    )
    if r.status_code == 400:
        assert "평평한 베이스" in r.json()["detail"]
        return
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"code", "strategy", "anchors", "lines", "touches"}
    assert j["code"] == "005930"
    assert j["strategy"] == "fib_retrace"
    assert set(j["anchors"]) == {"base_start", "base_end", "swing_high", "base_price", "high_price"}
    assert j["anchors"]["base_start"] <= j["anchors"]["base_end"] < j["anchors"]["swing_high"]
    kinds = {ln["kind"] for ln in j["lines"]}
    assert kinds <= {"fib", "round", "anchor"}
    fib_labels = [ln["label"] for ln in j["lines"] if ln["kind"] == "fib"]
    assert fib_labels == ["23.6%", "38.2%", "50.0%", "61.8%", "78.6%"]
    for ln in j["lines"]:
        assert set(ln) == {"price", "label", "kind"}
    assert len(j["touches"]) <= 30
    for t in j["touches"]:
        assert set(t) == {"time", "price", "label"}


def test_overlay_signals_only_strategy_400() -> None:
    """오버레이 미지원 전략(ma_cross)으로 /api/overlay 를 부르면 400."""
    r = client.post(
        "/api/overlay",
        json={"code": "005930", "strategy": "ma_cross", "params": {"short": 5, "long": 20}},
    )
    assert r.status_code == 400
    assert "오버레이" in r.json()["detail"]
