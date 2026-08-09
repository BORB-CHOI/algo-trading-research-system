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

from src.layer3_strategy import base_breakout, fib_zone, fibonacci, support_resistance, zigzag

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


def _check_sr(p: dict) -> None:
    """지지저항 파라미터 검증."""
    if p["sr_prd"] < 1:
        raise ValueError("고점·저점 잡는 폭(sr_prd)은 1 이상의 봉이어야 합니다.")
    if p["sr_loopback"] < 1:
        raise ValueError("거슬러 볼 봉 수(sr_loopback)는 1 이상이어야 합니다.")
    if p["sr_min_strength"] < 1:
        raise ValueError("최소 부딪힌 횟수(sr_min_strength)는 1 이상이어야 합니다.")
    if float(p["sr_channel_width_pct"]) <= 0:
        raise ValueError("한 자리로 묶는 폭(sr_channel_width_pct)은 0보다 커야 합니다.")
    if str(p["sr_source"]) not in support_resistance.SEED_SOURCES:
        raise ValueError(f"모르는 자리 후보입니다: {p['sr_source']!r}")
    if float(p["sr_round_max_gap_pct"]) <= 0:
        raise ValueError("주문가가 선에서 떨어져도 되는 폭(%)은 0보다 커야 합니다.")


def _check_fib(p: dict) -> None:
    # 꺾임점·띠 폭 파라미터는 계산 모듈이 직접 검증한다 — 규칙이 두 군데로 갈라지지 않게.
    zigzag.validate(zigzag.zigzag_params_from(p))
    fib_zone.validate_band(fib_zone.band_params_from(p))
    if str(p["start_mode"]) not in base_breakout.START_MODES:
        raise ValueError(f"모르는 시작점 방식입니다: {p['start_mode']!r}")
    base_breakout.validate_box(base_breakout.box_params_from(p))
    _check_sr(p)


# ─────────────────────────────────────────────────────────────
# 레지스트리 — /api/strategies 계약과 1:1 (등록 순서 = 응답 순서)
# ─────────────────────────────────────────────────────────────

