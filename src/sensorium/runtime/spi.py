#!/usr/bin/env python3
from __future__ import annotations

import copy
import errno

from sensorium.runtime.common import RUNTIME_MAX_SPI_XFERS
from sensorium.runtime.daemon_support import (
    SPI_LANE_WIDTHS,
    SPI_REQ_PREFIX_STRUCT,
    SPI_REQ_XFER_STRUCT,
)


class RuntimeSPIMixin:
    def _normalize_spi_lane_width(self, value: int) -> int:
        if value in (0, None):
            return 1
        return value

    def _reject_spi_request(
        self,
        *,
        device: dict | None,
        message_id: int,
        status: int,
        reason: str,
        request: dict,
    ):
        self._record_stats(device, status=status, bytes_in=request.get("bytes_in", 0), bytes_out=0)
        self._record_trace(
            {
                "transport": "spi",
                "op": "xfer",
                "device_id": device.get("id") if device else None,
                "bus_id": device.get("bus") if device else None,
                "status": status,
                "request": {"request_id": message_id, "reason": reason, **request},
                "reply": "",
            }
        )
        return status, b""

    def _handle_spi_request(self, message_id: int, payload: bytes):
        minimum = SPI_REQ_PREFIX_STRUCT.size
        if len(payload) < minimum:
            return self._reject_spi_request(
                device=None,
                message_id=message_id,
                status=-errno.EPROTO,
                reason="short-payload",
                request={
                    "payload_len": len(payload),
                    "minimum_len": minimum,
                    "bytes_in": len(payload),
                },
            )

        device_handle, _bus_handle, mode, num_xfers, data_len, chip_select = (
            SPI_REQ_PREFIX_STRUCT.unpack_from(payload, 0)
        )
        if num_xfers == 0 or num_xfers > RUNTIME_MAX_SPI_XFERS:
            return -errno.EOPNOTSUPP, b""

        descriptors_len = SPI_REQ_XFER_STRUCT.size * num_xfers
        minimum = SPI_REQ_PREFIX_STRUCT.size + descriptors_len
        if len(payload) < minimum:
            return self._reject_spi_request(
                device=None,
                message_id=message_id,
                status=-errno.EPROTO,
                reason="short-payload",
                request={
                    "payload_len": len(payload),
                    "minimum_len": minimum,
                    "bytes_in": len(payload),
                },
            )

        offset = SPI_REQ_PREFIX_STRUCT.size
        xfer_descs = []
        for _ in range(num_xfers):
            xfer_descs.append(SPI_REQ_XFER_STRUCT.unpack_from(payload, offset))
            offset += SPI_REQ_XFER_STRUCT.size

        tx = payload[offset:]
        if len(tx) != data_len:
            return self._reject_spi_request(
                device=None,
                message_id=message_id,
                status=-errno.EPROTO,
                reason="data-length-mismatch",
                request={
                    "payload_len": len(payload),
                    "num_xfers": num_xfers,
                    "data_len": data_len,
                    "actual_data_len": len(tx),
                    "bytes_in": len(payload),
                },
            )

        device = self._device_by_handle(device_handle)
        if device is None:
            return -errno.ENOENT, b""
        if chip_select != device.get("chip_select", chip_select):
            return self._reject_spi_request(
                device=device,
                message_id=message_id,
                status=-errno.EPROTO,
                reason="chip-select-mismatch",
                request={
                    "chip_select": chip_select,
                    "expected_chip_select": device.get("chip_select"),
                    "bytes_in": len(payload),
                },
            )

        transfers = []
        cursor = 0
        for index in range(num_xfers):
            (
                length,
                speed_hz,
                delay_usecs,
                bits_per_word,
                cs_change,
                tx_nbits,
                rx_nbits,
                word_delay_usecs,
                has_tx,
                has_rx,
            ) = xfer_descs[index]
            bits_per_word = bits_per_word or device["settings"].get("bits_per_word", 8)
            tx_nbits = self._normalize_spi_lane_width(tx_nbits)
            rx_nbits = self._normalize_spi_lane_width(rx_nbits)
            if length and bits_per_word not in range(1, 33):
                return self._reject_spi_request(
                    device=device,
                    message_id=message_id,
                    status=-errno.EOPNOTSUPP,
                    reason="invalid-bits-per-word",
                    request={
                        "transfer_index": index,
                        "bits_per_word": bits_per_word,
                        "bytes_in": len(payload),
                    },
                )
            if tx_nbits not in SPI_LANE_WIDTHS or rx_nbits not in SPI_LANE_WIDTHS:
                return self._reject_spi_request(
                    device=device,
                    message_id=message_id,
                    status=-errno.EOPNOTSUPP,
                    reason="invalid-lane-width",
                    request={
                        "transfer_index": index,
                        "tx_nbits": tx_nbits,
                        "rx_nbits": rx_nbits,
                        "bytes_in": len(payload),
                    },
                )
            if cs_change not in (0, 1):
                return self._reject_spi_request(
                    device=device,
                    message_id=message_id,
                    status=-errno.EOPNOTSUPP,
                    reason="invalid-cs-change",
                    request={
                        "transfer_index": index,
                        "cs_change": cs_change,
                        "bytes_in": len(payload),
                    },
                )
            if has_tx not in (0, 1) or has_rx not in (0, 1):
                return self._reject_spi_request(
                    device=device,
                    message_id=message_id,
                    status=-errno.EPROTO,
                    reason="invalid-tx-rx-flags",
                    request={
                        "transfer_index": index,
                        "has_tx": has_tx,
                        "has_rx": has_rx,
                        "bytes_in": len(payload),
                    },
                )
            chunk = tx[cursor : cursor + length]
            cursor += length
            transfers.append(
                {
                    "len": length,
                    "speed_hz": speed_hz,
                    "delay_usecs": delay_usecs,
                    "bits_per_word": bits_per_word,
                    "cs_change": cs_change,
                    "tx_nbits": tx_nbits,
                    "rx_nbits": rx_nbits,
                    "word_delay_usecs": word_delay_usecs,
                    "has_tx": bool(has_tx),
                    "has_rx": bool(has_rx),
                    "tx": chunk.hex(),
                }
            )

        request = {
            "request_id": message_id,
            "mode": mode,
            "chip_select": chip_select,
            "transfers": transfers,
            "bytes_in": len(payload),
        }
        pre_fault = self._apply_fault_pre(device)
        if pre_fault is not None:
            return self._finalize_request(device, "spi", "xfer", request, *pre_fault)

        if device["backend"]["kind"] == "template":
            status, data = 0, device["template"].handle_spi_message(transfers)
            return self._finalize_request(device, "spi", "xfer", request, status, data)

        event = {
            "request_id": message_id,
            "transport": "spi",
            "device_id": device["id"],
            "bus_id": device["bus"],
            "backend_id": device.get("attached_backend"),
            "chip_select": chip_select,
            "mode": mode,
            "transfers": transfers,
            "faults": copy.deepcopy(device["faults"]),
            "settings": copy.deepcopy(device["settings"]),
        }
        if transfers:
            event["tx"] = transfers[0]["tx"]
            event["bits_per_word"] = transfers[0]["bits_per_word"]
            event["speed_hz"] = transfers[0]["speed_hz"]
        status, data = self._dispatch_controller(device, event)
        return self._finalize_request(device, "spi", "xfer", request, status, data)
