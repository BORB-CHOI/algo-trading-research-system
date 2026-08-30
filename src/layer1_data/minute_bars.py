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

# 정규장이 끝나는 시각 — **장 시작부터 몇 분인지**로 적는다(`ANCHOR` 를 뺀 값).
# 자정 기준으로 적으면 `bucket_of` 의 비교가 통째로 안 걸린다(실측 2026-08-29에 낸 실수).
#   krx  09:00~15:20 = 380분  (종가는 15:30 에 따로 찍힌다)
#   통합·NXT 08:00~20:00 = 720분
SESSION_END = {"krx": 15 * 60 + 20 - 9 * 60, "unt": 20 * 60 - 8 * 60, "nxt": 20 * 60 - 8 * 60}

CLOSING_MARK = "999900"  # 장 마감 뒤 집계 봉 — 굵은 봉에도 그대로 하나 실린다
OHLC = ["stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr"]
SUMS = ["vol", "tr_pbmn"]


def minutes_of(times: pd.Series, market: str) -> pd.Series:
    """`HHMMSS` 를 장 시작부터 몇 분인지로 바꾼다."""
    t = times.astype(str).str.zfill(6)
    return t.str[:2].astype(int) * 60 + t.str[2:4].astype(int) - ANCHOR[market]


def bucket_of(mins: pd.Series, market: str, width: int) -> pd.Series:
    """그 분이 몇 번째 봉에 드나 — 봉 이름이 **끝 시각**이라 1을 빼고 나눈다.

    정규장이 끝난 뒤(15:20 초과)는 전부 마지막 격자 봉에 담는다(위 규칙 3).
    """
    idx = (mins - 1) // width
    end = SESSION_END[market]
    if end % width == 0:
        # 장 마감이 격자에 **딱 떨어진다** — 마지막 정규 봉이 온전하므로 종가 봉은 따로 선다.
        # 예) krx min5 는 15:20 봉이 15:16~15:20 로 꽉 차 있고, 15:30 종가 봉이 하나 더 온다.
        return idx
    # 딱 안 떨어진다 — 마지막 토막(예: krx min3 의 15:19~15:20)이 종가 봉과 한 봉이 된다.
    # 실측 005930 2026-08-28: 나무 min3 15:30 봉 = 151900+152000+153000 거래량 합.
    last = (end - 1) // width
    return idx.where(idx <= last, last)


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
    bucket = bucket_of(work["_m"], market, width)
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


__all__ = ["ANCHOR", "SESSION_END", "synthesize", "compare", "bucket_of", "widths_from"]
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
    """
    root, market, code, widths = job
    root = Path(root)
    seen = _SEEN
    acc = dict.fromkeys(
        ("made", "written", "unchanged", "cached", "no_stored", "errors", "checked", "bad"), 0
    )
    found: list[tuple[str, int]] = []
    stamps: dict[str, list] = {}
    src = root / market / "min1" / f"{code}.parquet"
    one = parquet_io.read(src, since=stored_floor(root, market, code, widths))
    if one is None or one.empty:
        return {**acc, "worst": found, "stamps": stamps}
    days = complete_days(one, market)
    if not days:
        return {**acc, "worst": found, "stamps": stamps}
    prep: dict[str, tuple] = {}
    for width in widths:
        path = root / market / f"min{width}" / f"{code}.parquet"
        try:
            now = stamp_of(src, path)
            was = seen.get(str(path))
            if now and was and was[0] == now:
                acc["cached"] += 1
                acc["bad"] += int(was[1])
                continue
            stored = parquet_io.read(path)
            if stored is None or stored.empty:
                acc["no_stored"] += 1  # 처음 수집은 나무 몫
                continue
            since = str(stored["bsop_date"].astype(str).max())
            if not any(d >= since for d in days):
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
                joined = graft(stored, made)
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
