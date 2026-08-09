"""화면 설정 저장소 — 로컬 SQLite (BORB, 오너 지시 2026-08-09).

## 왜 만들었나

전략·검색식·관심종목이 **브라우저 안(localStorage)에만** 있었다. 그래서

- 주소가 `localhost` ↔ `127.0.0.1` 로 바뀌면 브라우저가 다른 사이트로 보고 저장소를
  따로 쓴다 — 오너가 저장해 둔 전략이 통째로 안 보였다(2026-08-09).
- 브라우저 캐시를 지우거나 다른 브라우저로 열면 전부 사라진다.

> "하.. 간단하게 로컬 DB 구현해라" — 오너

## 무엇을 저장하나

**키 하나에 JSON 한 덩어리.** 화면이 지금 localStorage 에 넣던 값을 그대로 올린다.
스키마를 테이블로 쪼개지 않는 이유는, 쪼개는 순간 화면 구조가 바뀔 때마다 마이그레이션이
필요해지기 때문이다. 지금 필요한 건 "안 날아가는 것"이지 질의가 아니다.

    hts-strategies  전략
    hts-screens     검색식
    hts-watchlist   관심종목
    hts-layout      화면 배치
    …

## 어디에 두나

`data/app.db` — 프로젝트 폴더 안이고 `.gitignore` 로 git 에는 안 올라간다(오너 지시).

## 주의

- 이건 **화면 설정**이지 매매 데이터가 아니다. 주문·포지션은 여기 안 들어온다(CLAUDE.md).
- 표준 라이브러리 `sqlite3` 만 쓴다 — 설치할 것이 없다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 리포 루트/data/app.db. 이 파일 기준으로 올라간다 — 실행 위치에 안 흔들린다.
DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "app.db"

# 키 이름 규칙 — 화면이 쓰던 localStorage 키 그대로. 아무 문자열이나 받으면 오타가
# 조용히 새 칸을 만든다. 눈에 띄는 길이·문자만 허용한다.
_MAX_KEY = 64
_MAX_VALUE = 4 * 1024 * 1024  # 4MB — 전략 수백 개도 이 안에 들어온다

_lock = threading.Lock()


def _connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")  # 읽기와 쓰기가 서로 안 막는다
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kv (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


@contextmanager
def _db(db: Path | None = None) -> Iterator[sqlite3.Connection]:
    """한 번에 하나만 쓴다 — 개발용 단일 사용자라 이 정도로 충분하다."""
    conn = _connect(db or DEFAULT_DB)
    try:
        with _lock:
            yield conn
            conn.commit()
    finally:
        conn.close()


def validate_key(key: str) -> str:
    """저장 가능한 키인가. 아니면 ValueError(한국어)."""
    k = key.strip()
    if not k:
        raise ValueError("키가 비어 있습니다.")
    if len(k) > _MAX_KEY:
        raise ValueError(f"키가 너무 깁니다({len(k)}자, {_MAX_KEY}자까지): {k[:20]}…")
    if not all(c.isalnum() or c in "-_." for c in k):
        raise ValueError(f"키에는 영문·숫자·'-_.' 만 쓸 수 있습니다: {k!r}")
    return k


def get(key: str, *, db: Path | None = None) -> Any | None:
    """저장된 값(JSON 을 푼 것). 없으면 None."""
    k = validate_key(key)
    with _db(db) as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (k,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        # 저장할 때 JSON 으로 검사하므로 여기 오면 파일이 손상된 것이다.
        # 조용히 None 을 주면 화면이 "저장한 적 없음"으로 보여 덮어써 버린다.
        raise ValueError(f"'{k}' 에 저장된 값이 깨졌습니다 — data/app.db 를 확인하세요.") from None


def put(key: str, value: Any, *, db: Path | None = None) -> None:
    """값을 통째로 덮어쓴다. JSON 으로 바꿀 수 없는 값은 거부."""
    k = validate_key(key)
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as e:
        raise ValueError(f"'{k}' 값을 JSON 으로 바꿀 수 없습니다: {e}") from e
    if len(text) > _MAX_VALUE:
        raise ValueError(f"'{k}' 값이 너무 큽니다({len(text):,}자, {_MAX_VALUE:,}자까지).")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with _db(db) as conn:
        conn.execute(
            """
            INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (k, text, now),
        )


def delete(key: str, *, db: Path | None = None) -> bool:
    """지웠으면 True, 원래 없었으면 False."""
    k = validate_key(key)
    with _db(db) as conn:
        return conn.execute("DELETE FROM kv WHERE key = ?", (k,)).rowcount > 0


def keys(*, db: Path | None = None) -> list[str]:
    """저장된 키 목록 — 이름순."""
    with _db(db) as conn:
        return [r[0] for r in conn.execute("SELECT key FROM kv ORDER BY key")]


def snapshot(*, db: Path | None = None) -> dict[str, Any]:
    """전부 한 번에 — 화면이 뜰 때 한 번 받아 가는 용도."""
    with _db(db) as conn:
        rows = conn.execute("SELECT key, value FROM kv ORDER BY key").fetchall()
    out: dict[str, Any] = {}
    for k, text in rows:
        try:
            out[k] = json.loads(text)
        except json.JSONDecodeError:
            continue  # 깨진 칸 하나 때문에 나머지를 못 쓰게 하지 않는다
    return out
