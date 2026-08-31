from __future__ import annotations


def test_freshness_reports_vi_and_market_funds() -> None:
    from src.layer1_data.freshness import SOURCES

    by_key = {source["key"]: source for source in SOURCES}

    assert by_key["market_vi"]["dir"] == "market_state/vi"
    assert by_key["market_vi"]["date_col"] == "bsop_date"
    assert by_key["market_funds"]["dir"] == "market_funds"
    assert by_key["market_funds"]["date_col"] == "date"


def test_kis_stream_includes_market_state(monkeypatch) -> None:
    import scripts.update_data as update_data

    monkeypatch.setattr(
        update_data,
        "update_kis",
        lambda _mod, _out, _date_col, label, _last_day: {"label": label},
    )
    monkeypatch.setattr(update_data.members, "snapshot_all", lambda: 7)
    called: list[str] = []
    monkeypatch.setattr(
        update_data.market_state,
        "update_daily",
        lambda day, progress=None: called.append(day) or {"vi": {"days": 1}},
    )

    result = update_data._kis_stream("20260831")

    assert called == ["20260831"]
    assert result["market_state"] == {"vi": {"days": 1}}


def test_market_funds_wrapper_uses_same_collector(monkeypatch) -> None:
    import scripts.update_data as update_data

    monkeypatch.setattr(
        update_data.kofia_market_funds,
        "update",
        lambda: {"rows": 7_000, "last_date": "20260828"},
    )

    assert update_data.update_market_funds() == {"rows": 7_000, "last_date": "20260828"}
