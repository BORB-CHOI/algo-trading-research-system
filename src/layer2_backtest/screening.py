"""오늘 따져볼 종목 추리기 — 전략 조건 (§4.2).

layer1 `exclusions.py` 와 역할이 다르다.

- layer1 = **애초에 거래 대상이 아닌 것** (스팩·KONEX·우선주·리츠·관리종목).
  전략을 어떻게 바꾸든 대상이 아니다. 거의 안 바뀐다.
- layer3(여기) = **전략상 이런 종목을 노린다**. 거래대금·시총·등락률 조건.
  튜닝하면서 계속 바뀐다. Validate 구간에서 조정하는 대상.

지침서가 §4.2를 layer3로 둔 이유가 이것이다 — 여기 숫자는 전략 그 자체다.

## 상태: 미구현

임계값이 하나도 정해지지 않았다. CLAUDE.md "모든 정량 임계값은 placeholder,
하드코딩 ❌" 이므로, 값을 먼저 박고 시작하지 않는다.

정하기 전에 필요한 것:
1. 거래비용·슬리피지 모델 — 거래대금 하한은 슬리피지가 결정한다.
   marcap 실측(2026-07): KOSPI 일평균 거래대금 중앙값 7.4억, KOSDAQ 3.5억.
2. 시총 구간 근거 — 최신일 분포는 500억 미만 978종목, 1천~3천억 668,
   1~3조 148, 10조+ 68. 어디를 노릴지는 전략 결정이다.
3. Train/Validate 분할 확정 (§4.1). 임계값 튜닝은 Validate 까지만.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ScreeningRule:
    """전략 조건. **전부 placeholder — 확정값 아님.**

    None = 해당 조건 미적용. 근거 없이 숫자를 박지 않기 위해 기본값을 전부 None 으로 둔다.
    """

    min_amount: float | None = None  # 일 거래대금 하한 (원)
    min_marcap: float | None = None  # 시총 하한 (원)
    max_marcap: float | None = None  # 시총 상한 (원)


def screen(df: pd.DataFrame, rule: ScreeningRule) -> pd.DataFrame:
    """조건을 만족하는 행만 남긴다. 조건이 None 이면 그 조건은 적용하지 않는다."""
    mask = pd.Series(True, index=df.index)
    if rule.min_amount is not None:
        mask &= df["Amount"] >= rule.min_amount
    if rule.min_marcap is not None:
        mask &= df["Marcap"] >= rule.min_marcap
    if rule.max_marcap is not None:
        mask &= df["Marcap"] <= rule.max_marcap
    return df.loc[mask]
