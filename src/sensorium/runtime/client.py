#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from sensorium.runtime.common import (
    normalize_runtime_model,
    rpc_call,
    runtime_socket_path,
)


class RuntimeEvent:
    __slots__ = ("request_id", "transport", "device_id", "bus_id", "backend_id", "op", "payload")

    def __init__(
        self,
        *,
        request_id: int,
        transport: str,
        device_id: str,
        bus_id: str,
        backend_id: str | None,
        op: str | None,
        payload: dict,
    ):
        self.request_id = request_id
        self.transport = transport
        self.device_id = device_id
        self.bus_id = bus_id
        self.backend_id = backend_id
        self.op = op
        self.payload = payload

    @classmethod
    def from_dict(cls, event: dict | None):
        if not event:
            return None
        payload = dict(event)
        request_id = int(payload.pop("request_id"))
        transport = payload.pop("transport")
        device_id = payload.pop("device_id")
        bus_id = payload.pop("bus_id")
        backend_id = payload.pop("backend_id", None)
        op = payload.pop("op", None)
        return cls(
            request_id=request_id,
            transport=transport,
            device_id=device_id,
            bus_id=bus_id,
            backend_id=backend_id,
            op=op,
            payload=payload,
        )


class ControllerSession:
    def __init__(self, client: "SensoriumRuntimeClient", backend_id: str):
        self.client = client
        self.backend_id = backend_id

    def attach(self, device_ids: list[str]):
        return self.client.attach_backend(self.backend_id, device_ids)

    def detach(self, device_ids: list[str] | None = None):
        return self.client.detach_backend(self.backend_id, device_ids)

    def next_event(self, timeout: float = 30.0):
        return self.client.next_event(self.backend_id, timeout=timeout)

    def heartbeat(self):
        return self.client.heartbeat_backend(self.backend_id)

    def reply_ok(self, event: RuntimeEvent | int, data: str = ""):
        request_id = event if isinstance(event, int) else event.request_id
        return self.client.reply(self.backend_id, request_id, status=0, data=data)

    def reply_error(self, event: RuntimeEvent | int, status: int, data: str = ""):
        request_id = event if isinstance(event, int) else event.request_id
        return self.client.reply(self.backend_id, request_id, status=status, data=data)


class SensoriumRuntimeClient:
    def __init__(self, socket_path: Path | None = None, timeout: float = 10.0):
        self.socket_path = runtime_socket_path() if socket_path is None else Path(socket_path)
        self.timeout = timeout

    def _call(self, method: str, params=None, timeout: float | None = None):
        return rpc_call(
            method,
            params,
            socket_path=self.socket_path,
            timeout=self.timeout if timeout is None else timeout,
        )

    def status(self):
        return self._call("status")

    def health(self):
        return self._call("health.get")

    def apply_model(self, model_path: Path):
        model = normalize_runtime_model(Path(model_path).resolve())
        return self._call("runtime.apply", {"model": model}, timeout=20.0)

    def apply_model_data(self, model: dict):
        return self._call("runtime.apply", {"model": model}, timeout=20.0)

    def reset(self):
        return self._call("runtime.reset")

    def resync(self):
        return self._call("runtime.resync", timeout=20.0)

    def list_buses(self):
        return self._call("bus.list")

    def list_devices(self):
        return self._call("device.list")

    def get_device(self, device_id: str):
        return self._call("device.get", {"device_id": device_id})

    def update_device(self, device_id: str, patch: dict):
        return self._call("device.update", {"device_id": device_id, "patch": patch})

    def list_backends(self):
        return self._call("backend.list")

    def attach_backend(self, backend_id: str, device_ids: list[str]):
        return self._call("backend.attach", {"backend_id": backend_id, "device_ids": device_ids})

    def detach_backend(self, backend_id: str, device_ids: list[str] | None = None):
        params = {"backend_id": backend_id}
        if device_ids is not None:
            params["device_ids"] = device_ids
        return self._call("backend.detach", params)

    def heartbeat_backend(self, backend_id: str):
        return self._call("backend.heartbeat", {"backend_id": backend_id})

    def controller(self, backend_id: str):
        return ControllerSession(self, backend_id)

    def next_event(self, backend_id: str, timeout: float = 30.0):
        result = self._call(
            "backend.next_event",
            {"backend_id": backend_id, "timeout": timeout},
            timeout=timeout + 5.0,
        )
        return RuntimeEvent.from_dict(result.get("event"))

    def reply(self, backend_id: str, request_id: int, status: int = 0, data: str = ""):
        return self._call(
            "backend.reply",
            {
                "backend_id": backend_id,
                "request_id": request_id,
                "status": status,
                "data": data,
            },
        )

    def inject_uart_rx(self, device_id: str, data_hex: str):
        return self._call("uart.inject_rx", {"device_id": device_id, "data": data_hex})

    def set_uart_modem(self, device_id: str, signals: dict):
        return self._call("uart.control_set", {"device_id": device_id, "signals": signals})

    def stats(self):
        return self._call("stats.get")

    def trace(self, limit: int = 32):
        return self._call("trace.list", {"limit": limit})
