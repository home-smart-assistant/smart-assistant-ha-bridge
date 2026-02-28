import json
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core import settings
from app.models.schemas import OperationLogItem, UiActionLogRequest


_DETAIL_MAX_CHARS = 4000
_CLEANUP_INTERVAL_SEC = 60.0
_WRITE_IDLE_TIMEOUT_SEC = 0.25
_WRITE_BATCH_MAX = 200

_last_cleanup_at = 0.0
_queue: queue.Queue[OperationLogItem] = queue.Queue(maxsize=settings.HA_LOG_QUEUE_MAX)
_worker_thread: threading.Thread | None = None
_stop_event = threading.Event()
_worker_state_lock = threading.Lock()
_dropped_count = 0


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _backup_path(index: int) -> Path:
    return settings.HA_LOG_PATH.with_name(f"{settings.HA_LOG_PATH.name}.{index}")


def _ensure_log_dir() -> None:
    settings.HA_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _compress_detail(detail: Any) -> dict[str, Any]:
    if isinstance(detail, dict):
        data = detail
    else:
        data = {"value": detail}

    try:
        raw = json.dumps(data, ensure_ascii=False)
    except TypeError:
        raw = json.dumps({"value": str(data)}, ensure_ascii=False)

    if len(raw) <= _DETAIL_MAX_CHARS:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    return {
        "_truncated": True,
        "_size": len(raw),
        "preview": raw[:_DETAIL_MAX_CHARS],
    }


def _rotate_if_needed() -> None:
    path = settings.HA_LOG_PATH
    if not path.exists():
        return
    if path.stat().st_size < settings.HA_LOG_MAX_BYTES:
        return

    tail = _backup_path(settings.HA_LOG_BACKUP_COUNT)
    if tail.exists():
        tail.unlink(missing_ok=True)

    for idx in range(settings.HA_LOG_BACKUP_COUNT - 1, 0, -1):
        src = _backup_path(idx)
        dst = _backup_path(idx + 1)
        if src.exists():
            src.replace(dst)

    path.replace(_backup_path(1))


def _cleanup_backups(force: bool = False) -> None:
    global _last_cleanup_at
    now = time.time()
    if not force and now - _last_cleanup_at < _CLEANUP_INTERVAL_SEC:
        return

    _last_cleanup_at = now
    expire_before = now - (settings.HA_LOG_RETENTION_DAYS * 24 * 3600)

    pattern = f"{settings.HA_LOG_PATH.name}.*"
    for candidate in settings.HA_LOG_PATH.parent.glob(pattern):
        suffix = candidate.name.replace(f"{settings.HA_LOG_PATH.name}.", "", 1)
        if not suffix.isdigit():
            continue

        idx = int(suffix)
        too_many = idx > settings.HA_LOG_BACKUP_COUNT
        too_old = candidate.stat().st_mtime < expire_before
        if too_many or too_old:
            candidate.unlink(missing_ok=True)


def _write_batch(entries: list[OperationLogItem]) -> None:
    if not entries:
        return

    lines = [json.dumps(entry.model_dump(mode="json"), ensure_ascii=False) for entry in entries]
    with settings.log_lock:
        _ensure_log_dir()
        _rotate_if_needed()
        _cleanup_backups()
        with settings.HA_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
            f.write("\n")


def _flush_queue_batch() -> int:
    entries: list[OperationLogItem] = []
    try:
        first = _queue.get(timeout=_WRITE_IDLE_TIMEOUT_SEC)
        entries.append(first)
    except queue.Empty:
        return 0

    while len(entries) < _WRITE_BATCH_MAX:
        try:
            entries.append(_queue.get_nowait())
        except queue.Empty:
            break

    _write_batch(entries)
    for _ in entries:
        _queue.task_done()
    return len(entries)


def _log_worker_loop() -> None:
    while not _stop_event.is_set() or not _queue.empty():
        try:
            _flush_queue_batch()
        except Exception:
            # Logging errors must not terminate worker.
            time.sleep(0.05)


def start_log_worker() -> None:
    global _worker_thread
    with _worker_state_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _stop_event.clear()
        _worker_thread = threading.Thread(
            target=_log_worker_loop,
            name="ha-bridge-log-worker",
            daemon=True,
        )
        _worker_thread.start()


