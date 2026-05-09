#!/usr/bin/env python3
from __future__ import annotations

import collections
import json
import threading
from pathlib import Path

from sensorium.runtime.daemon_support import append_jsonl, rewrite_jsonl


class AsyncTraceWriter:
    def __init__(
        self,
        path: Path | None,
        *,
        file_limit_bytes: int,
        trace_snapshot,
        status_callback,
        queue_limit_records: int,
        queue_limit_bytes: int,
    ):
        self.path = Path(path) if path else None
        self.file_limit_bytes = max(1024, int(file_limit_bytes))
        self.trace_snapshot = trace_snapshot
        self.status_callback = status_callback
        self.queue_limit_records = max(1, int(queue_limit_records))
        self.queue_limit_bytes = max(1024, int(queue_limit_bytes))
        self.cond = threading.Condition()
        self.queue = collections.deque()
        self.queue_bytes = 0
        self.pending_writes = 0
        self.drop_count = 0
        self.max_queue_depth = 0
        self.max_queue_bytes = 0
        self._stop = False
        self.thread: threading.Thread | None = None

    def start(self):
        if self.path is None or self.thread is not None:
            return
        self.thread = threading.Thread(
            target=self._loop,
            name="sensoriumd-trace-writer",
            daemon=True,
        )
        self.thread.start()

    def _serialized_size(self, record: dict) -> int:
        return len(json.dumps(record, sort_keys=False)) + 1

    def enqueue(self, record: dict):
        if self.path is None:
            return
        self.start()
        encoded_size = self._serialized_size(record)
        with self.cond:
            while self.queue and (
                len(self.queue) >= self.queue_limit_records
                or self.queue_bytes + encoded_size > self.queue_limit_bytes
            ):
                _, dropped_size = self.queue.popleft()
                self.queue_bytes -= dropped_size
                self.drop_count += 1
            self.queue.append((record, encoded_size))
            self.queue_bytes += encoded_size
            self.max_queue_depth = max(self.max_queue_depth, len(self.queue))
            self.max_queue_bytes = max(self.max_queue_bytes, self.queue_bytes)
            self.cond.notify_all()

    def flush(self):
        if self.path is None:
            return
        self.start()
        with self.cond:
            while self.queue or self.pending_writes:
                self.cond.wait(timeout=0.1)

    def stop(self):
        if self.thread is None:
            return
        with self.cond:
            self._stop = True
            self.cond.notify_all()
        self.thread.join(timeout=2.0)
        self.thread = None

    def stats(self):
        with self.cond:
            return {
                "queue_depth": len(self.queue),
                "queue_bytes": self.queue_bytes,
                "drop_count": self.drop_count,
                "max_queue_depth": self.max_queue_depth,
                "max_queue_bytes": self.max_queue_bytes,
            }

    def reset_counters(self):
        with self.cond:
            self.drop_count = 0
            self.max_queue_depth = len(self.queue)
            self.max_queue_bytes = self.queue_bytes

    def _write_record(self, record: dict):
        assert self.path is not None
        append_jsonl(self.path, record)
        if self.path.stat().st_size > self.file_limit_bytes:
            rewrite_jsonl(self.path, self.trace_snapshot())
        self.status_callback(None)

    def _loop(self):
        while True:
            with self.cond:
                while not self.queue and not self._stop:
                    self.cond.wait()
                if self._stop and not self.queue:
                    self.cond.notify_all()
                    return
                item, item_size = self.queue.popleft()
                self.queue_bytes -= item_size
                self.pending_writes += 1
            try:
                self._write_record(item)
            except OSError as exc:
                self.status_callback(str(exc))
            finally:
                with self.cond:
                    self.pending_writes -= 1
                    self.cond.notify_all()
