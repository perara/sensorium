# Architecture

Sensorium targets Linux-facing device simulation for downstream software. The
design goal is interface fidelity through normal Linux device nodes, ioctls,
sysfs, and media graphs, not hardware-accurate emulation of electrical or
cycle-level behavior.

## Core shape

The repo now separates three layers:

- `sensorium-core`
  Owns adapter selection, transport selection, instance naming, shared timing,
  transport alias naming, and shared fault-mode state.
- subsystem adapters
  The current built-in adapters are `camera`, `iio`, and `runtime`.
- model-driven userspace control
  `sensoriumctl` applies YAML models onto the kernel module or the live runtime
  daemon, depending on adapter type.

Adapters are the primary extension point. Transports are shared capabilities
used by adapters rather than the top-level product identity.

## Camera graph

The current graph is:

```text
/dev/video-inject --> [ selected imx sensor subdev ] --> /dev/video-capture
```

Entity model:

- profile-selected sensor subdevice such as `imx708`, `imx477`, or `imx219`
- `sensorium-inject` OUTPUT video node
- `sensorium-capture` CAPTURE video node

The sensor carries the selected IMX profile identity and owns mode selection, controls,
and stream state. The inject and capture nodes mirror the active sensor mode rather than
inventing their own configuration model.

## Compatibility target

The repo does not carry a custom libcamera pipeline handler. The target is a kernel-only
solution whose graph is close enough to an existing supported camera pipeline that
libcamera can discover it automatically without libcamera source changes.

That means the driver must eventually match more than the sensor name alone:

- entity naming that looks like the expected sensor and receiver path
- controls and mode negotiation that behave like a real selected IMX sensor
- media-bus formats and frame sizes aligned with the existing camera stack
- graph topology close enough to an already-supported IMX708 path that no extra
  userspace matching code is needed

The current three-entity graph is the smallest shape that still preserves that
camera-like behavior. The reusable core is family/profile-driven, so most of the
pipeline code is shared and only the selected backend identity, mode table, and default
control ranges change per profile.

## Runtime model

The runtime split follows four logical units:

- `sensorium-core.c`
  Owns top-level lifetime, family/profile selection, media registration, links, and
  cadence work.
- `sensorium-sensor.c`
  Owns the subdevice, controls, and format negotiation.
- `sensorium-inject.c`
  Owns the OUTPUT node and ingress queue.
- `sensorium-capture.c`
  Owns the CAPTURE node and egress queue.
- `sensorium-iio.c`
  Owns the first non-camera IIO adapter path.
- `sensorium-runtime.c`
  Owns the daemon bridge plus the live multi-device I2C, SPI, and UART
  surfaces used by the `runtime` adapter.

The daemon side of that runtime lives in `src/sensorium/cli/sensoriumd.py`
behind the stable `scripts/runtime/sensoriumd` launcher. It owns bus and device
inventory, live per-device settings and fault state, built-in template
backends, recent transfer tracing, per-device/runtime counters, and
controller-app routing over `/run/sensorium/sensoriumd.sock`.

The daemon now also owns explicit runtime state semantics:

- `empty`: no active runtime inventory
- `applying`: a new generation is being staged into the kernel bridge
- `ready`: the daemon's active generation is believed to match the kernel
- `degraded`: the runtime is still serving traffic, but operator-visible errors
  should be investigated
- `desynced`: the daemon can no longer prove that its in-memory inventory
  matches the kernel bridge state, so recovery must go through `runtime resync`
  or restart repair

The runtime control split is now:

- kernel:
  owns the Linux-visible `/dev/i2c-*`, `/dev/spidev*`, and `/dev/tty*` nodes plus
  synchronous bridge requests
- `sensoriumd`:
  owns the current runtime model, device settings, stats/trace history, and
  template/controller routing
- controller apps:
  can use JSON-RPC directly or the helper wrapper in
  `src/sensorium/runtime/client.py`

