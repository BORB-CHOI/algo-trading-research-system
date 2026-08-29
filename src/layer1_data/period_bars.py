"""주·월봉을 일봉으로 만들고, 저장된 주·월봉과 맞는지 대조한다 — 증권사 호출 0.

## 왜 만드나

주·월봉은 그 주(달)가 끝날 때까지 값이 매일 바뀐다. 그걸 증권사에 매일 물으면
전 종목 5,140콜·6분이 든다(실측 2026-08-28). 그런데 일봉은 어차피 매일 받으므로
**진행 중인 봉은 일봉을 묶으면 나온다.**

실측으로 확인한 것(2026-08-28):
- 최신 주봉 200종목 → 200/200 완전 일치
- 최신 월봉 200종목 → 195/200 (틀린 5개도 저가 1원·거래량 2~5주 차이)
- 2024·2025·2026년 주봉 전체 → 99.4% · 98.9% · 99.2%

## 왜 대조까지 하나

전 종목 63만 봉을 대조하는 데 **호출 0개로 2분 16초**면 된다(실측). 그래서 매 갱신마다
전수로 본다. 표본 20종목만 보던 전보다 훨씬 촘촘하다.

실제로 이 대조가 **이미 있던 오류를 찾아냈다** — 2026-08-28 기준 1,339종목(31.1%)의
주·월봉이 옛 수정주가 그대로 남아 있었다. 한화(000880) 8월 첫째 주가 우리 파일엔
83,800 인데 나무·KIS 에 다시 물으니 100,600 이었다. 일봉은 멀쩡했다(30/30 일치).

⚠️ **대조가 알아내는 건 "둘이 다르다"까지다. "어느 쪽이 맞다"는 못 정한다.**
어긋난 종목은 증권사에 다시 물어서 고쳐야 한다.

## 화면 쪽 합성과의 차이

`api/candles.py:resample_candles` 도 일봉으로 주·월봉을 만든다. 거기는 **화면에 그릴
정규화된 표**(Date/Open/High/…/Marcap)를 다루고, 여기는 **나무 수집본 파일 형식**
(bsop_date/stck_oprc/… 전부 문자열)을 다룬다. 묶는 규칙(첫 시가·최고 고가·최저 저가·
마지막 종가·합산 거래량/대금)은 같다.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import parquet_io

# 나무 수집본 파일의 열 차례 — 새로 만든 봉도 이 모양이어야 이어붙일 수 있다.
COLUMNS = [
    "bsop_date",
    "bsop_time",
    "stck_sdpr",
    "stck_oprc",
    "stck_hgpr",
    "stck_lwpr",
    "stck_prpr",
    "vol",
    "tr_pbmn",
    "flng_cls_code",
    "prtt_rate",
    "news_cnt",
    "updownmark",
    "fcam_mod_cls_code",
]

# 주·월봉에서 **정말로** 비어 있는 열 — 나무 수집본도 빈 문자열이다.
#
# ⚠️ `stck_sdpr`(기준가)은 나무가 값을 주는데도 여기 남겨 둔다. 나무의 **일봉 기준가
# 자체가 깨져 있어서** 옮기면 깨진 값을 퍼뜨리게 된다. 실측 2026-08-29 (일봉 515,640줄):
#   기준가가 0                          20.4%
#   0 아닌 것 중 전일 종가와 같음         74.3%   ← 나머지는 안 맞는다
#   0 아닌데 종가와 규모가 다름(수정주가) 11.2%   ← 42,214 옆에 종가 10,791 같은 줄
# 주봉 쪽도 어긋난 것의 95%가 "나무가 0을 준" 경우였다. 재현할 규칙이 없다.
BLANK_COLUMNS = [
    "bsop_time",
    "updownmark",
]

# 값을 견줄 때 쓰는 열. 여기가 다르면 봉이 다른 것이다.
VALUE_COLUMNS = ["stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr", "vol", "tr_pbmn"]

# 일봉에서 **그대로 만들어 낼 수 있는** 열. 규칙은 나무 수집본과 맞춰 찾았다 —
# 실측 2026-08-29, 120종목 · 주봉 85,792봉 · 월봉 20,208봉 (연말 걸친 주·양 끝 제외):
#
# | 열 | 규칙 | 주봉 | 월봉 | 진짜 불일치 |
# |---|---|---|---|---|
# | flng_cls_code (락 구분)     | 그 기간 최대값 | 91.25% | 77.78% | **0건** |
# | prtt_rate (락 비율)         | 그 기간 최대값 | 99.97% | 97.47% | **0건** |
# | fcam_mod_cls_code (액면변경)| 그 기간 최대값 | 100.0% | 97.57% | **0건** |
# | news_cnt (뉴스 건수)        | 그 기간 합     | 98.94% | 100.0% | 주봉 913건(1.06%) |
#
# 일치율이 100% 가 아닌 건 **전부 "나무가 0 을 준" 경우**다(락 구분 7,509건 등).
# 우리 값은 일봉에 실제로 있는 값이라 나무의 0 보다 맞다. 뉴스 건수만 주봉에서
# 1.06% 가 진짜로 다른데, **월봉에서는 합이 정확히 맞아** 규칙은 합이 맞다고 본다
# (나무 주봉 쪽 잡음). 예: 028300 2022-05-30 주 — 일봉 6+16+7+4=33 인데 나무는 13.
#
# ⚠️ 이 열들을 비워 두면 **다시는 안 채워진다**(①-1b 는 저장본 없는 종목만 받는다).
# 실측: 주봉 0.15%·월봉 1.07% 가 이미 비어 있었고, 월봉은 한 종목에서 31줄까지 갔다.
# `stck_sdpr`(기준가)도 만든다 — **그 기간 첫 일봉의 기준가**.
# 처음엔 "나무 데이터가 깨졌다"고 보고 비우려 했는데, 오너 지적으로 다시 재니 틀렸다
# (실측 2026-08-29):
#   · 기준가가 0 인 건 **옛 구간 결측**이다 — 1990년대 93.8% · 2000년대 53.7% ·
#     2010년대 0.0% · 2020년대 0.3%. 깨진 게 아니라 나무가 옛날 걸 안 준 것이다.
#   · 갭과는 무관하다. 기준가는 정의상 전일 종가라, 갭은 기준가와 **시가** 사이에 생긴다.
#   · 나무가 값을 준 2015년 이후 주봉 40,635봉에 이 규칙을 대면 **97.24%**,
#     그리고 **145종목 중 143종목이 정확히 100%** 다.
#   · 안 맞는 2종목(096690·088800)은 **일봉 안에서** 기준가와 OHLC 의 척도가 어긋나 있다
#     (기준가 13,481 옆에 종가 12,060). 그건 우리가 만드는 문제가 아니다.
# 주봉엔 나무가 최근에도 34.6% 를 0 으로 준다 — 그 자리에 우리 값이 들어가면 더 낫다.
EXTRA_RULES = {
    "stck_sdpr": "first",
    "flng_cls_code": "max",
    "prtt_rate": "max",
    "fcam_mod_cls_code": "max",
    "news_cnt": "sum",
}


def period_key(dates: pd.Series, folder: str) -> pd.Series:
    """그 날짜가 어느 주(달)에 드는지 — 주는 그 주 월요일, 달은 YYYYMM 으로 묶는다."""
    text = dates.astype(str)
    if folder == "month":
        return text.str[:6]
    ts = pd.to_datetime(text, format="%Y%m%d")
    return (ts - pd.to_timedelta(ts.dt.weekday, unit="D")).dt.strftime("%Y%m%d")


def _aggregate(day: pd.DataFrame, folder: str, since_key: str = "") -> pd.DataFrame:
    """일봉을 주(달)별로 묶은 **숫자 표**. 만들기와 대조가 같이 쓰는 알맹이다.

    돌려주는 표는 묶음 열쇠(주=그 주 월요일, 달=YYYYMM)로 색인하고, 값은 숫자 그대로다.
    글자로 바꾸는 건 파일에 넣을 때만 필요하다 — 대조는 숫자로 견주면 된다.
    (실측 2026-08-28: 대조 시간의 절반이 이 글자 변환에 들어가고 있었다.)
    """
    if day.empty or folder not in ("week", "month"):
        return pd.DataFrame()

    # 일봉에 딸려 온 열이 있으면 그것도 같이 묶는다. 없으면 없는 대로 간다 —
    # 부르는 쪽이 열을 몇 개 읽어 왔는지에 따라 알아서 맞춘다.
    extras = [c for c in EXTRA_RULES if c in day.columns]
    work = day[["bsop_date", *VALUE_COLUMNS, *extras]]
    # 필요한 구간만 잘라 낸다. 일봉은 날짜순으로 저장돼 있으므로 **시작 자리만 찾으면**
    # 된다 — 9,000줄에 조건을 걸어 참/거짓 표를 만들 필요가 없다.
    # 날짜가 `YYYYMMDD` 라 글자 순서 = 날짜 순서다. 월봉 시작키(`YYYYMM`)는 그 달 1일로 편다.
    dates = work["bsop_date"].to_numpy(dtype="U8")
    if since_key:
        start = int(dates.searchsorted(since_key + "01" if len(since_key) == 6 else since_key))
        if start >= len(dates):
            return pd.DataFrame()
        work = work.iloc[start:]
        dates = dates[start:]
    if work.empty:
        return pd.DataFrame()

    if folder == "month":
        keys = np.char.ljust(dates.astype("U6"), 6)
    else:
        ts = pd.to_datetime(dates, format="%Y%m%d")
        keys = (ts - pd.to_timedelta(ts.weekday, unit="D")).strftime("%Y%m%d").to_numpy()

    num = {c: pd.to_numeric(work[c], errors="coerce").to_numpy(float) for c in VALUE_COLUMNS}
    frame = pd.DataFrame(num)
    frame["last_day"] = dates
    spec = {
        "stck_oprc": ("stck_oprc", "first"),
        "stck_hgpr": ("stck_hgpr", "max"),
        "stck_lwpr": ("stck_lwpr", "min"),
        "stck_prpr": ("stck_prpr", "last"),
        "vol": ("vol", "sum"),
        "tr_pbmn": ("tr_pbmn", "sum"),
        "last_day": ("last_day", "last"),
    }
    for col in extras:
        if EXTRA_RULES[col] == "sum":  # 뉴스 건수 — 숫자로 더한다
            frame[col] = pd.to_numeric(work[col], errors="coerce").fillna(0).to_numpy(float)
        else:  # 락 구분·락 비율·액면변경 — 글자 그대로 가장 큰 값
            frame[col] = work[col].astype(str).to_numpy()
        spec[col] = (col, EXTRA_RULES[col])
    return frame.groupby(keys, sort=True).agg(**spec)


def _finish_extras(out: pd.DataFrame) -> None:
    """파일에 넣기 직전 마무리 — 진짜 빈 열은 비우고, 만들어 낸 열은 글자로 바꾼다."""
    for col in BLANK_COLUMNS:
        out[col] = ""
    for col, how in EXTRA_RULES.items():
        if col not in out.columns:
            out[col] = ""  # 일봉에서 그 열을 안 읽어 왔다 — 빈 칸으로 둔다
        elif how == "sum":
            out[col] = out[col].fillna(0).round().astype("int64").astype(str)


def synthesize(day: pd.DataFrame, folder: str, since_key: str = "") -> pd.DataFrame:
    """일봉을 묶어 주봉·월봉을 만든다. `since_key` 부터(포함) 만든다.

    봉 날짜는 나무와 같게 맞춘다 — 주봉은 **그 주의 마지막 거래일**, 월봉은 `YYYYMM`.
    """
    got = _aggregate(day, folder, since_key)
    if got.empty:
        return pd.DataFrame(columns=COLUMNS)
    out = got.reset_index(names="_key")
    out["bsop_date"] = out["_key"] if folder == "month" else out["last_day"]
    for col in VALUE_COLUMNS:
        # 나무는 정수 문자열로 준다. 거래대금이 커서 float 로 두면 지수 표기가 섞인다.
        out[col] = out[col].round().astype("int64").astype(str)
    _finish_extras(out)
    # `astype(str)` 이어야 나무 수집본과 같은 dtype 이 된다. `astype("string")` 은
    # 빈칸 표시가 <NA> 로 달라져 저장본과 섞이면 비교가 어긋난다(실측 2026-08-28).
    return out[COLUMNS].astype(str).reset_index(drop=True)


def graft(stored: pd.DataFrame | None, made: pd.DataFrame) -> pd.DataFrame:
    """만든 봉을 저장본에 얹는다 — 같은 날짜는 새 값으로 덮고, 과거는 그대로 둔다."""
    if made.empty:
        return stored if stored is not None else pd.DataFrame(columns=COLUMNS)
    if stored is None or stored.empty:
        return made
    keep = stored[~stored["bsop_date"].astype(str).isin(set(made["bsop_date"].astype(str)))]
    joined = pd.concat([keep, made], ignore_index=True)
    return joined.sort_values("bsop_date").reset_index(drop=True)


PRICE_COLUMNS = ["stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr"]

# 거래량 몇 주까지 같은 값으로 볼까 — **오너 결정 2026-08-28: 2주.**
# 나무는 자기 주봉과 자기 일봉을 1~2주 다르게 준다. 실측(2026-08-28, 63만 봉):
# 어긋난 6,943봉 가운데 **4,650봉(67.0%)이 거래량 2주 이하 차이**였고 중앙값이 1주다.
# 이걸 안 봐주면 그런 종목을 매 회차 영원히 다시 받게 된다.
VOLUME_TOLERANCE = 2.0
PRICE_TOLERANCE = 0.5  # 가격은 엄격하게 — 1원만 달라도 다른 값이다


def straddles_year(keys: pd.Index, dates: pd.Series) -> pd.Series:
    """그 주가 해를 걸치나 — 주 시작(월요일)과 그 주 마지막 거래일의 해가 다르면 걸친 것."""
    return pd.Series(keys.str[:4].to_numpy() != dates.astype(str).str[:4].to_numpy(), index=keys)


def disagreements(day: pd.DataFrame, stored: pd.DataFrame, folder: str, since: str = ""):
    """저장된 주·월봉과 일봉으로 만든 것이 **어느 봉에서** 다른가 — 호출 없이 찾는다.

    돌려주는 것: (어긋난 묶음 열쇠들, 일봉으로 묶은 숫자 표). 열쇠로 그 표를 찾으면
    "맞는 값"이 나온다 — 고칠 때 그대로 쓴다.

    **양 끝은 뺀다.** 첫 봉은 일봉이 그 주(달) 도중부터 시작해 잘려 있고, 마지막 봉은
    아직 진행 중이라 다른 게 당연하다. 가운데만 봐야 진짜 어긋남이 보인다.

    **연말·연초에 걸친 주도 뺀다.** 나무는 그 주를 12월분·1월분으로 쪼개고, KIS 와 우리
    화면 코드(`api/candles.py`)는 안 쪼갠다 — 틀린 게 아니라 규칙이 다른 것이다.
    실측 2026-08-28: 어긋난 6,943봉 중 2,039봉(29.4%)이 이것이었다.

    **거래량은 2주까지 봐준다**(`VOLUME_TOLERANCE`, 오너 결정). 거래대금도 그만큼
    — 2주치 값어치까지 — 봐준다. 가격은 1원도 안 봐준다.

    이 셋을 빼고 남는 게 진짜 오류다. 실측 2026-08-28 — 전 종목 4,310개·63만 봉:
    여유 없이 6,943봉(1.09%) → 세 가지를 빼면 **274봉(0.043%)**.
    그중 60%가 한 종목(335870)이다 — 나무 주봉이 액면분할을 반영 안 해 5배로 어긋난다.
    """
    empty = pd.Index([]), pd.DataFrame()
    if day.empty or stored is None or stored.empty:
        return empty
    left = _aggregate(day, folder, since[:6] if folder == "month" else since)
    if left.empty:
        return empty

    kept = stored["bsop_date"].astype(str)
    right = stored[kept >= (since[:6] if folder == "month" else since)]
    if right.empty:
        return empty
    right = right.set_index(period_key(right["bsop_date"], folder))
    right = right[~right.index.duplicated(keep="last")]

    both = left.index.intersection(right.index).sort_values()[1:-1]
    if folder == "week" and len(both) > 0:
        # 주 시작(월요일)과 그 주 마지막 거래일의 해가 다르면 연말·연초에 걸친 주다.
        keys = both.to_numpy().astype("U8")
        ends = left.loc[both, "last_day"].to_numpy().astype("U8")
        both = both[keys.astype("U4") == ends.astype("U4")]
    if len(both) == 0:
        return empty

    a = left.loc[both, VALUE_COLUMNS].to_numpy(float)
    b = right.loc[both, VALUE_COLUMNS].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    gap = np.abs(a - b)
    idx = {c: i for i, c in enumerate(VALUE_COLUMNS)}

    off = (gap[:, [idx[c] for c in PRICE_COLUMNS]] > PRICE_TOLERANCE).any(axis=1)
    off |= gap[:, idx["vol"]] > VOLUME_TOLERANCE
    # 거래대금은 "몇 주가 빠졌나"로 환산해 본다 — 한 주 값어치 = 거래대금 ÷ 거래량.
    vol_b = b[:, idx["vol"]]
    per_share = np.divide(
        b[:, idx["tr_pbmn"]], vol_b, out=np.zeros_like(vol_b), where=vol_b > 0
    )
    off |= gap[:, idx["tr_pbmn"]] > np.maximum(per_share * VOLUME_TOLERANCE, PRICE_TOLERANCE)
    return both[off], left


def rows_for(keys, made: pd.DataFrame, folder: str) -> pd.DataFrame:
    """묶음 열쇠들에 해당하는 봉을 **파일에 넣을 모양**으로 만든다(고칠 때 쓴다)."""
    if len(keys) == 0 or made.empty:
        return pd.DataFrame(columns=COLUMNS)
    out = made.loc[keys].reset_index(names="_key")
    out["bsop_date"] = out["_key"] if folder == "month" else out["last_day"]
    for col in VALUE_COLUMNS:
        out[col] = out[col].round().astype("int64").astype(str)
    _finish_extras(out)
    return out[COLUMNS].astype(str).reset_index(drop=True)


def mismatches(day: pd.DataFrame, stored: pd.DataFrame, folder: str, since: str = "") -> int:
    """어긋난 봉이 몇 개인가 — `disagreements` 의 개수만 센다."""
    bad, _ = disagreements(day, stored, folder, since)
    return len(bad)


# ─────────────────────────────────────────────────────────────────────────────
# 한 종목을 통째로 처리 — **프로세스로 나눠 돌리려고** 여기에 둔다
# ─────────────────────────────────────────────────────────────────────────────
# 봉을 묶는 건 계산이라 줄기(스레드)를 늘려도 안 빨라진다(GIL). 실측 2026-08-28:
#   1줄기 241.0초 · 4줄기 330.0 · 8줄기 333.7 · 16줄기 340.3   ← 늘수록 손해
# 프로세스는 각자 파이썬을 하나씩 쓰므로 코어만큼 진짜로 나뉜다(굵은 분봉에서 3배 확인).
# 일감을 **`update_data` 가 아니라 이 모듈에** 두는 이유: 프로세스가 새로 뜰 때마다 모듈을
# 다시 읽는데, `update_data` 는 나무 SDK·KIS 어댑터까지 딸려 와 뜨는 데만 몇 초가 걸린다.

_SEEN: dict = {}
_SINCE = "20240101"


def start_worker(seen: dict, verify_since: str) -> None:
    """프로세스가 뜰 때 **한 번만** 지난 회차 지문과 대조 시작점을 받아 둔다."""
    global _SEEN, _SINCE
    _SEEN = seen or {}
    _SINCE = verify_since


def stamp_of(a: Path, b: Path) -> list | None:
    """두 파일의 지문(고친 시각·크기) — 둘 다 그대로면 결과도 그대로다."""
    try:
        x, y = a.stat(), b.stat()
    except OSError:
        return None
    return [x.st_mtime_ns, x.st_size, y.st_mtime_ns, y.st_size]


def day_of(stored_date: str, folder: str) -> str:
    """저장된 마지막 봉 날짜 → 그 봉이 든 기간의 **첫날**."""
    if folder == "month":
        return f"{stored_date[:6]}01"
    d = date(int(stored_date[:4]), int(stored_date[4:6]), int(stored_date[6:8]))
    return (d - timedelta(days=d.weekday())).strftime("%Y%m%d")


def already_there(stored: pd.DataFrame, made: pd.DataFrame) -> bool:
    """만든 봉이 저장본에 이미 같은 값으로 들어 있나 — **바뀐 줄만 본다.**"""
    if made.empty:
        return True
    dates = set(made["bsop_date"].astype(str))
    hit = stored[stored["bsop_date"].astype(str).isin(dates)]
    if len(hit) != len(made):
        return False
    left = made.set_index("bsop_date").sort_index().astype(str)
    right = hit.set_index("bsop_date").sort_index()[left.columns].astype(str)
    return left.equals(right)


def build_one(job: tuple) -> dict:
    """한 종목의 주·월봉을 일봉으로 채우고 **그 자리에서 전수 대조까지** 한다. 호출 0.

    저장된 마지막 봉이 든 주(달)부터 다시 만든다 — 그 봉은 받을 당시 진행 중이었을 수
    있어서다. **그보다 과거는 어긋났을 때만 건드린다.**
    """
    root, code, markets = job
    root = Path(root)
    keys = (
        "made", "written", "unchanged", "cached", "fixed", "no_stored", "errors",
        "checked_bars", "mismatch_units", "mismatch_bars",
    )
    acc = dict.fromkeys(keys, 0)
    done: list[tuple[str, str, str]] = []
    found: list[tuple[str, int]] = []
    stamps: dict[str, list] = {}
    cols = ["bsop_date", *VALUE_COLUMNS, *EXTRA_RULES]
    for market in markets:
        day_path = root / market / "day" / f"{code}.parquet"
        if not day_path.exists():
            continue
        day = None
        for folder in ("week", "month"):
            path = root / market / folder / f"{code}.parquet"
            now = stamp_of(day_path, path)
            was = _SEEN.get(str(path))
            if now and was and was[0] == now:
                # 두 파일 다 지난번 그대로다 — 만들 값도 대조 결과도 그대로다.
                acc["cached"] += 1
                if was[1]:
                    acc["mismatch_units"] += 1
                    acc["mismatch_bars"] += int(was[1])
                done.append((market, code, folder))  # 안 그러면 ①-1b 가 헛돈다
                continue
            try:
                stored = parquet_io.read(path)
                if stored is None or stored.empty:
                    acc["no_stored"] += 1  # 처음 수집은 나무 몫
                    continue
                since = str(stored["bsop_date"].astype(str).max())
                if day is None:
                    day = pd.read_parquet(day_path, columns=cols)
                key = period_key(pd.Series([day_of(since, folder)]), folder).iloc[0]
                fresh = synthesize(day, folder, since_key=key)
                if not fresh.empty:
                    acc["made"] += len(fresh)
                    done.append((market, code, folder))
                if already_there(stored, fresh):
                    acc["unchanged"] += 1
                    joined = stored
                else:
                    joined = graft(stored, fresh)
                    parquet_io.save(joined, path)
                    acc["written"] += 1
                bad, made_all = disagreements(day, joined[cols], folder, _SINCE)
                acc["checked_bars"] += len(joined)
                if len(bad):
                    acc["mismatch_units"] += 1
                    acc["mismatch_bars"] += len(bad)
                    found.append((f"{market}/{code}/{folder}", len(bad)))
                    joined = graft(joined, rows_for(bad, made_all, folder))
                    parquet_io.save(joined, path)
                    acc["fixed"] += len(bad)
                fresh_stamp = stamp_of(day_path, path)
                if fresh_stamp:
                    stamps[str(path)] = [fresh_stamp, 0]
            except (OSError, ValueError, KeyError):
                acc["errors"] += 1
    return {**acc, "done": done, "worst": found, "stamps": stamps}
