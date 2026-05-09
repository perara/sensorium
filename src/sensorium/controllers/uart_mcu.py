#!/usr/bin/env python3
from __future__ import annotations

from sensorium.runtime.client import SensoriumRuntimeClient
from sensorium.runtime.worker_client import connect_managed_controller_session


def main():
    session = connect_managed_controller_session()
    if session is None:
        client = SensoriumRuntimeClient()
        session = client.controller("example-uart-mcu")
        session.attach(["console-uart"])

    while True:
        event = session.next_event(timeout=30.0)
        if event is None:
            session.heartbeat()
            continue
        if event.transport != "uart":
            session.reply_error(event, status=-95)
            continue
        op = event.op
        if op == "config":
            session.reply_ok(event)
            continue
        rx = bytes.fromhex(event.payload.get("data", ""))
        if rx.strip() == b"AT":
            session.reply_ok(event, data="4f4b0d0a")
        else:
            session.reply_ok(event, data=rx.hex())


if __name__ == "__main__":
    main()
