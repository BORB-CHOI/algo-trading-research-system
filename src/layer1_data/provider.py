"""전략에 데이터를 건네는 창구 하나 (미션 문서 §19-4, ADR-0019 후속).

전략이 `data/derived/adjusted/{code}.parquet` 같은 **파일 경로를 직접 알면**, 데이터
위치나 형식이 바뀔 때마다 전략을 다 고쳐야 한다. 그래서 창구를 하나 둔다.

    전략 → 데이터 창구 → 실제 데이터

`as_of` 를 주면 **그날 뒤 행을 잘라서** 준다. "그때 알 수 있었던 정보만"(지침서 §4.1)을
전략이 아니라 창구가 강제한다 — 전략마다 따로 지키면 언젠가 하나가 빠진다.

## ⚠️ 수급에는 망한 회사가 없다

실측 2026-08-16: 2010년엔 있었고 2025년엔 없는 종목 595개 중 **수급 파일이 있는 것은 0개**
(일봉은 595개 전부 있다). KIS API 가 상장 종목만 주기 때문이다.

수급 조건으로 과거를 검사하면 망한 회사가 표본에서 빠져 **결과가 실제보다 좋게 나온다** —
CLAUDE.md 가 금지한 "살아남은 것만 보는 착시"다. `supply_coverage()` 로 얼마나 빠졌는지
셀 수 있다. **고치지 않고 알린다** — 어떻게 할지는 오너가 정한다(지침서 §12).

## 지금은 창구만 둔다

기존 호출부(`derived.load_adjusted` 등)를 바꾸지 않는다. 한 번에 다 바꾸면 무엇이 깨졌는지
알 수 없다. 호출부 이전은 다음 작업.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_ROOT = Path("data/derived")


@dataclass(frozen=True)
class DataProvider:
    """읽기 창구. 값을 바꾸지 않는다 — 늘 새 표를 돌려준다."""

    root: Path = DEFAULT_ROOT

    # ── 종목별 ────────────────────────────────────────────────

    def daily(self, code: str | int, *, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
        """한 종목의 수정주가 일봉. `as_of` 이후 행은 잘라 낸다.

        상장폐지 종목도 자기 시절 행이 그대로 있다(지침서 §4.2).
        """
        path = self._path("adjusted", code)
        if not path.exists():
            raise FileNotFoundError(f"{self._code(code)} 의 일봉이 없습니다 — {path}")
        return self._cut(pd.read_parquet(path), as_of)

    def supply(self, code: str | int, *, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
        """한 종목의 외인·기관·개인 수급 (ADR-0012).

        **상장폐지 종목은 없다** — KIS 가 상장 종목만 주기 때문이다. 위 모듈 설명 참조.
        """
        path = self._path("supply", code)
        if not path.exists():
            raise FileNotFoundError(
                f"{self._code(code)} 의 수급이 없습니다 — {path} "
                f"(상장폐지된 종목은 수급을 받을 수 없습니다)"
            )
        return self._cut(pd.read_parquet(path), as_of, date_col=self._supply_date_col)

    # ── 전 종목 ───────────────────────────────────────────────

    def panel(self, y0: int, y1: int) -> pd.DataFrame:
        """전 종목 × 날짜 표 (marcap). 정본 로더를 그대로 부른다."""
        from src.layer1_data.marcap_loader import load_years

        return load_years(y0, y1)

    # ── 데이터가 얼마나 있나 ──────────────────────────────────

    def supply_coverage(self) -> dict[str, int]:
        """수급이 몇 종목이나 빠졌는지 — 화면 경고에 쓴다.

        일봉은 있는데 수급이 없는 종목이 곧 "과거 검사에서 빠지는 회사"다.
        """
        adj = {p.stem for p in (self.root / "adjusted").glob("*.parquet")}
        sup = {p.stem for p in (self.root / "supply").glob("*.parquet")}
        return {"일봉": len(adj), "수급": len(sup), "수급_없는_종목": len(adj - sup)}

    # ── 내부 ──────────────────────────────────────────────────

    _supply_date_col = "stck_bsop_date"

    @staticmethod
    def _code(code: str | int) -> str:
        return str(code).zfill(6)

    def _path(self, kind: str, code: str | int) -> Path:
        return self.root / kind / f"{self._code(code)}.parquet"

    @staticmethod
    def _cut(
        df: pd.DataFrame, as_of: pd.Timestamp | None, *, date_col: str = "Date"
    ) -> pd.DataFrame:
        if as_of is None or date_col not in df.columns:
            return df.reset_index(drop=True)
        col = df[date_col]
        if not pd.api.types.is_datetime64_any_dtype(col):
            # 수급은 날짜가 '19950427' 같은 문자열이다 — 비교 전에 날짜로 바꾼다.
            col = pd.to_datetime(col, format="%Y%m%d", errors="coerce")
        return df.loc[col <= pd.Timestamp(as_of)].reset_index(drop=True)
