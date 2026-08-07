"""재무 조건 — 수익성·흑자적자·안정성·성장성 (BORB-41).

## 왜 별도 파일인가

`conditions.py` 는 이미 849줄이고, 재무는 데이터 출처가 다르다. 가격 조건은 일봉 패널
(`HistPanel`)에서 오지만 재무는 DART 공시에서 온다. **이 모듈은 `conditions.py` 를 import
하지 않는다** — 조건 객체 조립은 그쪽이 하고 여기는 계산과 명세만 준다(단방향 의존).

## as-of — 이 모듈의 존재 이유이자 가장 조심할 곳

재무제표의 **대상 기간과 발표일은 다르다.** 12월 결산 법인의 사업보고서는 이듬해 3월에야
접수된다. 2024년 실적을 2024-12-31 기준으로 쓰면 아직 세상에 없는 숫자를 미리 보는 것이다.
그래서 `dart.as_of(기준일)` 이 **접수일 기준**으로 자른 것만 넘겨준다.

## 데이터가 없는 종목

NaN 비교는 False 가 되어 자동 탈락한다. 문제는 **지금 재무 데이터가 절반뿐**이라는 것
(종목코드 089860 에서 내려받기가 끊겼다). 재무 조건을 걸면 그 뒤 종목이 통째로 빠진다 —
종목코드 순서라는 아무 의미 없는 기준으로. 과거분을 마저 받기 전에는 검색 결과를 믿으면 안 된다.
`coverage()` 가 그 사실을 화면에 알리기 위한 것이다.
"""

from __future__ import annotations

import pandas as pd

from src.layer1_data import dart

PCT = 100.0

# 분모가 0 이거나 음수면 비율이 뒤집힌다(자본잠식 기업의 ROE·부채비율). 판정 불가로 둔다.
_MIN_DENOM = 1.0


def _bounds(s: pd.Series, params: dict) -> pd.Series:
    """min/max 범위 필터. NaN 은 항상 False — 데이터 없는 종목은 자동 탈락."""
    mask = s.notna()
    if "min" in params:
        mask &= s >= params["min"]
    if "max" in params:
        mask &= s <= params["max"]
    return mask


def _figures(hist, base: pd.DataFrame) -> pd.DataFrame:
    """기준일까지 공시된 재무를 base 종목 순서에 맞춘다. 없는 종목은 NaN 행."""
    fin = dart.as_of(hist.base_date)
    if fin.empty:
        return pd.DataFrame(index=base.index)
    return fin.reindex(base.index)


def _ratio(numer: pd.Series, denom: pd.Series) -> pd.Series:
    ok = denom.notna() & (denom.abs() >= _MIN_DENOM) & (denom > 0)
    return (numer / denom * PCT).where(ok)


def _growth(now: pd.Series, before: pd.Series) -> pd.Series:
    """전기 대비 증가율(%). 전기가 0 이하면 증가율이 무의미하다 — 판정 불가."""
    ok = before.notna() & (before > _MIN_DENOM)
    return ((now - before) / before * PCT).where(ok)


# ── 조건 함수 — 시그니처는 가격 조건과 같다 (hist, base, params) → bool Series ──


def cond_operating_margin(hist, base: pd.DataFrame, p: dict) -> pd.Series:
    """영업이익률: 영업이익 ÷ 매출액."""
    f = _figures(hist, base)
    if f.empty or "영업이익" not in f:
        return pd.Series(False, index=base.index)
    return _bounds(_ratio(f["영업이익"], f["매출액"]), p)


def cond_net_margin(hist, base: pd.DataFrame, p: dict) -> pd.Series:
    """순이익률: 당기순이익 ÷ 매출액."""
    f = _figures(hist, base)
    if f.empty or "당기순이익" not in f:
        return pd.Series(False, index=base.index)
    return _bounds(_ratio(f["당기순이익"], f["매출액"]), p)


def cond_roe(hist, base: pd.DataFrame, p: dict) -> pd.Series:
    """ROE: 당기순이익 ÷ 자본총계. 자본잠식(자본총계 ≤ 0)은 판정 불가."""
    f = _figures(hist, base)
    if f.empty or "당기순이익" not in f:
        return pd.Series(False, index=base.index)
    return _bounds(_ratio(f["당기순이익"], f["자본총계"]), p)


def cond_debt_ratio(hist, base: pd.DataFrame, p: dict) -> pd.Series:
    """부채비율: 부채총계 ÷ 자본총계."""
    f = _figures(hist, base)
    if f.empty or "부채총계" not in f:
        return pd.Series(False, index=base.index)
    return _bounds(_ratio(f["부채총계"], f["자본총계"]), p)


