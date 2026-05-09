#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
from pathlib import Path

from sensorium.runtime.constants import *  # noqa: F401,F403
from sensorium.runtime.model import *  # noqa: F401,F403
from sensorium.runtime.paths import *  # noqa: F401,F403


def rpc_call(method: str, params=None, *, socket_path: Path | None = None, timeout=10.0):
    socket_path = runtime_socket_path() if socket_path is None else Path(socket_path)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }
    payload = (json.dumps(request) + "\n").encode("utf-8")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
        sock.sendall(payload)
        response = b""
        while not response.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                raise RuntimeError("sensoriumd closed the connection unexpectedly")
            response += chunk

    message = json.loads(response.decode("utf-8"))
    if "error" in message:
        error = message["error"]
        raise RuntimeError(error.get("message", "unknown sensoriumd error"))
    return message.get("result")
