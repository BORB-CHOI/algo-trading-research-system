from __future__ import annotations

from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.layer1_data import kv_store

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


router = APIRouter()

# ─────────────────────────────────────────────────────────────
# 화면 설정 저장소 (오너 지시 2026-08-09: "간단하게 로컬 DB 구현해라")
#
# 전략·검색식·관심종목이 브라우저 localStorage 에만 있어서, 주소가 localhost ↔ 127.0.0.1
# 로 바뀌자 통째로 안 보였다. 이제 `data/app.db` 에 둔다 — 주소가 바뀌든 캐시를 지우든
# 다른 브라우저로 열든 살아남는다.
#
# 매매 데이터가 아니라 **화면 설정**이다. 주문·포지션은 여기 안 들어온다(CLAUDE.md).
# ─────────────────────────────────────────────────────────────


class StoreValue(BaseModel):
    value: Any


@router.get("/api/store")
def api_store_all() -> dict:
    """저장된 것 전부 — 화면이 뜰 때 한 번 받아 간다."""
    return {"items": kv_store.snapshot()}


@router.get("/api/store/{key}")
def api_store_get(key: str) -> dict:
    """없으면 `value: null`. 404 로 하지 않는다 — "아직 저장 안 함"은 오류가 아니다."""
    try:
        return {"key": key, "value": kv_store.get(key)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/api/store/{key}")
def api_store_put(key: str, body: StoreValue) -> dict:
    try:
        kv_store.put(key, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"key": key, "ok": True}


@router.delete("/api/store/{key}")
def api_store_delete(key: str) -> dict:
    try:
        return {"key": key, "deleted": kv_store.delete(key)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
