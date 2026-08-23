from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.candles import candle_map, change_vs_prev, load_year_screen, load_year_slim
from api.notes import data_notes
from src.layer1_data.exclusions import DEFAULT_POLICY, apply_exclusions
from src.layer1_data.marcap_loader import available_years
from src.layer1_data.themes import theme_map
from src.layer3_strategy import conditions as cond_registry

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


router = APIRouter()

# ─────────────────────────────────────────────────────────────
# 조건검색 (키움 [0150] 방식) — GET /api/conditions + POST /api/screen/run
# 조건 정의·계산의 정본은 layer3 conditions.py 다. 여기는 데이터 로드와 응답 조립만 한다.
# ─────────────────────────────────────────────────────────────

# 조건 계산에 필요한 일봉 컬럼 (룩백 패널용). 캔들 캐시(load_year_slim)에서 잘라 쓴다.
# High/Low 는 패턴분석(TA-Lib), Stocks 는 수정주가 back-adjust(ADR-0006)용.
_HIST_COLS = ["Date", "Code", "Open", "High", "Low", "Close", "Volume", "Stocks"]


class ConditionSpec(BaseModel):
    key: str
    # 드롭다운(select) 값은 "흑자"·"자동" 같은 **말**이라 str 도 받는다 — 숫자만 받으면
    # 그런 조건·파라미터는 422 로 튕긴다(조건검색 흑자/적자도 같은 이유로 막혀 있었다).
    params: dict[str, float | int | str | None] = Field(default_factory=dict)


class ScreenRunRequest(BaseModel):
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    logic: Literal["and", "or"] = "and"
    conditions: list[ConditionSpec] = Field(default_factory=list)
    limit: int = Field(100, ge=1, le=200)


@router.get("/api/conditions")
def api_conditions() -> dict:
    """조건검색 조건 목록 — 프런트가 이 메타로 조건식 UI 를 그린다(계약 고정).

    `data_notes` 는 조건을 고르기 전에 알아야 할 데이터 사실이다 — 고치지 않고 알린다
    (지침서 §5.5 알려진 구멍). 지금은 수급 결손 하나뿐이다.
    """
    payload = cond_registry.categories_payload()
    payload["data_notes"] = data_notes()
    return payload


def _load_history_panel(
    year: int, years: list[int], base_date: pd.Timestamp, lookback: int, codes: set[str]
) -> pd.DataFrame:
    """기준일 이하 최근 (lookback+1) 거래일의 일봉 패널(long 형).

    당해 연도만으로 거래일이 모자라면 전년도까지 로드한다(연도 경계).
    기준일 이후 행은 여기서 한 번, HistPanel 생성자에서 또 한 번 잘린다(look-ahead 금지).
    """
    frames = [load_year_slim(year)]
    n_dates = frames[0].loc[frames[0]["Date"] <= base_date, "Date"].nunique()
    # 연간 거래일은 ~242일 — 룩백 260 이면 전년도 하나로도 모자랄 수 있어 채워질 때까지 거슬러 간다.
    y = year - 1
    while n_dates < lookback + 1 and y in years:
        prev = load_year_slim(y)
        frames.append(prev)
        n_dates += prev["Date"].nunique()
        y -= 1
    hist = pd.concat(frames, ignore_index=True)[_HIST_COLS]
    hist = hist[(hist["Date"] <= base_date) & hist["Code"].isin(codes)]
    keep = hist["Date"].drop_duplicates().sort_values().iloc[-(lookback + 1) :]
    return hist[hist["Date"].isin(keep)]


@router.post("/api/screen/run")
def api_screen_run(req: ScreenRunRequest) -> dict:
    """조건검색 실행 (키움 [0150] 방식). **조회·시각화 전용** — 주문·매매 판단 아님.

    임계값·지표 기간은 전부 요청에서 받는다 — 서버 기본값 금지(CLAUDE.md placeholder 원칙).
    """
    # 조건이 비면 "전체 종목"이다 — 제외정책만 적용한 유니버스를 그대로 돌려준다.
    parsed: cond_registry.Parsed = []
    if req.conditions:
        try:
            parsed = cond_registry.parse_conditions([c.model_dump() for c in req.conditions])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    years = available_years()
    if not years:
        raise HTTPException(status_code=503, detail="marcap 데이터가 없습니다.")
    year = min(int(req.date[:4]), years[-1]) if req.date else years[-1]
    if year not in years:
        raise HTTPException(status_code=503, detail=f"{year}년 데이터가 없습니다.")

    df = load_year_screen(year)
    if req.date:
        df = df[df["Date"] <= pd.Timestamp(req.date)]
    else:
        # 기본 기준일 = 마지막 완결 거래일. 장중(오늘) 보충 데이터로 조건을 평가하면
        # 거래대금 등이 반나절치라 결과가 왜곡된다 — 오너 지시(2026-08-05): 전날 기준.
        df = df[df["Date"] < pd.Timestamp.today().normalize()]
    if df.empty:
        raise HTTPException(status_code=503, detail=f"{req.date} 이전 거래일 데이터가 없습니다.")
    base_date = df["Date"].max()  # 기준일이 휴장일이면 직전 거래일로
    base = apply_exclusions(df[df["Date"] == base_date], DEFAULT_POLICY).set_index("Code")

    if parsed:
        lookback = cond_registry.required_lookback(parsed)
        hist = _load_history_panel(year, years, base_date, lookback, set(base.index))
        panel = cond_registry.HistPanel(hist, base_date)
        mask = cond_registry.evaluate(parsed, panel, base, req.logic)
        hits = base.loc[mask]
    else:
        hits = base

    hits = hits.sort_values("Amount", ascending=False)
    total = len(hits)
    chg = change_vs_prev(year, base_date)
    hit_chgs = [c for c in (chg.get(str(i)) for i in hits.index) if c is not None]
    hits = hits.head(req.limit)
    candles = candle_map(year, {str(i) for i in hits.index}, years, base_date)
    themes, themes_ready = theme_map()
    return {
        "date": base_date.strftime("%Y-%m-%d"),
        "total": total,
        "conditions": len(parsed),
        # 검색된 종목들의 당일 평균 등락률 — 검색식이 오늘 얼마나 먹혔는지 한 줄 요약
        "avg_chg": (sum(hit_chgs) / len(hit_chgs)) if hit_chgs else None,
        "themes_ready": themes_ready,
        "items": [
            {
                "code": str(r.Index),
                "name": str(r.Name),
                "market": str(r.Market),
                "close": float(r.Close),
                "chg": chg.get(str(r.Index)),
                "amount": float(r.Amount),
                "marcap": float(r.Marcap),
                "candles": candles.get(str(r.Index), []),
                "themes": themes.get(str(r.Index), []),
            }
            for r in hits.itertuples()
        ],
    }
