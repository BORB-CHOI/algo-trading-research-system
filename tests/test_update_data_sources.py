from __future__ import annotations

import pandas as pd
import threading
import time


def test_freshness_reports_vi_and_market_funds() -> None:
    from src.layer1_market_data.freshness import SOURCES

    by_key = {source["key"]: source for source in SOURCES}

    assert by_key["market_vi"]["dir"] == "market_state/vi"
    assert by_key["market_vi"]["date_col"] == "bsop_date"
    assert by_key["market_funds"]["dir"] == "market_funds"
    assert by_key["market_funds"]["date_col"] == "date"


def test_freshness_uses_the_same_daily_sources_as_the_chart() -> None:
    from src.layer1_market_data.freshness import SOURCES

    by_key = {source["key"]: source for source in SOURCES}

    assert by_key["namuh_day"]["label"] == "차트 일봉"
    assert by_key["namuh_day"]["dir"] == "namuh_bars/krx/day"
    assert by_key["namuh_unt_day"]["label"] == "통합·NXT 거래량"
    assert by_key["namuh_unt_day"]["dir"] == "namuh_bars/unt/day"
    assert by_key["marcap"]["label"] == "시가총액·상장폐지 종목 일봉"


def test_web_members_freshness_uses_daily_snapshot_not_slow_backfill() -> None:
    from src.layer1_market_data.freshness import SOURCES

    members = {source["key"]: source for source in SOURCES}["members_snapshot"]

    assert members["dir"] == "members/snapshot"
    assert members["date_col"] == "date"
    assert all(source["key"] != "members_daily" for source in SOURCES)


def test_unavailable_source_is_not_reported_as_days_behind(tmp_path) -> None:
    from src.layer1_market_data import freshness

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


def _master(rows: list[tuple[str, str]]) -> pd.DataFrame:
    """상장 목록 흉내 — (종목코드, 액면가)."""
    return pd.DataFrame(
        [{"sCode": code, "sParvalue": par, "sKorName": code} for code, par in rows]
    )


def test_etf_without_a_saved_file_is_not_called(monkeypatch, tmp_path) -> None:
    """ETF·ETN 은 백필 유니버스(marcap) 밖이다 — 파일이 없어도 부르지 않는다."""
    import scripts.update_data as update_data

    monkeypatch.setattr(
        update_data.bars, "load_master", lambda _name: _master([("069500", "0000000")])
    )
    monkeypatch.setattr(update_data.listing, "marcap_codes", lambda *a, **k: set())

    result = update_data.update_kis(object(), tmp_path, "date", "신용잔고", "20260901")

    assert result["called"] == 0
    assert result["not_stock"] == 1
    assert result["new_listing"] == 0


def test_newly_listed_stock_is_collected_even_on_its_listing_day(monkeypatch, tmp_path) -> None:
    """백필 뒤에 상장한 주식은 첫 거래일부터 받아 첫 파일을 만든다.

    옛 코드는 "저장 파일이 없으면 백필 몫"이라며 건너뛰어서, 백필을 다시 돌리기 전까지
    신규 상장 종목이 영원히 비어 있었다(실측 2026-09-11: 수급 9종목·신용잔고 8종목).

    상장 당일이면 첫 거래일과 마지막 거래일이 같다 — "이미 최신"으로 보고 건너뛰면
    첫 파일이 안 생긴다(엔에이치스팩34호가 2026-09-10 에 그랬다).
    """
    import scripts.update_data as update_data

    monkeypatch.setattr(
        update_data.bars, "load_master", lambda _name: _master([("386380", "0000500")])
    )
    monkeypatch.setattr(update_data.listing, "marcap_codes", lambda *a, **k: set())
    monkeypatch.setattr(update_data.listing, "first_traded", lambda _c, **_k: "20260901")

    asked: list[tuple[str, str, str]] = []

    class Module:
        FLOOR_DATE = "20070712"
        KisApiError = update_data.KisApiError

        @staticmethod
        def load_state() -> dict:
            return {}

        @staticmethod
        def collect_code(_client, code, first_date, last_date):
            asked.append((code, first_date, last_date))
            return pd.DataFrame([{"date": "20260908", "v": 1}])

    monkeypatch.setattr(update_data, "kis_client_for", lambda _m: object())
    result = update_data.update_kis(Module(), tmp_path, "date", "신용잔고", "20260901")

    assert len(asked) == 1
    assert asked[0][0] == "386380"
    assert asked[0][1] == "20260901"  # 첫 거래일부터 — 마지막 거래일과 같아도 받는다
    assert result["new_listing"] == 1
    assert result["called"] == 1
    assert (tmp_path / "386380.parquet").exists()


