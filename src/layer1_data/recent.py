"""marcap 이후 최신 거래일 데이터 읽기 (BORB-44).

scripts/update_recent.py 가 만든 data/derived/recent/*.parquet 를 읽는다.
marcap 이 정본이므로 같은 날짜가 양쪽에 있으면 marcap 을 쓴다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

RECENT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "derived" / "recent"


def recent_meta() -> dict:
    f = RECENT_DIR / "meta.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text())
    except json.JSONDecodeError:
        return {}


def load_recent(after: pd.Timestamp | None = None) -> pd.DataFrame:
    """보충 데이터 전체(또는 after 이후). 없으면 빈 DataFrame."""
    if not RECENT_DIR.is_dir():
        return pd.DataFrame()
    frames = []
    for f in sorted(RECENT_DIR.glob("*.parquet")):
        if after is not None and pd.Timestamp(f.stem) <= after:
            continue
        frames.append(pd.read_parquet(f))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["Date", "Code"])


def merge_with_marcap(marcap: pd.DataFrame) -> pd.DataFrame:
    """marcap 프레임에 보충분을 덧붙인다. marcap 에 이미 있는 날짜는 건너뛴다."""
    if marcap.empty:
        return marcap
    last_date = marcap["Date"].max()
    extra = load_recent(after=last_date)
    if extra.empty:
        return marcap
    cols = [c for c in marcap.columns if c in extra.columns]
    extra = extra[cols]
    if "Dept" in marcap.columns and "Dept" not in extra.columns:
        extra = extra.assign(Dept=_last_dept(marcap, last_date, extra["Code"]))
    return pd.concat([marcap, extra], ignore_index=True).sort_values(["Date", "Code"])


def _last_dept(marcap: pd.DataFrame, last_date: pd.Timestamp, codes: pd.Series) -> pd.Series:
    """보충 구간의 소속부 = marcap 마지막 관측값.

    Dept 가 비면 관리종목·투자주의환기종목 제외가 통째로 풀린다(그 판정은 Dept 전용).
    그 며칠 사이의 신규 지정·해제는 놓치지만, 판정을 포기하는 것보다 낫다.
    """
    snap = marcap[marcap["Date"] == last_date]
    return codes.map(dict(zip(snap["Code"], snap["Dept"], strict=True)))
