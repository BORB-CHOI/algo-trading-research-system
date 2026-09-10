"""종목에 생긴 사건 — 액면교체·합병·무상증자·유상증자·자본감소를 예탁원에서 받는다.

## 왜 받나 — 지금은 **가격을 보고 추측**하고 있다

수정주가 보정(ADR-0006, `adjust.split_adjustment`)은 실제 사건 기록이 없어서 **전날 종가
대비 오늘 종가가 얼마나 튀었나**로 액면분할을 짐작한다. 임계값은 전부 placeholder다.
짐작이라 두 가지가 틀린다:

    놓친다   분할 비율이 작으면(예: 2:1) 그냥 큰 하락과 구별이 안 된다
    헛짚는다 하한가 두 방 맞은 종목을 분할로 보고 가격을 통째로 어긋나게 만든다

예탁원 일정은 **언제 무엇이 얼마나** 바뀌었는지를 그대로 준다. 짐작할 일이 없어진다.

## 무엇을 어디서 받나 (실측 2026-09-10)

| 무엇 | KIS TR | 왜 수정주가에 영향을 주나 |
|---|---|---|
| 액면교체 | `HHKDB669105C0` | 액면가가 바뀌며 주식 수와 주가가 같이 갈린다 |
| 합병·분할 | `HHKDB669104C0` | 합병비율만큼 주식 수가 바뀐다 |
| 무상증자 | `HHKDB669101C0` | 공짜로 주식이 늘어 그만큼 주가가 내려앉는다(권리락) |
| 유상증자 | `HHKDB669100C0` | 시세보다 싸게 발행하면 그만큼 주가가 내려앉는다 |
| 자본감소 | `HHKDB669106C0` | 주식 수가 줄며 주가가 올라 보인다(무상감자) |

전부 **전 종목 × 기간을 한 콜**에 준다(`SHT_CD` 를 비우면 전체). 종목마다 묻지 않는다.

## 한 콜에 100행이 천장이다 — 그래서 창을 쪼갠다

이어받기(`CTS`)가 응답에 안 온다(실측: 본문에 `rt_cd`·`msg_cd`·`msg1` 뿐). 100행이 차면
그 뒤는 그냥 잘린다. 그래서 **날짜 창을 좁히는 것 말고 방법이 없다.**

    2026-01 한 달   6행     창이 넉넉하다
    2026-08 한 달  62행     아직 넉넉하다
    2020 한 해    100행     ← 천장. 잘렸다
    1995 한 해      0행     이 API 는 이만큼 과거를 안 준다

`backfill_kis_corp_actions` 가 해 단위로 묻고, 100행이 차면 **반으로 쪼개 다시 묻는다.**

**조회만 한다. 주문 없음.**
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# 한 콜이 돌려주는 최대 행. 이 수가 오면 잘린 것으로 보고 창을 쪼갠다.
PAGE_CAP = 100


@dataclass(frozen=True)
class Spec:
    """사건 하나를 어디서 어떻게 받나."""

    key: str          # 저장 파일 이름
    label: str        # 사람이 읽는 이름
    path: str
    tr: str
    keys: tuple[str, ...]                       # 같은 줄인지 가르는 열
    extra: dict[str, str] = field(default_factory=dict)  # 이 TR 만 쓰는 파라미터


SPECS: tuple[Spec, ...] = (
    Spec(
        key="rev_split",
        label="액면교체",
        path="/uapi/domestic-stock/v1/ksdinfo/rev-split",
        tr="HHKDB669105C0",
        keys=("record_date", "sht_cd"),
        extra={"MARKET_GB": "0"},  # 0 전체 · 1 코스피 · 2 코스닥
    ),
    Spec(
        key="merger_split",
        label="합병·분할",
        path="/uapi/domestic-stock/v1/ksdinfo/merger-split",
        tr="HHKDB669104C0",
        keys=("record_date", "sht_cd", "cust_cd", "seq"),  # 한 날 여러 건이 온다
    ),
    Spec(
        key="bonus_issue",
        label="무상증자",
        path="/uapi/domestic-stock/v1/ksdinfo/bonus-issue",
        tr="HHKDB669101C0",
        keys=("record_date", "sht_cd", "stk_kind"),
    ),
    Spec(
        key="paidin_capin",
        label="유상증자",
        path="/uapi/domestic-stock/v1/ksdinfo/paidin-capin",
        tr="HHKDB669100C0",
        keys=("record_date", "sht_cd", "stk_kind"),
        extra={"GB1": "2"},  # 1 청약일별 · 2 기준일별 — 우리는 기준일이 필요하다
    ),
    Spec(
        key="cap_dcrs",
        label="자본감소",
        path="/uapi/domestic-stock/v1/ksdinfo/cap-dcrs",
        tr="HHKDB669106C0",
        keys=("record_date", "sht_cd", "stk_kind"),
    ),
)

BY_KEY = {s.key: s for s in SPECS}


def fetch(client, spec: Spec, f_dt: str, t_dt: str, code: str = "") -> list[dict]:
    """한 창의 사건 줄. `code` 를 비우면 전 종목이다.

    천장(100행)에 닿았는지는 부르는 쪽이 길이를 보고 판단한다 — 여기서는 자르지 않는다.
    """
    params = {"CTS": "", "F_DT": f_dt, "T_DT": t_dt, "SHT_CD": code}
    params.update(spec.extra)
    rows = client.get(spec.path, spec.tr, params).body.get("output1") or []
    if isinstance(rows, dict):
        rows = [rows]
    return [
        {k: str(v).strip() for k, v in r.items()}
        for r in rows
        if isinstance(r, dict) and str(r.get("record_date", "")).strip()
    ]


def to_frame(rows: list[dict], spec: Spec) -> pd.DataFrame:
    """받은 줄을 표로. 전부 글자로 담는다 — 원본을 그대로 남기려고."""
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).astype("string")
    have = [k for k in spec.keys if k in frame.columns]
    return (
        frame.drop_duplicates(subset=have or None, keep="last")
        .sort_values(have or list(frame.columns))
        .reset_index(drop=True)
    )


__all__ = ["BY_KEY", "PAGE_CAP", "SPECS", "Spec", "fetch", "to_frame"]
