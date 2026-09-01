from __future__ import annotations

import threading
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter

from api.refresh import REFRESH_STATE, clear_data_caches, run_refresh
from src.layer1_data import freshness

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


router = APIRouter()


@router.get("/api/data/freshness")
def api_data_freshness() -> dict:
    """데이터가 어디까지 들어와 있나 — 화면 상단 배지가 쓴다.

    **워터마크 파일 하나만 읽는다.** 데이터 파일을 열지 않으므로 1밀리초다.
    묵은 데이터는 화면이 멀쩡히 그려져서 눈에 안 띈다 — 그래서 날짜만이 아니라 등급을 준다.
    """
    return {
        "sources": freshness.report(),
        "worst": freshness.worst_grade(),
        "refreshing": bool(REFRESH_STATE["running"]),
        # 게이지용 — 갱신은 파일 16,576개를 훑어 약 27초 걸린다(실측 2026-08-17).
        "progress": {
            "phase": REFRESH_STATE["phase"],
            "done": int(REFRESH_STATE["done"]),
            "total": int(REFRESH_STATE["total"]),
        },
        "finished_at": REFRESH_STATE["finished_at"],
        # 무거운 갱신(나무 봉·수급·신용잔고)도 이제 버튼으로 된다 — /api/data/update 참조.
        # 터미널로 직접 돌리고 싶으면 이 명령도 그대로 쓸 수 있다(같은 잠금 파일을 본다).
        "manual_command": ".venv/Scripts/python scripts/update_data.py",
        "heavy": {
            "running": bool(_UPDATE_STATE["running"]),
            "phase": _UPDATE_STATE["phase"],
            "done": int(_UPDATE_STATE["done"]),
            "total": int(_UPDATE_STATE["total"]),
            "finished_at": _UPDATE_STATE["finished_at"],
            "result": _UPDATE_STATE["result"],
            "events": list(_UPDATE_STATE.get("events", [])),
        },
    }


@router.post("/api/data/refresh")
def api_data_refresh() -> dict:
    """차트 일봉을 지금 최신으로 (marcap git pull → 캐시 비우기 → 워터마크 다시 훑기).

    실측 2026-08-17: 파일 16,577개를 훑어 약 30초. **뒤에서 도는 스레드**라 그동안에도
    화면은 그대로 쓸 수 있다 — 갱신 중 차트 응답 147ms → 149ms(1.0배, 실측).
    파일 읽기는 GIL 을 놓기 때문이다.

    나무 봉·KIS 수급·신용잔고 증분(호출 한도를 크게 태우는 무거운 갱신)은 여기서 하지
    않는다 — 그건 `/api/data/update` 다.
    """
    if REFRESH_STATE["running"]:
        return {"started": False, "message": "이미 갱신 중입니다."}
    threading.Thread(target=lambda: run_refresh(rescan=True), daemon=True).start()
    return {
        "started": True,
        "message": "갱신을 시작했습니다 — 약 30초, 그동안 화면은 그대로 쓰셔도 됩니다.",
    }


_UPDATE_LOCK = threading.Lock()
_UPDATE_STATE: dict[str, Any] = {
    "running": False,
    "phase": "",
    "done": 0,
    "total": 0,
    "finished_at": None,
    "result": None,
    "events": [],
    "run_id": 0,
}


def _record_update_progress(
    label: str, done: int, total: int, *, run_id: int | None = None
) -> None:
    """프론트가 터미널 없이 볼 수 있도록 최근 진행 기록을 남긴다."""
    event = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "message": label,
        "done": int(done),
        "total": int(total),
    }
    with _UPDATE_LOCK:
        if run_id is not None and run_id != _UPDATE_STATE.get("run_id"):
            return
        events = [*_UPDATE_STATE.get("events", []), event][-120:]
        _UPDATE_STATE.update(phase=label, done=done, total=total, events=events)


