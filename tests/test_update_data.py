"""증분 갱신이 **파일을 통째로 열지 않는지** 지킨다.

여기가 무너지면 "받을 게 하나도 없어도 몇 분씩 걸리는" 갱신으로 되돌아간다.
실측 2026-08-16: 일·주·월봉 증분이 만지는 파일 16,530개 × 15.3ms = 4.2분(호출 0건인데도).
날짜 열만 읽으면 1.6ms 라 0.5분이다.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def last_date_of():
    pytest.importorskip("nhplug", reason="나무 SDK 없이도 도는 환경이 있다")
    from update_data import last_date_of as fn

    return fn


class Test마지막_날짜_읽기:
    def test_저장된_마지막_날짜를_준다(self, tmp_path, last_date_of) -> None:
        f = tmp_path / "005930.parquet"
        pd.DataFrame({"bsop_date": ["20260812", "20260814", "20260813"]}).to_parquet(f)
        assert last_date_of(f, "bsop_date") == "20260814"

    def test_파일이_없으면_빈_문자열(self, tmp_path, last_date_of) -> None:
        assert last_date_of(tmp_path / "없음.parquet", "bsop_date") == ""

    def test_빈_파일이면_빈_문자열(self, tmp_path, last_date_of) -> None:
        f = tmp_path / "빈.parquet"
        pd.DataFrame({"bsop_date": pd.Series([], dtype="object")}).to_parquet(f)
        assert last_date_of(f, "bsop_date") == ""

    def test_깨진_파일이어도_안_터진다(self, tmp_path, last_date_of) -> None:
        """파일 하나 때문에 전체 갱신이 멈추면 안 된다."""
        f = tmp_path / "깨짐.parquet"
        f.write_bytes(b"not parquet")
        assert last_date_of(f, "bsop_date") == ""

    def test_없는_열을_물어도_안_터진다(self, tmp_path, last_date_of) -> None:
        f = tmp_path / "다른모양.parquet"
        pd.DataFrame({"딴열": [1]}).to_parquet(f)
        assert last_date_of(f, "bsop_date") == ""

    def test_날짜_열만_읽는다(self, tmp_path, last_date_of, monkeypatch) -> None:
        """이게 핵심이다 — pandas 로 통째로 열면 10배 느려진다."""
        import pyarrow.parquet as pq

        f = tmp_path / "005930.parquet"
        pd.DataFrame({"bsop_date": ["20260814"], "open": [1], "close": [2]}).to_parquet(f)

        seen: list[list[str] | None] = []
        real = pq.read_table

        def spy(path, **kw):
            seen.append(kw.get("columns"))
            return real(path, **kw)

        monkeypatch.setattr(pq, "read_table", spy)
        last_date_of(f, "bsop_date")
        assert seen == [["bsop_date"]]
