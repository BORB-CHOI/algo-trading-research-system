from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.layer1_data import run_store
from src.layer4_execution.runner import aggregate_returns

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


router = APIRouter()

# ── 백테스트 보관함 (오너 2026-08-09) ──────────────────────────
# 돌린 결과를 남겨 두고 나중에 잘라 본다 — "이 달에 산 건 다 깨졌다" 를 찾는 입구.
# 저장은 /api/backtest 가 자동으로 한다. 여기는 꺼내 보는 쪽.


@router.get("/api/runs")
def api_runs(limit: int = Query(50, ge=1, le=500)) -> dict:
    """보관해 둔 백테스트 목록 — 최근 순."""
    return {"runs": run_store.list_runs(limit=limit)}


@router.get("/api/runs/{run_id}")
def api_run(run_id: int) -> dict:
    """한 번 돌린 것 전체 — 요약 + 종목별 줄 + **처음 산 달로 묶은 성적**."""
    got = run_store.load_run(run_id)
    if got is None:
        raise HTTPException(status_code=404, detail=f"{run_id}번 결과가 없습니다.")
    return {**got, "by_month": run_store.by_month(run_id)}


@router.get("/api/runs/{run_id}/result")
def api_run_result(run_id: int) -> dict:
    """보관해 둔 결과를 **④ 화면이 그대로 그릴 수 있는 모양**으로 돌려준다.

    저장은 되는데 꺼내 볼 입구가 없었다(오너 2026-08-10: "저장할 수 있으면 뭐하냐
    불러오지를 못하는데"). 화면 계약은 POST /api/backtest 응답과 같다 — 불러온 결과와
    방금 돌린 결과가 같은 표·같은 지표로 보여야 한다.

    한 주도 못 산 줄은 순수익률이 없다(NULL) — 그걸로 갈라 담는다.
    """
    got = run_store.load_run(run_id)
    if got is None:
        raise HTTPException(status_code=404, detail=f"{run_id}번 결과가 없습니다.")

    results: list[dict] = []
    no_fill: list[dict] = []
    for p in got["picks"]:
        row = {
            "code": p["code"],
            "name": p["name"],
            "n_buys": p["n_buys"],
            "stopped": bool(p["stopped"]),
            "avg_entry": p["avg_entry"],
            "exit_value": p["exit_value"],
            "net_return": p["net_return"],
            "first_fill": p["first_fill"],
            "last_exit": p["last_exit"],
            "wave_low": p["wave_low"],
            "wave_high": p["wave_high"],
            **p["detail"],  # 걸어 둔 값·체결·손절선·미청산 표시·라운드 시작일
        }
        (results if p["net_return"] is not None else no_fill).append(row)

    closed = [float(r["net_return"]) for r in results if not r.get("open")]
    return {
        "run_id": run_id,
        "label": got["label"],
        "ran_at": got["ran_at"],
        "screen": got["screen"],
        "split": got["split"],
        "split_start": got["split_start"],
        "split_end": got["split_end"],
        "base_date": got["base_date"] or None,  # 전 기간 검사는 고른 날이 하루가 아니다
        "picked": got["picked"],
        "picked_names": [],  # 목록은 안 담는다 — 표에 있는 줄이면 충분하다
        "universe": got["picked"],
        "results": results,
        "no_fill": len(no_fill),
        "no_fill_rows": no_fill,
        "skipped": {},  # 검사 못 한 사유는 안 담았다 — 옛 저장분과 계약을 맞춘다
        "metrics": aggregate_returns([float(r["net_return"]) for r in results]),
        "closed_metrics": aggregate_returns(closed),
        "open_rounds": sum(1 for r in results if r.get("open")),
        "params": got["params"],
    }


@router.delete("/api/runs/{run_id}")
def api_run_delete(run_id: int) -> dict:
    return {"deleted": run_store.delete_run(run_id)}


class RunNote(BaseModel):
    scope: str  # period | code | run
    key: str  # '2020-03' | '005930' | run id
    body: str


@router.post("/api/runs/notes")
def api_run_note(note: RunNote) -> dict:
    """구간·종목에 메모를 붙인다 — "2020-03 코로나 폭락" 같은 재료.

    나중에 재료 분석 결과를 여기 쌓아 두면 성적 표와 나란히 놓고 볼 수 있다.
    """
    try:
        nid = run_store.add_note(
            note.scope,
            note.key,
            note.body,
            added_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"id": nid}


@router.get("/api/runs/notes/{scope}/{key}")
def api_run_notes(scope: str, key: str) -> dict:
    return {"notes": run_store.notes_for(scope, key)}
