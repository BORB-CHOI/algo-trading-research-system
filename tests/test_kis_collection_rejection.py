from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from src.layer1_market_data.brokers.kis.auth import TokenIssueError
from src.layer1_market_data.brokers.kis.client import KisApiError, KisResponseError


@pytest.mark.parametrize("auth_failure", [False, True])
def test_rejected_api_stops_remaining_symbols_without_marking_empty(monkeypatch, tmp_path, auth_failure):
    import scripts.update_data as update

    codes = [f"{i:06d}" for i in range(100)]
    monkeypatch.setattr(update.bars, "load_master", lambda _: pd.DataFrame({"sCode": codes}))
    monkeypatch.setattr(update.listing, "stock_codes", lambda _: set(codes))
    monkeypatch.setattr(update, "last_date_of", lambda *_: "20260915")
    monkeypatch.setattr(update.parquet_io, "read", lambda _: pd.DataFrame())
    get_client = Mock(side_effect=TokenIssueError("토큰 발급 HTTP 302")) if auth_failure else Mock(return_value=object())
    monkeypatch.setattr(update, "kis_client_for", get_client)
    monkeypatch.setattr(update, "KIS_UPDATE_WORKERS", 1)
    monkeypatch.setattr(update.time, "sleep", lambda _: None)
    collect = Mock(side_effect=KisResponseError("KIS HTTP 302"))
    save = Mock()
    module = SimpleNamespace(
        KisApiError=KisApiError, load_state=lambda: {}, load_partial=lambda: {},
        collect_code=collect, save_state=save,
    )
    result = update.update_kis(module, tmp_path, "date", "수급", "20261109")
    assert collect.call_count == (0 if auth_failure else 1)
    assert get_client.call_count == 1
    assert result["provider_unavailable"] is True
    assert result["errors"] == 1
    assert result["empty_responses"] == 0
    assert result["called"] == 0
    save.assert_not_called()


@pytest.mark.parametrize("auth_failure", [False, True])
def test_rejected_supply_does_not_start_other_kis_collectors(monkeypatch, auth_failure):
    import scripts.update_data as update

    collect = Mock(return_value={"provider_unavailable": True, "error": "KIS HTTP 302"})
    if auth_failure:
        collect.side_effect = TokenIssueError("토큰 발급 HTTP 302")
    members = Mock()
    market = Mock()
    monkeypatch.setattr(update, "update_kis", collect)
    monkeypatch.setattr(update.members, "snapshot_all", members)
    monkeypatch.setattr(update.market_state, "update_daily", market)
    result = update._kis_stream("20261109")
    assert collect.call_count == 1
    members.assert_not_called()
    market.assert_not_called()
    for key in ["supply", "credit", "members", "market_state"]:
        assert result[key]["error"]
    assert update.web_update_problems(result)