def stop_log_worker(timeout_sec: float = 2.0) -> None:
    global _worker_thread
    with _worker_state_lock:
        worker = _worker_thread
        if not worker:
            return
        _stop_event.set()

    worker.join(timeout=timeout_sec)
    with _worker_state_lock:
        if _worker_thread is worker:
            _worker_thread = None


def flush_logs(timeout_sec: float = 0.3) -> None:
    start = time.perf_counter()
    while _queue.unfinished_tasks > 0 and (time.perf_counter() - start) < timeout_sec:
        time.sleep(0.01)


def _enqueue(entry: OperationLogItem) -> bool:
    global _dropped_count
    start_log_worker()
    try:
        _queue.put_nowait(entry)
        return True
    except queue.Full:
        with _worker_state_lock:
            _dropped_count += 1
        return False


def log_operation(
    *,
    event_type: str,
    source: str,
    action: str,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    duration_ms: float | None = None,
    client_ip: str | None = None,
    trace_id: str | None = None,
    success: bool | None = None,
    detail: Any = None,
) -> OperationLogItem:
    item = OperationLogItem(
        event_id=uuid4().hex,
        created_at=_now_iso(),
        event_type=event_type,
        source=source,
        action=action,
        method=method,
        path=path,
        status_code=status_code,
        duration_ms=duration_ms,
        client_ip=client_ip,
        trace_id=trace_id,
        success=success,
        detail=_compress_detail(detail or {}),
    )
    _enqueue(item)
    return item


def log_http_request(
    *,
    source: str,
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    client_ip: str | None,
    detail: dict[str, Any] | None = None,
) -> OperationLogItem:
    return log_operation(
        event_type="http_request",
        source=source,
        action="http.request",
        method=method,
        path=path,
        status_code=status_code,
        duration_ms=duration_ms,
        client_ip=client_ip,
        success=status_code < 400,
        detail=detail or {},
    )


def log_ui_action(req: UiActionLogRequest, client_ip: str | None) -> OperationLogItem:
    detail = dict(req.detail)
    if req.view:
        detail["view"] = req.view
    return log_operation(
        event_type="ui_action",
        source="ui",
        action=req.action,
        client_ip=client_ip,
        trace_id=req.trace_id,
        success=req.success,
        detail=detail,
    )


def list_recent_logs(
    *,
    limit: int = 200,
    sources: list[str] | None = None,
    source: str | None = None,
    event_type: str | None = None,
) -> list[OperationLogItem]:
    safe_limit = max(1, min(limit, 1000))
    flush_logs(timeout_sec=0.35)
    source_filters = set(sources or [])
    if source:
        source_filters.add(source)

    with settings.log_lock:
        _ensure_log_dir()
        _cleanup_backups(force=True)

        files: list[Path] = []
        if settings.HA_LOG_PATH.exists():
            files.append(settings.HA_LOG_PATH)
        for idx in range(1, settings.HA_LOG_BACKUP_COUNT + 1):
            p = _backup_path(idx)
            if p.exists():
                files.append(p)

        result: list[OperationLogItem] = []
        for file_path in files:
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue

            for line in reversed(lines):
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                    item = OperationLogItem.model_validate(row)
                except Exception:
                    continue

                if source_filters and item.source not in source_filters:
                    continue
                if event_type and item.event_type != event_type:
                    continue

                result.append(item)
                if len(result) >= safe_limit:
                    return result

        return result


def get_log_storage_meta() -> dict[str, Any]:
    with settings.log_lock:
        _ensure_log_dir()
        size = settings.HA_LOG_PATH.stat().st_size if settings.HA_LOG_PATH.exists() else 0
    with _worker_state_lock:
        dropped = _dropped_count
        worker_alive = bool(_worker_thread and _worker_thread.is_alive())
    return {
        "storage": "file",
        "log_path": str(settings.HA_LOG_PATH),
        "current_size_bytes": size,
        "max_bytes": settings.HA_LOG_MAX_BYTES,
        "backup_count": settings.HA_LOG_BACKUP_COUNT,
        "retention_days": settings.HA_LOG_RETENTION_DAYS,
        "queue_max": settings.HA_LOG_QUEUE_MAX,
        "queue_size": _queue.qsize(),
        "dropped_count": dropped,
        "worker_alive": worker_alive,
    }
