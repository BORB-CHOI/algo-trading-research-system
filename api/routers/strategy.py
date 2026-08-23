from __future__ import annotations

from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.candles import full_history_adjusted, get_candles
from src.layer1_data.derived import (
    drop_halted,
)
from src.layer3_strategy import price_zones, sr_overlay, support_resistance
from src.layer3_strategy.case_overlay import (
    STRATEGIES,
    Strategy,
    parse_params,
    strategies_payload,
)

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


router = APIRouter()

# ─────────────────────────────────────────────────────────────
# 전략 카탈로그·신호·오버레이 (ADR-0009) — GET /api/strategies + POST /api/signals·/api/overlay
# 전략 정의·계산의 정본은 layer3 case_overlay.py(레지스트리)·fibonacci.py 다.
# 모든 정량 값은 요청 params 로 받는다 — 서버 기본값·하드코딩 금지(ADR-0009).
# 기존 GET /api/signals 는 제거 — 파라미터를 숨기지 않기 위해 항상 명시 전달(POST).
# ─────────────────────────────────────────────────────────────


class SignalsRequest(BaseModel):
    code: str
    strategy: str
    # 드롭다운(select) 값은 "흑자"·"자동" 같은 **말**이라 str 도 받는다 — 숫자만 받으면
    # 그런 조건·파라미터는 422 로 튕긴다(조건검색 흑자/적자도 같은 이유로 막혀 있었다).
    params: dict[str, float | int | str | None] = Field(default_factory=dict)
    start: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class OverlayRequest(BaseModel):
    code: str
    strategy: str
    # 드롭다운(select) 값은 "흑자"·"자동" 같은 **말**이라 str 도 받는다 — 숫자만 받으면
    # 그런 조건·파라미터는 422 로 튕긴다(조건검색 흑자/적자도 같은 이유로 막혀 있었다).
    params: dict[str, float | int | str | None] = Field(default_factory=dict)
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


def _get_strategy(key: str, *, need: str) -> Strategy:
    """레지스트리 조회 + 기능 지원 확인. 없으면 404, 미지원 기능이면 400."""
    strat = STRATEGIES.get(key)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"등록되지 않은 전략: {key}")
    if need == "signals" and not strat.signals:
        raise HTTPException(
            status_code=400, detail=f"'{strat.name}' 전략은 신호(signals)를 지원하지 않습니다."
        )
    if need == "overlay" and not strat.overlay:
        raise HTTPException(
            status_code=400, detail=f"'{strat.name}' 전략은 오버레이를 지원하지 않습니다."
        )
    return strat


def _parse_params_or_400(strat: Strategy, given: dict) -> dict:
    try:
        return parse_params(strat, given)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/api/strategies")
def api_strategies() -> dict:
    """전략 카탈로그 — param 스키마 형식은 조건검색(/api/conditions)과 동일(계약, ADR-0009).

    프런트가 같은 폼 코드로 전략 파라미터 UI 를 그린다. 전략은 결정론적 함수뿐이다.
    """
    return strategies_payload()


@router.post("/api/signals")
def api_signals(req: SignalsRequest) -> dict:
    """전략 신호를 차트에 얹기 위한 조회. **시각화 전용** — 주문·검증 아님.

    모든 정량 파라미터(이평 기간 등)는 요청 params 로 받는다(ADR-0009).
    """
    strat = _get_strategy(req.strategy, need="signals")
    params = _parse_params_or_400(strat, dict(req.params))
    df = get_candles(req.code, req.start, req.end, adjust=True)
    if df.empty:
        raise HTTPException(
            status_code=404, detail=f"'{req.code.strip().zfill(6)}' 구간 데이터가 없습니다."
        )
    signals = strat.signal_fn(df, params)
    return {
        "code": req.code.strip().zfill(6),
        "strategy": strat.key,
        "signals": [
            {"time": r.Date.strftime("%Y-%m-%d"), "side": r.side, "price": float(r.price)}
            for r in signals.itertuples()
        ],
    }


