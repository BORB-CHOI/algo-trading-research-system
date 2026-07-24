"""수정주가 보정 — 액면분할/병합 back-adjust (ADR-0006).

marcap 은 원주가다. 액면분할일에 가격이 −98% 처럼 보이므로 보정 없이는
케이스 검사기 차트도, 백테스트 수익률도 전부 왜곡된다.

원래 api/main.py 안에 있던 로직을 layer1 로 옮겼다(2026-07-24, BORB-31) —
차트(API)와 백테스트 엔진이 **같은 정본**을 쓰기 위해서다.
"""

from __future__ import annotations

import pandas as pd

# 액면분할/병합 감지 임계값 (ADR-0006). 전부 placeholder.
SPLIT_SHARE_HI = 1.5  # 상장주식수가 1.5배 이상 (분할)
SPLIT_SHARE_LO = 1 / 1.5  # 또는 2/3 이하 (병합)
SPLIT_PRICE_MATCH = 0.2  # 주식수 비율 ≈ 가격 역비율 (20% 이내). 유상증자 배제용.


def split_adjustment(df: pd.DataFrame) -> pd.Series:
    """액면분할/병합 back-adjust 계수. df 는 한 종목, 날짜 오름차순.

    분할 = 상장주식수가 크게 변하고(×f) 종가가 그에 맞춰 역방향(÷f)으로 튄 날.
    유상증자(주식수만 늘고 가격은 그만큼 안 빠짐)는 두 조건이 안 맞아 제외된다.
    반환 계수를 OHLC 에 곱하면 과거 가격이 축소되어 최신일 기준으로 연속이 된다.
    """
    close = df["Close"].tolist()
    stocks = df["Stocks"].tolist()
    n = len(df)
    adj = [1.0] * n
    running = 1.0  # 어떤 날짜 이후에 있는 분할 계수들의 곱
    for i in range(n - 1, -1, -1):
        adj[i] = 1.0 / running
        if i > 0 and stocks[i - 1] and close[i]:
            share_ratio = stocks[i] / stocks[i - 1]
            price_ratio = close[i - 1] / close[i]
            big = share_ratio >= SPLIT_SHARE_HI or share_ratio <= SPLIT_SHARE_LO
            matches = price_ratio > 0 and abs(share_ratio / price_ratio - 1) < SPLIT_PRICE_MATCH
            if big and matches:
                running *= share_ratio  # 이 분할은 그 이전(더 과거) 날짜들에 적용된다
    return pd.Series(adj, index=df.index)


def apply_split_adjustment(df: pd.DataFrame) -> pd.DataFrame:
    """한 종목 일봉의 OHLC 를 back-adjust 하고 Volume 을 역보정한 사본을 돌려준다."""
    factor = split_adjustment(df)
    out = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        out[col] = out[col] * factor
    out["Volume"] = out["Volume"] / factor  # 분할 전 거래량은 비교 위해 늘린다
    return out
