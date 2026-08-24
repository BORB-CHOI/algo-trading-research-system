from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.schemas import SimStage, SimStop
from src.layer1_data import run_store
from src.layer1_data.marcap_loader import available_years, load_years
from src.layer1_data.unified import is_unified, unified_last_day
from src.layer3_strategy import support_resistance
from src.layer4_execution.backtest import resolve_period
from src.layer4_execution.strategy_one import DEFAULT_BUY_WAIT_DAYS, run_strategy_one
from src.layer4_execution.walk_forward import Progress, run_walk_forward

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


router = APIRouter()

# ─────────────────────────────────────────────────────────────
# ④ 백테스팅 — 전략 1호 전수 검사 (layer4 strategy_one, ADR-0013·0014)
# ─────────────────────────────────────────────────────────────


class BacktestRequest(BaseModel):
    conditions: list[dict] = Field(default_factory=list)  # POST /api/screen/run 과 동일 형식
    logic: str = "and"
    # 시작점 — SimulateRequest 와 동일(ADR-0013 7차)
    start_mode: str = "평평한 구간 돌파"
    start_box_bars: int = 20
    start_volume_mult: float = 2.0
    start_keep_mult: float = 2.0
    start_cool_pct: float = 0.0
    # 파동 파라미터 — SimulateRequest 와 동일(ADR-0013 5차)
    zz_depth: int
    zz_deviation: float
    zz_deviation_mode: str = "auto"
    # 피보나치 선 띠 + 지지/저항 존 파라미터 — SimulateRequest 와 동일(ADR-0014 2차 개정)
    fib_band_mode: str
    fib_band_value: float
    sr_scope: str
    sr_source: str = support_resistance.SEED_ALL
    sr_prd: int
    sr_loopback: int
    sr_channel_width_pct: float
    sr_min_strength: int
    sr_round_max_gap_pct: float
    buy: list[SimStage] = Field(default_factory=list)
    sell: list[SimStage] = Field(default_factory=list)
    sell_basis: str = "avg_entry"
    # 피보나치 **끝점(최고점)** 을 어디로 잡을지 (ADR-0020).
    # '파동 꼭대기'(기본) = 바닥 이후 최고 고가 — 안 고르면 예전과 결과가 같다.
    # 'N일 신고가' = 검색식이 정한 신고가 기간을 그대로 쓴다(서버가 conditions 에서 꺼낸다).
    # 매수 타점을 며칠까지 기다릴지 — 모든 전략 공통, 기본 1년(오너 결정 2026-08-22).
    # 그 안에 한 주도 못 사면 그 매매는 '매수 못함'으로 끝난다.
    buy_wait_days: int = DEFAULT_BUY_WAIT_DAYS
    buy_tick_offset: int = 0
    sell_tick_offset: int = 0
    buy_min_gap_pct: float = 0.0
    stop: SimStop | None = None
    # 보관함에 남길 이름·검색식 (오너 2026-08-09: "백테스트 돌린 거 어디에 저장해둘 수
    # 있게 해서, 데이터 분석을 너가 가능하도록 해"). 안 주면 이름 없이 담는다.
    label: str = ""
    screen_name: str = ""
    # 어느 거래소 체결로 볼지 — krx(지금까지와 같음) / unt(넥스트레이드까지 합친 통합).
    # 종목 고르기의 거래량·거래대금과 종목별 일봉 둘 다 이 기준으로 간다.
    market: str = "krx"
    # **검사 구간 — 화면에서 고른 날짜가 그대로 온다**(ADR-0019). 코드가 안 나눈다.
    # 안 주면 2007-01-01 ~ 최신 거래일(resolve_period).
    # /api/backtest/all(전 구간)은 이 구간의 **거래일마다** 검색식을 다시 돌린다
    # (walk_forward, 오너 2026-08-10: "그때부터 하루씩 지금까지 매매 가능해야지").
    start: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # (2026-08-10 폐기) reenter_same_wave — 이제 검색식에 걸린 날마다 무조건 라운드를
    # 열므로(중복 허용) 켜고 끌 것이 없다. 옛 저장본이 보내와도 무시한다.
    reenter_same_wave: bool = False


