"""키움 REST API **조회 전용** 프로브 — 1분봉을 나무와 대조해 "나눠 받아도 되나"를 본다.

## 왜 물어보나

갱신 회차의 병목은 **나무 1분봉 5,526콜 ÷ 4.5/s = 20분** 하나다. 키움에도 1분봉이 있고
한도가 따로 논다면 종목을 나눠 받아 그 시간을 반으로 줄일 수 있다.

## 다만 값이 **완전히** 같아야만 쓴다

증권사가 다르면 규칙도 다르다. 2026-08-29 하루에만 이런 걸 만났다:
  · 나무 주봉이 자기 일봉과 1,339종목에서 어긋남
  · 나무가 NXT 60분봉의 09:00 경계를 날마다 다르게 줌
  · 저장된 min1 과 min3 의 가격 계열이 다른 종목이 있음
종목 A는 나무, 종목 B는 키움으로 받으면 종목마다 다른 규칙이 섞인다. 그건 10분 벌자고
치를 값이 아니다. **그래서 먼저 대조한다.**

## 안전

- **조회만 한다.** 주문·정정·취소 TR(`kt1`·`kt10000` 계열)은 아래에서 막는다.
- 실행: `.venv/Scripts/python scripts/kiwoom_probe.py [종목코드]`
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.layer1_data import parquet_io  # noqa: E402

BASE = os.environ.get("KIWOOM_BASE_URL", "https://api.kiwoom.com").rstrip("/")
CHART_PATH = "/api/dostk/chart"
MINUTE_TR = "ka10080"  # 주식분봉차트조회
TIMEOUT = 15


def _guard(api_id: str) -> None:
    """주문 계열 TR 은 이 프로브로 절대 못 부르게 막는다 (CLAUDE.md: 조회 전용)."""
    if not api_id.lower().startswith("ka"):
        raise SystemExit(f"조회 TR 이 아니다: {api_id} — 이 프로브는 조회만 한다")


def token() -> str:
    r = requests.post(
        f"{BASE}/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "appkey": os.environ["KIWOOM_APP_KEY"].strip(),
            "secretkey": os.environ["KIWOOM_APP_SECRET"].strip(),
        },
        headers={"Content-Type": "application/json;charset=UTF-8"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    body = r.json()
    tok = body.get("token") or body.get("access_token")
    if not tok:
        raise SystemExit(f"토큰을 못 받았다: {body}")
    return tok


def call(tok: str, api_id: str, payload: dict, cont: str = "", key: str = "") -> tuple[dict, dict]:
    _guard(api_id)
    r = requests.post(
        f"{BASE}{CHART_PATH}",
        json=payload,
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {tok}",
            "api-id": api_id,
            "cont-yn": cont,
            "next-key": key,
        },
        timeout=TIMEOUT,
    )
    return r.json(), dict(r.headers)


def minutes(tok: str, code: str, tic: str = "1") -> tuple[list, dict]:
    body, head = call(
        tok, MINUTE_TR, {"stk_cd": code, "tic_scope": tic, "upd_stkpc_tp": "1"}
    )
    rows = next(
        (v for k, v in body.items() if isinstance(v, list) and v and isinstance(v[0], dict)),
        [],
    )
    return rows, {"rt_cd": body.get("return_code"), "msg": body.get("return_msg"), **{
        k: head.get(k) for k in ("cont-yn", "next-key")
    }}


def main() -> int:
    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    print(f"BASE {BASE} · 종목 {code}", flush=True)
    tok = token()
    print("토큰 OK", flush=True)

    t = time.perf_counter()
    rows, meta = minutes(tok, code)
    print(f"ka10080 1분봉 {len(rows)}줄 · {time.perf_counter() - t:.2f}초 · {meta}")
    if not rows:
        return 1
    print("첫 줄 열 이름:", sorted(rows[0]))
    print("앞 3줄:", rows[:3])

    # 초당 몇 건까지 받나 — 같은 종목으로 10번 연달아 부른다
    t = time.perf_counter()
    fails = 0
    for _ in range(10):
        got, m = minutes(tok, code)
        if not got:
            fails += 1
    el = time.perf_counter() - t
    print(f"연속 10콜: {el:.2f}초 → 초당 {10 / el:.2f}건 · 실패 {fails}")

    # 나무 1분봉과 대조
    ours = parquet_io.read(ROOT / "data/derived/namuh_bars/krx/min1" / f"{code}.parquet")
    if ours is None:
        print("우리 1분봉이 없다 — 대조 못 함")
        return 0
    got = pd.DataFrame(rows)
    print("\n키움 열:", list(got.columns)[:12])
    print("나무 열:", list(ours.columns)[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main())


def compare(code: str = "005930") -> None:
    """키움 1분봉과 우리(나무) 1분봉을 같은 날·같은 시각으로 맞대 본다."""
    tok = token()
    rows, _ = minutes(tok, code)
    got = pd.DataFrame(rows)
    got["date"] = got["cntr_tm"].str[:8]
    got["time"] = got["cntr_tm"].str[8:14]
    # 키움은 가격에 전일대비 부호를 붙여 준다(-257000). 크기만 쓴다.
    for a, b in (("open_pric", "o"), ("high_pric", "h"), ("low_pric", "low"), ("cur_prc", "c")):
        got[b] = pd.to_numeric(got[a], errors="coerce").abs()
    got["v"] = pd.to_numeric(got["trde_qty"], errors="coerce")

    ours = parquet_io.read(ROOT / "data/derived/namuh_bars/krx/min1" / f"{code}.parquet")
    if ours is None:
        print("우리 1분봉이 없다")
        return
    ours = ours.copy()
    ours["date"] = ours["bsop_date"].astype(str)
    ours["time"] = ours["bsop_time"].astype(str).str.zfill(6)
    for c in ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr", "vol"):
        ours[c] = pd.to_numeric(ours[c], errors="coerce")

    j = got.merge(ours, on=["date", "time"], suffixes=("_k", "_n"))
    if j.empty:
        print("겹치는 봉이 없다")
        return
    same = (
        (j["o"] == j["stck_oprc"]) & (j["h"] == j["stck_hgpr"])
        & (j["low"] == j["stck_lwpr"]) & (j["c"] == j["stck_prpr"]) & (j["v"] == j["vol"])
    )
    print(f"\n겹치는 봉 {len(j):,} · 완전 일치 {int(same.sum()):,} ({same.mean() * 100:.3f}%)")
    for a, b, name in (("o", "stck_oprc", "시가"), ("h", "stck_hgpr", "고가"),
                       ("low", "stck_lwpr", "저가"), ("c", "stck_prpr", "종가"), ("v", "vol", "거래량")):
        d = (j[a] != j[b]).sum()
        if d:
            print(f"  {name} 다른 봉 {d:,} · 차이 중앙값 {(j[a] - j[b]).abs().median():,.0f}")
    print("  거래대금(tr_pbmn): 키움 응답에 " + ("있음" if "trde_prica" in got else "**없음**"))
