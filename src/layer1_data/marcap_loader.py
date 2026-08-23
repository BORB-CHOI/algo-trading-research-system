"""marcap 데이터셋 로드 + 무결성 검증 (ADR-0002).

marcap = 날짜별 전종목 스냅샷.

핵심: 망해서 상장폐지된 회사도 자기 시절 행에 그대로 남아 있다.
지금 살아있는 종목만 모은 데이터로 백테스트를 하면 "망한 회사는 처음부터 안 샀다"는
뜻이 되어 수익률이 실제보다 부풀려진다 (= 살아남은 것만 보는 착시, survivorship bias).
같은 이유로 각 시점에 실제 거래되던 종목만 보이는 것도 중요하다 (point-in-time universe).

이 모듈은 그 전제가 실제로 성립하는지 검증한다. 성립하지 않으면 ADR-0002를 다시 연다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

MARCAP_DIR = Path("data/marcap/data")

# 백테스트 구간 (CLAUDE.md: 2017-01 ~ 현재)
BACKTEST_START = 2017

# Marcap = Close × Stocks 정합성 허용 오차 (부동소수점/반올림)
MARCAP_RTOL = 1e-6


def load_years(start: int, end: int, marcap_dir: Path = MARCAP_DIR) -> pd.DataFrame:
    """연도별 parquet을 읽어 하나로 합친다. end 포함. 종목코드는 6자리로 맞춘다."""
    frames = []
    for year in range(start, end + 1):
        path = marcap_dir / f"marcap-{year}.parquet"
        if not path.exists():
            continue
        frames.append(pd.read_parquet(path))
    if not frames:
        raise FileNotFoundError(f"{marcap_dir} 에 {start}~{end} parquet 없음")
    df = pd.concat(frames, ignore_index=True)
    df["Code"] = normalize_code(df["Code"])
    return df


def normalize_code(code: pd.Series) -> pd.Series:
    """종목코드를 6자리로 통일한다 (앞자리 0 복원).

    marcap은 2000-05-29 이전 코드를 숫자로 저장해 앞자리 0이 날아가 있다
    (삼성전자 005930 → "5930"). 그대로 두면 6자리 체계로 바뀌는 2000-05-29에
    1,460개 종목이 한꺼번에 "상장폐지"된 것처럼 보인다 — 실제로는 코드만 바뀐 것.
    """
    return code.str.zfill(6)


SYMBOL_MASTER_CACHE = Path("data/derived/_symbol_master.parquet")


def symbol_master(
    marcap_dir: Path = MARCAP_DIR, cache: Path | None = SYMBOL_MASTER_CACHE
) -> pd.DataFrame:
    """검색용 종목 목록 — **상장폐지 종목까지 전부**. (Code, Name, Market, LastDate, Delisted)

    오너 지시 2026-08-23: "검색은 상장폐지까지 다 볼 수 있게 해야지. 그냥 상장폐지 태그만
    붙어도 될 거 같은데."

    가장 최근 연도 한 해만 보면 지금 살아 있는 종목밖에 안 나온다 — 롯데푸드 002270
    (2022-07-19 상폐)이 검색에 안 잡히던 게 그것이다. 데이터는 1995년부터 다 있는데
    목록에서만 빠져 있었다.

    이름·시장은 그 종목이 **마지막으로 거래된 날** 기준이다(사명이 바뀐 종목은 마지막 이름).
    `Delisted` = 마지막 거래일이 데이터 전체의 마지막 거래일보다 이르다.

    32개 연도를 다 훑어 4.6초 걸리므로(실측 2026-08-23, 5,481종목) 결과를 parquet 로
    남긴다. 새 연도 파일이 들어오면(가장 최근 연도 파일이 캐시보다 새로우면) 다시 만든다.
    """
    years = available_years(marcap_dir)
    if not years:
        return pd.DataFrame(columns=["Code", "Name", "Market", "LastDate", "Delisted"])
    newest = marcap_dir / f"marcap-{years[-1]}.parquet"
    if cache is not None and cache.exists() and cache.stat().st_mtime >= newest.stat().st_mtime:
        return pd.read_parquet(cache)

    frames = []
    for year in years:
        path = marcap_dir / f"marcap-{year}.parquet"
        if not path.exists():
            continue
        d = pd.read_parquet(path, columns=["Date", "Code", "Name", "Market"])
        d["Code"] = normalize_code(d["Code"])
        # 연도 안에서 먼저 줄인다 — 32년치를 통째로 concat 하면 수천만 행이 된다.
        frames.append(d.sort_values("Date").drop_duplicates("Code", keep="last"))
    m = pd.concat(frames, ignore_index=True).sort_values("Date")
    m = m.drop_duplicates("Code", keep="last").reset_index(drop=True)
    m = m.rename(columns={"Date": "LastDate"})
    m["Delisted"] = m["LastDate"] < m["LastDate"].max()
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        m.to_parquet(cache, index=False)
    return m


def available_years(marcap_dir: Path = MARCAP_DIR) -> list[int]:
    return sorted(int(p.stem.split("-")[1]) for p in marcap_dir.glob("marcap-*.parquet"))


@dataclass
class DelistingEvidence:
    """망해서 사라진 회사의 흔적 — 어느 시점엔 있었는데 이후 사라진 종목."""

    total_codes: int
    survivors: int  # 마지막 날짜에도 살아있는 종목
    disappeared: int  # 중간에 사라진 종목 = 상폐 후보
    samples: pd.DataFrame


def find_delisted(df: pd.DataFrame) -> DelistingEvidence:
    """마지막 거래일 이전에 사라진 종목을 찾는다 = 망한 회사가 데이터에 남아 있는지 확인.

    disappeared > 0 이어야 한다. 0이면 지금 살아있는 종목만 담긴 데이터라는 뜻이고,
    그런 데이터로 낸 백테스트 수익률은 전부 부풀려져 있어 쓸 수 없다.
    """
    last_date = df["Date"].max()
    last_seen = df.groupby("Code")["Date"].max()
    alive = last_seen[last_seen == last_date].index

    gone = last_seen[last_seen < last_date]
    names = df.drop_duplicates("Code").set_index("Code")["Name"]
    samples = (
        pd.DataFrame({"last_date": gone}).join(names).sort_values("last_date", ascending=False)
    )

    return DelistingEvidence(
        total_codes=int(last_seen.size),
        survivors=int(alive.size),
        disappeared=int(gone.size),
        samples=samples,
    )


def check_marcap_consistency(df: pd.DataFrame) -> pd.DataFrame:
    """Marcap == Close × Stocks 인지 검증. 어긋난 행을 반환한다."""
    expected = df["Close"] * df["Stocks"]
    mismatch = ~pd.Series(
        (df["Marcap"] - expected).abs() <= (expected.abs() * MARCAP_RTOL),
        index=df.index,
    )
    return df.loc[mismatch, ["Date", "Code", "Name", "Close", "Stocks", "Marcap"]]


def check_nulls(df: pd.DataFrame) -> pd.Series:
    """백테스트에 필수인 컬럼의 결측 수."""
    required = ["Date", "Code", "Name", "Close", "Volume", "Amount", "Marcap", "Stocks", "Market"]
    return df[required].isna().sum()


def check_duplicates(df: pd.DataFrame) -> int:
    """같은 날짜에 같은 종목이 두 번 나오면 안 된다."""
    return int(df.duplicated(subset=["Date", "Code"]).sum())
