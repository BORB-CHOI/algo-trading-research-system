from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI

from api.candles import (
    full_history_adjusted,
    latest_marcap,
    load_year_screen,
    load_year_slim,
    symbol_master_cached,
)
from src.layer1_data import freshness
from src.layer1_data.krx_gapfill import fill_marcap_gap
from src.layer1_data.refresh import pull_marcap

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


# ── 데이터 최신 상태 (워터마크 방식) ──────────────────────────
#
# 증분 갱신의 업계 표준은 **"어디까지 받았나"를 작은 상태 파일 하나에 적어 두고 그 뒤부터만
# 받는 것**이다(high watermark). 데이터 파일을 전부 열어 보며 알아내지 않는다 — 실측
# 2026-08-16 기준 수급 폴더 하나 훑는 데 47초, 나무 봉 전체는 4.2분이었다(API 호출 0건인데도).
#
# **서버를 켤 때는 빠른 것만 한다**: marcap `git pull`(몇 초) → marcap 뒤쪽 공백을 KRX 로
# 채우기(날짜당 3콜, 몇 초) → 프로세스 캐시 비우기.
# 차트 일봉의 정본이 그 깃 복제본이라, 그것만 당기면 차트 오른쪽 끝이 어제쯤 되고,
# KRX 공백 채우기까지 하면 오늘(장 마감 후)까지 온다.
# 나무 봉·KIS 수급 증분은 호출 한도를 크게 태우므로 **서버가 멋대로 시작하지 않는다** —
# 그건 사람이 `scripts/update_data.py` 로 돌린다.

_REFRESH_LOCK = threading.Lock()
REFRESH_STATE: dict[str, Any] = {
    "running": False,
    "phase": "",
    "done": 0,
    "total": 0,
    "finished_at": None,
    "result": None,
}


def clear_data_caches() -> None:
    """새로 받은 데이터를 읽으려면 프로세스가 들고 있던 옛 표를 버려야 한다.

    이걸 빼먹으면 `git pull` 은 됐는데 화면은 그대로다 — 오너가 "갱신했는데 왜 그대로냐"를
    묻게 되는 자리다.
    """
    for fn in (
        load_year_slim,
        full_history_adjusted,
        load_year_screen,
        symbol_master_cached,
        latest_marcap,
    ):
        fn.cache_clear()


def run_refresh(*, rescan: bool) -> dict:
    """빠른 갱신 한 번. 두 번 겹쳐 돌지 않는다."""
    with _REFRESH_LOCK:
        if REFRESH_STATE["running"]:
            return {"skipped": "이미 갱신 중입니다."}
        REFRESH_STATE["running"] = True
    REFRESH_STATE.update(phase="차트 일봉 받는 중", done=0, total=0)

    def progress(label: str, done: int, total: int) -> None:
        REFRESH_STATE.update(phase=f"{label} 훑는 중", done=done, total=total)

    try:
        pulled = pull_marcap()
        REFRESH_STATE.update(phase="marcap 뒤쪽 공백 KRX 로 채우는 중")
        gap = fill_marcap_gap()
        changed = bool(pulled.get("changed") or gap.get("saved") or gap.get("removed"))
        if changed:
            clear_data_caches()
        if rescan or changed:
            freshness.refresh_marks(on_progress=progress)
        result = {"marcap": pulled, "recent": gap, "sources": freshness.report()}
    except Exception as e:  # 서버가 이것 때문에 죽으면 안 된다 — 실패도 값으로 남긴다
        result = {"error": f"{type(e).__name__}: {e}"}
    finally:
        REFRESH_STATE.update(
            running=False,
            phase="",
            finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
            result=result,
        )
    return result


def startup_refresh() -> None:
    """서버 시작 훅에서 뒤로 돌린다. 훑기는 오늘 아직 안 했을 때만."""
    run_refresh(rescan=freshness.needs_rescan())


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """켜자마자 차트 일봉을 최신으로 — 뒤에서. 서버 뜨는 걸 막지 않는다."""
    threading.Thread(target=startup_refresh, daemon=True).start()
    yield
