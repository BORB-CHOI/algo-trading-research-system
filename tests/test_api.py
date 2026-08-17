"""케이스 검사기 API (ADR-0005).

실제 marcap 데이터가 있어야 도는 통합 테스트라 slow 로 표시한다.
데이터가 없으면 skip — 데이터 경로(요청→marcap→JSON)가 살아있는지 확인하는 용도.
"""

from __future__ import annotations

import pandas as pd
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
    """삼성전자 2018-05-04 50:1 분할이 보정으로 연속이 된다(ADR-0006).

    구간을 04-25 부터 잡는 이유: 04-30~05-03 은 분할 때문에 거래정지라 봉이 없다
    (거래정지일 제거, 2026-08-07). 마지막 분할 전 거래일은 04-27 이다."""
    params = {"code": "005930", "start": "2018-04-25", "end": "2018-05-08"}
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
    # 지지저항은 **차트 기능**이라 전략 목록에 없다 (오너 2026-08-09).
    assert set(by_key) >= {"ma_cross", "fib_retrace"}
    assert "sr_only" not in by_key

    ma = by_key["ma_cross"]
    assert (ma["signals"], ma["overlay"]) == (True, False)
    assert [p["key"] for p in ma["params"]] == ["short", "long"]

    fib = by_key["fib_retrace"]
    assert (fib["signals"], fib["overlay"]) == (False, True)
    assert [p["key"] for p in fib["params"]] == [
        "start_mode",
        "start_box_bars",
        "start_volume_mult",
        "start_keep_mult",
        "zz_depth",
        "zz_deviation_mode",
        "zz_deviation",
        "fib_band_mode",
        "fib_band_value",
        "sr_source",
        "sr_prd",
        "sr_scope",
        "sr_loopback",
        "sr_channel_width_pct",
        "sr_min_strength",
        "sr_round_max_gap_pct",
    ]  # ADR-0013 5차(Auto Fib)·0014 2차 개정(선 위아래 밴드 안에서만 지지저항)
    for s in by_key.values():
        assert set(s) == {"key", "name", "desc", "signals", "overlay", "params"}
        for p in s["params"]:
            # /api/conditions 와 같은 param 스키마 — 설명문·드롭다운 선택지까지 같이 내려간다.
            assert set(p) == {"key", "label", "type", "unit", "required", "desc", "choices"}


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
    """POST /api/overlay — 피보나치 되돌림 = 올라간 구간 (ADR-0013 5차)."""
    r = client.post(
        "/api/overlay",
        json={
            "code": "005930",
            "strategy": "fib_retrace",
            "params": {
                "start_mode": "평평한 구간 돌파",
                "start_box_bars": 20,
                "start_volume_mult": 2,
                "start_keep_mult": 2,
                "zz_depth": 10,
                "zz_deviation": 3,
                "zz_deviation_mode": "자동",
                "fib_band_mode": "자동",
                "fib_band_value": 0.5,
                "sr_scope": "전체",
                "sr_source": "고가·저가 전부",
                "sr_prd": 10,
                "sr_loopback": 290,
                "sr_channel_width_pct": 3,
                "sr_min_strength": 1,
                "sr_round_max_gap_pct": 5,
            },
            "end": "2026-07-16",
        },
    )
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"code", "strategy", "anchors", "lines", "touches"}
    assert j["code"] == "005930"
    assert j["strategy"] == "fib_retrace"
    assert set(j["anchors"]) == {
        "low_date",
        "high_date",
        "low_price",
        "high_price",
        "confirmed",
        "falling",
    }
    # look-ahead 금지 — 기준일(end) 오른쪽은 절대 안 본다 (오너 지적 2026-08-06).
    assert j["anchors"]["low_date"] <= j["anchors"]["high_date"] <= "2026-07-16"
    kinds = {ln["kind"] for ln in j["lines"]}
    assert kinds <= {"fib", "sr", "anchor"}
    # 피보나치 선은 근거와 무관하게 **항상 5개** (오너 2026-08-08: "피보나치는 피보나치대로").
    fib_labels = [ln["label"] for ln in j["lines"] if ln["kind"] == "fib"]
    assert fib_labels == ["23.6%", "38.2%", "50.0%", "61.8%", "78.6%"]
    # 지지저항 띠는 **피보나치 선에 붙은 것만** (ADR-0014 2차 개정). 개수는 종목·기준일에
    # 따라 0개일 수 있다 — 근거가 없으면 안 붙는 게 맞다. 붙었다면 어느 선의 띠인지가
    # 라벨 맨 앞에 나와야 한다("50.0% 지지저항 · 꺾임 2 · 210,000 · 220,000").
    for ln in j["lines"]:
        if ln["kind"] == "sr":
            assert any(ln["label"].startswith(f"{lb} 지지저항 ") for lb in fib_labels), ln["label"]
    for ln in j["lines"]:
        # 띠(top/bottom)는 **피보나치 선**에 붙는다 — 지지저항은 선 하나다
        # (오너 2026-08-09: "왜 지지저항에 그려져 있지? 왜 두께가 다른 거야?").
        expected = {"price", "label", "kind"} | (
            {"top", "bottom"} if ln["kind"] == "fib" else set()
        )
        assert set(ln) == expected
        if ln["kind"] == "fib":
            assert ln["bottom"] < ln["price"] < ln["top"]
    # 밴드 폭은 한 방식으로 재므로 다섯 선이 **같은 두께**다.
    widths = {round(ln["top"] - ln["bottom"], 6) for ln in j["lines"] if ln["kind"] == "fib"}
    assert len(widths) == 1
    assert j["touches"] == []  # 근접 판정 폐기 — 계약 필드만 유지


