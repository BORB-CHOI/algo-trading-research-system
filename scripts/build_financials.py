#!/usr/bin/env python
"""재무 요약 테이블 사전 생성 (BORB-41).

`data/derived/dart/{종목}/{연도}Q4.parquet` 수천 장을 훑어 한 장으로 합친다.
조건검색이 매 호출마다 종목별 파일을 여는 것을 막기 위한 것 — 1,460 종목에 193초였다(실측).

    python scripts/build_financials.py

과거분을 더 내려받은 뒤에는 다시 돌려야 반영된다.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.layer1_data.dart import build_summary, save_summary  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_financials")


def main() -> int:
    log.info("재무 요약 생성 시작 — 종목별 parquet 을 전부 훑는다")
    df = build_summary()
    if df.empty:
        log.warning("재무 데이터가 없다. scripts/backfill_dart.py 를 먼저 돌려라.")
        return 1

    path = save_summary(df)
    years = f"{int(df['year'].min())}~{int(df['year'].max())}"
    log.info("저장 %s — %d행 / 종목 %d개 / 연도 %s", path, len(df), df["code"].nunique(), years)

    missing = [c for c in ("매출액", "영업이익", "당기순이익", "자본총계") if c in df.columns]
    for col in missing:
        n = int(df[col].notna().sum())
        log.info("  %s 값 있는 행 %d/%d (%.0f%%)", col, n, len(df), 100 * n / len(df))
    return 0


if __name__ == "__main__":
    sys.exit(main())
