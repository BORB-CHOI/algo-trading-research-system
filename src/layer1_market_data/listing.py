"""상장 목록에서 **주식만** 가려낸다 — 종목별 KIS 일별 수집 대상.

## 왜 필요한가

`update_data.update_kis` 는 나무 마스터(`m_new_stock`)를 돌며 수급·신용잔고를 종목마다
받는다. 이 목록에는 ETF·ETN 이 섞여 있다(실측 2026-09-11: 4,299종목 중 1,534개).

백필(`backfill_kis_supply`·`backfill_kis_credit`)의 유니버스는 marcap 인데 **ETF/ETN 은
marcap 에 아예 없다**(`api/routers/symbols.py` 실측 0건). 그래서 백필은 이들을 한 번도
받은 적이 없고, 앞으로도 받지 않는다.

문제는 증분이 "저장 파일이 없다"를 어떻게 읽느냐였다.

* 파일이 없으면 무조건 받으러 간다 → 유니버스 밖 ETF 1,535종목의 전 이력을 새로 받는다.
* 파일이 없으면 무조건 건너뛴다 → **백필 이후에 상장한 종목이 영원히 빈 채로 남는다.**
  2026-08-16 백필 이후 상장한 9종목(해치텍·스카이랩스·니어스랩·딜리셔스·
  케이앤에스아이앤씨·엔에이치스팩34호·기도산업·한화머시너리앤서비스홀딩스 + 3우B)이
  2026-09-11 까지 한 줄도 없었다. 옛 코드가 이쪽이었다.

둘을 가르려면 "이 코드가 주식인가"를 알아야 한다. 그게 이 파일이다.

## 규칙 — 액면가가 있거나, marcap 에 실렸으면 주식이다

ETF·ETN 은 무액면이라 증권사 마스터의 액면가(`sParvalue`)가 0 이다. 다만 무액면인
주식도 있어(인프라펀드·리츠·외국기업) 액면가만 보면 이들을 놓친다. 그쪽은 marcap 이
받쳐 준다.

| | 액면가 | marcap |
|---|---|---|
| 보통주·우선주·스팩 | 있음 | 있음 |
| 갓 상장해 marcap 이 아직 못 따라잡은 주식 | 있음 | 없음 |
| 무액면 주식 (맥쿼리인프라·맵스리얼티·KB발해인프라·900xxx·950xxx 외국기업) | 0 | 있음 |
| ETF · ETN | 0 | 없음 |

실측 2026-09-11 — 4,299종목 → 주식 2,765 · ETF/ETN 1,534.
액면가만으로 가르면 무액면 주식 20종목을 ETF 로 잘못 밀어낸다.
marcap 만으로 가르면 갓 상장한 2종목(엔에이치스팩34호·스카이랩스)을 놓친다.

조회 전용 판정만 한다. 주문 없음.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.layer1_market_data.derived import NAMUH_BARS_DIR
from src.layer1_market_data.marcap_loader import available_years, load_years, normalize_code

PAR_COL = "sParvalue"
CODE_COL = "sCode"

DAY_BARS_DIR = Path(NAMUH_BARS_DIR) / "krx" / "day"
DAY_DATE_COL = "bsop_date"


def par_value(raw: Any) -> int:
    """증권사 마스터의 액면가. 빈 칸·글자는 0 으로 읽는다(= 무액면)."""
    text = str(raw).strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def marcap_codes(year: int | None = None) -> set[str]:
    """marcap 에 실린 종목 코드. 기본은 **가장 늦은 연도** 한 해만 읽는다.

    상장 목록에 있는 종목을 가리는 게 목적이라 옛 연도는 볼 필요가 없다 — 옛 연도에만
    있는 코드는 이미 상장폐지됐고 상장 목록에 없다.
    """
    years = available_years()
    if not years:
        return set()
    target = year if year is not None else max(years)
    frame = load_years(target, target)
    if frame.empty or "Code" not in frame.columns:
        return set()
    return {str(c) for c in normalize_code(frame["Code"])}


def stock_codes(master: pd.DataFrame, *, in_marcap: set[str] | None = None) -> set[str]:
    """상장 목록에서 주식 코드만. ETF·ETN 은 뺀다.

    `in_marcap` 을 넘기면 marcap 을 다시 읽지 않는다 — 한 회차에서 여러 번 부를 때 쓴다.
    """
    if master is None or master.empty or CODE_COL not in master.columns:
        return set()
    known = marcap_codes() if in_marcap is None else in_marcap
    pars = master[PAR_COL] if PAR_COL in master.columns else None
    out: set[str] = set()
    for i, raw_code in enumerate(master[CODE_COL]):
        code = str(raw_code).strip()
        if not code:
            continue
        if code in known or (pars is not None and par_value(pars.iloc[i]) > 0):
            out.add(code)
    return out


def first_traded(code: str, *, bars_dir: Path | None = None) -> str:
    """이 종목이 **처음 거래된 날** (YYYYMMDD). 우리 일봉에서 읽는다. 없으면 빈 글자.

    신규 상장 종목을 받을 때 어디서부터 달라고 할지 정하는 값이다.

    KIS 수급·신용잔고는 상장 전 날짜를 물어도 **날짜만 든 빈 행**을 돌려준다. 그래서
    바닥(1994·2007)부터 달라고 하면 빈 행 8,400여 줄이 쌓이고 종목당 280여 콜을 헛쓴다
    (실측 2026-09-11: 10종목에 8,490행씩 담겼는데 값이 든 행은 2~22줄이었다).

    백필은 marcap 에서 첫 거래일을 얻어 이 문제가 없었다. 갓 상장한 종목은 marcap 이
    아직 못 따라잡으므로 우리 일봉을 본다 — 실측으로 수급 값 시작일과 정확히 같았다
    (기도산업 20260821 · 스카이랩스 20260904 · 엔에이치스팩34호 20260910).
    """
    path = (bars_dir or DAY_BARS_DIR) / f"{code}.parquet"
    if not path.is_file():
        return ""
    try:
        days = pd.read_parquet(path, columns=[DAY_DATE_COL])[DAY_DATE_COL]
    except (OSError, ValueError, KeyError):
        return ""
    if days.empty:
        return ""
    first = str(days.astype(str).min()).strip()
    return first if len(first) == 8 and first.isdigit() else ""