def test_new_listing_with_no_provider_data_is_marked_done(monkeypatch, tmp_path) -> None:
    """제공처에 자료가 없으면 표시를 남긴다 — 회차마다 다시 부르지 않게."""
    import scripts.update_data as update_data

    monkeypatch.setattr(
        update_data.bars, "load_master", lambda _name: _master([("282620", "0000500")])
    )
    monkeypatch.setattr(update_data.listing, "marcap_codes", lambda *a, **k: set())

    saved: dict = {}

    class Module:
        FLOOR_DATE = "20070712"
        KisApiError = update_data.KisApiError

        @staticmethod
        def load_state() -> dict:
            return {}

        @staticmethod
        def save_state(state) -> None:
            saved.update(state)

        @staticmethod
        def collect_code(_client, _code, _first, _last):
            return pd.DataFrame()

    monkeypatch.setattr(update_data, "kis_client_for", lambda _m: object())
    result = update_data.update_kis(Module(), tmp_path, "date", "신용잔고", "20260901")

    assert result["empty_responses"] == 1
    assert saved["282620"]["done"] is True
    assert saved["282620"]["rows"] == 0


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


def test_force_save_rewrites_file_even_when_row_count_is_unchanged(tmp_path) -> None:
    """반쪽 행을 정본으로 덮을 때는 날짜가 그대로여서 행 수가 안 늘어난다.

    평소 규칙(행 수가 그대로면 저장 안 함)에 맡기면 반쪽이 영영 남는다.
    """
    import scripts.update_data as update_data

    path = tmp_path / "000001.parquet"
    old = pd.DataFrame({"date": ["20260901"], "value": ["반쪽"], "_src": ["FHKST01010900"]})
    old.to_parquet(path, index=False)
    new = pd.DataFrame({"date": ["20260901"], "value": ["정본"]})

    assert update_data.merge_save(path, old, new, ["date"]) == 0
    assert pd.read_parquet(path)["value"].tolist() == ["반쪽"]  # 아직 안 덮였다

    update_data.merge_save(path, old, new, ["date"], force=True)
    assert pd.read_parquet(path)["value"].tolist() == ["정본"]


def test_partially_filled_days_are_refetched_by_the_full_source(monkeypatch, tmp_path) -> None:
    """낮에 대체 창구로 채운 구간을 정본 회차가 다시 받아 덮는지."""
    import scripts.update_data as update_data

    monkeypatch.setattr(
        update_data.bars, "load_master", lambda _name: _master([("000001", "0000500")])
    )
    monkeypatch.setattr(update_data.listing, "marcap_codes", lambda *a, **k: set())

    path = tmp_path / "000001.parquet"
    pd.DataFrame(
        {
            "date": ["20260828", "20260901"],
            "value": ["정본", "반쪽"],
            "_src": [None, "FHKST01010900"],
        }
    ).to_parquet(path, index=False)

    asked: list[str] = []
    saved: list[dict] = []

    class FakeModule:
        KisApiError = RuntimeError
        OUT_DIR = tmp_path

        @staticmethod
        def load_state() -> dict:
            return {}

        @staticmethod
        def load_partial() -> dict:
            return {"000001": "20260828"}

        @staticmethod
        def save_partial(state: dict) -> None:
            saved.append(state)

        @staticmethod
        def _thread_client():
            return object()

        @staticmethod
        def collect_code(_client, _code, since, _today):
            asked.append(since)
            return pd.DataFrame({"date": ["20260901"], "value": ["정본"]})

    # 저장된 마지막 날짜(20260901)가 마지막 거래일과 같다 — 고치기 전이면 통째로 건너뛰었다.
    result = update_data.update_kis(FakeModule, tmp_path, "date", "수급", "20260901")

    assert asked == ["20260828"], "반쪽 앞 날짜부터 다시 받아야 한다"
    assert result["called"] == 1 and result["skipped"] == 0
    assert result["repaired_partial"] == 1
    assert saved == [{}], "덮은 종목은 반쪽 표에서 빠져야 한다"
    after = pd.read_parquet(path)
    assert after.loc[after["date"] == "20260901", "value"].tolist() == ["정본"]


