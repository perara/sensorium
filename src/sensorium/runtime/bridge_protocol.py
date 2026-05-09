#!/usr/bin/env python3
from __future__ import annotations

import collections
import errno
import fcntl
import mmap
import os
import re
import selectors
import struct
import threading
from pathlib import Path

from sensorium.runtime.common import (
    RUNTIME_BRIDGE_ABI_VERSION,
    RUNTIME_MAX_PAYLOAD,
)


MAGIC = 0x5352544D
VERSION = RUNTIME_BRIDGE_ABI_VERSION

CMD_RESET = 1
CMD_BUS_ADD = 2
CMD_BUS_REMOVE = 3
CMD_DEVICE_ADD = 4
CMD_DEVICE_REMOVE = 5
CMD_UART_INJECT_RX = 6
CMD_UART_SET_MODEM = 7
CMD_REPLY = 8
CMD_HELLO = 9
CMD_HELLO_ACK = 10

REQ_I2C_XFER = 101
REQ_SPI_XFER = 102
REQ_UART_TX = 103
REQ_UART_CTRL = 104
REQ_UART_CFG = 105

TRANSPORT_IDS = {"i2c": 1, "spi": 2, "uart": 3}
TRANSPORT_NAMES = {value: key for key, value in TRANSPORT_IDS.items()}
UART_MODEM_BITS = {
    "rts": 0x004,
    "cts": 0x020,
    "dtr": 0x002,
    "dsr": 0x100,
    "cd": 0x040,
    "ri": 0x080,
}
UART_MODEM_MASK = 0
for _bit in UART_MODEM_BITS.values():
    UART_MODEM_MASK |= _bit
SPI_LANE_WIDTHS = {1, 2, 4, 8}

QUEUE_CLASS_CONTROL = 1
QUEUE_CLASS_TRANSPORT = 2
QUEUE_CLASS_REPLY = 3

FEATURE_SHARED_RINGS = 0x1
FEATURE_EVENTFD_NOTIFY = 0x2
FEATURE_INDEXED_REQUESTS = 0x4
REQUIRED_FEATURES = (
    FEATURE_SHARED_RINGS | FEATURE_EVENTFD_NOTIFY | FEATURE_INDEXED_REQUESTS
)
FEATURE_SEGMENTED = FEATURE_SHARED_RINGS
FEATURE_GENERATION = FEATURE_EVENTFD_NOTIFY
FEATURE_DYNAMIC_DESCRIPTORS = FEATURE_INDEXED_REQUESTS

DEFAULT_CONTROL_RING_ENTRIES = 64
DEFAULT_TRANSPORT_RING_ENTRIES = 128
DEFAULT_REPLY_RING_ENTRIES = 128
DEFAULT_PAYLOAD_ARENA_SIZE = 8 * 1024 * 1024

BUS_CMD_STRUCT = struct.Struct("<III64s")
DEVICE_CMD_STRUCT = struct.Struct("<IIIIIIBB2x64s")
UART_MODEM_STRUCT = struct.Struct("<III")
UART_RX_PREFIX_STRUCT = struct.Struct("<III")
I2C_REQ_PREFIX_STRUCT = struct.Struct("<IIII")
I2C_REQ_MSG_STRUCT = struct.Struct("<HHHH")
SPI_REQ_PREFIX_STRUCT = struct.Struct("<IIIIII")
SPI_REQ_XFER_STRUCT = struct.Struct("<IIHBBBBBBB2x")
UART_REQ_PREFIX_STRUCT = struct.Struct("<IIIII")
UART_CFG_STRUCT = struct.Struct("<IIIIII")
REPLY_PREFIX_STRUCT = struct.Struct("<iI")
HELLO_STRUCT = struct.Struct("<IIIII")
HELLO_ACK_STRUCT = struct.Struct("<IIIIII")

V5_DESC_STRUCT = struct.Struct("<IIIHHIIIiI")
V5_CONTROL_STRUCT = struct.Struct("<39I")
V5_SETUP_STRUCT = struct.Struct("<20I")
V5_EVENTFDS_STRUCT = struct.Struct("<ii")

