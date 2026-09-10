"""기타 수급 — 대차거래를 날짜별 대량으로 받는지, 앞뒤 빈 자리를 다 채우는지."""

from __future__ import annotations

import pandas as pd


def test_loan_is_not_collected_per_stock() -> None:
    """대차거래를 종목별 갈래에 두면 하루 4,306콜이 든다 — 키움 대량(36콜)으로 옮겼다."""
    from src.layer1_data import kis_extra_supply as flows

    assert "loan" not in {f.key for f in flows.FLOWS}
    assert {f.key for f in flows.FLOWS} == {"short_sale", "program"}


def test_loan_day_asks_every_market_and_stamps_the_date(monkeypatch) -> None:
    """응답에 날짜가 안 들어 있다 — 붙여 주지 않으면 어느 날 것인지 알 수 없다."""
    from src.layer1_data import kiwoom_extra_supply as kx

    asked: list[tuple[str, str]] = []

    def fake_post(_api, _path, payload, _cont, _key):
        asked.append((payload["dt"], payload["mrkt_tp"]))
        return {
            "dbrt_trde_prps": [{"stk_cd": f"00000{len(asked)}", "rmnd": "1"}],
            "_cont": "N",
            "_key": "",
        }

    monkeypatch.setattr(kx, "_post", fake_post)
    frame = kx.loan_day("20260909")

    assert [m for _, m in asked] == list(kx.LOAN_MARKETS), "시장마다 물어야 전 종목이 된다"
    assert set(frame["bsop_date"]) == {"20260909"}
    assert len(frame) == len(kx.LOAN_MARKETS)


def test_loan_day_follows_the_continuation_key(monkeypatch) -> None:
    """한 장에 50줄이 천장이다 — 이어받기를 안 따라가면 그 날 대부분을 잃는다."""
    from src.layer1_data import kiwoom_extra_supply as kx

    pages = {"n": 0}

    def fake_post(_api, _path, payload, cont, key):
        pages["n"] += 1
        more = pages["n"] % 3 != 0  # 시장마다 3장씩
        return {
            "dbrt_trde_prps": [{"stk_cd": f"{pages['n']:06d}", "rmnd": "1"}],
            "_cont": "Y" if more else "N",
            "_key": "next" if more else "",
        }

    monkeypatch.setattr(kx, "_post", fake_post)
    frame = kx.loan_day("20260909")

    assert pages["n"] == 3 * len(kx.LOAN_MARKETS)
    assert len(frame) == 3 * len(kx.LOAN_MARKETS)


def test_missing_windows_fills_both_ends(tmp_path) -> None:
    """앞으로만 늘리면 더 과거를 받으라고 해도 그 종목이 통째로 빠진다."""
    import scripts.backfill_kis_extra_supply as bx
    from src.layer1_data import kis_extra_supply as flows

    flow = flows.BY_KEY["short_sale"]
    path = tmp_path / "005930.parquet"
    pd.DataFrame({flow.date_col: ["20260101", "20260601"]}).to_parquet(path, index=False)

    gaps = bx.missing_windows(path, flow, "005930", "20250101", "20260909", {})

    assert gaps == [("20250101", "20251231"), ("20260602", "20260909")]


def test_missing_windows_says_nothing_to_do_when_covered(tmp_path) -> None:
    import scripts.backfill_kis_extra_supply as bx
    from src.layer1_data import kis_extra_supply as flows

    flow = flows.BY_KEY["short_sale"]
    path = tmp_path / "005930.parquet"
    pd.DataFrame({flow.date_col: ["20250101", "20260909"]}).to_parquet(path, index=False)

    assert bx.missing_windows(path, flow, "005930", "20250101", "20260909", {}) == []
