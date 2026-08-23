"""화면에 띄우는 데이터 경고문 — 수급에 상폐 종목이 없다 등."""

from __future__ import annotations

from src.layer1_data.provider import DataProvider


def data_notes() -> list[dict]:
    """조건을 고르기 전에 알아야 할 데이터 사실. 화면이 그대로 띄운다."""
    notes: list[dict] = []
    try:
        cov = DataProvider().supply_coverage()
    except OSError:
        return notes
    missing = cov.get("수급_없는_종목", 0)
    if missing > 0:
        notes.append(
            {
                "key": "supply_delisted",
                "level": "warn",
                "title": "수급 조건을 쓰면 상장폐지된 종목이 빠집니다",
                "body": (
                    f"일봉은 {cov['일봉']:,}종목 있는데 수급은 {cov['수급']:,}종목뿐입니다"
                    f"({missing:,}종목 없음). 망한 회사는 수급 자료를 받을 수 없어서, "
                    "과거 구간 성적이 실제보다 좋게 나올 수 있습니다."
                ),
            }
        )
    return notes
