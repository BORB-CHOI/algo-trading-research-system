"""백테스트 결과 보관함 — 돌린 걸 남겨 두고 나중에 잘라 보기 (오너 2026-08-09).

> "백테스트 돌린 거 어디에 저장해둘 수 있게 해서, 데이터 분석을 너가 가능하도록 해.
>  어느 구간이 특이하게 상승장에서는 승률이 안좋았다거나. 이 구간은 너무 승률이 안좋은데
>  나중에 LLM 통해 재료 분석해보니 유가 급락이나 코로나, 법정 싸움 패배 등의 이슈가 있던
>  거였다는 걸 알게 될 수도 있잖아."

그래서 **JSON 덩어리가 아니라 표**로 담는다. 종목·날짜·수익률로 걸러서 세어 봐야
"2020년 3월에 산 건 다 깨졌다" 같은 걸 찾을 수 있다.

    runs    한 번 돌린 것       — 언제·어떤 구간·어떤 설정·전체 성적
    picks   그 안의 종목 한 줄  — 언제 사서 언제 팔았고 얼마 벌었나
    notes   나중에 붙이는 메모  — "2020-03 코로나 폭락" 같은 재료. 사람이든 LLM 이든.

`kv_store` 와 **같은 파일**(`data/app.db`)을 쓴다. 화면 설정과 성격이 달라 파일을 나눌까
했지만, 백업·이사가 파일 하나면 끝나는 쪽이 낫다.

**저장은 실패해도 백테스트를 막지 않는다** — 결과를 못 봐 주는 것보다 낫다. 호출부가
`save_run` 의 예외를 삼키고 경고만 남긴다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.layer1_data.kv_store import DEFAULT_DB

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at      TEXT NOT NULL,              -- 돌린 시각 (ISO)
    label       TEXT NOT NULL DEFAULT '',   -- 사람이 붙이는 이름
    split       TEXT NOT NULL,              -- train | validate | test
    split_start TEXT NOT NULL,
    split_end   TEXT NOT NULL,
    base_date   TEXT NOT NULL,              -- 종목을 고른 날
    screen      TEXT NOT NULL DEFAULT '',   -- 검색식 이름
    picked      INTEGER NOT NULL,           -- 그날 걸린 종목 수
    n_trades    INTEGER NOT NULL,
    win_rate    REAL,
    expectancy  REAL,
    cum_return  REAL,
    params      TEXT NOT NULL               -- 그때 쓴 전략 값 전부 (JSON)
);
CREATE TABLE IF NOT EXISTS picks (
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    code        TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    n_buys      INTEGER NOT NULL,
    stopped     INTEGER NOT NULL DEFAULT 0,
    avg_entry   REAL,
    exit_value  REAL,
    net_return  REAL,
    first_fill  TEXT,                       -- 처음 산 날
    last_exit   TEXT,                       -- 마지막 판 날
    wave_low    REAL,
    wave_high   REAL,
    detail      TEXT NOT NULL DEFAULT '{}'  -- 걸어 둔 값·체결 내역 (JSON)
);
CREATE INDEX IF NOT EXISTS picks_run ON picks(run_id);
CREATE INDEX IF NOT EXISTS picks_code ON picks(code);
CREATE INDEX IF NOT EXISTS picks_first_fill ON picks(first_fill);
CREATE TABLE IF NOT EXISTS notes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    scope    TEXT NOT NULL,   -- 'period' | 'code' | 'run'
    key      TEXT NOT NULL,   -- '2020-03' | '005930' | run_id
    body     TEXT NOT NULL,
    added_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_scope_key ON notes(scope, key);
"""


