"""KRX Open API 파싱·관문·marcap 공백 채우기 (ADR-0002 개정 2026-08-18).

실제 호출은 하지 않는다 — 응답 모양을 흉내 내서 marcap 스키마로 옮기는 부분과
"마지막 거래일" 판정, 공백 채우기의 파일 정리 규칙만 본다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.layer1_data import krx_gapfill
from src.layer1_data import krx_openapi as krx

ROW = {
    "BAS_DD": "20260814",
    "ISU_CD": "005930",
    "ISU_NM": "삼성전자",
    "MKT_NM": "KOSPI",
    "SECT_TP_NM": "",
    "TDD_CLSPRC": "274,500",
    "CMPPREVDD_PRC": "6,500",
    "FLUC_RT": "2.43",
    "TDD_OPNPRC": "275,000",
    "TDD_HGPRC": "275,500",
    "TDD_LWPRC": "266,000",
    "ACC_TRDVOL": "21,669,476",
    "ACC_TRDVAL": "5,874,450,961,500",
    "MKTCAP": "1,604,803,477,896,000",
    "LIST_SHRS": "5,846,278,608",
}
HALTED = {**ROW, "ISU_CD": "KR7000001000", "ISU_NM": "거래정지", "TDD_CLSPRC": "-"}
KOSDAQ_ROW = {
    **ROW,
    "ISU_CD": "035720",
    "ISU_NM": "카카오",
    "MKT_NM": "KOSDAQ",
    "SECT_TP_NM": "관리종목(소속부없음)",
    "TDD_CLSPRC": "50,000",
    "MKTCAP": "1,000",
    "LIST_SHRS": "10",
}


def test_to_marcap_frame_matches_marcap_shape() -> None:
    df = krx.to_marcap_frame([ROW, HALTED], "KOSPI", "20260814")
    assert list(df.columns) == krx.MARCAP_COLS
    assert len(df) == 1  # 종가 '-' 는 뺀다
    r = df.iloc[0]
    assert r["Code"] == "005930" and r["Market"] == "KOSPI" and r["MarketId"] == "STK"
    assert r["Close"] == 274500.0 and r["Volume"] == 21669476.0
    assert r["Marcap"] == 1604803477896000.0 and r["Stocks"] == 5846278608
    assert r["Date"] == pd.Timestamp("2026-08-14")
    assert r["Dept"] is None


def test_kosdaq_dept_kept_for_watchlist_exclusion() -> None:
    """소속부(관리종목 표기)를 그대로 넘겨야 보충 구간에서 관리종목 제외가 산다."""
    df = krx.to_marcap_frame([KOSDAQ_ROW], "KOSDAQ", "20260814")
    assert df.iloc[0]["Dept"] == "관리종목(소속부없음)"
    assert df.iloc[0]["MarketId"] == "KSQ"


def test_short_code_from_isin() -> None:
    assert krx._short_code("KR7005930003") == "005930"
    assert krx._short_code("5930") == "005930"


def test_snapshot_ranks_by_marcap(monkeypatch) -> None:
    def fake_rows(market, bas_dd, key, session=None):
        return {"KOSPI": [ROW], "KOSDAQ": [KOSDAQ_ROW], "KONEX": []}[market]

    monkeypatch.setattr(krx, "fetch_rows", fake_rows)
    df = krx.snapshot("20260814", "k")
    assert list(df["Code"]) == ["005930", "035720"]
    assert list(df["Rank"]) == [1, 2]


def test_snapshot_empty_on_holiday(monkeypatch) -> None:
    monkeypatch.setattr(krx, "fetch_rows", lambda *a, **k: [])
    assert krx.snapshot("20260815", "k").empty


def test_last_trading_day_walks_back(monkeypatch) -> None:
    def fake_rows(market, bas_dd, key, session=None):
        return [ROW] if bas_dd <= "20260814" else []

    monkeypatch.setattr(krx, "fetch_rows", fake_rows)
    assert krx.last_trading_day("k", today=date(2026, 8, 17)) == "20260814"
    assert krx.last_trading_day("k", today=date(2026, 9, 17), lookback=3) == ""


def test_fetch_rows_requires_key() -> None:
    with pytest.raises(krx.KrxApiError):
        krx.fetch_rows("KOSPI", "20260814", "")


def test_fill_gap_saves_new_days_and_drops_superseded(tmp_path, monkeypatch) -> None:
    """marcap 이 8/14 까지면 8/17·8/18 만 부르고, 8/14 이하 보충 파일은 지운다."""
    monkeypatch.setattr(krx_gapfill, "marcap_last_date", lambda: pd.Timestamp("2026-08-14"))
    (tmp_path / "2026-08-13.parquet").write_bytes(b"x")  # marcap 이 따라잡은 옛 보충분
    calls: list[str] = []

    def fake_snapshot(bas_dd, key):
        calls.append(bas_dd)
        if bas_dd == "20260818":
            return pd.DataFrame(columns=krx.MARCAP_COLS)  # 아직 집계 전
        return krx.to_marcap_frame([ROW], "KOSPI", bas_dd)

    out = krx_gapfill.fill_marcap_gap(
        "k", today=date(2026, 8, 18), out_dir=tmp_path, snapshot=fake_snapshot
    )
    assert calls == ["20260817", "20260818"]  # 주말(15·16)은 부르지 않는다
    assert out["saved"] == ["2026-08-17"] and out["removed"] == 1
    assert (tmp_path / "2026-08-17.parquet").exists()
    assert not (tmp_path / "2026-08-13.parquet").exists()
    meta = (tmp_path / "meta.json").read_text(encoding="utf-8")
    assert '"source": "krx"' in meta and '"amount_is_approx": false' in meta

    # 두 번째 회차 — 이미 받은 8/17 은 다시 부르지 않는다
    calls.clear()
    krx_gapfill.fill_marcap_gap(
        "k", today=date(2026, 8, 18), out_dir=tmp_path, snapshot=fake_snapshot
    )
    assert calls == ["20260818"]


def test_fill_gap_without_key_skips(tmp_path) -> None:
    out = krx_gapfill.fill_marcap_gap("", out_dir=tmp_path)
    assert "skipped" in out
