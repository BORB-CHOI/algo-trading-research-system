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

import numpy as np
import pandas as pd

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

# 주·월봉에는 값이 안 오는 열들 — 나무가 빈 문자열로 준다. 그대로 맞춘다.
BLANK_COLUMNS = [
    "bsop_time",
    "stck_sdpr",
    "flng_cls_code",
    "prtt_rate",
    "news_cnt",
    "updownmark",
    "fcam_mod_cls_code",
]

# 값을 견줄 때 쓰는 열. 나머지는 빈 칸이라 견줄 게 없다.
VALUE_COLUMNS = ["stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr", "vol", "tr_pbmn"]


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

    work = day[["bsop_date", *VALUE_COLUMNS]]
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
    grouped = frame.groupby(keys, sort=True)
    return grouped.agg(
        stck_oprc=("stck_oprc", "first"),
        stck_hgpr=("stck_hgpr", "max"),
        stck_lwpr=("stck_lwpr", "min"),
        stck_prpr=("stck_prpr", "last"),
        vol=("vol", "sum"),
        tr_pbmn=("tr_pbmn", "sum"),
        last_day=("last_day", "last"),
    )


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
    for col in BLANK_COLUMNS:
        out[col] = ""
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
    for col in BLANK_COLUMNS:
        out[col] = ""
    return out[COLUMNS].astype(str).reset_index(drop=True)


def mismatches(day: pd.DataFrame, stored: pd.DataFrame, folder: str, since: str = "") -> int:
    """어긋난 봉이 몇 개인가 — `disagreements` 의 개수만 센다."""
    bad, _ = disagreements(day, stored, folder, since)
    return len(bad)