class _FrontendLogHandler(logging.Handler):
    """KIS 공통 클라이언트의 재시도 경고를 현재 갱신 화면으로 보낸다."""

    def __init__(self, run_id: int) -> None:
        super().__init__(level=logging.WARNING)
        self.run_id = run_id

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        if len(message) > 240:
            message = message[:237] + "..."
        _record_update_progress(
            message, 0, 0, run_id=self.run_id
        )


def _run_heavy_update(run_id: int) -> None:
    """봉·수급·신용잔고·VI·시장 자금 — `scripts/update_data.py` 를 뒤에서 그대로 돌린다.

    브라우저를 닫거나 새로고침해도 이 스레드는 서버 프로세스가 살아 있는 한 계속 돈다
    (오너 요청 2026-08-22: "웹상에서 다 갱신 가능하도록, 끊겨도 문제없도록"). 겹침 방지는
    `scripts/update_data.py` 의 잠금 파일(`_update.lock`)이 그대로 한다 — 터미널에서
    같은 스크립트를 돌려도 서로 못 겹친다.
    """
    def progress(label: str, done: int, total: int) -> None:
        _record_update_progress(label, done, total, run_id=run_id)

    kis_logger = logging.getLogger("src.layer4_execution.brokers.kis.client")
    frontend_handler = _FrontendLogHandler(run_id)
    kis_logger.addHandler(frontend_handler)
    try:
        import scripts.update_data as update_data  # 무거운 임포트라 여기서만 — 서버 뜨는 속도에 안 영향

        result = update_data.run_update(progress=progress)
    except Exception as e:  # 스레드가 이것 때문에 조용히 죽으면 안 된다 — 실패도 값으로 남긴다
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        kis_logger.removeHandler(frontend_handler)
        clear_data_caches()
        completed = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "message": "전체 갱신 완료" if result.get("ok") else "갱신 중 문제가 생겼습니다",
            "done": 1,
            "total": 1,
        }
        with _UPDATE_LOCK:
            if run_id == _UPDATE_STATE.get("run_id"):
                _UPDATE_STATE.update(
                    running=False,
                    phase="",
                    finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
                    result=result,
                    events=[*_UPDATE_STATE.get("events", []), completed][-120:],
                )


@router.post("/api/data/update")
def api_data_update() -> dict:
    """봉·수급·신용잔고·VI·시장 자금 증분 — 종목당 호출이 많은 **무거운** 갱신.

    조회만 한다. 주문 없음. 서버 백그라운드 스레드로 돌아서 브라우저를 닫아도 계속
    진행된다 — 다시 열면 `/api/data/freshness` 의 `heavy` 필드로 진행 상황을 이어서 본다.
    """
    # 스레드를 띄운 뒤에 상태를 바꾸면, 브라우저가 곧바로 다시 읽는 찰나에는 아직
    # `running=False`라 버튼이 그대로 보인다. 예약과 상태 변경은 같은 잠금에서 끝낸다.
    with _UPDATE_LOCK:
        if _UPDATE_STATE["running"]:
            return {"started": False, "message": "이미 갱신 중입니다."}
        run_id = int(_UPDATE_STATE.get("run_id", 0)) + 1
        _UPDATE_STATE.update(
            phase="시작",
            done=0,
            total=0,
            result=None,
            running=True,
            events=[{
                "at": datetime.now().isoformat(timespec="seconds"),
                "message": "전체 갱신 시작",
                "done": 0,
                "total": 0,
            }],
            run_id=run_id,
        )
    try:
        threading.Thread(target=lambda: _run_heavy_update(run_id), daemon=True).start()
    except RuntimeError as e:
        # 예약 뒤 스레드 생성이 거절되면 영구히 "받아오는 중"으로 남기지 않는다.
        with _UPDATE_LOCK:
            _UPDATE_STATE.update(
                running=False,
                phase="",
                finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
                result={"ok": False, "error": f"{type(e).__name__}: {e}"},
            )
        return {"started": False, "message": "갱신을 시작하지 못했습니다. 다시 시도해 주세요."}
    return {
        "started": True,
        "message": "전체 갱신을 시작했습니다. 현재 저장 상태에서는 약 5분 걸립니다. 창을 닫아도 계속됩니다.",
    }
