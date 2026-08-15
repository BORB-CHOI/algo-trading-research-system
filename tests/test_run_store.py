"""백테스트 보관함 — 임시 파일에만 쓴다(실제 data/app.db 는 안 건드린다)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.layer1_data import run_store

RESULT = {
    "split": "train",
    "split_start": "2020-01-01",
    "split_end": "2023-12-31",
    "base_date": "2019-12-30",
    "picked": 24,
    "results": [
        {
            "code": "196170",
            "name": "알테오젠",
            "n_buys": 3,
            "stopped": False,
            "avg_entry": 45_240.0,
            "exit_value": 98_500.0,
            "net_return": 1.172,
            "first_fill": "2022-01-24",
            "last_exit": "2023-12-28",
            "wave_low": 25_575.0,
            "wave_high": 77_200.0,
            "buy_orders": [{"tranche": 1, "price": 55_000.0, "ratio": 0.382}],
            "fills": [{"time": "2022-01-24", "side": "buy", "price": 55_000.0, "w": 33}],
        },
        {
            "code": "005930",
            "name": "삼성전자",
            "n_buys": 2,
            "stopped": True,
            "avg_entry": 60_000.0,
            "exit_value": 48_000.0,
            "net_return": -0.2,
            "first_fill": "2020-03-19",
            "last_exit": "2020-04-02",
            "wave_low": 40_000.0,
            "wave_high": 90_000.0,
        },
    ],
    "no_fill_rows": [
        {"code": "000660", "name": "SK하이닉스", "n_buys": 0, "low_in_span": 70_000.0}
    ],
    "metrics": {"n_trades": 2, "win_rate": 0.5, "expectancy": 0.486, "cum_net_return": 0.738},
}
PARAMS = {"sr_channel_width_pct": 3, "buy_min_gap_pct": 10}
NOW = "2026-08-09T22:00:00"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "runs.db"


def test_담고_꺼낸다(db: Path) -> None:
    rid = run_store.save_run(
        RESULT, ran_at=NOW, params=PARAMS, label="첫 검사", screen="신고가", db=db
    )
    got = run_store.load_run(rid, db=db)
    assert got is not None
    assert (got["label"], got["screen"], got["picked"], got["n_trades"]) == (
        "첫 검사",
        "신고가",
        24,
        2,
    )
    assert got["params"] == PARAMS
    # 못 산 종목도 같이 담는다 — 왜 안 걸렸는지가 전략 고치는 데 제일 쓸모 있다
    assert [p["code"] for p in got["picks"]] == ["196170", "005930", "000660"]
    assert got["picks"][0]["name"] == "알테오젠"
    assert got["picks"][0]["detail"]["buy_orders"][0]["price"] == 55_000.0
    assert got["picks"][2]["detail"]["low_in_span"] == 70_000.0


def test_없는_번호는_None(db: Path) -> None:
    assert run_store.load_run(999, db=db) is None


def test_목록은_최근_순(db: Path) -> None:
    a = run_store.save_run(RESULT, ran_at=NOW, params=PARAMS, label="A", db=db)
    b = run_store.save_run(RESULT, ran_at=NOW, params=PARAMS, label="B", db=db)
    assert [r["id"] for r in run_store.list_runs(db=db)] == [b, a]


def test_지운다(db: Path) -> None:
    rid = run_store.save_run(RESULT, ran_at=NOW, params=PARAMS, db=db)
    assert run_store.delete_run(rid, db=db) is True
    assert run_store.delete_run(rid, db=db) is False
    assert run_store.load_run(rid, db=db) is None


def test_처음_산_달로_묶어_본다(db: Path) -> None:
    """오너가 말한 쓰임 — "이 구간은 승률이 너무 안 좋은데" 를 찾는 입구."""
    rid = run_store.save_run(RESULT, ran_at=NOW, params=PARAMS, db=db)
    rows = {r["month"]: r for r in run_store.by_month(rid, db=db)}
    assert set(rows) == {"2020-03", "2022-01"}  # 못 산 종목은 산 날이 없어 안 들어간다
    assert rows["2020-03"]["win_rate"] == 0.0
    assert rows["2020-03"]["worst"] == pytest.approx(-0.2)
    assert rows["2022-01"]["win_rate"] == 1.0


def test_구간에_메모를_붙인다(db: Path) -> None:
    run_store.add_note("period", "2020-03", "코로나 폭락", added_at=NOW, db=db)
    run_store.add_note("period", "2020-03", "유가 급락", added_at=NOW, db=db)
    got = run_store.notes_for("period", "2020-03", db=db)
    assert [n["body"] for n in got] == ["코로나 폭락", "유가 급락"]
    assert run_store.notes_for("period", "2021-01", db=db) == []


def test_이상한_메모는_거부한다(db: Path) -> None:
    with pytest.raises(ValueError, match="모르는 메모 종류"):
        run_store.add_note("아무거나", "x", "내용", added_at=NOW, db=db)
    with pytest.raises(ValueError, match="비었"):
        run_store.add_note("code", "005930", "   ", added_at=NOW, db=db)


def test_불러온_결과가_화면_계약과_같다(tmp_path) -> None:
    """보관함에서 꺼낸 것이 방금 돌린 것과 **같은 모양**이어야 한다.

    오너 2026-08-10: "저장할 수 있으면 뭐하냐 불러오지를 못하는데". 저장은 되는데
    꺼내 볼 입구가 없었다. 손절선·미청산 표시·라운드 시작일도 같이 남아야 다시 그린다.
    """
    from src.layer4_execution.runner import aggregate_returns

    db = tmp_path / "runs.db"
    result = {
        "split": "all",
        "split_start": "2019-01-01",
        "split_end": "2026-08-10",
        "base_date": None,  # 전 기간 검사 — 고른 날이 하루가 아니다
        "picked": 2,
        "metrics": {"n_trades": 1},
        "results": [
            {
                "code": "005930",
                "name": "삼성전자",
                "n_buys": 2,
                "stopped": False,
                "avg_entry": 70_000.0,
                "exit_value": 77_000.0,
                "net_return": 0.1,
                "first_fill": "2020-03-20",
                "last_exit": "2020-06-01",
                "wave_low": 60_000.0,
                "wave_high": 90_000.0,
                "wave_low_date": "2020-01-02",
                "stop_price": 63_600.0,
                "open": False,
                "plan_date": "2020-03-10",
                "buy_orders": [{"tranche": 1, "price": 70_000.0, "ratio": 0.5}],
                "fills": [{"time": "2020-03-20", "side": "buy", "price": 70_000.0, "w": 100}],
            }
        ],
        "no_fill_rows": [
            {"code": "000660", "name": "SK하이닉스", "n_buys": 0, "stopped": False,
             "buy_orders": [{"tranche": 1, "price": 50_000.0, "ratio": 0.5}]}
        ],
    }
    run_id = run_store.save_run(result, ran_at="2026-08-10T01:00:00", params={}, db=db)
    got = run_store.load_run(run_id, db=db)
    assert got is not None
    assert got["base_date"] == ""  # None 을 넣어도 저장이 깨지지 않는다

    # 화면이 쓰는 갈래 — 순수익률이 없는 줄이 '한 주도 못 산' 쪽이다
    filled = [p for p in got["picks"] if p["net_return"] is not None]
    empty = [p for p in got["picks"] if p["net_return"] is None]
    assert [p["code"] for p in filled] == ["005930"]
    assert [p["code"] for p in empty] == ["000660"]

    # 다시 그리는 데 필요한 값이 전부 남아 있다
    d = filled[0]["detail"]
    assert d["stop_price"] == 63_600.0
    assert d["plan_date"] == "2020-03-10"
    assert d["open"] is False
    assert d["wave_low_date"] == "2020-01-02"
    assert d["fills"][0]["price"] == 70_000.0

    # 지표는 방금 돌린 것과 같은 정의로 다시 센다
    assert aggregate_returns([0.1])["win_rate"] == 1.0


# ─────────────────────────────────────────────────────────────
# 체결 내역 — 실험 산출물 (미션 문서 §19-3, ADR-0019 후속)
#
# 지금까지 runs·picks·notes 만 남아 두 실험을 **줄 단위로** 견줄 수 없었다.
# 체결 한 줄씩 남겨 "이 자리에서 실제로 샀나, 얼마나 아슬아슬했나"를 나중에 본다.
# ─────────────────────────────────────────────────────────────

FILLS = [
    {
        "code": "005930",
        "date": "2026-01-05",
        "side": "buy",
        "price": 70000.0,
        "weight": 0.3,
        "stage": 1,
        "slack_ticks": 0,  # 딱 닿기만 함 — 실제로는 못 샀을 수 있다
    },
    {
        "code": "005930",
        "date": "2026-02-10",
        "side": "sell",
        "price": 77000.0,
        "weight": 1.0,
        "stage": 1,
        "slack_ticks": 2,
    },
]


def test_검사를_담으면_체결도_같이_담긴다(db: Path) -> None:
    """`save_run` 이 결과 행의 fills 를 표로 옮긴다 — 따로 부르지 않아도 된다."""
    rid = run_store.save_run(RESULT, ran_at=NOW, params=PARAMS, db=db)
    got = run_store.load_fills(rid, db=db)
    assert got, "결과에 체결이 있으면 표에도 있어야 한다"
    assert all(set(f) == set(run_store._FILL_COLS) for f in got)


def test_체결을_더_담고_꺼낸다(db: Path) -> None:
    rid = run_store.save_run(RESULT, ran_at=NOW, params=PARAMS, db=db)
    before = len(run_store.load_fills(rid, db=db))
    run_store.save_fills(rid, FILLS, db=db)
    got = run_store.load_fills(rid, db=db)
    assert len(got) == before + 2
    mine = [f for f in got if f["code"] == "005930"]
    assert len(mine) == 2
    assert {f["side"] for f in mine} == {"buy", "sell"}
    assert [f["slack_ticks"] for f in sorted(mine, key=lambda f: f["date"])] == [0, 2]


def test_빈_체결을_담아도_안_터진다(db: Path) -> None:
    rid = run_store.save_run(RESULT, ran_at=NOW, params=PARAMS, db=db)
    before = run_store.load_fills(rid, db=db)
    run_store.save_fills(rid, [], db=db)
    assert run_store.load_fills(rid, db=db) == before


def test_검사를_지우면_체결도_같이_지워진다(db: Path) -> None:
    rid = run_store.save_run(RESULT, ran_at=NOW, params=PARAMS, db=db)
    run_store.save_fills(rid, FILLS, db=db)
    run_store.delete_run(rid, db=db)
    assert run_store.load_fills(rid, db=db) == []
