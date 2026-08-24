"""통합(KRX+넥스트레이드) 거래량·거래대금 — marcap 패널 위에 덮어쓰는 배율 표.

## 왜 필요한가

marcap 은 **KRX 체결만** 담는다. 2025-03-04 넥스트레이드(NXT)가 열린 뒤로 한 종목의
체결이 두 거래소에 나뉘었고, 실제 전체 거래는 둘을 합친 **통합**이다(ADR-0018).
삼성전자 2026-08-21 실측: 통합 18조 6480억 · KRX 7조 7032억 — NXT 몫이 58.7% 다.
조건검색·백테스트가 KRX 값만 보면 거래대금 조건이 실제의 절반쯤으로 잘린다.

## 값을 갈아끼우지 않고 **배율**을 쓰는 이유

나무 수집본은 수정주가 기준이라 과거 거래량이 이미 분할 보정돼 있다. 그 값을 marcap
패널에 그대로 넣으면 조건검색이 한 번 더 보정해(`conditions._adjusted`) 두 번 접힌다.
같은 종목·같은 날의 **통합 ÷ KRX** 배율은 두 값이 같은 기준이라 보정과 무관하다.
그래서 배율만 뽑아 marcap 값에 곱한다 — 원래 쓰던 보정 경로를 건드리지 않는다.

## 덮이는 범위 (데이터 사실 — 고치지 않고 알린다)

- 통합 수집본이 있는 종목만이다. NXT 미상장 종목은 배율이 없어 KRX 값 그대로다.
- 수집본 마지막 날(`unified_last_day`) 이후 날짜도 KRX 그대로다.
- 바꾸는 건 거래량·거래대금뿐이다. 시가·고가·저가·종가·시가총액은 marcap(KRX) 값이다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

from src.layer1_data.derived import DERIVED_DIR, NAMUH_BARS_DIR, load_namuh_bars

# 넥스트레이드 개장일. 이 앞 날짜는 통합 = KRX 라 볼 필요가 없다.
NXT_OPEN = pd.Timestamp("2025-03-04")

MARKETS = ("krx", "unt", "nxt")

# 배율 표를 저장해 두는 곳. 608종목 두 벌을 맞대는 데 35초 걸린다(실측 2026-08-25) —
# 서버가 뜰 때마다 다시 세면 첫 조건검색이 그만큼 멈춘다. 수집본이 늘면 다시 만든다.
UNIFIED_DIR = DERIVED_DIR / "unified"


def is_unified(market: str) -> bool:
    """이 시장 이름이 KRX 말고 다른 체결까지 보는 것인가."""
    return market in ("unt", "nxt")


def _source_stamp(root: Path) -> dict:
    """수집본이 그대로인지 알아볼 지문 — 파일 수와 가장 최근 저장 시각."""
    files = sorted(root.glob("*.parquet"))
    return {
        "files": len(files),
        "mtime": max((f.stat().st_mtime_ns for f in files), default=0),
    }


def _build_ratios(market: str, bars_dir: Path) -> pd.DataFrame:
    """나무 수집본 두 벌(krx · market)을 종목마다 맞대 배율을 뽑는다. 35초쯤 걸린다."""
    root = bars_dir / market / "day"
    frames: list[pd.DataFrame] = []
    for path in sorted(root.glob("*.parquet")):
        code = path.stem
        uni = load_namuh_bars(code, "day", market, bars_dir=bars_dir)
        krx = load_namuh_bars(code, "day", "krx", bars_dir=bars_dir)
        if uni is None or krx is None or uni.empty or krx.empty:
            continue
        cols = ["Date", "Volume", "Amount"]
        m = uni[cols].merge(krx[cols], on="Date", suffixes=("_u", "_k"))
        m = m[(m["Date"] >= NXT_OPEN) & (m["Volume_k"] > 0) & (m["Amount_k"] > 0)]
        if m.empty:
            continue
        frames.append(
            pd.DataFrame(
                {
                    "Date": m["Date"].to_numpy(),
                    "Code": code,
                    "VolMult": (m["Volume_u"] / m["Volume_k"]).to_numpy(),
                    "AmtMult": (m["Amount_u"] / m["Amount_k"]).to_numpy(),
                }
            )
        )
    if not frames:
        return pd.DataFrame(columns=["Date", "Code", "VolMult", "AmtMult"])
    return pd.concat(frames, ignore_index=True)


@lru_cache(maxsize=2)
def unified_ratios(market: str = "unt", bars_dir: Path = NAMUH_BARS_DIR) -> pd.DataFrame:
    """종목·날짜별 **통합 ÷ KRX** 배율 표. 컬럼: Date, Code, VolMult, AmtMult.

    608종목 × 개장 이후 거래일이라 21만 행쯤 나온다(실측 2026-08-25).
    한 번 만들면 `data/derived/unified/` 에 저장해 두고, 수집본이 늘었을 때만 다시 만든다.
    KRX 체결이 0인 날(그날 NXT 에서만 거래)은 배율을 낼 수 없어 뺀다 — 그런 날은
    marcap 값이 그대로 남는다.
    """
    empty = pd.DataFrame(columns=["Date", "Code", "VolMult", "AmtMult"])
    root = bars_dir / market / "day"
    if not root.exists():
        return empty

    stamp = _source_stamp(root)
    cache = UNIFIED_DIR / f"ratios_{market}.parquet"
    meta = UNIFIED_DIR / f"ratios_{market}.json"
    if cache.exists() and meta.exists():
        try:
            if json.loads(meta.read_text(encoding="utf-8")) == stamp:
                return pd.read_parquet(cache)
        except (OSError, ValueError):
            pass  # 캐시가 깨졌으면 그냥 다시 만든다

    out = _build_ratios(market, bars_dir)
    if not out.empty:
        UNIFIED_DIR.mkdir(parents=True, exist_ok=True)
        out.to_parquet(cache, index=False)
        meta.write_text(json.dumps(stamp), encoding="utf-8")
    return out


def unified_last_day(market: str = "unt", bars_dir: Path = NAMUH_BARS_DIR) -> str | None:
    """통합 값이 채워진 마지막 날짜(YYYY-MM-DD). 없으면 None.

    화면이 "이 날까지만 통합입니다"를 띄우는 데 쓴다 — 그 뒤 구간을 통합인 줄 알고
    보면 거래대금이 절반으로 보인다.
    """
    r = unified_ratios(market, bars_dir)
    return None if r.empty else str(r["Date"].max().date())


def apply_unified(
    df: pd.DataFrame, market: str = "unt", bars_dir: Path = NAMUH_BARS_DIR
) -> pd.DataFrame:
    """일봉 패널(long 형)의 거래량·거래대금을 통합 기준으로 바꾼 **새 표**를 돌려준다.

    `market` 이 krx 면(또는 배율 표가 비면) 받은 표를 그대로 돌려준다. 배율이 없는
    종목·날짜도 그대로 남는다 — 없는 값을 지어내지 않는다.
    """
    if not is_unified(market) or df.empty or "Code" not in df.columns:
        return df
    ratios = unified_ratios(market, bars_dir)
    if ratios.empty:
        return df
    out = df.copy()
    out["Code"] = out["Code"].astype(str).str.zfill(6)
    merged = out.merge(ratios, on=["Date", "Code"], how="left")
    vol = merged["VolMult"].fillna(1.0).to_numpy()
    amt = merged["AmtMult"].fillna(1.0).to_numpy()
    if "Volume" in out.columns:
        out["Volume"] = out["Volume"].to_numpy() * vol
    if "Amount" in out.columns:
        out["Amount"] = out["Amount"].to_numpy() * amt
    return out
