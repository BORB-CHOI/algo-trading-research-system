"""재무 요약 읽기 — 요약 파일이 바뀌면 서버를 다시 띄우지 않아도 새로 읽는다."""

from __future__ import annotations

import os

import pandas as pd


def test_summary_is_reread_when_the_file_changes(monkeypatch, tmp_path) -> None:
    """갱신이 요약을 새로 만들어도 서버가 예전 빈 표를 들고 있으면 재무 조건이 0종목이다."""
    from src.layer1_market_data import dart

    path = tmp_path / "financials.parquet"
    monkeypatch.setattr(dart, "SUMMARY_PATH", path)
    monkeypatch.setattr(dart, "_summary_cache", None)
    monkeypatch.setattr(dart, "_summary_key", None)

    assert dart.load_summary().empty, "파일이 없으면 빈 표"

    pd.DataFrame({"code": ["005930"], "year": [2025]}).to_parquet(path, index=False)
    assert dart.load_summary()["code"].tolist() == ["005930"], "파일이 생기면 바로 읽는다"

    pd.DataFrame({"code": ["005930", "000660"], "year": [2025, 2025]}).to_parquet(path, index=False)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert len(dart.load_summary()) == 2, "파일이 바뀌면 다시 읽는다"
