"""종목이 그 날 어떤 상태였나 — VI·시장경보·시간외를 KIS 에서 받아 **날짜별로 쌓는다.**

봉만으로는 "그 분에 왜 체결이 없었나"를 알 수 없다. VI 가 걸렸는지, 거래가 정지됐는지,
투자경고였는지가 봉 어디에도 안 적혀 있다. 그걸 따로 모은다.

## 무엇을 어디서 받나 (실측 2026-08-30)

| 무엇 | KIS TR | 과거 | 콜 |
|---|---|---|---|
| VI 발동·해제 시각 | `FHPST01390000` | **2024-01 까지 확인** | 종목당 1콜 = 그 날치 |
| 종목 상태(경보·과열·정지…) | `FHKST01010100` | **오늘만** | 종목당 1콜 |
| 시간외 단일가 일자별 | `FHPST02320000` | **최근 30일** | 종목당 1콜 = 30일치 |

### VI 는 왜 종목마다 물어야 하나

시장 전체 목록으로도 받을 수 있는데 **한 콜에 30줄이 끝이고 이어받기가 안 된다.**
구분값(전체/상승/하락 × 거래소/코스닥 × 정적/동적) 12조합으로 긁어 봤더니 177건이
모였는데, 종목별로 검산하니 **63% 밖에 못 잡았다**(표본 400종목: 실제 27건 중 17건).
그래서 종목마다 묻는다.

### 상태 플래그는 오늘 것만 있다 — 그래서 매일 쌓아야 한다

`FHKST01010100`(현재가)에 아래가 다 들어 있는데 **과거 조회가 없다.**

    mrkt_warn_cls_code  시장경고 (00 없음 01 투자주의 02 투자경고 03 투자위험)
    short_over_yn       단기과열          mang_issu_cls_code  관리종목
    temp_stop_yn        거래정지          sltr_yn             정리매매
    invt_caful_yn       투자유의          iscd_stat_cls_code  종목상태
    vi_cls_code         VI 적용           ovtm_vi_cls_code    시간외단일가 VI
    crdt_able_yn        신용가능          ssts_yn             공매도가능

우리가 이미 쓰는 멀티시세(30종목/콜)에는 **이 열들이 없다**(시세 29열뿐, 실측). 그래서
종목당 1콜이 따로 든다.

과거는 **DART 거래소공시**로 메운다 — 1999-03 부터 330개월치를 이미 갖고 있고, 최근
3개월만 세도 매매거래정지 291건·관리종목 125건·상장폐지 64건·정리매매 23건이 들어 있다.
다만 **투자경고·투자위험·단기과열은 DART 에 거의 안 온다**(한 달 3,693건 중 0·0·1건).
그건 KRX 가 KIND 로만 내므로 오늘부터 쌓는 수밖에 없다.

## 시간외 — 증권사 차트가 하는 대로 한다

분봉 차트에 시간외를 그리는 증권사는 없다. **키움이 주는 KRX 분봉이 09:00~15:30 에서
끝난다**(실측) — 영웅문 차트가 그리는 게 바로 그 데이터다. 그래서 우리 분봉도 그대로 두고,
시간외는 **따로 표로 모은다.** 시간외 단일가는 16:00~18:00 에 10분마다 한 번 체결하는
방식이라 애초에 1분봉이 성립하지 않는다.

시간외는 세 갈래다:

    시간외 종가매매   08:30~08:40 · 15:40~16:00   그 날 종가 하나로만 체결
    시간외 단일가     16:00~18:00                 10분에 한 번 단일가
    NXT 애프터마켓    15:40~20:00                 연속 체결 — **이미 분봉에 있다**

종가매매는 KIS 에도 따로 없다. 대신 **일봉 거래량 − 정규장 분봉 합 − 시간외 단일가**로
유도된다(호출 0). 실측으로 확인: 035720 은 일봉−분봉합이 시간외 단일가와 원 단위까지
같았고(10,347 = 10,347 · 54,332 = 54,332), 005930·000660 은 단일가가 0인데도 차이가
있었다 — **NXT 경쟁매매 대상 종목은 KRX 시간외 단일가에서 빠지기 때문**이다
(DART 공시 `기타시장안내(금일NXT경쟁매매대상종목지정으로인한KRX시간외단일가매매제외종목안내)`,
한 달 38건). 그 종목의 차이는 종가매매 물량이다.

## 저장

    data/derived/market_state/vi/YYYYMMDD.parquet        그 날 VI 발동·해제
    data/derived/market_state/flags/YYYYMMDD.parquet     그 날 종목 상태
    data/derived/market_state/overtime/YYYYMMDD.parquet  그 날 시간외 단일가

**받은 그대로 담는다.** 가공은 나중에 파일에서 한다 — 계산식이 바뀌어도 다시 안 받게.

**조회만 한다. 주문 없음.**
"""

