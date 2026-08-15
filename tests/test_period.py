"""검사 구간 — 화면에서 고른 날짜가 그대로 쓰인다. 코드가 구간을 강제하지 않는다.

오너 결정 2026-08-16: "2007~ 나누지 않고 전체."
연습/검증/시험 3분할은 쓰지 않는다 (ADR-0019).
"""

import pandas as pd
import pytest

from src.layer4_execution.backtest import DEFAULT_START, resolve_period


class Test검사_구간:
    def test_화면이_준_날짜를_그대로_쓴다(self) -> None:
        got = resolve_period("2007-01-01", "2026-08-03")
        assert got == (pd.Timestamp("2007-01-01"), pd.Timestamp("2026-08-03"))

    def test_시작일을_안_주면_2007년부터(self) -> None:
        start, _ = resolve_period(None, "2026-08-03")
        assert start == pd.Timestamp(DEFAULT_START)

    def test_끝나는_날을_안_주면_준_최신_거래일을_쓴다(self) -> None:
        latest = pd.Timestamp("2026-08-03")
        _, end = resolve_period("2007-01-01", None, latest=latest)
        assert end == latest

    def test_거꾸로_주면_이유를_말한다(self) -> None:
        with pytest.raises(ValueError, match="끝나는 날"):
            resolve_period("2026-01-01", "2007-01-01")

    def test_구간을_쪼개지_않는다(self) -> None:
        """연습·검증·시험으로 자동으로 나누면 안 된다 — 오너가 화면에서 정한다."""
        start, end = resolve_period("2007-01-01", "2026-08-03")
        assert (end - start).days > 7000  # 19년 이상 통으로

    def test_리먼_전부터_본다(self) -> None:
        """기본 시작일은 리먼 사태(2008-09-15) 이전이어야 한다 — 오너 지시."""
        assert pd.Timestamp(DEFAULT_START) < pd.Timestamp("2008-09-15")
