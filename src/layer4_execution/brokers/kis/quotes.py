"""KIS 국내주식 조회 — 순위분석부터.

## HTS조회상위20종목 (국내주식-214)

증권사 HTS 에서 **사람들이 실제로 많이 조회한 종목** 상위 20 개. 오너가 나무 앱에서
보던 "실시간 BEST"에 가장 가까운 API 다. 시가총액·거래량 순위와 달리 계산으로는
재현할 수 없는, 증권사만 가진 관심도 데이터다.

응답은 종목코드와 시장구분만 준다 — **순위 값을 따로 주지 않는다.** `output1` 의
배열 순서가 곧 순위다.

## ⚠ 백테스트에 바로 쓸 수 없다

이 API 는 "지금 이 순간의 상위 20"만 준다. 과거 특정일의 조회 상위를 되돌려 받을
방법이 없다. point-in-time 재현이 불가능하므로, 신호로 채택하려면 **지금부터 주기적으로
찍어서 시계열을 쌓아야 한다.** 쌓기 전에는 look-ahead 없는 검증이 불가능하다
(CLAUDE.md 방법론 가드레일). 수집기는 아직 없다 — 별도 작업.
"""

from __future__ import annotations

from dataclasses import dataclass

from .client import KisClient

HTS_TOP_VIEW_PATH = "/uapi/domestic-stock/v1/ranking/hts-top-view"
HTS_TOP_VIEW_TR_ID = "HHMCM000100C0"


@dataclass(frozen=True)
class TopViewItem:
    """조회 상위 한 줄. `rank` 는 응답 순서에서 매긴 값(1 부터)."""

    rank: int
    code: str  # 단축 종목코드 6 자리
    market: str  # 시장구분 코드 (mrkt_div_cls_code)


def fetch_hts_top_view(client: KisClient) -> list[TopViewItem]:
    """HTS 조회상위 20 종목. 파라미터 없음."""
    response = client.get(HTS_TOP_VIEW_PATH, tr_id=HTS_TOP_VIEW_TR_ID)
    rows = response.body.get("output1") or []

    items: list[TopViewItem] = []
    for row in rows:
        code = (row.get("mksc_shrn_iscd") or "").strip()
        if not code:
            # 응답 끝에 빈 행이 섞여 오는 경우가 있다. 그대로 두면 하위 조회가 400 을 맞는다.
            continue
        items.append(
            TopViewItem(
                rank=len(items) + 1,
                code=code,
                market=(row.get("mrkt_div_cls_code") or "").strip(),
            )
        )
    return items
