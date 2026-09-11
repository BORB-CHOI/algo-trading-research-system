"""상장 목록에서 주식만 가려내는 규칙 — `layer1_market_data.listing`.

이 규칙이 틀리면 둘 중 하나가 난다.

* 너무 넓으면 유니버스 밖 ETF·ETN 1,535종목의 전 이력을 수급·신용잔고로 새로 받는다.
* 너무 좁으면 백필 뒤에 상장한 종목이 영원히 빈 채로 남는다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer1_market_data import listing


def _master(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame([{"sCode": c, "sParvalue": p} for c, p in rows])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0000500", 500), ("0000000", 0), ("", 0), ("   ", 0), ("없음", 0), (None, 0)],
)
def test_par_value_reads_the_master_field(raw, expected) -> None:
    assert listing.par_value(raw) == expected


def test_etf_and_etn_are_not_stocks(monkeypatch) -> None:
    """ETF·ETN 은 무액면이고 marcap 에도 없다 — 둘 다 아니면 주식이 아니다."""
    monkeypatch.setattr(listing, "marcap_codes", lambda *a, **k: {"005930"})

    codes = listing.stock_codes(_master([("069500", "0000000"), ("005930", "0000100")]))

    assert codes == {"005930"}


def test_a_stock_marcap_has_not_caught_up_with_is_still_a_stock(monkeypatch) -> None:
    """갓 상장한 종목은 marcap 에 아직 없다 — 액면가가 받쳐 준다.

    실측 2026-09-11: 엔에이치스팩34호·스카이랩스가 이 경우였다.
    """
    monkeypatch.setattr(listing, "marcap_codes", lambda *a, **k: set())

    codes = listing.stock_codes(_master([("386380", "0000500"), ("069500", "0000000")]))

    assert codes == {"386380"}


def test_a_stock_with_no_par_value_is_still_a_stock(monkeypatch) -> None:
    """무액면 주식(맥쿼리인프라·외국기업 950xxx)은 액면가가 0 이다 — marcap 이 받쳐 준다."""
    monkeypatch.setattr(listing, "marcap_codes", lambda *a, **k: {"088980", "950260"})

    codes = listing.stock_codes(_master([("088980", "0000000"), ("950260", "0000000")]))

    assert codes == {"088980", "950260"}


def test_empty_listing_gives_nothing(monkeypatch) -> None:
    monkeypatch.setattr(listing, "marcap_codes", lambda *a, **k: {"005930"})

    assert listing.stock_codes(pd.DataFrame()) == set()
    assert listing.stock_codes(None) == set()


def test_marcap_is_read_only_once_when_handed_in(monkeypatch) -> None:
    """한 회차에서 여러 번 부를 때 marcap 을 다시 읽지 않는다."""
    calls = []
    monkeypatch.setattr(listing, "marcap_codes", lambda *a, **k: calls.append(1) or set())

    codes = listing.stock_codes(_master([("005930", "0000000")]), in_marcap={"005930"})

    assert codes == {"005930"}
    assert calls == []


def test_first_traded_reads_our_own_daily_bars(tmp_path) -> None:
    """신규 상장 종목을 어디서부터 받을지는 우리 일봉의 첫 거래일로 정한다."""
    pd.DataFrame({"bsop_date": ["20260904", "20260907", "20260911"]}).to_parquet(
        tmp_path / "386380.parquet", index=False
    )

    assert listing.first_traded("386380", bars_dir=tmp_path) == "20260904"


def test_first_traded_is_empty_when_there_are_no_bars(tmp_path) -> None:
    assert listing.first_traded("000001", bars_dir=tmp_path) == ""
