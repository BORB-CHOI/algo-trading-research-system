"""케이스 검사기 전략 오버레이 배관 (BORB-39 ④).

전략 자체는 **오너가 정한다**. 여기는 "전략 함수 → 매수/매도 신호 목록"으로 바꾸는
배관만 둔다. 등록된 전략은 전부 결정론적 pandas 계산이다 — LLM/MCP 개입 없음(CLAUDE.md).

이 신호는 케이스 탐색용 **시각화**일 뿐이다. 성과 검증은 가드레일 백테스트(BORB-31)가 한다.
여기 숫자로 수익률을 논하지 않는다.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

# 입력: 한 종목 일봉(Date, Open/High/Low/Close/Volume/Amount, 날짜 오름차순, 수정주가).
# 출력: 신호 행만 담은 DataFrame(Date, side['buy'|'sell'], price).
SignalFn = Callable[[pd.DataFrame], pd.DataFrame]


def _crosses(fast: pd.Series, slow: pd.Series) -> tuple[pd.Series, pd.Series]:
    """골든/데드 크로스 시점. 전일은 아래(위)였는데 오늘 위(아래)로 바뀐 날."""
    above = fast > slow
    golden = above & ~above.shift(1, fill_value=False)
    dead = ~above & above.shift(1, fill_value=True)
    # 이동평균이 아직 없는 앞 구간은 신호로 치지 않는다.
    valid = fast.notna() & slow.notna() & fast.shift(1).notna() & slow.shift(1).notna()
    return golden & valid, dead & valid


def ma_cross_example(df: pd.DataFrame, short: int = 5, long: int = 20) -> pd.DataFrame:
    """이평 교차 **예시** — 배관 검증용이지 확정 전략이 아니다.

    기간(5/20)도 placeholder 다(CLAUDE.md). 오너가 전략을 정하면 이 자리를 대체한다.
    """
    fast = df["Close"].rolling(short).mean()
    slow = df["Close"].rolling(long).mean()
    golden, dead = _crosses(fast, slow)
    out = pd.DataFrame(
        {
            "Date": df["Date"],
            "side": pd.Series(pd.NA, index=df.index, dtype="object"),
            "price": df["Close"],
        }
    )
    out.loc[golden, "side"] = "buy"
    out.loc[dead, "side"] = "sell"
    return out.dropna(subset=["side"]).reset_index(drop=True)


# 이름 → 전략 함수. 오너가 전략을 정하면 여기 등록한다.
STRATEGIES: dict[str, SignalFn] = {
    "ma_cross_5_20_예시": ma_cross_example,
}