def _strategy_kwargs(req: BacktestRequest) -> dict:
    """요청 → 엔진 인자. `run_strategy_one` 과 `run_walk_forward` 가 같은 값을 받는다.

    두 엔진의 차이는 "종목을 언제 고르나"뿐이다 — 전략 값은 한 곳에서 만든다.
    """
    if not req.conditions:
        raise HTTPException(
            status_code=400, detail="조건검색식이 비었습니다 — ①에서 검색식을 고르세요."
        )
    return {
        "market": req.market,
        "zz": {
            "zz_depth": req.zz_depth,
            "zz_deviation": req.zz_deviation,
            "zz_deviation_mode": req.zz_deviation_mode,
            "start_mode": req.start_mode,
            "start_box_bars": req.start_box_bars,
            "start_volume_mult": req.start_volume_mult,
            "start_keep_mult": req.start_keep_mult,
            "start_cool_pct": req.start_cool_pct,
        },
        "buy_wait_days": req.buy_wait_days,
        "sr": {
            "fib_band_mode": req.fib_band_mode,
            "fib_band_value": req.fib_band_value,
            "sr_scope": req.sr_scope,
            "sr_source": req.sr_source,
            "sr_prd": req.sr_prd,
            "sr_loopback": req.sr_loopback,
            "sr_channel_width_pct": req.sr_channel_width_pct,
            "sr_min_strength": req.sr_min_strength,
            "sr_round_max_gap_pct": req.sr_round_max_gap_pct,
        },
        "buy": [
            {"ratio": s.ratio, "weight": s.weight}
            for s in req.buy
            if s.enabled and s.ratio is not None and 0 < s.ratio < 1
        ],
        "sell": [
            {"rebound_pct": s.rebound_pct, "weight": s.weight}
            for s in req.sell
            if s.enabled and s.rebound_pct is not None and s.rebound_pct > 0
        ],
        "sell_basis": req.sell_basis,
        "buy_tick_offset": req.buy_tick_offset,
        "sell_tick_offset": req.sell_tick_offset,
        "buy_min_gap_pct": req.buy_min_gap_pct,
        "stop": req.stop.to_cfg() if req.stop and req.stop.enabled else None,
    }


