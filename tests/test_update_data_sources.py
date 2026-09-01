from __future__ import annotations

import pandas as pd
import threading
import time


def test_freshness_reports_vi_and_market_funds() -> None:
    from src.layer1_data.freshness import SOURCES

    by_key = {source["key"]: source for source in SOURCES}

    assert by_key["market_vi"]["dir"] == "market_state/vi"
    assert by_key["market_vi"]["date_col"] == "bsop_date"
    assert by_key["market_funds"]["dir"] == "market_funds"
    assert by_key["market_funds"]["date_col"] == "date"


def test_web_members_freshness_uses_daily_snapshot_not_slow_backfill() -> None:
    from src.layer1_data.freshness import SOURCES

    members = {source["key"]: source for source in SOURCES}["members_snapshot"]

    assert members["dir"] == "members/snapshot"
    assert members["date_col"] == "date"
    assert all(source["key"] != "members_daily" for source in SOURCES)


def test_unavailable_source_is_not_reported_as_days_behind(tmp_path) -> None:
    from src.layer1_data import freshness

    freshness.write_mark(
        "supply",
        "2026-08-28",
        root=tmp_path,
        availability="unavailable_now",
        note="현재 수집 가능 시간이 아닙니다.",
    )

    supply = {row["key"]: row for row in freshness.report(
        root=tmp_path, today=pd.Timestamp("2026-09-02")
    )}["supply"]

    assert supply["availability"] == "unavailable_now"
    assert supply["note"] == "현재 수집 가능 시간이 아닙니다."
    assert supply["days_behind"] is None
    assert supply["grade"] == "unavailable"


def test_kis_stream_includes_market_state(monkeypatch) -> None:
    import scripts.update_data as update_data

    monkeypatch.setattr(
        update_data,
        "update_kis",
        lambda _mod, _out, _date_col, label, _last_day, **_kwargs: {"label": label},
    )
    monkeypatch.setattr(
        update_data.members,
        "snapshot_all",
        lambda **_kwargs: {"rows": 7, "codes": 1},
    )
    called: list[str] = []
    monkeypatch.setattr(
        update_data.market_state,
        "update_daily",
        lambda day, progress=None: called.append(day) or {"vi": {"days": 1}},
    )

    result = update_data._kis_stream("20260831")

    assert called == ["20260831"]
    assert result["market_state"] == {"vi": {"days": 1}}
    assert result["members"] == {"rows": 7, "codes": 1}


def test_market_funds_wrapper_uses_same_collector(monkeypatch) -> None:
    import scripts.update_data as update_data

    monkeypatch.setattr(
        update_data.kofia_market_funds,
        "update",
        lambda: {"rows": 7_000, "last_date": "20260828"},
    )

    assert update_data.update_market_funds() == {"rows": 7_000, "last_date": "20260828"}


def test_web_update_only_excludes_supply_time_window_from_failures() -> None:
    import scripts.update_data as update_data

    summary = {
        "supply": {"blocked": "TIME LIMIT"},
        "members": {"complete": True, "failed_codes": 0},
        "credit": {"errors": 0, "missing_backfill": 0},
    }
    assert update_data.web_update_problems(summary) == []

    summary["members"] = {"complete": False, "failed_codes": 3}
    assert update_data.web_update_problems(summary) == ["거래원 3종목을 받지 못했습니다."]


def test_credit_provider_latest_requires_observed_response_date() -> None:
    import scripts.update_data as update_data

    assert update_data.credit_is_provider_latest({"called": 10, "observed_latest": "20260827"})
    assert not update_data.credit_is_provider_latest({"called": 10, "observed_latest": ""})
    assert not update_data.credit_is_provider_latest(
        {"called": 10, "observed_latest": "20260827", "empty_responses": 1}
    )


def test_bar_errors_make_web_update_incomplete() -> None:
    import scripts.update_data as update_data

    summary = {
        "supply": {"blocked": "TIME LIMIT"},
        "members": {"complete": True, "failed_codes": 0},
        "credit": {"errors": 0},
        "bars_min1": {"kiwoom": {"errors": 2}, "namuh": {"errors": 0}},
    }

    assert update_data.web_update_problems(summary) == ["1분봉 2종목을 받지 못했습니다."]


def test_missing_backfill_is_counted_without_starting_slow_collection(monkeypatch, tmp_path) -> None:
    import scripts.update_data as update_data

    class Master:
        def itertuples(self):
            return iter([type("Row", (), {"sCode": "000001"})()])

    monkeypatch.setattr(update_data.bars, "load_master", lambda _name: Master())
    result = update_data.update_kis(
        object(), tmp_path, "date", "신용잔고", "20260901"
    )

    assert result["called"] == 0
    assert result["missing_backfill"] == 1


def test_members_and_credit_run_together_after_supply(monkeypatch) -> None:
    import scripts.update_data as update_data

    active = 0
    most_active = 0
    lock = threading.Lock()

    def overlap(result):
        nonlocal active, most_active
        with lock:
            active += 1
            most_active = max(most_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return result

    def fake_update(_mod, _out, _date_col, label, _last_day, **_kwargs):
        if label == "수급":
            return {"blocked": "TIME LIMIT"}
        return overlap({"label": label})

    monkeypatch.setattr(update_data, "update_kis", fake_update)
    monkeypatch.setattr(
        update_data.members,
        "snapshot_all",
        lambda **_kwargs: overlap({"complete": True, "failed_codes": 0}),
    )
    monkeypatch.setattr(update_data.market_state, "update_daily", lambda _day: {})

    result = update_data._kis_stream("20260901")

    assert result["supply"]["blocked"] == "TIME LIMIT"
    assert most_active == 2
