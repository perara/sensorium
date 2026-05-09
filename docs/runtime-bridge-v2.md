# Runtime Bridge ABI v2

## Status

ABI v2 is now the live bridge format in both the kernel runtime adapter and
`sensoriumd`, including an explicit open-time `HELLO` / `HELLO_ACK` handshake.

The implemented v2 contract currently includes:

- `version = 2`
- a fixed `262144` byte frame limit
- a `32` byte header carrying `request_id`, `msg_type`, `generation`,
  `segment_index`, `segment_count`, and `total_payload_len`
- segmented request/reply reassembly on both sides of the bridge
- generation-aware reply matching so stale replies are ignored
- dynamic I2C/SPI descriptor blocks with `256` I2C messages and `256` SPI
  transfers per logical request
- a `4 MiB` logical payload ceiling per request or reply

## Why a v2 exists

The old runtime bridge ABI (`version = 1`) was intentionally simple, but it
had real limits:

- fixed maximum frame size
- fixed maximum I2C message count per request
- fixed maximum SPI transfer count per request
- one request maps to one complete bridge frame
- no feature negotiation beyond the hardcoded version field

That simplicity has been useful for getting the daemon-backed runtime stable,
but it is now the main protocol-level ceiling for larger SPI payloads, richer
multi-transfer traffic, and future transport growth.

## Goals

ABI v2 should:

- preserve the existing Linux-visible device contracts (`/dev/i2c-*`,
  `/dev/spidev*`, `/dev/tty*`)
- support larger payloads without a fixed per-request cap tied to one bridge
  frame
- keep one request/reply lifecycle visible to the daemon even when data spans
  multiple frames
- make feature negotiation explicit so daemon and kernel can reject
  incompatible peers early
- keep malformed-frame handling strict and testable

## Non-goals

ABI v2 is not trying to:

- become electrical or cycle-accurate emulation
- remove the daemon-backed architecture by itself
- change the user-facing runtime model schema

## Proposed wire model

### Handshake

When the daemon opens `/dev/sensorium-runtime-bridge`, the first read/write
exchange should negotiate:

- ABI version
- maximum segment size
- supported optional features

Example negotiated features:

- segmented payloads
- batched replies
- explicit request cancellation
- out-of-band health notifications

### Request identity

Every request keeps:

- `request_id`
- `msg_type`
- `generation`
- `flags`

`generation` ties replies to the runtime generation that produced the request.
That makes late replies from a previous generation provably ignorable instead of
only “best effort” late-reply handling.

### Segmentation

Large requests and replies are split into one or more segments:

- `segment_index`
- `segment_count`
- `payload_len`

The daemon only exposes a request to controller/template handlers after the
full logical request has been reassembled.

The kernel only completes the original bus operation after the full logical
reply has been reassembled.

### Reply status

Replies carry:

- transport status (`0`, `-ETIMEDOUT`, `-EIO`, ...)
- reply flags
- optional payload segments

This keeps current behavior familiar while allowing partial transport-layer
delivery underneath.

## Compatibility plan

v2 was introduced as a clean break between kernel and daemon rather than a
silent best-effort fallback. The kernel rejects frames with the wrong ABI
version, and the daemon does the same.

The model schema does not need a new major version just because the bridge wire
format changes; the bridge ABI is an implementation detail behind the runtime
model contract.

## Follow-up Work

1. Add optional open-time feature negotiation and segment-size negotiation.
2. Add malformed-segment ordering and truncation tests to the QEMU path.
3. Expand the smoke/stress matrix to cover multi-segment runtime traffic
   explicitly instead of only unit-level reassembly tests.

## Acceptance criteria

ABI v2 is ready when:

- a full runtime smoke and burn-in pass are green using segmented traffic
- larger-than-v1 SPI payloads pass end to end
- malformed segment ordering and truncated segments fail deterministically
- late replies from stale generations are ignored without desyncing the runtime