@router.post("/api/backtest")
def api_backtest(req: BacktestRequest) -> dict:
    """전략 1호 전수 백테스트 — 조건검색식 유니버스 전 종목에 세팅→체결→집계.

    **분석 전용 결정론 계산, 주문 없음(CLAUDE.md).** 세팅은 기준일(선별일) 왼쪽만,
    체결은 오른쪽만 — look-ahead 는 구조로 차단(strategy_one docstring).
    비용은 왕복 정액률(ADR-0004 placeholder) 포함. N<30 은 reliable=False 로 표시.

    종목을 고르는 건 구간 시작 직전 **하루**뿐이다. 거래일마다 다시 고르는 검사는
    `/api/backtest/all`(전 구간).
    """
    try:
        result = run_strategy_one(
            req.conditions,
            req.logic,
            start=req.start,
            end=req.end,
            **_strategy_kwargs(req),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 보관함에 남긴다 — 나중에 "이 달에 산 건 다 깨졌다" 를 찾으려면 결과가 남아 있어야 한다.
    return _archive(result, req)


def _archive(result: dict, req: BacktestRequest) -> dict:
    """결과를 보관함에 남긴다. **저장 실패가 결과를 못 보게 만들면 안 된다** — 경고만.

    검색 조건(`conditions`)도 **같이 남긴다.** 전에는 이름(`screen`)만 남기고 조건 자체를
    빼 놨는데, 그러면 몇 달 뒤 "이 결과가 무슨 조건으로 나온 거냐"를 되짚을 방법이 없다.
    검색식은 이름이 같아도 내용이 바뀔 수 있어서 이름만으로는 재현이 안 된다
    (오너 지적 2026-08-22 — 피씨엘 기준일을 다시 확인하려는데 조건이 안 남아 있었다).
    """
    # 어느 체결로 봤는지 화면이 그대로 띄운다 — 통합인데 채워진 날짜를 모르면
    # 그 뒤 구간을 통합인 줄 알고 본다.
    result["market"] = req.market
    result["unified_until"] = unified_last_day() if is_unified(req.market) else None
    try:
        result["run_id"] = run_store.save_run(
            result,
            ran_at=datetime.now(UTC).isoformat(timespec="seconds"),
            params=req.model_dump(),
            label=req.label,
            screen=req.screen_name,
        )
    except (OSError, sqlite3.Error, ValueError, TypeError) as e:
        result["run_id"] = None
        result.setdefault("warnings", []).append(f"결과 보관 실패 — {e}")
    return result


# ─────────────────────────────────────────────────────────────
# ④-b 전 구간 — 거래일마다 다시 고르는 검사 (layer4 walk_forward)
#
# 몇 분 걸린다(실측 5~10분). 한 번의 HTTP 요청으로 붙들고 있으면 브라우저가 먼저
# 끊어 버리고, 오너는 "멈춘 건지 도는 건지" 알 수 없다. 그래서 **시작 / 진행 확인**
# 두 요청으로 나눈다. 진행률은 엔진이 주는 Progress 를 그대로 옮긴다.
#
# 무거운 계산이라 **한 번에 하나만** 돌린다 — 두 개가 겹치면 둘 다 느려지고 메모리가 는다.
# ─────────────────────────────────────────────────────────────

_WF_JOBS: dict[str, dict] = {}
_WF_LOCK = threading.Lock()
_WF_KEEP = 5  # 최근 몇 개의 결과를 메모리에 들고 있을지


def _latest_trading_day() -> pd.Timestamp | None:
    """마지막 연도 패널의 마지막 날짜. 못 구하면 None(그러면 오늘을 쓴다)."""
    try:
        years = available_years()
        if not years:
            return None
        return pd.Timestamp(load_years(years[-1], years[-1])["Date"].max())
    except (OSError, ValueError, KeyError):
        return None


def _wf_worker(job_id: str, req: BacktestRequest, start: str, end: str) -> None:
    """작업 스레드 — 진행률을 job 에 적고, 끝나면 결과(또는 오류)를 담는다."""

    def on_progress(p: Progress) -> None:
        with _WF_LOCK:
            job = _WF_JOBS.get(job_id)
            if job is not None:
                job.update(phase=p.phase, done=p.done, total=p.total)

    try:
        result = run_walk_forward(
            req.conditions,
            req.logic,
            start=start,
            end=end,
            progress=on_progress,
            **_strategy_kwargs(req),
        )
        # 전 구간 검사도 보관함에 남긴다 — 화면 계약은 ④와 같다(run_id).
        _archive(result, req)
        with _WF_LOCK:
            _WF_JOBS[job_id].update(status="done", result=result)
    except (ValueError, FileNotFoundError, HTTPException) as e:
        detail = e.detail if isinstance(e, HTTPException) else str(e)
        with _WF_LOCK:
            _WF_JOBS[job_id].update(status="error", detail=str(detail))
    except Exception as e:  # noqa: BLE001 — 스레드에서 터지면 화면이 영영 '도는 중'이 된다
        with _WF_LOCK:
            _WF_JOBS[job_id].update(status="error", detail=f"검사 중 오류 — {e}")


@router.post("/api/backtest/all")
def api_backtest_all(req: BacktestRequest) -> dict:
    """전 구간 검사 **시작**. 바로 job_id 를 주고, 진행은 GET 으로 확인한다.

    거래일마다 검색식을 다시 돌린다(walk_forward) — "2020~2023 검사"인데 실제로는
    2019-12-30 하루에 걸린 종목만 보던 문제를 없앤 검사다. 돈 무한 전제라 동시 보유
    한도가 없다. 이 숫자는 계좌 수익률이 아니라 "한 종목에 들어갔을 때 평균 어땠나"다.
    """
    # 날짜를 안 주면 기본값(2007-01-01 ~ 최신 거래일)을 쓴다 — ADR-0019.
    # **구간을 막지 않는다.** 오너가 고른 구간은 그대로 돈다(§4.3).
    try:
        start_ts, end_ts = resolve_period(req.start, req.end, latest=_latest_trading_day())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    start, end = start_ts.strftime("%Y-%m-%d"), end_ts.strftime("%Y-%m-%d")
    _strategy_kwargs(req)  # 검색식·전략 값 검증을 스레드 밖에서 먼저 — 400 을 바로 준다

    job_id = uuid.uuid4().hex[:12]
    with _WF_LOCK:
        if any(j["status"] == "running" for j in _WF_JOBS.values()):
            raise HTTPException(
                status_code=409, detail="이미 전 구간 검사가 돌고 있습니다 — 끝나면 다시 누르세요."
            )
        # 오래된 결과부터 버린다 — 결과 하나가 수 MB 라 쌓이면 메모리를 먹는다.
        for old in list(_WF_JOBS)[: max(0, len(_WF_JOBS) - _WF_KEEP + 1)]:
            _WF_JOBS.pop(old, None)
        _WF_JOBS[job_id] = {
            "status": "running",
            "phase": "시작하는 중",
            "done": 0,
            "total": 0,
            "start": start,
            "end": end,
        }
    threading.Thread(target=_wf_worker, args=(job_id, req, start, end), daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@router.get("/api/backtest/all/{job_id}")
def api_backtest_all_status(job_id: str) -> dict:
    """전 구간 검사 진행 확인. status=running|done|error. done 이면 result 가 실린다."""
    with _WF_LOCK:
        job = _WF_JOBS.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404, detail="그 검사 기록이 없습니다 — 다시 실행하세요."
            )
        return dict(job)
