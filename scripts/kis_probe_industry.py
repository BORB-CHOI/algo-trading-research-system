"""KIS 업종 조회 실측 프로브 — 업종 모멘텀을 백테스트에 쓸 수 있는지 확정한다.

**조회만 한다** (CLAUDE.md 단계 6 — 주문 전송 없음).

    python scripts/kis_probe_industry.py           # 전부
    python scripts/kis_probe_industry.py codes     # 업종 코드 목록만
    python scripts/kis_probe_industry.py daily     # 일자별지수만

## 무엇을 확정하려는가

오너 요구: "개별 종목이 아니라 그 종목이 속한 무리 자체에 그 시점 모멘텀이 있는가"를
종목 선정 조건에 넣고 싶다. 네이버 테마 크롤링(`themes.py`)은 **오늘 명부만** 주므로
과거 구간에 쓰면 look-ahead 다 — 지금 그 테마로 묶인 종목은 테마가 뜬 뒤 편입된 것들이다.

KIS `국내업종 일자별지수`(TR `FHPUP02120000`)는 `FID_INPUT_DATE_1` 로 **날짜를 지정**받는다.
날짜 지정 호출이면 point-in-time 이 성립한다(수급 API `FHPTJ04160001` 과 같은 구조).
응답 필드에 `ascn_issu_cnt`(상승 종목 수)·`down_issu_cnt`(하락 종목 수)가 있어서,
"업종 구성원 중 몇 %가 올랐나"를 **KIS 가 이미 세어서 준다** — 구성원 명부가 필요 없다.

다만 공식 예제의 컬럼 매핑은 output1·output2 를 한 덩어리로 뭉쳐놔서, 상승/하락 종목 수가
**오늘 요약(output1)에만 오는지 일자별(output2)에도 오는지 판별이 안 된다.** 여기가 갈림길이라
실제 응답으로 가른다. 안 오면 업종 지수 등락률만으로 모멘텀을 잡아야 한다(그건 확실히 온다).

## 확인 항목

1. 업종 코드 목록 — 세부 업종코드는 KIS FAQ 첨부에만 있다. `구분별전체시세`(`FHPUP02140000`)가
   `bstp_cls_code`·`hts_kor_isnm` 으로 돌려주므로 그걸로 대신 얻는다.
2. 일자별지수 응답 필드 — output1/output2 각각 무엇이 오는가. 상승/하락 종목 수 포함 여부.
3. 과거 한도 — 백테스트 구간 2017-01 까지 응답하는가 (CLAUDE.md 데이터 범위).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.layer4_execution.brokers.kis.auth import KisCredentials, get_access_token  # noqa: E402
from src.layer4_execution.brokers.kis.client import (  # noqa: E402
    CallPolicy,
    KisApiError,
    KisClient,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "kis_token.json"

CATEGORY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-category-price"
CATEGORY_TR = "FHPUP02140000"

DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-daily-price"
DAILY_TR = "FHPUP02120000"

# EGW00201(초당 거래건수 초과)이 프로브 연타만으로 떨어진다. 간격·재시도는 클라이언트가 맡는다.
PROBE_POLICY = CallPolicy(min_interval_sec=0.6)  # placeholder

# 상승/하락 종목 수 필드 — 이게 output2 에 있느냐가 이 프로브의 핵심 질문이다.
BREADTH_FIELDS = ("ascn_issu_cnt", "down_issu_cnt", "stnr_issu_cnt")

# 코스피 전체·코스닥 전체. 세부 업종은 1단계에서 실측으로 얻는다.
MARKET_ROOTS = [("K", "0001", "코스피"), ("Q", "1001", "코스닥")]

PROBE_DATES = ["20260731", "20260101", "20240102", "20220103", "20170102"]

# 세부 업종 표본 수. 전 업종을 훑을 필요 없이 응답 형태만 확인한다.
SECTOR_SAMPLE = 3


@dataclass(frozen=True)
class Sector:
    code: str
    name: str
    market: str


def _credentials() -> KisCredentials:
    load_dotenv(ROOT / ".env")
    import os

    app_key = os.environ.get("KIS_APP_KEY", "").strip()
    app_secret = os.environ.get("KIS_APP_SECRET", "").strip()
    env = os.environ.get("KIS_ENV", "vts").strip()
    if not app_key or not app_secret:
        raise SystemExit("KIS_APP_KEY / KIS_APP_SECRET 이 .env 에 없다. .env.example 참고.")
    return KisCredentials(app_key=app_key, app_secret=app_secret, env=env)


def _rows(body: dict, key: str) -> list[dict]:
    rows = body.get(key) or []
    if isinstance(rows, dict):
        rows = [rows]
    return [r for r in rows if isinstance(r, dict)]


def fetch_sectors(client: KisClient) -> list[Sector]:
    """업종 코드 목록. 코스피·코스닥 전업종(`fid_blng_cls_code=0`)."""
    sectors: list[Sector] = []
    for mrkt_cls, iscd, label in MARKET_ROOTS:
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": iscd,
            "FID_COND_SCR_DIV_CODE": "20214",
            "FID_MRKT_CLS_CODE": mrkt_cls,
            "FID_BLNG_CLS_CODE": "0",
        }
        try:
            body = client.get(CATEGORY_PATH, CATEGORY_TR, params).body
        except KisApiError as exc:
            print(f"  {label}: 실패 — {exc}")
            continue

        for row in _rows(body, "output2") or _rows(body, "output1"):
            code = str(row.get("bstp_cls_code", "")).strip()
            name = str(row.get("hts_kor_isnm", "")).strip()
            if code:
                sectors.append(Sector(code=code, name=name, market=label))
    return sectors


def probe_sector_codes(client: KisClient) -> tuple[int, list[Sector]]:
    print("=" * 72)
    print("1. 업종 코드 목록 — 구분별전체시세로 대신 얻는다")
    print("=" * 72)

    sectors = fetch_sectors(client)
    if not sectors:
        print("\n업종 목록이 비었다. 장 시간대·권한을 먼저 의심하라.")
        return 1, []

    print(f"\n업종 {len(sectors)}건")
    for s in sectors[:20]:
        print(f"  {s.code:>6}  {s.market:<6} {s.name}")
    if len(sectors) > 20:
        print(f"  ... 외 {len(sectors) - 20}건")
    return 0, sectors


def probe_daily_fields(client: KisClient, sectors: list[Sector]) -> int:
    """일자별지수 응답 필드 — 상승/하락 종목 수가 일자별로 오는가."""
    print("=" * 72)
    print("2. 일자별지수 응답 필드 (핵심: 상승/하락 종목 수가 output2 에 오는가)")
    print("=" * 72)

    target = sectors[0] if sectors else Sector("0001", "코스피", "코스피")
    params = {
        "FID_PERIOD_DIV_CODE": "D",
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": target.code,
        "FID_INPUT_DATE_1": PROBE_DATES[0],
    }
    try:
        body = client.get(DAILY_PATH, DAILY_TR, params).body
    except KisApiError as exc:
        print(f"\n실패 — {exc}")
        return 1

    print(f"\n대상 업종: {target.code} {target.name} / 요청일 {PROBE_DATES[0]}\n")
    verdict = False
    for key in ("output1", "output2"):
        rows = _rows(body, key)
        print(f"[{key}] {len(rows)}행")
        if not rows:
            print("  (없음)\n")
            continue
        for field, value in rows[0].items():
            mark = "  ★" if field in BREADTH_FIELDS else "   "
            print(f"{mark} {field:<28} = {value}")
        if key == "output2" and any(f in rows[0] for f in BREADTH_FIELDS):
            verdict = True
        print()

    rows2 = _rows(body, "output2")
    if rows2:
        print("[output2] 앞 5행 — 값이 상식에 맞는지 본다(일봉이면 등락률이 한 자릿수여야 한다)")
        print(f"  {'일자':>10} {'종가':>12} {'시가':>12} {'등락률':>8}")
        for row in rows2[:5]:
            print(
                f"  {row.get('stck_bsop_date', ''):>10}"
                f" {row.get('bstp_nmix_prpr', ''):>12}"
                f" {row.get('bstp_nmix_oprc', ''):>12}"
                f" {row.get('bstp_nmix_prdy_ctrt', ''):>8}"
            )
        print()

    print("-" * 72)
    if verdict:
        print("→ 상승/하락 종목 수가 일자별(output2)에 온다.")
        print("  업종 구성원 중 몇 %가 올랐는지를 과거 시점까지 그대로 얻는다 — 명부 불필요.")
    else:
        print("→ 상승/하락 종목 수가 일자별에 없다. output1(오늘 요약)에만 있다.")
        print("  과거 구간의 '절반 이상 상승'은 이 API 로 못 만든다.")
        print("  대안: 업종 지수 등락률로 모멘텀을 잡거나, 오늘부터 output1 을 매일 쌓는다.")
    return 0


def probe_history_limit(client: KisClient, sectors: list[Sector]) -> int:
    """과거 어느 날짜까지 응답하는가 — 백테스트 구간 2017-01 이 목표."""
    print("=" * 72)
    print("3. 과거 이력 한도 — 2017-01 까지 오는가")
    print("=" * 72)

    targets = sectors[:SECTOR_SAMPLE] or [Sector("0001", "코스피", "코스피")]
    print(f"\n{'업종':>6} {'업종명':<12} {'요청일':>10}  {'행수':>4}  구간/사유")
    for sector in targets:
        for date in PROBE_DATES:
            params = {
                "FID_PERIOD_DIV_CODE": "D",
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": sector.code,
                "FID_INPUT_DATE_1": date,
            }
            try:
                body = client.get(DAILY_PATH, DAILY_TR, params).body
            except KisApiError as exc:
                print(f"{sector.code:>6} {sector.name:<12} {date:>10}  실패  {exc}")
                continue

            rows = _rows(body, "output2")
            dates = [str(r.get("stck_bsop_date", "")) for r in rows]
            dates = [d for d in dates if d]
            span = f"{min(dates)}~{max(dates)}" if dates else "(날짜 필드 없음)"
            print(f"{sector.code:>6} {sector.name:<12} {date:>10}  {len(rows):>4}  {span}")
    return 0


def probe_stock_sector(client: KisClient) -> int:
    """종목 → 업종 매핑이 있는가. 없으면 업종 지수를 받아도 조건에 못 건다."""
    print("=" * 72)
    print("4. 종목 → 업종 매핑 (주식기본조회)")
    print("=" * 72)

    for code, label in (("000660", "SK하이닉스"), ("005930", "삼성전자")):
        try:
            body = client.get(
                "/uapi/domestic-stock/v1/quotations/search-stock-info",
                "CTPF1002R",
                {"PRDT_TYPE_CD": "300", "PDNO": code},
            ).body
        except KisApiError as exc:
            print(f"\n{code} {label}: 실패 — {exc}")
            continue

        rows = _rows(body, "output")
        if not rows:
            print(f"\n{code} {label}: 응답 없음")
            continue

        hits = {k: v for k, v in rows[0].items() if "idx" in k or "bstp" in k or "std" in k}
        print(f"\n{code} {label} — 업종 관련 필드")
        for field, value in hits.items():
            print(f"  {field:<28} = {value}")
        if not hits:
            print("  (업종 관련 필드 없음)")
            print(f"  전체 필드: {', '.join(rows[0].keys())}")
    return 0


def main(argv: list[str]) -> int:
    which = argv[1] if len(argv) > 1 else "all"
    creds = _credentials()
    print(f"환경: {creds.env} ({creds.base_url})\n")
    client = KisClient(creds, get_access_token(creds, cache_path=CACHE_PATH), policy=PROBE_POLICY)

    status, sectors = probe_sector_codes(client)
    print()
    if which in ("all", "daily"):
        status |= probe_daily_fields(client, sectors)
        print()
        status |= probe_history_limit(client, sectors)
        print()
    if which in ("all", "stock"):
        status |= probe_stock_sector(client)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
