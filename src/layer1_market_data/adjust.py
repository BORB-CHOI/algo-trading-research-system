"""수정주가 보정 — 분할·병합 back-adjust (ADR-0006, 2026-09-10 개정).

marcap 은 원주가다. 액면분할일에 가격이 −98% 처럼 보이므로 보정 없이는
케이스 검사기 차트도, 백테스트 수익률도 전부 왜곡된다.

원래 api/main.py 안에 있던 로직을 layer1(지금 1단계)로 옮겼다(2026-07-24, BORB-31) —
차트(API)와 백테스트 엔진이 **같은 정본**을 쓰기 위해서다.

## 계수는 짐작하지 않는다 — **증권사가 이미 조정한 값에서 뽑는다**

우리 일봉(`namuh_bars/*/day/`)은 **이미 수정주가**다. 그 값을 marcap 원주가로 나누면
그 날의 조정 계수가 그대로 나온다. 증권사에 다시 묻지 않는다(호출 0).

    계수(d) = 나무 수정주가(d) ÷ marcap 원주가(d)

004090 을 예로 들면(2021-04-15 재상장):

    marcap 원주가  2021-04-09  281,000   →  2021-04-15  18,900   (주식수 10배)
    나무 수정주가   2021-04-09   14,550   →  2021-04-15  18,900
    계수                        0.0518                    1.0

계수 19.31 = 주식수 10배 × 락 비율 1.9313(`prtt_rate` −48.22). **주식수 비율만으로는**
**안 맞는다** — 액면분할과 무상감자가 같이 일어난 날이라 배수가 둘의 곱이다.

## 왜 짐작을 버렸나 — 실측으로 재 봤다

조정이 잘 됐나는 **하루 변동이 가격제한(±30%)을 크게 넘는 날의 수**로 잰다. 조정이
빠지면 거기서 튄다. 2016~2026 · 상장 종목 · 584만 일:

| 방식 | 튄 날 | 비율 |
|---|---|---|
| 옛 짐작(주식수 1.5배 + 가격 20% 일치) | 680 | 0.0116% |
| 락 표시 + 주식수 비율 | 707 | 0.0121% |
| **나무 수정주가에서 뽑은 계수** | **128** | **0.0022%** |

락 표시로 사건은 더 잡히지만(1,488 vs 739) **배수를 주식수 비율로 쓰면 위 004090 처럼
틀린다.** 증권사가 이미 맞춰 놓은 값을 쓰는 게 5배 이상 깨끗하다.

## 남은 구멍 — 상폐 종목은 우리 일봉이 없다

일봉은 상장 종목만 있다(2,769종목). 상폐 종목(2,717종목)은 marcap 밖에 없어
**옛 짐작으로 떨어진다.** 백테스트에 상폐 종목이 반드시 들어가야 하므로(살아남은 것만
보는 착시 방지) 두 길을 섞어 둔다. 이 구멍은 `BORB-95` 에 적어 두었다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# 상폐 종목만 쓰는 옛 짐작 규칙의 임계값. 전부 placeholder 다.
SPLIT_SHARE_HI = 1.5  # 상장주식수가 1.5배 이상 (분할)
SPLIT_SHARE_LO = 1 / 1.5  # 또는 2/3 이하 (병합)
SPLIT_PRICE_MATCH = 0.2  # 주식수 비율 ≈ 가격 역비율 (20% 이내). 유상증자 배제용.


def broker_factor(
    df: pd.DataFrame, code: str, bars_dir: Path, market: str = "krx"
) -> pd.Series | None:
    """**증권사 수정주가에서 뽑은 조정 계수.** 일봉이 없으면 `None`(상폐 종목).

    `df` 는 한 종목 marcap, 날짜 오름차순. 돌려주는 계수를 OHLC 에 곱하면 된다.

    marcap 에는 있는데 일봉에 없는 날이 약 1% 있다(실측). 계수는 사건 사이에서 상수라
    **가장 가까운 앞날의 계수를 이어 쓴다** — 그래야 marcap 날짜를 하나도 안 잃는다.
    """
    path = Path(bars_dir) / market / "day" / f"{code}.parquet"
    if not path.exists():
        return None
    try:
        bars = pd.read_parquet(path, columns=["bsop_date", "stck_prpr"])
    except (OSError, ValueError, KeyError):
        return None
    bars = bars.assign(
        d=bars["bsop_date"].astype(str),
        adj=pd.to_numeric(bars["stck_prpr"], errors="coerce"),
    )
    bars = bars[bars["adj"] > 0].drop_duplicates("d", keep="last")
    if bars.empty:
        return None

    days = pd.to_datetime(df["Date"]).dt.strftime("%Y%m%d")
    joined = pd.DataFrame({"d": days.to_numpy(), "raw": df["Close"].to_numpy()}).merge(
        bars[["d", "adj"]], on="d", how="left"
    )
    factor = joined["adj"] / joined["raw"].where(joined["raw"] > 0)
    # 사건 사이에서는 계수가 상수다 — 빈 날은 앞뒤로 이어 채운다.
    factor = factor.ffill().bfill()
    if factor.isna().all():
        return None
    return pd.Series(factor.to_numpy(), index=df.index)


def split_adjustment(df: pd.DataFrame) -> pd.Series:
    """**옛 짐작 규칙** — 일봉이 없는 상폐 종목에만 쓴다. 임계값은 placeholder.

    분할 = 상장주식수가 크게 변하고(×f) 종가가 그에 맞춰 역방향(÷f)으로 튄 날.
    유상증자(주식수만 늘고 가격은 그만큼 안 빠짐)는 두 조건이 안 맞아 제외된다.
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


def apply_split_adjustment(df: pd.DataFrame, factor: pd.Series | None = None) -> pd.DataFrame:
    """한 종목 일봉의 OHLC 를 back-adjust 하고 Volume 을 역보정한 사본을 돌려준다.

    `factor` 를 주면 그걸 쓴다(증권사 수정주가에서 뽑은 계수). 안 주면 옛 짐작으로 만든다.
    """
    if factor is None:
        factor = split_adjustment(df)
    out = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        out[col] = out[col] * factor
    # 분할 전 거래량은 비교 위해 늘린다. 계수가 0 이면 나눌 수 없으니 그대로 둔다.
    safe = factor.where(factor > 0, np.nan)
    out["Volume"] = (out["Volume"] / safe).fillna(df["Volume"])
    return out
