"""marcap 뒤쪽 공백을 KRX Open API 로 채운다 (ADR-0002 개정 2026-08-18, BORB-44 후속).

marcap 저장소는 갱신이 하루~몇 주 늦다. marcap 마지막 날짜 다음 날부터 오늘까지를
KRX 에서 날짜당 3콜(코스피·코스닥·코넥스)로 받아 `data/derived/recent/{YYYY-MM-DD}.parquet`
에 marcap 모양으로 둔다. 읽는 쪽(`recent.py`)은 그대로다.

전엔 네이버를 종목마다 4천 번 불러 시가총액을 "종가 × 옛 상장주식수"로 어림했다.
이제 시가총액·상장주식수·거래대금이 거래소 값 그대로다(`amount_is_approx: false`).

marcap 이 정본이다 — 같은 날짜가 marcap 에 들어오면 그쪽을 쓰고 여기 파일은 지운다.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.layer1_data import krx_openapi as krx
from src.layer1_data.marcap_loader import available_years, load_years
from src.layer1_data.recent import RECENT_DIR


def marcap_last_date() -> pd.Timestamp | None:
    years = available_years()
    if not years:
        return None
    return load_years(years[-1], years[-1])["Date"].max()


def _weekdays_after(last: date, today: date) -> list[date]:
    """marcap 마지막 날짜 다음 날부터 오늘까지의 평일. 휴장일은 KRX 가 빈 표를 줘서 걸러진다."""
    days: list[date] = []
    d = last + timedelta(days=1)
    while d <= today:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _drop_superseded(out_dir: Path, last: date) -> int:
    """marcap 이 따라잡은 날짜의 보충 파일은 지운다 — 같은 날짜를 두 벌 남기지 않는다."""
    n = 0
    for f in out_dir.glob("*.parquet"):
        try:
            stale = pd.Timestamp(f.stem).date() <= last
        except ValueError:
            continue
        if stale:
            f.unlink()
            n += 1
    return n


def fill_marcap_gap(
    key: str | None = None,
    *,
    today: date | None = None,
    out_dir: Path = RECENT_DIR,
    snapshot=krx.snapshot,
) -> dict:
    """공백 채우기 한 번. 결과 요약(dict)을 돌려준다 — 갱신 로그·화면이 그대로 쓴다.

    이미 받아 둔 날짜는 다시 부르지 않는다(파일이 있으면 건너뜀). 오늘 것은 장 마감 전이면
    빈 표가 와서 저장하지 않는다 — 다음 회차에 받는다.
    """
    key = krx.auth_key() if key is None else key
    if not key:
        return {"skipped": f"{krx.ENV_KEY} 없음"}
    last_ts = marcap_last_date()
    if last_ts is None:
        return {"skipped": "marcap 없음"}
    last = last_ts.date()
    today = today or date.today()
    out_dir.mkdir(parents=True, exist_ok=True)
    removed = _drop_superseded(out_dir, last)

    saved: list[str] = []
    called = 0
    errors: list[str] = []
    for d in _weekdays_after(last, today):
        path = out_dir / f"{d.isoformat()}.parquet"
        if path.exists():
            continue
        try:
            df = snapshot(d.strftime("%Y%m%d"), key)
        except krx.KrxApiError as e:
            errors.append(str(e))
            break  # 키·망 문제면 다음 날짜도 똑같이 막힌다 — 헛호출 금지
        called += 1
        if df.empty:
            continue  # 휴장일이거나 아직 집계 전
        df.to_parquet(path, index=False)
        saved.append(d.isoformat())

    dates = sorted(f.stem for f in out_dir.glob("*.parquet"))
    (out_dir / "meta.json").write_text(
        json.dumps(
            {
                "marcap_last": last.isoformat(),
                "dates": dates,
                "amount_is_approx": False,
                "source": "krx",
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    out: dict = {
        "marcap_last": last.isoformat(),
        "saved": saved,
        "called": called,
        "removed": removed,
        "dates": dates,
    }
    if errors:
        out["errors"] = errors
    return out
