#!/usr/bin/env python3
from __future__ import annotations

import errno

from sensorium.runtime.daemon_support import (
    REQ_I2C_XFER,
    REQ_SPI_XFER,
    REQ_UART_CFG,
    REQ_UART_CTRL,
    REQ_UART_TX,
)
from sensorium.runtime.i2c import RuntimeI2CMixin
from sensorium.runtime.spi import RuntimeSPIMixin
from sensorium.runtime.uart import RuntimeUARTMixin


class RuntimeTransportMixin(RuntimeI2CMixin, RuntimeSPIMixin, RuntimeUARTMixin):
    def _finalize_request(
        self,
        device: dict | None,
        transport: str,
        op: str,
        request: dict,
        status: int,
        data: bytes,
    ):
        if device is not None:
            status, data = self._apply_fault_post(device, status, data)
            self._record_stats(
                device,
                status=status,
                bytes_in=request.get("bytes_in", 0),
                bytes_out=len(data),
            )
        else:
            self._record_stats(
                None,
                status=status,
                bytes_in=request.get("bytes_in", 0),
                bytes_out=len(data),
            )

        self._record_trace(
            {
                "transport": transport,
                "op": op,
                "device_id": device.get("id") if device else None,
                "bus_id": device.get("bus") if device else None,
                "status": status,
                "request": {key: value for key, value in request.items() if key != "bytes_in"},
                "reply": data.hex(),
            }
        )
        return status, data

    def _handle_bridge_request(self, msg_type: int, message_id: int, payload: bytes):
        if msg_type == REQ_I2C_XFER:
            return self._handle_i2c_request(message_id, payload)
        if msg_type == REQ_SPI_XFER:
            return self._handle_spi_request(message_id, payload)
        if msg_type in {REQ_UART_TX, REQ_UART_CTRL}:
            return self._handle_uart_request(message_id, msg_type, payload)
        if msg_type == REQ_UART_CFG:
            return self._handle_uart_config_request(message_id, payload)
        return -errno.EINVAL, b""
