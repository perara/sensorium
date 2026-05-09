#!/usr/bin/env python3
from __future__ import annotations

from sensorium.runtime.client import SensoriumRuntimeClient
from sensorium.runtime.worker_client import connect_managed_controller_session


def main():
    session = connect_managed_controller_session()
    if session is None:
        client = SensoriumRuntimeClient()
        session = client.controller("example-eeprom")
        session.attach(["eeprom-i2c"])

    while True:
        event = session.next_event(timeout=30.0)
        if event is None:
            session.heartbeat()
            continue
        if event.transport != "i2c":
            session.reply_error(event, status=-95)
            continue
        messages = event.payload.get("messages", [])
        if not messages:
            session.reply_ok(event)
            continue
        first = messages[0]
        reg = bytes.fromhex(first.get("data", "00") or "00")[0]
        if len(messages) > 1 and messages[1].get("flags", 0) & 0x0001:
            session.reply_ok(event, data=f"{(reg & 0xff):02x}")
        else:
            session.reply_ok(event)


if __name__ == "__main__":
    main()
