"""기타 수급을 **키움 대량 조회**로 받는다 — 조회 전용.

## 왜 창구를 나눴나 (2026-09-10 전수 조사)

같은 자료라도 창구마다 한 콜에 주는 양이 딴판이다. 종목마다 부르는 게 늘 답이 아니다.
전 종목 4,306 · 2015~2026(거래일 약 3,050) 기준으로 재 봤다:

| 자료 | 대량 조회 | 최선 | 콜 |
|---|---|---|---|
| 대차거래 | **있다** | **키움 `ka90012`** — 날짜 하나로 전 종목 | 3,050일 × 14장 = **42,700** |
| 공매도 | 없다 | **키움 `ka10014`** — 한 콜에 **372 거래일** | 4,306 (2025-03~) |
| 공매도 과거 | 없다 | KIS `FHPST04830000` — 종목당 100일/콜 | 약 112,000 (2015~2025-03) |
| 프로그램매매 | **없다** | KIS `FHPPG04650201` — 종목당 30일/콜 | 439,212 |

찾아본 곳: KRX Open API(24개 서비스에 셋 다 없음) · KIS 시세분석 29개·순위분석 22개
(순위 공매도 `FHPST04820000` 은 **날짜 파라미터가 없어** 과거를 못 받는다) · 키움 전체 목록 ·
나무 PLUG(프로그램매매는 **실시간 채널만**, 과거 REST 없음) · 공공데이터포털 금융위
주식대차정보(키 미발급).

**프로그램매매는 어느 창구에도 전 종목 대량 조회가 없다.** 종목별이 유일한 길이다.

## 실측 (2026-09-10)

    ka90012  하루치 = 14 장 · 671 종목 · 4 초   ← 대차거래가 실제로 있는 종목만 온다
    ka10014  한 콜 = 372 거래일 (2025-03-05 ~) · 이어받기 없음. 그 이전은 안 준다

키움도 **조회 전용**이다 — `kiwoom_bars._guard()` 가 `ka` 아닌 TR 을 막는다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import requests

from . import kiwoom_bars as kw

LOAN_TR = "ka90012"  # 대차거래내역 — 날짜 하나로 전 종목
LOAN_PATH = "/api/dostk/slb"
LOAN_LIST = "dbrt_trde_prps"

SHORT_TR = "ka10014"  # 공매도추이 — 종목당 한 콜에 372 거래일
SHORT_PATH = "/api/dostk/shsa"
SHORT_LIST = "shrts_trnsn"

# 시장 구분 — 대차거래는 시장마다 따로 물어야 전 종목이 된다.
LOAN_MARKETS = ("001", "101")  # 001 코스피 · 101 코스닥

MAX_PAGES = 400  # 하루 14 장이 보통. 넘칠 일 없는 안전핀


@dataclass(frozen=True)
class Page:
    rows: list[dict]
    more: bool
    key: str


def _post(api_id: str, path: str, payload: dict, cont: str, key: str) -> dict:
    kw._guard(api_id)
    last = ""
    for attempt in range(kw.MAX_RETRY + 1):
        kw.THROTTLE.wait()
        try:
            r = requests.post(
                f"{kw.base_url()}{path}",
                json=payload,
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {kw.token()}",
                    "api-id": api_id,
                    "cont-yn": cont,
                    "next-key": key,
                },
                timeout=kw.TIMEOUT,
            )
        except Exception as e:  # noqa: BLE001 — 순단은 종류를 안 가리고 다시 부른다
            last = f"{type(e).__name__}: {e}"
            if attempt < kw.MAX_RETRY:
                import time

                time.sleep(kw.NET_RETRY_SLEEP)
                continue
            raise kw.KiwoomError(f"{api_id} 통신 실패: {last}") from e
        if r.status_code == 429:
            import time

            if attempt < kw.MAX_RETRY:
                time.sleep(kw.RATE_RETRY_SLEEP)
                continue
            raise kw.KiwoomError(f"{api_id} 한도 초과가 계속된다")
        if r.status_code != 200:
            raise kw.KiwoomError(f"{api_id} HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        if str(body.get("return_code")) not in ("0", "None"):
            raise kw.KiwoomError(f"{api_id} {body.get('return_code')}: {body.get('return_msg')}")
        body["_cont"] = str(r.headers.get("cont-yn") or "")
        body["_key"] = str(r.headers.get("next-key") or "")
        return body
    raise kw.KiwoomError(f"{api_id} 실패: {last}")


def loan_day(day: str) -> pd.DataFrame:
    """**하루치 대차거래 전 종목.** 시장마다 물어 이어받기로 끝까지 훑는다.

    돌려주는 표에는 `bsop_date` 를 붙인다 — 응답에 날짜가 안 들어 있다.
    """
    rows: list[dict] = []
    for market in LOAN_MARKETS:
        cont = key = ""
        for _ in range(MAX_PAGES):
            body = _post(LOAN_TR, LOAN_PATH, {"dt": day, "mrkt_tp": market}, cont, key)
            got = body.get(LOAN_LIST) or []
            if not got:
                break
            rows.extend({**r, "mrkt_tp": market} for r in got if isinstance(r, dict))
            cont, key = body["_cont"], body["_key"]
            if cont != "Y":
                break
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).astype("string")
    frame["bsop_date"] = day
    return (
        frame.drop_duplicates(subset=["stk_cd", "bsop_date"], keep="last")
        .sort_values("stk_cd")
        .reset_index(drop=True)
    )


def short_code(code: str, since: str, until: str) -> pd.DataFrame:
    """한 종목 공매도 — 한 콜에 372 거래일. 이어받기가 없어 그게 천장이다."""
    body = _post(
        SHORT_TR, SHORT_PATH,
        {"stk_cd": code, "tm_tp": "1", "strt_dt": since, "end_dt": until},
        "", "",
    )
    rows = [r for r in (body.get(SHORT_LIST) or []) if isinstance(r, dict) and r.get("dt")]
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).astype("string")
    frame["stk_cd"] = code
    return (
        frame.drop_duplicates(subset=["dt"], keep="last")
        .sort_values("dt")
        .reset_index(drop=True)
    )


__all__ = ["LOAN_MARKETS", "loan_day", "short_code"]
