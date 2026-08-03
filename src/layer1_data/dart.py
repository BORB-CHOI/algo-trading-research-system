"""DART 재무 데이터 읽기 (BORB-41 ②).

scripts/backfill_dart.py 가 저장한 parquet 에서 주요 계정만 뽑아 연도별로 정리한다.
계정 매칭은 account_id(IFRS 표준 ID) 우선, 없으면 계정명 폴백.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DART_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "derived" / "dart"

# 표시할 주요 계정: 라벨 → (account_id 후보, 계정명 후보)
KEY_ACCOUNTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "매출액": (("ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"), ("매출액", "수익(매출액)", "영업수익")),
    "영업이익": (("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"), ("영업이익", "영업이익(손실)")),
    "당기순이익": (("ifrs-full_ProfitLoss",), ("당기순이익", "당기순이익(손실)")),
    "자산총계": (("ifrs-full_Assets",), ("자산총계",)),
    "부채총계": (("ifrs-full_Liabilities",), ("부채총계",)),
    "자본총계": (("ifrs-full_Equity",), ("자본총계",)),
}


def _pick(df: pd.DataFrame, ids: tuple[str, ...], names: tuple[str, ...]) -> float | None:
    for col, candidates in (("account_id", ids), ("account_nm", names)):
        if col not in df.columns:
            continue
        hit = df[df[col].isin(candidates)]
        if len(hit):
            v = pd.to_numeric(hit.iloc[0]["thstrm_amount"], errors="coerce")
            if pd.notna(v):
                return float(v)
    return None


def load_financials(code: str) -> list[dict]:
    """종목의 연도별 주요 계정. 최신 연도 우선. 데이터 없으면 빈 리스트."""
    code = code.strip().zfill(6)
    d = DART_DIR / code
    if not d.is_dir():
        return []

    rows = []
    for path in sorted(d.glob("*Q4.parquet"), reverse=True):
        df = pd.read_parquet(path)
        if df.empty:
            continue
        year = int(df["bsns_year"].iloc[0])
        rcept = str(df["rcept_dt"].iloc[0]) if "rcept_dt" in df.columns else ""
        item = {
            "year": year,
            # 공시일 — 백테스트는 이 날짜 이후에만 이 수치를 쓸 수 있다
            "disclosed": f"{rcept[:4]}-{rcept[4:6]}-{rcept[6:8]}" if len(rcept) == 8 else None,
            "fs_div": str(df["fs_div"].iloc[0]) if "fs_div" in df.columns else None,
        }
        for label, (ids, names) in KEY_ACCOUNTS.items():
            item[label] = _pick(df, ids, names)
        rows.append(item)
    return rows


def available_codes() -> set[str]:
    if not DART_DIR.is_dir():
        return set()
    return {p.name for p in DART_DIR.iterdir() if p.is_dir()}
