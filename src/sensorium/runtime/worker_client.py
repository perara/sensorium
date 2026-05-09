#!/usr/bin/env python3
from __future__ import annotations

import os

from sensorium.runtime.client import RuntimeEvent
from sensorium.runtime.worker_protocol import (
    open_worker_socket_from_env,
    recv_worker_message,
    send_worker_message,
)


class ManagedControllerSession:
    def __init__(self, sock, *, backend_id: str, device_id: str):
        self.sock = sock
        self.backend_id = backend_id
        self.device_id = device_id

    def attach(self, device_ids: list[str]):
        return {"backend_id": self.backend_id, "device_ids": list(device_ids), "managed": True}

    def detach(self, device_ids: list[str] | None = None):
        return {
            "backend_id": self.backend_id,
            "device_ids": list(device_ids or []),
            "managed": True,
        }

    def next_event(self, timeout: float = 30.0):
        while True:
            message = recv_worker_message(self.sock, timeout=timeout)
            if message is None:
                return None
            msg_type = message.get("type")
            if msg_type == "event":
                return RuntimeEvent.from_dict(message.get("event"))
            if msg_type == "shutdown":
                return None

    def heartbeat(self):
        send_worker_message(self.sock, {"type": "heartbeat"})
        return {"backend_id": self.backend_id, "managed": True}

    def reply_ok(self, event: RuntimeEvent | int, data: str = ""):
        request_id = event if isinstance(event, int) else event.request_id
        send_worker_message(
            self.sock,
            {"type": "reply", "request_id": int(request_id), "status": 0, "data": data},
        )
        return {"request_id": int(request_id)}

    def reply_error(self, event: RuntimeEvent | int, status: int, data: str = ""):
        request_id = event if isinstance(event, int) else event.request_id
        send_worker_message(
            self.sock,
            {
                "type": "reply",
                "request_id": int(request_id),
                "status": int(status),
                "data": data,
            },
        )
        return {"request_id": int(request_id)}


def connect_managed_controller_session():
    sock = open_worker_socket_from_env()
    if sock is None:
        return None
    return ManagedControllerSession(
        sock,
        backend_id=os.environ.get("SENSORIUM_WORKER_BACKEND_ID", ""),
        device_id=os.environ.get("SENSORIUM_WORKER_DEVICE_ID", ""),
    )
