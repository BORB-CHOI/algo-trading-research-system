"""KIS 관심종목 멀티시세 — 30종목/콜로 **오늘 확정 일봉**을 받아 나무 봉 모양으로 준다.

오너 결정 2026-08-18: 일봉 증분만 KIS 멀티시세로 한다(주·월봉은 그대로 나무).
실측: 나무 `period` 는 초당 5건이 서버 한도(초당 5.6건에서 이미 429)라 전 종목 일봉이
5,510콜·17분인데, KIS `FHKST11300006` 은 한 콜에 30종목·초당 20건 → 약 226콜·1분.

시장코드 `J`(KRX) · `UN`(통합) · `NX`(NXT) 셋 다 실측으로 확인했다(문서엔 UN 이 없다).
KIS 일봉과 나무 일봉은 240봉 대조에서 가격·거래량 240/240 일치, 통합 거래대금만
0.003% 차이(증권사끼리 통합 대금 계산이 다르다) — 오너가 받아들였다.

**"지금 값" 조회라 장이 다 끝난 뒤에만 확정 일봉이다.** 통합·NXT 는 NXT 애프터마켓이
끝나는 20:00 뒤. 그 전에 부르면 미완성 봉이 파일에 들어가므로 호출하는 쪽이 시각 관문을 건다.
응답에 날짜가 없다 — 오늘이 거래일이고 장이 끝났다는 걸 호출자가 보장한다.

같이 오는 `전일종가`(inter2_prdy_clpr)는 액면분할·병합 감지에 쓴다: 우리 파일의 마지막
종가와 다르면 증권사가 과거를 접었다는 뜻이라 그 종목만 나무에서 전체를 다시 받는다.
"""

from __future__ import annotations

from typing import Any

MULTI_PATH = "/uapi/domestic-stock/v1/quotations/intstock-multprice"
MULTI_TR = "FHKST11300006"
CHUNK = 30  # KIS 명세 — 한 콜 최대 30종목

# 우리 폴더 이름(나무 market_cd 소문자) → KIS 시장코드
MARKET_CODE = {"krx": "J", "unt": "UN", "nxt": "NX"}

# 나무 일봉 파일 열 — 값이 없는 나무 전용 열은 빈 문자열로 둔다(차트·백테스트는 OHLCV·대금만 읽는다)
NAMUH_DAY_COLS = [
    "bsop_date", "bsop_time", "stck_sdpr", "stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr",
    "vol", "tr_pbmn", "flng_cls_code", "prtt_rate", "news_cnt", "updownmark", "fcam_mod_cls_code",
]


def _s(v: Any) -> str:
    return str(v if v is not None else "").strip()


def multi_params(market: str, codes: list[str]) -> dict[str, str]:
    """멀티시세 요청 파라미터 — 30종목까지 번호를 붙여 나열한다."""
    if len(codes) > CHUNK:
        raise ValueError(f"한 콜 최대 {CHUNK}종목")
    mkt = MARKET_CODE[market]
    params: dict[str, str] = {}
    for i, code in enumerate(codes, start=1):
        params[f"FID_COND_MRKT_DIV_CODE_{i}"] = mkt
        params[f"FID_INPUT_ISCD_{i}"] = str(code).zfill(6)
    return params


def to_namuh_row(item: dict, bas_dd: str) -> dict[str, str] | None:
    """멀티시세 한 종목 → 나무 일봉 한 행. 체결이 없던 종목(현재가 0)은 None."""
    close = _s(item.get("inter2_prpr"))
    if not close or float(close) == 0:
        return None
    return {
        "bsop_date": bas_dd,
        "bsop_time": "",
        "stck_sdpr": _s(item.get("inter2_sdpr")),
        "stck_oprc": _s(item.get("inter2_oprc")) or close,
        "stck_hgpr": _s(item.get("inter2_hgpr")) or close,
        "stck_lwpr": _s(item.get("inter2_lwpr")) or close,
        "stck_prpr": close,
        "vol": _s(item.get("acml_vol")) or "0",
        "tr_pbmn": _s(item.get("acml_tr_pbmn")) or "0",
        "flng_cls_code": "",
        "prtt_rate": "",
        "news_cnt": "",
        "updownmark": "",
        "fcam_mod_cls_code": "",
    }


def fetch_snapshot(client, market: str, codes: list[str], bas_dd: str) -> dict[str, dict]:
    """종목 목록 전체의 오늘 봉. code → {"row": 나무행 | None, "prdy_clpr": 전일종가}.

    `client` 는 `KisClient`(get 메서드). 응답에 없는 종목은 결과에도 없다.
    """
    out: dict[str, dict] = {}
    for i in range(0, len(codes), CHUNK):
        chunk = [str(c).zfill(6) for c in codes[i : i + CHUNK]]
        body = client.get(MULTI_PATH, MULTI_TR, multi_params(market, chunk)).body
        for item in body.get("output") or []:
            code = _s(item.get("inter_shrn_iscd")).zfill(6)
            if not code or code == "000000":
                continue
            out[code] = {
                "row": to_namuh_row(item, bas_dd),
                "prdy_clpr": _s(item.get("inter2_prdy_clpr")),
            }
    return out
