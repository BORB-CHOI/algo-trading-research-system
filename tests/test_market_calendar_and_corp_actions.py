"""장 달력·예탁원 사건 — 콜을 아끼는 규칙과 잘린 응답 쪼개기가 도는지."""

from __future__ import annotations

import pandas as pd


def test_calendar_is_skipped_while_the_future_is_already_covered(monkeypatch, tmp_path) -> None:
    """달력은 한 번 받으면 안 바뀐다 — 앞날이 덮여 있으면 한 콜도 안 써야 한다."""
    import scripts.backfill_kis_market_state as ms

    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    (tmp_path / "calendar").mkdir(parents=True)
    pd.DataFrame({"bass_dt": ["20260910", "20271231"]}).to_parquet(
        tmp_path / "calendar" / "2026.parquet", index=False
    )

    def boom(*_a, **_k):
        raise AssertionError("덮여 있는데 불렀다")

    monkeypatch.setattr(ms, "collect_calendar", boom)
    got = ms.update_calendar("20260910")

    assert got["skipped"]
    assert got["covered_through"] == "20271231"


def test_calendar_is_fetched_when_the_horizon_runs_short(monkeypatch, tmp_path) -> None:
    import scripts.backfill_kis_market_state as ms

    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    (tmp_path / "calendar").mkdir(parents=True)
    pd.DataFrame({"bass_dt": ["20260915"]}).to_parquet(
        tmp_path / "calendar" / "2026.parquet", index=False
    )

    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(ms, "collect_calendar", lambda f, t: (asked.append((f, t)), (5, 1))[1])
    got = ms.update_calendar("20260910")

    assert asked == [("20260916", "20271231")], "덮인 다음 날부터, 내년 말까지"
    assert got["days"] == 5


def test_corp_actions_split_the_window_when_the_answer_is_truncated(monkeypatch) -> None:
    """한 콜 100 행이 천장이다 — 차면 창을 반으로 쪼개 다시 물어야 한다."""
    import scripts.backfill_kis_corp_actions as bca
    from src.layer1_market_data import kis_corp_actions as ca

    spec = ca.BY_KEY["rev_split"]
    windows: list[tuple[str, str]] = []

    def fake_fetch(_client, _spec, f_dt, t_dt, code=""):
        windows.append((f_dt, t_dt))
        # 한 해 전체를 물으면 꽉 차서 잘리고, 반년씩이면 여유가 있다
        n = ca.PAGE_CAP if (f_dt, t_dt) == ("20200101", "20201231") else 3
        return [{"record_date": f_dt, "sht_cd": f"{i:06d}"} for i in range(n)]

    monkeypatch.setattr(ca, "fetch", fake_fetch)
    rows = bca.collect_window(object(), spec, "20200101", "20201231", [0])

    assert windows[0] == ("20200101", "20201231")
    assert len(windows) == 3, "천장에 닿았으니 반으로 쪼개 두 번 더 물어야 한다"
    # 잘린 응답은 버린다 — 쪼갠 두 창이 같은 구간을 온전히 덮으므로 겹쳐 세지 않는다
    assert len(rows) == 6
    assert windows[1:] == [("20200101", "20200701"), ("20200702", "20201231")]


def test_corp_actions_stop_splitting_at_the_narrowest_window(monkeypatch) -> None:
    """하루에 100 건이 넘으면 더 쪼갤 수 없다 — 끝없이 쪼개지 않는지."""
    import scripts.backfill_kis_corp_actions as bca
    from src.layer1_market_data import kis_corp_actions as ca

    monkeypatch.setattr(
        ca, "fetch",
        lambda *_a, **_k: [{"record_date": "20200101", "sht_cd": f"{i:06d}"}
                           for i in range(ca.PAGE_CAP)],
    )
    calls = [0]
    bca.collect_window(object(), ca.BY_KEY["rev_split"], "20200101", "20200105", calls)

    assert calls[0] < 20, "안전핀이 없으면 끝없이 쪼갠다"
