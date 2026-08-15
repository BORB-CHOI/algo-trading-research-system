"""OpenDART 공시 목록 백필 — 상장사(유가·코스닥) 전체 공시 이력 (BORB-81).

실행: .venv/Scripts/python scripts/backfill_dart_disclosures.py              # 2015~오늘
      .venv/Scripts/python scripts/backfill_dart_disclosures.py --since 2020-01-01

- 재무제표 백필(backfill_dart.py, BORB-41)과는 다른 것 — 여기서는 "언제 어떤 공시가 났나"
  이벤트 목록(유상증자·CB·최대주주 변경·감사의견 등)을 받는다.
- 원천: OpenDART list.json. 월 단위로 잘라 상장구분(Y 유가 / K 코스닥)별로 페이지를 넘긴다.
  실측(2026-08-15~16): 최근 상장사 월 약 1.1만 건, 100건/페이지, 일 호출 한도 2만.
  과거 바닥은 **1999-03**(전자공시 시작). 1999-02 이전은 0건이라 거기서 멈춘다.
  주의: bgn~end 를 1년으로 주면 status 100 으로 거부된다 — 월 단위로 자른다.
- 저장: data/derived/disclosures/YYYY-MM.parquet (월별). 원본 필드 + 수집시각.
- 체크포인트: 월×구분 단위(_state.json). 다시 실행하면 안 끝난 월만 받는다.
- 이달(진행 중) 파일은 매번 다시 받아 덮어쓴다 — 증분 갱신(update_data.py)이 이걸 쓴다.
- KIS 는 쓰지 않는다. 수급 백필과 한도가 겹치지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "derived" / "disclosures"
STATE_PATH = OUT_DIR / "_state.json"

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
PAGE_SIZE = 100  # API 상한
CORP_CLASSES = ("Y", "K")  # 유가증권 · 코스닥 (E 기타·N 코넥스는 유니버스 밖 — ADR-0003)
DEFAULT_SINCE = "1999-01-01"  # 실측 바닥: 전자공시 시작이 1999-03 (그 이전은 0건)
PAUSE = 0.15  # 일 2만 한도 안에서 여유 있게 (초당 약 6건)
MAX_RETRY = 5


def month_starts(since: date, until: date) -> list[date]:
    out = []
    y, m = since.year, since.month
    while date(y, m, 1) <= until:
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_end(d: date) -> date:
    nxt = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def fetch_page(key: str, bgn: str, end: str, corp_cls: str, page: int) -> dict:
    params = {
        "crtfc_key": key,
        "bgn_de": bgn,
        "end_de": end,
        "corp_cls": corp_cls,
        "page_no": page,
        "page_count": PAGE_SIZE,
    }
    for attempt in range(MAX_RETRY + 1):
        try:
            r = requests.get(LIST_URL, params=params, timeout=20)
            body = r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt >= MAX_RETRY:
                raise RuntimeError(f"네트워크 실패 {bgn}~{end} {corp_cls} p{page}: {e}") from e
            time.sleep(10 * (attempt + 1))
            continue
        status = body.get("status")
        if status == "000":
            return body
        if status == "013":  # 조회 결과 없음
            return {"list": [], "total_page": 0, "total_count": 0}
        if status == "020":  # 일 한도 초과 — 자정 지나야 풀린다
            raise RuntimeError("DART 일 호출 한도(20,000건) 초과 — 내일 이어서 실행")
        if attempt >= MAX_RETRY:
            raise RuntimeError(f"DART 오류 {status} {body.get('message')} ({bgn}~{end} {corp_cls})")
        time.sleep(5)
    raise AssertionError("도달 불가")


def collect_month(key: str, start: date, corp_cls: str) -> list[dict]:
    bgn, end = start.strftime("%Y%m%d"), month_end(start).strftime("%Y%m%d")
    first = fetch_page(key, bgn, end, corp_cls, 1)
    rows = list(first.get("list", []))
    total_page = int(first.get("total_page", 0) or 0)
    for page in range(2, total_page + 1):
        time.sleep(PAUSE)
        rows.extend(fetch_page(key, bgn, end, corp_cls, page).get("list", []))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenDART 공시 목록 백필")
    ap.add_argument("--since", default=DEFAULT_SINCE, help="시작일 YYYY-MM-DD")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("DART_API_KEY 가 .env 에 없다.")
        return 1

    since = date.fromisoformat(args.since)
    today = date.today()
    months = month_starts(since, today)
    state = load_state()
    print(f"대상 {len(months)}개월 × {len(CORP_CLASSES)}구분, 저장 {OUT_DIR}", flush=True)

    calls = 0
    for start in months:
        ym = start.strftime("%Y-%m")
        is_current = start.year == today.year and start.month == today.month
        frames: list[pd.DataFrame] = []
        for cls in CORP_CLASSES:
            k = f"{ym}:{cls}"
            if state.get(k, {}).get("done") and not is_current:
                # 이미 받은 달은 파일에서 다시 읽어 합친다 (구분별 파일이 아니라 월별 파일이라)
                continue
            try:
                rows = collect_month(key, start, cls)
            except RuntimeError as e:
                print(f"  ✗ {ym} {cls}: {e}", flush=True)
                save_state(state)
                return 2
            calls += 1 + max(0, (len(rows) - 1) // PAGE_SIZE)
            frames.append(pd.DataFrame(rows))
            state[k] = {
                "done": not is_current,
                "rows": len(rows),
                "at": datetime.now().isoformat(timespec="seconds"),
            }
        if not frames:
            continue
        path = OUT_DIR / f"{ym}.parquet"
        old = pd.read_parquet(path) if path.exists() else None
        new = (
            pd.concat([f for f in frames if not f.empty], ignore_index=True)
            if any(not f.empty for f in frames)
            else pd.DataFrame()
        )
        merged = new if old is None or old.empty else pd.concat([old, new], ignore_index=True)
        if not merged.empty:
            merged = (
                merged.drop_duplicates(subset=["rcept_no"])
                .sort_values("rcept_dt")
                .reset_index(drop=True)
            )
            merged["_collected_at"] = datetime.now().isoformat(timespec="seconds")
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            merged.to_parquet(path, index=False)
        save_state(state)
        print(
            f"[{datetime.now():%H:%M:%S}] {ym}: {len(merged):,}건 (누적 호출 {calls:,})", flush=True
        )

    print("공시 백필 끝.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
