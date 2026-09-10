"""금융투자협회 증시자금 원본을 한 파일로 모은다.

공식 자본시장통계 FreeSIS의 두 표를 날짜로 합친다.

* ``STATSCU0100000060``: 투자자예탁금, 장내파생상품 거래예수금,
  대고객 RP 매도잔고, 위탁매매 미수금, 반대매매 체결금액·비중
* ``STATSCU0100000070``: 신용거래융자·대주와 담보융자

금액 단위는 공식 화면과 같은 **백만원**이다. 원 단위로 바꾸지 않는다. 원본 열의 뜻은
``Service.columns``에 한 번만 적고, 과거 백필과 매일 갱신이 같은 :func:`update`를 쓴다.
조회 전용이며 로그인·주문·계좌 정보가 필요 없다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from . import parquet_io

BASE_URL = "https://freesis.kofia.or.kr"
LANDING_PATH = "/stat/FreeSIS.do"
DATA_PATH = "/meta/getMetaDataList.do"
DEFAULT_OUTPUT_DIR = Path("data/derived/market_funds")
FIRST_DATE = "19980618"
RECENT_OVERLAP_DAYS = 14


@dataclass(frozen=True)
class Service:
    service_id: str
    object_name: str
    columns: Mapping[str, str]


MARKET_FUNDS_COLUMNS: dict[str, str] = {
    "TMPV1": "date",
    "TMPV2": "customer_deposits_million_won",
    "TMPV3": "derivatives_deposits_million_won",
    "TMPV4": "customer_rp_balance_million_won",
    "TMPV5": "unsettled_receivables_million_won",
    "TMPV6": "forced_liquidation_million_won",
    "TMPV7": "forced_liquidation_ratio_pct",
}

CREDIT_COLUMNS: dict[str, str] = {
    "TMPV1": "date",
    "TMPV2": "credit_loans_total_million_won",
    "TMPV3": "credit_loans_kospi_million_won",
    "TMPV4": "credit_loans_kosdaq_million_won",
    "TMPV5": "stock_lending_total_million_won",
    "TMPV6": "stock_lending_kospi_million_won",
    "TMPV7": "stock_lending_kosdaq_million_won",
    "TMPV8": "subscription_loans_million_won",
    "TMPV9": "securities_collateral_loans_million_won",
}

MARKET_FUNDS = Service("STATSCU0100000060", "STATSCU0100000060BO", MARKET_FUNDS_COLUMNS)
CREDIT = Service("STATSCU0100000070", "STATSCU0100000070BO", CREDIT_COLUMNS)
SERVICES = (MARKET_FUNDS, CREDIT)

FetchFn = Callable[[Service, str, str], list[dict]]


def rows_to_frame(rows: list[dict], columns: Mapping[str, str]) -> pd.DataFrame:
    """FreeSIS의 ``TMPV*`` 원본 열을 뜻이 드러나는 고정 열로 바꾼다."""
    ordered = list(columns.values())
    if not rows:
        return pd.DataFrame(columns=ordered)
    frame = pd.DataFrame(rows).rename(columns=columns)
    frame = frame.reindex(columns=ordered)
    frame["date"] = frame["date"].astype("string").str.replace("-", "", regex=False)
    frame = frame[frame["date"].str.fullmatch(r"\d{8}", na=False)].copy()
    for col in ordered:
        if col != "date":
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return (
        frame.drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def merge_official_tables(funds: pd.DataFrame, credit: pd.DataFrame) -> pd.DataFrame:
    """두 공식 표를 날짜로 합친다. 발표일이 달라도 어느 한쪽을 버리지 않는다."""
    if funds.empty and credit.empty:
        return pd.DataFrame(columns=["date"])
    if funds.empty:
        return credit.sort_values("date").reset_index(drop=True)
    if credit.empty:
        return funds.sort_values("date").reset_index(drop=True)
    return (
        funds.merge(credit, how="outer", on="date", validate="one_to_one")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; ATS data collector)",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{BASE_URL}/stat/main.do",
        }
    )
    return session


def fetch_service(service: Service, since: str, until: str) -> list[dict]:
    """FreeSIS 공식 화면이 쓰는 조회 요청 한 번. 일시 오류만 세 번 다시 묻는다."""
    payload = {
        "dmSearch": {
            "tmpV40": "1000000",  # 화면 표시 단위: 백만원
            "tmpV41": "1",
            "tmpV1": "D",
            "tmpV45": since,
            "tmpV46": until,
            "OBJ_NM": service.object_name,
        }
    }
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with _session() as session:
                session.get(
                    f"{BASE_URL}{LANDING_PATH}",
                    params={"parentDivId": "MSIS10000000000000", "serviceId": service.service_id},
                    timeout=20,
                ).raise_for_status()
                response = session.post(f"{BASE_URL}{DATA_PATH}", json=payload, timeout=90)
                response.raise_for_status()
                body = response.json()
            got = body.get("ds1") or []
            if not isinstance(got, list):
                raise ValueError(f"FreeSIS {service.service_id} 응답의 ds1이 목록이 아닙니다")
            return got
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    raise RuntimeError(f"FreeSIS {service.service_id} 조회 실패: {last_error}") from last_error


def _query_start(old: pd.DataFrame | None) -> str:
    if old is None or old.empty or "date" not in old.columns:
        return FIRST_DATE
    dates = old["date"].dropna().astype(str)
    if dates.empty:
        return FIRST_DATE
    try:
        last = datetime.strptime(dates.max(), "%Y%m%d")
    except ValueError:
        return FIRST_DATE
    return (last - timedelta(days=RECENT_OVERLAP_DAYS)).strftime("%Y%m%d")


def _overlay(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    """새로 온 값만 덮는다. 한 표가 빈 날에도 다른 표의 옛 값은 보존한다."""
    if old is None or old.empty:
        return new.sort_values("date").reset_index(drop=True)
    if new.empty:
        return old.sort_values("date").reset_index(drop=True)
    old_i = old.drop_duplicates("date", keep="last").set_index("date")
    new_i = new.drop_duplicates("date", keep="last").set_index("date")
    merged = new_i.combine_first(old_i)
    return merged.sort_index().reset_index()


def update(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    fetch: FetchFn = fetch_service,
    until: str | None = None,
) -> dict:
    """과거가 없으면 전부, 있으면 최근 겹침 구간만 다시 받아 한 파일에 합친다."""
    output_dir = Path(output_dir)
    path = output_dir / "daily.parquet"
    old = parquet_io.read(path, date_col="date")
    since = _query_start(old)
    until = until or datetime.now().strftime("%Y%m%d")

    frames: dict[str, pd.DataFrame] = {}
    for service in SERVICES:
        rows = fetch(service, since, until)
        frames[service.service_id] = rows_to_frame(rows, service.columns)
    new = merge_official_tables(
        frames[MARKET_FUNDS.service_id], frames[CREDIT.service_id]
    )
    merged = _overlay(old, new)
    if not new.empty:
        parquet_io.save(merged, path)

    last_date = ""
    if not merged.empty and "date" in merged.columns:
        last_date = str(merged["date"].dropna().astype(str).max())
    old_rows = 0 if old is None else len(old)
    return {
        "rows": len(merged),
        "added_rows": max(len(merged) - old_rows, 0),
        "last_date": last_date,
        "since": since,
        "called": len(SERVICES),
        "source": "KOFIA FreeSIS",
    }


__all__ = [
    "CREDIT_COLUMNS",
    "MARKET_FUNDS_COLUMNS",
    "Service",
    "fetch_service",
    "merge_official_tables",
    "rows_to_frame",
    "update",
]
