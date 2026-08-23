"""케이스 검사기 API (ADR-0005) — **앱 조립만** 한다.

프런트(웹 차트)가 "이 종목, 이 구간"의 일봉을 받아 그릴 수 있게 marcap 을 내준다.

## 원칙 (CLAUDE.md)

- 이 서버는 **데이터를 보여주기만** 한다. BUY/SELL·포지션·주문은 여기 없다.
- 전략 로직의 정본은 파이썬(layer3)이다. 프런트는 결과를 그릴 뿐이다.
- 조회용 종목 마스터·유니버스 제외 규칙은 기존 layer1 코드를 그대로 재사용한다.

## 어디에 뭐가 있나

한 파일 2,551줄이던 걸 쪼갰다(2026-08-23). 엔드포인트는 화면 단계별로 모여 있다.

| 파일 | 무엇 |
|---|---|
| `api/candles.py` | 봉 읽기·수정주가·합성(주·월봉)·시총 붙이기 — 여러 라우터가 같이 쓴다 |
| `api/refresh.py` | 서버 켤 때 빠른 갱신(watermark) |
| `api/routers/live.py` | 장중 오늘 봉(나무 실시간) |
| `api/routers/store.py` | 화면 설정 저장소 |
| `api/routers/symbols.py` | ① 종목 검색·봉·시세·열지도 |
| `api/routers/screening.py` | ② 조건검색 |
| `api/routers/data.py` | 데이터 최신 상태·갱신 |
| `api/routers/market.py` | 지수·순위·뉴스·재무 |
| `api/routers/strategy.py` | ③ 전략·오버레이·지지저항 |
| `api/routers/simulate.py` | ③ 시뮬레이션 |
| `api/routers/backtest.py` | ④ 백테스트·전 구간 검사 |
| `api/routers/runs.py` | 백테스트 보관함 |

## 실행

    uvicorn api.main:app --reload --port 8000

프런트(Vite)는 dev 서버에서 `/api/*` 를 이 서버로 proxy 한다(web/vite.config.ts).
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.refresh import startup_refresh
from api.routers import (
    backtest,
    data,
    live,
    market,
    runs,
    screening,
    simulate,
    store,
    strategy,
    symbols,
)
from src.layer1_data.marcap_loader import available_years
from src.layer1_data.recent import recent_meta

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """켜자마자 차트 일봉을 최신으로 — 뒤에서. 서버 뜨는 걸 막지 않는다."""
    threading.Thread(target=startup_refresh, daemon=True).start()
    yield


app = FastAPI(title="ATS API", version="0.1.0", lifespan=_lifespan)

# Vite dev 서버에서 직접 부를 때를 대비. proxy 를 쓰면 사실상 필요 없다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

for _mod in (live, store, symbols, screening, data, market, strategy, runs, simulate, backtest):
    app.include_router(_mod.router)


@app.get("/api/health")
def health() -> dict:
    years = available_years()
    meta = recent_meta()
    return {
        "ok": True,
        "years": years,
        "marcap_last": meta.get("marcap_last"),
        "recent_dates": meta.get("dates", []),
    }
