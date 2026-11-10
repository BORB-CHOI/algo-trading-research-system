import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from src import collection_logging as audit
from src.layer1_market_data.brokers.kis import auth, client


@pytest.fixture
def logs(tmp_path):
    audit.shutdown()
    audit.configure(tmp_path)
    yield tmp_path
    audit.shutdown()


def read_logs(path):
    audit.flush()
    return [
        json.loads(line)
        for p in path.glob("*.jsonl")
        for line in p.read_text(encoding="utf-8").splitlines()
    ]


def test_redaction_allowlist_and_query_removal(logs):
    audit.register_secrets("secret-app-key", "secret-token")
    audit.event(
        "kis.request_failed",
        endpoint="/secret-app-key",
        authorization="Bearer secret-token",
        body={"account": "12345678"},
        **audit.response_fields(302, {"Location": "/error.html?token=secret-token#private"}, {}),
    )
    row = read_logs(logs)[0]
    assert row["endpoint"] == "/[REDACTED]"
    assert row["redirect_path"] == "/error.html"
    assert "authorization" not in row and "body" not in row
    assert "secret-token" not in json.dumps(row)


def test_cache_hit_does_not_issue_token_and_is_logged(logs, tmp_path):
    creds = auth.KisCredentials("test-key", "test-secret", "real")
    token = auth.AccessToken("cached-token", "Bearer", datetime.now(UTC) + timedelta(hours=5))
    cache = tmp_path / "token.json"
    auth.save_token(cache, token, creds)
    issue = Mock()
    assert auth.get_access_token(creds, cache, issue=issue) == token
    issue.assert_not_called()
    assert read_logs(logs)[0]["event"] == "kis.token_cache_used"


def test_threads_share_run_and_complete_counts(logs):
    run_id = audit.begin_run()
    with audit.CollectionThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: audit.event("kis.request_succeeded"), range(40)))
    audit.end_run(ok=True)
    rows = read_logs(logs)
    assert {row["run_id"] for row in rows} == {run_id}
    assert rows[-1]["counts"]["kis.request_succeeded"] == 40
    audit.event("outside_run")
    assert read_logs(logs)[-1]["run_id"] is None


def test_rotation_bounds_files(tmp_path):
    audit.shutdown()
    audit.configure(tmp_path, max_bytes=800, backups=2)
    for _ in range(50):
        audit.event("kis.request_failed", endpoint="/query")
    audit.shutdown()
    assert len(list(tmp_path.glob("collection-*.jsonl*"))) == 3


def test_late_worker_keeps_previous_run_and_unrelated_thread_has_none(logs):
    first = audit.begin_run()
    release = threading.Event()
    with audit.CollectionThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: (release.wait(5), audit.event("late_worker")))
        audit.end_run(ok=False)
        second = audit.begin_run()
        with ThreadPoolExecutor(max_workers=1) as outside:
            outside.submit(audit.event, "unrelated").result()
        release.set()
        future.result()
        audit.end_run(ok=True)
    rows = read_logs(logs)
    assert next(r for r in rows if r["event"] == "late_worker")["run_id"] == first
    assert next(r for r in rows if r["event"] == "unrelated")["run_id"] is None
    last = [r for r in rows if r["event"] == "collection.finished"][-1]
    assert last["run_id"] == second and "late_worker" not in last["counts"]


def test_log_init_failure_does_not_break_collection(monkeypatch, capsys):
    audit.shutdown()
    monkeypatch.setattr(audit, "_dropped", 0)
    monkeypatch.setattr(audit, "configure", Mock(side_effect=PermissionError()))
    audit.event("kis.request_failed")
    assert audit._dropped == 1
    assert "unavailable" in capsys.readouterr().err


def test_queue_full_counts_loss_without_logging_record(capsys):
    import logging
    import queue

    records = queue.Queue(maxsize=1)
    handler = audit._QueueHandler(records)
    record = logging.LogRecord("test", logging.INFO, "", 0, "not printed", (), None)
    before = audit._dropped
    handler.enqueue(record)
    handler.enqueue(record)
    assert audit._dropped == before + 1
    assert "not printed" not in capsys.readouterr().err


def test_retention_only_removes_old_owned_logs(tmp_path):
    import os
    import time

    audit.shutdown()
    old = tmp_path / "collection-old.jsonl.1"
    unrelated = tmp_path / "important.txt"
    for path in (old, unrelated):
        path.write_text("old", encoding="utf-8")
        os.utime(path, (time.time() - 31 * 86400,) * 2)
    audit.configure(tmp_path)
    audit.shutdown()
    assert not old.exists() and unrelated.exists()


def test_transport_error_logs_type_without_exception_message(logs):
    creds = auth.KisCredentials("test-key", "test-secret", "real")
    token = auth.AccessToken("cached-token", "Bearer", datetime.now(UTC))
    transport = Mock(side_effect=TimeoutError("sensitive-account-number"))
    with pytest.raises(TimeoutError):
        client.KisClient(creds, token, transport=transport).get("/query", "TR")
    rows = read_logs(logs)
    assert rows[0]["error_type"] == "TimeoutError"
    assert "sensitive-account-number" not in json.dumps(rows)


def test_run_wrapper_records_failure_and_releases_lock(logs, monkeypatch):
    from scripts import update_data

    monkeypatch.setattr(update_data, "_run_update", Mock(side_effect=ValueError("private")))
    with pytest.raises(ValueError):
        update_data.run_update()
    assert not update_data._DIAGNOSTIC_RUN_LOCK.locked()
    row = read_logs(logs)[-1]
    assert row["event"] == "collection.finished"
    assert row["ok"] is False and row["error_type"] == "ValueError"
    assert "private" not in json.dumps(row)
