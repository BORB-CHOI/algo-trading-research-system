"""HTS 조회상위 20 종목을 한 번 찍어본다 (KIS 실전 조회).

첫 관통 확인용 스크립트다 — 토큰 발급·캐싱·조회가 실제로 도는지 눈으로 본다.
**조회만 한다.** 주문은 이 경로에 없다(CLAUDE.md 단계 6).

    python scripts/kis_top_view.py

토큰은 `kis_token.json` 에 캐시되어 만료 전까지 재사용된다(.gitignore 등재됨).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.layer4_execution.brokers.kis.auth import KisCredentials, get_access_token  # noqa: E402
from src.layer4_execution.brokers.kis.client import KisClient  # noqa: E402
from src.layer4_execution.brokers.kis.quotes import fetch_hts_top_view  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "kis_token.json"

# 시장구분 코드 → 사람이 읽을 이름. 응답이 코드만 주므로 여기서 붙인다.
#
# ⚠ 이 매핑은 **실측 추정**이다. 공식 문서에 이 필드(mrkt_div_cls_code)의 코드표가 없다.
# 2026-08-04 실거래 응답에서 J 와 Q 만 관찰됐고, 코스닥 종목이 Q 로 온 것을 근거로 매핑했다.
# 주의: 같은 글자라도 다른 TR 의 요청 파라미터 fid_cond_mrkt_div_code 에서는 J=KRX,
# NX=NXT, UN=통합 으로 의미가 다르다. 이 값을 종목 필터링에 쓰기 전에 확인이 필요하다.
MARKETS = {"J": "KOSPI", "Q": "KOSDAQ"}


def main() -> int:
    load_dotenv(ROOT / ".env")

    app_key = os.environ.get("KIS_APP_KEY", "").strip()
    app_secret = os.environ.get("KIS_APP_SECRET", "").strip()
    env = os.environ.get("KIS_ENV", "vts").strip()

    if not app_key or not app_secret:
        print("KIS_APP_KEY / KIS_APP_SECRET 이 .env 에 없다. .env.example 참고.", file=sys.stderr)
        return 1

    creds = KisCredentials(app_key=app_key, app_secret=app_secret, env=env)
    print(f"환경: {env} ({creds.base_url})")

    token = get_access_token(creds, cache_path=CACHE_PATH)
    print(f"토큰 만료: {token.expires_at.isoformat()}")

    items = fetch_hts_top_view(KisClient(creds, token))
    if not items:
        print("조회 결과 없음 (장 시작 전이거나 데이터 미제공 시간대일 수 있다).")
        return 0

    print(f"\nHTS 조회상위 {len(items)}종목")
    for item in items:
        market = MARKETS.get(item.market, item.market or "-")
        print(f"  {item.rank:2}. {item.code}  [{market}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