V5_CONTROL_FIELDS = {
    "magic": 0,
    "abi_version": 1,
    "session_id": 2,
    "generation": 3,
    "flags": 4,
    "features": 5,
    "control_ring_entries": 6,
    "transport_ring_entries": 7,
    "reply_ring_entries": 8,
    "control_ring_head": 9,
    "control_ring_tail": 10,
    "transport_ring_head": 11,
    "transport_ring_tail": 12,
    "reply_ring_head": 13,
    "reply_ring_tail": 14,
    "control_payload_size": 15,
    "transport_payload_size": 16,
    "reply_payload_size": 17,
    "control_payload_head": 18,
    "control_payload_tail": 19,
    "transport_payload_head": 20,
    "transport_payload_tail": 21,
    "reply_payload_head": 22,
    "reply_payload_tail": 23,
    "inflight_credit_limit": 24,
    "inflight_in_use": 25,
    "ebusy_generation": 26,
    "ebusy_total": 27,
    "request_completed_total": 28,
    "request_timeout_generation": 29,
    "request_timeout_total": 30,
    "broker_eventfd_registered": 31,
    "kernel_eventfd_registered": 32,
    "desynced": 33,
}

FIXED_COMMAND_SIZES = {
    CMD_RESET: 0,
    CMD_BUS_ADD: BUS_CMD_STRUCT.size,
    CMD_BUS_REMOVE: 4,
    CMD_DEVICE_ADD: DEVICE_CMD_STRUCT.size,
    CMD_DEVICE_REMOVE: 4,
    CMD_UART_SET_MODEM: UART_MODEM_STRUCT.size,
}


def _IOC(direction: int, type_: int, nr: int, size: int) -> int:
    return (
        (direction << 30)
        | (size << 16)
        | (type_ << 8)
        | nr
    )


def _IO(type_: int, nr: int) -> int:
    return _IOC(0, type_, nr, 0)


def _IOW(type_: int, nr: int, size: int) -> int:
    return _IOC(1, type_, nr, size)


def _IOWR(type_: int, nr: int, size: int) -> int:
    return _IOC(3, type_, nr, size)


IOCTL_BASE = ord("r")
IOCTL_SETUP_V5 = _IOWR(IOCTL_BASE, 0x01, V5_SETUP_STRUCT.size)
IOCTL_REGISTER_EVENTFDS = _IOW(IOCTL_BASE, 0x02, V5_EVENTFDS_STRUCT.size)
IOCTL_START_V5 = _IO(IOCTL_BASE, 0x03)
IOCTL_SUBMIT_CONTROL = _IO(IOCTL_BASE, 0x04)
IOCTL_SUBMIT_REPLY = _IO(IOCTL_BASE, 0x05)


def validate_command_payload(msg_type: int, payload: bytes):
    if len(payload) > RUNTIME_MAX_PAYLOAD:
        raise ValueError(
            f"payload too large for runtime bridge command {msg_type}: {len(payload)} > {RUNTIME_MAX_PAYLOAD}"
        )
    expected = FIXED_COMMAND_SIZES.get(msg_type)
    if expected is not None and len(payload) != expected:
        raise ValueError(
            f"bridge command {msg_type} requires {expected} bytes, got {len(payload)}"
        )


def pack_header(msg_type: int, message_id: int, payload: bytes) -> bytes:
    raise RuntimeError("pack_header is not supported by ABI v5")


def iter_frames(msg_type: int, message_id: int, payload: bytes, *, generation: int = 0):
    raise RuntimeError("iter_frames is not supported by ABI v5")


def encode_c_string(value: str, length: int) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) >= length:
        raise ValueError(f"value too long for fixed field: {value!r}")
    return encoded + b"\0" * (length - len(encoded))