UART termios changes are now reported from the kernel runtime adapter back to
the daemon, so controller backends can react to baud, parity, stop-bit, and
flow-control changes rather than only raw TX/RX bytes.

The bridge contract itself is treated as a first-class ABI now. ABI v5 replaces
the old framed transport with a shared-memory bridge negotiated by setup
ioctls, backed by `mmap` rings, and surfaced through explicit queue-depth and
generation-scoped overload metrics. The repo ships an independent verifier plus
ABI-focused tests so kernel packing changes or Python struct drift are caught
before they reach QEMU. SPI device-add still carries default `mode`,
`bits_per_word`, and `max_speed_hz`, so model-level SPI settings survive all
the way into the kernel-created `spidev*` nodes.

The bridge execution model is also less serialized than before. `sensoriumd`
keeps a single bridge reader, but it now feeds a bounded worker pool with
per-backend or per-device routing locks so slow controller-backed devices do
not stall unrelated transports. The daemon reports queue depth, in-flight
request count, late replies, and latency summaries through `runtime status` and
`runtime stats`, and its controller deadline is derived from the kernel
module's `runtime_timeout_ms` parameter with a small safety margin instead of
using an unrelated hard-coded timeout.

Controller-backed runtime devices now have two supported execution modes:

- external controllers that poll and reply through the JSON-RPC backend API
- broker-managed controller workers launched by `sensoriumd` from
  `backend.worker.command` and isolated behind local socketpair IPC

Within that daemon process, bridge dispatch is now keyed through an O(1)
device-handle index instead of a linear scan across the full runtime inventory.
Recent runtime traces are still kept in memory for fast `runtime trace`
queries, but JSONL persistence now happens on a background writer thread with
a bounded drop-oldest queue so file I/O does not sit directly on the hot
request path or grow memory without bound.

The daemon now also persists two operator-facing artifacts under its runtime
state root (`$SENSORIUM_STATE_DIR`, otherwise the default user state path):

- `sensoriumd-runtime-snapshot.json`:
  the last applied normalized runtime model plus live device updates and
  backend attachments
- `sensoriumd-trace.jsonl`:
  append-only JSONL event history for recent runtime requests

On startup, `sensoriumd` reloads recent trace entries into memory and, unless
started with `--no-restore-snapshot`, replays the saved runtime snapshot back
into the kernel bridge. That makes daemon restarts much less disruptive during
local development and QEMU sessions.

Snapshots are versioned and carry the last known runtime generation, state, and
backend attachments. Older snapshot schema versions are discarded instead of
being replayed optimistically.

I2C forwarding is intentionally single-target per combined transaction. The
runtime rejects mixed-address `I2C_RDWR` sequences instead of silently routing
them to the first device handle. SPI forwarding now carries the per-transfer
delay metadata already exposed by the `spidev` ABI so controller backends can
see `delay_usecs`, `word_delay_usecs`, and `cs_change` in addition to payload,
speed, and lane-width settings.

The SPI runtime path now uses a real `spi_controller` plus runtime-created
`spi_device` children, with `spidev` binding forced through the kernel's
`driver_override` flow. That keeps `/dev/spidevB.C` creation aligned with the
current SPI subsystem rules instead of relying on a miscdevice shim. The UART
runtime path now keeps explicit RX/TX queue state so `write_room()`,
`chars_in_buffer()`, `flush_buffer()`, and `wait_until_sent()` are closer to
what real TTY consumers expect, and multiple `ttyAMA*` ports now share one
runtime tty driver per base name instead of each port registering its own
private driver. Unset SPI lane-width fields are normalized to single-lane
instead of leaking ambiguous zero values into the runtime event surface. The
default shipped runtime model and QEMU smoke now also
exercise more than one I2C address, more than one SPI chip select, and more
than one UART port at once.

The runtime UART side no longer hardcodes one tiny tty family/queue shape in
the module. `runtime_uart_lines`, `runtime_uart_tx_capacity`, and
`runtime_uart_rx_capacity` are now module parameters, so larger sparse
`ttyAMA*` numbering and larger queued serial bursts can be enabled without
changing the bridge ABI or recompiling userspace.

