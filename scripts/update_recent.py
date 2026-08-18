#!/usr/bin/env python
"""marcap 이후 최신 거래일 보충 — KRX Open API (BORB-44, ADR-0002 개정 2026-08-18).

실행: .venv/Scripts/python scripts/update_recent.py

marcap 은 저장소 갱신이 하루~몇 주 늦다. 그 공백만 KRX 일별매매정보(날짜당 3콜)로 채워
data/derived/recent/{YYYY-MM-DD}.parquet 로 저장한다(marcap 스키마 호환).
`.env` 의 KRX_AUTH_KEY 가 필요하다. 로직은 `src/layer1_data/krx_gapfill.py` 에 있다 —
`update_data.py` 와 화면의 빠른 갱신도 같은 함수를 부른다(규칙 한 벌).

전에는 네이버 종목시세를 종목마다 불렀다(4천 콜, 시가총액은 어림). 그 경로는 지웠다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.layer1_data.krx_gapfill import fill_marcap_gap  # noqa: E402


def main() -> int:
    load_dotenv(ROOT / ".env")
    result = fill_marcap_gap()
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("skipped") else 0


if __name__ == "__main__":
    raise SystemExit(main())
