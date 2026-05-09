#!/usr/bin/env python3
from __future__ import annotations

import copy
import errno

from sensorium.runtime.common import RUNTIME_MAX_I2C_MSGS
from sensorium.runtime.daemon_support import I2C_REQ_MSG_STRUCT, I2C_REQ_PREFIX_STRUCT


class RuntimeI2CMixin:
    def _handle_i2c_request(self, message_id: int, payload: bytes):
        if len(payload) < I2C_REQ_PREFIX_STRUCT.size:
            return -errno.EPROTO, b""

        device_handle, _bus_handle, num_msgs, data_len = I2C_REQ_PREFIX_STRUCT.unpack_from(payload, 0)
        if num_msgs == 0 or num_msgs > RUNTIME_MAX_I2C_MSGS:
            return -errno.EOPNOTSUPP, b""
        descriptors_len = I2C_REQ_MSG_STRUCT.size * num_msgs
        minimum = I2C_REQ_PREFIX_STRUCT.size + descriptors_len
        if len(payload) < minimum:
            return -errno.EPROTO, b""
        offset = I2C_REQ_PREFIX_STRUCT.size
        msg_descs = []
        for _ in range(num_msgs):
            msg_descs.append(I2C_REQ_MSG_STRUCT.unpack_from(payload, offset))
            offset += I2C_REQ_MSG_STRUCT.size
        tx_data = payload[offset:]
        if len(tx_data) != data_len:
            return -errno.EPROTO, b""

        device = self._device_by_handle(device_handle)
        if device is None:
            return -errno.ENOENT, b""

        request = {
            "request_id": message_id,
            "messages": [],
            "bytes_in": len(payload),
        }
        pre_fault = self._apply_fault_pre(device)
        if pre_fault is not None:
            return self._finalize_request(device, "i2c", "xfer", request, *pre_fault)

        messages = []
        cursor = 0
        for index in range(num_msgs):
            addr, flags, length, _reserved = msg_descs[index]
            chunk = b""
            if not (flags & 0x0001):
                chunk = tx_data[cursor : cursor + length]
                cursor += length
            entry = {"addr": addr, "flags": flags, "len": length, "data": chunk}
            messages.append(entry)
            request["messages"].append(
                {
                    "addr": addr,
                    "flags": flags,
                    "len": length,
                    "data": chunk.hex(),
                }
            )

        expected_addr = device.get("address")
        if messages and any(message["addr"] != expected_addr for message in messages):
            self._record_stats(device, status=-errno.EOPNOTSUPP, bytes_in=len(payload), bytes_out=0)
            self._record_trace(
                {
                    "transport": "i2c",
                    "op": "xfer",
                    "device_id": device["id"],
                    "bus_id": device["bus"],
                    "status": -errno.EOPNOTSUPP,
                    "request": {
                        "request_id": message_id,
                        "reason": "mixed-address-transfer",
                        "expected_addr": expected_addr,
                        "messages": request["messages"],
                    },
                    "reply": "",
                }
            )
            return -errno.EOPNOTSUPP, b""

        if device["backend"]["kind"] == "template":
            status, data = 0, device["template"].handle_i2c(messages)
            return self._finalize_request(device, "i2c", "xfer", request, status, data)

        event = {
            "request_id": message_id,
            "transport": "i2c",
            "device_id": device["id"],
            "bus_id": device["bus"],
            "backend_id": device.get("attached_backend"),
            "messages": request["messages"],
            "faults": copy.deepcopy(device["faults"]),
        }
        status, data = self._dispatch_controller(device, event)
        return self._finalize_request(device, "i2c", "xfer", request, status, data)