For broader scale and restart coverage, the repo also ships
`models/runtime/rpi-multibus-scale.yaml`,
`models/runtime/rpi-multibus-burnin.yaml`, plus
`scripts/local/stress-runtime-model.sh` and `scripts/remote/remote-stress-runtime.sh`.
Those flows intentionally restart `sensoriumd` between later iterations,
optionally auto-resync if health drifts, and reuse the restored runtime
snapshot, so the burn-in path validates daemon recovery and bridge
reattachment rather than only a fresh model apply.

UART draining is now timer-driven rather than "send then sleep inside one work
item". Bytes remain in the tty-visible pending count until the scheduled drain
completes, so `flush()` / `tcdrain()` and `write_room()` behave more like a
real low-baud serial path under load.

## Buffer flow

1. Userspace queues frames on the inject node.
2. The inject node accepts either packed RGB ingress or raw `SRGGB10`.
3. Packed RGB ingress is converted in-kernel to the active `SRGGB10` mode layout.
4. A cadence worker delivers frames into the capture queue.
5. If no new ingress frame is available, the newest held inject buffer may be repeated to
   keep camera clients moving.

The cadence logic lives in delayed work so the queue model stays simple while
still behaving like a clocked sensor instead of a pure push-through transport.

## Kernel policy

- The sensor subdevice is the authoritative owner of the active mode.
- Inject and capture formats must match the active sensor mode.
- Mode changes while queues are busy are rejected with `-EBUSY`.
- Timestamps use `CLOCK_MONOTONIC`.
- Sequence numbers advance on each delivered capture frame.
- Inject and capture queues use DMA-backed vb2 memory operations.
- The default underrun policy is repeat-last-frame, but module parameter
  `repeat_last_frame=0` switches to a stricter throughput mode.
- Raw-path cadence can be driven through the sensor `VBLANK` control, which is how the
  benchmark and record tools validate `10`, `20`, and `30` fps operation.
  The QEMU wrappers now persist parsed benchmark artifacts under
  `.cache/benchmarks/` so regressions can be compared across runs instead of
  relying on ad hoc stdout capture.
- Processed/viewfinder cadence depends on the libcamera software ISP throughput of the
  host in addition to the raw sensor cadence. On the validated 1 vCPU droplet, the
  processed path tops out at about `10 fps`.

## Support matrix

- Camera:
  - fully supported:
    - libcamera discovery on the validated host/QEMU path
    - explicit media graph and control-surface contract checks
    - raw capture, processed capture, and raw MP4 record
  - approximated:
    - host-dependent processed-path throughput
    - production-shaped IMX behavior rather than exhaustive electrical fidelity
  - intentionally unsupported:
    - a custom libcamera pipeline handler inside this repo
- IIO:
  - fully supported:
    - environmental models over `i2c`, `spi`, and `uart`
    - `environment-plus` humidity, calibration-bias, and threshold-event coverage
      is now exercised over `i2c`, `spi`, and `uart`
  - approximated:
    - wider real-world sensor diversity and richer buffered/triggered ecosystems
  - intentionally unsupported:
    - using IIO as a second live multi-device runtime daemon surface
- Runtime:
  - fully supported:
    - Linux-facing protocol simulation for I2C, SPI, and UART userspace consumers
    - controller-backed and template-backed devices
    - restart-aware snapshot restore and resync
  - approximated:
    - controller timing, line timing, and hardware/electrical fidelity
  - intentionally unsupported:
    - cycle-accurate or electrical bus emulation

## Next structural change

The current focus is stability and regression-proofing rather than another graph rewrite.
The main operational goals are:

- keep libcamera auto-detection stable
- keep the raw record path visually correct
- track performance regressions in the remote droplet loop

One compatibility detail remains intentional: the media-device `driver_name` still uses
the libcamera-compatible receiver identity expected by the current simple pipeline path,
even though the module, scripts, repo surface, and camera IDs are now under `sensorium`.
