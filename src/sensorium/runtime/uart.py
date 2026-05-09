#!/usr/bin/env python3
from __future__ import annotations

import copy
import errno
import termios

from sensorium.runtime.daemon_support import (
    CMD_UART_SET_MODEM,
    REQ_UART_CTRL,
    UART_CFG_STRUCT,
    UART_MODEM_BITS,
    UART_MODEM_MASK,
    UART_MODEM_STRUCT,
    UART_REQ_PREFIX_STRUCT,
)


class RuntimeUARTMixin:
    def _reject_uart_request(
        self,
        *,
        device: dict | None,
        message_id: int,
        op: str,
        status: int,
        reason: str,
        request: dict,
    ):
        self._record_stats(device, status=status, bytes_in=request.get("bytes_in", 0), bytes_out=0)
        self._record_trace(
            {
                "transport": "uart",
                "op": op,
                "device_id": device.get("id") if device else None,
                "bus_id": device.get("bus") if device else None,
                "status": status,
                "request": {"request_id": message_id, "reason": reason, **request},
                "reply": "",
            }
        )
        return status, b""

    def _derive_uart_settings(self, device: dict, baud_rate: int, cflag: int, iflag: int):
        data_bits = 8
        csize = cflag & termios.CSIZE
        if csize == termios.CS5:
            data_bits = 5
        elif csize == termios.CS6:
            data_bits = 6
        elif csize == termios.CS7:
            data_bits = 7

        parity = "none"
        if cflag & termios.PARENB:
            parity = "odd" if (cflag & termios.PARODD) else "even"

        return {
            "baud_rate": baud_rate or device["settings"].get("baud_rate", 115200),
            "data_bits": data_bits,
            "parity": parity,
            "stop_bits": 2 if (cflag & termios.CSTOPB) else 1,
            "xonxoff": bool(iflag & (termios.IXON | termios.IXOFF)),
            "rtscts": bool(cflag & getattr(termios, "CRTSCTS", 0)),
            "cflag": cflag,
            "iflag": iflag,
        }

    def _handle_uart_request(self, message_id: int, msg_type: int, payload: bytes):
        if len(payload) < UART_REQ_PREFIX_STRUCT.size:
            return -errno.EPROTO, b""

        device_handle, flags, length, modem_mask, modem_values = UART_REQ_PREFIX_STRUCT.unpack_from(
            payload, 0
        )
        data = payload[UART_REQ_PREFIX_STRUCT.size :]
        if len(data) != length:
            return -errno.EPROTO, b""

        device = self._device_by_handle(device_handle)
        if device is None:
            return -errno.ENOENT, b""

        op = "control" if msg_type == REQ_UART_CTRL else "tx"
        request = {
            "request_id": message_id,
            "op": op,
            "data": data.hex(),
            "modem_mask": modem_mask,
            "modem_values": modem_values,
            "bytes_in": len(payload),
        }
        expected_flags = 1 if msg_type == REQ_UART_CTRL else 0
        if flags != expected_flags:
            return self._reject_uart_request(
                device=device,
                message_id=message_id,
                op=op,
                status=-errno.EPROTO,
                reason="invalid-flags",
                request={**request, "flags": flags, "expected_flags": expected_flags},
            )
        if modem_mask & ~UART_MODEM_MASK:
            return self._reject_uart_request(
                device=device,
                message_id=message_id,
                op=op,
                status=-errno.EOPNOTSUPP,
                reason="invalid-modem-mask",
                request={**request, "invalid_mask_bits": modem_mask & ~UART_MODEM_MASK},
            )
        pre_fault = self._apply_fault_pre(device)
        if pre_fault is not None:
            return self._finalize_request(device, "uart", request["op"], request, *pre_fault)

        if device["backend"]["kind"] == "template":
            if msg_type == REQ_UART_CTRL:
                reply, modem_updates = device["template"].handle_uart_control(
                    modem_mask, modem_values
                )
                if modem_updates:
                    update_mask = 0
                    update_values = 0
                    for name, enabled in modem_updates.items():
                        bit = UART_MODEM_BITS.get(name.lower())
                        if bit is None:
                            continue
                        update_mask |= bit
                        if enabled:
                            update_values |= bit
                    if update_mask:
                        modem_payload = UART_MODEM_STRUCT.pack(
                            device["handle"], update_mask, update_values
                        )
                        self.bridge.write_frame(CMD_UART_SET_MODEM, 0, modem_payload)
                        request["auto_modem_updates"] = {
                            name: bool(enabled)
                            for name, enabled in sorted(modem_updates.items())
                            if name.lower() in UART_MODEM_BITS
                        }
                status = 0
            else:
                status, reply = 0, device["template"].handle_uart(data)
            return self._finalize_request(device, "uart", request["op"], request, status, reply)

        event = {
            "request_id": message_id,
            "transport": "uart",
            "device_id": device["id"],
            "bus_id": device["bus"],
            "backend_id": device.get("attached_backend"),
            "op": request["op"],
            "data": data.hex(),
            "modem_mask": modem_mask,
            "modem_values": modem_values,
            "faults": copy.deepcopy(device["faults"]),
            "settings": copy.deepcopy(device["settings"]),
        }
        status, reply = self._dispatch_controller(device, event)
        return self._finalize_request(device, "uart", request["op"], request, status, reply)

    def _handle_uart_config_request(self, message_id: int, payload: bytes):
        if len(payload) != UART_CFG_STRUCT.size:
            return -errno.EPROTO, b""

        device_handle, baud_rate, cflag, iflag, oflag, lflag = UART_CFG_STRUCT.unpack(payload)
        device = self._device_by_handle(device_handle)
        if device is None:
            return -errno.ENOENT, b""

        settings = self._derive_uart_settings(device, baud_rate, cflag, iflag)
        settings["oflag"] = oflag
        settings["lflag"] = lflag
        device["settings"].update(settings)
        with self.lock:
            self._persist_snapshot_locked()

        request = {
            "request_id": message_id,
            "baud_rate": baud_rate,
            "cflag": cflag,
            "iflag": iflag,
            "oflag": oflag,
            "lflag": lflag,
            "settings": copy.deepcopy(device["settings"]),
            "bytes_in": len(payload),
        }

        pre_fault = self._apply_fault_pre(device)
        if pre_fault is not None:
            return self._finalize_request(device, "uart", "config", request, *pre_fault)

        if device["backend"]["kind"] == "controller":
            event = {
                "request_id": message_id,
                "transport": "uart",
                "device_id": device["id"],
                "bus_id": device["bus"],
                "backend_id": device.get("attached_backend"),
                "op": "config",
                "settings": copy.deepcopy(device["settings"]),
                "raw_flags": {
                    "cflag": cflag,
                    "iflag": iflag,
                    "oflag": oflag,
                    "lflag": lflag,
                },
                "faults": copy.deepcopy(device["faults"]),
            }
            status, reply = self._dispatch_controller(device, event)
        else:
            status, reply = 0, b""
        return self._finalize_request(device, "uart", "config", request, status, reply)
