"""파일마다 "마지막 날짜"를 적어 두는 쪽지 — **안 바뀐 파일은 다시 열지 않는다.**

## 왜 필요한가

증분 갱신은 "이 파일이 어디까지 차 있나"를 수만 번 묻는다. 실측 2026-08-28:

| 방식 | 파일 1개 | 봉 파일 16,578개 |
|---|---|---|
| 통째로 열기 | 15.3ms | 4.2분 |
| 날짜 열만 읽기 | 5.7ms | 94초 |
| **쪽지 보기** | **0.08ms** | **1.3초** |

한 회차에 이 물음이 세 번 넘게 되풀이된다(주·월봉 판정 · 나무 봉 판정 · 수급 판정 ·
워터마크 갱신) — 통째로 3분이 넘었다.

## 낡아서 틀릴 일이 없는 이유

쪽지에 **고친 시각과 크기**를 같이 적는다. 다른 수집기가 파일을 건드리면 둘 중 하나는
반드시 달라지므로 그때만 다시 읽는다. `os.stat` 은 파일을 여는 것보다 수백 배 싸다.

쪽지가 없거나 깨져 있어도 문제없다 — 그냥 전부 다시 읽고 새로 적는다.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pyarrow.parquet as pq

DEFAULT_PATH = Path("data/derived/_last_dates.json")

_lock = threading.Lock()
_marks: dict[str, list] = {}
_path: Path = DEFAULT_PATH
_dirty = False
_loaded = False


def load(path: Path | None = None) -> None:
    """쪽지를 읽어 둔다. 없거나 깨졌으면 빈 채로 시작한다(이번 회차가 다시 채운다)."""
    global _marks, _path, _dirty, _loaded
    _path = Path(path) if path is not None else DEFAULT_PATH
    try:
        _marks = json.loads(_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _marks = {}
    _dirty = False
    _loaded = True


def ensure_loaded(path: Path | None = None) -> None:
    """아직 안 읽었으면 그때만 읽는다 — **이미 읽었으면 건드리지 않는다.**

    한 회차 안에서 갱신과 워터마크 갱신이 같은 쪽지를 쓴다. 뒤에 오는 쪽이 다시 읽으면
    앞에서 쌓은 걸 통째로 버리게 된다.
    """
    if not _loaded:
        load(path)


def save() -> None:
    """바뀐 게 있을 때만 적는다. 쓰다 죽어도 원본이 안 깨지게 임시 파일을 거친다."""
    if not _dirty:
        return
    _path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_marks, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_path)


def of(path: Path, date_col: str) -> str:
    """이 파일에 저장된 **가장 늦은 날짜**. 파일이 없거나 못 읽으면 빈 문자열."""
    global _dirty
    try:
        st = Path(path).stat()
    except OSError:
        return ""
    # 부르는 쪽마다 경로 모양이 다르다(갱신은 절대경로, 워터마크는 상대경로).
    # 그대로 열쇠로 쓰면 같은 파일이 두 칸을 차지해 쪽지가 안 통한다(실측 2026-08-28 —
    # 워터마크가 0.4초로 안 내려가고 7.5초에 머물렀다). 한 모양으로 펴서 쓴다.
    key = os.path.normcase(os.path.abspath(path))
    hit = _marks.get(key)
    if hit and hit[1] == st.st_mtime_ns and hit[2] == st.st_size:
        return str(hit[0])
    try:
        col = pq.read_table(path, columns=[date_col])[date_col]
    except (OSError, ValueError, KeyError):
        return ""
    value = "" if len(col) == 0 else str(max(col.to_pylist()))
    with _lock:
        _marks[key] = [value, st.st_mtime_ns, st.st_size]
        _dirty = True
    return value
