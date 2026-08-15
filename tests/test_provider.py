"""데이터 창구 — 전략이 파일 경로를 몰라도 되게 (미션 문서 §19-4).

`as_of` 를 주면 그날 뒤 행을 잘라서 준다. "그때 알 수 있었던 정보만" 원칙을
전략이 아니라 창구가 강제한다 — 전략마다 따로 지키면 언젠가 하나가 빠진다.
"""

import pandas as pd
import pytest

from src.layer1_data.provider import DataProvider


def _write(dirpath, code: str, dates: list[str]) -> None:
    dirpath.mkdir(exist_ok=True, parents=True)
    n = len(dates)
    pd.DataFrame(
        {
            "Date": pd.to_datetime(dates),
            "Open": [1.0] * n,
            "High": [1.0] * n,
            "Low": [1.0] * n,
            "Close": [1.0] * n,
            "Volume": [1] * n,
        }
    ).to_parquet(dirpath / f"{code}.parquet")


class Test데이터_창구:
    def test_기준일_뒤_행은_안_준다(self, tmp_path) -> None:
        _write(tmp_path / "adjusted", "005930", ["2026-01-02", "2026-01-05", "2026-01-06"])
        got = DataProvider(root=tmp_path).daily("005930", as_of=pd.Timestamp("2026-01-05"))
        assert len(got) == 2
        assert got["Date"].max() == pd.Timestamp("2026-01-05")

    def test_기준일을_안_주면_전부_준다(self, tmp_path) -> None:
        _write(tmp_path / "adjusted", "005930", ["2026-01-02", "2026-01-05"])
        assert len(DataProvider(root=tmp_path).daily("005930")) == 2

    def test_종목코드_여섯자리로_맞춘다(self, tmp_path) -> None:
        _write(tmp_path / "adjusted", "005930", ["2026-01-02"])
        assert len(DataProvider(root=tmp_path).daily(5930)) == 1

    def test_없는_종목이면_이유를_말한다(self, tmp_path) -> None:
        (tmp_path / "adjusted").mkdir()
        with pytest.raises(FileNotFoundError, match="999999"):
            DataProvider(root=tmp_path).daily("999999")

    def test_상폐_종목_수급은_없다고_말한다(self, tmp_path) -> None:
        """수급은 상장 종목만 받을 수 있다 — 이유를 메시지에 담아야 헷갈리지 않는다."""
        (tmp_path / "supply").mkdir()
        with pytest.raises(FileNotFoundError, match="상장폐지"):
            DataProvider(root=tmp_path).supply("123456")


class Test수급_결손_알림:
    def test_수급이_몇_종목이나_빠졌는지_알려준다(self, tmp_path) -> None:
        _write(tmp_path / "adjusted", "005930", ["2026-01-02"])
        _write(tmp_path / "adjusted", "111111", ["2026-01-02"])
        _write(tmp_path / "supply", "005930", ["2026-01-02"])
        got = DataProvider(root=tmp_path).supply_coverage()
        assert got["일봉"] == 2
        assert got["수급"] == 1
        assert got["수급_없는_종목"] == 1

    def test_수급_폴더가_없어도_안_터진다(self, tmp_path) -> None:
        _write(tmp_path / "adjusted", "005930", ["2026-01-02"])
        got = DataProvider(root=tmp_path).supply_coverage()
        assert got["수급"] == 0
        assert got["수급_없는_종목"] == 1