def decode_c_string(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("utf-8")


def parse_tty_name(value: str):
    if not value:
        raise ValueError(f"invalid tty-style port name: {value!r}")
    split = len(value)
    while split > 0 and value[split - 1].isdigit():
        split -= 1
    if split == len(value) or split == 0:
        raise ValueError(f"invalid tty-style port name: {value!r}")
    base = value[:split]
    suffix = value[split:]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", base):
        raise ValueError(f"invalid tty-style port name: {value!r}")
    return base, int(suffix, 10)


class ControllerPending:
    def __init__(self, event: dict):
        self.event = event
        self.cond = threading.Condition()
        self.done = False
        self.status = -errno.ETIMEDOUT
        self.data = b""


class KernelBridge:
    def __init__(self, path: Path):
        self.path = path
        self.fd = None
        self.mm = None
        self.lock = threading.Lock()
        self.broker_eventfd = None
        self.kernel_eventfd = None
        self.selector = None
        self.pending_transport = collections.deque()
        self.session_id = 0
        self.generation = 0
        self.features = REQUIRED_FEATURES
        self.region_size = 0
        self.control_ring_offset = 0
        self.transport_ring_offset = 0
        self.reply_ring_offset = 0
        self.control_payload_offset = 0
        self.control_payload_size = 0
        self.transport_payload_offset = 0
        self.transport_payload_size = 0
        self.reply_payload_offset = 0
        self.reply_payload_size = 0

    def open(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CLOEXEC)

    def _require_open(self):
        if self.fd is None:
            raise RuntimeError("bridge is not open")

    def _control_words(self):
        return V5_CONTROL_STRUCT.unpack_from(self.mm, 0)

    def _control_word(self, name: str) -> int:
        return self._control_words()[V5_CONTROL_FIELDS[name]]

    def _set_control_word(self, name: str, value: int):
        struct.pack_into("<I", self.mm, V5_CONTROL_FIELDS[name] * 4, int(value) & 0xFFFFFFFF)

    def _ring_desc(self, ring_offset: int, entries: int, counter: int):
        slot = counter % entries
        offset = ring_offset + (slot * V5_DESC_STRUCT.size)
        return V5_DESC_STRUCT.unpack_from(self.mm, offset), offset

    def _set_ring_desc(self, ring_offset: int, entries: int, counter: int, values):
        slot = counter % entries
        offset = ring_offset + (slot * V5_DESC_STRUCT.size)
        V5_DESC_STRUCT.pack_into(self.mm, offset, *values)

    def _payload_view(self, base_offset: int, zone_size: int, absolute_offset: int, length: int):
        zone_offset = absolute_offset % zone_size
        start = base_offset + zone_offset
        end = start + length
        return memoryview(self.mm)[start:end]

    def _reserve_payload(self, head: int, tail: int, zone_size: int, payload_len: int):
        start = tail
        mod = start % zone_size
        if payload_len == 0:
            return start
        if mod + payload_len > zone_size:
            start += zone_size - mod
        if (start + payload_len) - head > zone_size:
            raise BlockingIOError(errno.EBUSY, "runtime payload arena full")
        return start

    def negotiate(self):
        self._require_open()
        setup_buffer = bytearray(
            V5_SETUP_STRUCT.pack(
                VERSION,
                DEFAULT_CONTROL_RING_ENTRIES,
                DEFAULT_TRANSPORT_RING_ENTRIES,
                DEFAULT_REPLY_RING_ENTRIES,
                DEFAULT_PAYLOAD_ARENA_SIZE,
                DEFAULT_TRANSPORT_RING_ENTRIES,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
            )
        )
        fcntl.ioctl(self.fd, IOCTL_SETUP_V5, setup_buffer, True)
        (
            abi_version,
            control_entries,
            transport_entries,
            reply_entries,
            _payload_arena_size,
            _credit_limit,
            region_size,
            session_id,
            generation,
            features,
            _control_page_offset,
            control_ring_offset,
            transport_ring_offset,
            reply_ring_offset,
            control_payload_offset,
            control_payload_size,
            transport_payload_offset,
            transport_payload_size,
            reply_payload_offset,
            reply_payload_size,
        ) = V5_SETUP_STRUCT.unpack(setup_buffer)
        if abi_version != VERSION:
            raise RuntimeError("bridge ABI version mismatch")
        if (features & REQUIRED_FEATURES) != REQUIRED_FEATURES:
            missing = REQUIRED_FEATURES & ~features
            raise RuntimeError(
                f"runtime bridge missing required ABI v5 feature bits: 0x{missing:x}"
            )
        self.region_size = region_size
        self.session_id = session_id
        self.generation = generation
        self.features = features
        self.control_ring_entries = control_entries
        self.transport_ring_entries = transport_entries
        self.reply_ring_entries = reply_entries
        self.control_ring_offset = control_ring_offset
        self.transport_ring_offset = transport_ring_offset
        self.reply_ring_offset = reply_ring_offset
        self.control_payload_offset = control_payload_offset
        self.control_payload_size = control_payload_size
        self.transport_payload_offset = transport_payload_offset
        self.transport_payload_size = transport_payload_size
        self.reply_payload_offset = reply_payload_offset
        self.reply_payload_size = reply_payload_size

        self.mm = mmap.mmap(self.fd, self.region_size, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ | mmap.PROT_WRITE)
        if not hasattr(os, "eventfd"):
            raise RuntimeError("Python runtime does not expose os.eventfd; ABI v5 broker requires it")
        self.broker_eventfd = os.eventfd(0, os.EFD_CLOEXEC | os.EFD_NONBLOCK)
        self.kernel_eventfd = os.eventfd(0, os.EFD_CLOEXEC | os.EFD_NONBLOCK)
        eventfds = V5_EVENTFDS_STRUCT.pack(self.broker_eventfd, self.kernel_eventfd)
        fcntl.ioctl(self.fd, IOCTL_REGISTER_EVENTFDS, eventfds)
        fcntl.ioctl(self.fd, IOCTL_START_V5)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.broker_eventfd, selectors.EVENT_READ)
        return {
            "generation": self.generation,
            "features": self.features,
            "session_id": self.session_id,
        }

    def close(self):
        if self.selector is not None:
            try:
                self.selector.close()
            except Exception:
                pass
            self.selector = None
        if self.mm is not None:
            try:
                self.mm.close()
            except Exception:
                pass
            self.mm = None
        if self.broker_eventfd is not None:
            try:
                os.close(self.broker_eventfd)
            except OSError:
                pass
            self.broker_eventfd = None
        if self.kernel_eventfd is not None:
            try:
                os.close(self.kernel_eventfd)
            except OSError:
                pass
            self.kernel_eventfd = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def metrics(self):
        if self.mm is None:
            return {}
        words = self._control_words()
        control_head = words[V5_CONTROL_FIELDS["control_ring_head"]]
        control_tail = words[V5_CONTROL_FIELDS["control_ring_tail"]]
        transport_head = words[V5_CONTROL_FIELDS["transport_ring_head"]]
        transport_tail = words[V5_CONTROL_FIELDS["transport_ring_tail"]]
        reply_head = words[V5_CONTROL_FIELDS["reply_ring_head"]]
        reply_tail = words[V5_CONTROL_FIELDS["reply_ring_tail"]]
        return {
            "session_id": words[V5_CONTROL_FIELDS["session_id"]],
            "generation": words[V5_CONTROL_FIELDS["generation"]],
            "inflight_requests": words[V5_CONTROL_FIELDS["inflight_in_use"]],
            "inflight_credit_limit": words[V5_CONTROL_FIELDS["inflight_credit_limit"]],
            "queue_depths": {
                "control": control_tail - control_head,
                "transport": transport_tail - transport_head,
                "reply": reply_tail - reply_head,
            },
            "ebusy_generation": words[V5_CONTROL_FIELDS["ebusy_generation"]],
            "ebusy_total": words[V5_CONTROL_FIELDS["ebusy_total"]],
            "request_timeout_generation": words[V5_CONTROL_FIELDS["request_timeout_generation"]],
            "request_timeout_total": words[V5_CONTROL_FIELDS["request_timeout_total"]],
            "request_completed_total": words[V5_CONTROL_FIELDS["request_completed_total"]],
            "desynced": bool(words[V5_CONTROL_FIELDS["desynced"]]),
        }

    def _wait_for_transport(self):
        if self.pending_transport:
            return
        if self._control_word("transport_ring_head") != self._control_word("transport_ring_tail"):
            return
        if self.selector is None:
            raise RuntimeError("bridge selector not initialized")
        while True:
            events = self.selector.select(timeout=1.0)
            if not events:
                if self._control_word("transport_ring_head") != self._control_word("transport_ring_tail"):
                    return
                continue
            for _key, _mask in events:
                try:
                    os.read(self.broker_eventfd, 8)
                except BlockingIOError:
                    pass
                return

    def _drain_transport_ring(self):
        while True:
            head = self._control_word("transport_ring_head")
            tail = self._control_word("transport_ring_tail")
            if head == tail:
                return
            desc, _ = self._ring_desc(
                self.transport_ring_offset,
                self.transport_ring_entries,
                head,
            )
            (
                session_id,
                generation,
                request_id,
                queue_class,
                opcode,
                device_handle,
                payload_offset,
                payload_len,
                status,
                _reserved,
            ) = desc
            if session_id != self.session_id:
                raise RuntimeError("runtime bridge session mismatch")
            payload = bytes(
                self._payload_view(
                    self.transport_payload_offset,
                    self.transport_payload_size,
                    payload_offset,
                    payload_len,
                )
            )
            self._set_control_word("transport_ring_head", head + 1)
            self._set_control_word("transport_payload_head", payload_offset + payload_len)
            self.pending_transport.append((opcode, request_id, generation, payload))

    def read_frame(self):
        while not self.pending_transport:
            self._wait_for_transport()
            self._drain_transport_ring()
        return self.pending_transport.popleft()

    def _submit_ring_entry(
        self,
        *,
        ring_kind: str,
        msg_type: int,
        message_id: int,
        generation: int,
        payload: bytes,
    ):
        validate_command_payload(msg_type, payload)
        with self.lock:
            if ring_kind == "control":
                ring_entries = self.control_ring_entries
                ring_offset = self.control_ring_offset
                head_name = "control_ring_head"
                tail_name = "control_ring_tail"
                payload_head_name = "control_payload_head"
                payload_tail_name = "control_payload_tail"
                payload_base = self.control_payload_offset
                payload_size = self.control_payload_size
                queue_class = QUEUE_CLASS_CONTROL
                submit_ioctl = IOCTL_SUBMIT_CONTROL
            else:
                ring_entries = self.reply_ring_entries
                ring_offset = self.reply_ring_offset
                head_name = "reply_ring_head"
                tail_name = "reply_ring_tail"
                payload_head_name = "reply_payload_head"
                payload_tail_name = "reply_payload_tail"
                payload_base = self.reply_payload_offset
                payload_size = self.reply_payload_size
                queue_class = QUEUE_CLASS_REPLY
                submit_ioctl = IOCTL_SUBMIT_REPLY

            head = self._control_word(head_name)
            tail = self._control_word(tail_name)
            if tail - head >= ring_entries:
                raise BlockingIOError(errno.EBUSY, f"runtime {ring_kind} ring full")
            payload_head = self._control_word(payload_head_name)
            payload_tail = self._control_word(payload_tail_name)
            payload_offset = self._reserve_payload(payload_head, payload_tail, payload_size, len(payload))
            if payload:
                self._payload_view(payload_base, payload_size, payload_offset, len(payload))[:] = payload
            self._set_ring_desc(
                ring_offset,
                ring_entries,
                tail,
                (
                    self.session_id,
                    generation,
                    message_id,
                    queue_class,
                    msg_type,
                    0,
                    payload_offset,
                    len(payload),
                    0,
                    0,
                ),
            )
            self._set_control_word(payload_tail_name, payload_offset + len(payload))
            self._set_control_word(tail_name, tail + 1)
            fcntl.ioctl(self.fd, submit_ioctl)

    def write_frame(self, msg_type: int, message_id: int, payload: bytes, *, generation: int = 0):
        if msg_type == CMD_HELLO or msg_type == CMD_HELLO_ACK:
            raise RuntimeError("HELLO frames are not part of ABI v5")
        ring_kind = "reply" if msg_type == CMD_REPLY else "control"
        self._submit_ring_entry(
            ring_kind=ring_kind,
            msg_type=msg_type,
            message_id=message_id,
            generation=generation,
            payload=payload,
        )
