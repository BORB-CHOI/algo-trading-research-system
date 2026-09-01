from __future__ import annotations


def test_update_is_reported_as_running_before_background_thread_starts(monkeypatch) -> None:
    """A click must immediately switch the UI to an in-progress state."""
    from api.routers import data

    class DeferredThread:
        def __init__(self, *, target, daemon: bool) -> None:
            self.target = target
            self.daemon = daemon

        def start(self) -> None:
            pass  # Keep the worker deferred so this checks the route's own contract.

    monkeypatch.setattr(data.threading, "Thread", DeferredThread)
    monkeypatch.setattr(
        data,
        "_UPDATE_STATE",
        {
            "running": False,
            "phase": "",
            "done": 0,
            "total": 0,
            "finished_at": None,
            "result": None,
        },
    )

    assert data.api_data_update()["started"] is True
    assert data.api_data_freshness()["heavy"]["running"] is True


def test_update_start_failure_leaves_refresh_retryable(monkeypatch) -> None:
    """A failed thread start must not leave the only refresh button disabled."""
    from api.routers import data

    class BrokenThread:
        def __init__(self, *, target, daemon: bool) -> None:
            self.target = target
            self.daemon = daemon

        def start(self) -> None:
            raise RuntimeError("thread limit")

    monkeypatch.setattr(data.threading, "Thread", BrokenThread)
    monkeypatch.setattr(
        data,
        "_UPDATE_STATE",
        {
            "running": False,
            "phase": "",
            "done": 0,
            "total": 0,
            "finished_at": None,
            "result": None,
        },
    )

    result = data.api_data_update()

    assert result["started"] is False
    heavy = data.api_data_freshness()["heavy"]
    assert heavy["running"] is False
    assert heavy["finished_at"] is not None
    assert heavy["result"] == {"ok": False, "error": "RuntimeError: thread limit"}


def test_frontend_receives_live_update_events(monkeypatch) -> None:
    from api.routers import data

    monkeypatch.setattr(
        data,
        "_UPDATE_STATE",
        {
            "running": True,
            "phase": "",
            "done": 0,
            "total": 0,
            "finished_at": None,
            "result": None,
            "events": [],
        },
    )

    data._record_update_progress("거래원 받는 중", 1200, 2874)

    heavy = data.api_data_freshness()["heavy"]
    assert heavy["events"][-1]["message"] == "거래원 받는 중"
    assert heavy["events"][-1]["done"] == 1200
    assert heavy["events"][-1]["total"] == 2874


def test_late_progress_from_previous_run_is_ignored(monkeypatch) -> None:
    from api.routers import data

    monkeypatch.setattr(
        data,
        "_UPDATE_STATE",
        {
            "running": True,
            "phase": "새 회차",
            "done": 0,
            "total": 0,
            "finished_at": None,
            "result": None,
            "events": [],
            "run_id": 2,
        },
    )

    data._record_update_progress("이전 회차의 늦은 기록", 10, 10, run_id=1)

    assert data._UPDATE_STATE["phase"] == "새 회차"
    assert data._UPDATE_STATE["events"] == []