@router.post("/api/overlay")
def api_overlay(req: OverlayRequest) -> dict:
    """전략 오버레이(피보나치 되돌림 등) 계산. **시각화 전용** — 주문·검증 아님.

    end 기준 lookback 거래일만 계산에 쓴다. 로드 구간은 거래일 수를 여유 있게 덮도록
    달력일 ×2 + 14일로 잡는다(거래일 ≈ 달력일의 2/3 — 주말·휴장 감안, 넉넉한 상한).
    """
    strat = _get_strategy(req.strategy, need="overlay")
    params = _parse_params_or_400(strat, dict(req.params))
    if strat.full_history:
        # 파동 정의(ADR-0013 5차) — 바닥이 수년 전일 수 있다. end 를 주면 그날까지만
        # 잘라 look-ahead 를 막는다(왼쪽만 본다). 안 주면 기준일 = 최신 거래일.
        df = full_history_adjusted(req.code)
        if not df.empty and req.end:
            df = df.loc[df["Date"] <= pd.Timestamp(req.end)].reset_index(drop=True)
    else:
        lookback = strat.lookback(params) if strat.lookback is not None else 1
        end_ts = pd.Timestamp(req.end) if req.end else pd.Timestamp.now().normalize()
        start = (end_ts - pd.Timedelta(days=lookback * 2 + 14)).strftime("%Y-%m-%d")
        df = get_candles(req.code, start, req.end, adjust=True)
    if df.empty:
        raise HTTPException(
            status_code=404, detail=f"'{req.code.strip().zfill(6)}' 구간 데이터가 없습니다."
        )
    try:
        result = strat.overlay_fn(df, params)  # 베이스 못 찾음 등 → ValueError(한국어)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"code": req.code.strip().zfill(6), "strategy": strat.key, **result}


def _visible_bars(df: pd.DataFrame, start: str | None, fallback: int) -> int:
    """화면에 보이는 구간이 **일봉 몇 개**인가.

    차트 도구(지지저항·오더블록·가격 빈틈)는 전부 일봉으로 계산한다. 화면이 주봉·월봉이면
    보이는 봉 개수와 일봉 개수가 다르므로, 왼쪽 끝 봉의 날짜를 받아 실제 일봉 수를 센다.
    `start` 가 없으면(옛 호출·일봉) 넘어온 봉 수를 그대로 쓴다.
    """
    if not start:
        return fallback
    n = int((df["Date"] >= pd.Timestamp(start)).sum())
    return max(n, 1)