def test_adjusted_gate_looks_at_the_krx_gap_fill_too(monkeypatch) -> None:
    """수정주가 재생성 관문은 marcap 뿐 아니라 KRX 보충분까지 본다.

    실측 2026-09-11 사고: marcap 파일은 2026-09-03 에서 멈췄고 보충분이 2026-09-10 까지
    채워져 있었는데, 관문이 marcap 만 보고 "이미 최신"이라며 건너뛰었다. 그래서 백테스트가
    읽는 수정주가만 이틀 뒤처졌다.
    """
    import pandas as pd

    import scripts.update_data as update_data

    built: list[int] = []
    monkeypatch.setattr(
        update_data.build_adjusted, "source_last_date", lambda: "2026-09-10"
    )
    monkeypatch.setattr(
        update_data.derived, "derived_last_date", lambda: pd.Timestamp("2026-09-09")
    )
    monkeypatch.setattr(
        update_data.build_adjusted, "main", lambda _argv: built.append(1) or 0
    )

    result = update_data.update_adjusted()

    assert built == [1], "원천이 앞서 있으면 다시 만들어야 한다"
    assert "skipped" not in result


def test_adjusted_is_not_rebuilt_when_already_current(monkeypatch) -> None:
    import pandas as pd

    import scripts.update_data as update_data

    built: list[int] = []
    monkeypatch.setattr(
        update_data.build_adjusted, "source_last_date", lambda: "2026-09-10"
    )
    monkeypatch.setattr(
        update_data.derived, "derived_last_date", lambda: pd.Timestamp("2026-09-10")
    )
    monkeypatch.setattr(
        update_data.build_adjusted, "main", lambda _argv: built.append(1) or 0
    )

    result = update_data.update_adjusted()

    assert built == []
    assert result["last_date"] == "2026-09-10"


def test_new_listing_asks_from_its_first_trading_day_not_the_floor(monkeypatch, tmp_path) -> None:
    """바닥(1994·2007)부터 달라고 하면 KIS 가 상장 전 빈 행을 8,400여 줄 돌려준다.

    실측 2026-09-11: 10종목에 8,490행씩 담겼는데 값이 든 행은 2~22줄이었고, 종목당
    280여 콜을 헛썼다. 첫 거래일부터 달라고 하면 30행 한 페이지로 끝난다.
    """
    import scripts.update_data as update_data

    monkeypatch.setattr(
        update_data.bars, "load_master", lambda _name: _master([("386380", "0000500")])
    )
    monkeypatch.setattr(update_data.listing, "marcap_codes", lambda *a, **k: set())
    monkeypatch.setattr(update_data.listing, "first_traded", lambda _c, **_k: "20260904")

    asked: list[str] = []

    class Module:
        FLOOR_DATE = "20070712"
        KisApiError = update_data.KisApiError

        @staticmethod
        def load_state() -> dict:
            return {}

        @staticmethod
        def collect_code(_client, _code, first_date, _last):
            asked.append(first_date)
            return pd.DataFrame([{"date": "20260911", "v": 1}])

    monkeypatch.setattr(update_data, "kis_client_for", lambda _m: object())
    update_data.update_kis(Module(), tmp_path, "date", "수급", "20260911")

    assert asked == ["20260904"]