@contextmanager
def _db(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    p = Path(path or DEFAULT_DB)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(p, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()


def save_run(
    result: dict,
    *,
    ran_at: str,
    params: dict[str, Any],
    label: str = "",
    screen: str = "",
    db: Path | None = None,
) -> int:
    """백테스트 결과 하나를 담는다. 반환 = 보관함 번호(run id).

    `ran_at` 은 **호출부가 준다** — 이 모듈이 시계를 읽으면 테스트가 시각에 흔들린다.
    체결된 종목뿐 아니라 **한 주도 못 산 종목**(`no_fill_rows`)도 같이 담는다. 왜 안
    걸렸는지가 전략을 고치는 데 제일 쓸모 있는 정보다.
    """
    m = result.get("metrics", {})
    with _db(db) as conn:
        cur = conn.execute(
            "INSERT INTO runs (ran_at, label, split, split_start, split_end, base_date,"
            " screen, picked, n_trades, win_rate, expectancy, cum_return, params)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ran_at,
                label,
                result.get("split", ""),
                result.get("split_start", ""),
                result.get("split_end", ""),
                result.get("base_date") or "",  # 전 기간 검사는 고른 날이 하루가 아니다
                screen,
                int(result.get("picked", 0)),
                int(m.get("n_trades", 0)),
                m.get("win_rate"),
                m.get("expectancy"),
                m.get("cum_net_return"),
                json.dumps(params, ensure_ascii=False, sort_keys=True),
            ),
        )
        run_id = int(cur.lastrowid or 0)
        rows = [*result.get("results", []), *result.get("no_fill_rows", [])]
        conn.executemany(
            "INSERT INTO picks (run_id, code, name, n_buys, stopped, avg_entry, exit_value,"
            " net_return, first_fill, last_exit, wave_low, wave_high, detail)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    run_id,
                    r.get("code", ""),
                    r.get("name", ""),
                    int(r.get("n_buys", 0)),
                    1 if r.get("stopped") else 0,
                    r.get("avg_entry"),
                    r.get("exit_value"),
                    r.get("net_return"),
                    r.get("first_fill"),
                    r.get("last_exit"),
                    r.get("wave_low"),
                    r.get("wave_high"),
                    json.dumps(
                        {
                            k: r[k]
                            for k in (
                                "buy_orders",
                                "sell_orders",
                                "fills",
                                "unplaced",
                                "low_in_span",
                                # 화면이 그대로 다시 그리려면 이것들도 있어야 한다 —
                                # 없으면 불러온 결과에 손절선·미청산 표시·라운드 시작일이
                                # 통째로 빠진다 (오너 2026-08-10: "불러오지를 못하는데").
                                "wave_low_date",
                                "sell_basis_price",
                                "stop_price",
                                "open",
                                "plan_date",
                            )
                            if k in r
                        },
                        ensure_ascii=False,
                    ),
                )
                for r in rows
            ],
        )
    return run_id


def list_runs(*, limit: int = 50, db: Path | None = None) -> list[dict]:
    """보관해 둔 것들 — 최근 순."""
    with _db(db) as conn:
        cur = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


def load_run(run_id: int, *, db: Path | None = None) -> dict | None:
    """한 번 돌린 것 전체 — 요약 + 종목별 줄."""
    with _db(db) as conn:
        head = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if head is None:
            return None
        picks = conn.execute(
            "SELECT * FROM picks WHERE run_id = ? ORDER BY net_return IS NULL, net_return DESC",
            (run_id,),
        ).fetchall()
    out = dict(head)
    out["params"] = json.loads(out["params"])
    out["picks"] = [{**dict(p), "detail": json.loads(p["detail"])} for p in picks]
    return out


def delete_run(run_id: int, *, db: Path | None = None) -> bool:
    with _db(db) as conn:
        conn.execute("DELETE FROM picks WHERE run_id = ?", (run_id,))
        cur = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        return cur.rowcount > 0


def by_month(run_id: int, *, db: Path | None = None) -> list[dict]:
    """**처음 산 달**로 묶은 성적 — "2020-03 에 산 건 다 깨졌다"를 찾는 입구.

    오너가 말한 쓰임이 이것이다: 성적이 유난히 나쁜 달을 먼저 찾고, 그 달에 무슨 일이
    있었는지(유가 급락·코로나·소송 패소 등)는 나중에 따로 붙인다(`notes`).
    """
    with _db(db) as conn:
        cur = conn.execute(
            "SELECT substr(first_fill, 1, 7) AS month, COUNT(*) AS n,"
            " AVG(net_return) AS avg_return,"
            " SUM(CASE WHEN net_return > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate,"
            " MIN(net_return) AS worst, MAX(net_return) AS best"
            " FROM picks WHERE run_id = ? AND first_fill IS NOT NULL"
            " GROUP BY month ORDER BY month",
            (run_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def add_note(scope: str, key: str, body: str, *, added_at: str, db: Path | None = None) -> int:
    """구간·종목에 메모를 붙인다. `scope` = 'period' | 'code' | 'run'.

    예) add_note("period", "2020-03", "코로나 폭락", added_at=...)
    나중에 재료 분석 결과를 여기 쌓아 두면 성적 표와 나란히 놓고 볼 수 있다.
    """
    if scope not in ("period", "code", "run"):
        raise ValueError(f"모르는 메모 종류입니다: {scope!r} (period | code | run)")
    if not body.strip():
        raise ValueError("메모가 비었습니다.")
    with _db(db) as conn:
        cur = conn.execute(
            "INSERT INTO notes (scope, key, body, added_at) VALUES (?,?,?,?)",
            (scope, key, body.strip(), added_at),
        )
        return int(cur.lastrowid or 0)


def notes_for(scope: str, key: str, *, db: Path | None = None) -> list[dict]:
    with _db(db) as conn:
        cur = conn.execute(
            "SELECT * FROM notes WHERE scope = ? AND key = ? ORDER BY id", (scope, key)
        )
        return [dict(r) for r in cur.fetchall()]
