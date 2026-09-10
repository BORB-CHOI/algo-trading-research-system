"""기타 수급 — 공매도·대차거래·프로그램매매를 KIS 에서 받는다. 조회 전용.

증권사 화면이 이 셋을 "기타 수급"으로 묶어 부른다(KIS 문서: 국내 현재가 > 기타수급 >
프로그램). 외인·기관·개인 수급(`backfill_kis_supply`)과 **다른 자료**다 — 그쪽은 누가
샀나이고, 이쪽은 **어떤 성격의 매물인가**다.

## 무엇을 왜 받나

| 무엇 | KIS TR | 한 콜 | 눌림매매에 왜 쓰나 |
|---|---|---|---|
| 공매도 일별추이 | `FHPST04830000` | 100 거래일 | 빌려 판 물량이 쌓이면 되돌림 구간에서 위로 눌린다 |
| 프로그램매매 | `FHPPG04650201` | 30 거래일 | 사람이 산 건지 기계가 산 건지 가른다 |

셋 다 **2015 년까지 답한다**(실측 2026-09-10, 005930). 그 아래는 안 재 봤다.

## 콜이 비싸다 — 이 둘은 종목마다 물어야 한다

2026-09-10 에 창구를 전수로 뒤졌다. 대차거래는 키움에 날짜 하나로 전 종목을 주는 창구가
있어서 그쪽으로 옮겼다(`kiwoom_extra_supply`). **공매도와 프로그램매매는 어디에도 전 종목
대량 조회가 없다** — KRX Open API(24개 서비스에 없음) · KIS 순위분석(공매도 순위
`FHPST04820000` 은 날짜 파라미터가 없어 과거를 못 받는다) · 키움 전체 목록 · 나무 PLUG
(프로그램매매는 실시간 채널만) 전부 확인했다.

그래서 이 둘은 **종목당 한 콜**이고 그마저 100일(프로그램은 30일)씩 잘려 온다.
전 종목 4,306 × 2015~2026(거래일 약 3,050)으로 재면:

    공매도     31콜/종목 × 4,306 = 133,486 콜
    프로그램  102콜/종목 × 4,306 = 439,212 콜

공매도는 **키움 `ka10014` 가 한 콜에 372 거래일**을 준다 — 다만 2025-03 이 바닥이라
최근 1.5년만 1콜/종목(4,306콜)으로 끝나고 그보다 과거는 KIS 로 채워야 한다.

받는 기간을 늘리면 그대로 곱해진다. 얼마나 과거까지 받을지는 **오너가 정한다.**

**조회만 한다. 주문 없음.**
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class Flow:
    """기타 수급 한 종류를 어디서 어떻게 받나."""

    key: str
    label: str
    path: str
    tr: str
    out_key: str          # 응답에서 일별 배열이 들어 있는 자리
    date_col: str
    per_call: int         # 한 콜에 오는 거래일 수 — 콜 예산 계산에 쓴다
    code_param: str       # 종목코드를 넣는 파라미터 이름
    base: dict[str, str] = field(default_factory=dict)
    # 기간을 어떻게 지정하나. ("range", 시작키, 끝키) 또는 ("anchor", 기준일키)
    window: tuple = ("range", "FID_INPUT_DATE_1", "FID_INPUT_DATE_2")


FLOWS: tuple[Flow, ...] = (
    Flow(
        key="short_sale",
        label="공매도",
        path="/uapi/domestic-stock/v1/quotations/daily-short-sale",
        tr="FHPST04830000",
        out_key="output2",
        date_col="stck_bsop_date",
        per_call=100,
        code_param="FID_INPUT_ISCD",
        base={"FID_COND_MRKT_DIV_CODE": "J"},
        window=("range", "FID_INPUT_DATE_1", "FID_INPUT_DATE_2"),
    ),
    # ⚠️ 대차거래는 **여기서 안 받는다.** 키움에 날짜 하나로 전 종목을 주는 창구가 있다
    #    (`kiwoom_extra_supply.loan_day`, ka90012). 실측 2026-09-10:
    #
    #        키움 대량   하루 36 콜 · 1,745 종목 · 6 초
    #        KIS 종목별  하루 4,306 콜          · 4 분    ← 120 배 느리다
    #
    #    게다가 키움은 **그 날 거래된 종목이 통째로** 오므로 상장폐지 종목이 저절로 들어온다.
    #    종목별로 돌면 지금 상장된 목록만 훑어 살아남은 것만 보는 착시가 생긴다
    #    (CLAUDE.md 방법론 가드레일). KIS `HHPST074500C0` 은 그래서 안 쓴다.
    Flow(
        key="program",
        label="프로그램매매",
        path="/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily",
        tr="FHPPG04650201",
        out_key="output",
        date_col="stck_bsop_date",
        per_call=30,
        code_param="FID_INPUT_ISCD",
        base={"FID_COND_MRKT_DIV_CODE": "J"},
        # 기준일 하나만 받는다 — 그 날부터 거슬러 30일. 수급 정본과 같은 방식이다.
        window=("anchor", "FID_INPUT_DATE_1"),
    ),
)

BY_KEY = {f.key: f for f in FLOWS}


def fetch(client, flow: Flow, code: str, f_dt: str, t_dt: str) -> list[dict]:
    """한 종목·한 창. `window` 모양에 맞춰 날짜 파라미터를 채운다."""
    params = {flow.code_param: code, **flow.base}
    if flow.window[0] == "range":
        params[flow.window[1]] = f_dt
        params[flow.window[2]] = t_dt
    else:  # anchor — 끝 날짜만 주고 거슬러 받는다
        params[flow.window[1]] = t_dt
    rows = client.get(flow.path, flow.tr, params).body.get(flow.out_key) or []
    if isinstance(rows, dict):
        rows = [rows]
    return [
        {k: str(v).strip() for k, v in r.items()}
        for r in rows
        if isinstance(r, dict) and str(r.get(flow.date_col, "")).strip()
    ]


def to_frame(rows: list[dict], flow: Flow) -> pd.DataFrame:
    """받은 줄을 표로. 전부 글자로 담는다 — 원본을 그대로 남기려고."""
    if not rows:
        return pd.DataFrame()
    return (
        pd.DataFrame(rows)
        .astype("string")
        .drop_duplicates(subset=[flow.date_col], keep="last")
        .sort_values(flow.date_col)
        .reset_index(drop=True)
    )


__all__ = ["BY_KEY", "FLOWS", "Flow", "fetch", "to_frame"]
