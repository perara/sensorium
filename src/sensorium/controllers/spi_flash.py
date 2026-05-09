#!/usr/bin/env python3
from __future__ import annotations

from sensorium.runtime.client import SensoriumRuntimeClient
from sensorium.runtime.worker_client import connect_managed_controller_session


def main():
    session = connect_managed_controller_session()
    if session is None:
        client = SensoriumRuntimeClient()
        session = client.controller("example-spi-flash")
        session.attach(["flash-spi"])

    while True:
        event = session.next_event(timeout=30.0)
        if event is None:
            session.heartbeat()
            continue
        if event.transport != "spi":
            session.reply_error(event, status=-95)
            continue
        transfers = event.payload.get("transfers", [])
        tx = bytes.fromhex(transfers[0]["tx"]) if transfers else b""
        if tx.startswith(b"\x9f"):
            session.reply_ok(event, data="ef4018")
        else:
            session.reply_ok(event, data=tx.hex())


if __name__ == "__main__":
    main()