# 지지저항 파라미터는 두 전략이 **같은 목록**을 쓴다 — 한쪽만 고치면 같은 이름의 값이
# 화면마다 다른 뜻이 된다. 라벨은 쉬운 말로 (CLAUDE.md).
_SR_PARAMS: tuple[Param, ...] = (
    Param(
        "sr_source",
        "자리 후보",
        "select",
        "",
        required=True,
        choices=support_resistance.SEED_SOURCES,
        desc="고가·저가 전부 = 모든 봉의 고가·저가 / 꺾임점 = 좌우 N봉에서 제일 높거나 낮은 값만",
    ),
    Param(
        "sr_prd",
        "고점·저점 잡는 폭",
        "int",
        "봉",
        required=True,
        desc="좌우로 이만큼 봐서 제일 높으면 고점, 제일 낮으면 저점으로 친다",
    ),
    Param(
        "sr_scope",
        "어디서 찾을까",
        "select",
        "",
        required=True,
        choices=fib_zone.SR_SCOPES,
        desc="파동 구간 = 파동 바닥 이후만 / 최근 N봉 = 아래 봉 수만큼 / 전체 = 다 본다",
    ),
    Param(
        "sr_loopback",
        "몇 봉 거슬러 볼까",
        "int",
        "봉",
        required=True,
        desc="'최근 N봉'을 골랐을 때만 쓴다. 길게 잡으면 지금과 가격대가 다른 옛날 자리가 섞인다",
    ),
    Param(
        "sr_channel_width_pct",
        "한 자리로 묶는 폭",
        "float",
        "%",
        required=True,
        desc="이만큼 안에 있는 고가·저가는 같은 자리로 본다. 그 자리 가격 대비 %다 "
        "(2%면 5만원에서 1,000원, 250만원에서 5만원). 차트 기능과 같은 뜻",
    ),
    Param(
        "sr_min_strength",
        "몇 번은 닿아야",
        "int",
        "번",
        required=True,
        desc="그 자리에 닿은 봉 수. 올리면 확실한 자리만 남는다",
    ),
    Param(
        "sr_round_max_gap_pct",
        "주문가가 선에서 떨어져도 되는 폭",
        "float",
        "%",
        required=True,
        desc="자리 안에 라운드 가격이 여럿이면 굵은 숫자를 먼저 고른다. "
        "단 되돌림 선에서 이만큼 넘게 떨어진 값은 뺀다 — 자리 맨 끝의 굵은 값이 이기는 걸 막는다",
    ),
)

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
        "바닥에서 꼭대기까지 오른 구간에 피보나치를 긋고, 각 선 위아래로 밴드를 그린 뒤 "
        "그 안에서 지지저항을 찾는다. 주문가는 그 지지저항 안의 라운드 가격이다. "
        "③ 시뮬레이션·④ 백테스팅과 같은 계산 (ADR-0013 5차·0014 2차 개정)",
        signals=False,
        overlay=True,
        params=(
            Param(
                "start_mode",
                "시작점 잡는 법",
                "select",
                "",
                required=True,
                choices=base_breakout.START_MODES,
                desc="평평한 구간 돌파 = 옆으로 기던 구간을 거래대금이 늘며 뚫고 올라간 날 / "
                "상승 전환 = 값이 직전 꼭대기를 넘어선 때의 바닥(옛 방식)",
            ),
            Param(
                "start_box_bars",
                "평평한 구간으로 볼 봉 수",
                "int",
                "봉",
                required=True,
                desc="돌파일 직전 이만큼을 한 구간으로 본다. 그 구간의 한가운데가 시작 가격",
            ),
            Param(
                "start_volume_mult",
                "돌파한 날 거래대금",
                "number",
                "배",
                required=True,
                desc="그 구간 평균의 몇 배여야 '힘이 실린 돌파'로 볼지",
            ),
            Param(
                "start_keep_mult",
                "돌파 뒤 거래대금",
                "number",
                "배",
                required=True,
                desc="돌파 뒤 같은 봉 수 동안의 평균. 하루짜리 급증을 걸러낸다",
            ),
            Param(
                "zz_depth",
                "파동 꼭대기·바닥 잡는 폭",
                "int",
                "봉",
                required=True,
                desc="이 숫자의 절반만큼 좌우를 봐서 제일 높으면 꼭대기, 제일 낮으면 바닥",
            ),
            Param(
                "zz_deviation_mode",
                "작은 출렁임 무시 — 기준",
                "select",
                "",
                required=True,
                choices=("자동", "고정"),
                desc="자동 = 그 종목이 하루에 움직이는 폭에 맞춰 / 고정 = 내가 정한 %",
            ),
            Param(
                "zz_deviation",
                "작은 출렁임 무시 — 크기",
                "number",
                "",
                required=True,
                desc="이만큼보다 작게 움직인 건 파동으로 안 친다. 올릴수록 큰 파동만 남는다",
            ),
            Param(
                "fib_band_mode",
                "선 위아래 밴드 — 기준",
                "select",
                "",
                required=True,
                choices=tuple(fib_zone.BAND_MODES),
                desc="자동 = 하루에 움직이는 폭 기준 / 파동폭 = 바닥~꼭대기 폭 기준 / 가격 = 그 선 가격 기준",
            ),
            Param(
                "fib_band_value",
                "선 위아래 밴드 — 크기",
                "number",
                "",
                required=True,
                desc="피보나치 선 위아래로 이만큼씩 벌린다. 그 안에서만 지지저항을 찾는다",
            ),
            *_SR_PARAMS,
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
                        # 입력칸 아래 흐린 설명문. 선택지가 있으면 드롭다운을 그린다.
                        # (/api/conditions 와 같은 형식 — 프런트가 한 벌의 폼 코드로 그린다.)
                        "desc": p.desc,
                        "choices": list(p.choices),
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
    - "select" 파라미터는 choices 안의 값만 (숫자로 바꾸지 않는다 — 값이 한국어 말이다)
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
        if p.type == "select":
            s = str(v)
            if p.choices and s not in p.choices:
                raise ValueError(
                    f"전략 '{strat.name}': '{p.label}' 값은 {' / '.join(p.choices)} 중 하나여야 합니다."
                )
            params[p.key] = s
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