from __future__ import annotations

import pandas as pd

VI_PATH = "/uapi/domestic-stock/v1/quotations/inquire-vi-status"
VI_TR = "FHPST01390000"
PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
PRICE_TR = "FHKST01010100"
OVERTIME_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-overtimeprice"
OVERTIME_TR = "FHPST02320000"

# VI 한 줄에서 담을 것 — 받은 이름 그대로 둔다(나중에 뜻이 바뀌어도 원본이 남게).
VI_COLS = [
    "mksc_shrn_iscd", "hts_kor_isnm", "bsop_date", "cntg_vi_hour", "vi_cncl_hour",
    "vi_cls_code", "vi_kind_code", "vi_prc", "vi_stnd_prc", "vi_dprt",
    "vi_dmc_stnd_prc", "vi_dmc_dprt", "vi_count",
]

# 종목 상태에서 담을 것. 봉에 없는 것만 고른다(시세는 이미 봉에 있다).
FLAG_COLS = [
    "iscd_stat_cls_code",   # 종목상태 (51관리 52투자위험 53투자경고 54투자주의 58정지 59단기과열)
    "mrkt_warn_cls_code",   # 시장경고 (00없음 01투자주의 02투자경고 03투자위험)
    "short_over_yn",        # 단기과열
    "mang_issu_cls_code",   # 관리종목
    "temp_stop_yn",         # 거래정지
    "sltr_yn",              # 정리매매
    "invt_caful_yn",        # 투자유의
    "vi_cls_code",          # VI 적용
    "ovtm_vi_cls_code",     # 시간외단일가 VI
    "crdt_able_yn",         # 신용가능
    "ssts_yn",              # 공매도가능
    "clpr_rang_cont_yn",    # 종가범위연장
    "oprc_rang_cont_yn",    # 시가범위연장
    "grmn_rate_cls_code",   # 증거금율
]

OVERTIME_COLS = [
    "stck_bsop_date", "ovtm_untp_prpr", "ovtm_untp_vol", "ovtm_untp_tr_pbmn",
    "ovtm_untp_prdy_vrss", "ovtm_untp_prdy_ctrt", "stck_clpr", "acml_vol",
]


def _rows(body: dict, key: str) -> list[dict]:
    got = body.get(key) or []
    return [got] if isinstance(got, dict) else got


def fetch_vi(client, code: str, day: str) -> list[dict]:
    """한 종목이 그 날 VI 에 걸린 내역. 없으면 빈 목록."""
    body = client.get(
        VI_PATH, VI_TR,
        {
            "FID_DIV_CLS_CODE": "0", "FID_COND_SCR_DIV_CODE": "20139",
            "FID_MRKT_CLS_CODE": "0", "FID_INPUT_ISCD": code,
            "FID_RANK_SORT_CLS_CODE": "0", "FID_INPUT_DATE_1": day,
            "FID_TRGT_CLS_CODE": "", "FID_TRGT_EXLS_CLS_CODE": "",
        },
    ).body
    out = []
    for r in _rows(body, "output"):
        if str(r.get("mksc_shrn_iscd") or "").strip() != code:
            continue  # 종목을 지정해도 다른 종목이 섞여 오면 버린다
        out.append({c: str(r.get(c, "")).strip() for c in VI_COLS})
    return out


def fetch_flags(client, code: str, market: str = "J") -> dict | None:
    """한 종목의 **오늘** 상태. 시세는 안 담는다 — 봉에 이미 있다."""
    body = client.get(
        PRICE_PATH, PRICE_TR, {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code}
    ).body
    got = body.get("output") or {}
    if not got:
        return None
    row = {"code": code}
    row.update({c: str(got.get(c, "")).strip() for c in FLAG_COLS})
    return row


def fetch_overtime(client, code: str, market: str = "J") -> list[dict]:
    """한 종목의 시간외 단일가 — 한 콜에 최근 30 거래일이 온다."""
    body = client.get(
        OVERTIME_PATH, OVERTIME_TR,
        {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code},
    ).body
    out = []
    for r in _rows(body, "output2"):
        day = str(r.get("stck_bsop_date") or "").strip()
        if len(day) != 8 or not day.isdigit():
            continue
        row = {"code": code}
        row.update({c: str(r.get(c, "")).strip() for c in OVERTIME_COLS})
        out.append(row)
    return out


def to_frame(rows: list[dict]) -> pd.DataFrame:
    """받은 줄을 표로. 전부 글자로 담는다 — 원본을 그대로 남기려고."""
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).astype("string").reset_index(drop=True)


__all__ = [
    "FLAG_COLS", "OVERTIME_COLS", "VI_COLS",
    "fetch_flags", "fetch_overtime", "fetch_vi", "to_frame",
]