def test_simulate_looks_left_of_base_date_only() -> None:
    """POST /api/simulate — 기준일 왼쪽만 본다 (오너 지적 2026-08-06 회귀 고정).

    파동 바닥·꼭대기·체결일 전부 기준일 이하여야 한다. 기준일을 당기면 그 시점에
    존재하지 않던 고점이 사라져야 한다."""
    body = {
        "code": "005930",
        "end": "2026-03-31",
        "start_mode": "평평한 구간 돌파",
        "start_box_bars": 20,
        "start_volume_mult": 2,
        "start_keep_mult": 2,
        "zz_depth": 10,
        "zz_deviation": 3,
        "zz_deviation_mode": "자동",
        "fib_band_mode": "자동",
        "fib_band_value": 0.5,
        # "전체" 범위는 30년 이력 종목에서 재계획마다 전체를 훑어 분 단위로 느리다 —
        # ② 기본값(파동 구간)으로 검사한다. 이 테스트의 관심사는 look-ahead 뿐이다.
        "sr_scope": "파동 구간",
        "sr_prd": 10,
        "sr_loopback": 290,
        "sr_channel_width_pct": 3,
        "sr_min_strength": 1,
        "sr_round_max_gap_pct": 5,
        "buy": [{"id": "a", "ratio": 0.5, "weight": 100}],
        "sell": [{"id": "s", "rebound_pct": 10, "weight": 100}],
        "sell_basis": "avg_entry",
        "qty": 10,
    }
    r = client.post("/api/simulate", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["cycle"]["low_date"] <= j["cycle"]["high_date"] <= "2026-03-31"
    for f in j["fills"]:
        assert f["time"] <= "2026-03-31"
    # 기준일을 뒤로 옮기면 고점은 그대로거나 더 뒤 — 앞선 기준일 결과가 미래를 봤다면 모순.
    r2 = client.post("/api/simulate", json={**body, "end": "2026-07-16"})
    assert r2.status_code == 200
    assert r2.json()["cycle"]["high_date"] >= j["cycle"]["high_date"]


def test_overlay_signals_only_strategy_400() -> None:
    """오버레이 미지원 전략(ma_cross)으로 /api/overlay 를 부르면 400."""
    r = client.post(
        "/api/overlay",
        json={"code": "005930", "strategy": "ma_cross", "params": {"short": 5, "long": 20}},
    )
    assert r.status_code == 400
    assert "오버레이" in r.json()["detail"]


def test_거래정지일은_차트에_안_나온다() -> None:
    """marcap 은 거래정지일을 OHLC 0원으로 남긴다(BORB-32). 그대로 그리면 0원까지
    내리꽂는 캔들이 생긴다 — 삼성전자 2018-04-30~05-03(액면분할 정지)가 그랬다.
    전략 쪽(surge._clean)은 이미 걸러내고 있었고, 화면만 안 걸렀다(2026-08-07)."""
    for period in ("day", "week"):
        r = client.get(f"/api/candles?code=005930&start=2018-01-01&end=2018-12-31&period={period}")
        assert r.status_code == 200
        bad = [
            c for c in r.json()["candles"] if min(c["open"], c["high"], c["low"], c["close"]) <= 0
        ]
        assert bad == [], f"{period}봉에 0원 캔들: {bad[:3]}"


# ── 차트 도구(지지저항·오더블록·가격 빈틈)의 보이는 구간 ────────────────
# 계산은 언제나 일봉이다. 화면이 주봉·월봉이면 "보이는 봉 200개" = 200주·200달이라
# 봉 개수를 그대로 보내면 구간이 통째로 어긋난다 (오너 지적 2026-08-09).


def test_visible_bars_날짜를_주면_그날부터_센다() -> None:
    import pandas as pd
    from api.main import _visible_bars

    df = pd.DataFrame({"Date": pd.date_range("2026-01-01", periods=100, freq="D")})
    assert _visible_bars(df, "2026-01-01", 7) == 100
    assert _visible_bars(df, "2026-03-01", 7) == 41  # 3/1~4/10
    assert _visible_bars(df, None, 7) == 7  # 날짜가 없으면 봉 수 그대로
    # 구간에 봉이 하나도 없어도 0 을 넘기면 안 된다 — 서버가 400 을 낸다.
    assert _visible_bars(df, "2030-01-01", 7) == 1


def test_월봉_화면이면_지지저항이_그_구간_전체를_본다() -> None:
    """`start` 없이 bars 만 보내던 때는 2010~2026 이 보이는 월봉 화면에 최근 200일
    자리만 그려졌다. 왼쪽 끝 날짜를 주면 그 구간 전체에서 자리를 찾는다."""
    common = {
        "code": "005930",
        "end": "2026-08-04",
        "prd": 5,
        "width_pct": 2,
        "min_turns": 5,
        "bars": 200,
    }
    near = client.get("/api/support-resistance", params=common)
    far = client.get("/api/support-resistance", params={**common, "start": "2015-01-01"})
    assert near.status_code == 200 and far.status_code == 200
    lo = lambda r: min(x["price"] for x in r.json()["lines"])  # noqa: E731
    # 2015 년부터 보면 그 시절 낮은 가격대 자리가 나온다 — 최근 200일에는 없던 값이다.
    assert lo(far) < lo(near)


def test_월봉_화면이면_가격빈틈도_그_구간_전체를_본다() -> None:
    common = {
        "code": "005930",
        "kind": "가격 빈틈",
        "end": "2026-08-04",
        "bars": 200,
        "push_pct": 5,
        "min_gap_pct": 1,
        "lookback_bars": 10,
    }
    near = client.get("/api/price-zones", params=common)
    far = client.get("/api/price-zones", params={**common, "start": "2015-01-01"})
    assert near.status_code == 200 and far.status_code == 200
    assert len(far.json()["lines"]) > len(near.json()["lines"])


# ─────────────────────────────────────────────────────────────
# ④-b 전 구간 검사 + 되돌림 선 손절 (walk_forward, layer4.stops)
# ─────────────────────────────────────────────────────────────

# 실제 계산까지 가지 않는 검증 경로만 본다 — 전 구간 검사 자체는 몇 분짜리라
# 여기서 돌리지 않는다(tests/test_walk_forward.py 가 합성 데이터로 엔진을 본다).
_BT_BODY = {
    "split": "train",
    "conditions": [{"key": "price_range", "params": {"min": 1000}}],
    "logic": "and",
    "zz_depth": 10,
    "zz_deviation": 3.0,
    "fib_band_mode": "자동",
    "fib_band_value": 1.0,
    "sr_scope": "파동 구간",
    "sr_prd": 10,
    "sr_loopback": 290,
    "sr_channel_width_pct": 3.0,
    "sr_min_strength": 2,
    "sr_round_max_gap_pct": 3.0,
    "buy": [{"id": "b1", "ratio": 0.5, "weight": 100, "enabled": True}],
    "sell": [{"id": "s1", "rebound_pct": 10, "weight": 100, "enabled": True}],
}


def test_전_구간_검사는_기간을_안_줘도_된다() -> None:
    """ADR-0019: 날짜를 안 주면 기본값(2007-01-01 ~ 최신 거래일)을 쓴다.

    옛 동작은 400 이었다. 지금은 날짜 검사를 통과하고 **그다음** 검색식 검사에서 걸린다 —
    검색식을 비워서 그 사실을 확인한다(진짜 검사를 돌리면 몇 분 걸린다).
    """
    body = {**_BT_BODY, "conditions": []}
    body.pop("start", None)
    body.pop("end", None)
    r = client.post("/api/backtest/all", json=body)
    assert r.status_code == 400
    assert "검색식" in r.json()["detail"]  # 날짜가 아니라 검색식에서 걸렸다


def test_전_구간_검사는_거꾸로_된_기간을_거부한다() -> None:
    body = {**_BT_BODY, "start": "2024-01-01", "end": "2023-01-01"}
    r = client.post("/api/backtest/all", json=body)
    assert r.status_code == 400
    assert "끝나는 날" in r.json()["detail"]


def test_전_구간_검사도_검색식이_비면_거부한다() -> None:
    body = {**_BT_BODY, "conditions": [], "start": "2024-01-01", "end": "2024-06-01"}
    r = client.post("/api/backtest/all", json=body)
    assert r.status_code == 400
    assert "검색식" in r.json()["detail"]


def test_없는_작업번호는_404() -> None:
    assert client.get("/api/backtest/all/없는번호").status_code == 404


def test_되돌림_선_손절은_사기_전에도_그려진다() -> None:
    """평단 기준과 달리 파동만 정해지면 자리가 정해진다 — 매수 체결이 없어도 선이 뜬다."""
    body = {
        "code": "005930",
        "end": "2026-08-04",
        "zz_depth": 10,
        "zz_deviation": 3.0,
        "fib_band_mode": "자동",
        "fib_band_value": 1.0,
        "sr_scope": "파동 구간",
        "sr_prd": 10,
        "sr_loopback": 290,
        "sr_channel_width_pct": 3.0,
        "sr_min_strength": 2,
        "sr_round_max_gap_pct": 3.0,
        # 되돌림 99% — 사실상 안 걸리는 자리라 매수 체결이 없다
        "buy": [{"id": "b1", "ratio": 0.99, "weight": 100, "enabled": True}],
        "sell": [],
        "stop": {"enabled": True, "mode": "fib", "fib_ratio": 0.786},
    }
    r = client.post("/api/simulate", json=body)
    assert r.status_code == 200, r.text
    stop_lines = [x for x in r.json()["lines"] if x["kind"] == "stop"]
    assert len(stop_lines) == 1
    assert "78.6" in stop_lines[0]["label"]


def test_모르는_되돌림_비율은_거부한다() -> None:
    body = {
        "code": "005930",
        "end": "2026-08-04",
        "zz_depth": 10,
        "zz_deviation": 3.0,
        "fib_band_mode": "자동",
        "fib_band_value": 1.0,
        "sr_scope": "파동 구간",
        "sr_prd": 10,
        "sr_loopback": 290,
        "sr_channel_width_pct": 3.0,
        "sr_min_strength": 2,
        "sr_round_max_gap_pct": 3.0,
        "buy": [{"id": "b1", "ratio": 0.5, "weight": 100, "enabled": True}],
        "sell": [],
        "stop": {"enabled": True, "mode": "fib", "fib_ratio": 0.42},
    }
    r = client.post("/api/simulate", json=body)
    assert r.status_code == 400
    assert "되돌림 비율" in r.json()["detail"]


# ── 주봉·월봉 = 나무증권 원본 + 상폐만 합성 (2026-08-15 오너 결정) ────────────


def _fake_daily() -> pd.DataFrame:
    import pandas as pd

    dates = pd.bdate_range("2026-07-06", "2026-07-17")  # 2주(평일 10일)
    return pd.DataFrame(
        {
            "Date": dates,
            "Code": "000001",
            "Name": "가짜종목",
            "Open": 100.0,
            "High": 110.0,
            "Low": 90.0,
            "Close": 105.0,
            "Volume": 1000.0,
            "Amount": 105000.0,
        }
    )


def test_주봉은_나무_원본을_먼저_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """원본이 있으면 그 값이 그대로 나오고, 원본 마지막(미완성 가능) 봉 뒤는 합성으로 잇는다."""
    import api.main as m
    import pandas as pd

    raw = pd.DataFrame(
        {
            "Date": [pd.Timestamp("2026-07-10"), pd.Timestamp("2026-07-17")],
            "Open": [55.0, 56.0],
            "High": [66.0, 67.0],
            "Low": [44.0, 45.0],
            "Close": [60.0, 61.0],
            "Volume": [5000.0, 5100.0],
            "Amount": [1.0, 2.0],
        }
    )
    monkeypatch.setattr(m, "load_namuh_bars", lambda code, span, market="krx": raw)
    out = m.period_candles(_fake_daily(), "week")
    # 원본 두 봉 중 마지막은 미완성 취급으로 버려져 첫 봉만 남고, 뒤는 합성이다.
    assert float(out.iloc[0]["Close"]) == 60.0  # 나무 원본 값
    assert float(out.iloc[-1]["Close"]) == 105.0  # 합성 꼬리(일봉 값)
    assert out["Date"].is_monotonic_increasing


def test_나무_원본이_없으면_합성으로_대체(monkeypatch: pytest.MonkeyPatch) -> None:
    """상장폐지·미수집 종목: 원본 파일이 없으면(None) 기존 합성 결과와 같아야 한다."""
    import api.main as m

    daily = _fake_daily()
    monkeypatch.setattr(m, "load_namuh_bars", lambda code, span, market="krx": None)
    out = m.period_candles(daily, "week")
    expected = m.resample_candles(daily, "week")
    assert out.reset_index(drop=True).equals(expected.reset_index(drop=True))
