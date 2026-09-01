"""데이터가 어디까지 들어와 있나 — 워터마크(high watermark) 방식.

## 왜 이렇게 하나

증분 갱신의 업계 표준은 **"마지막으로 어디까지 받았나"를 작은 상태 파일 하나에 적어 두고,
다음 실행 때 그 뒤부터만 받는 것**이다. 이미 처리한 데이터는 두 번 건드리지 않는다.

우리 코드는 그걸 안 하고 있었다. `update_data.py` 는 마지막 날짜를 알아내려고
**데이터 파일을 전부 열어 봤다**. 실측(2026-08-16):

| 방식 | 파일 1개 | 일·주·월봉 증분(16,530개) |
| 파일 통째로 열기 | 15.3ms | **4.2분** (API 호출 0건이어도) |
| 날짜 열만 읽기   |  1.6ms | 0.5분 |
| 워터마크 파일 1개 |   —    | **1밀리초** |

## 화면에는 며칠이 아니라 등급을 준다

묵은 데이터는 **화면이 멀쩡히 그려진다.** 값이 비어 있지도, 오류가 나지도 않는다.
그래서 "8월 3일"이라는 날짜만 띄우면 오너가 그냥 지나친다. 괜찮음·주의·묵음 세 등급으로
색을 줘야 눈에 걸린다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_ROOT = Path("data/derived")
MARK_FILE = "_freshness.json"

# 며칠 밀리면 어떤 등급인가. **달력 날이 아니라 장이 열린 날로 센다.**
# 달력으로 세면 월요일 아침마다 "금요일 데이터 = 사흘 전"이 되어 멀쩡한데도 경고가 뜬다.
# 경고가 늘 떠 있으면 아무도 안 본다.
WARN_AFTER_DAYS = 2  # 이틀치 빠짐 = 주의
STALE_AFTER_DAYS = 4  # 나흘치 빠짐 = 묵음


def _norm(day: Any) -> str:
    """'20260803' · Timestamp · '2026-08-03' 을 모두 'YYYY-MM-DD' 한 모양으로."""
    if day is None:
        return ""
    if isinstance(day, str) and len(day) == 8 and day.isdigit():
        return f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return str(pd.Timestamp(day).date())


def read_marks(*, root: Path = DEFAULT_ROOT) -> dict[str, dict]:
    """워터마크 전부. 파일이 없거나 깨졌으면 빈 것으로 본다.

    상태 파일 하나 때문에 화면이 죽으면 안 된다 — 없으면 "받은 적 없음"으로 흘려보낸다.
    """
    path = Path(root) / MARK_FILE
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def write_mark(
    source: str, last_date: Any, *, n_symbols: int | None = None, root: Path = DEFAULT_ROOT
) -> None:
    """한 소스의 워터마크를 갱신한다. 다른 소스는 그대로 둔다."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    marks = read_marks(root=root)
    entry: dict[str, Any] = {
        "last_date": _norm(last_date),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    if n_symbols is not None:
        entry["n_symbols"] = int(n_symbols)
    (root / MARK_FILE).write_text(
        json.dumps({**marks, source: entry}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def days_behind(last_date: Any, *, today: pd.Timestamp | None = None) -> int | None:
    """**장이 열린 날로** 며칠치가 빠졌나. 0 = 최신. 받은 적 없으면 None.

    토·일은 빼고 센다. 한국 공휴일까지는 안 본다 — 공휴일이 낀 주엔 하루 덜 밀린 것으로
    나오지만, 등급이 한 칸 늦게 뜨는 쪽이라 잘못된 경고를 만들지 않는다.
    """
    day = _norm(last_date)
    if not day:
        return None
    import numpy as np

    now = pd.Timestamp(today) if today is not None else pd.Timestamp.today()
    elapsed = int(np.busday_count(day, str(now.normalize().date())))
    return max(elapsed - 1, 0)


def grade(last_date: Any, *, today: pd.Timestamp | None = None) -> str:
    """'ok' · 'warn' · 'stale'. 받은 적이 없으면 'stale'."""
    behind = days_behind(last_date, today=today)
    if behind is None:
        return "stale"
    if behind >= STALE_AFTER_DAYS:
        return "stale"
    if behind >= WARN_AFTER_DAYS:
        return "warn"
    return "ok"


# ── 실제 데이터에서 워터마크 만들기 ──────────────────────────
#
# 이건 **느리다**(폴더당 수십 초). 화면 요청에서 부르지 않는다 — 워터마크가 없거나
# 하루 지났을 때 뒤에서 한 번만 돌린다. 평소 화면은 `report()` 로 워터마크만 읽는다.


def count_files(dirpath: Path) -> int:
    """훑기 전에 몇 개인지 — 게이지의 분모다. 도중에 늘면 되감기처럼 보인다."""
    d = Path(dirpath)
    return sum(1 for _ in d.glob("*.parquet")) if d.is_dir() else 0


def scan_last_date(
    dirpath: Path, date_col: str, *, on_file: Callable[[], None] | None = None
) -> tuple[str | None, int]:
    """폴더 안 parquet 들에서 가장 늦은 날짜.

    날짜는 공용 쪽지(`last_dates`)에 물어본다 — **파일이 지난번과 똑같으면 열지 않는다.**
    전엔 여기서만 16,576개를 매번 다시 열어 약 27초를 썼다(실측 2026-08-17).
    갱신 쪽도 같은 쪽지를 보므로, 한 회차에 같은 파일을 두 번 여는 일이 없어진다.

    파일 하나가 깨져도 나머지로 답한다 — 하나 때문에 "모른다"가 되면 안 된다.
    `on_file` 은 파일 하나 볼 때마다 불린다(화면 게이지용, 깨진 파일도 센다).
    """
    from . import last_dates

    dirpath = Path(dirpath)
    if not dirpath.is_dir():
        return None, 0
    best = ""
    n = 0
    for path in dirpath.glob("*.parquet"):
        if on_file is not None:
            on_file()
        raw = last_dates.of(path, date_col)
        if not raw:
            continue
        n += 1
        day = _norm(raw)
        if day > best:
            best = day
    return (best or None), n


# 화면에 뭐라고 띄울지. key 는 워터마크 파일의 키와 같다.
SOURCES: list[dict[str, Any]] = [
    {
        "key": "marcap",
        "label": "차트 일봉",
        "why": "차트에 그려지는 일봉·시가총액입니다. 이게 묵으면 차트 오른쪽 끝이 과거입니다.",
        "dir": None,  # 연도별 한 파일 — 아래 refresh_marks 가 따로 다룬다
        "date_col": "Date",
    },
    {
        "key": "namuh_day",
        "label": "통합·NXT 거래량",
        "why": "KRX 말고 NXT까지 합친 거래량입니다. 없으면 KRX 체결만 보게 됩니다.",
        "dir": "namuh_bars/krx/day",
        "date_col": "bsop_date",
    },
    {
        "key": "supply",
        "label": "수급(외인·기관·개인)",
        "why": "수급 조건을 걸고 과거를 검사할 때 쓰입니다.",
        "dir": "supply",
        "date_col": "stck_bsop_date",
    },
    {
        "key": "credit",
        "label": "신용잔고",
        "why": "빚내서 산 물량입니다.",
        "dir": "credit",
        "date_col": "deal_date",
    },
    {
        "key": "market_funds",
        "label": "시장 전체 예탁금·신용융자",
        "why": "고객예탁금·파생상품 예수금·미수금·시장 전체 신용융자 흐름입니다.",
        "dir": "market_funds",
        "date_col": "date",
    },
    {
        "key": "market_vi",
        "label": "VI 발동 내역",
        "why": "분봉 체결이 멈춘 때가 변동성완화장치(VI) 때문인지 확인할 때 씁니다.",
        "dir": "market_state/vi",
        "date_col": "bsop_date",
    },
    {
        "key": "members_daily",
        "label": "거래원(증권사별 매매)",
        "why": "어느 증권사 창구로 사고팔았나. KIS 가 260거래일치만 줘서 안 모으면 사라집니다.",
        "dir": "members/daily",
        "date_col": "date",
    },
    {
        "key": "disclosures",
        "label": "공시(DART)",
        "why": "종목 화면에 뜨는 공시 목록입니다.",
        "dir": "disclosures",
        "date_col": "rcept_dt",
    },
]


def report(*, root: Path = DEFAULT_ROOT, today: pd.Timestamp | None = None) -> list[dict]:
    """화면이 그대로 띄울 줄들. **워터마크만 읽는다** — 데이터 파일은 안 연다."""
    marks = read_marks(root=root)
    rows = []
    for src in SOURCES:
        mark = marks.get(src["key"], {})
        last = mark.get("last_date") or None
        rows.append(
            {
                "key": src["key"],
                "label": src["label"],
                "why": src["why"],
                "last_date": last,
                "days_behind": days_behind(last, today=today),
                "grade": grade(last, today=today),
                "n_symbols": mark.get("n_symbols"),
                "checked_at": mark.get("checked_at"),
            }
        )
    return rows


_GRADE_ORDER = {"ok": 0, "warn": 1, "stale": 2}


def worst_grade(*, root: Path = DEFAULT_ROOT, today: pd.Timestamp | None = None) -> str:
    """소스 중 가장 나쁜 등급 — 화면 상단 배지 색에 쓴다."""
    grades: list[str] = [str(r["grade"]) for r in report(root=root, today=today)]
    if not grades:
        return "stale"
    return max(grades, key=lambda g: _GRADE_ORDER[g])


DEFAULT_MARCAP_DIR = Path("data/marcap/data")


def needs_rescan(*, root: Path = DEFAULT_ROOT, today: pd.Timestamp | None = None) -> bool:
    """오늘 이미 훑었나. 훑기는 수십 초짜리라 하루 한 번이면 충분하다."""
    marks = read_marks(root=root)
    if not marks:
        return True
    now = pd.Timestamp(today) if today is not None else pd.Timestamp.today()
    checked: list[str] = [str(m["checked_at"]) for m in marks.values() if m.get("checked_at")]
    if not checked:
        return True
    return pd.Timestamp(max(checked)).normalize() < now.normalize()


def refresh_marks(
    *,
    root: Path = DEFAULT_ROOT,
    marcap_dir: Path = DEFAULT_MARCAP_DIR,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, str]:
    """데이터를 실제로 훑어 워터마크를 다시 적는다. **느리다** — 뒤에서 돌려라.

    실측 2026-08-17: 파일 16,576개, 약 27초. 그래서 `on_progress(소스이름, 한 것, 전체)` 로
    어디까지 왔는지 알린다 — 화면이 그대로 게이지에 쓴다.

    전체 개수는 **훑기 전에 한 번** 세서 고정한다. 도중에 늘어나면 게이지가 되감긴다.
    폴더가 아예 없는 소스는 건너뛴다 — "모름"을 적으면 화면이 헷갈린다.
    """
    from . import last_dates

    last_dates.ensure_loaded(Path(root) / "_last_dates.json")
    counts = {
        s["key"]: (1 if s["key"] == "marcap" else count_files(Path(root) / s["dir"]))
        for s in SOURCES
    }
    total = sum(counts.values())
    done = 0

    def tick(label: str) -> Callable[[], None]:
        def _one() -> None:
            nonlocal done
            done += 1
            if on_progress is not None:
                on_progress(label, done, total)

        return _one

    written: dict[str, str] = {}
    for src in SOURCES:
        step = tick(str(src["label"]))
        if src["key"] == "marcap":
            last, n = _scan_marcap(Path(marcap_dir), recent_dir=Path(root) / "recent")
            step()
        else:
            last, n = scan_last_date(Path(root) / src["dir"], src["date_col"], on_file=step)
        if last is None:
            continue
        write_mark(src["key"], last, n_symbols=n, root=root)
        written[src["key"]] = last
    last_dates.save()
    if on_progress is not None:
        on_progress("끝", total, total)
    return written


def _scan_marcap(marcap_dir: Path, recent_dir: Path | None = None) -> tuple[str | None, int]:
    """marcap 은 연도별 한 파일이다 — 가장 늦은 연도 파일의 날짜 열만 본다.

    marcap 뒤쪽 공백을 KRX 로 채운 보충 파일(`recent/YYYY-MM-DD.parquet`)이 있으면 그 날짜가
    차트 일봉의 실제 오른쪽 끝이다 — 파일 이름이 날짜라 열지 않고도 안다.
    """
    import pyarrow.parquet as pq

    files = sorted(Path(marcap_dir).glob("marcap-*.parquet"))
    if not files:
        return None, 0
    try:
        col = pq.read_table(files[-1], columns=["Date"])["Date"]
    except (OSError, ValueError, KeyError):
        return None, 0
    if len(col) == 0:
        return None, 0
    last = _norm(max(col.to_pylist()))
    if recent_dir is not None:
        for f in Path(recent_dir).glob("*.parquet"):
            try:
                day = _norm(pd.Timestamp(f.stem))
            except ValueError:
                continue
            if day > last:
                last = day
    return last, len(files)
