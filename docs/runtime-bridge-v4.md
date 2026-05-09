# Runtime Bridge ABI v4

ABI v4 is a historical bridge contract retained for reference. The live
kernel/daemon bridge is now ABI v5 in
[runtime-bridge-v5.md](runtime-bridge-v5.md).

## Current contract

- explicit `HELLO` / `HELLO_ACK` startup handshake
- negotiated ABI version with a daemon-visible session id
- `262144` byte frame limit
- `32` byte segmented header
- `262112` byte segment payload limit
- `4 MiB` logical payload limit
- `256` I2C message descriptors per request
- `256` SPI transfer descriptors per request
- per-device SPI defaults in the device-add command:
  `mode`, `bits_per_word`, and `max_speed_hz`

## Operational behavior

- runtime requests still present synchronous Linux-facing behavior to callers
- bridge dispatch uses a bounded daemon worker pool
- bridge overload fails fast with explicit `-EBUSY` replies instead of
  stretching queue latency indefinitely
- runtime status surfaces bridge queue depth, in-flight requests, late reply
  drops, trace drops, and RPC busy rejections
- desynced runtimes freeze mutating operations until `runtime resync` or a
  daemon restart restores a known-good state

## Observability

- independent ABI checks live in `tests/test_runtime_abi.py`
- repo-level verifier: `./scripts/local/verify-runtime-abi.py`
- structured local artifacts:
  - runtime snapshot JSON
  - bounded runtime trace JSONL
  - benchmark result JSON
