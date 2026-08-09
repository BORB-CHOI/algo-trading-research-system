"""재무 조건 테스트 (BORB-41).

실제 parquet 을 타지 않는다 — 요약 테이블을 주입해 판정 규칙만 본다.
가장 중요한 것은 **as-of**: 아직 공시되지 않은 실적이 조건에 새어들면 백테스트가 통째로 무효다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer1_data import dart
from src.layer3_strategy import conditions as C
from src.layer3_strategy import conditions_finance as F

# 종목 A: 2024년 실적(2025-03-15 공시) · 2025년 실적(2026-03-15 공시)
# 종목 B: 적자 · 자본잠식 — 비율 계산이 뒤집히는 자리
SUMMARY = pd.DataFrame(
    [
        {
            "code": "AAAAAA",
            "year": 2024,
            "disclosed": pd.Timestamp("2025-03-15"),
            "fs_div": "CFS",
            "매출액": 1000.0,
            "영업이익": 200.0,
            "당기순이익": 150.0,
            "자산총계": 3000.0,
            "부채총계": 1000.0,
            "자본총계": 2000.0,
            "매출액_전기": 800.0,
            "영업이익_전기": 100.0,
        },
        {
            "code": "AAAAAA",
            "year": 2025,
            "disclosed": pd.Timestamp("2026-03-15"),
            "fs_div": "CFS",
            "매출액": 2000.0,
            "영업이익": 100.0,
            "당기순이익": 80.0,
            "자산총계": 3500.0,
            "부채총계": 1200.0,
            "자본총계": 2300.0,
            "매출액_전기": 1000.0,
            "영업이익_전기": 200.0,
        },
        {
            "code": "BBBBBB",
            "year": 2024,
            "disclosed": pd.Timestamp("2025-03-20"),
            "fs_div": "OFS",
            "매출액": 500.0,
            "영업이익": -50.0,
            "당기순이익": -80.0,
            "자산총계": 900.0,
            "부채총계": 1000.0,
            "자본총계": -100.0,
            "매출액_전기": 400.0,
            "영업이익_전기": -20.0,
        },
    ]
)


class _Panel:
    """조건 함수가 쓰는 건 base_date 뿐이다."""

    def __init__(self, base_date: str) -> None:
        self.base_date = pd.Timestamp(base_date)


@pytest.fixture(autouse=True)
def _inject_summary(monkeypatch):
    monkeypatch.setattr(dart, "_summary_cache", SUMMARY.copy())
    monkeypatch.setattr(dart, "load_summary", lambda force=False: SUMMARY.copy())


def _base(*codes: str) -> pd.DataFrame:
    return pd.DataFrame({"Close": [1.0] * len(codes)}, index=list(codes))


def _run(key: str, params: dict, base_date: str, codes=("AAAAAA", "BBBBBB")) -> pd.Series:
    return C.CONDITIONS[key].fn(_Panel(base_date), _base(*codes), params)


# ── as-of — 공시 전 실적은 절대 보이면 안 된다 ──────────────


def test_공시_전날에는_그_실적이_안_보인다():
    # 2024년 실적은 2025-03-15 공시. 하루 전에는 존재하지 않는 숫자다.
    fin = dart.as_of("2025-03-14")
    assert "AAAAAA" not in fin.index


def test_공시일_당일부터_보인다():
    fin = dart.as_of("2025-03-15")
    assert int(fin.loc["AAAAAA", "year"]) == 2024


def test_기준일이_지나면_더_최신_실적으로_바뀐다():
    assert int(dart.as_of("2026-01-01").loc["AAAAAA", "year"]) == 2024  # 2025년 실적은 아직 미공시
    assert int(dart.as_of("2026-03-15").loc["AAAAAA", "year"]) == 2025


def test_기준일에_따라_판정이_뒤집힌다():
    # 2024년 영업이익률 20%, 2025년은 5%. "15% 이상" 은 기준일에 따라 갈려야 한다.
    assert _run("operating_margin", {"min": 15}, "2025-06-01")["AAAAAA"]
    assert not _run("operating_margin", {"min": 15}, "2026-06-01")["AAAAAA"]


# ── 비율 계산 ────────────────────────────────────────────


def test_영업이익률():
    assert _run("operating_margin", {"min": 19, "max": 21}, "2025-06-01")["AAAAAA"]


def test_순이익률():
    assert _run("net_margin", {"min": 14, "max": 16}, "2025-06-01")["AAAAAA"]  # 150/1000


def test_ROE():
    assert _run("roe", {"min": 7, "max": 8}, "2025-06-01")["AAAAAA"]  # 150/2000 = 7.5%


def test_부채비율():
    assert _run("debt_ratio", {"max": 50}, "2025-06-01")["AAAAAA"]  # 1000/2000 = 50%


def test_자본잠식이면_비율_판정을_하지_않는다():
    # 자본총계가 음수면 ROE·부채비율이 부호째 뒤집힌다. 통과시키면 안 된다.
    assert not _run("roe", {"min": -9999}, "2025-06-01")["BBBBBB"]
    assert not _run("debt_ratio", {"max": 9999}, "2025-06-01")["BBBBBB"]


# ── 흑자/적자 ────────────────────────────────────────────


def test_흑자_적자_구분():
    assert _run("profit_sign", {"대상": "영업이익", "구분": "흑자"}, "2025-06-01")["AAAAAA"]
    assert _run("profit_sign", {"대상": "영업이익", "구분": "적자"}, "2025-06-01")["BBBBBB"]
    assert not _run("profit_sign", {"대상": "영업이익", "구분": "흑자"}, "2025-06-01")["BBBBBB"]


def test_대상을_당기순이익으로_바꿀_수_있다():
    assert _run("profit_sign", {"대상": "당기순이익", "구분": "적자"}, "2025-06-01")["BBBBBB"]


# ── 증가율 ──────────────────────────────────────────────


def test_매출액_증가율():
    assert _run("revenue_growth", {"min": 24, "max": 26}, "2025-06-01")["AAAAAA"]  # 800→1000


def test_전기가_적자면_증가율은_판정_불가():
    # -20 → -50 을 "증가율"로 환산하면 부호가 뒤집혀 엉뚱한 값이 된다.
    assert not _run("operating_growth", {"min": -9999}, "2025-06-01")["BBBBBB"]


# ── 데이터 없는 종목 ─────────────────────────────────────


def test_재무가_없는_종목은_탈락한다():
    result = _run("operating_margin", {"min": 0}, "2025-06-01", codes=("AAAAAA", "ZZZZZZ"))
    assert result["AAAAAA"]
    assert not result["ZZZZZZ"]


def test_요약_테이블이_비면_전부_탈락():
    dart.load_summary = lambda force=False: pd.DataFrame()  # noqa: ARG005
    result = F.cond_operating_margin(_Panel("2025-06-01"), _base("AAAAAA"), {"min": 0})
    assert not result.any()


# ── select 파라미터 파싱 ─────────────────────────────────


def test_select_파라미터를_숫자로_강요하지_않는다():
    parsed = C.parse_conditions(
        [{"key": "profit_sign", "params": {"대상": "영업이익", "구분": "흑자"}}]
    )
    assert parsed[0][1] == {"대상": "영업이익", "구분": "흑자"}


def test_허용되지_않은_선택지는_거부한다():
    with pytest.raises(ValueError, match="흑자"):
        C.parse_conditions(
            [{"key": "profit_sign", "params": {"대상": "영업이익", "구분": "반흑자"}}]
        )
