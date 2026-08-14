"""KIS 수급(외국인·기관·개인 일별) 백필 — 전 종목 × 1995~현재 (ADR-0012, BORB-79).

실행: .venv/Scripts/python scripts/backfill_kis_supply.py            # 전 종목
      .venv/Scripts/python scripts/backfill_kis_supply.py 005930     # 한 종목만 (테스트)

- 대상: marcap 에 등장하는 모든 종목(상장폐지 포함). 각 종목의 marcap 첫 거래일까지
  30거래일씩 날짜를 뒤로 밀며 받는다. 오너 결정(2026-08-15): 전 종목, 가능한 한 과거부터.
- 저장: data/derived/supply/<종목코드>.parquet — API 원본 필드 + 요청일·수집시각.
- 중단·재시작 안전: data/derived/supply/_state.json 에 종목 단위 완료 기록.
- 조회만 한다. 주문 없음. KIS 실전(real) 키 필요 — 이 TR 은 모의투자에 없다.
- 스로틀·재시도(EGW00201)·토큰 캐시는 기존 KIS 클라이언트가 맡는다.

소요 추정: 약 45~50만 호출, 초당 2건 기준 연속 2~3일. 끊겨도 이어서 돌리면 된다.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.layer1_data.marcap_loader import available_years, load_years, normalize_code  # noqa: E402
from src.layer4_execution.brokers.kis.auth import KisCredentials, get_access_token  # noqa: E402
from src.layer4_execution.brokers.kis.client import (  # noqa: E402
    CallPolicy,
    KisApiError,
    KisClient,
)

OUT_DIR = ROOT / "data" / "derived" / "supply"
STATE_PATH = OUT_DIR / "_state.json"
TOKEN_CACHE = ROOT / "kis_token.json"

SUPPLY_PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
SUPPLY_TR = "FHPTJ04160001"

# 초당 2건 — ADR-0012 실측(EGW00201)에 맞춘 보수적 간격. 재시도는 클라이언트 정책.
POLICY = CallPolicy(min_interval_sec=0.5, max_attempts=5, backoff_base_sec=2.0)

FLOOR_DATE = "19940101"  # API 실측 바닥(1994-11)보다 조금 아래 — 이보다 과거는 요청하지 않는다
MAX_PAGES_PER_CODE = 500  # 30년 ≈ 260페이지. 그 두 배 안전핀


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def build_universe() -> pd.DataFrame:
    """marcap 전 연도에서 종목별 (첫 거래일, 마지막 거래일, 이름)을 만든다. 상폐 포함."""
    years = [y for y in available_years() if y >= 1995]
    spans: dict[str, dict] = {}
    for year in years:
        df = load_years(year, year)
        if df.empty:
            continue
        df = df.assign(Code=normalize_code(df["Code"]))
        grouped = df.groupby("Code").agg(first=("Date", "min"), last=("Date", "max"))
        names = df.drop_duplicates("Code", keep="last").set_index("Code")["Name"]
        for code, row in grouped.iterrows():
            entry = spans.setdefault(
                str(code), {"first": row["first"], "last": row["last"], "name": ""}
            )
            entry["first"] = min(entry["first"], row["first"])
            entry["last"] = max(entry["last"], row["last"])
            entry["name"] = str(names.get(code, entry["name"]))
    out = pd.DataFrame.from_dict(spans, orient="index")
    # 최근에 살아있는 종목부터 — 전략 작업에 먼저 필요한 순서
    return out.sort_values("last", ascending=False)


def fetch_page(client: KisClient, code: str, req_date: str) -> list[dict]:
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": req_date,
        "FID_ORG_ADJ_PRC": "",
        "FID_ETC_CLS_CODE": "",
    }
    body = client.get(SUPPLY_PATH, SUPPLY_TR, params).body
    rows = body.get("output2") or body.get("output1") or []
    if isinstance(rows, dict):
        rows = [rows]
    return [r for r in rows if isinstance(r, dict) and r.get("stck_bsop_date")]


def collect_code(client: KisClient, code: str, first_date: str, last_date: str) -> pd.DataFrame:
    """한 종목 — 마지막 거래일부터 첫 거래일(또는 API 바닥)까지 뒤로 밀며 받는다."""
    frames: list[pd.DataFrame] = []
    req = last_date
    prev_oldest = ""
    for _ in range(MAX_PAGES_PER_CODE):
        rows = fetch_page(client, code, req)
        if not rows:
            break
        df = pd.DataFrame(rows)
        df["_req_date"] = req
        frames.append(df)
        oldest = min(df["stck_bsop_date"])
        if oldest == prev_oldest:  # 진전이 없으면 바닥
            break
        prev_oldest = oldest
        if oldest <= first_date or oldest <= FLOOR_DATE:
            break
        req = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    merged["_collected_at"] = datetime.now().isoformat(timespec="seconds")
    return (
        merged.drop_duplicates(subset=["stck_bsop_date"])
        .sort_values("stck_bsop_date")
        .reset_index(drop=True)
    )


def make_client() -> tuple[KisClient, object]:
    load_dotenv(ROOT / ".env")
    import os

    creds = KisCredentials(
        app_key=os.environ["KIS_APP_KEY"].strip(),
        app_secret=os.environ["KIS_APP_SECRET"].strip(),
        env=os.environ.get("KIS_ENV", "vts").strip(),
    )
    if creds.env != "real":
        raise SystemExit("이 TR 은 실전(real) 전용이다. .env 의 KIS_ENV=real 확인.")
    token = get_access_token(creds, cache_path=TOKEN_CACHE)
    return KisClient(creds, token, policy=POLICY), token


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None

    print("유니버스 구성 중 (marcap 전 연도 스캔)...", flush=True)
    universe = build_universe()
    if only:
        universe = universe.loc[universe.index == only]
        if universe.empty:
            print(f"marcap 에 {only} 가 없다")
            return 1
    print(f"대상 {len(universe)}종목 (상폐 포함), 저장 {OUT_DIR}", flush=True)

    state = load_state()
    client, token = make_client()
    start_at = datetime.now()

    for idx, (code, row) in enumerate(universe.iterrows(), 1):
        if state.get(code, {}).get("done"):
            continue
        # 24시간 토큰 — 만료가 가까우면 갱신해서 클라이언트를 새로 만든다
        if token.is_expired(datetime.now(UTC)):
            client, token = make_client()

        first = row["first"].strftime("%Y%m%d")
        last = row["last"].strftime("%Y%m%d")
        df = None
        for net_retry in range(1, 11):
            try:
                df = collect_code(client, str(code), first, last)
                break
            except KisApiError as e:
                state[code] = {"done": False, "error": str(e)}
                save_state(state)
                print(f"  ✗ {code} {row['name']}: {e}", flush=True)
                break
            except OSError as e:  # requests 의 ConnectionError 포함 — 네트워크 순단
                # 며칠 도는 작업이라 죽지 않고 쉬었다 같은 종목부터 다시.
                wait = min(60 * net_retry, 600)
                print(f"  ~ 네트워크 오류({net_retry}/10), {wait}초 후 재시도: {e}", flush=True)
                time.sleep(wait)
                client, token = make_client()
        if df is None:
            continue

        if not df.empty:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            df.to_parquet(OUT_DIR / f"{code}.parquet", index=False)
        state[code] = {
            "done": True,
            "rows": int(len(df)),
            "oldest": str(df["stck_bsop_date"].min()) if not df.empty else "",
            "newest": str(df["stck_bsop_date"].max()) if not df.empty else "",
            "collected_at": datetime.now().isoformat(timespec="seconds"),
        }
        save_state(state)

        if idx % 10 == 0 or idx == len(universe):
            done_n = sum(1 for v in state.values() if v.get("done"))
            elapsed = (datetime.now() - start_at).total_seconds() / 3600
            print(
                f"[{datetime.now():%m-%d %H:%M}] 완료 {done_n}/{len(universe)}종목 "
                f"({elapsed:.1f}시간 경과, 마지막: {code} {row['name']})",
                flush=True,
            )

    print("백필 끝.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
