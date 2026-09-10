"""거래 대상이 아닌 종목 빼기 (ADR-0003).

**"애초에 거래 대상이 아닌 것"만 여기서 뺀다.** 전략과 무관하게 항상 참인 규칙이다.
스팩·KONEX·우선주·리츠는 전략을 어떻게 바꾸든 대상이 아니다.

거래대금·시총 같은 **전략 조건은 여기가 아니라 layer3**(`screening.py`)이다.
그쪽은 튜닝하면서 계속 바뀌고, 이쪽은 거의 안 바뀐다.

제외 규칙은 전부 설정값(`ExclusionPolicy`)으로 뺀다. CLAUDE.md:
"모든 정량 임계값은 placeholder. 하드코딩 ❌."
켜고 끌 수 있어야 §3.13 drop-one 검증으로 각 제외의 기여도를 측정할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# ─────────────────────────────────────────────────────────────
# 식별 규칙 — 단순 문자열 매칭은 오탐이 난다. 실측으로 다듬은 정규식.
# ─────────────────────────────────────────────────────────────

# "미래에셋비전스팩7호", "KB제33호스팩". '아스팩오일'(정상 기업)이 걸리면 안 된다.
# Dept == "SPAC(소속부없음)" 이 더 정확하나, Dept 결측 행이 있어 이름 규칙과 OR 로 쓴다.
SPAC_PATTERN = r"스팩\s?\d*\s?호?$|제\d+호\s?스팩"

# 거래소가 Dept 에 직접 표시하는 상태값. 이름 추측보다 정확하다.
DEPT_SPAC = "SPAC(소속부없음)"
DEPT_WATCH = ("관리종목(소속부없음)", "투자주의환기종목(소속부없음)")

# "롯데리츠", "SK리츠". '메리츠화재/메리츠증권/메리츠금융지주'가 걸리면 안 된다.
REIT_PATTERN = r"(?<!메)리츠"

# 보통주는 종목코드 끝자리가 0. 우선주는 5·7·9·K·L·M 등.
COMMON_STOCK_LAST_DIGIT = "0"

KONEX_MARKET = "KONEX"


@dataclass(frozen=True)
class ExclusionPolicy:
    """어떤 종목을 백테스트에서 뺄지. 전부 placeholder — 근거가 생기면 바꾼다.

    2026-07 실측(최근 3개월 일평균 거래대금 중앙값)에 근거한 초기값:
      KONEX 0.0억(무거래일 22.7%) / 스팩 0.1억 / 우선주 0.8억 / 리츠 4.9억
      vs KOSPI 7.4억 / KOSDAQ 3.5억
    """

    # 유동성이 없어 체결 가정 자체가 성립하지 않는다.
    exclude_konex: bool = True
    exclude_spac: bool = True

    # 사업 실체가 없어 Layer 2의 재료 해석이 원리상 작동하지 않는다.
    # (스팩은 위 유동성 사유와 중복 — 둘 중 하나만으로도 제외 대상)

    # 모종목과 재료가 중복된다. 삼성전자 뉴스가 삼성전자우에도 걸려 이중 계상된다.
    exclude_preferred: bool = True

    # 부동산 임대수익 구조라 §3.9.x 재료 5축 분해가 맞지 않는다.
    exclude_reit: bool = True

    # 관리종목·투자주의환기종목. 거래소가 Dept 에 표시한다.
    # 실전에서 진입이 제약되므로 백테스트에서 사면 실현 불가능한 수익이 된다.
    exclude_watchlisted: bool = True


DEFAULT_POLICY = ExclusionPolicy()


def is_konex(df: pd.DataFrame) -> pd.Series:
    return df["Market"] == KONEX_MARKET


def is_spac(df: pd.DataFrame) -> pd.Series:
    by_name = df["Name"].str.contains(SPAC_PATTERN, regex=True, na=False)
    if "Dept" not in df.columns:
        return by_name
    return by_name | (df["Dept"] == DEPT_SPAC)


def is_watchlisted(df: pd.DataFrame) -> pd.Series:
    """관리종목·투자주의환기종목. Dept 컬럼이 없으면 판정하지 않는다."""
    if "Dept" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["Dept"].isin(DEPT_WATCH)


def is_preferred(df: pd.DataFrame) -> pd.Series:
    return df["Code"].str[-1] != COMMON_STOCK_LAST_DIGIT


def is_reit(df: pd.DataFrame) -> pd.Series:
    return df["Name"].str.contains(REIT_PATTERN, regex=True, na=False)


def exclusion_mask(df: pd.DataFrame, policy: ExclusionPolicy = DEFAULT_POLICY) -> pd.Series:
    """제외 대상이면 True. 어떤 규칙에 걸렸는지 알고 싶으면 개별 is_* 를 쓴다."""
    mask = pd.Series(False, index=df.index)
    if policy.exclude_konex:
        mask |= is_konex(df)
    if policy.exclude_spac:
        mask |= is_spac(df)
    if policy.exclude_preferred:
        mask |= is_preferred(df)
    if policy.exclude_reit:
        mask |= is_reit(df)
    if policy.exclude_watchlisted:
        mask |= is_watchlisted(df)
    return mask


def apply_exclusions(df: pd.DataFrame, policy: ExclusionPolicy = DEFAULT_POLICY) -> pd.DataFrame:
    """제외 규칙을 적용한 유니버스를 돌려준다."""
    return df.loc[~exclusion_mask(df, policy)]


def exclusion_report(df: pd.DataFrame, policy: ExclusionPolicy = DEFAULT_POLICY) -> pd.DataFrame:
    """규칙별로 몇 종목이 걸리는지 — drop-one 검증과 눈으로 보는 점검용.

    규칙끼리 겹치므로 각 행의 합은 전체 제외 수보다 크다.
    """
    rules = {
        "KONEX": (policy.exclude_konex, is_konex),
        "스팩": (policy.exclude_spac, is_spac),
        "우선주": (policy.exclude_preferred, is_preferred),
        "리츠": (policy.exclude_reit, is_reit),
        "관리종목": (policy.exclude_watchlisted, is_watchlisted),
    }
    rows = [
        {"rule": name, "enabled": on, "codes": int(df.loc[fn(df), "Code"].nunique())}
        for name, (on, fn) in rules.items()
    ]
    return pd.DataFrame(rows)
