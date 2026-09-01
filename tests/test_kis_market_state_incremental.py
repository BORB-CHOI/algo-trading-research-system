from __future__ import annotations

import pytest


def test_missing_vi_days_retries_errors_and_skips_clean_days() -> None:
    from scripts.backfill_kis_market_state import missing_vi_days

    notes = {
        "vi:20260827": {"done": True, "errors": 0},
        "vi:20260828": {"done": True, "errors": 2},
    }

    assert missing_vi_days(notes, ["20260827", "20260828", "20260831"]) == [
        "20260828",
        "20260831",
    ]


def test_kis_key_pairs_include_numbered_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.backfill_kis_market_state import kis_key_pairs

    monkeypatch.setenv("KIS_APP_KEY", "first")
    monkeypatch.setenv("KIS_APP_SECRET", "secret-1")
    monkeypatch.setenv("KIS_APP_KEY_2", "second")
    monkeypatch.setenv("KIS_APP_SECRET_2", "secret-2")

    assert kis_key_pairs() == [("first", "secret-1"), ("second", "secret-2")]


def test_kis_key_pairs_reject_half_configured_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.backfill_kis_market_state import kis_key_pairs

    monkeypatch.setenv("KIS_APP_KEY", "first")
    monkeypatch.setenv("KIS_APP_SECRET", "secret-1")
    monkeypatch.setenv("KIS_APP_KEY_2", "second")
    monkeypatch.delenv("KIS_APP_SECRET_2", raising=False)

    with pytest.raises(ValueError, match="KIS_APP_SECRET_2"):
        kis_key_pairs()


def test_daily_update_runs_only_missing_vi_days(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import scripts.backfill_kis_market_state as market_state

    notes = market_state.Notes(tmp_path / "state.json")
    notes.data = {"vi:20260828": {"done": True, "errors": 0}}
    called: list[tuple[str, str]] = []

    monkeypatch.setattr(market_state, "all_codes", lambda: ["005930"])
    monkeypatch.setattr(market_state, "listed_stock_codes", lambda: ["005930"])
    monkeypatch.setattr(
        market_state, "trading_days", lambda _since, _until: ["20260828", "20260831"]
    )
    monkeypatch.setattr(
        market_state,
        "collect_flags",
        lambda _codes, day: called.append(("flags", day)) or (1, 0),
    )
    monkeypatch.setattr(
        market_state,
        "collect_overtime",
        lambda _codes, day: called.append(("overtime", day)) or (2, 0),
    )
    monkeypatch.setattr(
        market_state,
        "collect_day_vi",
        lambda _codes, day, **_kwargs: called.append(("vi", day)) or (3, 0, []),
    )

    result = market_state.update_daily(
        "20260831", notes=notes, today="20260901", prepare_credentials=False
    )

    assert called == [
        ("overtime", "20260901"),
        ("flags", "20260901"),
        ("vi", "20260831"),
    ]
    assert result["vi"] == {"days": 1, "rows": 3, "errors": 0}
    assert notes.data["vi:20260831"]["done"] is True


def test_flags_lane_uses_the_measured_optimal_rate() -> None:
    import scripts.backfill_kis_market_state as market_state

    workers, rate = market_state.LANES["flags"]
    assert workers == 6
    assert rate == 16.0


def test_alphanumeric_stock_code_can_be_assigned_to_a_key() -> None:
    import scripts.backfill_kis_market_state as market_state

    assert market_state.key_index("0000D0", 3) in {0, 1, 2}


def test_daily_update_retries_only_failed_vi_codes(monkeypatch, tmp_path) -> None:
    import scripts.backfill_kis_market_state as market_state

    notes = market_state.Notes(tmp_path / "state.json")
    notes.data = {
        "overtime:20260901": {"done": True, "errors": 0},
        "flags:20260901": {"done": True, "errors": 0},
        "vi:20260831": {
            "done": False,
            "errors": 2,
            "failed_codes": ["0000D0", "00088K"],
        },
    }
    called = []
    monkeypatch.setattr(market_state, "all_codes", lambda: ["005930", "0000D0", "00088K"])
    monkeypatch.setattr(market_state, "listed_stock_codes", lambda: ["005930"])
    monkeypatch.setattr(market_state, "trading_days", lambda _since, _until: ["20260831"])
    monkeypatch.setattr(
        market_state,
        "collect_day_vi",
        lambda codes, day, **kwargs: called.append((codes, day, kwargs)) or (2, 0, []),
    )

    market_state.update_daily(
        "20260831", notes=notes, today="20260901", prepare_credentials=False
    )

    assert called == [
        (["0000D0", "00088K"], "20260831", {"merge_existing": True})
    ]
