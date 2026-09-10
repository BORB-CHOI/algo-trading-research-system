"""거래원(증권사별 매매) — 파싱·읽기 창구.

## 왜 매일 모아야 하나 (실측 2026-08-17)

KIS 회원사 종목매매동향(FHPST04540000)은 날짜 구간을 받지만, 아무리 뒤로 요청해도
**260 거래일**까지만 준다. 실측:

    2026-01-01 ~ 2026-08-14  →  152행 (구간 전체)
    2025-01-01 ~ 2026-08-14  →  260행, 가장 오래된 2025-07-23
    2020-01-01 ~ 2026-08-14  →  260행, 가장 오래된 2025-07-23  ← 더 안 준다

신용잔고(바닥 2007-07-12)·분봉(약 6주)과 같은 보관 한계다. 하루가 지나면 하루치가
뒤에서 사라지므로 **지금부터 매일 받아 쌓지 않으면 영영 못 구한다.**

나무 PLUG 에는 거래원 API 가 없다 — KIS 가 유일한 창구다.

## 두 가지를 모은다

- **당일 상위 5** (`inquire-member`): 매도·매수 상위 5개 증권사 + 수량·비중·전일대비 증감.
  전 종목을 매일 한 번. 응답에 코드와 이름이 함께 오므로 거래원 이름 사전이 저절로 찬다.
- **일자별** (`inquire-member-daily`): 종목 × 회원사 × 날짜. 상위 5위 밖 증권사까지 보이지만
  회원사코드를 하나씩 지정해야 해서 호출이 종목수 × 회원사수로 불어난다.

**조회만 한다.** 주문은 이 경로에 없다(CLAUDE.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

DERIVED = Path("data/derived/members")
SNAP_DIR = DERIVED / "snapshot"
DAILY_DIR = DERIVED / "daily"
MEMBERS_PATH = DERIVED / "_members.json"

SIDES = ("매도", "매수")
_SIDE_PREFIX = {"매도": "seln", "매수": "shnu"}

_DAILY_COLS = ["date", "code", "member_code", "sell_qty", "buy_qty", "net_qty", "close", "acc_vol"]


def _int(v: Any) -> int:
    try:
        return int(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0


def _float(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _ymd(v: Any) -> str:
    s = str(v).strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


# ── 파싱 ──────────────────────────────────────────────────────


def parse_snapshot(output: dict, code: str, date: str) -> list[dict]:
    """당일 상위 5 응답을 매도·매수 한 표로 편다. 빈 자리는 줄을 안 만든다."""
    rows: list[dict] = []
    for side in SIDES:
        p = _SIDE_PREFIX[side]
        for i in range(1, 6):
            no = str(output.get(f"{p}_mbcr_no{i}") or "").strip()
            if not no:
                continue
            rows.append(
                {
                    "date": date,
                    "code": code,
                    "side": side,
                    "rank": i,
                    "member_code": no,
                    "member_name": str(output.get(f"{p}_mbcr_name{i}") or "").strip(),
                    "qty": _int(output.get(f"total_{p}_qty{i}")),
                    "ratio": _float(output.get(f"{p}_mbcr_rlim{i}")),
                    "chg": _int(output.get(f"{p}_qty_icdc{i}")),
                }
            )
    return rows


def parse_daily(output: list[dict], code: str, member_code: str) -> list[dict]:
    """일자별 응답을 표 줄로. 그 회원사가 그 종목을 안 만진 날(전부 0)은 버린다."""
    rows: list[dict] = []
    for it in output or []:
        sell, buy = _int(it.get("total_seln_qty")), _int(it.get("total_shnu_qty"))
        if sell == 0 and buy == 0:
            continue
        rows.append(
            {
                "date": _ymd(it.get("stck_bsop_date")),
                "code": code,
                "member_code": member_code,
                "sell_qty": sell,
                "buy_qty": buy,
                "net_qty": _int(it.get("ntby_qty")),
                "close": _int(it.get("stck_prpr")),
                "acc_vol": _int(it.get("acml_vol")),
            }
        )
    return rows


def merge_member_names(known: dict[str, str], output: dict) -> dict[str, str]:
    """스냅샷 응답에서 거래원 이름을 주워 사전을 채운다. 이미 있는 이름은 안 덮는다."""
    out = dict(known)
    for side in SIDES:
        p = _SIDE_PREFIX[side]
        for i in range(1, 6):
            no = str(output.get(f"{p}_mbcr_no{i}") or "").strip()
            nm = str(output.get(f"{p}_mbcr_name{i}") or "").strip()
            if no and nm and not out.get(no):
                out[no] = nm
    return out


# ── 읽기 창구 ─────────────────────────────────────────────────


def load_daily(code: str, *, root: Path = DAILY_DIR) -> pd.DataFrame | None:
    """한 종목의 일자별 거래원 표. 없으면 None."""
    path = Path(root) / f"{str(code).strip().zfill(6)}.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


def load_snapshot(date: str, *, root: Path = SNAP_DIR) -> pd.DataFrame | None:
    """그날 전 종목의 상위 5 표. 없으면 None."""
    path = Path(root) / f"{date}.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


def daily_frame(rows: list[dict]) -> pd.DataFrame:
    """열 순서를 고정한 표 — 저장 형식이 흔들리면 나중에 읽는 쪽이 깨진다."""
    return pd.DataFrame(rows, columns=_DAILY_COLS)
