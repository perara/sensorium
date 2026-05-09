#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import struct


WORKER_MESSAGE_LIMIT = 8 * 1024 * 1024
_LENGTH_STRUCT = struct.Struct("<I")


def _recv_exact(sock: socket.socket, length: int):
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if chunk == b"":
            if not chunks:
                return None
            raise EOFError("unexpected EOF while reading worker message")
        chunks.extend(chunk)
    return bytes(chunks)


def recv_worker_message(sock: socket.socket, timeout: float | None = None):
    previous_timeout = sock.gettimeout()
    if timeout is not None:
        sock.settimeout(timeout)
    try:
        header = _recv_exact(sock, _LENGTH_STRUCT.size)
        if header is None:
            return None
        length = _LENGTH_STRUCT.unpack(header)[0]
        if length <= 0 or length > WORKER_MESSAGE_LIMIT:
            raise RuntimeError(f"invalid worker message length: {length}")
        payload = _recv_exact(sock, length)
        if payload is None:
            raise EOFError("unexpected EOF while reading worker payload")
        return json.loads(payload.decode("utf-8"))
    except socket.timeout as exc:
        raise TimeoutError from exc
    finally:
        if timeout is not None:
            sock.settimeout(previous_timeout)


def send_worker_message(sock: socket.socket, payload: dict):
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) > WORKER_MESSAGE_LIMIT:
        raise RuntimeError("worker message exceeds size limit")
    sock.sendall(_LENGTH_STRUCT.pack(len(encoded)) + encoded)


def open_worker_socket_from_env():
    fd_value = os.environ.get("SENSORIUM_WORKER_FD")
    if not fd_value:
        return None
    fd = int(fd_value)
    sock = socket.socket(fileno=fd)
    sock.setblocking(True)
    return sock
