"""일봉 정본 — 상장 종목은 나무 수집본, 상장폐지·미수집은 marcap 보정본 (오너 결정 2026-08-16).

## 왜 나무가 먼저인가 (실측 2026-08-16)

표본 250종목을 2026-01~08 구간에서 대조했더니 **18종목(7.6%)** 에서 우리 marcap 보정과
나무 값이 어긋났다. 어긋난 비율이 0.1·0.2·0.5 처럼 딱 떨어졌다 — 액면병합이다.

부산가스(277410): 10:1 병합인데 우리 보정 계수가 1.0 그대로라 800원 → 6,040원,
**7.5배 가짜 급등**으로 찍혔다. 나무는 과거 가격을 10배로 접어 정상이었다.

나무는 증권사가 직접 보정한 값이라 추측이 없다. 다만 **상장폐지 종목이 없어서**
(마스터 4,298 = 현재 상장분) 망한 회사는 marcap 보정본으로 간다.
"""

import pandas as pd
import pytest

from src.layer1_data.daily import MARCAP, NAMUH, daily_bars, daily_source

COLS = ["Date", "Open", "High", "Low", "Close", "Volume", "Amount"]


def _namuh(dirpath, code, dates, close):
    d = dirpath / "krx" / "day"
    d.mkdir(parents=True, exist_ok=True)
    n = len(dates)
    pd.DataFrame(
        {
            "bsop_date": dates,
            "stck_oprc": close,
            "stck_hgpr": close,
            "stck_lwpr": close,
            "stck_prpr": close,
            "vol": [10] * n,
            "tr_pbmn": [1000] * n,
        }
    ).to_parquet(d / f"{code}.parquet")


def _adjusted(dirpath, code, dates, close):
    dirpath.mkdir(parents=True, exist_ok=True)
    n = len(dates)
    pd.DataFrame(
        {
            "Date": pd.to_datetime(dates),
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": [10.0] * n,
            "Amount": [1000.0] * n,
            "Marcap": [1e11] * n,
            "Stocks": [1000] * n,
        }
    ).to_parquet(dirpath / f"{code}.parquet")


@pytest.fixture
def dirs(tmp_path):
    return tmp_path / "namuh_bars", tmp_path / "adjusted"


class Test정본_고르기:
    def test_상장_종목은_나무를_쓴다(self, dirs) -> None:
        bars, adj = dirs
        _namuh(bars, "005930", ["20260813", "20260814"], [7760.0, 7800.0])
        _adjusted(adj, "005930", ["2026-08-13"], [776.0])  # 병합 못 잡은 옛 값
        got = daily_bars("005930", bars_dir=bars, adjusted_dir=adj)
        assert list(got["Close"]) == [7760.0, 7800.0]
        assert daily_source("005930", bars_dir=bars, adjusted_dir=adj) == NAMUH

    def test_나무에_없으면_marcap_보정본(self, dirs) -> None:
        """상장폐지 종목이 여기로 온다 — 망한 회사를 빼면 백테스트가 부풀려진다."""
        bars, adj = dirs
        bars.mkdir(parents=True, exist_ok=True)
        _adjusted(adj, "123456", ["2010-05-03", "2010-05-04"], [500.0, 400.0])
        got = daily_bars("123456", bars_dir=bars, adjusted_dir=adj)
        assert list(got["Close"]) == [500.0, 400.0]
        assert daily_source("123456", bars_dir=bars, adjusted_dir=adj) == MARCAP

    def test_둘_다_없으면_None(self, dirs) -> None:
        bars, adj = dirs
        bars.mkdir(parents=True, exist_ok=True)
        adj.mkdir(parents=True, exist_ok=True)
        assert daily_bars("999999", bars_dir=bars, adjusted_dir=adj) is None

    def test_종목코드는_여섯자리로_맞춘다(self, dirs) -> None:
        bars, adj = dirs
        _namuh(bars, "005930", ["20260814"], [7800.0])
        assert daily_bars(5930, bars_dir=bars, adjusted_dir=adj) is not None

    def test_어느_쪽이든_같은_모양으로_준다(self, dirs) -> None:
        """엔진이 소스에 따라 다르게 굴면 안 된다 — 열 이름과 정렬이 같아야 한다."""
        bars, adj = dirs
        _namuh(bars, "000001", ["20260814", "20260813"], [100.0, 90.0])  # 일부러 거꾸로
        _adjusted(adj, "000002", ["2026-08-13", "2026-08-14"], [90.0, 100.0])
        a = daily_bars("000001", bars_dir=bars, adjusted_dir=adj)
        b = daily_bars("000002", bars_dir=bars, adjusted_dir=adj)
        for df, code in ((a, "000001"), (b, "000002")):
            assert set(COLS) <= set(df.columns)
            assert df["Date"].is_monotonic_increasing  # 날짜순
            assert list(df["Code"].unique()) == [code]  # 엔진이 Code 로 종목을 읽는다

    def test_빈_나무_파일은_marcap_으로_넘긴다(self, dirs) -> None:
        """수집이 실패해 0행짜리가 남았을 때 빈 표를 주면 그 종목이 통째로 빠진다."""
        bars, adj = dirs
        _namuh(bars, "000003", [], [])
        _adjusted(adj, "000003", ["2026-08-13"], [500.0])
        got = daily_bars("000003", bars_dir=bars, adjusted_dir=adj)
        assert len(got) == 1
        assert daily_source("000003", bars_dir=bars, adjusted_dir=adj) == MARCAP
