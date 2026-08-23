"""KIS 멀티시세 → 나무 일봉 행 변환, 시각 관문, 액면분할 감지 (오너 결정 2026-08-18)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from src.layer1_data import kis_snapshot as ks

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

ITEM = {
    "inter_shrn_iscd": "005930",
    "inter2_prpr": "274500",
    "inter2_oprc": "275000",
    "inter2_hgpr": "275500",
    "inter2_lwpr": "266000",
    "acml_vol": "21669476",
    "acml_tr_pbmn": "5874450961500",
    "inter2_prdy_clpr": "268000",
    "inter2_sdpr": "268000",
}
HALTED = {**ITEM, "inter_shrn_iscd": "000001", "inter2_prpr": "0", "acml_vol": "0"}


def test_multi_params_numbers_each_code() -> None:
    p = ks.multi_params("unt", ["5930", "000660"])
    assert p["FID_COND_MRKT_DIV_CODE_1"] == "UN" and p["FID_INPUT_ISCD_1"] == "005930"
    assert p["FID_COND_MRKT_DIV_CODE_2"] == "UN" and p["FID_INPUT_ISCD_2"] == "000660"
    with pytest.raises(ValueError):
        ks.multi_params("krx", ["0"] * 31)


def test_to_namuh_row_shape() -> None:
    row = ks.to_namuh_row(ITEM, "20260814")
    assert row is not None
    assert list(row) == ks.NAMUH_DAY_COLS
    assert row["bsop_date"] == "20260814" and row["stck_prpr"] == "274500"
    assert row["vol"] == "21669476" and row["tr_pbmn"] == "5874450961500"
    assert row["stck_sdpr"] == "268000"


def test_to_namuh_row_none_when_no_trade() -> None:
    assert ks.to_namuh_row(HALTED, "20260814") is None


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get(self, path, tr_id, params):
        self.calls.append(params)
        n = sum(1 for k in params if k.startswith("FID_INPUT_ISCD_"))
        out = [ITEM] if n else []
        if n > 1:
            out.append(HALTED)

        class R:
            body = {"rt_cd": "0", "output": out}

        return R()


def test_fetch_snapshot_chunks_by_30() -> None:
    c = FakeClient()
    snap = ks.fetch_snapshot(c, "krx", [f"{i:06d}" for i in range(65)], "20260814")
    assert len(c.calls) == 3
    assert snap["005930"]["row"]["stck_prpr"] == "274500"
    assert snap["005930"]["prdy_clpr"] == "268000"
    assert snap["000001"]["row"] is None  # 체결 없음


@pytest.fixture
def ud():
    pytest.importorskip("nhplug", reason="나무 SDK 없이도 도는 환경이 있다")
    import update_data

    return update_data


def test_kis_day_ready_gate(ud) -> None:
    assert ud.kis_day_ready("20260818", datetime(2026, 8, 18, 20, 5))
    assert not ud.kis_day_ready("20260818", datetime(2026, 8, 18, 19, 59))  # NXT 애프터마켓 전
    assert not ud.kis_day_ready("20260817", datetime(2026, 8, 18, 21, 0))  # 오늘이 거래일이 아님
    assert not ud.kis_day_ready("", datetime(2026, 8, 18, 21, 0))


def test_split_detected_when_prev_close_differs(ud) -> None:
    old = pd.DataFrame({"bsop_date": ["20260813", "20260814"], "stck_prpr": ["268000", "274500"]})
    assert not ud._split_detected(old, "274500")
    assert ud._split_detected(old, "27450")  # 10:1 분할 — 증권사가 과거를 접었다
    assert not ud._split_detected(old, "")  # 전일종가가 없으면 판단 안 함


def test_kis_bars_ready_gate(ud) -> None:
    assert ud.kis_bars_ready("20260818", datetime(2026, 8, 18, 20, 5))
    assert not ud.kis_bars_ready("20260818", datetime(2026, 8, 18, 19, 0))
    assert ud.kis_bars_ready("20260818", datetime(2026, 8, 19, 7, 30))  # 다음날 장 열리기 전
    assert not ud.kis_bars_ready(
        "20260818", datetime(2026, 8, 19, 10, 0)
    )  # 장중 — 오늘 체결이 섞인다
    assert ud.kis_bars_ready("20260814", datetime(2026, 8, 15, 12, 0))  # 토요일


def test_week_rules(ud, tmp_path) -> None:
    from datetime import date

    assert ud._monday("20260814") == date(2026, 8, 10)
    assert ud._spans_year_end(date(2025, 12, 29))  # 12/29~1/4 — 나무는 둘로 쪼갠다
    assert not ud._spans_year_end(date(2026, 8, 10))
    day = tmp_path / "005930.parquet"
    pd.DataFrame({"bsop_date": ["20260810", "20260811", "20260812", "20260814"]}).to_parquet(day)
    assert ud._week_label(day, date(2026, 8, 10)) == "20260814"  # 그 주 마지막 거래일
    assert ud._week_label(day, date(2026, 8, 17)) == ""  # 그 주 일봉이 없다
    assert ud._week_label(tmp_path / "없음.parquet", date(2026, 8, 10)) == ""


def test_merge_period_save_replaces_same_week(ud, tmp_path) -> None:
    """수요일에 붙인 진행 주봉(0812)은 목요일 봉(0813)이 오면 지워진다 — 같은 주 두 봉 금지."""
    cols = ["bsop_date", "stck_prpr"]
    old = pd.DataFrame([["20260807", "1"], ["20260812", "2"]], columns=cols)
    new = pd.DataFrame([["20260813", "3"]], columns=cols)
    path = tmp_path / "w.parquet"
    ud.merge_period_save(path, old, new, "week")
    assert pd.read_parquet(path)["bsop_date"].tolist() == ["20260807", "20260813"]
    oldm = pd.DataFrame([["202607", "1"], ["202608", "2"]], columns=cols)
    ud.merge_period_save(path, oldm, pd.DataFrame([["202608", "9"]], columns=cols), "month")
    got = pd.read_parquet(path)
    assert got["bsop_date"].tolist() == ["202607", "202608"] and got["stck_prpr"].tolist() == [
        "1",
        "9",
    ]
