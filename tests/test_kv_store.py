"""화면 설정 저장소(로컬 SQLite) 단위 테스트.

임시 파일에만 쓴다 — 실제 `data/app.db` 는 절대 안 건드린다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.layer1_data import kv_store


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def test_없는_키는_None(db: Path) -> None:
    assert kv_store.get("hts-strategies", db=db) is None


def test_넣고_뺀다(db: Path) -> None:
    value = {"눌림매매": {"screen": {"conditions": []}, "split": {"buy": []}}}
    kv_store.put("hts-strategies", value, db=db)
    assert kv_store.get("hts-strategies", db=db) == value


def test_덮어쓴다(db: Path) -> None:
    kv_store.put("k", {"a": 1}, db=db)
    kv_store.put("k", {"a": 2, "b": 3}, db=db)
    assert kv_store.get("k", db=db) == {"a": 2, "b": 3}


def test_한글과_중첩_구조가_그대로_돌아온다(db: Path) -> None:
    value = {"관심종목": [{"code": "005930", "name": "삼성전자", "메모": "눌림 대기"}]}
    kv_store.put("hts-watchlist", value, db=db)
    assert kv_store.get("hts-watchlist", db=db) == value


def test_목록과_전체_스냅샷(db: Path) -> None:
    kv_store.put("hts-screens", {"거래대금상위": {}}, db=db)
    kv_store.put("hts-strategies", {"눌림": {}}, db=db)
    assert kv_store.keys(db=db) == ["hts-screens", "hts-strategies"]
    assert kv_store.snapshot(db=db) == {
        "hts-screens": {"거래대금상위": {}},
        "hts-strategies": {"눌림": {}},
    }


def test_지운다(db: Path) -> None:
    kv_store.put("k", [1, 2], db=db)
    assert kv_store.delete("k", db=db) is True
    assert kv_store.delete("k", db=db) is False
    assert kv_store.get("k", db=db) is None


def test_빈_값도_저장된다(db: Path) -> None:
    """전략을 전부 지운 상태도 '지운 적 없음'과 구분돼야 한다."""
    kv_store.put("hts-strategies", {}, db=db)
    assert kv_store.get("hts-strategies", db=db) == {}
    assert kv_store.get("없는키", db=db) is None


def test_이상한_키는_거부한다(db: Path) -> None:
    with pytest.raises(ValueError, match="비어 있"):
        kv_store.put("  ", {}, db=db)
    with pytest.raises(ValueError, match="너무 깁니다"):
        kv_store.put("k" * 65, {}, db=db)
    with pytest.raises(ValueError, match="영문·숫자"):
        kv_store.put("has space", {}, db=db)
    with pytest.raises(ValueError, match="영문·숫자"):
        kv_store.put("../etc/passwd", {}, db=db)


def test_JSON_으로_못_바꾸는_값은_거부한다(db: Path) -> None:
    with pytest.raises(ValueError, match="JSON"):
        kv_store.put("k", {"f": lambda: 1}, db=db)


def test_너무_큰_값은_거부한다(db: Path) -> None:
    with pytest.raises(ValueError, match="너무 큽니다"):
        kv_store.put("k", ["x" * 1000] * 5000, db=db)


def test_깨진_값은_조용히_넘기지_않는다(db: Path) -> None:
    """파일이 손상됐는데 None 을 주면 화면이 '저장한 적 없음'으로 보고 덮어쓴다."""
    kv_store.put("k", {"a": 1}, db=db)
    with kv_store._db(db) as conn:  # noqa: SLF001 — 손상 상황을 만들려면 직접 써야 한다
        conn.execute("UPDATE kv SET value = '{깨짐' WHERE key = 'k'")
    with pytest.raises(ValueError, match="깨졌습니다"):
        kv_store.get("k", db=db)
    # 스냅샷은 깨진 칸만 건너뛰고 나머지를 살린다
    kv_store.put("ok", [1], db=db)
    assert kv_store.snapshot(db=db) == {"ok": [1]}