@router.get("/api/support-resistance")
def api_support_resistance(
    code: str = Query(..., description="종목코드 6자리"),
    end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="기준일"),
    prd: int = Query(..., ge=1, description="고점·저점 잡는 폭(좌우 N봉)"),
    width_pct: float = Query(..., gt=0, description="한 자리로 묶는 폭 — 그 자리 가격 대비 %"),
    bars: int = Query(..., ge=1, description="거슬러 볼 봉 수 = 화면에 보이는 봉 수(일봉 기준)"),
    start: str | None = Query(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="화면 **왼쪽 끝** 봉의 날짜. 주면 bars 대신 이 날짜부터 본다",
    ),
    min_turns: int = Query(..., ge=1, description="최소로 닿아야 하는 횟수"),
    source: str = Query(
        default=support_resistance.SEED_ALL,
        description="자리 후보 — '고가·저가 전부'(기본) | '꺾임점'",
    ),
    max_lines: int | None = Query(
        default=None, ge=1, description="남길 자리 수. 안 주면 보이는 봉 안의 자리를 전부 그린다"
    ),
) -> dict:
    """지지저항 — **차트 기능**이지 전략이 아니다 (오너 2026-08-09).

    거래량·MACD 처럼 도구 막대에서 켜고 끈다. 그래서 `/api/strategies` 카탈로그에 없다.
    피보나치 되돌림 기법의 지지저항과는 계산이 다르다 — 그쪽은 피보나치 선 위아래 밴드
    안에서만 찾고, 이쪽은 화면 전체에서 찾는다.

    `end` 오른쪽은 보지 않는다(미래 데이터 훔쳐보기 금지). 안 주면 최신 거래일이 기준.

    **`start` 를 왜 받나 (2026-08-09).** 이 계산은 언제나 일봉으로 한다. 그런데 화면이
    주봉·월봉이면 "보이는 봉 200개"가 일봉 200개가 아니라 200주·200달이다. `bars` 만
    받던 때는 2010~2026 이 보이는 월봉 화면에 **최근 200일** 자리를 그려서, 선이 오른쪽
    끝 몇 봉에만 몰렸다(오너 지적 2026-08-09: "지지저항 고장났네"). 화면 왼쪽 끝 봉의
    날짜를 그대로 받으면 어떤 주기에서도 보이는 구간과 정확히 같아진다.
    """
    df = full_history_adjusted(code)
    if not df.empty and end:
        df = df.loc[df["Date"] <= pd.Timestamp(end)].reset_index(drop=True)
    df = drop_halted(df)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"'{code.strip().zfill(6)}' {end or '전체'} 까지 데이터가 없습니다.",
        )
    try:
        result = sr_overlay.compute_overlay(
            df,
            {
                "sr_prd": prd,
                "sr_channel_width_pct": width_pct,
                "sr_loopback": _visible_bars(df, start, bars),
                "sr_min_strength": min_turns,
                "sr_source": source,
                "sr_max_channels": max_lines,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"code": code.strip().zfill(6), **result}


@router.get("/api/price-zones")
def api_price_zones(
    code: str = Query(..., description="종목코드 6자리"),
    kind: str = Query(..., description="오더블록 | 가격 빈틈"),
    end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="기준일"),
    bars: int = Query(..., ge=1, description="볼 봉 수 = 화면에 보이는 봉 수(일봉 기준)"),
    start: str | None = Query(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="화면 **왼쪽 끝** 봉의 날짜. 주면 bars 대신 이 날짜부터 본다",
    ),
    push_pct: float = Query(..., gt=0, description="오더블록: 몸통이 이만큼(%) 움직여야"),
    min_gap_pct: float = Query(..., gt=0, description="빈틈: 이만큼(%) 이상 벌어져야"),
    lookback_bars: int = Query(..., ge=1, description="오더블록: 반대색 봉을 몇 봉 뒤까지"),
    alive_only: bool = Query(
        default=False, description="켜면 이미 지나간 자리를 뺀다. 기본은 흐리게라도 다 보여준다"
    ),
) -> dict:
    """오더블록 · 가격 빈틈(FVG) — **차트 기능** (오너 2026-08-09).

    지지저항(`/api/support-resistance`)과 **따로** 켜고 끈다. 셋이 서로 다른 자리를
    짚기 때문이다(ADR-0014 5차 개정의 실측 표 참조).

    `end` 오른쪽은 보지 않는다. 빈틈은 세 번째 봉이, 오더블록은 밀어낸 봉이 나와야
    보이므로 구조적으로도 미래를 못 본다.

    `start` 는 지지저항과 같은 뜻이다 — 주봉·월봉 화면에서 구간이 어긋나는 걸 막는다
    (`_visible_bars` 주석 참조).
    """
    if kind not in (price_zones.ORDER_BLOCK, price_zones.FAIR_VALUE_GAP):
        raise HTTPException(
            status_code=400,
            detail=f"모르는 종류입니다: {kind!r} "
            f"(쓸 수 있는 값: {price_zones.ORDER_BLOCK}, {price_zones.FAIR_VALUE_GAP})",
        )
    df = full_history_adjusted(code)
    if not df.empty and end:
        df = df.loc[df["Date"] <= pd.Timestamp(end)].reset_index(drop=True)
    df = drop_halted(df)
    df = df.tail(_visible_bars(df, start, bars)).reset_index(drop=True)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"'{code.strip().zfill(6)}' {end or '전체'} 까지 데이터가 없습니다.",
        )
    p = {
        "zone_push_pct": push_pct,
        "zone_min_gap_pct": min_gap_pct,
        "zone_lookback_bars": lookback_bars,
        "zone_alive_only": alive_only,
    }
    try:
        params = price_zones.zone_params_from(p)
        finder = (
            price_zones.find_order_blocks
            if kind == price_zones.ORDER_BLOCK
            else price_zones.find_fair_value_gaps
        )
        zones = finder(df, params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    lines = [
        {
            "price": z.mid,
            "label": price_zones.zone_label(z),
            "kind": "ob" if z.kind == price_zones.ORDER_BLOCK else "fvg",
            "top": z.top,
            "bottom": z.bottom,
            # 이미 지나간 자리는 화면에서 흐리게 그린다 (오너 2026-08-09: "일단 보이게 해봐")
            "dim": not z.alive,
            # 그 자리가 **생긴 날**. 화면에서 이 봉부터 오른쪽으로만 띠를 그린다 —
            # 오더블록·빈틈은 생기기 전 과거엔 존재하지 않던 자리다(표준 지표도 그렇게 그린다).
            "start": z.date.strftime("%Y-%m-%d"),
        }
        for z in zones
    ]
    return {
        "code": code.strip().zfill(6),
        "anchors": {
            "low_date": df["Date"].iloc[0].strftime("%Y-%m-%d"),
            "high_date": df["Date"].iloc[-1].strftime("%Y-%m-%d"),
            "low_price": float(df["Low"].min()),
            "high_price": float(df["High"].max()),
            "confirmed": bool(zones),
            "falling": False,
        },
        "lines": lines,
        "touches": [],
    }
