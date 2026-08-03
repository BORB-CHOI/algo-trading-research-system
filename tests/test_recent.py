"""marcap 이후 보충분 이어붙이기 (BORB-44, ADR-0002 개정).

보충 parquet 에 Dept 가 없으면 그 구간에서 관리종목 제외가 통째로 풀린다
(`is_watchlisted()` 는 Dept 전용 판정이라 컬럼이 없으면 전부 False 를 돌려준다).
실측에서 118 종목이 그대로 통과했던 구멍이라 회귀 테스트로 박아둔다.
"""

from __future__ import annotations

import pandas as pd

from src.layer1_data.exclusions import is_watchlisted
from src.layer1_data.recent import merge_with_marcap

MARCAP_COLS = ["Date", "Code", "Name", "Market", "Dept", "Close"]
WATCH = "관리종목(소속부없음)"


def marcap_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            (pd.Timestamp("2026-07-16"), "001840", "이화공영", "KOSPI", WATCH, 1000.0),
            (pd.Timestamp("2026-07-16"), "005930", "삼성전자", "KOSPI", None, 70000.0),
        ],
        columns=MARCAP_COLS,
    )


def recent_frame(with_dept: bool) -> pd.DataFrame:
    rows = [
        (pd.Timestamp("2026-08-03"), "001840", "이화공영", "KOSPI", 1100.0),
        (pd.Timestamp("2026-08-03"), "005930", "삼성전자", "KOSPI", 71000.0),
    ]
    df = pd.DataFrame(rows, columns=["Date", "Code", "Name", "Market", "Close"])
    return df.assign(Dept=[WATCH, None]) if with_dept else df


def merged(monkeypatch, with_dept: bool) -> pd.DataFrame:
    monkeypatch.setattr(
        "src.layer1_data.recent.load_recent", lambda after=None: recent_frame(with_dept)
    )
    return merge_with_marcap(marcap_frame())


def test_dept_filled_from_marcap_when_missing(monkeypatch) -> None:
    """보충분에 Dept 가 없으면 marcap 마지막 관측값으로 채운다."""
    out = merged(monkeypatch, with_dept=False)
    day = out[out["Date"] == pd.Timestamp("2026-08-03")]
    assert len(day) == 2
    assert is_watchlisted(day).sum() == 1  # 이화공영만 걸린다
    assert day.set_index("Code").loc["001840", "Dept"] == WATCH


def test_dept_in_recent_file_is_kept(monkeypatch) -> None:
    """보충분이 Dept 를 들고 오면 그대로 쓴다(폴백이 덮지 않는다)."""
    out = merged(monkeypatch, with_dept=True)
    day = out[out["Date"] == pd.Timestamp("2026-08-03")]
    assert is_watchlisted(day).sum() == 1


def test_marcap_dates_are_not_duplicated(monkeypatch) -> None:
    """marcap 이 정본 — 보충분은 marcap 최신일 이후만 들어온다."""
    out = merged(monkeypatch, with_dept=False)
    assert out.groupby(["Date", "Code"]).size().max() == 1
    assert len(out[out["Date"] == pd.Timestamp("2026-07-16")]) == 2
