# Runtime Bridge ABI v5

ABI v5 is the live kernel/daemon bridge contract for the runtime adapter.

## Current contract

- bridge setup uses dedicated ioctls instead of framed `read` / `write`
- runtime traffic uses an `mmap`-backed shared region with:
  - control page
  - control ring
  - transport request ring
  - reply ring
  - payload arena
- descriptors carry:
  - `session_id`
  - `generation`
  - `request_id`
  - `queue_class`
  - `opcode`
  - `device_handle`
  - `payload_offset`
  - `payload_len`
  - `status`
- broker wakeups use `eventfd` registration plus explicit submit ioctls
- per-device SPI defaults are carried in the device-add command:
  - `mode`
  - `bits_per_word`
  - `max_speed_hz`
- built-in descriptor ceilings remain:
  - `256` I2C messages per request
  - `256` SPI transfers per request

## Operational behavior

- Linux-facing runtime calls remain synchronous to downstream callers
- the internal bridge path is concurrent and queue-based
- control and transport work use separate rings
- bridge overload fails fast with explicit `-EBUSY` replies instead of allowing
  unbounded queue growth
- runtime status surfaces:
  - `bridge_abi`
  - `session_id`
  - `inflight_requests`
  - per-ring queue depths
  - generation-scoped and lifetime overload counters
  - worker restart and last-failure fields for broker-managed controller workers
  - trace drops
  - RPC busy rejections
- desynced runtimes freeze mutating operations until `runtime resync` or a
  daemon restart restores a known-good state

## Observability

- independent ABI checks live in `tests/test_runtime_abi.py`
- repo-level verifier: `./scripts/local/verify-runtime-abi.py`
- structured local artifacts:
  - runtime snapshot JSON
  - bounded runtime trace JSONL
  - benchmark result JSON

## Historical note

ABI v4 was the previous framed bridge protocol. It remains documented only as
historical context and is no longer the live kernel/daemon transport.
