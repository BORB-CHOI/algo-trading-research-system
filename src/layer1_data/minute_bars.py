"""굵은 분봉을 **1분봉으로 만든다** — 주·월봉을 일봉으로 만드는 것과 같은 구조.

## 왜 필요한가

분봉은 굵기 9종 × 종목 × 시장 = 파일 49,734개다. 굵은 것도 다 받으면 나무 한도(초당 4.5)에
걸려 **3.07시간**이다. 1분봉만 받으면 5,526콜 = **20분**이고, 나머지 8종은 만들면 된다.

굵은 분봉은 보관이 6.2년이라 안 받아도 안 잃는다(1분봉은 56일). 그래서 원본은 그대로 두고
**진행 중인 꼬리만 만들어 붙이고, 매 회차 전 종목을 호출 0으로 대조**한다.

## 나무가 봉을 묶는 규칙 (실측 2026-08-29로 알아냈다)

### 1. 기준 시각이 시장마다 다르다

| 시장 | 거래 시간 | 기준 |
|---|---|---|
| krx | 09:01 ~ 15:30 | **09:00** |
| 통합(unt) · NXT | 08:01 ~ 20:00 | **08:00** |

봉 이름은 **끝 시각**이다(090300 = 0901+0902+0903). 그래서 `(지난 분 - 1) // 굵기`로 묶는다.
자정 기준으로 세면 09:00 = 540분이 120·240으로 안 나눠떨어져 어긋난다
(실측: 그래서 min240 이 4.5% 였다).

### 2. 거래가 없는 분에도 1분봉이 있다 — 값을 만들 땐 뺀다

거래 없는 분은 거래량 0에 직전 값이 그대로 실려 온다. 그걸 시가로 쓰면 어긋난다.
**거래 있는 봉으로 시·고·저·종을 잡고**, 그 묶음에 거래가 하나도 없으면 그때만 전부를 쓴다.
거래량은 그냥 다 더하면 된다(0을 더해도 같다).
실측: 이 하나로 min60 이 77.8% → 91.7% 로 올랐다.

### 3. 하루의 마지막 봉은 격자를 안 따른다

krx 는 장이 15:20 에 끝나고 종가가 15:30 에 찍힌다. 나무는 **마지막 격자 경계 뒤를 전부**
마감 봉(15:30)에 담는다. 실측 005930 2026-08-28 min3:
`151900(41,701) + 152000(99,081) + 153000(1,146,522) = 1,287,304` = 나무 15:30 봉 그대로.

### 4. NXT·통합의 60분봉만 프리마켓을 다음 봉에 넘긴다

09:00 봉은 **거래량 0인 빈 자리**로 두고, 프리마켓(08:01~09:00)을 10:00 봉에 합친다.
실측 40종목 1,384봉: 09:00 봉이 0인 경우가 97.3%.
120·240분봉은 첫 봉이 이미 프리마켓을 품으므로(10:00·12:00) 이 규칙이 필요 없다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import parquet_io

# 장이 열리는 시각 — 봉을 묶는 기준점이다(분 단위).
ANCHOR = {"krx": 9 * 60, "unt": 8 * 60, "nxt": 8 * 60}

# 마감동시호가가 찍히는 시각 — **장 시작부터 몇 분인지**로 적는다(`ANCHOR` 를 뺀 값).
# 이 봉만 15:30 **에서 새로 시작하는** 봉이라 묶을 때 따로 봐야 한다(`bucket_of`).
AUCTION_AT = {m: 15 * 60 + 30 - a for m, a in ANCHOR.items()}

CLOSING_MARK = "999900"  # 장 마감 뒤 집계 봉 — 굵은 봉에도 그대로 하나 실린다
OHLC = ["stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr"]
SUMS = ["vol", "tr_pbmn"]


def minutes_of(times: pd.Series, market: str) -> pd.Series:
    """`HHMMSS` 를 장 시작부터 몇 분인지로 바꾼다."""
    t = times.astype(str).str.zfill(6)
    return t.str[:2].astype(int) * 60 + t.str[2:4].astype(int) - ANCHOR[market]


def lone_auction(days: pd.Series, mins: pd.Series, gap: int) -> pd.Series:
    """어느 줄이 **마감 동시호가**인가 — 시계를 박아 두지 않고 모양으로 찾는다.

    동시호가는 1분 동안 쌓인 봉이 아니라 **그 시각에 한 번 체결된 것**이다. 그래서 다른
    봉과 달리 자기 자리에서 새로 시작한다.

    ## 왜 시계를 못 박나 — 수능일에 1분을 잃었다 (실측 2026-08-30)

    옛 규칙은 `15:30 이면 동시호가`였다. 그런데 **수능일(2025-11-13)은 장이 한 시간 늦게
    열고 늦게 닫는다** — 10:00~16:20, 동시호가 16:30. 그날 15:30 은 그냥 거래 중인 1분인데
    동시호가로 보는 바람에 앞 봉과 이름이 겹쳐 **한 줄이 통째로 사라졌다**(068270 3,534주).
    262 거래일 중 하루가 그렇게 망가져 있었고, 굵은 분봉도 그날만 틀렸다.

    ## 어떻게 찾나

    연속 체결이 끝나고 한참 뒤에 **혼자 서는 봉**이다. 셋을 다 만족해야 한다:

        · 앞뒤 1분에 봉이 없다 (혼자 선다)
        · `gap` 분 앞에는 봉이 있다 (= 연속 체결의 마지막 분)
        · 오후 3시~5시 사이다

    `gap` 은 시각을 어느 쪽 이름으로 세느냐에 따라 다르다 — 봉이 **시작한** 시각이면 11
    (15:19 → 15:30), **끝난** 시각이면 10 (15:20 → 15:30).

    장 마감 뒤에 하나 더 오는 봉(정상일 15:35~36)이나 통합·NXT 애프터마켓 첫 봉(15:40)은
    `gap` 앞에 봉이 없어서 안 걸린다.
    """
    m = mins.astype("int64")
    key = days.astype(str) + ":" + m.astype(str)
    have = set(key)

    def near(shift: int) -> pd.Series:
        return (days.astype(str) + ":" + (m + shift).astype(str)).isin(have)

    return ~near(-1) & ~near(1) & near(-gap) & (m >= 15 * 60) & (m <= 17 * 60)


def bucket_of(
    mins: pd.Series, market: str, width: int, days: pd.Series | None = None
) -> pd.Series:
    """그 분이 몇 번째 봉에 드나 — **봉이 시작한 분**을 격자로 나눈다.

    우리 봉 이름은 끝 시각이라 1을 빼면 시작 분이 된다. 다만 **마감동시호가(15:30)만
    예외**다 — 그건 15:29~15:30 을 담은 봉이 아니라 15:30 **에** 한 번 체결된 것이라
    거기서 새 봉이 시작한다. 1을 빼면 앞 봉에 딸려 들어간다.

    ## 왜 고쳤나 (실측 2026-08-30)

    옛 규칙은 "장 마감(15:20) 뒤는 전부 마지막 정규 봉에 담는다"였다. **나무가 그렇게
    묶었기 때문**이다. 1분봉 창구를 키움으로 옮긴 뒤 키움이 직접 주는 굵은 봉과 맞대 보니
    갈리는 자리가 딱 여기였다 — 키움은 15:30 을 격자대로 넣는다:

        krx min30  키움: … 1430(15:00까지) · 1500(15:20까지) · **1530(동시호가만)**
                   옛것: … 1500          · 1530(15:20+동시호가를 한 봉으로 뭉갬)
        krx min60  키움: … 1500 = 15:01~15:20 + 동시호가  (한 봉이 맞다)

    즉 굵기마다 다른 게 아니라 **격자에 15:30 이 걸리느냐**로 갈린다. 격자대로 나누면
    두 경우가 저절로 맞는다. 값 자체(시·고·저·종·거래량)는 옛 규칙에서도 같았다 —
    **경계만 틀렸다.**
    """
    if days is None:  # 날짜를 안 주면 옛 방식 — 정상일만 맞는다
        is_auction = mins == AUCTION_AT[market]
    else:
        is_auction = lone_auction(days, mins + ANCHOR[market], gap=10)
    return (mins - 1).where(~is_auction, mins) // width


def _pre_market_fold(buckets: pd.Series, market: str, width: int) -> pd.Series:
    """**더 이상 아무것도 안 한다** — 나무 때문에 넣었던 규칙이라 키움 1분봉엔 걸면 안 된다.

    옛 규칙(규칙 4): NXT 60분봉에서 09:00 봉을 비우고 프리마켓을 10:00 봉에 합친다.
    나무가 그렇게 줬기 때문이다(실측 2026-08-29, 30종목: NXT 83.66% → 99.28%).

    1분봉을 키움으로 바꾸고 다시 재보니 **정반대다** (실측 2026-08-30, 통합·NXT 4종목 ·
    1,650봉 · 키움이 직접 주는 60분봉을 잣대로):

        NXT   합치기 켬 90.91%  →  **끔 100.00%**
        통합  합치기 켬 100%    ·   끔 100%   (원래 이 규칙이 안 걸린다)

    즉 그 접힘은 나무가 봉을 묶던 방식이었고, 키움은 자정 격자 그대로 묶는다.
    껍데기만 남겨 둔다 — 지운 자리를 나중에 다시 만들지 않도록 이력을 남기려고.
    """
    _ = (market, width)
    return buckets


def complete_days(one_min: pd.DataFrame, market: str) -> set[str]:
    """1분봉이 **그 날 처음부터** 있는 날들 — 그런 날만 굵은 봉을 만들 수 있다.

    1분봉은 보관이 56일이라, 경계에 걸린 날은 앞이 잘린 채로 저장돼 있다.
    실측 2026-08-29: 005930 의 2026-07-07·07-28 은 1분봉이 34개(14:49부터)뿐인데
    나무 240분봉에는 그 날이 통째로 있다(보관 6.2년). 잘린 걸로 만들면 당연히 틀린다.
    남은 어긋남의 대부분이 이것이었다.
    """
    if one_min.empty:
        return set()
    work = one_min[one_min["bsop_time"].astype(str) != CLOSING_MARK]
    if work.empty:
        return set()
    first = work.groupby(work["bsop_date"].astype(str))["bsop_time"].min().astype(str).str.zfill(6)
    opens = ANCHOR[market] + 1  # 장 첫 1분봉 — krx 09:01, 통합·NXT 08:01
    want = f"{opens // 60:02d}{opens % 60:02d}00"
    return set(first[first == want].index)


def prepare(one_min: pd.DataFrame, market: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """굵기마다 되풀이하지 않게 **한 번만** 해 두는 손질 — 숫자 변환과 분 계산.

    실측 2026-08-29: 안 하면 한 종목에서 `to_numeric` 이 8번(굵기마다) 돈다.
    프로파일에서 대조 시간의 31% 가 거기였다.
    """
    if one_min.empty:
        return one_min.iloc[:0].copy(), one_min.iloc[:0].copy()
    work = one_min.copy()
    is_close = work["bsop_time"].astype(str) == CLOSING_MARK
    tail = work[is_close]
    work = work[~is_close].copy()
    if work.empty:
        return work, tail
    for col in [*OHLC, *SUMS]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work["_m"] = minutes_of(work["bsop_time"], market)
    work["_d"] = pd.to_numeric(work["bsop_date"], errors="coerce").astype("int64")
    return work, tail


def synthesize(one_min: pd.DataFrame, market: str, width: int) -> pd.DataFrame:
    """1분봉 한 종목분을 굵기 `width` 분봉으로 묶는다. 열 차례는 그대로 둔다."""
    return synthesize_from(*prepare(one_min, market), one_min.columns, market, width)


def synthesize_from(work, tail, columns, market: str, width: int) -> pd.DataFrame:
    """`prepare` 로 손질해 둔 표에서 묶는다 — 굵기마다 이걸 부른다.

    **판다스를 최소로 만진다.** 이 함수는 한 회차에 4만 번 넘게 불리므로, 표를 열마다
    만들고 형을 바꾸면 그 고정 비용이 단계 전체를 잡아먹는다(프로파일 2026-08-29).
    묶은 결과는 넘파이 배열로 들고 있다가 **마지막에 한 번** 표로 만든다.
    """
    columns = list(columns)
    if work.empty:
        return tail.reset_index(drop=True) if len(tail) else pd.DataFrame(columns=columns)
    # 묶음 열쇠는 **숫자로** 만든다. 날짜와 자리를 글자로 이어 붙이면 판다스가 원소마다
    # 문자열 연산을 돌아 호출 하나에 정규식이 수백 번 돈다.
    bucket = bucket_of(work["_m"], market, width, work["bsop_date"])
    bucket = _pre_market_fold(bucket, market, width)
    keys = (work["_d"].to_numpy() * 10_000 + bucket.to_numpy()).astype("int64")

    grouped = work.groupby(keys, sort=True)
    traded = work[work["vol"].to_numpy() > 0]
    got = {
        "stck_hgpr": grouped["stck_hgpr"].max(),
        "stck_lwpr": grouped["stck_lwpr"].min(),
        "vol": grouped["vol"].sum(),
        "bsop_date": grouped["bsop_date"].last(),
        "bsop_time": grouped["bsop_time"].last(),
    }
    if "tr_pbmn" in work.columns:
        got["tr_pbmn"] = grouped["tr_pbmn"].sum()
    index = got["vol"].index
    # 시·종가와 고·저는 **거래가 있는 봉으로** 잡는다(규칙 2). 그 묶음에 거래가 하나도
    # 없으면 그때만 전부로 채운다 — 거래 없는 분은 직전 값이 그대로 실려 오기 때문이다.
    if len(traded):
        tg = traded.groupby(keys[work["vol"].to_numpy() > 0], sort=True)
        for name, how in (("stck_oprc", "first"), ("stck_prpr", "last"),
                          ("stck_hgpr", "max"), ("stck_lwpr", "min")):
            got[name] = getattr(tg[name], how)().reindex(index).fillna(
                getattr(grouped[name], how)()
            )
    else:
        got["stck_oprc"] = grouped["stck_oprc"].first()
        got["stck_prpr"] = grouped["stck_prpr"].last()

    data: dict[str, object] = {}
    for col in columns:
        if col in got:
            v = got[col]
            data[col] = (
                np.asarray(v).astype("int64").astype(str)
                if col in OHLC or col in SUMS
                else np.asarray(v, dtype=object).astype(str)
            )
        else:
            data[col] = np.full(len(index), "", dtype=object)
    out = pd.DataFrame(data, columns=columns)
    if not tail.empty:
        out = pd.concat([out, tail[columns].astype(str)], ignore_index=True)
    return out.reset_index(drop=True)


def _fold(src: pd.DataFrame) -> pd.DataFrame:
    g = src.groupby("_k", sort=True)
    return pd.DataFrame(
        {
            "stck_oprc": g["stck_oprc"].first(),
            "stck_hgpr": g["stck_hgpr"].max(),
            "stck_lwpr": g["stck_lwpr"].min(),
            "stck_prpr": g["stck_prpr"].last(),
        }
    )


def compare_from(made: pd.DataFrame, stored: pd.DataFrame) -> dict:
    """이미 만들어 둔 봉과 저장본을 견준다 — 날짜·시각이 겹치는 것만.

    ⚠️ **덮기 전에 불러야 한다.** 덮은 뒤에 부르면 저장본이 곧 만든 값이라 늘 0 이 나온다.

    저장본은 굵기에 따라 수천 줄이다. 거기 전부에 글자 열쇠를 만들면 이 함수가 단계에서
    제일 비싼 일이 된다 — **만든 봉이 든 날만** 잘라 놓고 본다.
    """
    if made.empty or stored is None or stored.empty:
        return {"checked": 0, "bad": 0}
    days = set(made["bsop_date"].astype(str))
    stored = stored[stored["bsop_date"].astype(str).isin(days)]
    if stored.empty:
        return {"checked": 0, "bad": 0}
    key = lambda d: d["bsop_date"].astype(str) + "_" + d["bsop_time"].astype(str)  # noqa: E731
    a = made.assign(_k=key(made)).drop_duplicates("_k", keep="last").set_index("_k")
    b = stored.assign(_k=key(stored)).drop_duplicates("_k", keep="last").set_index("_k")
    both = a.index.intersection(b.index)
    if len(both) == 0:
        return {"checked": 0, "bad": 0}
    cols = [*OHLC, "vol"]
    left = a.loc[both, cols].apply(pd.to_numeric, errors="coerce")
    right = b.loc[both, cols].apply(pd.to_numeric, errors="coerce")
    return {"checked": int(len(both)), "bad": int((~(left == right).all(axis=1)).sum())}


def compare(one_min: pd.DataFrame, stored: pd.DataFrame, market: str, width: int) -> dict:
    """만든 봉과 저장본이 어디서 다른가 — 호출 0으로 센다. 양 끝 날은 뺀다(잘려 있다)."""
    days = complete_days(one_min, market)
    if not days:
        return {"checked": 0, "bad": 0}
    one_min = one_min[one_min["bsop_date"].astype(str).isin(days)]
    stored = stored[stored["bsop_date"].astype(str).isin(days)]
    made = synthesize(one_min, market, width)
    if made.empty or stored.empty:
        return {"checked": 0, "bad": 0}
    key = lambda d: d["bsop_date"].astype(str) + "_" + d["bsop_time"].astype(str)  # noqa: E731
    a = made.assign(_k=key(made)).drop_duplicates("_k", keep="last").set_index("_k")
    b = stored.assign(_k=key(stored)).drop_duplicates("_k", keep="last").set_index("_k")
    both = a.index.intersection(b.index)
    if len(both) == 0:
        return {"checked": 0, "bad": 0}
    cols = [*OHLC, "vol"]
    left = a.loc[both, cols].apply(pd.to_numeric, errors="coerce")
    right = b.loc[both, cols].apply(pd.to_numeric, errors="coerce")
    same = (left == right).all(axis=1)
    return {"checked": int(len(both)), "bad": int((~same).sum())}


def widths_from(intervals) -> list[int]:
    """`min240` 같은 이름에서 숫자만 뽑는다."""
    return [int(str(name)[3:]) for name, *_ in intervals if str(name).startswith("min")]


__all__ = [
    "ANCHOR", "AUCTION_AT", "bucket_of", "compare", "lone_auction", "synthesize", "widths_from",
]
_ = np  # numpy 는 형 변환에서만 쓴다


# ─────────────────────────────────────────────────────────────────────────────
# 한 종목·한 시장을 통째로 처리 — **프로세스로 나눠 돌리려고** 여기에 둔다
# ─────────────────────────────────────────────────────────────────────────────
# 봉을 묶는 건 계산이라 줄기(스레드)를 늘려도 안 빨라진다(GIL). 실측 2026-08-29:
#   1줄기 26.7분 · 2줄기 25.2분 · 4줄기 32.2분 · 16줄기 36.2분
# 프로세스는 각자 파이썬을 하나씩 쓰므로 코어만큼 진짜로 나뉜다. 그래서 일감을
# **`update_data` 가 아니라 이 모듈에** 둔다 — 프로세스가 새로 뜰 때마다 모듈을 다시
# 읽는데, `update_data` 는 나무 SDK·KIS 어댑터까지 딸려 와 뜨는 데만 몇 초가 걸린다.


def stored_floor(root: Path, market: str, code: str, widths) -> str:
    """1분봉을 **어느 날짜부터 읽으면 되나** — 굵기별 저장본의 마지막 날짜 중 가장 이른 것.

    굵은 봉은 "저장된 마지막 봉이 든 날부터" 다시 만든다. 그러니 그보다 앞선 1분봉은
    읽어 봐야 안 쓴다. 1분봉이 262 거래일로 깊어지면 파일이 6.7배가 되는데, 통째로 읽으면
    이 단계가 과거 깊이에 비례해 느려진다.

    저장본이 하나도 없으면 빈 글자를 돌려준다(= 통째로 읽는다).
    """
    import pyarrow.parquet as pq

    floors: list[str] = []
    for width in widths:
        path = root / market / f"min{width}" / f"{code}.parquet"
        if not path.exists():
            continue
        try:
            col = pq.read_table(path, columns=["bsop_date"])["bsop_date"].to_pylist()
        except (OSError, ValueError, KeyError):
            return ""  # 못 읽는 파일이 하나라도 있으면 안전하게 통째로
        if col:
            floors.append(max(str(x) for x in col))
    return min(floors) if floors else ""


def stamp_of(a: Path, b: Path) -> list | None:
    """두 파일의 지문(고친 시각·크기) — 둘 다 그대로면 결과도 그대로다."""
    try:
        x, y = a.stat(), b.stat()
    except OSError:
        return None
    return [x.st_mtime_ns, x.st_size, y.st_mtime_ns, y.st_size]


def graft(stored: pd.DataFrame, made: pd.DataFrame) -> pd.DataFrame | None:
    """만든 봉을 저장본 위에 덮는다. **값이 그대로면 `None`** — 그래야 파일을 안 건드린다.

    안 그러면 파일 4만여 개의 고친 시각이 매 회차 바뀌어 쪽지가 통째로 무효가 된다.
    겹치는 날만 본다 — 저장본 전체에 열쇠를 만들면 그게 제일 비싼 일이 된다.
    """
    if made.empty:
        return None
    days = set(made["bsop_date"].astype(str))
    where = stored["bsop_date"].astype(str).isin(days)
    hit = stored[where]
    cols = list(stored.columns)
    if len(hit) == len(made):
        left = made.sort_values(["bsop_date", "bsop_time"])[cols].astype(str)
        right = hit.sort_values(["bsop_date", "bsop_time"])[cols].astype(str)
        if left.to_numpy().tolist() == right.to_numpy().tolist():
            return None
    kept = stored[~where]
    out = pd.concat([kept, made], ignore_index=True)
    if kept.empty or str(made["bsop_date"].min()) > str(kept["bsop_date"].max()):
        return out.reset_index(drop=True)
    return out.sort_values(["bsop_date", "bsop_time"]).reset_index(drop=True)


_SEEN: dict = {}


def start_worker(seen: dict) -> None:
    """프로세스가 뜰 때 **한 번만** 지난 회차 지문을 받아 둔다.

    일감마다 실어 보내면 4만여 칸짜리 쪽지를 회차마다 수백 번 절이게 된다.
    """
    global _SEEN
    _SEEN = seen or {}


def build_one(job: tuple) -> dict:
    """한 종목·한 시장의 굵은 분봉을 만들고 **덮기 전에** 대조한다. 호출 0.

    돌려주는 것에 지문도 실어 보낸다 — 프로세스끼리 쪽지를 못 나눠 쓰므로,
    부모가 받아서 한 곳에 모은다.

    일감에 다섯째 자리로 `True` 를 실어 주면 **한 번 통째로 다시 만든다.** 평소에는
    저장된 마지막 봉이 든 날부터만 만드는데, 1분봉이 39일에서 262 거래일로 깊어진
    뒤에는 그러면 **깊어진 과거가 굵은 봉에 영영 안 실린다**(마지막 날짜는 그대로라
    "새로 만들 게 없다"고 본다). 창구를 바꾼 뒤 한 번만 이 길로 돈다.
    """
    root, market, code, widths = job[:4]
    full = len(job) > 4 and bool(job[4])
    root = Path(root)
    seen = _SEEN
    acc = dict.fromkeys(
        ("made", "written", "unchanged", "cached", "no_stored", "errors", "checked", "bad"), 0
    )
    found: list[tuple[str, int]] = []
    stamps: dict[str, list] = {}
    src = root / market / "min1" / f"{code}.parquet"
    one = parquet_io.read(src) if full else parquet_io.read(
        src, since=stored_floor(root, market, code, widths)
    )
    if one is None or one.empty:
        return {**acc, "worst": found, "stamps": stamps}
    days = complete_days(one, market)
    if not days:
        return {**acc, "worst": found, "stamps": stamps}
    prep: dict[str, tuple] = {}
    for width in widths:
        path = root / market / f"min{width}" / f"{code}.parquet"
        try:
            if not full:  # 통째로 다시 만들 땐 "안 바뀌었다"는 쪽지를 믿지 않는다
                now = stamp_of(src, path)
                was = seen.get(str(path))
                if now and was and was[0] == now:
                    acc["cached"] += 1
                    acc["bad"] += int(was[1])
                    continue
            stored = parquet_io.read(path)
            if stored is None or stored.empty:
                if not full:
                    acc["no_stored"] += 1  # 처음 수집은 나무 몫
                    continue
                stored = None  # 저장본이 없어도 만든 값으로 새로 놓는다
            since = min(days) if full else str(stored["bsop_date"].astype(str).max())
            if not full and not any(d >= since for d in days):
                acc["unchanged"] += 1
                continue
            if since not in prep:
                keep = one["bsop_date"].astype(str)
                prep[since] = (
                    *prepare(one[(keep >= since) & keep.isin(days)], market),
                    one.columns,
                )
            work, tail, cols = prep[since]
            made = synthesize_from(work, tail, cols, market, width)
            got = compare_from(made, stored)  # **덮기 전에** — 덮고 보면 늘 0 이다
            acc["checked"] += got["checked"]
            acc["bad"] += got["bad"]
            if got["bad"]:
                found.append((f"{market}/{code}/min{width}", got["bad"]))
            if not made.empty:
                acc["made"] += len(made)
                joined = made if stored is None else graft(stored, made)
                if joined is None:
                    acc["unchanged"] += 1
                else:
                    parquet_io.save(joined, path)
                    acc["written"] += 1
            fresh = stamp_of(src, path)
            if fresh:
                stamps[str(path)] = [fresh, int(got["bad"])]
        except (OSError, ValueError, KeyError):
            acc["errors"] += 1
    return {**acc, "worst": found, "stamps": stamps}
