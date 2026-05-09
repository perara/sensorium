#!/usr/bin/env python3
from __future__ import annotations

from sensorium.runtime.bridge_protocol import UART_MODEM_BITS


class I2CRegisterBankTemplate:
    def __init__(self, backend: dict):
        self.size = int(backend.get("size", 256))
        self.pointer_width = int(backend.get("pointer_width", 1))
        self.auto_increment = bool(backend.get("auto_increment", True))
        self.registers = bytearray(self.size)
        self.pointer = 0
        self.clear_on_read = {
            int(register, 0) for register in backend.get("clear_on_read", [])
        }
        self.write_effects = {
            int(register, 0): {
                int(target, 0): value for target, value in effects.items()
            }
            for register, effects in backend.get("write_effects", {}).items()
        }
        for key, value in backend.get("registers", {}).items():
            self.registers[int(key, 0)] = value

    def handle_i2c(self, messages):
        rx_parts = []
        for message in messages:
            if message["flags"] & 0x0001:
                start = self.pointer
                length = message["len"]
                data = bytes(self.registers[(start + index) % self.size] for index in range(length))
                for index in range(length):
                    register = (start + index) % self.size
                    if register in self.clear_on_read:
                        self.registers[register] = 0
                if self.auto_increment:
                    self.pointer = (start + length) % self.size
                rx_parts.append(data)
                continue

            data = message["data"]
            if not data:
                continue
            if len(data) < self.pointer_width:
                continue
            pointer_bytes = data[: self.pointer_width]
            self.pointer = int.from_bytes(pointer_bytes, "big") % self.size
            payload = data[self.pointer_width :]
            if payload:
                for offset, byte in enumerate(payload):
                    register = (self.pointer + offset) % self.size
                    self.registers[register] = byte
                    for target, value in self.write_effects.get(register, {}).items():
                        self.registers[target % self.size] = value
                if self.auto_increment:
                    self.pointer = (self.pointer + len(payload)) % self.size
        return b"".join(rx_parts)


class SPIScriptTemplate:
    def __init__(self, backend: dict):
        self.responses = {
            bytes.fromhex(tx): bytes.fromhex(rx)
            for tx, rx in backend.get("responses", {}).items()
        }
        self.prefix_responses = [
            (bytes.fromhex(tx), bytes.fromhex(rx))
            for tx, rx in backend.get("prefix_responses", {}).items()
        ]
        self.default_response = bytes.fromhex(backend.get("default_response", ""))
        self.echo = backend.get("echo", False)
        self.flash_jedec_id = bytes.fromhex(backend.get("flash_jedec_id", ""))
        self.flash_status_register = int(backend.get("flash_status_register", 0)) & 0xFF
        self.flash_write_busy_cycles = int(backend.get("flash_write_busy_cycles", 0))
        self._flash_wel = False
        self._flash_busy_reads_remaining = 0

    def handle_spi(self, tx: bytes, length: int):
        if self.flash_jedec_id and tx:
            opcode = tx[0]
            if opcode == 0x06:
                self._flash_wel = True
                response = b""
            elif opcode == 0x04:
                self._flash_wel = False
                response = b""
            elif opcode == 0x05:
                status = self.flash_status_register & 0xFC
                if self._flash_wel:
                    status |= 0x02
                if self._flash_busy_reads_remaining > 0:
                    status |= 0x01
                    self._flash_busy_reads_remaining -= 1
                response = bytes([status]) * max(1, length)
            elif opcode == 0x9F:
                response = self.flash_jedec_id
            elif opcode == 0x01 and len(tx) >= 2 and self._flash_wel:
                self.flash_status_register = tx[1] & 0xFC
                self._flash_wel = False
                self._flash_busy_reads_remaining = self.flash_write_busy_cycles
                response = b""
            else:
                response = tx if self.echo else self.default_response
        elif tx in self.responses:
            response = self.responses[tx]
        elif self.prefix_responses:
            response = None
            for prefix, candidate in self.prefix_responses:
                if tx.startswith(prefix):
                    response = candidate
                    break
            if response is None:
                response = tx if self.echo else self.default_response
        elif self.echo:
            response = tx
        else:
            response = self.default_response

        if len(response) < length:
            response = response + b"\0" * (length - len(response))
        return response[:length]

    def handle_spi_message(self, transfers):
        return b"".join(
            self.handle_spi(bytes.fromhex(transfer["tx"]), transfer["len"])
            for transfer in transfers
        )


class UARTScriptTemplate:
    def __init__(self, backend: dict):
        self.echo = backend.get("echo", True)
        self.line_responses = {
            key: value.encode("utf-8") for key, value in backend.get("line_responses", {}).items()
        }
        self.binary_responses = {
            bytes.fromhex(key): bytes.fromhex(value)
            for key, value in backend.get("binary_responses", {}).items()
        }
        self.default_response = bytes.fromhex(backend.get("default_response", ""))
        self.control_defaults = backend.get("control_defaults", {})
        self.cts_follows_rts = bool(backend.get("cts_follows_rts", False))
        self.carrier_follows_dtr = bool(backend.get("carrier_follows_dtr", False))
        self._line_buffer = bytearray()

    def modem_defaults(self):
        mask = 0
        values = 0
        for name, enabled in self.control_defaults.items():
            bit = UART_MODEM_BITS.get(name.lower())
            if bit is None:
                continue
            mask |= bit
            if enabled:
                values |= bit
        return mask, values

    def handle_uart(self, data: bytes):
        reply = bytearray()
        if self.echo and data:
            reply.extend(data)

        if data in self.binary_responses:
            reply.extend(self.binary_responses[data])

        if self.line_responses and data:
            self._line_buffer.extend(data)
            while b"\n" in self._line_buffer:
                raw_line, _, rest = self._line_buffer.partition(b"\n")
                self._line_buffer = bytearray(rest)
                line = raw_line.rstrip(b"\r").decode("utf-8", errors="ignore")
                if line in self.line_responses:
                    reply.extend(self.line_responses[line])

        if not reply and data and self.default_response:
            reply.extend(self.default_response)

        return bytes(reply)

    def handle_uart_control(self, modem_mask: int, modem_values: int):
        updates = {}
        if self.cts_follows_rts and (modem_mask & UART_MODEM_BITS["rts"]):
            updates["cts"] = bool(modem_values & UART_MODEM_BITS["rts"])
        if self.carrier_follows_dtr and (modem_mask & UART_MODEM_BITS["dtr"]):
            enabled = bool(modem_values & UART_MODEM_BITS["dtr"])
            updates["cd"] = enabled
            updates["dsr"] = enabled
        return b"", updates
