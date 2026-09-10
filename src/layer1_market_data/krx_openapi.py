"""KRX 정보데이터시스템 Open API — 날짜 하나로 그 시장 **전 종목** 일별매매정보를 받는다.

오너 결정 2026-08-18: 인증키를 받았다. 쓰임새는 둘뿐이다.
  1. marcap 뒤쪽 공백 채우기 (`krx_gapfill.py`) — 전엔 네이버를 종목마다 4천 번 불렀다.
     KRX 는 날짜당 시장 3번(코스피·코스닥·코넥스)이면 끝이고, 시가총액·상장주식수도 실제 값이다.
  2. 갱신 시작 때 "시장의 마지막 거래일" 판정 1회 — 받을 게 없으면 나머지를 전부 건너뛴다.

일봉의 정본은 그대로 나무 수집본(수정주가)이다. 여기서 받는 값은 marcap 과 같은 **원주가**라
marcap 자리에만 들어간다(ADR-0002 개정 2026-08-18). 주문·매매 경로와는 무관하다.

호출: GET https://data-dbg.krx.co.kr/svc/apis/sto/{stk|ksq|knx}_bydd_trd?basDd=YYYYMMDD
      헤더 AUTH_KEY. 하루 10,000콜 한도(넉넉하다 — 하루 갱신은 10콜 안쪽).
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import requests

BASE_URL = "https://data-dbg.krx.co.kr/svc/apis/sto"
ENV_KEY = "KRX_AUTH_KEY"
TIMEOUT = 20

# marcap 의 Market 값 → (엔드포인트, marcap MarketId)
MARKETS: dict[str, tuple[str, str]] = {
    "KOSPI": ("stk_bydd_trd", "STK"),
    "KOSDAQ": ("ksq_bydd_trd", "KSQ"),
    "KONEX": ("knx_bydd_trd", "KNX"),
}

# marcap 과 열 이름·순서를 맞춘다 — 그대로 이어붙일 수 있게.
MARCAP_COLS = [
    "Date",
    "Code",
    "Name",
    "Market",
    "MarketId",
    "Dept",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Amount",
    "Changes",
    "ChangesRatio",
    "Marcap",
    "Stocks",
    "Rank",
]


class KrxApiError(RuntimeError):
    """KRX 호출 실패 — 키 없음·HTTP 오류·응답 모양 이상."""


def auth_key() -> str:
    """환경변수의 인증키. 없으면 빈 문자열 — 호출하는 쪽이 건너뛸지 정한다."""
    return os.environ.get(ENV_KEY, "").strip()


def _num(v) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _short_code(isu_cd: str) -> str:
    """표준코드(KR7005930003)면 단축코드(005930)만 남긴다. 이미 6자리면 그대로."""
    s = str(isu_cd).strip()
    return s[3:9] if len(s) == 12 and s.startswith("KR") else s.zfill(6)


def fetch_rows(
    market: str, bas_dd: str, key: str, session: requests.Session | None = None
) -> list[dict]:
    """한 시장·한 날짜의 원본 행(OutBlock_1). 휴장일이면 빈 목록."""
    if market not in MARKETS:
        raise KrxApiError(f"모르는 시장: {market}")
    if not key:
        raise KrxApiError(f"{ENV_KEY} 가 비어 있다 — .env 를 확인하라")
    endpoint, _ = MARKETS[market]
    http = session or requests
    try:
        r = http.get(
            f"{BASE_URL}/{endpoint}",
            params={"basDd": bas_dd},
            headers={"AUTH_KEY": key},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise KrxApiError(f"KRX {market} {bas_dd}: {e}") from e
    if r.status_code != 200:
        raise KrxApiError(f"KRX {market} {bas_dd}: HTTP {r.status_code} {r.text[:200]}")
    try:
        body = r.json()
    except ValueError as e:
        raise KrxApiError(f"KRX {market} {bas_dd}: JSON 아님 {r.text[:200]}") from e
    rows = body.get("OutBlock_1") if isinstance(body, dict) else None
    if rows is None:
        raise KrxApiError(f"KRX {market} {bas_dd}: 응답 모양 이상 {str(body)[:200]}")
    return list(rows)


def to_marcap_frame(rows: list[dict], market: str, bas_dd: str) -> pd.DataFrame:
    """KRX 원본 행 → marcap 모양 표. 종가가 없는 행(거래정지 표기 '-')은 뺀다."""
    _, market_id = MARKETS[market]
    day = pd.Timestamp(bas_dd)
    out: list[dict] = []
    for r in rows:
        close = _num(r.get("TDD_CLSPRC"))
        if close is None:
            continue
        o, h, lo = (_num(r.get(k)) for k in ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC"))
        stocks = _num(r.get("LIST_SHRS")) or 0.0
        dept = str(r.get("SECT_TP_NM") or "").strip() or None
        out.append(
            {
                "Date": day,
                "Code": _short_code(r.get("ISU_CD", "")),
                "Name": str(r.get("ISU_NM", "")).strip(),
                "Market": str(r.get("MKT_NM") or market).strip(),
                "MarketId": market_id,
                "Dept": dept,
                "Open": o or close,
                "High": h or close,
                "Low": lo or close,
                "Close": close,
                "Volume": _num(r.get("ACC_TRDVOL")) or 0.0,
                "Amount": _num(r.get("ACC_TRDVAL")) or 0.0,
                "Changes": _num(r.get("CMPPREVDD_PRC")) or 0.0,
                "ChangesRatio": _num(r.get("FLUC_RT")) or 0.0,
                "Marcap": _num(r.get("MKTCAP")) or close * stocks,
                "Stocks": int(stocks),
            }
        )
    if not out:
        return pd.DataFrame(columns=MARCAP_COLS)
    df = pd.DataFrame(out)
    df["Rank"] = 0
    return df[MARCAP_COLS]


def snapshot(bas_dd: str, key: str, session: requests.Session | None = None) -> pd.DataFrame:
    """그 날짜 전 시장 스냅샷(코스피+코스닥+코넥스). 휴장일이면 빈 표. 시총 순위도 매긴다."""
    frames = [to_marcap_frame(fetch_rows(m, bas_dd, key, session), m, bas_dd) for m in MARKETS]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=MARCAP_COLS)
    df = pd.concat(frames, ignore_index=True)
    df["Rank"] = df["Marcap"].rank(ascending=False, method="first").astype(int)
    return df.sort_values("Rank").reset_index(drop=True)


def last_trading_day(
    key: str,
    today: date | None = None,
    lookback: int = 10,
    session: requests.Session | None = None,
) -> str:
    """오늘부터 뒤로 가며 코스피 자료가 있는 첫 날 = 시장의 마지막 거래일(YYYYMMDD).

    장 마감 전엔 오늘 자료가 아직 없어 어제가 나온다 — 그게 맞다. "받을 수 있는 마지막
    날"이 갱신 관문의 뜻이다. 열흘 안에 없으면 빈 문자열(판정 포기).
    """
    d = today or date.today()
    for _ in range(lookback):
        bas_dd = d.strftime("%Y%m%d")
        if fetch_rows("KOSPI", bas_dd, key, session):
            return bas_dd
        d -= timedelta(days=1)
    return ""
