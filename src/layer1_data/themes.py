"""네이버 테마 → 종목 매핑 (표시 전용).

m.stock.naver.com 비공식 API. 백테스트·매매 판단에 쓰지 않는다 — point-in-time 아님.
수집 로직은 naver_groups.GroupCollector 공용 — 업종(industry.py)과 같다.
완성 전에는 빈 맵을 돌려준다(themes_ready=False).
"""

from __future__ import annotations

from src.layer1_data.naver_groups import GroupCollector

_collector = GroupCollector("https://m.stock.naver.com/api/stocks/theme")


def theme_map() -> tuple[dict[str, list[str]], bool]:
    """(code → 테마명 목록, ready). 처음/만료 시 백그라운드로 재수집."""
    return _collector.map()