def cond_profit_sign(hist, base: pd.DataFrame, p: dict) -> pd.Series:
    """흑자/적자: 영업이익 또는 당기순이익의 부호."""
    f = _figures(hist, base)
    label = "영업이익" if p.get("대상", "영업이익") == "영업이익" else "당기순이익"
    if f.empty or label not in f:
        return pd.Series(False, index=base.index)
    v = f[label]
    return (v > 0).where(v.notna(), False) if p.get("구분", "흑자") == "흑자" else (v < 0).where(v.notna(), False)


def cond_revenue_growth(hist, base: pd.DataFrame, p: dict) -> pd.Series:
    """매출액 증가율: 같은 보고서의 전기 금액 대비."""
    f = _figures(hist, base)
    if f.empty or "매출액_전기" not in f:
        return pd.Series(False, index=base.index)
    return _bounds(_growth(f["매출액"], f["매출액_전기"]), p)


def cond_operating_growth(hist, base: pd.DataFrame, p: dict) -> pd.Series:
    """영업이익 증가율: 같은 보고서의 전기 금액 대비. 전기가 적자면 판정 불가."""
    f = _figures(hist, base)
    if f.empty or "영업이익_전기" not in f:
        return pd.Series(False, index=base.index)
    return _bounds(_growth(f["영업이익"], f["영업이익_전기"]), p)


# ── 명세 — conditions.py 가 Condition 객체로 조립한다 ──
#
# params 항목: (key, label, type, unit, required, desc, choices)

_RANGE_DESC = "빈칸이면 그쪽 한도는 안 본다"

FINANCE_SPECS: list[dict] = [
    {
        "key": "operating_margin",
        "name": "영업이익률",
        "desc": "영업이익 ÷ 매출액. 본업으로 얼마나 남기는가.",
        "params": [
            ("min", "최소", "number", "%", False, _RANGE_DESC, ()),
            ("max", "최대", "number", "%", False, _RANGE_DESC, ()),
        ],
        "fn": cond_operating_margin,
    },
    {
        "key": "net_margin",
        "name": "순이익률",
        "desc": "당기순이익 ÷ 매출액. 세금·이자까지 빼고 남는 몫.",
        "params": [
            ("min", "최소", "number", "%", False, _RANGE_DESC, ()),
            ("max", "최대", "number", "%", False, _RANGE_DESC, ()),
        ],
        "fn": cond_net_margin,
    },
    {
        "key": "roe",
        "name": "자기자본이익률(ROE)",
        "desc": "당기순이익 ÷ 자본총계. 주주 돈으로 얼마를 벌었는가. 자본잠식 기업은 제외된다.",
        "params": [
            ("min", "최소", "number", "%", False, _RANGE_DESC, ()),
            ("max", "최대", "number", "%", False, _RANGE_DESC, ()),
        ],
        "fn": cond_roe,
    },
    {
        "key": "debt_ratio",
        "name": "부채비율",
        "desc": "부채총계 ÷ 자본총계. 낮을수록 빚이 적다. 통상 200% 이하를 안정권으로 본다.",
        "params": [
            ("min", "최소", "number", "%", False, _RANGE_DESC, ()),
            ("max", "최대", "number", "%", False, _RANGE_DESC, ()),
        ],
        "fn": cond_debt_ratio,
    },
    {
        "key": "profit_sign",
        "name": "흑자/적자",
        "desc": "가장 최근 공시된 연간 실적 기준.",
        "params": [
            ("대상", "대상", "select", "", True, "", ("영업이익", "당기순이익")),
            ("구분", "구분", "select", "", True, "", ("흑자", "적자")),
        ],
        "fn": cond_profit_sign,
    },
    {
        "key": "revenue_growth",
        "name": "매출액 증가율",
        "desc": "전년 대비. 같은 보고서에 실린 전기 금액과 비교한다.",
        "params": [
            ("min", "최소", "number", "%", False, _RANGE_DESC, ()),
            ("max", "최대", "number", "%", False, _RANGE_DESC, ()),
        ],
        "fn": cond_revenue_growth,
    },
    {
        "key": "operating_growth",
        "name": "영업이익 증가율",
        "desc": "전년 대비. 전년이 적자면 증가율이 무의미하므로 제외된다.",
        "params": [
            ("min", "최소", "number", "%", False, _RANGE_DESC, ()),
            ("max", "최대", "number", "%", False, _RANGE_DESC, ()),
        ],
        "fn": cond_operating_growth,
    },
]

FINANCE_KEYS = [spec["key"] for spec in FINANCE_SPECS]


def coverage() -> dict:
    """재무 데이터를 가진 종목이 얼마나 되는지. 화면 경고용.

    지금은 절반뿐이라(내려받기 중단) 재무 조건 결과가 종목코드 순서로 잘린다.
    """
    df = dart.load_summary()
    if df.empty:
        return {"ready": False, "codes": 0, "years": None}
    return {
        "ready": True,
        "codes": int(df["code"].nunique()),
        "years": [int(df["year"].min()), int(df["year"].max())],
    }
