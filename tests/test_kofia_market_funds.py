from __future__ import annotations

from pathlib import Path

import pandas as pd


def test_market_funds_rows_use_official_column_meanings() -> None:
    from src.layer1_data.kofia_market_funds import MARKET_FUNDS_COLUMNS, rows_to_frame

    frame = rows_to_frame(
        [
            {
                "TMPV1": "20260828",
                "TMPV2": 99_813_822,
                "TMPV3": 41_379_416,
                "TMPV4": 109_523_053,
                "TMPV5": 930_301,
                "TMPV6": 4_797,
                "TMPV7": 0.5,
            }
        ],
        MARKET_FUNDS_COLUMNS,
    )

    assert frame.to_dict("records") == [
        {
            "date": "20260828",
            "customer_deposits_million_won": 99_813_822,
            "derivatives_deposits_million_won": 41_379_416,
            "customer_rp_balance_million_won": 109_523_053,
            "unsettled_receivables_million_won": 930_301,
            "forced_liquidation_million_won": 4_797,
            "forced_liquidation_ratio_pct": 0.5,
        }
    ]


def test_credit_rows_are_joined_by_date_without_losing_market_funds() -> None:
    from src.layer1_data.kofia_market_funds import (
        CREDIT_COLUMNS,
        MARKET_FUNDS_COLUMNS,
        merge_official_tables,
        rows_to_frame,
    )

    funds = rows_to_frame(
        [{"TMPV1": "20260828", "TMPV2": 10, "TMPV3": 20, "TMPV5": 30}],
        MARKET_FUNDS_COLUMNS,
    )
    credit = rows_to_frame(
        [{"TMPV1": "20260827", "TMPV2": 40}, {"TMPV1": "20260828", "TMPV2": 50}],
        CREDIT_COLUMNS,
    )

    merged = merge_official_tables(funds, credit)

    assert merged["date"].tolist() == ["20260827", "20260828"]
    assert pd.isna(merged.loc[0, "customer_deposits_million_won"])
    assert merged.loc[1, "credit_loans_total_million_won"] == 50


def test_update_replaces_recent_dates_and_is_safe_to_run_twice(tmp_path: Path) -> None:
    from src.layer1_data.kofia_market_funds import Service, update

    calls: list[tuple[str, str, str]] = []

    def fetch(service: Service, since: str, until: str) -> list[dict]:
        calls.append((service.service_id, since, until))
        if service.service_id.endswith("60"):
            return [{"TMPV1": "20260828", "TMPV2": 100, "TMPV3": 20, "TMPV5": 3}]
        return [{"TMPV1": "20260828", "TMPV2": 40}]

    first = update(output_dir=tmp_path, fetch=fetch, until="20260831")
    second = update(output_dir=tmp_path, fetch=fetch, until="20260831")

    saved = pd.read_parquet(tmp_path / "daily.parquet")
    assert first["rows"] == 1
    assert second["rows"] == 1
    assert len(saved) == 1
    assert saved.loc[0, "customer_deposits_million_won"] == 100
    assert saved.loc[0, "credit_loans_total_million_won"] == 40
    assert calls[0][1] == "19980618"
    assert calls[2][1] <= "20260828"


def test_empty_official_response_does_not_erase_saved_data(tmp_path: Path) -> None:
    from src.layer1_data.kofia_market_funds import Service, update

    path = tmp_path / "daily.parquet"
    pd.DataFrame([{"date": "20260828", "customer_deposits_million_won": 100}]).to_parquet(
        path, index=False
    )

    def empty(_service: Service, _since: str, _until: str) -> list[dict]:
        return []

    result = update(output_dir=tmp_path, fetch=empty, until="20260831")

    assert result["last_date"] == "20260828"
    assert pd.read_parquet(path).to_dict("records") == [
        {"date": "20260828", "customer_deposits_million_won": 100}
    ]
