"""KIS 조회 실측 프로브 — 미확정 2건을 실제 응답으로 확정한다.

문서에 없는 것을 추측으로 채우지 않기 위한 스크립트다. **조회만 한다**
(CLAUDE.md 단계 6 — 주문 전송 없음).

    python scripts/kis_probe_supply.py            # 둘 다
    python scripts/kis_probe_supply.py market     # BORB-47 만
    python scripts/kis_probe_supply.py supply     # BORB-34 만

## BORB-47 — `mrkt_div_cls_code` 코드표

HTS조회상위20 응답의 시장구분 코드에 공식 코드표가 없다. 공식 샘플
(`examples_llm/.../hts_top_view.py`)에도 응답 필드 설명이 없다. 그래서 응답으로 온
종목코드를 **marcap 의 Market 컬럼과 대조**해 코드가 실제로 무슨 시장인지 맞춘다.
관측 못 한 코드(코넥스 등)는 여전히 미확정으로 남는다 — 그 사실도 같이 찍는다.

## BORB-34 — 수급 API 과거 이력 한도

`종목별 투자자매매동향(일별)`(TR `FHPTJ04160001`)은 날짜 하나(`FID_INPUT_DATE_1`)를
받아 그 시점 기준 구간을 돌려준다. 과거 며칠치까지 주는지, 몇 년 전 날짜를 넣어도
응답하는지 문서에 없다. 과거 날짜를 단계적으로 넣어보며 **실제 응답 유무와 행 수**를
기록한다. 백테스트에 쓰려면 2017-01 까지 필요하다(CLAUDE.md 데이터 범위).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.layer1_data.marcap_loader import load_years, normalize_code  # noqa: E402
from src.layer4_execution.brokers.kis.auth import KisCredentials, get_access_token  # noqa: E402
from src.layer4_execution.brokers.kis.client import KisApiError, KisClient  # noqa: E402
from src.layer4_execution.brokers.kis.quotes import fetch_hts_top_view  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "kis_token.json"

SUPPLY_PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
SUPPLY_TR = "FHPTJ04160001"

# 프로브 종목 — 코스피 대형주 하나면 충분하다(한도는 종목이 아니라 API 속성).
PROBE_CODE = "005930"

# 호출 간 간격. EGW00201(초당 거래건수 초과)이 실제로 떨어져서 넣었다.
# 값은 placeholder — 백필용 스로틀은 별도로 실측해서 정한다.
THROTTLE_SEC = 0.6

# 상폐 프로브 표본 수. 한 종목만 보면 그 종목 사정인지 API 사정인지 모른다.
DELISTED_SAMPLE = 4

# 과거 어디까지 주는지 볼 기준일들. 촘촘하게 볼 필요 없이 자릿수만 잡는다.
PROBE_DATES = [
    "20260731",  # 최근
    "20260601",
    "20260101",
    "20250601",
    "20240102",
    "20220103",
    "20170102",  # 백테스트 시작 시점 (CLAUDE.md 데이터 범위)
]


@dataclass(frozen=True)
class ProbeResult:
    """프로브 한 번의 결과. 실패도 결과다 — 조용히 삼키지 않는다."""

    label: str
    ok: bool
    rows: int
    detail: str


def _credentials() -> KisCredentials:
    load_dotenv(ROOT / ".env")
    app_key = os.environ.get("KIS_APP_KEY", "").strip()
    app_secret = os.environ.get("KIS_APP_SECRET", "").strip()
    env = os.environ.get("KIS_ENV", "vts").strip()
    if not app_key or not app_secret:
        raise SystemExit("KIS_APP_KEY / KIS_APP_SECRET 이 .env 에 없다. .env.example 참고.")
    return KisCredentials(app_key=app_key, app_secret=app_secret, env=env)


def _market_truth() -> dict[str, str]:
    """종목코드 → 시장(marcap 기준). 최신 연도만 읽는다 — 지금 상장된 종목이면 충분."""
    year = pd.Timestamp.today().year
    df = load_years(year, year)
    if df.empty:  # 연초라 올해 파일이 비었을 수 있다
        df = load_years(year - 1, year - 1)
    df = df.assign(Code=normalize_code(df["Code"]))
    latest = df.sort_values("Date").groupby("Code").tail(1)
    return dict(zip(latest["Code"], latest["Market"], strict=True))


def probe_market_codes(client: KisClient) -> int:
    """BORB-47 — 응답의 시장구분 코드를 marcap Market 과 대조한다."""
    print("=" * 72)
    print("BORB-47  mrkt_div_cls_code 실측 대조")
    print("=" * 72)

    items = fetch_hts_top_view(client)
    if not items:
        print("조회 결과 없음 — 장 시작 전이거나 데이터 미제공 시간대다. 장중에 다시 돌려라.")
        return 1

    truth = _market_truth()
    observed: dict[str, dict[str, int]] = {}
    unknown_codes: list[str] = []

    for item in items:
        market = truth.get(item.code)
        if market is None:
            unknown_codes.append(item.code)
            market = "(marcap 에 없음)"
        observed.setdefault(item.market or "(빈값)", {}).setdefault(market, 0)
        observed[item.market or "(빈값)"][market] += 1

    print(f"\n조회 {len(items)}종목 — 코드별 실제 시장 분포")
    for code, dist in sorted(observed.items()):
        pairs = ", ".join(f"{m}×{n}" for m, n in sorted(dist.items()))
        verdict = "일관" if len([m for m in dist if not m.startswith("(")]) == 1 else "⚠ 섞임"
        print(f"  {code!r:8} → {pairs}   [{verdict}]")

    if unknown_codes:
        print(f"\n⚠ marcap 에 없는 종목 {len(unknown_codes)}건: {', '.join(unknown_codes)}")
        print("  (신규상장·ETF·스팩 등 유니버스 제외 대상일 수 있다)")

    print("\n주의 — 이 결과는 '오늘 조회상위에 뜬 종목'만 덮는다.")
    print("  KONEX 나 다른 코드가 여기 안 나왔다고 없는 게 아니다. 관측 범위 밖은 여전히 미확정.")
    return 0


def probe_supply_history(client: KisClient) -> int:
    """BORB-34 — 수급 API 가 과거 어느 날짜까지 응답하는지 본다."""
    print("=" * 72)
    print("BORB-34  종목별 투자자매매동향(일별) 과거 이력 한도")
    print(f"         TR={SUPPLY_TR}  종목={PROBE_CODE}")
    print("=" * 72)

    results: list[ProbeResult] = []
    first_body: dict | None = None

    for date in PROBE_DATES:
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": PROBE_CODE,
            "FID_INPUT_DATE_1": date,
            "FID_ORG_ADJ_PRC": "",
            "FID_ETC_CLS_CODE": "",
        }
        try:
            response = client.get(SUPPLY_PATH, SUPPLY_TR, params)
        except KisApiError as exc:
            results.append(ProbeResult(date, False, 0, str(exc)))
            continue

        body = response.body
        if first_body is None:
            first_body = body
        rows = body.get("output2") or body.get("output1") or []
        if isinstance(rows, dict):
            rows = [rows]
        dates = [str(r.get("stck_bsop_date", "")) for r in rows if isinstance(r, dict)]
        dates = [d for d in dates if d]
        span = f"{min(dates)}~{max(dates)}" if dates else "(날짜 필드 없음)"
        results.append(ProbeResult(date, True, len(rows), span))

    print(f"\n{'요청일':>10}  {'결과':>4}  {'행수':>4}  구간/사유")
    for r in results:
        mark = "OK" if r.ok else "실패"
        print(f"{r.label:>10}  {mark:>4}  {r.rows:>4}  {r.detail}")

    if first_body is not None:
        rows = first_body.get("output2") or first_body.get("output1") or []
        if isinstance(rows, dict):
            rows = [rows]
        if rows and isinstance(rows[0], dict):
            print("\n응답 필드 (첫 행) — 어떤 수급 항목이 오는지:")
            for key, value in rows[0].items():
                print(f"  {key:<28} = {value}")

    reachable = [r for r in results if r.ok and r.rows > 0]
    if reachable:
        print(f"\n→ 응답이 온 가장 과거 요청일: {min(r.label for r in reachable)}")
    else:
        print("\n→ 어느 날짜에서도 데이터가 오지 않았다. 권한·TR·장 시간대를 먼저 의심하라.")
    return 0


def probe_delisted(client: KisClient) -> int:
    """상장폐지 종목의 수급이 조회되는가 (ADR-0012 미해결 2번).

    안 되면 상폐 종목 수급이 통째로 비고, CLAUDE.md 가 금지한 생존 편향이
    수급 신호에만 생긴다. 백필 설계가 통째로 달라지는 문제라 먼저 확인한다.

    비교군으로 살아 있는 종목(삼성전자)을 같은 날짜로 함께 부른다 — 상폐라서
    안 나온 건지 그 날짜가 문제인 건지 구분하기 위해서다.
    """
    print("=" * 72)
    print("ADR-0012 미해결 2번  상장폐지 종목 수급 조회 가능 여부")
    print("=" * 72)

    targets = _delisted_probe_targets()
    if not targets:
        print("marcap 에서 상폐 종목을 찾지 못했다.")
        return 1

    print(f"\n{'종목':>8}  {'구분':<10} {'상폐(추정)':<12} {'요청일':<10} 결과")
    ok_count = 0
    for code, name, last_date, label in targets:
        # 마지막 거래일 직전을 요청한다 — 살아 있던 시점의 수급이 남아 있는지 본다.
        req = (last_date - pd.Timedelta(days=5)).strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": req,
            "FID_ORG_ADJ_PRC": "",
            "FID_ETC_CLS_CODE": "",
        }
        time.sleep(THROTTLE_SEC)  # EGW00201 회피
        try:
            body = client.get(SUPPLY_PATH, SUPPLY_TR, params).body
        except KisApiError as exc:
            print(f"{code:>8}  {label:<10} {last_date.date()}   {req}   실패: {exc}")
            continue

        rows = body.get("output2") or body.get("output1") or []
        if isinstance(rows, dict):
            rows = [rows]
        dates = [str(r.get("stck_bsop_date", "")) for r in rows if isinstance(r, dict)]
        dates = [d for d in dates if d]
        span = f"{min(dates)}~{max(dates)}" if dates else "(빈 응답)"
        if dates:
            ok_count += 1
        print(f"{code:>8}  {label:<10} {last_date.date()}   {req}   {len(rows):>2}행 {span}  {name}")

    print(f"\n→ {ok_count}/{len(targets)} 건에서 데이터가 왔다.")
    print("  상폐 종목이 0 이고 비교군(생존)만 오면 → 백필로 상폐 수급을 못 채운다는 뜻이다.")
    return 0


def _delisted_probe_targets() -> list[tuple[str, str, pd.Timestamp, str]]:
    """상폐 추정 종목 몇 개 + 비교군(생존 종목) 하나."""
    df = load_years(2024, pd.Timestamp.today().year)
    if df.empty:
        return []
    df = df.assign(Code=normalize_code(df["Code"]))
    last = df.groupby("Code")["Date"].max()
    recent = df["Date"].max()
    names = df.drop_duplicates("Code").set_index("Code")["Name"]

    gone = last[last < recent - pd.Timedelta(days=30)].sort_values(ascending=False)
    targets = [
        (code, str(names.get(code, "?")), date, "상폐(추정)")
        for code, date in list(gone.items())[:DELISTED_SAMPLE]
    ]
    targets.append((PROBE_CODE, str(names.get(PROBE_CODE, "?")), recent, "비교군(생존)"))
    return targets


def main(argv: list[str]) -> int:
    which = argv[1] if len(argv) > 1 else "all"
    creds = _credentials()
    print(f"환경: {creds.env} ({creds.base_url})\n")
    client = KisClient(creds, get_access_token(creds, cache_path=CACHE_PATH))

    status = 0
    if which in ("all", "market"):
        status |= probe_market_codes(client)
        print()
    if which in ("all", "supply"):
        status |= probe_supply_history(client)
        print()
    if which in ("all", "delisted"):
        status |= probe_delisted(client)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
