"""parquet 저장·읽기 한 군데 — **반쯤 쓰이다 만 파일이 남지 않게 한다.**

## 무엇이 문제였나

`df.to_parquet(path)` 는 **원본을 먼저 비우고** 처음부터 다시 쓴다. 그 사이에 프로그램이
죽으면 0바이트 파일이 그대로 남는다. 다음 회차가 그 파일을 열면 이렇게 터진다:

    ArrowInvalid: Could not open Parquet input source '<Buffer>':
    Parquet file size is 0 bytes

실제 사고 2026-08-29 03:17 — 분봉 단계에서 이 오류 하나로 갱신이 통째로 죽었다.
그때까지 30분 동안 받아 둔 주·월봉은 남았지만, 뒤에 올 수급·거래원·공시·신용잔고는
아예 못 돌았다. **파일 하나 때문에 회차 전체를 버린 것이다.**

## 어떻게 막나

| | 하는 일 |
|---|---|
| `save` | 옆에 임시 파일로 **다 쓴 뒤** 이름만 바꿔 끼운다 |
| `read` | 못 읽는 파일은 터뜨리지 말고 "저장된 게 없다"로 본다 |

이름 바꾸기(`os.replace`)는 도중에 쪼개지지 않는다. 그래서 언제 죽더라도 파일은
**옛 내용 그대로**거나 **새 내용 그대로**지, 중간이 없다.

읽기 쪽은 옛 사고로 이미 깨져 있는 파일을 위한 것이다. `None` 을 받은 쪽은 그 종목을
"처음 받는 것"으로 치고 전체를 다시 받는다 — 한 종목 다시 받는 값이 회차 전체를 버리는
값보다 훨씬 싸다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


def save(df: pd.DataFrame, path: Path) -> None:
    """옆에 다 쓴 뒤 이름을 바꿔 끼운다 — 도중에 죽어도 반쪽 파일이 안 남는다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 같은 폴더에 둬야 이름 바꾸기가 한 번에 끝난다(다른 드라이브면 복사가 된다).
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def read(path: Path, since: str = "", date_col: str = "bsop_date") -> pd.DataFrame | None:
    """저장본을 읽는다. **없거나 못 읽으면 `None`** — 부르는 쪽은 처음 받는 것으로 친다.

    `ArrowInvalid` 는 `ValueError` 를 물려받는다(0바이트·잘린 파일이 여기로 온다).

    `since` 를 주면 **그 날짜부터만 읽는다.** 1분봉이 39일에서 262 거래일로 깊어지면
    파일이 6.7배가 되는데, 굵은 분봉을 만들 땐 늘 최근 며칠만 쓴다. 통째로 읽으면
    안 쓸 과거를 읽느라 그 단계가 깊이에 비례해 느려진다.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        if since:
            return pd.read_parquet(path, filters=[(date_col, ">=", str(since))])
        return pd.read_parquet(path)
    except (OSError, ValueError, KeyError):
        if since:  # 거르기가 안 먹는 파일(열이 없거나 옛 형식)이면 통째로 읽어 본다
            try:
                return pd.read_parquet(path)
            except (OSError, ValueError):
                return None
        return None
