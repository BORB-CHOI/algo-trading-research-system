"""DART 재무 데이터 읽기 (BORB-41 ②).

scripts/backfill_dart.py 가 저장한 parquet 에서 주요 계정만 뽑아 연도별로 정리한다.
계정 매칭은 account_id(IFRS 표준 ID) 우선, 없으면 계정명 폴백.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DART_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "derived" / "dart"

# 어느 표(sj_div)에서 뽑을지까지 못 박는다. 같은 계정명이 여러 표에 **다른 값으로** 나오기 때문이다.
# 실측(2024Q4 400종목): 자본총계가 자본변동표에 6개 값으로 등장 — 그중 하나는 전체의 2.6% 였다.
# 손익 3종은 회사마다 손익계산서(IS)나 포괄손익계산서(CIS) 중 한쪽으로만 내므로 둘 다 허용하되,
# 현금흐름표·자본변동표의 동명 계정은 배제한다(지배/비지배가 갈려 ROE 가 틀어진다).
BS = ("BS",)
PL = ("IS", "CIS")

# 라벨 → (account_id 후보, 계정명 후보, 허용 표)
KEY_ACCOUNTS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "매출액": (
        ("ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"),
        ("매출액", "수익(매출액)", "영업수익"),
        PL,
    ),
    "영업이익": (
        ("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"),
        ("영업이익", "영업이익(손실)"),
        PL,
    ),
    "당기순이익": (("ifrs-full_ProfitLoss",), ("당기순이익", "당기순이익(손실)"), PL),
    "자산총계": (("ifrs-full_Assets",), ("자산총계",), BS),
    "부채총계": (("ifrs-full_Liabilities",), ("부채총계",), BS),
    "자본총계": (("ifrs-full_Equity",), ("자본총계",), BS),
}


def _pick(
    df: pd.DataFrame,
    ids: tuple[str, ...],
    names: tuple[str, ...],
    sj_divs: tuple[str, ...],
    amount_col: str = "thstrm_amount",
) -> float | None:
    if amount_col not in df.columns:
        return None  # 전기 금액(frmtrm_amount)이 아예 없는 보고서가 있다.
    if "sj_div" in df.columns:
        df = df[df["sj_div"].isin(sj_divs)]
    for col, candidates in (("account_id", ids), ("account_nm", names)):
        if col not in df.columns:
            continue
        hit = df[df[col].isin(candidates)]
        if len(hit):
            v = pd.to_numeric(hit.iloc[0][amount_col], errors="coerce")
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
        for label, (ids, names, sj_divs) in KEY_ACCOUNTS.items():
            item[label] = _pick(df, ids, names, sj_divs)
        rows.append(item)
    return rows


def available_codes() -> set[str]:
    if not DART_DIR.is_dir():
        return set()
    return {p.name for p in DART_DIR.iterdir() if p.is_dir()}


# ── 조건검색용 요약 테이블 ──────────────────────────────────
#
# 종목별 parquet 을 그때그때 열면 1,460 종목에 193초다(실측). 조건검색은 매 호출 전 종목을
# 봐야 하므로 미리 한 장으로 합쳐둔다 — 수정주가를 build_adjusted.py 로 미리 계산하는 것과 같은 결.

SUMMARY_PATH = DART_DIR.parent / "financials.parquet"

# 증가율은 전기 금액(frmtrm_amount)으로 구한다. 과거 연도 파일이 없어도 계산되고,
# 같은 보고서 안의 값이라 회계 기준 변경에 따른 어긋남도 없다.
GROWTH_ACCOUNTS = ("매출액", "영업이익")

_summary_cache: pd.DataFrame | None = None


def build_summary() -> pd.DataFrame:
    """전 종목 × 연도 재무 요약. 종목별 parquet 전체를 훑으므로 느리다 — 사전 생성용."""
    rows: list[dict] = []
    for code in sorted(available_codes()):
        for path in sorted((DART_DIR / code).glob("*Q4.parquet")):
            df = pd.read_parquet(path)
            if df.empty:
                continue
            rcept = str(df["rcept_dt"].iloc[0]) if "rcept_dt" in df.columns else ""
            item: dict = {
                "code": code,
                "year": int(df["bsns_year"].iloc[0]),
                "disclosed": pd.to_datetime(rcept, format="%Y%m%d", errors="coerce"),
                "fs_div": str(df["fs_div"].iloc[0]) if "fs_div" in df.columns else None,
            }
            for label, (ids, names, sj_divs) in KEY_ACCOUNTS.items():
                item[label] = _pick(df, ids, names, sj_divs)
            for label in GROWTH_ACCOUNTS:
                ids, names, sj_divs = KEY_ACCOUNTS[label]
                item[f"{label}_전기"] = _pick(df, ids, names, sj_divs, "frmtrm_amount")
            rows.append(item)

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["code", "year"]).reset_index(drop=True)
    return out


def save_summary(df: pd.DataFrame | None = None) -> Path:
    df = build_summary() if df is None else df
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(SUMMARY_PATH, index=False)
    return SUMMARY_PATH


def load_summary(force: bool = False) -> pd.DataFrame:
    """요약 테이블. 없으면 빈 프레임 — 재무 조건은 전 종목 탈락이 아니라 '판정 불가'로 다룬다."""
    global _summary_cache
    if _summary_cache is None or force:
        _summary_cache = pd.read_parquet(SUMMARY_PATH) if SUMMARY_PATH.exists() else pd.DataFrame()
    return _summary_cache


def as_of(base_date) -> pd.DataFrame:
    """기준일까지 **공시된** 것 중 종목별 최신 1행. index=종목코드.

    `disclosed`(접수일) 기준으로 자른다. 사업연도 종료일로 자르면 아직 발표되지 않은
    실적을 미리 보는 것이다 — 12월 결산 법인의 사업보고서는 이듬해 3월에야 접수된다.
    "신호 계산 시점 < 체결 시점" 불변식의 재무판(DATA_SCHEMA §4 as-of 원칙).
    """
    df = load_summary()
    if df.empty:
        return df
    cutoff = pd.Timestamp(base_date)
    visible = df[df["disclosed"].notna() & (df["disclosed"] <= cutoff)]
    if visible.empty:
        return visible.set_index("code") if "code" in visible.columns else visible
    latest = visible.sort_values(["code", "year"]).groupby("code").tail(1)
    return latest.set_index("code")
