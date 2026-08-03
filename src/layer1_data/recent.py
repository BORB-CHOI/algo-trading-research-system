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
    extra = load_recent(after=marcap["Date"].max())
    if extra.empty:
        return marcap
    cols = [c for c in marcap.columns if c in extra.columns]
    return pd.concat([marcap, extra[cols]], ignore_index=True).sort_values(["Date", "Code"])
