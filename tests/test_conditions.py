"""조건검색 백엔드 테스트 (키움 [0150] 방식).

- 단위 테스트: 합성 일봉 패널로 조건별 판정을 검증한다. 실데이터 불필요 → 항상 돈다.
- API 테스트: 실제 marcap 이 필요해 slow 표시, 데이터 없으면 skip (test_api.py 와 동일 관례).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer3_strategy.conditions import (
    CATEGORIES,
    CONDITIONS,
    MAX_LOOKBACK,
    HistPanel,
    categories_payload,
    evaluate,
    parse_conditions,
    required_lookback,
)

# ─────────────────────────────────────────────────────────────
# 합성 패널 헬퍼
# ─────────────────────────────────────────────────────────────


def make_hist(
    closes: dict[str, list[float]],
    opens: dict[str, list[float]] | None = None,
    volumes: dict[str, list[float]] | None = None,
    base: str = "2026-07-16",
) -> tuple[pd.DataFrame, pd.Timestamp]:
    """종목별 종가 리스트 → long 형 일봉. 마지막 원소 = 기준일.

    리스트가 짧은 종목은 최근 쪽으로 정렬한다(신규상장 흉내).
    """
    n = max(len(v) for v in closes.values())
    dates = pd.bdate_range(end=base, periods=n)
    rows = []
    for code, cs in closes.items():
        os_ = (opens or {}).get(code, cs)
        vs = (volumes or {}).get(code, [1000.0] * len(cs))
        for i, c in enumerate(cs):
            rows.append(
                {
                    "Date": dates[n - len(cs) + i],
                    "Code": code,
                    "Open": os_[i],
                    "Close": c,
                    "Volume": vs[i],
                }
            )
    return pd.DataFrame(rows), dates[-1]


def run(
    conds: list[dict],
    closes: dict[str, list[float]],
    opens: dict[str, list[float]] | None = None,
    volumes: dict[str, list[float]] | None = None,
    logic: str = "and",
    base_over: dict[str, dict[str, float]] | None = None,
) -> set[str]:
    """조건식을 합성 패널에 돌려 매칭된 종목 코드 집합을 돌려준다."""
    hist_df, base_date = make_hist(closes, opens, volumes)
    panel = HistPanel(hist_df, base_date)
    last = hist_df[hist_df["Date"] == base_date].set_index("Code")
    base = pd.DataFrame(
        {
            "Name": list(last.index),
            "Market": "KOSPI",
            "Close": last["Close"],
            "Amount": 1e9,
            "Marcap": 5000 * 1e8,
        }
    )
    for col, per_code in (base_over or {}).items():
        base[col] = base.index.map(per_code)
    mask = evaluate(parse_conditions(conds), panel, base, logic)
    return set(mask[mask].index)


# ─────────────────────────────────────────────────────────────
# 계약(메타) 검증
# ─────────────────────────────────────────────────────────────


def test_categories_payload_matches_contract() -> None:
    payload = categories_payload()
    assert set(payload) == {"categories"}
    cat_keys = [c["key"] for c in payload["categories"]]
    assert cat_keys == ["range", "price", "technical", "volume", "pattern"]
    seen = []
    for cat in payload["categories"]:
        assert set(cat) == {"key", "name", "conditions"}
        for c in cat["conditions"]:
            assert set(c) == {"key", "name", "desc", "params"}
            assert c["params"], f"{c['key']}: 파라미터가 비면 UI 를 못 그린다"
            for p in c["params"]:
                assert set(p) == {"key", "label", "type", "unit", "required"}
                assert p["type"] in {"number", "int"}
                assert p["unit"] in {"원", "억", "%", "일", "배", "주"}
            seen.append(c["key"])
    # 레지스트리의 모든 조건이 정확히 한 카테고리에 속한다.
    assert sorted(seen) == sorted(CONDITIONS)
    assert len(seen) == len(set(seen))


def test_categories_meta_consistent_with_registry() -> None:
    for _, _, keys in CATEGORIES:
        for k in keys:
            assert k in CONDITIONS


# ─────────────────────────────────────────────────────────────
# 파싱·검증 오류
# ─────────────────────────────────────────────────────────────


def test_parse_empty_conditions_rejected() -> None:
    with pytest.raises(ValueError, match="비어"):
        parse_conditions([])


def test_parse_unknown_key_rejected() -> None:
    with pytest.raises(ValueError, match="알 수 없는 조건"):
        parse_conditions([{"key": "no_such", "params": {"min": 1}}])


def test_parse_unknown_param_rejected() -> None:
    with pytest.raises(ValueError, match="알 수 없는 파라미터"):
        parse_conditions([{"key": "price_range", "params": {"weird": 1}}])


def test_parse_missing_required_param_rejected() -> None:
    with pytest.raises(ValueError, match="누락"):
        parse_conditions([{"key": "golden_cross", "params": {"short": 5, "long": 20}}])


def test_parse_needs_at_least_one_value() -> None:
    # 선택 파라미터뿐인 조건은 최소 1개 값이 있어야 한다(계약).
    with pytest.raises(ValueError, match="최소 1개"):
        parse_conditions([{"key": "price_range", "params": {}}])


def test_parse_int_param_rejects_fraction_and_zero() -> None:
    with pytest.raises(ValueError, match="정수"):
        parse_conditions([{"key": "new_high", "params": {"days": 2.5}}])
    with pytest.raises(ValueError, match="1 이상"):
        parse_conditions([{"key": "new_high", "params": {"days": 0}}])


def test_parse_short_must_be_less_than_long() -> None:
    with pytest.raises(ValueError, match="짧아야"):
        parse_conditions([{"key": "golden_cross", "params": {"short": 20, "long": 5, "within": 3}}])


def test_parse_lookback_capped() -> None:
    ok = parse_conditions(
        [{"key": "new_high", "params": {"days": 250, "within": 250}}]
    )
    assert required_lookback(ok) == 500
    with pytest.raises(ValueError, match=str(MAX_LOOKBACK)):
        parse_conditions(
            [{"key": "new_high", "params": {"days": 400, "within": 200}}]
        )


# ─────────────────────────────────────────────────────────────
# 범위지정
# ─────────────────────────────────────────────────────────────


def test_price_range() -> None:
    closes = {"A": [1500.0], "B": [500.0], "C": [90000.0]}
    got = run([{"key": "price_range", "params": {"min": 1000, "max": 50000}}], closes)
    assert got == {"A"}


def test_marcap_and_amount_eok_conversion() -> None:
    # 시총 5000억으로 세팅됨 → min 1000억은 통과, min 6000억은 탈락 (억→원 환산은 서버).
    closes = {"A": [100.0]}
    assert run([{"key": "marcap_range", "params": {"min": 1000}}], closes) == {"A"}
    assert run([{"key": "marcap_range", "params": {"min": 6000}}], closes) == set()
    # 거래대금 10억 세팅 → min 5억 통과, min 50억 탈락.
    assert run([{"key": "amount_range", "params": {"min": 5}}], closes) == {"A"}
    assert run([{"key": "amount_range", "params": {"min": 50}}], closes) == set()


def test_volume_range() -> None:
    closes = {"A": [100.0], "B": [100.0]}
    volumes = {"A": [50_000.0], "B": [100.0]}
    got = run([{"key": "volume_range", "params": {"min": 10_000}}], closes, volumes=volumes)
    assert got == {"A"}


# ─────────────────────────────────────────────────────────────
# 시세분석
# ─────────────────────────────────────────────────────────────


def test_change_range() -> None:
    closes = {"A": [100.0, 110.0], "B": [100.0, 95.0]}  # A +10%, B -5%
    got = run([{"key": "change_range", "params": {"min": 5}}], closes)
    assert got == {"A"}
    got = run([{"key": "change_range", "params": {"max": 0}}], closes)
    assert got == {"B"}


def test_cum_change() -> None:
    closes = {"A": [100.0, 90.0, 95.0, 130.0], "B": [100.0, 100.0, 100.0, 101.0]}
    # A: 3일 전 100 → 130 = +30%.
    got = run([{"key": "cum_change", "params": {"days": 3, "min": 20}}], closes)
    assert got == {"A"}


def test_new_high_and_new_low() -> None:
    closes = {
        "A": [5.0, 6.0, 7.0, 4.0, 8.0],  # 직전 3일 최고 7 < 8 → 신고가
        "B": [5.0, 6.0, 9.0, 4.0, 8.0],  # 직전 3일 최고 9 → 아님
        "C": [7.0, 8.0],  # 이력 부족 → 아님
    }
    assert run([{"key": "new_high", "params": {"days": 3, "within": 1}}], closes) == {"A"}
    lows = {"A": [5.0, 4.0, 3.0, 6.0, 2.0], "B": [5.0, 4.0, 3.0, 6.0, 4.0]}
    assert run([{"key": "new_low", "params": {"days": 3, "within": 1}}], lows) == {"A"}


def test_new_high_within() -> None:
    """이내(within): 기준일엔 신고가가 아니어도 최근 X일 안에 돌파했으면 잡힌다 — 눌림 검색용."""
    closes = {
        "A": [5.0, 6.0, 7.0, 9.0, 8.0],  # 어제 9 로 돌파 후 오늘 눌림
        "B": [5.0, 6.0, 9.0, 8.0, 7.0],  # 돌파일이 3일 전 → within 2 밖
    }
    assert run([{"key": "new_high", "params": {"days": 3, "within": 1}}], closes) == set()
    assert run([{"key": "new_high", "params": {"days": 3, "within": 2}}], closes) == {"A"}


def test_gap_up() -> None:
    closes = {"A": [100.0, 120.0], "B": [100.0, 120.0]}
    opens = {"A": [100.0, 110.0], "B": [100.0, 102.0]}  # A 갭 +10%, B 갭 +2%
    got = run([{"key": "gap_up", "params": {"min": 5, "within": 1}}], closes, opens=opens)
    assert got == {"A"}


def test_gap_up_within() -> None:
    closes = {"A": [100.0, 120.0, 118.0], "B": [100.0, 102.0, 101.0]}
    opens = {"A": [100.0, 110.0, 119.0], "B": [100.0, 102.0, 101.0]}  # A 는 어제 +10% 갭
    assert run([{"key": "gap_up", "params": {"min": 5, "within": 1}}], closes, opens=opens) == set()
    assert run([{"key": "gap_up", "params": {"min": 5, "within": 2}}], closes, opens=opens) == {"A"}


def test_consec_up_down() -> None:
    closes = {"A": [1.0, 2.0, 3.0, 4.0], "B": [1.0, 2.0, 3.0, 3.0], "C": [4.0, 3.0, 2.0, 1.0]}
    assert run([{"key": "consec_up", "params": {"days": 3}}], closes) == {"A"}
    # days=4 는 5개 시점이 필요 → 이력 부족으로 아무도 안 걸린다.
    assert run([{"key": "consec_up", "params": {"days": 4}}], closes) == set()
    assert run([{"key": "consec_down", "params": {"days": 3}}], closes) == {"C"}


# ─────────────────────────────────────────────────────────────
# 기술적분석
# ─────────────────────────────────────────────────────────────


def test_golden_and_dead_cross() -> None:
    closes = {
        "A": [10.0, 9.0, 8.0, 7.0, 11.0, 15.0],  # MA2 가 MA3 을 5번째 봉에서 상향 돌파
        "B": [10.0, 11.0, 12.0, 12.0, 11.0, 7.0],  # MA2 가 MA3 을 5번째 봉에서 하향 돌파
    }
    p = {"short": 2, "long": 3, "within": 2}
    assert run([{"key": "golden_cross", "params": p}], closes) == {"A"}
    # A 의 돌파는 2봉 전 → within=1 로 좁히면 안 걸린다.
    assert run([{"key": "golden_cross", "params": {**p, "within": 1}}], closes) == set()
    assert run([{"key": "dead_cross", "params": p}], closes) == {"B"}
    assert run([{"key": "dead_cross", "params": {**p, "within": 1}}], closes) == set()


def test_ma_breakout_and_above_ma() -> None:
    closes = {"A": [10.0, 10.0, 10.0, 16.0], "B": [10.0, 10.0, 10.0, 10.0]}
    # A: MA3=12 < 종가 16, 전일은 종가 10 ≤ MA3 10 → 당일 상향 돌파.
    assert run([{"key": "ma_breakout", "params": {"period": 3, "within": 1}}], closes) == {"A"}
    assert run([{"key": "above_ma", "params": {"period": 3}}], closes) == {"A"}


def test_disparity_range() -> None:
    closes = {"A": [10.0, 10.0, 10.0, 16.0], "B": [10.0, 10.0, 10.0, 10.0]}
    # A: 16 / MA3(12) × 100 ≈ 133.3, B: 100.
    assert run([{"key": "disparity", "params": {"period": 3, "min": 120, "max": 140}}], closes) == {
        "A"
    }
    assert run([{"key": "disparity", "params": {"period": 3, "max": 100}}], closes) == {"B"}


def test_ma_aligned() -> None:
    up = [float(i) for i in range(1, 11)]
    closes = {"A": up, "B": up[::-1]}
    got = run([{"key": "ma_aligned", "params": {"short": 2, "mid": 3, "long": 5}}], closes)
    assert got == {"A"}


# ─────────────────────────────────────────────────────────────
# 거래량분석
# ─────────────────────────────────────────────────────────────


def test_vol_vs_prev_and_avg() -> None:
    closes = {"A": [1.0] * 4, "B": [1.0] * 4}
    volumes = {"A": [100.0, 100.0, 100.0, 500.0], "B": [100.0, 100.0, 100.0, 150.0]}
    assert run([{"key": "vol_vs_prev", "params": {"min": 3}}], closes, volumes=volumes) == {"A"}
    assert run(
        [{"key": "vol_vs_avg", "params": {"days": 3, "min": 4}}], closes, volumes=volumes
    ) == {"A"}
    assert (
        run([{"key": "vol_vs_avg", "params": {"days": 3, "min": 6}}], closes, volumes=volumes)
        == set()
    )


def test_vol_vs_prev_zero_prev_excluded() -> None:
    # 전일 거래량 0(거래정지 등)이면 배수 정의 불가 → 제외.
    closes = {"A": [1.0, 1.0]}
    volumes = {"A": [0.0, 100.0]}
    assert run([{"key": "vol_vs_prev", "params": {"min": 1}}], closes, volumes=volumes) == set()


# ─────────────────────────────────────────────────────────────
# look-ahead 가드 + 논리 결합
# ─────────────────────────────────────────────────────────────


def test_histpanel_cuts_future_rows() -> None:
    """기준일 이후 행은 패널 생성 시 잘려야 한다(look-ahead 금지, CLAUDE.md)."""
    hist_df, last_date = make_hist({"A": [100.0, 100.0, 110.0, 120.0, 999.0]})
    base_date = hist_df["Date"].sort_values().unique()[-2]  # 999.0 행은 미래
    panel = HistPanel(hist_df, base_date)
    assert panel.close.index.max() == base_date
    # 기준일 등락률 = 120/110 ≈ +9.1%. 미래 999 가 새면 +732% 가 되어 범위를 벗어난다.
    base = pd.DataFrame(
        {"Name": ["A"], "Market": "KOSPI", "Close": [120.0], "Amount": 1e9, "Marcap": 1e12},
        index=pd.Index(["A"], name="Code"),
    )
    parsed = parse_conditions([{"key": "change_range", "params": {"min": 5, "max": 15}}])
    mask = evaluate(parsed, panel, base, "and")
    assert set(mask[mask].index) == {"A"}


def test_logic_and_or() -> None:
    closes = {"A": [100.0, 110.0], "B": [100.0, 95.0]}  # A +10%, B -5%
    conds = [
        {"key": "change_range", "params": {"min": 5}},  # A 만
        {"key": "price_range", "params": {"min": 90, "max": 100}},  # B(95) 만
    ]
    assert run(conds, closes, logic="and") == set()
    assert run(conds, closes, logic="or") == {"A", "B"}


# ─────────────────────────────────────────────────────────────
# API 통합 (실데이터 필요 → slow, 없으면 skip)
# ─────────────────────────────────────────────────────────────

from api.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.layer1_data.marcap_loader import available_years  # noqa: E402

client = TestClient(app)

needs_data = pytest.mark.skipif(not available_years(), reason="marcap 데이터 없음")


@pytest.mark.slow
@needs_data
def test_api_conditions_shape() -> None:
    r = client.get("/api/conditions")
    assert r.status_code == 200
    body = r.json()
    assert [c["key"] for c in body["categories"]] == ["range", "price", "technical", "volume", "pattern"]


@pytest.mark.slow
@needs_data
def test_api_run_golden_cross_plus_amount() -> None:
    """스모크: 골든크로스 + 거래대금 조합 (지시된 검증 경로). 기간 값은 요청이 준다."""
    body = {
        "logic": "and",
        "conditions": [
            {"key": "golden_cross", "params": {"short": 5, "long": 20, "within": 5}},
            {"key": "amount_range", "params": {"min": 100}},  # 100억 이상
        ],
        "limit": 20,
    }
    r = client.post("/api/screen/run", json=body)
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"date", "total", "conditions", "avg_chg", "themes_ready", "items"}
    assert j["conditions"] == 2
    assert len(j["items"]) <= 20
    amounts = [it["amount"] for it in j["items"]]
    assert amounts == sorted(amounts, reverse=True)  # 거래대금 내림차순
    assert all(a >= 100 * 1e8 for a in amounts)
    if j["items"]:
        it = j["items"][0]
        assert set(it) == {
            "code", "name", "market", "close", "chg", "amount", "marcap", "candles", "themes",
        }
        assert all(len(c) == 4 for c in it["candles"])  # [O,H,L,C]


@pytest.mark.slow
@needs_data
def test_api_run_past_date_uses_prev_trading_day() -> None:
    """휴장일(일요일)을 주면 직전 거래일로 스냅한다 + 과거 기준일 조회가 된다."""
    body = {
        "date": "2026-03-01",  # 일요일 + 삼일절
        "conditions": [{"key": "price_range", "params": {"min": 1}}],
        "limit": 5,
    }
    r = client.post("/api/screen/run", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["date"] <= "2026-02-28"
    assert j["total"] > 0


@pytest.mark.slow
@needs_data
def test_api_run_empty_conditions_is_whole_universe() -> None:
    """조건 0개 = 전체 종목. 전략에서 종목선정을 비우면 유니버스 전체가 대상이다."""
    r = client.post("/api/screen/run", json={"conditions": [], "limit": 5})
    assert r.status_code == 200
    j = r.json()
    assert j["conditions"] == 0
    assert j["total"] > 1000  # 제외정책만 적용한 유니버스
    assert len(j["items"]) == 5

    # 조건을 하나라도 걸면 전체보다 줄어든다
    narrowed = client.post(
        "/api/screen/run",
        json={"conditions": [{"key": "amount_range", "params": {"min": 100}}], "limit": 5},
    ).json()
    assert narrowed["total"] < j["total"]


def test_api_run_errors_are_korean_400() -> None:
    r = client.post(
        "/api/screen/run", json={"conditions": [{"key": "no_such", "params": {"min": 1}}]}
    )
    assert r.status_code == 400
    assert "알 수 없는 조건" in r.json()["detail"]

    r = client.post("/api/screen/run", json={"conditions": [{"key": "price_range", "params": {}}]})
    assert r.status_code == 400
    assert "최소 1개" in r.json()["detail"]
