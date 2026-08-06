"""전략 카탈로그 — 조건검색(conditions.py)과 같은 레지스트리 구조 (ADR-0009, BORB-42).

전략 = {key, name, desc, signals/overlay 지원 여부, 파라미터 스키마, 계산 함수}.
GET /api/strategies 가 이 카탈로그를 그대로 내려주고, 프런트는 조건검색과 **동일한
param 스키마 형식**으로 폼을 그린다 — 오너가 UI 에서 보고 수정할 수 있어야 한다.

## 원칙 (ADR-0009, CLAUDE.md)

- **모든 정량 값은 요청 파라미터로만** 받는다. 서버 코드에 전략 숫자 하드코딩 금지.
  (기존 ma_cross 5/20 고정, "ma_cross_5_20_예시" 명칭은 이 원칙 위반으로 폐기.)
- 여기 신호·오버레이는 케이스 탐색용 **시각화**다. BUY/SELL·포지션·주문 결정 없음.
  성과 검증은 가드레일 백테스트(BORB-31)가 한다. 등록 전략은 전부 결정론적 계산이다.
- 새 전략 추가 = 계산 함수 1개 + 여기 Strategy 등록. 숨은 숫자가 생길 자리가 없다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from src.layer3_strategy import fibonacci

# 파라미터 스키마는 조건검색과 같은 자료형을 재사용한다 — 응답 형식이 하나로 유지된다.
from src.layer3_strategy.conditions import Param

# 신호 함수: (일봉 df, 검증된 params) → 신호 행만 담은 DataFrame(Date, side['buy'|'sell'], price).
SignalFn = Callable[[pd.DataFrame, dict], pd.DataFrame]
# 오버레이 함수: (일봉 df, 검증된 params) → {"anchors","lines","touches"} dict (API 계약 형식).
OverlayFn = Callable[[pd.DataFrame, dict], dict]


@dataclass(frozen=True)
class Strategy:
    """전략 메타 + 계산 함수 — /api/strategies 응답 항목과 1:1."""

    key: str
    name: str
    desc: str
    signals: bool  # POST /api/signals 지원 여부
    overlay: bool  # POST /api/overlay 지원 여부
    params: tuple[Param, ...]
    signal_fn: SignalFn | None = None
    overlay_fn: OverlayFn | None = None
    # 오버레이 데이터 로드에 필요한 거래일 수(파라미터 의존). API 가 로드 구간 계산에 쓴다.
    lookback: Callable[[dict], int] | None = None
    # 파라미터 상호 검증 (short<long 등). 실패 시 ValueError(한국어 메시지).
    validate: Callable[[dict], None] | None = None
    # 전체 이력이 필요한 전략 (사이클 정의, ADR-0013 — 저점이 수년 전 바닥일 수 있다).
    full_history: bool = False


# ─────────────────────────────────────────────────────────────
# 이평 교차 (예시) — 배관 검증용. 기간은 항상 요청에서 받는다.
# ─────────────────────────────────────────────────────────────


def _crosses(fast: pd.Series, slow: pd.Series) -> tuple[pd.Series, pd.Series]:
    """골든/데드 크로스 시점. 전일은 아래(위)였는데 오늘 위(아래)로 바뀐 날."""
    above = fast > slow
    golden = above & ~above.shift(1, fill_value=False)
    dead = ~above & above.shift(1, fill_value=True)
    # 이동평균이 아직 없는 앞 구간은 신호로 치지 않는다.
    valid = fast.notna() & slow.notna() & fast.shift(1).notna() & slow.shift(1).notna()
    return golden & valid, dead & valid


def ma_cross_signals(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    """이평 교차 **예시** — 확정 전략이 아니다. 기간(short/long)은 요청 파라미터."""
    fast = df["Close"].rolling(p["short"]).mean()
    slow = df["Close"].rolling(p["long"]).mean()
    golden, dead = _crosses(fast, slow)
    out = pd.DataFrame(
        {
            "Date": df["Date"],
            "side": pd.Series(pd.NA, index=df.index, dtype="object"),
            "price": df["Close"],
        }
    )
    out.loc[golden, "side"] = "buy"
    out.loc[dead, "side"] = "sell"
    return out.dropna(subset=["side"]).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# 파라미터 상호 검증
# ─────────────────────────────────────────────────────────────


def _check_ma_cross(p: dict) -> None:
    if p["short"] >= p["long"]:
        raise ValueError("단기 이평 기간(short)은 장기(long)보다 짧아야 합니다.")


def _check_fib(p: dict) -> None:
    if not 0 < p["drop_pct"] < 100:
        raise ValueError("사이클 하락 기준(drop_pct)은 0과 100 사이(%)여야 합니다.")
    if p["sr_span"] < 1:
        raise ValueError("피벗 기준(sr_span)은 1 이상의 거래일이어야 합니다.")
    if p["sr_cluster_pct"] <= 0:
        raise ValueError("선 군집 폭(sr_cluster_pct)은 0보다 커야 합니다.")


# ─────────────────────────────────────────────────────────────
# 레지스트리 — /api/strategies 계약과 1:1 (등록 순서 = 응답 순서)
# ─────────────────────────────────────────────────────────────

_ALL = [
    Strategy(
        "ma_cross",
        "이평 교차 (예시)",
        "단기 이평이 장기 이평을 상향/하향 교차 → 매수/매도",
        signals=True,
        overlay=False,
        params=(
            Param("short", "단기", "int", "일", required=True),
            Param("long", "장기", "int", "일", required=True),
        ),
        signal_fn=ma_cross_signals,
        validate=_check_ma_cross,
    ),
    Strategy(
        "fib_retrace",
        "피보나치 되돌림 (전략 1호)",
        "상승장 사이클(저점→고점) 피보나치 레벨 + 지지/저항선 — ③ 시뮬레이션과 같은 파동 (ADR-0013·0014)",
        signals=False,
        overlay=True,
        params=(
            Param("drop_pct", "사이클 하락 기준", "number", "%", required=True),
            Param("sr_span", "피벗 기준", "int", "일", required=True),
            Param("sr_cluster_pct", "선 군집 폭", "number", "%", required=True),
        ),
        overlay_fn=fibonacci.compute_overlay,
        validate=_check_fib,
        full_history=True,
    ),
]

STRATEGIES: dict[str, Strategy] = {s.key: s for s in _ALL}


def strategies_payload() -> dict:
    """GET /api/strategies 응답 본문. param 스키마 형식은 /api/conditions 와 동일(계약)."""
    return {
        "strategies": [
            {
                "key": s.key,
                "name": s.name,
                "desc": s.desc,
                "signals": s.signals,
                "overlay": s.overlay,
                "params": [
                    {
                        "key": p.key,
                        "label": p.label,
                        "type": p.type,
                        "unit": p.unit,
                        "required": p.required,
                    }
                    for p in s.params
                ],
            }
            for s in _ALL
        ]
    }


def parse_params(strat: Strategy, given: dict | None) -> dict:
    """요청 params 를 검증·정규화한다. 문제가 있으면 ValueError(한국어 메시지).

    conditions.parse_conditions 와 같은 규칙:
    - 모르는 파라미터 → 오류 (계약 위반을 조용히 넘기지 않는다)
    - required 누락 → 오류 — 서버가 기본값으로 메꾸지 않는다(ADR-0009)
    - "int" 파라미터는 1 이상 정수만
    """
    given = given or {}
    unknown = set(given) - {p.key for p in strat.params}
    if unknown:
        raise ValueError(f"전략 '{strat.name}': 알 수 없는 파라미터 {sorted(unknown)}")
    params: dict = {}
    for p in strat.params:
        v = given.get(p.key)
        if v is None:
            if p.required:
                raise ValueError(f"전략 '{strat.name}': 필수 파라미터 '{p.label}'({p.key}) 누락")
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"전략 '{strat.name}': '{p.key}' 값은 숫자여야 합니다.") from None
        if p.type == "int":
            if not fv.is_integer():
                raise ValueError(f"전략 '{strat.name}': '{p.key}' 값은 정수여야 합니다.")
            if fv < 1:
                raise ValueError(f"전략 '{strat.name}': '{p.key}' 값은 1 이상이어야 합니다.")
            params[p.key] = int(fv)
        else:
            params[p.key] = fv
    if strat.validate is not None:
        strat.validate(params)
    return params
