"""나무증권(PLUG) 봉 데이터 전량 수집 — 전 종목 × 일/주/월봉 + 분봉 9종.

실행: .venv/Scripts/python scripts/collect_namuh_bars.py            # 전 종목
      .venv/Scripts/python scripts/collect_namuh_bars.py 005930     # 한 종목만 (테스트)

- 서버가 보관한 과거를 전부 받는다: 날짜를 뒤로 넘겨가며(빈 응답이 나올 때까지) 조회.
- 저장: data/derived/namuh_bars/<봉굵기>/<종목코드>.parquet (원본 필드 그대로).
- 중단·재시작 안전: data/derived/namuh_bars/_state.json 에 종목×굵기 단위로 완료를 기록,
  다시 실행하면 안 끝난 것만 이어서 받는다.
- 조회만 한다. 주문 없음.

실측 근거(2026-08-15, scripts/namuh_probe.py):
- 한 호출 최대 5,000건. 봉이 굵을수록 서버 보관이 깊다(1~15분 약 6주, 240분 약 6년).
- 날짜별 마지막에 시각 999900 집계봉이 붙는다 — 원본 그대로 저장하고 사용처에서 거른다.
- 호출 한도 실측 초당 약 7건 → 여유 있게 초당 4건 수준으로 던진다.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from nhplug import NhplugError, call  # noqa: E402
from nhplug.instruments import load_master  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "derived" / "namuh_bars"
STATE_PATH = OUT_DIR / "_state.json"

# (저장 폴더명, gubun, xtick). gubun: 1.일 2.주 3.월 5.분
INTERVALS: list[tuple[str, str, str | None]] = [
    ("day", "1", None),
    ("week", "2", None),
    ("month", "3", None),
    ("min1", "5", "1"),
    ("min3", "5", "3"),
    ("min5", "5", "5"),
    ("min10", "5", "10"),
    ("min15", "5", "15"),
    ("min30", "5", "30"),
    ("min60", "5", "60"),
    ("min120", "5", "120"),
    ("min240", "5", "240"),
]

PAUSE = 0.15  # 호출 간격(초) — 실측 한도(초당 약 7건)의 절반 수준
RATE_RETRY_SLEEP = 3.0  # 한도 초과 시 쉬는 시간
MAX_RATE_RETRY = 5
MAX_PAGES = 200  # 종목×굵기 하나가 무한 반복하지 않게 막는 안전핀


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def rows_of(res: dict) -> list[dict]:
    """봉 배열은 명세와 달리 Output_1 에 온다 — 배열인 Output_N 을 찾는다."""
    for key in sorted(k for k in res if k.startswith("Output")):
        if isinstance(res[key], list):
            return res[key]
    return []


def call_page(code: str, gubun: str, xtick: str | None, edate: str) -> list[dict]:
    """한 페이지 조회. 한도 초과는 쉬었다 재시도, 그 외 오류는 위로 올린다."""
    payload = {
        "market_cd": "KRX",
        "iem_cd": code,
        "edate": edate,
        "array_cnt": "9999",
        "gubun": gubun,
    }
    if xtick:
        payload["xtick"] = xtick
        payload["today_cls_code"] = "0"
    for attempt in range(MAX_RATE_RETRY + 1):
        try:
            return rows_of(call("/krstock/quote/v1/period", payload))
        except NhplugError as e:
            if e.category in ("rate_limit", "network") and attempt < MAX_RATE_RETRY:
                time.sleep(RATE_RETRY_SLEEP if e.category == "rate_limit" else 30.0)
                continue
            if e.code == "11512":  # 데이터 없음 = 바닥
                return []
            raise
    return []


def prev_edate(oldest: str) -> str:
    """받은 것 중 가장 오래된 날짜의 전날 → 다음 페이지 요청일. 월봉은 YYYYMM 로 온다."""
    if len(oldest) == 6:  # YYYYMM (월봉)
        first_of_month = datetime.strptime(oldest + "01", "%Y%m%d").date()
        return (first_of_month - timedelta(days=1)).strftime("%Y%m%d")
    d = datetime.strptime(oldest, "%Y%m%d").date()
    return (d - timedelta(days=1)).strftime("%Y%m%d")


def collect_one(code: str, gubun: str, xtick: str | None) -> pd.DataFrame:
    """한 종목 × 한 굵기 — 빈 응답이 나올 때까지 과거로 넘기며 전부 받는다."""
    frames: list[pd.DataFrame] = []
    edate = date.today().strftime("%Y%m%d")
    seen_oldest = ""
    for _ in range(MAX_PAGES):
        rows = call_page(code, gubun, xtick, edate)
        time.sleep(PAUSE)
        if not rows:
            break
        df = pd.DataFrame(rows)
        frames.append(df)
        oldest = min(df["bsop_date"])
        if oldest == seen_oldest:  # 같은 페이지가 반복되면 바닥
            break
        seen_oldest = oldest
        if len(df) < 100:  # 마지막 조각이면 더 넘겨도 없다
            break
        edate = prev_edate(oldest)
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    keys = [c for c in ("bsop_date", "bsop_time") if c in merged.columns]
    return merged.drop_duplicates(subset=keys).sort_values(keys).reset_index(drop=True)


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None

    master = load_master("m_new_stock")
    codes = [(str(r.sCode), str(r.sKorName)) for r in master.itertuples()]
    if only:
        codes = [(c, n) for c, n in codes if c == only]
        if not codes:
            print(f"마스터에 {only} 가 없다")
            return 1
    print(f"대상 {len(codes)}종목 × {len(INTERVALS)}굵기, 저장 {OUT_DIR}")

    state = load_state()
    t_start = time.time()

    for idx, (code, name) in enumerate(codes, 1):
        code_state = state.setdefault(code, {})
        for folder, gubun, xtick in INTERVALS:
            if code_state.get(folder, {}).get("done"):
                continue
            try:
                df = collect_one(code, gubun, xtick)
            except NhplugError as e:
                code_state[folder] = {"done": False, "error": f"{e.category}/{e.code} {e.message}"}
                save_state(state)
                print(f"  ✗ {code} {name} {folder}: [{e.category}/{e.code}] {e.message}")
                continue
            out_path = OUT_DIR / folder / f"{code}.parquet"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if not df.empty:
                df.to_parquet(out_path, index=False)
            code_state[folder] = {
                "done": True,
                "rows": int(len(df)),
                "oldest": str(df["bsop_date"].min()) if not df.empty else "",
                "collected_at": datetime.now().isoformat(timespec="seconds"),
            }
            save_state(state)
        if idx % 20 == 0 or idx == len(codes):
            elapsed = time.time() - t_start
            print(
                f"[{datetime.now():%H:%M:%S}] {idx}/{len(codes)} 종목 "
                f"({elapsed / 60:.0f}분 경과, 마지막: {code} {name})",
                flush=True,
            )

    print("수집 끝.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
