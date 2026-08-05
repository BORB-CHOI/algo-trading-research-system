"""네이버 업종 → 종목 매핑 (표시 전용).

m.stock.naver.com 비공식 API. 백테스트·매매 판단에 쓰지 않는다 — point-in-time 아님.
수집 로직은 naver_groups.GroupCollector 공용 — 테마(themes.py)와 같다.
완성 전에는 빈 맵을 돌려준다(ready=False).
"""

from __future__ import annotations

from src.layer1_data.naver_groups import GroupCollector

_collector = GroupCollector("https://m.stock.naver.com/api/stocks/industry")


def industry_map() -> tuple[dict[str, str], bool]:
    """(code → 업종명 1개, ready). 처음/만료 시 백그라운드로 재수집.

    시장맵 타일은 업종을 하나만 가지므로, 한 종목이 여러 업종에 나오면 처음 것을 쓴다.
    """
    m, ready = _collector.map()
    return {code: names[0] for code, names in m.items() if names}, ready
