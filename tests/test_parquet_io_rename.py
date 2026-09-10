"""저장 — 윈도우에서 이름 바꾸기가 튕길 때 다시 해 보는지."""

from __future__ import annotations

import pandas as pd
import pytest


def test_rename_is_retried_when_windows_refuses(monkeypatch, tmp_path) -> None:
    """백신·인덱서가 잡고 있으면 PermissionError 가 난다 — 곧 놓으므로 다시 하면 된다."""
    from src.layer1_data import parquet_io

    calls = {"n": 0}
    real = parquet_io.os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "액세스가 거부되었습니다")
        return real(src, dst)

    monkeypatch.setattr(parquet_io.os, "replace", flaky)
    monkeypatch.setattr(parquet_io.time, "sleep", lambda _s: None)

    path = tmp_path / "x.parquet"
    parquet_io.save(pd.DataFrame({"a": [1, 2]}), path)

    assert calls["n"] == 3, "두 번 튕기고 세 번째에 성공해야 한다"
    assert len(pd.read_parquet(path)) == 2


def test_rename_gives_up_after_enough_tries(monkeypatch, tmp_path) -> None:
    """영영 안 놓으면 끝없이 붙잡고 있지 말고 위로 올린다."""
    from src.layer1_data import parquet_io

    calls = {"n": 0}

    def always(src, dst):
        calls["n"] += 1
        raise PermissionError(5, "액세스가 거부되었습니다")

    monkeypatch.setattr(parquet_io.os, "replace", always)
    monkeypatch.setattr(parquet_io.time, "sleep", lambda _s: None)

    with pytest.raises(PermissionError):
        parquet_io.save(pd.DataFrame({"a": [1]}), tmp_path / "x.parquet")

    assert calls["n"] == parquet_io.RENAME_RETRY
    assert not list(tmp_path.glob("*.tmp")), "임시 파일은 치우고 나가야 한다"
