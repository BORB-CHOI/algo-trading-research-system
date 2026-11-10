"""수집 진단 기록. 원문/인증값 대신 허용된 메타데이터만 큐에 넣는다.

파일 기록 정본: docs/operations/collection-logging.md.
프로세스마다 파일을 분리해 Windows의 회전 충돌을 피한다.
"""

from __future__ import annotations

import atexit
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

from pythonjsonlogger.json import JsonFormatter

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "data" / "logs" / "collection"
_lock = threading.RLock()
_logger = logging.getLogger("ats.collection.audit")
_logger.propagate = False
_listener: QueueListener | None = None
_file: RotatingFileHandler | None = None


@dataclass
class _Run:
    run_id: str
    counts: Counter = field(default_factory=Counter)


_run: ContextVar[_Run | None] = ContextVar("collection_run", default=None)
_records: queue.Queue | None = None


class CollectionThreadPoolExecutor(ThreadPoolExecutor):
    """작업 제출 시 회차 문맥을 복사한다. 이전 작업은 이전 ID를 유지한다."""

    def submit(self, fn, /, *args, **kwargs):
        context = copy_context()
        return super().submit(context.run, fn, *args, **kwargs)


_dropped = 0
_write_errors = 0
_secrets: set[str] = set()
_FIELDS = frozenset(
    {
        "endpoint",
        "tr_id",
        "symbol",
        "account_ref",
        "environment",
        "http_status",
        "content_type",
        "redirect_path",
        "provider_code",
        "rt_cd",
        "attempt",
        "duration_ms",
        "error_type",
        "expires_at",
        "reason",
        "response_kind",
        "ok",
        "skipped",
        "git_commit",
        "git_dirty",
        "counts",
        "dropped",
        "write_errors",
        "phase",
        "done",
        "total",
        "cache_present",
    }
)


def register_secrets(*values: str) -> None:
    """인증값이 허용된 문자열 필드로 흘러와도 기록하지 않는다."""
    with _lock:
        _secrets.update(v for v in values if isinstance(v, str) and len(v) >= 4)


def _clean(value):
    if isinstance(value, str):
        for secret in sorted(_secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", value)
        return value[:512]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items() if isinstance(v, (int, bool))}
    return "[OMITTED]"


class _QueueHandler(QueueHandler):
    def enqueue(self, record):
        global _dropped
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            _dropped += 1
            if _dropped == 1:
                print("ATS diagnostic log queue full; records are being dropped", file=sys.stderr)


class _FileHandler(RotatingFileHandler):
    def handleError(self, record):
        global _write_errors
        _write_errors += 1
        if _write_errors == 1:
            print("ATS diagnostic log write failed; check disk and permissions", file=sys.stderr)


def configure(
    directory: Path = LOG_DIR, *, max_bytes: int = 10 * 1024 * 1024, backups: int = 5
) -> None:
    """한 번만 초기화. 파일당 10 MiB, 현재 파일 + 백업 5개. 재시작 때 30일 정리."""
    global _listener, _file, _records
    with _lock:
        if _listener is not None:
            return
        directory.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 30 * 86400
        for old in directory.glob("collection-*.jsonl*"):
            try:
                if old.is_file() and not old.is_symlink() and old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                print("ATS old diagnostic log cleanup failed", file=sys.stderr)
        name = f"collection-{datetime.now(UTC):%Y%m%dT%H%M%S}-{os.getpid()}-{uuid.uuid4().hex[:8]}.jsonl"
        _file = _FileHandler(
            directory / name, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        _file.setFormatter(JsonFormatter(json_ensure_ascii=False))
        records: queue.Queue = queue.Queue(maxsize=10000)
        _records = records
        _logger.handlers = [_QueueHandler(records)]
        _logger.setLevel(logging.INFO)
        _listener = QueueListener(records, _file)
        _listener.start()


def event(name: str, **fields) -> None:
    """원문 메시지/헤더/파라미터/예외 문자열은 받지 않는 별도 진단 채널."""
    global _dropped
    with _lock:
        try:
            configure()
        except OSError:
            _dropped += 1
            if _dropped == 1:
                print(
                    "ATS diagnostic logging unavailable; check disk and permissions",
                    file=sys.stderr,
                )
            return
        current = _run.get()
        if current is not None:
            current.counts[name] += 1
        data = {key: _clean(value) for key, value in fields.items() if key in _FIELDS}
        data.update(
            timestamp=datetime.now(UTC).isoformat(),
            event=name,
            run_id=current.run_id if current else None,
            pid=os.getpid(),
            worker_thread=threading.current_thread().name,
        )
        level = (
            logging.ERROR if name.endswith("failed") or fields.get("ok") is False else logging.INFO
        )
        data["severity"] = logging.getLevelName(level)
        _logger.log(level, "collection", extra=data)


def response_fields(status: int, headers, body: dict) -> dict:
    """응답 전체는 저장하지 않는다. 리다이렉트의 query/fragment도 버린다."""
    lower = {str(k).lower(): v for k, v in headers.items()}
    location = lower.get("location", "")
    try:
        redirect = urlsplit(location).path if isinstance(location, str) else None
    except ValueError:
        redirect = "[INVALID]"
    code = body.get("msg_cd") or body.get("error_code")
    return {
        "http_status": status,
        "content_type": lower.get("content-type"),
        "redirect_path": redirect,
        "provider_code": code,
        "rt_cd": body.get("rt_cd"),
        "response_kind": "redirect"
        if 300 <= status < 400
        else "json_object"
        if body
        else "empty_or_non_object",
    }


def begin_run() -> str:
    """수집 잠금을 획득한 프로세스에서만 호출. 스레드 풀에도 같은 회차 ID 제공."""
    with _lock:
        current = _Run(uuid.uuid4().hex)
        _run.set(current)
        version: dict[str, str | bool] = {}
        try:
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=3
            )
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if commit.returncode == 0:
                version["git_commit"] = commit.stdout.strip()
            if dirty.returncode == 0:
                version["git_dirty"] = bool(dirty.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
        event("collection.started", **version)
        return current.run_id


def end_run(*, ok: bool, error_type: str | None = None) -> None:
    with _lock:
        current = _run.get()
        event(
            "collection.finished",
            ok=ok,
            error_type=error_type,
            counts=dict(current.counts) if current else {},
            dropped=_dropped,
            write_errors=_write_errors,
        )
        _run.set(None)
    flush()


def flush() -> None:
    """큐 처리를 기다린다. 전원 차단에 대한 디스크 영속성(fsync)은 보장하지 않는다."""
    if _records is not None:
        _records.join()


def shutdown() -> None:
    global _listener, _file, _records
    with _lock:
        if _listener:
            flush()
            _listener.stop()
            _listener = None
            _records = None
        for handler in _logger.handlers:
            handler.close()
        _logger.handlers.clear()
        if _file:
            _file.close()
            _file = None


atexit.register(shutdown)
